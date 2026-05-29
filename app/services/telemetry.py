from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque

from app.core.config import EndpointConfig


# Per-channel rolling window of (timestamp, fill_ms) used to compute
# estimated_drift_ppm. 60 s at ~4 Hz sampling = 240 entries; keep a little
# headroom.
_FILL_HISTORY_MAX = 300
_FILL_HISTORY_WINDOW_S = 60.0
_SLIP_RING_MAX = 50


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
class ClockChannelObservation:
    """Per-output-channel snapshot of the spine clock-recovery queue.

    Both adaptive and free-running modes write here; UI sparkline / overrun
    badges read from here. ``recent_slips`` holds the last ``_SLIP_RING_MAX``
    (timestamp, kind) pairs."""

    channel: int
    buffer_fill_ms: float | None = None
    buffer_max_ms: float | None = None
    overrun_count: int = 0
    underrun_count: int = 0
    recent_slips: Deque[tuple[float, str]] = field(default_factory=lambda: deque(maxlen=_SLIP_RING_MAX))
    # Rolling (t, fill_ms) ring used to estimate drift. Per-channel because
    # the spine queues are per-channel; per-leg drift is averaged in snapshot.
    fill_history: Deque[tuple[float, float]] = field(default_factory=lambda: deque(maxlen=_FILL_HISTORY_MAX))
    observed_at: float = field(default_factory=time.time)

    def estimated_drift_ppm(self) -> float | None:
        """Slope of fill_ms over the last _FILL_HISTORY_WINDOW_S seconds, in
        ppm. Positive = sender faster than receiver (fill rising).

        ``d ms`` of fill accumulated per second of wall time means the sender
        produced ``1 + d/1000`` seconds of audio per receiver-second →
        ``d * 1000`` ppm.
        """
        if len(self.fill_history) < 2:
            return None
        now = self.fill_history[-1][0]
        cutoff = now - _FILL_HISTORY_WINDOW_S
        recent = [(t, v) for (t, v) in self.fill_history if t >= cutoff]
        if len(recent) < 2:
            return None
        t0, v0 = recent[0]
        t1, v1 = recent[-1]
        dt = t1 - t0
        if dt <= 0:
            return None
        slope_ms_per_s = (v1 - v0) / dt
        return round(slope_ms_per_s * 1000.0, 3)


