from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.core.config import EndpointConfig


@dataclass
class DiagnosticsState:
    tone_running: bool = False
    tone_frequency_hz: float = 1000.0
    tone_level_dbfs: float = -18.0
    tone_channel: int = 1
    tone_waveform: str = "sine"
    loopback_running: bool = False
    loopback_input_channels: list[int] = field(default_factory=list)
    loopback_output_channels: list[int] = field(default_factory=list)
    monitor_running: bool = False
    monitor_channel: int = 1
    monitor_is_input: bool = True
    monitor_transport_id: str | None = None


@dataclass
class SrtTransportState:
    state: str = "stopped"
    started_at: float | None = None


@dataclass
class WebRtcStreamState:
    state: str = "stopped"
    started_at: float | None = None


@dataclass
class AudioMeterObservation:
    peak_dbfs: float | None = None
    rms_dbfs: float | None = None
    observed_at: float = field(default_factory=time.time)


@dataclass
class SrtTransportObservation:
    bitrate_kbps: float | None = None
    rtt_ms: float | None = None
    rtt_variance_ms: float | None = None
    packets_lost: int | None = None
    packet_loss_percent: float | None = None
    packets_retransmitted: int | None = None
    send_bitrate_kbps: float | None = None
    receive_bitrate_kbps: float | None = None
    buffer_occupancy_ms: float | None = None
    raw_stats: str | None = None
    observed_at: float = field(default_factory=time.time)


@dataclass
class WebRtcStreamObservation:
    bitrate_kbps: float | None = None
    rtt_ms: float | None = None
    jitter_ms: float | None = None
    packet_loss_percent: float | None = None
    ice_state: str | None = None
    current_bitrate_kbps: float | None = None
    observed_at: float = field(default_factory=time.time)


@dataclass
class ClockObservation:
    lock_state: str | None = None
    frequency_ratio_ppm: float | None = None
    phase_trim_ppm: float | None = None
    buffer_occupancy_ms: float | None = None
    slip_events: int | None = None
    time_since_last_slip_s: float | None = None
    observed_at: float = field(default_factory=time.time)


