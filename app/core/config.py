from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, SecretStr, field_validator, model_validator


class SrtMode(str, Enum):
    caller = "caller"
    listener = "listener"
    rendezvous = "rendezvous"


class ClockRecoveryMode(str, Enum):
    adaptive = "adaptive"
    free_running = "free_running"


class EncryptionStrength(str, Enum):
    aes128 = "aes-128"
    aes192 = "aes-192"
    aes256 = "aes-256"


class NetworkConfig(BaseModel):
    dante_nic: str | None = None
    wan_nic: str | None = None
    public_address: str | None = None
    signaling_port: int = Field(default=8443, ge=1, le=65535)
    srt_port: int = Field(default=9000, ge=1, le=65535)
    stun_servers: list[str] = Field(
        default_factory=lambda: [
            "stun:stun.l.google.com:19302",
            "stun:stun.cloudflare.com:3478",
        ]
    )
    turn_server: str | None = None
    turn_username: str | None = None
    turn_password: SecretStr | None = None


class StreamType(str, Enum):
    """Operator label for what kind of audio this stream carries.
    Decoupled from transport — a PL can ride SRT or WebRTC."""
    pgm = "PGM"
    pl = "PL"
    ifb = "IFB"
    src = "SRC"
    bus = "BUS"
    aux = "AUX"
    tone = "TONE"


class StreamTransport(str, Enum):
    srt = "srt"        # multiplexed onto the program SRT pipe
    webrtc = "webrtc"  # carried on the WebRTC talkback path


class StreamDirection(str, Enum):
    tx = "tx"  # leaving this endpoint toward the peer
    rx = "rx"  # arriving at this endpoint from the peer


class SourceKind(str, Enum):
    dante_input = "dante_input"
    tone = "tone"
    silence = "silence"
    webrtc_stream = "webrtc_stream"


class SourceConfig(BaseModel):
    id: str
    name: str
    kind: SourceKind = SourceKind.dante_input
    dante_channel: int | None = Field(default=None, ge=1, le=64)
    webrtc_stream_id: str | None = None
    tone_frequency_hz: float | None = Field(default=None, gt=0)
    tone_level_dbfs: float = Field(default=-20.0, le=0.0, ge=-60.0)
    enabled: bool = True


class OpusStreamConfig(BaseModel):
    bitrate_kbps: int = Field(default=96, ge=16, le=512)
    bitrate_mode: Literal["cbr", "vbr", "cvbr"] = "cbr"
    frame_ms: int = Field(default=20, ge=2, le=60)
    complexity: int = Field(default=7, ge=0, le=10)
    inband_fec: bool = True
    expected_packet_loss_percent: int = Field(default=5, ge=0, le=30)


class WebRtcStreamConfig(BaseModel):
    id: str
    name: str
    direction: StreamDirection
    source_id: str | None = None
    enabled: bool = True
    opus: OpusStreamConfig | None = None


class EncodeGroupChannelConfig(BaseModel):
    index: int = Field(ge=1, le=64)
    source_id: str | None = None
    label: str | None = None
    gain_db: float = 0.0


class EncodeGroupConfig(BaseModel):
    id: str
    name: str
    channel_count: int = Field(default=2, ge=1, le=64)
    channels: list[EncodeGroupChannelConfig] = Field(default_factory=list)
    opus: OpusStreamConfig = Field(default_factory=OpusStreamConfig)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_channels(self) -> "EncodeGroupConfig":
        seen: set[int] = set()
        for channel in self.channels:
            if channel.index > self.channel_count:
                raise ValueError("encode group channel index cannot exceed channel_count")
            if channel.index in seen:
                raise ValueError("encode group channel indices must be unique")
            seen.add(channel.index)
        return self


class SrtTransportDirection(str, Enum):
    tx = "tx"
    rx = "rx"


class SrtTransportConfig(BaseModel):
    id: str
    name: str
    direction: SrtTransportDirection
    mode: SrtMode = SrtMode.listener
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    latency_ms: int | None = Field(default=None, ge=20, le=8000)
    encryption_enabled: bool = True
    encryption_strength: EncryptionStrength = EncryptionStrength.aes256
    passphrase: SecretStr | None = None
    encode_group_ids: list[str] = Field(default_factory=list)
    enabled: bool = True


class StreamConfig(BaseModel):
    name: str
    type: StreamType = StreamType.src
    transport: StreamTransport = StreamTransport.srt
    direction: StreamDirection
    dante_channel: int | None = Field(default=None, ge=1, le=64)
    # Per-stream OPUS encode/decode parameters. None inherits from the
    # bridge defaults (program.opus for SRT streams, talkback.* for
    # WebRTC streams).
    opus: OpusStreamConfig | None = None
    enabled: bool = True


