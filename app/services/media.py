from __future__ import annotations

import math
import re
from pathlib import Path
from collections import deque
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import EndpointConfig, SrtTransportConfig, SrtTransportDirection, WebRtcStreamConfig
from app.services.gst_runtime import CtypesGst, CtypesManagedPipeline
from app.services.media_graph import MediaGraphBuilder
from app.services.telemetry import DiagnosticsState, TelemetryService


_AUDIO_TEST_WAVEFORMS = {
    "sine": "sine",
    "square": "square",
    "triangle": "triangle",
    "saw": "saw",
    "sawtooth": "saw",
    "white-noise": "white-noise",
    "pink-noise": "pink-noise",
    "silence": "silence",
}

_LEVEL_MESSAGE_RE = re.compile(
    r'from element "(?P<name>dbmeter_(?P<direction>in|out)_[^"]*_(?P<channel>\d+))".*?'
    r"rms=\([^)]+\)<(?P<rms>[^>]*)>.*?"
    r"peak=\([^)]+\)<(?P<peak>[^>]*)>",
    re.IGNORECASE,
)


@dataclass
class ManagedPipeline:
    name: str
    argv: list[str]
    process: subprocess.Popen[str]
    output_tail: deque[str] = field(default_factory=lambda: deque(maxlen=120))
    started_at: float = field(default_factory=time.time)

    def describe(self, *, include_output_tail: bool = True) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "pid": self.process.pid,
            "argv": self.argv,
            "running": self.process.poll() is None,
            "returncode": self.process.poll(),
            "started_at": self.started_at,
        }
        if include_output_tail:
            payload["output_tail"] = list(self.output_tail)
        return payload


