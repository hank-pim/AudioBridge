from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import EndpointConfig
from app.services.telemetry import DiagnosticsState, TelemetryService


@dataclass
class MediaController:
    telemetry: TelemetryService

    @property
    def _diag(self) -> DiagnosticsState:
        return self.telemetry.diagnostics

    # --- program / talkback ---

    def describe_program_pipeline(self, config: EndpointConfig) -> dict[str, Any]:
        return {
            "capture": config.audio.interface_name or "unselected",
            "channels": config.audio.channel_count,
            "encode": "opus-mono-per-channel",
            "transport": f"srt-{config.program.srt_mode.value}",
            "latency_ms": config.program.srt_latency_ms,
            "clock_recovery": config.program.clock_recovery_mode.value,
        }

    def start_program(self) -> None:
        self.telemetry.program_running = True

    def stop_program(self) -> None:
        self.telemetry.program_running = False

    def start_talkback(self) -> None:
        self.telemetry.talkback_running = True

    def stop_talkback(self) -> None:
        self.telemetry.talkback_running = False

    # --- tone generator ---

    def start_tone(
        self,
        frequency_hz: float = 1000.0,
        level_dbfs: float = -18.0,
        channel: int = 1,
        waveform: str = "sine",
    ) -> None:
        self._diag.tone_running = True
        self._diag.tone_frequency_hz = frequency_hz
        self._diag.tone_level_dbfs = level_dbfs
        self._diag.tone_channel = channel
        self._diag.tone_waveform = waveform

    def stop_tone(self) -> None:
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

    def start_monitor(self, channel: int, is_input: bool = True) -> None:
        self._diag.monitor_running = True
        self._diag.monitor_channel = channel
        self._diag.monitor_is_input = is_input

    def stop_monitor(self) -> None:
        self._diag.monitor_running = False

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