class AudioConfig(BaseModel):
    interface_name: str | None = None
    interface_driver: Literal["wasapi", "coreaudio", "alsa", "unknown"] = "unknown"
    sample_rate: int = 48000
    channel_count: int = Field(default=8, ge=1, le=64)
    channel_labels: list[str] = Field(default_factory=lambda: [f"Channel {i}" for i in range(1, 9)])
    streams: list[StreamConfig] = Field(default_factory=list)

    @field_validator("sample_rate")
    @classmethod
    def only_48k(cls, value: int) -> int:
        if value != 48000:
            raise ValueError("Dante Bridge v1 only supports 48 kHz")
        return value

    @field_validator("channel_labels")
    @classmethod
    def labels_must_not_exceed_channel_limit(cls, value: list[str]) -> list[str]:
        if len(value) > 64:
            raise ValueError("channel_labels cannot exceed 64 entries")
        return value

    @field_validator("streams")
    @classmethod
    def streams_must_not_exceed_limit(cls, value: list[StreamConfig]) -> list[StreamConfig]:
        if len(value) > 128:
            raise ValueError("streams cannot exceed 128 entries (TX + RX combined)")
        return value


class AdaptiveClockConfig(BaseModel):
    convergence_window_seconds: int = Field(default=10, ge=5, le=60)
    steady_window_seconds: int = Field(default=300, ge=60, le=900)
    lock_ppm_threshold: float = Field(default=1.0, ge=0.1, le=10)
    lock_hold_seconds: int = Field(default=30, ge=5, le=120)
    ratio_clamp_ppm: float = Field(default=50.0, ge=1, le=200)


class FreeRunningClockConfig(BaseModel):
    jitter_buffer_ms: int = Field(default=500, ge=20, le=5000)
    underrun_policy: Literal["silence", "repeat_last_sample"] = "silence"


class ProgramConfig(BaseModel):
    enabled: bool = False
    srt_mode: SrtMode = SrtMode.listener
    srt_latency_ms: int = Field(default=240, ge=20, le=8000)
    srt_bandwidth_mode: Literal["auto", "manual"] = "auto"
    srt_overhead_bandwidth_percent: int = Field(default=25, ge=0, le=100)
    encryption_enabled: bool = True
    encryption_strength: EncryptionStrength = EncryptionStrength.aes256
    srt_passphrase: SecretStr | None = None
    inbound_bandwidth_cap_kbps: int | None = Field(default=None, ge=64, le=100000)
    clock_recovery_mode: ClockRecoveryMode = ClockRecoveryMode.adaptive
    opus: OpusStreamConfig = Field(default_factory=OpusStreamConfig)
    adaptive_clock: AdaptiveClockConfig = Field(default_factory=AdaptiveClockConfig)
    free_running_clock: FreeRunningClockConfig = Field(default_factory=FreeRunningClockConfig)


class TalkbackConfig(BaseModel):
    enabled: bool = False
    output_channel: int = Field(default=1, ge=1, le=64)
    opus_bitrate_kbps: int = Field(default=48, ge=12, le=128)
    opus_bitrate_mode: Literal["cbr", "vbr", "cvbr"] = "cbr"
    frame_ms: int = Field(default=10, ge=5, le=20)
    restricted_lowdelay: bool = False


class RouteMap(BaseModel):
    program_inputs: dict[int, int] = Field(default_factory=dict)
    program_outputs: dict[int, int] = Field(default_factory=dict)
    talkback_output_channel: int = Field(default=1, ge=1, le=64)


class PairingConfig(BaseModel):
    peer_name: str | None = None
    peer_signaling_url: str | None = None
    bearer_token: SecretStr | None = None


class UiConfig(BaseModel):
    theme: Literal["dark", "light"] = "dark"
    meter_units: Literal["dbfs"] = "dbfs"
    meter_ballistics: Literal["fast", "vu"] = "fast"


class EndpointConfig(BaseModel):
    schema_version: int = 1
    endpoint_name: str = "Dante Bridge Endpoint"
    network: NetworkConfig = Field(default_factory=NetworkConfig)
    audio: AudioConfig = Field(default_factory=AudioConfig)
    sources: list[SourceConfig] = Field(default_factory=list)
    encode_groups: list[EncodeGroupConfig] = Field(default_factory=list)
    srt_transports: list[SrtTransportConfig] = Field(default_factory=list)
    webrtc_streams: list[WebRtcStreamConfig] = Field(default_factory=list)
    routes: RouteMap = Field(default_factory=RouteMap)
    program: ProgramConfig = Field(default_factory=ProgramConfig)
    talkback: TalkbackConfig = Field(default_factory=TalkbackConfig)
    pairing: PairingConfig = Field(default_factory=PairingConfig)
    ui: UiConfig = Field(default_factory=UiConfig)


APP_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = APP_ROOT / "config/endpoint.toml"
