from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import (
    EncodeGroupConfig,
    EndpointConfig,
    SourceConfig,
    SourceKind,
    SrtTransportConfig,
    SrtTransportDirection,
)


class MediaGraphValidationError(ValueError):
    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__("; ".join(error["message"] for error in errors))


@dataclass(frozen=True)
class MediaGraphBuilder:
    gst_launch_executable: str = "gst-launch-1.0"

    def plan_endpoint(self, config: EndpointConfig) -> dict[str, Any]:
        transport_plans = []
        errors: list[dict[str, Any]] = []
        for transport in config.srt_transports:
            try:
                transport_plans.append(self.plan_srt_transport(config, transport.id, raise_on_error=True))
            except MediaGraphValidationError as exc:
                errors.extend(exc.errors)
                transport_plans.append(self._invalid_transport_plan(config, transport, exc.errors))

        return {
            "valid": not errors,
            "errors": errors,
            "srt_transports": transport_plans,
        }

    def plan_srt_transport(
        self,
        config: EndpointConfig,
        transport_id: str,
        *,
        raise_on_error: bool = False,
    ) -> dict[str, Any]:
        transport = self._find_transport(config, transport_id)
        errors = self._validate_transport(config, transport)
        if errors:
            if raise_on_error:
                raise MediaGraphValidationError(errors)
            return self._invalid_transport_plan(config, transport, errors)

        group_by_id = {group.id: group for group in config.encode_groups if group.enabled}
        selected_groups = [group_by_id[group_id] for group_id in transport.encode_group_ids]
        port = transport.port or config.network.srt_port
        latency_ms = transport.latency_ms or config.program.srt_latency_ms
        uri = self._build_srt_uri(transport.host, port, transport.mode.value, latency_ms)
        srt_element_name = self._srt_element_name(transport.id, transport.direction.value)

        if transport.direction == SrtTransportDirection.rx:
            monitor_taps = [self._rx_monitor_tap(transport.id)]
            argv = self._build_rx_argv(transport, uri, srt_element_name, monitor_taps[0]["id"])
            graph = " ".join(argv[2:])
            sources: list[dict[str, Any]] = []
        else:
            argv = self._build_tx_argv(config, transport, selected_groups, uri)
            graph = self._argv_to_graph(argv)
            sources = self._planned_sources(config, selected_groups)
            monitor_taps = self._tx_monitor_taps(transport, selected_groups)

        return {
            "valid": True,
            "errors": [],
            "transport": self._transport_payload(config, transport),
            "groups": [self._group_payload(group) for group in selected_groups],
            "sources": sources,
            "gstreamer": {
                "argv": argv,
                "graph": graph,
                "srt_element_name": srt_element_name,
                "monitor_taps": monitor_taps,
            },
        }

    def _validate_transport(self, config: EndpointConfig, transport: SrtTransportConfig) -> list[dict[str, Any]]:
        errors: list[dict[str, Any]] = []
        group_by_id = {group.id: group for group in config.encode_groups if group.enabled}
        source_by_id = {source.id: source for source in config.sources if source.enabled}

        if transport.direction == SrtTransportDirection.tx:
            if not transport.encode_group_ids:
                errors.append(self._error(transport.id, None, "tx_transport_has_no_groups", "TX SRT transport has no encode groups"))
            total_channels = sum(
                len(group_by_id[g].channels)
                for g in transport.encode_group_ids
                if g in group_by_id
            )
            if total_channels > 1:
                errors.append(self._error(
                    transport.id, None, "multi_channel_unsupported",
                    "TX SRT transport currently supports a single channel per transport (rtpopuspay)",
                ))

        if transport.direction == SrtTransportDirection.tx and transport.mode.value in {"caller", "rendezvous"} and not transport.host:
            errors.append(self._error(transport.id, None, "transport_host_required", "SRT caller and rendezvous transports require host"))

        for group_id in transport.encode_group_ids:
            group = group_by_id.get(group_id)
            if group is None:
                errors.append(self._error(transport.id, group_id, "missing_encode_group", f"encode group '{group_id}' was not found"))
                continue
            if not group.channels:
                errors.append(self._error(transport.id, group.id, "empty_encode_group", f"encode group '{group.id}' has no channels"))
                continue
            channel_indices = {channel.index for channel in group.channels}
            expected_indices = set(range(1, group.channel_count + 1))
            if channel_indices != expected_indices:
                errors.append(
                    self._error(
                        transport.id,
                        group.id,
                        "wrong_channel_count",
                        f"encode group '{group.id}' declares {group.channel_count} channels but maps {len(channel_indices)}",
                    )
                )
            for channel in group.channels:
                if not channel.source_id:
                    errors.append(
                        self._error(
                            transport.id,
                            group.id,
                            "missing_source_id",
                            f"encode group '{group.id}' channel {channel.index} has no source_id",
                            channel.index,
                        )
                    )
                    continue
                source = source_by_id.get(channel.source_id)
                if source is None:
                    errors.append(
                        self._error(
                            transport.id,
                            group.id,
                            "missing_source_id",
                            f"source '{channel.source_id}' referenced by encode group '{group.id}' channel {channel.index} was not found",
                            channel.index,
                            source_id=channel.source_id,
                        )
                    )
                    continue
                if transport.direction == SrtTransportDirection.tx and source.kind not in {SourceKind.tone, SourceKind.silence}:
                    errors.append(
                        self._error(
                            transport.id,
                            group.id,
                            "unsupported_source_kind",
                            f"source '{source.id}' kind '{source.kind.value}' is not supported by the first media runtime slice",
                            channel.index,
                            source_id=source.id,
                        )
                    )
        return errors

    def _build_tx_argv(
        self,
        config: EndpointConfig,
        transport: SrtTransportConfig,
        groups: list[EncodeGroupConfig],
        uri: str,
    ) -> list[str]:
        source_by_id = {source.id: source for source in config.sources if source.enabled}
        argv = [self.gst_launch_executable, "-m"]
        # Single-channel path: encoder feeds a tee, tee feeds rtpopuspay → srtsink.
        # Multi-channel will need mpegtsmux + dynamic pad linking; not yet supported.
        for group in groups:
            for channel in sorted(group.channels, key=lambda item: item.index):
                source = source_by_id[channel.source_id or ""]
                tap_name = self._tx_monitor_tap_name(transport.id, group.id, channel.index)
                argv.extend(
                    [
                        self._source_element(source),
                        "is-live=true",
                        *self._source_properties(source),
                        "!",
                        "audioconvert",
                        "!",
                        "audioresample",
                        "!",
                        "audio/x-raw,rate=48000,channels=1",
                        "!",
                        "level",
                        f"name={self._meter_element_name(group.id, channel.index)}",
                        "message=true",
                        "interval=100000000",
                        "!",
                        "opusenc",
                        f"bitrate={group.opus.bitrate_kbps * 1000}",
                        "!",
                        "tee",
                        f"name={tap_name}",
                        "allow-not-linked=true",
                        f"{tap_name}.",
                        "!",
                        "queue",
                        "!",
                        "rtpopuspay",
                        "pt=96",
                        "!",
                        "srtsink",
                        f"name={self._srt_element_name(transport.id, transport.direction.value)}",
                        f"uri={uri}",
                        "wait-for-connection=true",
                    ]
                )
        return argv

    def _build_rx_argv(
        self,
        transport: SrtTransportConfig,
        uri: str,
        srt_element_name: str,
        tap_name: str,
    ) -> list[str]:
        return [
            self.gst_launch_executable,
            "-m",
            "srtsrc",
            f"name={srt_element_name}",
            f"uri={uri}",
            "!",
            "application/x-rtp,media=audio,encoding-name=OPUS,clock-rate=48000,encoding-params=(string)1,payload=96",
            "!",
            "rtpjitterbuffer",
            "latency=40",
            "!",
            "rtpopusdepay",
            "!",
            "tee",
            f"name={tap_name}",
            "allow-not-linked=true",
            f"{tap_name}.",
            "!",
            "queue",
            "!",
            "opusdec",
            "!",
            "level",
            f"name={self._rx_meter_element_name(transport.id)}",
            "message=true",
            "interval=100000000",
            "!",
            "audioconvert",
            "!",
            "audioresample",
            "!",
            "fakesink",
            "sync=false",
        ]

    def _source_element(self, source: SourceConfig) -> str:
        if source.kind in {SourceKind.tone, SourceKind.silence}:
            return "audiotestsrc"
        raise ValueError(f"unsupported source kind '{source.kind.value}'")

    def _source_properties(self, source: SourceConfig) -> list[str]:
        if source.kind == SourceKind.silence:
            return ["wave=silence"]
        # tone_level_dbfs is the RMS target; sine peak is +3.01 dB above RMS.
        peak_dbfs = source.tone_level_dbfs + 3.0103
        volume = 10 ** (peak_dbfs / 20)
        return [
            "wave=sine",
            f"freq={source.tone_frequency_hz or 1000.0}",
            f"volume={volume:.6f}",
        ]

    def _meter_element_name(self, group_id: str, channel_index: int) -> str:
        safe_group = "".join(char if char.isalnum() else "_" for char in group_id)
        return f"dbmeter_out_{safe_group}_{channel_index}"

    def _rx_meter_element_name(self, transport_id: str) -> str:
        safe_transport = "".join(char if char.isalnum() else "_" for char in transport_id)
        return f"dbmeter_in_{safe_transport}_1"

    def _srt_element_name(self, transport_id: str, direction: str) -> str:
        safe_transport = "".join(char if char.isalnum() else "_" for char in transport_id)
        return f"srtstats_{direction}_{safe_transport}"

    def _rx_monitor_tap(self, transport_id: str) -> dict[str, Any]:
        return {
            "id": self._rx_monitor_tap_name(transport_id),
            "direction": "rx",
            "stage": "post-depay-pre-decode",
            "codec": "opus",
            "channel_index": 1,
        }

    def _tx_monitor_taps(self, transport: SrtTransportConfig, groups: list[EncodeGroupConfig]) -> list[dict[str, Any]]:
        taps: list[dict[str, Any]] = []
        for group in groups:
            for channel in sorted(group.channels, key=lambda item: item.index):
                taps.append({
                    "id": self._tx_monitor_tap_name(transport.id, group.id, channel.index),
                    "direction": transport.direction.value,
                    "stage": "post-encode-pre-pay",
                    "codec": "opus",
                    "group_id": group.id,
                    "channel_index": channel.index,
                    "source_id": channel.source_id,
                })
        return taps

    def _rx_monitor_tap_name(self, transport_id: str) -> str:
        safe_transport = "".join(char if char.isalnum() else "_" for char in transport_id)
        return f"monitor_tap_rx_{safe_transport}"

    def _tx_monitor_tap_name(self, transport_id: str, group_id: str, channel_index: int) -> str:
        safe_transport = "".join(char if char.isalnum() else "_" for char in transport_id)
        safe_group = "".join(char if char.isalnum() else "_" for char in group_id)
        return f"monitor_tap_tx_{safe_transport}_{safe_group}_{channel_index}"

    def _planned_sources(self, config: EndpointConfig, groups: list[EncodeGroupConfig]) -> list[dict[str, Any]]:
        source_by_id = {source.id: source for source in config.sources if source.enabled}
        planned = []
        for group in groups:
            for channel in sorted(group.channels, key=lambda item: item.index):
                source = source_by_id[channel.source_id or ""]
                planned.append({
                    "group_id": group.id,
                    "channel_index": channel.index,
                    "source_id": source.id,
                    "kind": source.kind.value,
                    "name": source.name,
                })
        return planned

    def _invalid_transport_plan(
        self,
        config: EndpointConfig,
        transport: SrtTransportConfig,
        errors: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "valid": False,
            "errors": errors,
            "transport": self._transport_payload(config, transport),
            "groups": [],
            "sources": [],
            "gstreamer": {
                "argv": [],
                "graph": None,
                "srt_element_name": None,
                "monitor_taps": [],
            },
        }

    def _find_transport(self, config: EndpointConfig, transport_id: str) -> SrtTransportConfig:
        for transport in config.srt_transports:
            if transport.id == transport_id:
                return transport
        raise ValueError(f"unknown SRT transport '{transport_id}'")

    def _transport_payload(self, config: EndpointConfig, transport: SrtTransportConfig) -> dict[str, Any]:
        return {
            "id": transport.id,
            "name": transport.name,
            "direction": transport.direction.value,
            "mode": transport.mode.value,
            "host": transport.host,
            "port": transport.port or config.network.srt_port,
            "latency_ms": transport.latency_ms or config.program.srt_latency_ms,
            "encode_group_ids": transport.encode_group_ids,
        }

    def _group_payload(self, group: EncodeGroupConfig) -> dict[str, Any]:
        return {
            "id": group.id,
            "name": group.name,
            "channel_count": group.channel_count,
            "opus": group.opus.model_dump(mode="json", exclude_none=True),
            "channels": [channel.model_dump(mode="json", exclude_none=True) for channel in sorted(group.channels, key=lambda item: item.index)],
        }

    def _build_srt_uri(self, host: str | None, port: int, srt_mode: str, latency_ms: int) -> str:
        authority = f"{host}:{port}" if host else f":{port}"
        return f"srt://{authority}?mode={srt_mode}&latency={latency_ms}"

    def _argv_to_graph(self, argv: list[str]) -> str:
        graph_start = 1
        while graph_start < len(argv) and argv[graph_start].startswith("-"):
            graph_start += 1
        return " ".join(argv[graph_start:])

    def _error(
        self,
        transport_id: str,
        group_id: str | None,
        code: str,
        message: str,
        channel_index: int | None = None,
        *,
        source_id: str | None = None,
    ) -> dict[str, Any]:
        return {
            "transport_id": transport_id,
            "group_id": group_id,
            "channel_index": channel_index,
            "source_id": source_id,
            "code": code,
            "message": message,
        }
