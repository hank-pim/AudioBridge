from __future__ import annotations

import asyncio
import math
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable


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


@dataclass
class TelemetryService:
    started_at: float = field(default_factory=time.time)
    program_running: bool = False
    talkback_running: bool = False
    diagnostics: DiagnosticsState = field(default_factory=DiagnosticsState)
    _phase: float = 0.0

    def snapshot(self, channel_count: int = 8) -> dict[str, Any]:
        uptime = time.time() - self.started_at
        self._phase += 0.17
        link_up = self.program_running or self.talkback_running
        ppm = math.sin(self._phase / 10) * 2.4 if self.program_running else 0.0
        locked = self.program_running and abs(ppm) < 1.0 and uptime > 10

        return {
            "uptime_seconds": round(uptime, 1),
            "link": {
                "signaling": "standalone",
                "program": "running" if self.program_running else "stopped",
                "talkback": "running" if self.talkback_running else "stopped",
                "peer_reachable": False,
            },
            "srt": {
                "rtt_ms": round(42 + math.sin(self._phase) * 3, 1) if link_up else None,
                "rtt_variance_ms": round(2.3 + math.sin(self._phase * 1.3) * 0.4, 2) if link_up else None,
                "packets_lost": 0,
                "packets_retransmitted": 0,
                "send_bitrate_kbps": 0 if not self.program_running else 768 + random.randint(-8, 8),
                "receive_bitrate_kbps": 0 if not self.program_running else 768 + random.randint(-8, 8),
                "buffer_occupancy_ms": 240 if self.program_running else None,
            },
            "webrtc": {
                "ice_state": "connected" if self.talkback_running else "new",
                "rtt_ms": round(28 + math.sin(self._phase * 0.7) * 2, 1) if self.talkback_running else None,
                "jitter_ms": round(4 + math.sin(self._phase * 1.1) * 1.5, 1) if self.talkback_running else None,
                "packet_loss_percent": 0,
                "current_bitrate_kbps": 48 if self.talkback_running else None,
            },
            "clock": self._clock_snapshot(ppm, locked),
            "system": {
                "cpu_percent": round(12 + math.sin(self._phase / 3) * 4, 1),
                "memory_mb": 188,
                "dante_rx_kbps": 0,
                "dante_tx_kbps": 0,
                "wan_rx_kbps": 0 if not link_up else 820,
                "wan_tx_kbps": 0 if not link_up else 820,
            },
            "meters": self._meter_snapshot(channel_count),
            "diagnostics": {
                "tone_running": self.diagnostics.tone_running,
                "tone_channel": self.diagnostics.tone_channel if self.diagnostics.tone_running else None,
                "loopback_running": self.diagnostics.loopback_running,
                "monitor_running": self.diagnostics.monitor_running,
                "monitor_channel": self.diagnostics.monitor_channel if self.diagnostics.monitor_running else None,
                "monitor_is_input": self.diagnostics.monitor_is_input if self.diagnostics.monitor_running else None,
            },
        }

    def _clock_snapshot(self, ppm: float, locked: bool) -> dict[str, Any]:
        if self.program_running:
            return {
                "mode": "adaptive",
                "lock_state": "locked" if locked else "locking",
                "frequency_ratio_ppm": round(ppm, 3),
                "phase_trim_ppm": round(math.sin(self._phase / 5) * 0.12, 3),
                "buffer_occupancy_ms": 240,
                "slip_events": 0,
                "time_since_last_slip_s": None,
            }
        return {
            "mode": "adaptive",
            "lock_state": "idle",
            "frequency_ratio_ppm": 0.0,
            "phase_trim_ppm": 0.0,
            "buffer_occupancy_ms": None,
            "slip_events": 0,
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
        active = self.program_running or self.diagnostics.tone_running or self.diagnostics.loopback_running or self.diagnostics.monitor_running
        inputs = []
        outputs = []
        for ch in range(1, channel_count + 1):
            if active:
                offset = ch * 0.7
                peak = round(-6 + math.sin(self._phase + offset) * 8, 1)
                rms = round(peak - 6, 1)
            else:
                peak = -90.0
                rms = -90.0
            inputs.append({"channel": ch, "peak_dbfs": peak, "rms_dbfs": rms})
            outputs.append({"channel": ch, "peak_dbfs": round(peak - 0.5, 1) if active else -90.0, "rms_dbfs": round(rms - 0.5, 1) if active else -90.0})
        return {"inputs": inputs, "outputs": outputs}

    async def stream(self, get_channel_count: Callable[[], int] = lambda: 8) -> Any:
        while True:
            yield self.snapshot(get_channel_count())
            await asyncio.sleep(1 / 30)
