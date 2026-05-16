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
from app.services.audio_devices import discover_audio_interfaces
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
    # ``_srt_transport_pipelines`` is the API-facing lookup keyed by transport id.
    # RX transports map to their own per-transport pipeline. TX transports all map
    # to the same shared ``_tx_bundle`` pipeline (multiple keys → same object) so
    # ASIO/DVS is opened exactly once. ``_tx_bundle_transport_ids`` tracks which
    # transports are currently members of the bundle so we know when to rebuild
    # vs stop it. See docs/single-pipeline-tx-plan.md.
    _srt_transport_pipelines: dict[str, Any] = field(default_factory=dict)
    _tx_bundle: Any | None = None
    _tx_bundle_transport_ids: set[str] = field(default_factory=set)
    # Transport-id -> branch handle for TX legs attached to the always-on spine.
    # When this map is non-empty the spine TX path is in use; the legacy
    # _tx_bundle path stays around for tests and for any caller that explicitly
    # chooses the bundle. The spine path is preferred when audio.interface_driver
    # is supported (it avoids the rebuild-glitch problem entirely).
    _spine_tx_branches: dict[str, str] = field(default_factory=dict)
    _spine_rx_branches: dict[str, str] = field(default_factory=dict)
    # Most recent EndpointConfig observed during a TX start. ``stop_srt_transport``
    # uses it to rebuild the bundle without re-plumbing config through callers.
    _latest_tx_config: EndpointConfig | None = None
    # The always-on full-duplex spine that owns DVS. Built lazily on the first
    # spine/TX/RX start in the new dynamic-pipeline architecture; TX and RX legs
    # will attach to its per-channel tee and audiomixer elements without ever
    # restarting the spine itself. See plan_spine() in media_graph.py.
    _spine_pipeline: Any | None = None
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
                "Dante TX capture depends on the selected host driver and device being available to GStreamer",
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
        # Detach any spine TX branches before tearing the spine down, so each
        # leg's srtsink sees its EOS and closes its socket cleanly.
        for transport_id in list(self._spine_tx_branches):
            self.stop_srt_transport(transport_id)
        for transport_id in list(self._spine_rx_branches):
            self.stop_srt_transport(transport_id)
        self.stop_spine()
        # Drop the bundle in one shot rather than thrashing through rebuilds as
        # each TX leg is removed individually.
        if self._tx_bundle is not None:
            self._stop_pipeline(self._tx_bundle)
            self._tx_bundle = None
            for member_id in list(self._tx_bundle_transport_ids):
                self._srt_transport_pipelines.pop(member_id, None)
                self.telemetry.mark_srt_transport(member_id, False)
            self._tx_bundle_transport_ids = set()
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

        builder = MediaGraphBuilder(gst_launch_executable=self.gst_launch_executable)
        graph_plan = builder.plan_srt_transport(config, transport_id, raise_on_error=True)
        kind = "receiver" if transport.direction == SrtTransportDirection.rx else "sender"
        runtime_note: str

        spine_tx_eligible = (
            transport.direction == SrtTransportDirection.tx
            and self._tx_leg_eligible_for_spine(config, transport)
        )
        spine_rx_eligible = (
            transport.direction == SrtTransportDirection.rx
            and self._rx_leg_eligible_for_spine(config, transport)
        )
        if spine_tx_eligible:
            # Spine TX path: build the per-transport branch and attach it to the
            # always-on spine. The spine owns DVS, so attaching/detaching a TX
            # leg never restarts capture and never glitches other legs. The
            # spine is started lazily on first TX so existing flows still work
            # when only RX is in use. Non-dante-input legs (e.g. diagnostic
            # tone TX) fall through to the legacy bundle path below.
            if self._spine_pipeline is None:
                self.start_spine(config)
            spine = self._spine_pipeline
            assert spine is not None
            builder = MediaGraphBuilder(gst_launch_executable=self.gst_launch_executable)
            leg_plan = builder.plan_tx_leg_branch(config, transport_id, raise_on_error=True)
            branch = spine.attach_branch_multi(
                tap_names=leg_plan["tap_names"],
                entry_element_names=leg_plan["entry_element_names"],
                description=leg_plan["branch_description"],
            )
            self._spine_tx_branches[transport_id] = branch.handle
            # Telemetry attribution: register this transport's SRT element so
            # the poll loop can read srtsink stats per leg out of the spine.
            self._register_spine_srt_endpoint(transport_id, leg_plan["srt_element_name"])
            for endpoint in leg_plan["meter_endpoints"]:
                spine.meter_lookup[endpoint["element_name"]] = (
                    endpoint["transport_id"], endpoint["direction"], endpoint["channel"],
                )
            pipeline = spine
            self._srt_transport_pipelines[transport_id] = pipeline
            runtime_note = (
                "TX leg attached to the always-on spine; no DVS reopen, no glitch on other legs"
            )
        elif spine_rx_eligible:
            if self._spine_pipeline is None:
                self.start_spine(config)
            spine = self._spine_pipeline
            assert spine is not None
            builder = MediaGraphBuilder(gst_launch_executable=self.gst_launch_executable)
            leg_plan = builder.plan_rx_leg_branch(config, transport_id, raise_on_error=True)
            
            branch = spine.attach_branch_outputs_multi(
                mixer_names=leg_plan["mixer_names"],
                exit_element_names=leg_plan["exit_element_names"],
                description=leg_plan["branch_description"],
            )
            self._spine_rx_branches[transport_id] = branch.handle

            self._register_spine_srt_endpoint(transport_id, leg_plan["srt_element_name"])
            for endpoint in leg_plan["meter_endpoints"]:
                spine.meter_lookup[endpoint["element_name"]] = (
                    endpoint["transport_id"], endpoint["direction"], endpoint["channel"],
                )
            pipeline = spine
            self._srt_transport_pipelines[transport_id] = pipeline
            runtime_note = (
                "RX leg decodes into the always-on spine output bus directly via audiomixer; DVS stays open in the spine"
            )
        elif transport.direction == SrtTransportDirection.tx:
            # Legacy bundle path: kept for non-dante TX legs (tones/silence in
            # diagnostics) and for environments where audio isn't configured.
            new_members = self._tx_bundle_transport_ids | {transport_id}
            pipeline = self._restart_tx_bundle(config, new_members)
            for member_id in new_members:
                self._srt_transport_pipelines[member_id] = pipeline
            self._tx_bundle_transport_ids = new_members
            runtime_note = (
                "TX graph runs inside the legacy endpoint bundle (non-dante sources); the spine path was skipped"
            )
        else:
            pipeline = self._spawn_managed_gst_pipeline(
                name=f"srt_transport_{transport_id}_{transport.direction.value}",
                graph=graph_plan["gstreamer"]["graph"],
                srt_element_name=graph_plan["gstreamer"]["srt_element_name"],
                transport_id=transport_id,
            )
            self._srt_transport_pipelines[transport_id] = pipeline
            runtime_note = "RX graph planned from SRT ingress with a named monitor tap"

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
            "runtime_note": runtime_note,
            "graph_plan": graph_plan,
            "process": pipeline.describe(),
        }

    def stop_srt_transport(self, transport_id: str) -> None:
        # Spine TX path: detach the branch from the running spine without
        # touching DVS or any other TX leg.
        if transport_id in self._spine_tx_branches:
            handle = self._spine_tx_branches.pop(transport_id)
            spine = self._spine_pipeline
            if spine is not None:
                try:
                    spine.detach_branch(handle)
                except Exception:
                    # If detach fails the leg is already gone; the user-visible
                    # state should still settle to "stopped" so we don't leave a
                    # zombie transport id around.
                    pass
                self._unregister_spine_srt_endpoint(transport_id)
            self._srt_transport_pipelines.pop(transport_id, None)
            self.telemetry.mark_srt_transport(transport_id, False)
            return

        if transport_id in self._spine_rx_branches:
            handle = self._spine_rx_branches.pop(transport_id)
            spine = self._spine_pipeline
            if spine is not None:
                try:
                    spine.detach_branch(handle)
                except Exception:
                    pass
                self._unregister_spine_srt_endpoint(transport_id)
            self._srt_transport_pipelines.pop(transport_id, None)
            self.telemetry.mark_srt_transport(transport_id, False)
            return

        if transport_id in self._tx_bundle_transport_ids:
            # Drop this leg from the bundle. If others remain, rebuild without it;
            # otherwise tear the bundle down.
            remaining = self._tx_bundle_transport_ids - {transport_id}
            self._srt_transport_pipelines.pop(transport_id, None)
            if remaining and self._latest_tx_config is not None:
                pipeline = self._restart_tx_bundle(self._latest_tx_config, remaining)
                for member_id in remaining:
                    self._srt_transport_pipelines[member_id] = pipeline
                self._tx_bundle_transport_ids = remaining
            else:
                self._stop_pipeline(self._tx_bundle)
                self._tx_bundle = None
                self._tx_bundle_transport_ids = set()
            self.telemetry.mark_srt_transport(transport_id, False)
            return

        pipeline = self._srt_transport_pipelines.pop(transport_id, None)
        self._stop_pipeline(pipeline)
        self.telemetry.mark_srt_transport(transport_id, False)

    def _restart_tx_bundle(self, config: EndpointConfig, member_ids: set[str]) -> Any:
        """Stop the current TX bundle (if any) and start a fresh one with ``member_ids``.

        ``member_ids`` is the full desired membership after the operation.
        """
        builder = MediaGraphBuilder(gst_launch_executable=self.gst_launch_executable)
        # Plan a bundle filtered to the requested members.
        filtered_config = self._config_with_tx_members(config, member_ids)
        plan = builder.plan_endpoint_tx_bundle(filtered_config)
        gstreamer = plan["gstreamer"]
        if not plan["valid"] or gstreamer is None or not gstreamer.get("argv"):
            raise RuntimeError(
                "TX bundle plan is empty or invalid: " + "; ".join(error.get("message", "") for error in plan["errors"])
            )

        if self._tx_bundle is not None:
            self._stop_pipeline(self._tx_bundle)
            self._tx_bundle = None

        srt_endpoints = [
            (endpoint["transport_id"], endpoint["element_name"])
            for endpoint in gstreamer["srt_endpoints"]
        ]
        meter_lookup = {
            endpoint["element_name"]: (
                endpoint["transport_id"],
                endpoint["direction"],
                endpoint["channel"],
            )
            for endpoint in gstreamer.get("meter_endpoints", [])
        }
        pipeline = self._spawn_managed_tx_bundle(
            name=f"tx_bundle_{'_'.join(sorted(member_ids))}",
            graph=gstreamer["graph"],
            srt_endpoints=srt_endpoints,
            meter_lookup=meter_lookup,
        )
        self._tx_bundle = pipeline
        self._latest_tx_config = config
        return pipeline

    def _spawn_managed_tx_bundle(
        self,
        *,
        name: str,
        graph: str,
        srt_endpoints: list[tuple[str, str]],
        meter_lookup: dict[str, tuple[str, str, int]] | None = None,
    ) -> Any:
        """Wrapper around CtypesManagedPipeline.start_bundle for monkeypatching in tests."""
        if self._gst_runtime is None:
            self._gst_runtime = CtypesGst.load(self.gst_launch_executable)
        return CtypesManagedPipeline.start_bundle(
            name=name,
            graph=graph,
            srt_endpoints=srt_endpoints,
            telemetry=self.telemetry,
            runtime=self._gst_runtime,
            meter_lookup=meter_lookup,
        )

    @staticmethod
    def _config_with_tx_members(config: EndpointConfig, member_ids: set[str]) -> EndpointConfig:
        """Return an EndpointConfig view whose srt_transports keep only TX legs in ``member_ids``.

        RX transports pass through unchanged so existing validators still see them.
        """
        kept = [
            transport for transport in config.srt_transports
            if transport.direction != SrtTransportDirection.tx or transport.id in member_ids
        ]
        return config.model_copy(update={"srt_transports": kept})

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
        # De-duplicate by object identity so a shared TX bundle (with multiple
        # transport ids pointing at the same pipeline) only appears once.
        seen: set[int] = set()
        pipelines: list[Any] = []

        def add(candidate: Any | None) -> None:
            if candidate is None or id(candidate) in seen:
                return
            seen.add(id(candidate))
            pipelines.append(candidate)

        add(self._spine_pipeline)
        add(self._program_pipeline)
        add(self._tone_pipeline)
        add(self._monitor_pipeline)
        add(self._tx_bundle)
        for pipeline in self._srt_transport_pipelines.values():
            add(pipeline)
        return [pipeline.describe(include_output_tail=include_output_tail) for pipeline in pipelines]

    def discover_audio_interfaces(self) -> list[dict[str, Any]]:
        return discover_audio_interfaces(self.gst_launch_executable)

    # --- spine (always-on full-duplex DVS pipeline) ---

    def start_spine(self, config: EndpointConfig) -> dict[str, Any]:
        """Start the always-on full-duplex spine. Idempotent: a second call
        returns the running pipeline's description without rebuilding.

        The spine opens DVS once (capture + playback) and exposes per-channel
        tee/audiomixer attach points for TX/RX legs. In this first commit it is
        a standalone diagnostic so we can verify the full-duplex DVS open
        before wiring it into TX/RX flows.
        """
        if self._spine_pipeline is not None:
            return {
                "running": True,
                "already_running": True,
                "process": self._spine_pipeline.describe(),
            }

        plan = MediaGraphBuilder(gst_launch_executable=self.gst_launch_executable).plan_spine(config)
        if not plan["valid"] or plan["gstreamer"] is None:
            raise RuntimeError(
                "spine plan is invalid: " + "; ".join(error.get("message", "") for error in plan["errors"])
            )

        pipeline = self._spawn_managed_spine_pipeline(
            name="spine_full_duplex",
            graph=plan["gstreamer"]["graph"],
            channel_count=plan["channel_count"],
        )
        self._spine_pipeline = pipeline
        return {
            "running": True,
            "already_running": False,
            "channel_count": plan["channel_count"],
            "capture_tee_names": plan["capture_tee_names"],
            "playback_mixer_names": plan["playback_mixer_names"],
            "asiosrc_element_name": plan["gstreamer"]["asiosrc_element_name"],
            "asiosink_element_name": plan["gstreamer"]["asiosink_element_name"],
            "process": pipeline.describe(),
        }

    def stop_spine(self) -> None:
        if self._spine_pipeline is None:
            return
        self._stop_pipeline(self._spine_pipeline)
        self._spine_pipeline = None

    def describe_spine(self) -> dict[str, Any]:
        if self._spine_pipeline is None:
            return {"running": False}
        return {"running": True, "process": self._spine_pipeline.describe()}

    def _tx_leg_eligible_for_spine(self, config: EndpointConfig, transport: SrtTransportConfig) -> bool:
        """Spine TX is used when every source for the leg is a dante_input AND
        the audio interface driver is one the spine can open (i.e. real
        capture). Non-dante sources (tones, silence) and unconfigured audio
        interfaces stay on the legacy bundle path.
        """
        from app.core.config import SourceKind

        if config.audio.interface_driver not in {"asio", "wasapi", "coreaudio", "alsa"}:
            return False
        if not config.audio.interface_name:
            return False
        source_by_id = {s.id: s for s in config.sources if s.enabled}
        group_by_id = {g.id: g for g in config.encode_groups if g.enabled}
        for group_id in transport.encode_group_ids:
            group = group_by_id.get(group_id)
            if group is None:
                return False
            for channel in group.channels:
                source = source_by_id.get(channel.source_id or "")
                if source is None or source.kind != SourceKind.dante_input:
                    return False
        return True

    def _rx_leg_eligible_for_spine(self, config: EndpointConfig, transport: SrtTransportConfig) -> bool:
        """RX spine legs require a configured playback device and an encode
        group whose decoded channels fit the DVS output channel count."""
        if config.audio.interface_driver not in {"asio", "wasapi", "coreaudio", "alsa"}:
            return False
        if not config.audio.interface_name:
            return False
        group_by_id = {g.id: g for g in config.encode_groups if g.enabled}
        for group_id in transport.encode_group_ids:
            group = group_by_id.get(group_id)
            if group is None:
                return False
            if group.channel_count > config.audio.channel_count:
                return False
        return bool(transport.encode_group_ids)

    def _register_spine_srt_endpoint(self, transport_id: str, element_name: str) -> None:
        """Attach a TX leg's srtsink to the spine's srt_endpoints list so the
        runtime poll loop can read srtsink.stats and attribute them to this
        transport. The element pointer is resolved by name on the live pipeline.
        """
        spine = self._spine_pipeline
        if spine is None or self._gst_runtime is None:
            return
        element_ptr = self._gst_runtime.gst.gst_bin_get_by_name(
            spine.pipeline, element_name.encode("utf-8"),
        )
        if not element_ptr:
            return
        spine.srt_endpoints.append((transport_id, element_ptr))

    def _unregister_spine_srt_endpoint(self, transport_id: str) -> None:
        spine = self._spine_pipeline
        if spine is None or self._gst_runtime is None:
            return
        remaining: list[tuple[str, int]] = []
        for tid, element_ptr in spine.srt_endpoints:
            if tid == transport_id and element_ptr:
                self._gst_runtime.gobject.g_object_unref(element_ptr)
                continue
            remaining.append((tid, element_ptr))
        spine.srt_endpoints = remaining

    def _spawn_managed_spine_pipeline(
        self,
        *,
        name: str,
        graph: str,
        channel_count: int,
    ) -> Any:
        """Like _spawn_managed_gst_pipeline but with no srt endpoint registration.

        The spine has no srtsink/srtsrc; it owns DVS only. Bus polling still
        runs so we get level messages and pipeline error visibility.

        ``meter_lookup`` attributes the spine's per-channel ``dbmeter_in_spine_K``
        level elements to a sentinel transport id ("spine") instead of letting
        them fall through to the legacy "trailing-int is the channel" parser,
        which would write local DVS capture levels into the per-channel global
        ``input_meters[K]`` slot and clobber per-transport RX observations on
        the same channel index — causing the UI to display local capture audio
        as if it were RX activity.
        """
        if self._gst_runtime is None:
            self._gst_runtime = CtypesGst.load(self.gst_launch_executable)
        meter_lookup: dict[str, tuple[str, str, int]] = {
            f"dbmeter_in_spine_{ch}": ("spine", "in", ch)
            for ch in range(1, channel_count + 1)
        }
        return CtypesManagedPipeline.start_bundle(
            name=name,
            graph=graph,
            srt_endpoints=[],
            telemetry=self.telemetry,
            runtime=self._gst_runtime,
            meter_lookup=meter_lookup,
        )

    def start_tx_capture_spine(self, config: EndpointConfig) -> dict[str, Any]:
        """Start the capture-only spine used for dynamic TX leg attachment."""
        if self._spine_pipeline is not None:
            return {
                "running": True,
                "already_running": True,
                "process": self._spine_pipeline.describe(),
            }

        plan = MediaGraphBuilder(gst_launch_executable=self.gst_launch_executable).plan_tx_capture_spine(config)
        if not plan["valid"] or plan["gstreamer"] is None:
            raise RuntimeError(
                "TX capture spine plan is invalid: " + "; ".join(error.get("message", "") for error in plan["errors"])
            )

        pipeline = self._spawn_managed_spine_pipeline(
            name="tx_capture_spine",
            graph=plan["gstreamer"]["graph"],
            channel_count=plan["channel_count"],
        )
        self._spine_pipeline = pipeline
        return {
            "running": True,
            "already_running": False,
            "channel_count": plan["channel_count"],
            "capture_tee_names": plan["capture_tee_names"],
            "asiosrc_element_name": plan["gstreamer"]["asiosrc_element_name"],
            "process": pipeline.describe(),
        }

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