@dataclass
class ClockLegObservation:
    """Per-RX-transport derived metrics. Channel-level data lives in
    ``ClockChannelObservation``; this is the leg's roll-up plus loop-side
    bookkeeping that doesn't decompose to a channel (PLC count, applied
    ratio, lock state from the control loop, measured sender drift)."""

    transport_id: str
    opus_plc_count: int = 0
    lock_state: str | None = None
    applied_ratio_ppm: float | None = None
    # Sender-vs-local clock measurement. Driven by the opusdec pad probe in
    # gst_runtime: bytes-out / wall-elapsed → effective sender rate → ppm
    # offset vs nominal 48 kHz. Positive = sender faster than local.
    sender_rate_hz: float | None = None
    drift_ppm: float | None = None
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
    # Legacy flat-by-channel meter storage. Last-write-wins across transports —
    # fine for single-TX deployments, ambiguous for the TX bundle. Kept so the
    # existing /api/status meters payload doesn't break old UI clients.
    input_meters: dict[int, AudioMeterObservation] = field(default_factory=dict)
    output_meters: dict[int, AudioMeterObservation] = field(default_factory=dict)
    # Per-transport meter storage. Keyed transport_id -> channel -> observation.
    # The TX bundle attributes each per-channel level message to its transport
    # via the element-name lookup in CtypesManagedPipeline, then this is the
    # canonical place to look for "what is TX-A channel 1 doing right now."
    input_meters_by_transport: dict[str, dict[int, AudioMeterObservation]] = field(default_factory=dict)
    output_meters_by_transport: dict[str, dict[int, AudioMeterObservation]] = field(default_factory=dict)
    clock_observation: ClockObservation | None = None
    # New per-channel / per-leg clock-recovery telemetry. Populated by the
    # spine queue sampler in gst_runtime and (later) the adaptive control loop.
    clock_channels: dict[int, ClockChannelObservation] = field(default_factory=dict)
    clock_legs: dict[str, ClockLegObservation] = field(default_factory=dict)
    # Maps RX leg transport_id -> list of output channel indices owned by that
    # leg. Registered on RX attach, cleared on detach. Used to roll per-channel
    # data up into per-leg snapshot rows.
    clock_leg_channels: dict[str, list[int]] = field(default_factory=dict)
    # System-wide measured local capture rate. Nominally 48000.000. Drift here
    # flags local PTP / DVS clock health independent of any RX leg.
    asiosrc_measured_rate_hz: float | None = None
    observation_ttl_seconds: float = 3.0

    def mark_srt_transport(self, transport_id: str, running: bool) -> None:
        state = self.srt_transports.setdefault(transport_id, SrtTransportState())
        state.state = "running" if running else "stopped"
        state.started_at = time.time() if running else None
        if not running:
            self.srt_observations.pop(transport_id, None)
            self.input_meters_by_transport.pop(transport_id, None)
            self.output_meters_by_transport.pop(transport_id, None)
            # Drop per-leg clock telemetry; per-channel data is owned by the
            # spine and may still be valid (another RX leg might share channels).
            self.clock_legs.pop(transport_id, None)
            self.clock_leg_channels.pop(transport_id, None)
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

    def observe_input_meter(
        self,
        channel: int,
        *,
        peak_dbfs: float | None,
        rms_dbfs: float | None,
        transport_id: str | None = None,
    ) -> None:
        observation = AudioMeterObservation(peak_dbfs=peak_dbfs, rms_dbfs=rms_dbfs)
        # The "spine" sentinel is for the always-on DVS capture level meters.
        # Those should be visible per-channel under the spine transport key, but
        # must NOT be written into the per-channel global input_meters map —
        # otherwise they overwrite per-transport RX observations on the same
        # channel index and the UI shows local capture audio as RX activity.
        if transport_id != "spine":
            self.input_meters[channel] = observation
        if transport_id is not None:
            self.input_meters_by_transport.setdefault(transport_id, {})[channel] = observation

    def observe_output_meter(
        self,
        channel: int,
        *,
        peak_dbfs: float | None,
        rms_dbfs: float | None,
        transport_id: str | None = None,
    ) -> None:
        observation = AudioMeterObservation(peak_dbfs=peak_dbfs, rms_dbfs=rms_dbfs)
        self.output_meters[channel] = observation
        if transport_id is not None:
            self.output_meters_by_transport.setdefault(transport_id, {})[channel] = observation

    def observe_clock(self, **values: Any) -> None:
        current = self.clock_observation or ClockObservation()
        self.clock_observation = self._replace_observation(current, values)

    def observe_clock_channel(
        self,
        channel: int,
        *,
        buffer_fill_ms: float,
        buffer_max_ms: float,
    ) -> None:
        """Push a fresh queue-level sample for one RX output channel. Slip
        counters (``overrun_count`` / ``underrun_count`` / ``recent_slips``)
        are NOT updated here — the spine connects GstSignal handlers on each
        ``rx_clkbuf_K`` queue and increments those counters from the streaming
        thread. This method only updates the fill snapshot and drift history."""

        now = time.time()
        observation = self.clock_channels.get(channel)
        if observation is None:
            observation = ClockChannelObservation(channel=channel)
            self.clock_channels[channel] = observation

        observation.buffer_fill_ms = buffer_fill_ms
        observation.buffer_max_ms = buffer_max_ms
        observation.observed_at = now
        observation.fill_history.append((now, buffer_fill_ms))

    def observe_clock_leg(
        self,
        transport_id: str,
        *,
        opus_plc_count: int | None = None,
        lock_state: str | None = None,
        applied_ratio_ppm: float | None = None,
        sender_rate_hz: float | None = None,
        drift_ppm: float | None = None,
    ) -> None:
        leg = self.clock_legs.get(transport_id) or ClockLegObservation(transport_id=transport_id)
        if opus_plc_count is not None:
            leg.opus_plc_count = opus_plc_count
        if lock_state is not None:
            leg.lock_state = lock_state
        if applied_ratio_ppm is not None:
            leg.applied_ratio_ppm = applied_ratio_ppm
        if sender_rate_hz is not None:
            leg.sender_rate_hz = sender_rate_hz
        if drift_ppm is not None:
            leg.drift_ppm = drift_ppm
        leg.observed_at = time.time()
        self.clock_legs[transport_id] = leg

    def register_clock_leg_channels(self, transport_id: str, channels: list[int]) -> None:
        self.clock_leg_channels[transport_id] = sorted(set(channels))

    def unregister_clock_leg(self, transport_id: str) -> None:
        self.clock_leg_channels.pop(transport_id, None)
        self.clock_legs.pop(transport_id, None)

    def observe_asiosrc_rate(self, measured_hz: float) -> None:
        self.asiosrc_measured_rate_hz = round(measured_hz, 3)

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
                "packet_loss_percent": observation.packet_loss_percent if observation else None,
                "buffer_occupancy_ms": observation.buffer_occupancy_ms if observation else None,
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
                "asiosrc_measured_rate_hz": self.asiosrc_measured_rate_hz,
            },
            "meters": self._meter_snapshot(channel_count),
            "meters_by_transport": self._meter_snapshot_by_transport(),
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
        base = {
            "mode": config.program.clock_recovery_mode.value,
            "lock_state": (observation.lock_state if observation else ("idle" if not self.program_running else None)),
            "frequency_ratio_ppm": observation.frequency_ratio_ppm if observation else None,
            "phase_trim_ppm": observation.phase_trim_ppm if observation else None,
            "buffer_occupancy_ms": observation.buffer_occupancy_ms if observation else None,
            "slip_events": observation.slip_events if observation else None,
            "time_since_last_slip_s": observation.time_since_last_slip_s if observation else None,
            "per_channel": self._clock_per_channel_rows(),
            "per_leg": self._clock_per_leg_rows(config),
        }
        return base

    def _clock_per_channel_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for channel, obs in sorted(self.clock_channels.items()):
            if not self._is_recent(obs):
                continue
            last_slip = obs.recent_slips[-1] if obs.recent_slips else None
            rows.append({
                "channel": channel,
                "buffer_fill_ms": round(obs.buffer_fill_ms, 3) if obs.buffer_fill_ms is not None else None,
                "buffer_max_ms": round(obs.buffer_max_ms, 3) if obs.buffer_max_ms is not None else None,
                "overrun_count": obs.overrun_count,
                "underrun_count": obs.underrun_count,
                "estimated_drift_ppm": obs.estimated_drift_ppm(),
                "last_slip": (
                    {"timestamp": last_slip[0], "kind": last_slip[1]} if last_slip else None
                ),
                "recent_slips": [
                    {"timestamp": t, "kind": k} for (t, k) in list(obs.recent_slips)
                ],
            })
        return rows

    def _clock_per_leg_rows(self, config: EndpointConfig) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for transport_id, channels in self.clock_leg_channels.items():
            channel_obs = [
                self.clock_channels[c] for c in channels
                if c in self.clock_channels and self._is_recent(self.clock_channels[c])
            ]
            leg = self.clock_legs.get(transport_id)
            # Prefer the opusdec-probe drift (sender bytes vs wall time) — the
            # channel-fill slope is structurally zero in this spine shape
            # (demand-driven audiomixer keeps queues near empty).
            if leg is not None and leg.drift_ppm is not None:
                drift = round(leg.drift_ppm, 3)
            else:
                drift_values = [
                    obs.estimated_drift_ppm() for obs in channel_obs
                    if obs.estimated_drift_ppm() is not None
                ]
                drift = round(sum(drift_values) / len(drift_values), 3) if drift_values else None
            overrun = sum(obs.overrun_count for obs in channel_obs)
            underrun = sum(obs.underrun_count for obs in channel_obs)
            effective_mode = self._effective_clock_mode(config, transport_id)
            result[transport_id] = {
                "mode": effective_mode,
                "channels": channels,
                "estimated_drift_ppm": drift,
                "sender_rate_hz": leg.sender_rate_hz if leg else None,
                "overrun_count": overrun,
                "underrun_count": underrun,
                "opus_plc_count": leg.opus_plc_count if leg else 0,
                "lock_state": leg.lock_state if leg else None,
                "applied_ratio_ppm": leg.applied_ratio_ppm if leg else None,
            }
        return result

    def _effective_clock_mode(self, config: EndpointConfig, transport_id: str) -> str:
        for transport in config.srt_transports:
            if transport.id == transport_id:
                mode = transport.clock_recovery_mode or config.program.clock_recovery_mode
                return mode.value
        return config.program.clock_recovery_mode.value

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

    def _meter_snapshot_by_transport(self) -> dict[str, dict[str, list[dict[str, Any]]]]:
        """Per-transport meters, only including transport ids that have a recent
        observation. Each channel list is sorted by channel index."""
        result: dict[str, dict[str, list[dict[str, Any]]]] = {}

        def rows(channels: dict[int, AudioMeterObservation]) -> list[dict[str, Any]]:
            return [
                {
                    "channel": channel,
                    "peak_dbfs": observation.peak_dbfs,
                    "rms_dbfs": observation.rms_dbfs,
                }
                for channel, observation in sorted(channels.items())
                if self._is_recent(observation)
            ]

        transport_ids = set(self.input_meters_by_transport) | set(self.output_meters_by_transport)
        for transport_id in transport_ids:
            input_rows = rows(self.input_meters_by_transport.get(transport_id, {}))
            output_rows = rows(self.output_meters_by_transport.get(transport_id, {}))
            if not input_rows and not output_rows:
                continue
            result[transport_id] = {"inputs": input_rows, "outputs": output_rows}
        return result

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
