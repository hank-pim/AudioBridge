from __future__ import annotations

import time

import pytest

from app.core.config import EndpointConfig, SrtTransportConfig, SrtTransportDirection
from app.services.telemetry import (
    ClockChannelObservation,
    TelemetryService,
    _SLIP_RING_MAX,
)


def test_observe_clock_channel_records_fill_and_max() -> None:
    telemetry = TelemetryService()
    telemetry.observe_clock_channel(1, buffer_fill_ms=12.0, buffer_max_ms=60.0)
    obs = telemetry.clock_channels[1]
    assert obs.buffer_fill_ms == 12.0
    assert obs.buffer_max_ms == 60.0
    assert obs.overrun_count == 0
    assert obs.underrun_count == 0


def test_observe_clock_channel_does_not_modify_slip_counters() -> None:
    """Polling-derived heuristic was removed; only the GstSignal handler in
    CtypesManagedPipeline mutates slip counts now."""
    telemetry = TelemetryService()
    telemetry.observe_clock_channel(1, buffer_fill_ms=60.0, buffer_max_ms=60.0)
    telemetry.observe_clock_channel(1, buffer_fill_ms=0.0, buffer_max_ms=60.0)
    obs = telemetry.clock_channels[1]
    assert obs.overrun_count == 0
    assert obs.underrun_count == 0
    assert len(obs.recent_slips) == 0


def test_estimated_drift_ppm_returns_positive_when_fill_is_rising() -> None:
    obs = ClockChannelObservation(channel=1)
    t0 = time.time()
    # 0.5 ms/sec rise sustained over 10 s → +500 ppm sender-faster.
    for i in range(11):
        obs.fill_history.append((t0 + i, 10.0 + i * 0.5))
    ppm = obs.estimated_drift_ppm()
    assert ppm is not None
    assert 480 <= ppm <= 520


def test_estimated_drift_ppm_returns_none_with_insufficient_data() -> None:
    obs = ClockChannelObservation(channel=1)
    assert obs.estimated_drift_ppm() is None
    obs.fill_history.append((time.time(), 10.0))
    assert obs.estimated_drift_ppm() is None


def test_recent_slips_ring_is_bounded() -> None:
    obs = ClockChannelObservation(channel=1)
    for i in range(_SLIP_RING_MAX + 25):
        obs.recent_slips.append((float(i), "overrun"))
    assert len(obs.recent_slips) == _SLIP_RING_MAX
    # Oldest entries were evicted; deque keeps the tail.
    assert obs.recent_slips[0][0] == float(25)


def test_unregister_clock_leg_clears_leg_state() -> None:
    telemetry = TelemetryService()
    telemetry.register_clock_leg_channels("rx-a", [1, 2])
    telemetry.observe_clock_leg("rx-a", lock_state="initializing")
    assert "rx-a" in telemetry.clock_legs
    telemetry.unregister_clock_leg("rx-a")
    assert "rx-a" not in telemetry.clock_legs
    assert "rx-a" not in telemetry.clock_leg_channels


def test_mark_srt_transport_stopped_clears_leg_telemetry() -> None:
    telemetry = TelemetryService()
    telemetry.mark_srt_transport("rx-a", running=True)
    telemetry.register_clock_leg_channels("rx-a", [1, 2])
    telemetry.observe_clock_leg("rx-a", lock_state="locked")
    telemetry.mark_srt_transport("rx-a", running=False)
    assert "rx-a" not in telemetry.clock_legs
    assert "rx-a" not in telemetry.clock_leg_channels


def _minimal_config(rx_id: str = "rx-a") -> EndpointConfig:
    return EndpointConfig(
        srt_transports=[
            SrtTransportConfig(id=rx_id, name=rx_id, direction=SrtTransportDirection.rx),
        ],
    )


def test_snapshot_includes_per_channel_and_per_leg_clock_rows() -> None:
    telemetry = TelemetryService()
    telemetry.mark_srt_transport("rx-a", running=True)
    telemetry.register_clock_leg_channels("rx-a", [1, 2])
    telemetry.observe_clock_channel(1, buffer_fill_ms=30.0, buffer_max_ms=60.0)
    telemetry.observe_clock_channel(2, buffer_fill_ms=45.0, buffer_max_ms=60.0)
    telemetry.observe_clock_leg("rx-a", lock_state="initializing")

    snapshot = telemetry.snapshot(_minimal_config())
    per_channel = snapshot["clock"]["per_channel"]
    per_leg = snapshot["clock"]["per_leg"]

    channels = {row["channel"]: row for row in per_channel}
    assert channels[1]["buffer_fill_ms"] == 30.0
    assert channels[2]["buffer_max_ms"] == 60.0

    assert "rx-a" in per_leg
    assert per_leg["rx-a"]["channels"] == [1, 2]
    assert per_leg["rx-a"]["lock_state"] == "initializing"


def test_snapshot_exposes_asiosrc_measured_rate() -> None:
    telemetry = TelemetryService()
    telemetry.observe_asiosrc_rate(48000.123)
    snapshot = telemetry.snapshot(_minimal_config())
    assert snapshot["system"]["asiosrc_measured_rate_hz"] == pytest.approx(48000.123)