@dataclass
class MediaController:
    telemetry: TelemetryService
    gst_launch_executable: str = "gst-launch-1.0"
    _tone_pipeline: ManagedPipeline | None = None
    _monitor_pipeline: ManagedPipeline | None = None
    _program_pipeline: ManagedPipeline | None = None
    _srt_transport_pipelines: dict[str, Any] = field(default_factory=dict)
    _gst_runtime: CtypesGst | None = None

    def __post_init__(self) -> None:
        self.gst_launch_executable = self._resolve_gst_launch_executable(self.gst_launch_executable)

    @property
    def _diag(self) -> DiagnosticsState:
        return self.telemetry.diagnostics

    # --- program / talkback ---

    def describe_program_pipeline(self, config: EndpointConfig) -> dict[str, Any]:
        return {
            "capture": config.audio.interface_name or "unselected",
            "channels": config.audio.channel_count,
            "encode": "placeholder-tone-or-srt-monitor",
            "transport": f"srt-{config.program.srt_mode.value}",
            "latency_ms": config.program.srt_latency_ms,
            "clock_recovery": config.program.clock_recovery_mode.value,
        }

    def runtime_status(self, config: EndpointConfig) -> dict[str, Any]:
        """Truthful media-runtime surface for API/UI feature gating."""
        graph_plan = self.plan_media_graph(config)
        observed_telemetry = self.telemetry.has_recent_media_observations()
        audio_metering = self.telemetry.has_recent_audio_meters()
        return {
            "engine": "managed-gstreamer+gst-launch",
            "gst_launch_executable": self.gst_launch_executable,
            "capabilities": {
                "first_class_media_runtime": True,
                "source_capture": True,
                "encode_group_graphs": True,
                "srt_subprocess_transport": False,
                "srt_managed_transport": True,
                "dynamic_monitor_branches": True,
                "webrtc_media": False,
                "observed_telemetry": observed_telemetry,
                "audio_metering": audio_metering,
                "clock_recovery": False,
            },
            "limitations": [
                "TX source graph supports configured tone and silence sources only; Dante capture is not wired yet",
                "WebRTC stream start/stop is control-plane state only",
                "Managed TX SRT graphs poll srtsink.stats for transport telemetry",
                "Monitor branches terminate at autoaudiosink/fakesink only; browser delivery not wired yet",
            ],
            "configured": {
                "sources": len(config.sources),
                "encode_groups": len(config.encode_groups),
                "srt_transports": len(config.srt_transports),
                "webrtc_streams": len(config.webrtc_streams),
            },
            "graph_plan": {
                "valid": graph_plan["valid"],
                "error_count": len(graph_plan["errors"]),
            },
            "pipelines": self.list_pipelines(include_output_tail=False),
        }

    def plan_media_graph(self, config: EndpointConfig) -> dict[str, Any]:
        return MediaGraphBuilder(gst_launch_executable=self.gst_launch_executable).plan_endpoint(config)

    def start_program(
        self,
        config: EndpointConfig,
        host: str | None = None,
        port: int | None = None,
        frequency_hz: float = 1000.0,
        level_dbfs: float = -18.0,
        waveform: str = "sine",
    ) -> dict[str, Any]:
        if self._program_pipeline is not None:
            raise RuntimeError("program pipeline is already running")

        srt_mode = config.program.srt_mode.value
        selected_port = port or config.network.srt_port
        pipeline_kind = "receiver"

        if srt_mode == "listener":
            pipeline = self._spawn_pipeline(
                name="program_listener",
                argv=self._build_monitor_pipeline(
                    config=config,
                    host=None,
                    port=selected_port,
                    srt_mode=srt_mode,
                ),
            )
        else:
            if not host:
                raise ValueError("program start requires host when srt_mode is caller or rendezvous")
            wave = _AUDIO_TEST_WAVEFORMS.get(waveform.lower())
            if wave is None:
                raise ValueError(f"unsupported waveform '{waveform}'")
            pipeline_kind = "tone_sender"
            pipeline = self._spawn_pipeline(
                name="program_sender",
                argv=self._build_tone_sender_pipeline(
                    config=config,
                    frequency_hz=frequency_hz,
                    level_dbfs=level_dbfs,
                    waveform=wave,
                    host=host,
                    port=selected_port,
                    srt_mode=srt_mode,
                ),
            )

        self._program_pipeline = pipeline
        self.telemetry.program_running = True
        return {
            **self.describe_program_pipeline(config),
            "host": host,
            "port": selected_port,
            "srt_mode": srt_mode,
            "kind": pipeline_kind,
            "runtime_note": "program start currently launches a diagnostic SRT pipeline, not a configured source graph",
            "process": pipeline.describe(),
            "tone": None if pipeline_kind == "receiver" else {
                "frequency_hz": frequency_hz,
                "level_dbfs": level_dbfs,
                "waveform": waveform,
            },
        }

    def stop_program(self) -> None:
        self._stop_pipeline(self._program_pipeline)
        self._program_pipeline = None
        self.telemetry.program_running = False

    def start_talkback(self) -> None:
        self.telemetry.talkback_running = True

    def stop_talkback(self) -> None:
        self.telemetry.talkback_running = False

    def shutdown(self) -> None:
        self.stop_program()
        self.stop_monitor()
        self.stop_tone()
        for transport_id in list(self._srt_transport_pipelines):
            self.stop_srt_transport(transport_id)

    # --- transport-oriented control ---

    def start_srt_transport(
        self,
        config: EndpointConfig,
        transport_id: str,
        frequency_hz: float = 1000.0,
        level_dbfs: float = -18.0,
        waveform: str = "sine",
    ) -> dict[str, Any]:
        transport = self._get_srt_transport(config, transport_id)
        if transport_id in self._srt_transport_pipelines:
            raise RuntimeError(f"SRT transport '{transport_id}' is already running")

        port = transport.port or config.network.srt_port
        latency_ms = transport.latency_ms or config.program.srt_latency_ms
        self._validate_srt_endpoint(mode=transport.mode.value, host=transport.host)
        graph_plan = MediaGraphBuilder(gst_launch_executable=self.gst_launch_executable).plan_srt_transport(
            config,
            transport_id,
            raise_on_error=True,
        )
        pipeline = self._spawn_managed_gst_pipeline(
            name=f"srt_transport_{transport_id}_{transport.direction.value}",
            graph=graph_plan["gstreamer"]["graph"],
            srt_element_name=graph_plan["gstreamer"]["srt_element_name"],
            transport_id=transport_id,
        )
        kind = "receiver" if transport.direction == SrtTransportDirection.rx else "sender"

        self._srt_transport_pipelines[transport_id] = pipeline
        self.telemetry.mark_srt_transport(transport_id, True)
        return {
            "id": transport.id,
            "name": transport.name,
            "direction": transport.direction.value,
            "mode": transport.mode.value,
            "host": transport.host,
            "port": port,
            "latency_ms": latency_ms,
            "encode_group_ids": transport.encode_group_ids,
            "kind": kind,
            "runtime_note": (
                "RX graph planned from SRT ingress with a named monitor tap" if kind == "receiver"
                else "TX graph planned from configured sources, encode groups, and SRT transport"
            ),
            "graph_plan": graph_plan,
            "process": pipeline.describe(),
        }

    def stop_srt_transport(self, transport_id: str) -> None:
        pipeline = self._srt_transport_pipelines.pop(transport_id, None)
        self._stop_pipeline(pipeline)
        self.telemetry.mark_srt_transport(transport_id, False)

    # --- monitor branch attachment ---

    def start_monitor_branch(
        self,
        config: EndpointConfig,
        transport_id: str,
        tap_id: str,
        *,
        audible: bool = True,
    ) -> dict[str, Any]:
        pipeline = self._srt_transport_pipelines.get(transport_id)
        if pipeline is None:
            raise RuntimeError(f"SRT transport '{transport_id}' is not running")
        if not hasattr(pipeline, "attach_branch"):
            raise RuntimeError(
                f"SRT transport '{transport_id}' is not running on the managed runtime; cannot attach branches"
            )

        plan = MediaGraphBuilder(gst_launch_executable=self.gst_launch_executable).plan_srt_transport(
            config, transport_id, raise_on_error=True
        )
        taps = plan["gstreamer"]["monitor_taps"]
        tap = next((t for t in taps if t["id"] == tap_id), None)
        if tap is None:
            raise ValueError(
                f"tap '{tap_id}' is not exposed by transport '{transport_id}'; available: {[t['id'] for t in taps]}"
            )

        description = self._build_monitor_branch_description(audible=audible)
        branch = pipeline.attach_branch(tap_name=tap_id, description=description)
        return {
            "handle": branch.handle,
            "transport_id": transport_id,
            "tap": tap,
            "audible": audible,
            "branch_description": description,
        }

    def stop_monitor_branch(self, transport_id: str, handle: str) -> bool:
        pipeline = self._srt_transport_pipelines.get(transport_id)
        if pipeline is None or not hasattr(pipeline, "detach_branch"):
            return False
        return pipeline.detach_branch(handle)

    def list_monitor_branches(self, transport_id: str) -> list[dict[str, Any]]:
        pipeline = self._srt_transport_pipelines.get(transport_id)
        if pipeline is None or not hasattr(pipeline, "list_branches"):
            return []
        return pipeline.list_branches()

    def _build_monitor_branch_description(self, *, audible: bool) -> str:
        if not audible:
            return "queue ! fakesink sync=false async=false"
        sink = " ".join(self._build_audio_output_sink())
        return f"queue ! opusdec ! audioconvert ! audioresample ! {sink} sync=false"

    def start_webrtc_stream(self, config: EndpointConfig, stream_id: str) -> dict[str, Any]:
        stream = self._get_webrtc_stream(config, stream_id)
        raise RuntimeError(
            f"WebRTC media runtime is not implemented yet for stream '{stream.id}'; use SRT transport monitoring instead"
        )

    def stop_webrtc_stream(self, stream_id: str) -> None:
        self.telemetry.mark_webrtc_stream(stream_id, False)

    def list_pipelines(self, *, include_output_tail: bool = True) -> list[dict[str, Any]]:
        pipelines: list[Any] = []
        if self._program_pipeline is not None:
            pipelines.append(self._program_pipeline)
        if self._tone_pipeline is not None:
            pipelines.append(self._tone_pipeline)
        if self._monitor_pipeline is not None:
            pipelines.append(self._monitor_pipeline)
        pipelines.extend(self._srt_transport_pipelines.values())
        return [pipeline.describe(include_output_tail=include_output_tail) for pipeline in pipelines]

    # --- tone generator ---

    def start_tone(
        self,
        config: EndpointConfig,
        frequency_hz: float = 1000.0,
        level_dbfs: float = -18.0,
        channel: int = 1,
        waveform: str = "sine",
        host: str = "127.0.0.1",
        port: int | None = None,
        srt_mode: str = "caller",
    ) -> dict[str, Any]:
        if self._tone_pipeline is not None:
            raise RuntimeError("tone pipeline is already running")

        wave = _AUDIO_TEST_WAVEFORMS.get(waveform.lower())
        if wave is None:
            raise ValueError(f"unsupported waveform '{waveform}'")

        pipeline = self._spawn_pipeline(
            name="tone_sender",
            argv=self._build_tone_sender_pipeline(
                config=config,
                frequency_hz=frequency_hz,
                level_dbfs=level_dbfs,
                waveform=wave,
                host=host,
                port=port or config.network.srt_port,
                srt_mode=srt_mode,
            ),
        )
        self._tone_pipeline = pipeline
        self._diag.tone_running = True
        self._diag.tone_frequency_hz = frequency_hz
        self._diag.tone_level_dbfs = level_dbfs
        self._diag.tone_channel = channel
        self._diag.tone_waveform = waveform
        return pipeline.describe()

    def stop_tone(self) -> None:
        self._stop_pipeline(self._tone_pipeline)
        self._tone_pipeline = None
        self._diag.tone_running = False

    # --- loopback test ---

    def start_loopback(
        self,
        input_channels: list[int],
        output_channels: list[int],
    ) -> None:
        self._diag.loopback_running = True
        self._diag.loopback_input_channels = list(input_channels)
        self._diag.loopback_output_channels = list(output_channels)

    def stop_loopback(self) -> None:
        self._diag.loopback_running = False
        self._diag.loopback_input_channels = []
        self._diag.loopback_output_channels = []

    # --- monitor / listen ---

    def start_monitor(
        self,
        config: EndpointConfig,
        channel: int,
        is_input: bool = True,
        transport_id: str | None = None,
        host: str | None = None,
        port: int | None = None,
        srt_mode: str = "listener",
    ) -> dict[str, Any]:
        if self._monitor_pipeline is not None:
            raise RuntimeError("monitor pipeline is already running")

        if transport_id:
            transport = self._get_srt_transport(config, transport_id)
            if transport.direction != SrtTransportDirection.rx:
                raise ValueError(
                    f"local monitor is only available for RX SRT transports; '{transport.id}' is {transport.direction.value}"
                )
            host = transport.host
            port = transport.port or config.network.srt_port
            srt_mode = transport.mode.value

        resolved_port = port or config.network.srt_port
        self._validate_srt_endpoint(mode=srt_mode, host=host)

        pipeline = self._spawn_pipeline(
            name="srt_monitor",
            argv=self._build_monitor_pipeline(
                config=config,
                host=host,
                port=resolved_port,
                srt_mode=srt_mode,
            ),
        )
        self._monitor_pipeline = pipeline
        self._diag.monitor_running = True
        self._diag.monitor_channel = channel
        self._diag.monitor_is_input = is_input
        self._diag.monitor_transport_id = transport_id
        return pipeline.describe()

    def stop_monitor(self) -> None:
        self._stop_pipeline(self._monitor_pipeline)
        self._monitor_pipeline = None
        self._diag.monitor_running = False
        self._diag.monitor_transport_id = None

    # --- round-trip test ---

    def run_round_trip(self, stimulus_channel: int = 1, return_channel: int = 1) -> dict[str, Any]:
        if not self.telemetry.program_running:
            return {"error": "program path is not running"}
        return {
            "stimulus_channel": stimulus_channel,
            "return_channel": return_channel,
            "round_trip_ms": None,
            "note": "stub — no real pipeline",
        }

    def _build_tone_sender_pipeline(
        self,
        config: EndpointConfig,
        frequency_hz: float,
        level_dbfs: float,
        waveform: str,
        host: str,
        port: int,
        srt_mode: str,
        latency_ms: int | None = None,
    ) -> list[str]:
        uri = self._build_srt_uri(host=host, port=port, srt_mode=srt_mode, latency_ms=latency_ms or config.program.srt_latency_ms)
        volume = 10 ** (level_dbfs / 20)
        bitrate_bps = config.program.opus.bitrate_kbps * 1000
        return [
            self.gst_launch_executable,
            "-q",
            "audiotestsrc",
            "is-live=true",
            f"wave={waveform}",
            f"freq={frequency_hz}",
            f"volume={volume}",
            "!",
            "audioconvert",
            "!",
            "audioresample",
            "!",
            "audio/x-raw,rate=48000,channels=1",
            "!",
            "opusenc",
            f"bitrate={bitrate_bps}",
            "!",
            "oggmux",
            "!",
            "srtsink",
            f"uri={uri}",
        ]

    def _build_monitor_pipeline(
        self,
        config: EndpointConfig,
        host: str | None,
        port: int,
        srt_mode: str,
        latency_ms: int | None = None,
    ) -> list[str]:
        uri = self._build_srt_uri(host=host, port=port, srt_mode=srt_mode, latency_ms=latency_ms or config.program.srt_latency_ms)
        return [
            self.gst_launch_executable,
            "-q",
            "srtsrc",
            f"uri={uri}",
            "!",
            "queue",
            "!",
            "oggdemux",
            "!",
            "opusdec",
            "!",
            "audioconvert",
            "!",
            "audioresample",
            "!",
            *self._build_audio_output_sink(),
        ]

    def _build_audio_output_sink(self) -> list[str]:
        if sys.platform == "win32":
            if self._gst_supports_element("wasapisink"):
                return ["wasapisink"]
            if self._gst_supports_element("directsoundsink"):
                return ["directsoundsink"]
        return ["autoaudiosink"]

    def _gst_supports_element(self, element_name: str) -> bool:
        gst_launch_path = Path(self.gst_launch_executable)
        gst_inspect_name = "gst-inspect-1.0.exe" if gst_launch_path.suffix.lower() == ".exe" else "gst-inspect-1.0"
        gst_inspect_path = gst_launch_path.with_name(gst_inspect_name)
        if not gst_inspect_path.exists():
            fallback = shutil.which(gst_inspect_name)
            if fallback is None:
                return False
            gst_inspect_path = Path(fallback)
        result = subprocess.run(
            [str(gst_inspect_path), element_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return result.returncode == 0

    def _build_srt_uri(
        self,
        host: str | None,
        port: int,
        srt_mode: str,
        latency_ms: int,
    ) -> str:
        if srt_mode not in {"caller", "listener", "rendezvous"}:
            raise ValueError(f"unsupported srt_mode '{srt_mode}'")
        self._validate_srt_endpoint(mode=srt_mode, host=host)
        authority = f"{host}:{port}" if host else f":{port}"
        return f"srt://{authority}?mode={srt_mode}&latency={latency_ms}"

    def _validate_srt_endpoint(self, *, mode: str, host: str | None) -> None:
        if mode in {"caller", "rendezvous"} and not host:
            raise ValueError(f"SRT mode '{mode}' requires a host")

    def _spawn_pipeline(self, name: str, argv: list[str]) -> ManagedPipeline:
        try:
            process = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"{self.gst_launch_executable} was not found on PATH; install GStreamer and reopen the shell"
            ) from exc

        pipeline = ManagedPipeline(name=name, argv=argv, process=process)
        self._start_output_capture(pipeline)

        time.sleep(0.15)
        return_code = process.poll()
        if return_code is not None:
            tail = "\n".join(pipeline.output_tail).strip()
            suffix = f": {tail}" if tail else ""
            raise RuntimeError(f"{name} pipeline exited immediately with code {return_code}{suffix}")
        return pipeline

    def _spawn_managed_gst_pipeline(
        self,
        *,
        name: str,
        graph: str,
        srt_element_name: str,
        transport_id: str,
    ) -> CtypesManagedPipeline:
        if self._gst_runtime is None:
            self._gst_runtime = CtypesGst.load(self.gst_launch_executable)
        return CtypesManagedPipeline.start(
            name=name,
            graph=graph,
            srt_element_name=srt_element_name,
            transport_id=transport_id,
            telemetry=self.telemetry,
            runtime=self._gst_runtime,
        )

    def _start_output_capture(self, pipeline: ManagedPipeline) -> None:
        stream = getattr(pipeline.process, "stdout", None)
        if stream is None:
            return

        def pump_output() -> None:
            try:
                for line in stream:
                    text = line.rstrip()
                    if text:
                        pipeline.output_tail.append(text)
                        self._observe_gstreamer_line(text)
            finally:
                try:
                    stream.close()
                except Exception:
                    pass

        thread = threading.Thread(target=pump_output, name=f"{pipeline.name}_stdout", daemon=True)
        thread.start()

    def _observe_gstreamer_line(self, text: str) -> None:
        match = _LEVEL_MESSAGE_RE.search(text)
        if match is None:
            return

        channel = int(match.group("channel"))
        rms = self._first_level_value(match.group("rms"))
        peak = self._first_level_value(match.group("peak"))
        if match.group("direction") == "in":
            self.telemetry.observe_input_meter(channel, peak_dbfs=peak, rms_dbfs=rms)
        else:
            self.telemetry.observe_output_meter(channel, peak_dbfs=peak, rms_dbfs=rms)

    def _first_level_value(self, raw_values: str) -> float | None:
        values = [part.strip() for part in raw_values.split(",") if part.strip()]
        if not values:
            return None
        first = values[0]
        try:
            value = float(first)
        except ValueError:
            return None
        if not math.isfinite(value):
            return None
        return round(value, 3)

    def _stop_pipeline(self, pipeline: ManagedPipeline | None) -> None:
        if pipeline is None:
            return
        if hasattr(pipeline, "stop"):
            pipeline.stop()
            return
        if pipeline.process.poll() is not None:
            return
        pipeline.process.terminate()
        try:
            pipeline.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pipeline.process.kill()
            pipeline.process.wait(timeout=2)

    def _resolve_gst_launch_executable(self, executable: str) -> str:
        if Path(executable).is_file():
            return executable

        resolved = shutil.which(executable)
        if resolved:
            return resolved

        candidates = [
            Path("C:/Program Files/gstreamer/1.0/msvc_x86_64/bin/gst-launch-1.0.exe"),
            Path("C:/Program Files/gstreamer/1.0/msvc_x86/bin/gst-launch-1.0.exe"),
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)
        return executable

    def _get_srt_transport(self, config: EndpointConfig, transport_id: str) -> SrtTransportConfig:
        for transport in config.srt_transports:
            if transport.id == transport_id:
                return transport
        raise ValueError(f"unknown SRT transport '{transport_id}'")

    def _get_webrtc_stream(self, config: EndpointConfig, stream_id: str) -> WebRtcStreamConfig:
        for stream in config.webrtc_streams:
            if stream.id == stream_id:
                return stream
        raise ValueError(f"unknown WebRTC stream '{stream_id}'")
