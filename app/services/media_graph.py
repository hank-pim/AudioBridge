from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.config import (
    AudioConfig,
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
            branch_plan = self.plan_rx_leg_branch(config, transport_id, raise_on_error=True)
            monitor_taps = branch_plan["monitor_taps"]
            argv = []
            graph = branch_plan["branch_description"]
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
            for g in transport.encode_group_ids:
                group = group_by_id.get(g)
                if group and group.channel_count > 8:
                    errors.append(self._error(
                        transport.id, group.id, "opus_multichannel_max_8",
                        f"encode group '{group.id}' has {group.channel_count} channels; native multichannel OPUS supports up to 8",
                    ))

        if transport.direction == SrtTransportDirection.rx:
            if not transport.encode_group_ids:
                errors.append(self._error(transport.id, None, "rx_transport_has_no_groups", "RX SRT transport has no decode/output group"))
            if config.audio.interface_driver == "unknown" or not config.audio.interface_name:
                errors.append(self._error(
                    transport.id, None, "audio_interface_not_selected",
                    "audio interface must be selected before starting RX playback",
                ))
            elif config.audio.interface_driver not in {"wasapi", "coreaudio", "alsa", "asio"}:
                errors.append(self._error(
                    transport.id, None, "unsupported_audio_driver",
                    f"audio driver '{config.audio.interface_driver}' is not supported for RX playback",
                ))
            for g in transport.encode_group_ids:
                group = group_by_id.get(g)
                if group and group.channel_count > config.audio.channel_count:
                    errors.append(self._error(
                        transport.id, group.id, "rx_output_channel_out_of_range",
                        f"encode group '{group.id}' has {group.channel_count} decoded channels but the interface has {config.audio.channel_count} outputs",
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
                if transport.direction == SrtTransportDirection.tx:
                    if source.kind == SourceKind.dante_input:
                        if not source.dante_channel:
                            errors.append(self._error(
                                transport.id, group.id, "dante_source_missing_channel",
                                f"dante_input source '{source.id}' has no dante_channel set",
                                channel.index, source_id=source.id,
                            ))
                        elif config.audio.interface_driver == "unknown" or not config.audio.interface_name:
                            errors.append(self._error(
                                transport.id, group.id, "audio_interface_not_selected",
                                "audio interface must be selected before using dante_input sources",
                                channel.index, source_id=source.id,
                            ))
                        elif config.audio.interface_driver not in {"wasapi", "coreaudio", "alsa", "asio"}:
                            errors.append(self._error(
                                transport.id, group.id, "unsupported_audio_driver",
                                f"audio driver '{config.audio.interface_driver}' is not supported for dante capture",
                                channel.index, source_id=source.id,
                            ))
                        elif source.dante_channel > config.audio.channel_count:
                            errors.append(self._error(
                                transport.id, group.id, "dante_channel_out_of_range",
                                f"dante_channel {source.dante_channel} exceeds interface channel_count {config.audio.channel_count}",
                                channel.index, source_id=source.id,
                            ))
                    elif source.kind not in {SourceKind.tone, SourceKind.silence}:
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
        # Multichannel-native OPUS: each source leg is mono (with its own level + tee
        # for per-channel metering and monitoring), then all legs are interleaved
        # into one N-channel buffer that feeds a single opusenc. This preserves
        # phase alignment across channels because every channel shares encoder
        # framing.
        #
        # Source legs are heterogeneous:
        #   - tone/silence channels start with their own audiotestsrc.
        #   - dante_input channels pull from a single shared capture node
        #     (wasapisrc/osxaudiosrc/alsasrc) that is deinterleaved into per-
        #     channel src pads. Multiple dante channels in the same TX (or
        #     across encode groups within the same pipeline) share that
        #     capture so the audio device is opened exactly once.
        source_by_id = {source.id: source for source in config.sources if source.enabled}
        argv = [self.gst_launch_executable, "-m"]
        if not groups:
            return argv
        # Only one encode group per TX is wired today.
        group = groups[0]
        n = group.channel_count
        interleave_name = self._interleave_element_name(group.id)
        srt_name = self._srt_element_name(transport.id, transport.direction.value)

        # Sink chain: interleave -> caps -> opusenc -> rtpopuspay -> srtsink.
        argv.extend([
            "interleave",
            f"name={interleave_name}",
            "!",
            f"audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels={n},channel-mask=(bitmask){self._channel_mask_hex(n)}",
            "!",
            "opusenc",
            f"bitrate={group.opus.bitrate_kbps * 1000}",
            "!",
            "rtpopuspay",
            "pt=96",
            "!",
            "srtsink",
            f"name={srt_name}",
            f"uri={uri}",
            "wait-for-connection=true",
        ])

        # Shared dante capture node, only emitted if any channel needs it.
        sorted_channels = sorted(group.channels, key=lambda item: item.index)
        any_dante = any(
            source_by_id[ch.source_id or ""].kind == SourceKind.dante_input
            for ch in sorted_channels
            if (ch.source_id or "") in source_by_id
        )
        deinterleave_name = self._deinterleave_element_name(group.id)
        dante_pad_by_channel: dict[int, int] = {}
        if any_dante:
            dante_channels = [
                source_by_id[channel.source_id or ""].dante_channel or 1
                for channel in sorted_channels
                if source_by_id[channel.source_id or ""].kind == SourceKind.dante_input
            ]
            capture_args, capture_channel_count, dante_pad_by_channel = self._dante_capture_plan(
                config.audio,
                dante_channels,
            )
            argv.extend([
                *capture_args,
                "!",
                "audioconvert",
                "!",
                "audioresample",
                "!",
                f"audio/x-raw,rate=48000,channels={capture_channel_count},format=S16LE",
                "!",
                "deinterleave",
                f"name={deinterleave_name}",
            ])

        # Per-channel branches into interleave.sink_K.
        for channel in sorted_channels:
            source = source_by_id[channel.source_id or ""]
            tap_name = self._tx_monitor_tap_name(transport.id, group.id, channel.index)
            sink_pad = f"{interleave_name}.sink_{channel.index - 1}"

            if source.kind == SourceKind.dante_input:
                # Pull a specific channel from the shared deinterleave.
                argv.extend([
                    f"{deinterleave_name}.src_{dante_pad_by_channel[source.dante_channel or 1]}",
                    "!",
                    "queue",
                    "!",
                    "audioconvert",
                    "!",
                    f"audio/x-raw,format=S16LE,rate=48000,channels=1,channel-mask=(bitmask){self._channel_mask_bit_hex(n, channel.index)}",
                    "!",
                    "level",
                    f"name={self._meter_element_name(group.id, channel.index)}",
                    "message=true",
                    "interval=100000000",
                    "!",
                    "tee",
                    f"name={tap_name}",
                    "allow-not-linked=true",
                    f"{tap_name}.",
                    "!",
                    "queue",
                    "!",
                    sink_pad,
                ])
            else:
                argv.extend([
                    self._source_element(source),
                    "is-live=true",
                    *self._source_properties(source),
                    "!",
                    "audioconvert",
                    "!",
                    "audioresample",
                    "!",
                    f"audio/x-raw,format=S16LE,rate=48000,channels=1,channel-mask=(bitmask){self._channel_mask_bit_hex(n, channel.index)}",
                    "!",
                    "level",
                    f"name={self._meter_element_name(group.id, channel.index)}",
                    "message=true",
                    "interval=100000000",
                    "!",
                    "tee",
                    f"name={tap_name}",
                    "allow-not-linked=true",
                    f"{tap_name}.",
                    "!",
                    "queue",
                    "!",
                    sink_pad,
                ])
        return argv

    # --- endpoint-bundle TX planner ---
    #
    # ASIO (and the DVS ASIO driver in particular) is single-client per device: a
    # second GStreamer process opening asiosrc against DVS fails preroll, which is
    # why TX SRT transports cannot each run in their own subprocess. The bundle
    # planner emits ONE gst-launch argv per endpoint that opens the capture device
    # exactly once and fans out (via a single deinterleave) into a per-TX sink
    # chain (interleave -> opusenc -> rtpopuspay -> srtsink). See
    # docs/single-pipeline-tx-plan.md for background.

    _BUNDLE_DEINTERLEAVE_NAME = "dante_in_shared"

    def plan_endpoint_tx_bundle(self, config: EndpointConfig) -> dict[str, Any]:
        """Plan one gst-launch invocation containing every enabled TX SRT transport.

        Returns ``{"valid", "errors", "transport_ids", "gstreamer"}``. ``gstreamer``
        is ``None`` when no TX transports are enabled. Per-transport monitor taps
        and srt element names are returned inside the bundle so the runtime can
        still target them individually.
        """
        tx_transports = [
            transport for transport in config.srt_transports
            if transport.direction == SrtTransportDirection.tx
        ]
        if not tx_transports:
            return {"valid": True, "errors": [], "transport_ids": [], "gstreamer": None}

        errors: list[dict[str, Any]] = []
        valid_transports: list[SrtTransportConfig] = []
        for transport in tx_transports:
            transport_errors = self._validate_transport(config, transport)
            if transport_errors:
                errors.extend(transport_errors)
            else:
                valid_transports.append(transport)

        if not valid_transports:
            return {"valid": False, "errors": errors, "transport_ids": [], "gstreamer": None}

        group_by_id = {group.id: group for group in config.encode_groups if group.enabled}
        transport_groups: list[tuple[SrtTransportConfig, list[EncodeGroupConfig]]] = []
        for transport in valid_transports:
            selected = [group_by_id[gid] for gid in transport.encode_group_ids if gid in group_by_id]
            transport_groups.append((transport, selected))

        argv = self._build_endpoint_tx_argv(config, transport_groups)
        graph = self._argv_to_graph(argv)

        srt_endpoints: list[dict[str, str]] = []
        monitor_taps: list[dict[str, Any]] = []
        sources: list[dict[str, Any]] = []
        meter_endpoints: list[dict[str, Any]] = []
        for transport, selected_groups in transport_groups:
            srt_endpoints.append({
                "transport_id": transport.id,
                "element_name": self._srt_element_name(transport.id, transport.direction.value),
            })
            monitor_taps.extend(self._tx_monitor_taps(transport, selected_groups))
            sources.extend(self._planned_sources(config, selected_groups))
            for group in selected_groups:
                for channel in sorted(group.channels, key=lambda item: item.index):
                    meter_endpoints.append({
                        "transport_id": transport.id,
                        "channel": channel.index,
                        "direction": "out",
                        "element_name": self._bundle_meter_name(transport.id, group.id, channel.index),
                    })

        return {
            "valid": True,
            "errors": errors,
            "transport_ids": [transport.id for transport in valid_transports],
            "gstreamer": {
                "argv": argv,
                "graph": graph,
                "srt_endpoints": srt_endpoints,
                "monitor_taps": monitor_taps,
                "sources": sources,
                "meter_endpoints": meter_endpoints,
            },
        }

    def _build_endpoint_tx_argv(
        self,
        config: EndpointConfig,
        transport_groups: list[tuple[SrtTransportConfig, list[EncodeGroupConfig]]],
    ) -> list[str]:
        source_by_id = {source.id: source for source in config.sources if source.enabled}
        argv: list[str] = [self.gst_launch_executable, "-m"]

        # Union of every dante channel any TX leg references, so the single shared
        # deinterleave exposes every src pad some downstream branch will need.
        all_dante_channels: set[int] = set()
        for _transport, groups in transport_groups:
            for group in groups:
                for channel in group.channels:
                    source = source_by_id.get(channel.source_id or "")
                    if source is not None and source.kind == SourceKind.dante_input:
                        all_dante_channels.add(source.dante_channel or 1)

        dante_pad_by_channel: dict[int, int] = {}
        if all_dante_channels:
            capture_args, capture_channel_count, dante_pad_by_channel = self._dante_capture_plan(
                config.audio,
                sorted(all_dante_channels),
            )
            argv.extend([
                *capture_args,
                "!",
                "audioconvert",
                "!",
                "audioresample",
                "!",
                f"audio/x-raw,rate=48000,channels={capture_channel_count},format=S16LE",
                "!",
                "deinterleave",
                f"name={self._BUNDLE_DEINTERLEAVE_NAME}",
            ])

        # Per-TX sink chains. Each transport gets its own interleave/opusenc/srtsink
        # plus its own per-channel branches. All branches that reference a dante
        # source pull from the SAME shared deinterleave above.
        for transport, selected_groups in transport_groups:
            if not selected_groups:
                continue
            port = transport.port or config.network.srt_port
            latency_ms = transport.latency_ms or config.program.srt_latency_ms
            uri = self._build_srt_uri(transport.host, port, transport.mode.value, latency_ms)
            srt_name = self._srt_element_name(transport.id, transport.direction.value)

            # Only one encode group per TX is wired today (mirrors _build_tx_argv).
            group = selected_groups[0]
            n = group.channel_count
            interleave_name = self._bundle_interleave_name(transport.id, group.id)

            argv.extend([
                "interleave",
                f"name={interleave_name}",
                "!",
                f"audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels={n},channel-mask=(bitmask){self._channel_mask_hex(n)}",
                "!",
                "opusenc",
                f"bitrate={group.opus.bitrate_kbps * 1000}",
                "!",
                "rtpopuspay",
                "pt=96",
                "!",
                "srtsink",
                f"name={srt_name}",
                f"uri={uri}",
                "wait-for-connection=true",
            ])

            for channel in sorted(group.channels, key=lambda item: item.index):
                source = source_by_id.get(channel.source_id or "")
                if source is None:
                    continue
                tap_name = self._tx_monitor_tap_name(transport.id, group.id, channel.index)
                meter_name = self._bundle_meter_name(transport.id, group.id, channel.index)
                sink_pad = f"{interleave_name}.sink_{channel.index - 1}"

                if source.kind == SourceKind.dante_input:
                    argv.extend([
                        f"{self._BUNDLE_DEINTERLEAVE_NAME}.src_{dante_pad_by_channel[source.dante_channel or 1]}",
                        "!",
                        "queue",
                        "!",
                        "audioconvert",
                        "!",
                        f"audio/x-raw,format=S16LE,rate=48000,channels=1,channel-mask=(bitmask){self._channel_mask_bit_hex(n, channel.index)}",
                        "!",
                        "level",
                        f"name={meter_name}",
                        "message=true",
                        "interval=100000000",
                        "!",
                        "tee",
                        f"name={tap_name}",
                        "allow-not-linked=true",
                        f"{tap_name}.",
                        "!",
                        "queue",
                        "!",
                        sink_pad,
                    ])
                else:
                    argv.extend([
                        self._source_element(source),
                        "is-live=true",
                        *self._source_properties(source),
                        "!",
                        "audioconvert",
                        "!",
                        "audioresample",
                        "!",
                        f"audio/x-raw,format=S16LE,rate=48000,channels=1,channel-mask=(bitmask){self._channel_mask_bit_hex(n, channel.index)}",
                        "!",
                        "level",
                        f"name={meter_name}",
                        "message=true",
                        "interval=100000000",
                        "!",
                        "tee",
                        f"name={tap_name}",
                        "allow-not-linked=true",
                        f"{tap_name}.",
                        "!",
                        "queue",
                        "!",
                        sink_pad,
                    ])
        return argv

    def _bundle_interleave_name(self, transport_id: str, group_id: str) -> str:
        safe_transport = "".join(char if char.isalnum() else "_" for char in transport_id)
        safe_group = "".join(char if char.isalnum() else "_" for char in group_id)
        return f"il_{safe_transport}_{safe_group}"

    def _bundle_meter_name(self, transport_id: str, group_id: str, channel_index: int) -> str:
        # Existing telemetry regex matches dbmeter_(in|out)_<arbitrary>_<channel>,
        # so embedding the transport id between the prefix and trailing channel
        # index is safe.
        safe_transport = "".join(char if char.isalnum() else "_" for char in transport_id)
        safe_group = "".join(char if char.isalnum() else "_" for char in group_id)
        return f"dbmeter_out_{safe_transport}_{safe_group}_{channel_index}"

    # --- TX leg branch planner (commit 3 of dynamic-pipeline refactor) ---
    #
    # In the spine model, each TX SRT transport becomes a *bin* that is attached
    # to the running spine via the existing attach_branch_multi runtime API.
    # The bin contains all per-channel processing + the single shared encode +
    # the single srtsink for that transport. It pulls audio from one or more
    # spine_in_tee_K tees (one per source channel) through ghost sink pads that
    # are named sink_0, sink_1, ... in source-channel order. The bin contains no
    # asiosrc/deinterleave — those live in the spine and stay running across
    # add/remove operations.
    #
    # Per-channel branch shape inside the bin (N-channel encode group):
    #
    #   queue name=in_K -> audioconvert -> caps(mono+pos) -> level (dbmeter_out)
    #     -> interleave_<group>.sink_{K-1}
    #
    # Shared tail after interleave:
    #
    #   interleave name=il_<transport>_<group>
    #     -> caps(N-channel + standard mask)
    #     -> opusenc bitrate=<group> -> rtpopuspay pt=96
    #     -> srtsink name=srtstats_tx_<transport> uri=<...> wait-for-connection=true
    #
    # entry_element_names is parallel to tap_names: each entry name (``in_K``)
    # is the queue at the head of the per-channel chain, so attach_branch_multi
    # can ghost its static sink pad as sink_{K-1}.

    def plan_tx_leg_branch(
        self,
        config: EndpointConfig,
        transport_id: str,
        *,
        raise_on_error: bool = False,
    ) -> dict[str, Any]:
        transport = self._find_transport(config, transport_id)
        if transport.direction != SrtTransportDirection.tx:
            raise ValueError(
                f"plan_tx_leg_branch only supports TX transports; '{transport_id}' is {transport.direction.value}"
            )
        errors = self._validate_transport(config, transport)
        if errors:
            if raise_on_error:
                raise MediaGraphValidationError(errors)
            return {
                "valid": False, "errors": errors,
                "transport_id": transport_id,
                "branch_description": None,
                "tap_names": [], "entry_element_names": [],
                "srt_element_name": None, "meter_endpoints": [],
            }

        group_by_id = {g.id: g for g in config.encode_groups if g.enabled}
        selected_groups = [group_by_id[gid] for gid in transport.encode_group_ids]
        # Only one encode group per TX is wired today (mirrors _build_tx_argv).
        group = selected_groups[0]
        n = group.channel_count
        sorted_channels = sorted(group.channels, key=lambda item: item.index)

        port = transport.port or config.network.srt_port
        latency_ms = transport.latency_ms or config.program.srt_latency_ms
        uri = self._build_srt_uri(transport.host, port, transport.mode.value, latency_ms)
        srt_name = self._srt_element_name(transport.id, transport.direction.value)
        interleave_name = self._tx_leg_interleave_name(transport.id, group.id)

        # Per-channel feeder branches. Each ``queue name=in_K`` becomes the
        # ghost sink pad sink_{K-1} on the bin. The level element name follows
        # the existing dbmeter_out_<transport>_<group>_<channel> convention so
        # the telemetry parser can still attribute per-channel meters to the
        # right transport.
        source_by_id = {source.id: source for source in config.sources if source.enabled}
        tap_names: list[str] = []
        entry_element_names: list[str] = []
        meter_endpoints: list[dict[str, Any]] = []

        parts: list[str] = []
        for channel in sorted_channels:
            source = source_by_id[channel.source_id or ""]
            entry_name = f"in_{channel.index}"
            meter_name = self._bundle_meter_name(transport.id, group.id, channel.index)
            channel_pos_mask = self._channel_mask_bit_hex(n, channel.index)

            if source.kind == SourceKind.dante_input:
                tap_names.append(self.spine_capture_tee_name(source.dante_channel or 1))
            else:
                # tone/silence sources cannot pull from a spine tee — they would
                # need their own audiotestsrc inside the bin. v1 spine TX legs
                # require dante_input sources only; non-dante sources stay on
                # the old per-leg subprocess path until commit 5 (or are
                # rejected by the validator).
                if raise_on_error:
                    raise MediaGraphValidationError([
                        self._error(
                            transport.id, group.id,
                            "non_dante_source_in_spine_tx",
                            f"spine TX legs require dante_input sources; source '{source.id}' is {source.kind.value}",
                            channel.index, source_id=source.id,
                        ),
                    ])
                return {
                    "valid": False,
                    "errors": [self._error(
                        transport.id, group.id,
                        "non_dante_source_in_spine_tx",
                        f"spine TX legs require dante_input sources; source '{source.id}' is {source.kind.value}",
                        channel.index, source_id=source.id,
                    )],
                    "transport_id": transport_id,
                    "branch_description": None,
                    "tap_names": [], "entry_element_names": [],
                    "srt_element_name": None, "meter_endpoints": [],
                }

            entry_element_names.append(entry_name)
            meter_endpoints.append({
                "transport_id": transport.id,
                "channel": channel.index,
                "direction": "out",
                "element_name": meter_name,
            })

            parts.append(
                f"queue name={entry_name} "
                f"! audioconvert "
                f"! audio/x-raw,format=S16LE,rate=48000,channels=1,channel-mask=(bitmask){channel_pos_mask} "
                f"! level name={meter_name} message=true interval=100000000 "
                f"! {interleave_name}.sink_{channel.index - 1}"
            )

        # srtsink's ``wait-for-connection`` defaults to TRUE in gst-plugins-bad —
        # if we leave it at the default, srtsink blocks in PAUSED until the peer
        # connects, which backpressures up through the encoder and interleave
        # into the spine capture tee, stalling capture on every channel (not just
        # this leg's). =false lets the branch reach PLAYING immediately; srtsink
        # drops buffers until the peer is up.
        parts.append(
            f"interleave name={interleave_name} "
            f"! audio/x-raw,format=S16LE,layout=interleaved,rate=48000,channels={n},channel-mask=(bitmask){self._channel_mask_hex(n)} "
            f"! opusenc bitrate={group.opus.bitrate_kbps * 1000} "
            f"! rtpopuspay pt=96 "
            f"! srtsink name={srt_name} uri={uri} wait-for-connection=false"
        )
        description = " ".join(parts)

        return {
            "valid": True,
            "errors": [],
            "transport_id": transport_id,
            "branch_description": description,
            "tap_names": tap_names,
            "entry_element_names": entry_element_names,
            "srt_element_name": srt_name,
            "meter_endpoints": meter_endpoints,
        }

    def _tx_leg_interleave_name(self, transport_id: str, group_id: str) -> str:
        # Reuse the same naming as the legacy bundle so telemetry/meter parsing
        # stays consistent.
        return self._bundle_interleave_name(transport_id, group_id)

    def plan_rx_leg_branch(
        self,
        config: EndpointConfig,
        transport_id: str,
        *,
        raise_on_error: bool = False,
    ) -> dict[str, Any]:
        transport = self._find_transport(config, transport_id)
        if transport.direction != SrtTransportDirection.rx:
            raise ValueError(
                f"plan_rx_leg_branch only supports RX transports; '{transport_id}' is {transport.direction.value}"
            )
        errors = self._validate_transport(config, transport)
        if errors:
            if raise_on_error:
                raise MediaGraphValidationError(errors)
            return {
                "valid": False, "errors": errors,
                "transport_id": transport_id,
                "branch_description": None,
                "mixer_names": [], "exit_element_names": [],
                "srt_element_name": None, "meter_endpoints": [],
            }

        group_by_id = {g.id: g for g in config.encode_groups if g.enabled}
        selected_groups = [group_by_id[gid] for gid in transport.encode_group_ids]
        group = selected_groups[0]
        n = group.channel_count
        sorted_channels = sorted(group.channels, key=lambda item: item.index)
        port = transport.port or config.network.srt_port
        latency_ms = transport.latency_ms or config.program.srt_latency_ms
        uri = self._build_srt_uri(transport.host, port, transport.mode.value, latency_ms)
        srt_name = self._srt_element_name(transport.id, transport.direction.value)
        split_name = f"rx_split_{''.join(char if char.isalnum() else '_' for char in transport.id)}"
        tap_name = self._rx_monitor_tap_name(transport.id)

        parts: list[str] = [
            f"srtsrc name={srt_name} uri={uri}",
            "! application/x-rtp,media=audio,encoding-name=OPUS,clock-rate=48000,encoding-params=(string)1,payload=96",
            "! rtpjitterbuffer latency=40",
            "! rtpopusdepay",
            f"! tee name={tap_name} allow-not-linked=true",
            f"{tap_name}. ! queue ! opusdec ! audioconvert ! audioresample",
            f"! audio/x-raw,format=S16LE,rate=48000,channels={n},layout=interleaved,channel-mask=(bitmask)0x0",
            f"! deinterleave name={split_name}",
        ]
        mixer_names: list[str] = []
        exit_element_names: list[str] = []
        interaudio_channels: list[str] = []
        meter_endpoints: list[dict[str, Any]] = []
        for channel in sorted_channels:
            output_channel_idx = channel.index
            if channel.source_id and channel.source_id != "system-silence-00":
                src_conf = next((s for s in config.sources if s.id == channel.source_id), None)
                if src_conf and src_conf.dante_channel is not None:
                    output_channel_idx = src_conf.dante_channel

            if output_channel_idx > config.audio.channel_count:
                error = self._error(
                    transport.id, group.id, "rx_output_channel_out_of_range",
                    f"RX output destination {output_channel_idx} exceeds interface channel_count {config.audio.channel_count}",
                    output_channel_idx,
                )
                if raise_on_error:
                    raise MediaGraphValidationError([error])
                return {
                    "valid": False, "errors": [error],
                    "transport_id": transport_id,
                    "branch_description": None,
                    "mixer_names": [], "exit_element_names": [],
                    "srt_element_name": None, "meter_endpoints": [],
                }
            mixer_names.append(self.spine_playback_mixer_name(output_channel_idx))
            interaudio_channel = self.spine_playback_interaudio_channel(output_channel_idx)
            interaudio_channels.append(interaudio_channel)
            exit_name = f"out_{output_channel_idx}"
            exit_element_names.append(exit_name)
            meter_name = self._rx_meter_element_name(transport.id, channel.index)
            meter_endpoints.append({
                "transport_id": transport.id,
                "channel": channel.index,
                "direction": "in",
                "element_name": meter_name,
            })
            parts.append(
                f"{split_name}.src_{channel.index - 1} "
                f"! queue "
                f"! level name={meter_name} message=true interval=100000000 "
                f"! audioconvert "
                f"! audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved,channel-mask=(bitmask)0x0 "
                f"! queue name={exit_name}"
            )

        return {
            "valid": True,
            "errors": [],
            "transport_id": transport_id,
            "branch_description": " ".join(parts),
            "mixer_names": mixer_names,
            "exit_element_names": exit_element_names,
            "srt_element_name": srt_name,
            "meter_endpoints": meter_endpoints,
            "monitor_taps": [self._rx_monitor_tap(transport.id)],
            "interaudio_channels": interaudio_channels,
        }

    # --- spine planner (commit 1 of dynamic-pipeline refactor) ---
    #
    # The spine is a single always-on full-duplex pipeline that owns DVS. It is
    # built once and stays in PLAYING; TX and RX legs are attached/detached at
    # runtime onto its per-channel tee (capture side) and per-channel playback mixer
    # (playback side) elements. By keeping DVS open continuously through one
    # process we avoid both the single-client ASIO conflict and the rebuild glitch
    # that the old "bundle rebuild on every start/stop" design produced.
    #
    # Static graph shape (N == audio.channel_count):
    #
    #   asiosrc -> audioconvert -> audioresample
    #     -> audio/x-raw,format=S16LE,rate=48000,channels=N
    #     -> deinterleave name=spine_capture_split
    #
    #   spine_capture_split.src_K -> queue -> tee name=spine_in_tee_{K+1}
    #     (K=0..N-1; allow-not-linked so the tee is happy with no listeners)
    #
    #   audiotestsrc wave=silence is-live=true
    #     -> audioconvert -> caps(mono S16LE)
    #     -> adder name=spine_out_mix_{K}
    #     -> queue -> caps(mono S16LE)
    #     -> spine_out_interleave.sink_{K-1}
    #     (one mixer per K=1..N; silence keeps the mixer producing buffers when
    #     no RX leg is attached, so the spine never starves asiosink)
    #
    #   interleave name=spine_out_interleave
    #     -> audioconvert
    #     -> audio/x-raw,format=S16LE,rate=48000,channels=N,layout=interleaved,channel-mask=(bitmask)0x0
    #     -> asiosink name=spine_asiosink
    #
    # channel-mask=0x0 (no positions) is used on the N-channel buses because
    # standard surround masks are not defined past 8 channels and asiosink maps
    # interleaved-order directly to ASIO output channels. The same caps shape is
    # used on the playback mixer leg and the interleave output to avoid position-
    # vs-position negotiation surprises.

    SPINE_ASIOSRC_NAME = "spine_asiosrc"
    SPINE_ASIOSINK_NAME = "spine_asiosink"
    SPINE_CAPTURE_SPLIT_NAME = "spine_capture_split"
    SPINE_PLAYBACK_INTERLEAVE_NAME = "spine_out_interleave"

    def spine_capture_tee_name(self, channel_index: int) -> str:
        """Capture-side tee that TX legs attach to. ``channel_index`` is 1-based."""
        return f"spine_in_tee_{channel_index}"

    def spine_capture_meter_name(self, channel_index: int) -> str:
        """Per-channel input level meter on the spine capture chain.

        Name follows the existing ``dbmeter_in_*`` convention with a trailing
        channel index so the telemetry parser picks it up as a Dante input meter
        without needing any new attribution logic.
        """
        return f"dbmeter_in_spine_{channel_index}"

    def spine_playback_mixer_name(self, channel_index: int) -> str:
        """Playback-side mixer that RX legs attach to. ``channel_index`` is 1-based."""
        return f"spine_out_mix_{channel_index}"

    def spine_playback_interaudio_channel(self, channel_index: int) -> str:
        return f"spine_out_{channel_index}"

    def plan_spine(self, config: EndpointConfig) -> dict[str, Any]:
        audio = config.audio
        errors: list[dict[str, Any]] = []
        if audio.interface_driver == "unknown" or not audio.interface_name:
            errors.append({
                "transport_id": None, "group_id": None, "channel_index": None, "source_id": None,
                "code": "audio_interface_not_selected",
                "message": "audio interface must be selected before starting the spine",
            })
        if audio.interface_driver not in {"asio", "wasapi", "coreaudio", "alsa", "unknown"}:
            errors.append({
                "transport_id": None, "group_id": None, "channel_index": None, "source_id": None,
                "code": "unsupported_audio_driver",
                "message": f"audio driver '{audio.interface_driver}' is not supported by the spine",
            })
        if audio.channel_count < 1:
            errors.append({
                "transport_id": None, "group_id": None, "channel_index": None, "source_id": None,
                "code": "invalid_channel_count",
                "message": f"audio.channel_count must be >= 1 (got {audio.channel_count})",
            })

        if errors:
            return {
                "valid": False, "errors": errors, "channel_count": audio.channel_count,
                "capture_tee_names": [], "playback_mixer_names": [],
                "gstreamer": None,
            }

        n = audio.channel_count
        # ASIO uses an explicit channel list so the deinterleave/interleave pad
        # ordering matches Dante input/output channels 1..N.
        if audio.interface_driver == "asio":
            asio_channels = list(range(n))
            capture_args = self._dante_capture_args(audio, asio_channels)
        else:
            capture_args = self._dante_capture_args(audio, None)
        sink_args = self._dante_playback_args(audio, list(range(n)) if audio.interface_driver == "asio" else None)

        argv: list[str] = [self.gst_launch_executable, "-m"]

        # Capture chain: one shared asiosrc/wasapisrc/etc., deinterleave to N tees.
        argv.extend([
            *capture_args,
            f"name={self.SPINE_ASIOSRC_NAME}",
            "!",
            "audioconvert",
            "!",
            "audioresample",
            "!",
            f"audio/x-raw,format=S16LE,rate=48000,channels={n},layout=interleaved,channel-mask=(bitmask)0x0",
            "!",
            "deinterleave",
            f"name={self.SPINE_CAPTURE_SPLIT_NAME}",
        ])

        for channel in range(1, n + 1):
            tee_name = self.spine_capture_tee_name(channel)
            meter_name = self.spine_capture_meter_name(channel)
            # A ``level`` element is wired between the queue and the tee on each
            # capture channel. It produces per-channel input metering for the UI
            # (real product feature) AND lets us confirm that buffers are
            # actually flowing through the spine capture chain even when no TX
            # leg is attached yet. Without this, an inactive capture path is
            # indistinguishable from a working one because the tee just drops
            # buffers when nothing is attached.
            argv.extend([
                f"{self.SPINE_CAPTURE_SPLIT_NAME}.src_{channel - 1}",
                "!",
                "queue",
                "!",
                "level",
                f"name={meter_name}",
                "message=true",
                "interval=100000000",
                "!",
                "tee",
                f"name={tee_name}",
                "allow-not-linked=true",
                f"{tee_name}.",
                "!",
                "queue",
                "!",
                "fakesink",
                "sync=false",
                "async=false",
            ])

        # Playback chain: per-channel adder fed by a default silence source,
        # then interleave -> asiosink. latency=0 and min-upstream-latency=0 keep
        # the old audiomixer version from inserting its default 10ms cushion;
        # adder is intentionally simpler here because it supports live dynamic
        # request-pad linking without returning EMPTY caps on newly requested pads.
        argv.extend([
            "interleave",
            f"name={self.SPINE_PLAYBACK_INTERLEAVE_NAME}",
            "!",
            "audioconvert",
            "!",
            "audioresample",
            "!",
            f"audio/x-raw,rate=48000,channels={n},layout=interleaved,channel-mask=(bitmask)0x0",
            "!",
            *sink_args,
            f"name={self.SPINE_ASIOSINK_NAME}",
        ])

        for channel in range(1, n + 1):
            mixer_name = self.spine_playback_mixer_name(channel)
            # The silence feeder is the always-on default input; RX legs add
            # additional sink pads on the same mixer at runtime.
            argv.extend([
                "audiomixer",
                f"name={mixer_name}",
                "latency=20000000",
                "!",
                "audioconvert",
                "!",
                "audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved,channel-mask=(bitmask)0x0",
                "!",
                "queue",
                "!",
                f"{self.SPINE_PLAYBACK_INTERLEAVE_NAME}.sink_{channel - 1}",
                "audiotestsrc",
                "is-live=true",
                "wave=silence",
                "!",
                "audioconvert",
                "!",
                "audio/x-raw,format=S16LE,rate=48000,channels=1,layout=interleaved,channel-mask=(bitmask)0x0",
                "!",
                f"{mixer_name}.",
            ])

        graph = self._argv_to_graph(argv)
        return {
            "valid": True,
            "errors": [],
            "channel_count": n,
            "capture_tee_names": [self.spine_capture_tee_name(c) for c in range(1, n + 1)],
            "playback_mixer_names": [self.spine_playback_mixer_name(c) for c in range(1, n + 1)],
            "gstreamer": {
                "argv": argv,
                "graph": graph,
                "asiosrc_element_name": self.SPINE_ASIOSRC_NAME,
                "asiosink_element_name": self.SPINE_ASIOSINK_NAME,
            },
        }

    def plan_tx_capture_spine(self, config: EndpointConfig) -> dict[str, Any]:
        """Plan the long-lived capture-only spine used by dynamic TX legs.

        This owns the Dante capture device once and exposes one tee per Dante
        input channel. TX SRT transports attach/detach encoder+srtsink bins to
        those tees without rebuilding capture or disturbing other TX legs.
        """
        audio = config.audio
        errors: list[dict[str, Any]] = []
        if audio.interface_driver == "unknown" or not audio.interface_name:
            errors.append({
                "transport_id": None, "group_id": None, "channel_index": None, "source_id": None,
                "code": "audio_interface_not_selected",
                "message": "audio interface must be selected before starting the TX capture spine",
            })
        if audio.interface_driver not in {"asio", "wasapi", "coreaudio", "alsa", "unknown"}:
            errors.append({
                "transport_id": None, "group_id": None, "channel_index": None, "source_id": None,
                "code": "unsupported_audio_driver",
                "message": f"audio driver '{audio.interface_driver}' is not supported by the TX capture spine",
            })
        if audio.channel_count < 1:
            errors.append({
                "transport_id": None, "group_id": None, "channel_index": None, "source_id": None,
                "code": "invalid_channel_count",
                "message": f"audio.channel_count must be >= 1 (got {audio.channel_count})",
            })
        if errors:
            return {
                "valid": False, "errors": errors, "channel_count": audio.channel_count,
                "capture_tee_names": [], "gstreamer": None,
            }

        n = audio.channel_count
        capture_args = self._dante_capture_args(audio, list(range(n)) if audio.interface_driver == "asio" else None)
        argv: list[str] = [self.gst_launch_executable, "-m"]
        argv.extend([
            *capture_args,
            f"name={self.SPINE_ASIOSRC_NAME}",
            "!",
            "audioconvert",
            "!",
            "audioresample",
            "!",
            f"audio/x-raw,format=S16LE,rate=48000,channels={n},layout=interleaved,channel-mask=(bitmask)0x0",
            "!",
            "deinterleave",
            f"name={self.SPINE_CAPTURE_SPLIT_NAME}",
        ])
        for channel in range(1, n + 1):
            argv.extend([
                f"{self.SPINE_CAPTURE_SPLIT_NAME}.src_{channel - 1}",
                "!",
                "queue",
                "!",
                "level",
                f"name={self.spine_capture_meter_name(channel)}",
                "message=true",
                "interval=100000000",
                "!",
                "tee",
                f"name={self.spine_capture_tee_name(channel)}",
                "allow-not-linked=true",
                f"{self.spine_capture_tee_name(channel)}.",
                "!",
                "queue",
                "!",
                "fakesink",
                "sync=false",
                "async=false",
            ])
        return {
            "valid": True,
            "errors": [],
            "channel_count": n,
            "capture_tee_names": [self.spine_capture_tee_name(c) for c in range(1, n + 1)],
            "gstreamer": {
                "argv": argv,
                "graph": self._argv_to_graph(argv),
                "asiosrc_element_name": self.SPINE_ASIOSRC_NAME,
            },
        }

    def _dante_playback_args(self, audio: AudioConfig, asio_channels: list[int] | None = None) -> list[str]:
        """Mirror of ``_dante_capture_args`` for the playback element."""
        driver = audio.interface_driver
        device = audio.interface_device_id
        if driver == "wasapi":
            args = ["wasapisink"]
            if device:
                args.append(f'device="{device}"')
            return args
        if driver == "coreaudio":
            args = ["osxaudiosink"]
            if device:
                args.append(f"device={device}")
            return args
        if driver == "alsa":
            args = ["alsasink"]
            if device:
                args.append(f'device="{device}"')
            return args
        if driver == "asio":
            args = ["asiosink"]
            if device:
                args.append(f'device-clsid="{{{device.strip("{}")}}}"')
            if asio_channels:
                args.append(f"output-channels={','.join(str(channel) for channel in asio_channels)}")
            return args
        raise ValueError(f"unsupported audio driver for dante playback: '{driver}'")

    def _dante_capture_plan(self, audio: AudioConfig, dante_channels: list[int]) -> tuple[list[str], int, dict[int, int]]:
        requested_channels = sorted({max(1, channel) for channel in dante_channels})
        if audio.interface_driver == "asio":
            zero_based = sorted({channel - 1 for channel in requested_channels})
            expanded: set[int] = set()
            for channel in zero_based:
                pair_base = channel - (channel % 2)
                expanded.update({pair_base, pair_base + 1})
            selected = sorted(expanded)
            args = self._dante_capture_args(audio, selected)
            pad_by_channel = {channel + 1: selected.index(channel) for channel in zero_based}
            return args, len(selected), pad_by_channel

        args = self._dante_capture_args(audio, None)
        return args, audio.channel_count, {channel: channel - 1 for channel in requested_channels}

    def _dante_capture_args(self, audio: AudioConfig, asio_channels: list[int] | None = None) -> list[str]:
        """Return the gst element + properties for the host audio capture device.
        Selected by interface_driver; device addressed by interface_device_id when set,
        otherwise the OS default capture device for the driver is used (DVS, in normal
        deployments, is configured as default)."""
        driver = audio.interface_driver
        device = audio.interface_device_id
        if driver == "wasapi":
            args = ["wasapisrc"]
            if device:
                args.append(f'device="{device}"')
            return args
        if driver == "coreaudio":
            args = ["osxaudiosrc"]
            if device:
                # CoreAudio device id is an integer; pass through as-is.
                args.append(f"device={device}")
            return args
        if driver == "alsa":
            args = ["alsasrc"]
            if device:
                args.append(f'device="{device}"')
            return args
        if driver == "asio":
            # asiosrc (gst-plugins-bad) addresses devices via device-clsid (GUID).
            # Without a clsid, asiosrc picks the system's registered ASIO driver
            # — fine for single-driver hosts (e.g. Dante Virtual Soundcard in
            # ASIO mode). DVS exposes itself in either ASIO or WASAPI mode but
            # not both simultaneously, so the driver follows whatever DVS is
            # currently configured for.
            args = ["asiosrc"]
            if device:
                args.append(f'device-clsid="{{{device.strip("{}")}}}"')
            if asio_channels:
                args.append(f"input-channels={','.join(str(channel) for channel in asio_channels)}")
            return args
        # unknown shouldn't reach here — the validator rejects it.
        raise ValueError(f"unsupported audio driver for dante capture: '{driver}'")

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

    def _interleave_element_name(self, group_id: str) -> str:
        safe_group = "".join(char if char.isalnum() else "_" for char in group_id)
        return f"il_{safe_group}"

    def _deinterleave_element_name(self, group_id: str) -> str:
        safe_group = "".join(char if char.isalnum() else "_" for char in group_id)
        return f"dante_in_{safe_group}"

    @staticmethod
    def _channel_mask_hex(channels: int) -> str:
        # Standard speaker masks for 1..8 channels matching gstreamer/Vorbis-order
        # surround layouts. opusenc uses these to set channel_mapping_family=1.
        masks = {
            1: 0x4,    # mono (front center)
            2: 0x3,    # stereo (front L, R)
            3: 0x7,    # 3.0 (front L, R, C)
            4: 0x33,   # quad
            5: 0x37,   # 5.0
            6: 0x3F,   # 5.1
            7: 0x70F,  # 6.1
            8: 0x63F,  # 7.1
        }
        return f"0x{masks.get(channels, 0):x}"

    @staticmethod
    def _channel_mask_bit_hex(channels: int, channel_index: int) -> str:
        # Per-mono-leg channel positions must add up to the interleaved output
        # mask, otherwise opusenc produces unknown-position caps that rtpopuspay
        # cannot negotiate.
        bits = {
            1: [0x4],
            2: [0x1, 0x2],
            3: [0x1, 0x2, 0x4],
            4: [0x1, 0x2, 0x10, 0x20],
            5: [0x1, 0x2, 0x4, 0x10, 0x20],
            6: [0x1, 0x2, 0x4, 0x8, 0x10, 0x20],
            7: [0x1, 0x2, 0x4, 0x8, 0x100, 0x200, 0x400],
            8: [0x1, 0x2, 0x4, 0x8, 0x10, 0x20, 0x200, 0x400],
        }
        selected = bits.get(channels, [])
        if channel_index < 1 or channel_index > len(selected):
            return "0x0"
        return f"0x{selected[channel_index - 1]:x}"

    def _rx_meter_element_name(self, transport_id: str, channel_index: int = 1) -> str:
        safe_transport = "".join(char if char.isalnum() else "_" for char in transport_id)
        return f"dbmeter_in_{safe_transport}_{channel_index}"

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