@dataclass
class TelemetryService:
    started_at: float = field(default_factory=time.time)
    program_running: bool = False
    talkback_running: bool = False
    diagnostics: DiagnosticsState = field(default_factory=DiagnosticsState)
    srt_transports: dict[str, SrtTransportState] = field(default_factory=dict)
    webrtc_streams: dict[str, WebRtcStreamState] = field(default_factory=dict)
    srt_observations: dict[str, SrtTransportObservation] = field(default_factory=dict)
    webrtc_observations: dict[str, WebRtcStreamObservation] = field(default_factory=dict)
    input_meters: dict[int, AudioMeterObservation] = field(default_factory=dict)
    output_meters: dict[int, AudioMeterObservation] = field(default_factory=dict)
    clock_observation: ClockObservation | None = None
    observation_ttl_seconds: float = 3.0

    def mark_srt_transport(self, transport_id: str, running: bool) -> None:
        state = self.srt_transports.setdefault(transport_id, SrtTransportState())
        state.state = "running" if running else "stopped"
        state.started_at = time.time() if running else None
        if not running:
            self.srt_observations.pop(transport_id, None)
        self.program_running = any(item.state == "running" for item in self.srt_transports.values())

    def mark_webrtc_stream(self, stream_id: str, running: bool) -> None:
        state = self.webrtc_streams.setdefault(stream_id, WebRtcStreamState())
        state.state = "running" if running else "stopped"
        state.started_at = time.time() if running else None
        if not running:
            self.webrtc_observations.pop(stream_id, None)
        self.talkback_running = any(item.state == "running" for item in self.webrtc_streams.values())

    def observe_srt_transport(self, transport_id: str, **values: Any) -> None:
        current = self.srt_observations.get(transport_id, SrtTransportObservation())
        self.srt_observations[transport_id] = self._replace_observation(current, values)

    def observe_webrtc_stream(self, stream_id: str, **values: Any) -> None:
        current = self.webrtc_observations.get(stream_id, WebRtcStreamObservation())
        self.webrtc_observations[stream_id] = self._replace_observation(current, values)

    def observe_input_meter(self, channel: int, *, peak_dbfs: float | None, rms_dbfs: float | None) -> None:
        self.input_meters[channel] = AudioMeterObservation(peak_dbfs=peak_dbfs, rms_dbfs=rms_dbfs)

    def observe_output_meter(self, channel: int, *, peak_dbfs: float | None, rms_dbfs: float | None) -> None:
        self.output_meters[channel] = AudioMeterObservation(peak_dbfs=peak_dbfs, rms_dbfs=rms_dbfs)

    def observe_clock(self, **values: Any) -> None:
        current = self.clock_observation or ClockObservation()
        self.clock_observation = self._replace_observation(current, values)

    def has_recent_media_observations(self) -> bool:
        now = time.time()
        collections = [
            *self.srt_observations.values(),
            *self.webrtc_observations.values(),
            *self.input_meters.values(),
            *self.output_meters.values(),
        ]
        if self.clock_observation is not None:
            collections.append(self.clock_observation)
        return any(self._is_recent(item, now) for item in collections)

    def has_recent_audio_meters(self) -> bool:
        now = time.time()
        return any(
            self._is_recent(item, now)
            for item in [*self.input_meters.values(), *self.output_meters.values()]
        )

    def snapshot(self, config: EndpointConfig, runtime: dict[str, Any] | None = None) -> dict[str, Any]:
        uptime = time.time() - self.started_at
        channel_count = config.audio.channel_count
        running_srt = [transport for transport in config.srt_transports if self.srt_transports.get(transport.id, SrtTransportState()).state == "running"]

        srt_rows = []
        for transport in config.srt_transports:
            state = self.srt_transports.get(transport.id, SrtTransportState())
            observation = self._recent_or_none(self.srt_observations.get(transport.id))
            srt_rows.append({
                "id": transport.id,
                "name": transport.name,
                "direction": transport.direction.value,
                "mode": transport.mode.value,
                "host": transport.host,
                "port": transport.port or config.network.srt_port,
                "encode_group_ids": transport.encode_group_ids,
                "state": state.state,
                "configured_bitrate_kbps": self._configured_srt_bitrate_kbps(config, transport.encode_group_ids),
                "bitrate_kbps": observation.bitrate_kbps if observation else None,
                "rtt_ms": observation.rtt_ms if observation else None,
                "latency_ms": transport.latency_ms or config.program.srt_latency_ms,
            })

        webrtc_rows = []
        for stream in config.webrtc_streams:
            state = self.webrtc_streams.get(stream.id, WebRtcStreamState())
            observation = self._recent_or_none(self.webrtc_observations.get(stream.id))
            webrtc_rows.append({
                "id": stream.id,
                "name": stream.name,
                "direction": stream.direction.value,
                "source_id": stream.source_id,
                "state": state.state,
                "configured_bitrate_kbps": config.talkback.opus_bitrate_kbps,
                "bitrate_kbps": observation.bitrate_kbps if observation else None,
                "rtt_ms": observation.rtt_ms if observation else None,
            })

        srt_summary = self._srt_summary()
        webrtc_summary = self._webrtc_summary()

        return {
            "uptime_seconds": round(uptime, 1),
            "runtime": runtime or {},
            "link": {
                "signaling": "standalone",
                "program": "running" if self.program_running else "stopped",
                "talkback": "running" if self.talkback_running else "stopped",
                "peer_reachable": False,
            },
            "srt": {
                "rtt_ms": srt_summary.get("rtt_ms"),
                "rtt_variance_ms": srt_summary.get("rtt_variance_ms"),
                "packets_lost": srt_summary.get("packets_lost"),
                "packet_loss_percent": srt_summary.get("packet_loss_percent"),
                "packets_retransmitted": srt_summary.get("packets_retransmitted"),
                "send_bitrate_kbps": srt_summary.get("send_bitrate_kbps"),
                "receive_bitrate_kbps": srt_summary.get("receive_bitrate_kbps"),
                "buffer_occupancy_ms": srt_summary.get("buffer_occupancy_ms"),
            },
            "webrtc": {
                "ice_state": webrtc_summary.get("ice_state"),
                "rtt_ms": webrtc_summary.get("rtt_ms"),
                "jitter_ms": webrtc_summary.get("jitter_ms"),
                "packet_loss_percent": webrtc_summary.get("packet_loss_percent"),
                "current_bitrate_kbps": webrtc_summary.get("current_bitrate_kbps"),
            },
            "srt_transports": srt_rows,
            "webrtc_streams": webrtc_rows,
            "encode_groups": [
                {
                    "id": group.id,
                    "name": group.name,
                    "channel_count": group.channel_count,
                    "source_ids": [channel.source_id for channel in group.channels],
                    "transport_ids": [transport.id for transport in running_srt if group.id in transport.encode_group_ids],
                }
                for group in config.encode_groups
            ],
            "clock": self._clock_snapshot(config),
            "system": {
                "cpu_percent": None,
                "memory_mb": None,
                "dante_rx_kbps": None,
                "dante_tx_kbps": None,
                "wan_rx_kbps": None,
                "wan_tx_kbps": None,
            },
            "meters": self._meter_snapshot(channel_count),
            "diagnostics": {
                "tone_running": self.diagnostics.tone_running,
                "tone_channel": self.diagnostics.tone_channel if self.diagnostics.tone_running else None,
                "loopback_running": self.diagnostics.loopback_running,
                "monitor_running": self.diagnostics.monitor_running,
                "monitor_channel": self.diagnostics.monitor_channel if self.diagnostics.monitor_running else None,
                "monitor_is_input": self.diagnostics.monitor_is_input if self.diagnostics.monitor_running else None,
                "monitor_transport_id": self.diagnostics.monitor_transport_id if self.diagnostics.monitor_running else None,
            },
        }

    def _configured_srt_bitrate_kbps(self, config: EndpointConfig, group_ids: list[str]) -> int | None:
        groups = [group for group in config.encode_groups if group.id in group_ids]
        if not groups:
            return None
        return sum(group.opus.bitrate_kbps for group in groups)

    def _clock_snapshot(self, config: EndpointConfig) -> dict[str, Any]:
        observation = self._recent_or_none(self.clock_observation)
        if observation is not None:
            return {
                "mode": config.program.clock_recovery_mode.value,
                "lock_state": observation.lock_state,
                "frequency_ratio_ppm": observation.frequency_ratio_ppm,
                "phase_trim_ppm": observation.phase_trim_ppm,
                "buffer_occupancy_ms": observation.buffer_occupancy_ms,
                "slip_events": observation.slip_events,
                "time_since_last_slip_s": observation.time_since_last_slip_s,
            }
        if self.program_running:
            return {
                "mode": config.program.clock_recovery_mode.value,
                "lock_state": None,
                "frequency_ratio_ppm": None,
                "phase_trim_ppm": None,
                "buffer_occupancy_ms": None,
                "slip_events": None,
                "time_since_last_slip_s": None,
            }
        return {
            "mode": config.program.clock_recovery_mode.value,
            "lock_state": "idle",
            "frequency_ratio_ppm": None,
            "phase_trim_ppm": None,
            "buffer_occupancy_ms": None,
            "slip_events": None,
            "time_since_last_slip_s": None,
        }

    def _clock_snapshot_free_running(self) -> dict[str, Any]:
        return {
            "mode": "free_running",
            "lock_state": "running" if self.program_running else "idle",
            "glitch_events": 0,
            "glitch_interval_avg_ms": None,
            "last_glitch_ago_s": None,
            "accumulated_offset_ms": 0.0,
            "buffer_occupancy_ms": 500 if self.program_running else None,
        }

    def _meter_snapshot(self, channel_count: int) -> dict[str, Any]:
        inputs = []
        outputs = []
        for ch in range(1, channel_count + 1):
            input_meter = self._recent_or_none(self.input_meters.get(ch))
            output_meter = self._recent_or_none(self.output_meters.get(ch))
            inputs.append({
                "channel": ch,
                "peak_dbfs": input_meter.peak_dbfs if input_meter else None,
                "rms_dbfs": input_meter.rms_dbfs if input_meter else None,
            })
            outputs.append({
                "channel": ch,
                "peak_dbfs": output_meter.peak_dbfs if output_meter else None,
                "rms_dbfs": output_meter.rms_dbfs if output_meter else None,
            })
        return {"inputs": inputs, "outputs": outputs}

    def _srt_summary(self) -> dict[str, Any]:
        observations = [item for item in self.srt_observations.values() if self._is_recent(item)]
        return {
            "rtt_ms": self._average([item.rtt_ms for item in observations]),
            "rtt_variance_ms": self._average([item.rtt_variance_ms for item in observations]),
            "packets_lost": self._sum_int([item.packets_lost for item in observations]),
            "packet_loss_percent": self._average([item.packet_loss_percent for item in observations]),
            "packets_retransmitted": self._sum_int([item.packets_retransmitted for item in observations]),
            "send_bitrate_kbps": self._sum_float([item.send_bitrate_kbps for item in observations]),
            "receive_bitrate_kbps": self._sum_float([item.receive_bitrate_kbps for item in observations]),
            "buffer_occupancy_ms": self._average([item.buffer_occupancy_ms for item in observations]),
        }

    def _webrtc_summary(self) -> dict[str, Any]:
        observations = [item for item in self.webrtc_observations.values() if self._is_recent(item)]
        ice_states = [item.ice_state for item in observations if item.ice_state is not None]
        return {
            "ice_state": ice_states[0] if ice_states else None,
            "rtt_ms": self._average([item.rtt_ms for item in observations]),
            "jitter_ms": self._average([item.jitter_ms for item in observations]),
            "packet_loss_percent": self._average([item.packet_loss_percent for item in observations]),
            "current_bitrate_kbps": self._sum_float([item.current_bitrate_kbps for item in observations]),
        }

    def _replace_observation(self, current: Any, values: dict[str, Any]) -> Any:
        allowed = set(current.__dataclass_fields__) - {"observed_at"}  # type: ignore[attr-defined]
        payload = {name: getattr(current, name) for name in allowed}
        payload.update({key: value for key, value in values.items() if key in allowed})
        payload["observed_at"] = time.time()
        return type(current)(**payload)

    def _recent_or_none(self, observation: Any | None) -> Any | None:
        if observation is None:
            return None
        return observation if self._is_recent(observation) else None

    def _is_recent(self, observation: Any, now: float | None = None) -> bool:
        return ((now or time.time()) - observation.observed_at) <= self.observation_ttl_seconds

    def _average(self, values: list[float | int | None]) -> float | None:
        real = [float(value) for value in values if value is not None]
        if not real:
            return None
        return round(sum(real) / len(real), 3)

    def _sum_float(self, values: list[float | int | None]) -> float | None:
        real = [float(value) for value in values if value is not None]
        if not real:
            return None
        return round(sum(real), 3)

    def _sum_int(self, values: list[int | None]) -> int | None:
        real = [int(value) for value in values if value is not None]
        if not real:
            return None
        return sum(real)

    async def stream(
        self,
        get_config: Callable[[], EndpointConfig],
        get_runtime: Callable[[], dict[str, Any] | None] | None = None,
    ) -> Any:
        while True:
            runtime = get_runtime() if get_runtime is not None else None
            yield self.snapshot(get_config(), runtime)
            await asyncio.sleep(1 / 30)
