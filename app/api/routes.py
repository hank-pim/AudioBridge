from __future__ import annotations

import json
import secrets
import subprocess
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.core.config import EndpointConfig, RouteMap
from app.core.config_store import ConfigStore
from app.services.audio_devices import normalize_config_driver
from app.services.events import EventLog
from app.services.media import MediaController
from app.services.telemetry import TelemetryService
from app.services.webrtc_monitor import WebRtcMonitorService

# ---------------------------------------------------------------------------
# Stub network interface catalogue.
# ---------------------------------------------------------------------------
_STUB_NETWORK_INTERFACES = [
    {"name": "Ethernet", "description": "Intel I219-V", "ip_address": None},
    {"name": "Ethernet 2", "description": "Realtek PCIe GbE", "ip_address": None},
    {"name": "Wi-Fi", "description": "Intel AX201", "ip_address": None},
]


def _network_interfaces() -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-NetIPConfiguration | "
                    "Select-Object InterfaceAlias,InterfaceDescription,"
                    "@{n='IPv4Address';e={$_.IPv4Address.IPAddress -join ','}} | "
                    "ConvertTo-Json -Depth 3"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
        raw = json.loads(completed.stdout or "[]")
        rows = raw if isinstance(raw, list) else [raw]
        interfaces = []
        for row in rows:
            name = row.get("InterfaceAlias")
            if not name:
                continue
            ip_addresses = [ip.strip() for ip in str(row.get("IPv4Address") or "").split(",") if ip.strip()]
            interfaces.append({
                "name": name,
                "description": row.get("InterfaceDescription"),
                "ip_address": ip_addresses[0] if ip_addresses else None,
                "ip_addresses": ip_addresses,
            })
        return interfaces or _STUB_NETWORK_INTERFACES
    except Exception:
        return _network_interfaces_from_ipconfig() or _STUB_NETWORK_INTERFACES


def _network_interfaces_from_ipconfig() -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            ["ipconfig", "/all"],
            capture_output=True,
            text=True,
            timeout=2,
            check=True,
        )
    except Exception:
        return []

    interfaces: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for raw_line in completed.stdout.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.endswith(":") and " adapter " in stripped:
            if current is not None:
                _finalize_ipconfig_interface(current, interfaces)
            name = stripped.split(" adapter ", 1)[1].rstrip(":")
            current = {"name": name, "description": None, "ip_addresses": []}
            continue
        if current is None or "." not in stripped:
            continue
        key, _, value = stripped.partition(":")
        key = key.replace(".", "").strip()
        value = value.strip()
        if key == "Description":
            current["description"] = value or None
        elif key == "IPv4 Address":
            ip = value.split("(", 1)[0].strip()
            if ip:
                current["ip_addresses"].append(ip)
    if current is not None:
        _finalize_ipconfig_interface(current, interfaces)
    return interfaces


def _finalize_ipconfig_interface(current: dict[str, Any], interfaces: list[dict[str, Any]]) -> None:
    ip_addresses = current.get("ip_addresses") or []
    interfaces.append({
        "name": current.get("name"),
        "description": current.get("description"),
        "ip_address": ip_addresses[0] if ip_addresses else None,
        "ip_addresses": ip_addresses,
    })


def create_api_router(
    store: ConfigStore,
    media: MediaController,
    telemetry: TelemetryService,
    events: EventLog,
    monitor: WebRtcMonitorService,
) -> APIRouter:
    router = APIRouter(prefix="/api")

    # pending pairing bundles: token -> (bundle_dict, expires_at)
    _pending_bundles: dict[str, tuple[dict[str, Any], float]] = {}

    def _purge_expired_bundles() -> None:
        now = time.time()
        expired = [t for t, (_, exp) in _pending_bundles.items() if exp < now]
        for t in expired:
            del _pending_bundles[t]

    def get_config_store() -> ConfigStore:
        return store

    def _raise_media_error(exc: Exception) -> None:
        detail = str(exc)
        status = 503 if "not found on PATH" in detail else 409
        raise HTTPException(status_code=status, detail=detail)

    def _config_payload() -> dict[str, Any]:
        return store.config.model_dump(mode="python", exclude_none=True)

    def _replace_collection_item(collection_name: str, item_id: str, body: dict[str, Any], create: bool) -> EndpointConfig:
        payload = _config_payload()
        collection = list(payload.get(collection_name, []))
        body = {**body, "id": item_id}

        for index, item in enumerate(collection):
            if item.get("id") == item_id:
                collection[index] = body
                payload[collection_name] = collection
                return store.save(EndpointConfig.model_validate(payload))

        if not create:
            raise HTTPException(status_code=404, detail=f"{collection_name[:-1]} '{item_id}' not found")

        collection.append(body)
        payload[collection_name] = collection
        return store.save(EndpointConfig.model_validate(payload))

    def _delete_collection_item(collection_name: str, item_id: str) -> EndpointConfig:
        payload = _config_payload()
        collection = list(payload.get(collection_name, []))
        filtered = [item for item in collection if item.get("id") != item_id]
        if len(filtered) == len(collection):
            raise HTTPException(status_code=404, detail=f"{collection_name[:-1]} '{item_id}' not found")
        payload[collection_name] = filtered
        return store.save(EndpointConfig.model_validate(payload))

    # -----------------------------------------------------------------------
    # Health
    # -----------------------------------------------------------------------

    @router.get("/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": "0.1.0",
            "endpoint_name": store.config.endpoint_name,
            "schema_version": store.config.schema_version,
        }

    # -----------------------------------------------------------------------
    # Config
    # -----------------------------------------------------------------------

    @router.get("/config")
    def get_config(config_store: ConfigStore = Depends(get_config_store)) -> dict[str, Any]:
        return config_store.export(include_secrets=False)

    @router.put("/config")
    def put_config(
        config: EndpointConfig,
        config_store: ConfigStore = Depends(get_config_store),
    ) -> dict[str, Any]:
        saved = config_store.save(config)
        events.append("info", "control", "configuration replaced")
        return saved.model_dump(mode="json", exclude_none=True)

    @router.patch("/config")
    def patch_config(
        patch: dict[str, Any],
        config_store: ConfigStore = Depends(get_config_store),
    ) -> dict[str, Any]:
        saved = config_store.update(patch)
        events.append("info", "control", "configuration updated")
        return saved.model_dump(mode="json", exclude_none=True)

    @router.get("/config/export")
    def export_config(include_secrets: bool = Query(default=False)) -> dict[str, Any]:
        return store.export(include_secrets=include_secrets)

    # -----------------------------------------------------------------------
    # Transport-oriented entities
    # -----------------------------------------------------------------------

    @router.get("/sources")
    def list_sources() -> list[dict[str, Any]]:
        return [source.model_dump(mode="json", exclude_none=True) for source in store.config.sources]

    @router.post("/sources")
    def create_source(body: dict[str, Any]) -> dict[str, Any]:
        source_id = body.get("id")
        if not source_id:
            raise HTTPException(status_code=422, detail="source id is required")
        saved = _replace_collection_item("sources", str(source_id), body, create=True)
        events.append("info", "control", "source created", source_id=source_id)
        return next(source.model_dump(mode="json", exclude_none=True) for source in saved.sources if source.id == source_id)

    @router.put("/sources/{source_id}")
    def update_source(source_id: str, body: dict[str, Any]) -> dict[str, Any]:
        saved = _replace_collection_item("sources", source_id, body, create=False)
        events.append("info", "control", "source updated", source_id=source_id)
        return next(source.model_dump(mode="json", exclude_none=True) for source in saved.sources if source.id == source_id)

    @router.delete("/sources/{source_id}")
    def delete_source(source_id: str) -> dict[str, str]:
        _delete_collection_item("sources", source_id)
        events.append("info", "control", "source deleted", source_id=source_id)
        return {"source": "deleted"}

    @router.get("/encode-groups")
    def list_encode_groups() -> list[dict[str, Any]]:
        return [group.model_dump(mode="json", exclude_none=True) for group in store.config.encode_groups]

    @router.post("/encode-groups")
    def create_encode_group(body: dict[str, Any]) -> dict[str, Any]:
        group_id = body.get("id")
        if not group_id:
            raise HTTPException(status_code=422, detail="encode group id is required")
        saved = _replace_collection_item("encode_groups", str(group_id), body, create=True)
        events.append("info", "control", "encode group created", encode_group_id=group_id)
        return next(group.model_dump(mode="json", exclude_none=True) for group in saved.encode_groups if group.id == group_id)

    @router.put("/encode-groups/{group_id}")
    def update_encode_group(group_id: str, body: dict[str, Any]) -> dict[str, Any]:
        saved = _replace_collection_item("encode_groups", group_id, body, create=False)
        events.append("info", "control", "encode group updated", encode_group_id=group_id)
        return next(group.model_dump(mode="json", exclude_none=True) for group in saved.encode_groups if group.id == group_id)

    @router.delete("/encode-groups/{group_id}")
    def delete_encode_group(group_id: str) -> dict[str, str]:
        _delete_collection_item("encode_groups", group_id)
        events.append("info", "control", "encode group deleted", encode_group_id=group_id)
        return {"encode_group": "deleted"}

    @router.get("/srt-transports")
    def list_srt_transports() -> list[dict[str, Any]]:
        return [transport.model_dump(mode="json", exclude_none=True) for transport in store.config.srt_transports]

    @router.post("/srt-transports")
    def create_srt_transport(body: dict[str, Any]) -> dict[str, Any]:
        transport_id = body.get("id")
        if not transport_id:
            raise HTTPException(status_code=422, detail="SRT transport id is required")
        saved = _replace_collection_item("srt_transports", str(transport_id), body, create=True)
        events.append("info", "control", "SRT transport created", transport_id=transport_id)
        return next(transport.model_dump(mode="json", exclude_none=True) for transport in saved.srt_transports if transport.id == transport_id)

    @router.put("/srt-transports/{transport_id}")
    def update_srt_transport(transport_id: str, body: dict[str, Any]) -> dict[str, Any]:
        saved = _replace_collection_item("srt_transports", transport_id, body, create=False)
        events.append("info", "control", "SRT transport updated", transport_id=transport_id)
        return next(transport.model_dump(mode="json", exclude_none=True) for transport in saved.srt_transports if transport.id == transport_id)

    @router.delete("/srt-transports/{transport_id}")
    def delete_srt_transport(transport_id: str) -> dict[str, str]:
        media.stop_srt_transport(transport_id)
        _delete_collection_item("srt_transports", transport_id)
        events.append("info", "control", "SRT transport deleted", transport_id=transport_id)
        return {"srt_transport": "deleted"}

    @router.post("/srt-transports/{transport_id}/start")
    def start_srt_transport(transport_id: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = body or {}
        try:
            pipeline = media.start_srt_transport(
                config=store.config,
                transport_id=transport_id,
                frequency_hz=float(payload.get("frequency_hz", 1000.0)),
                level_dbfs=float(payload.get("level_dbfs", -18.0)),
                waveform=str(payload.get("waveform", "sine")),
            )
        except (RuntimeError, ValueError) as exc:
            _raise_media_error(exc)
        events.append("info", "media", "SRT transport started", transport_id=transport_id)
        return {"transport": "running", "pipeline": pipeline}

    @router.post("/srt-transports/{transport_id}/stop")
    def stop_srt_transport(transport_id: str) -> dict[str, Any]:
        media.stop_srt_transport(transport_id)
        events.append("info", "media", "SRT transport stopped", transport_id=transport_id)
        return {"transport": "stopped", "transport_id": transport_id}

    @router.get("/srt-transports/{transport_id}/monitor-branches")
    def list_monitor_branches(transport_id: str) -> dict[str, Any]:
        return {"transport_id": transport_id, "branches": media.list_monitor_branches(transport_id)}

    @router.post("/srt-transports/{transport_id}/monitor-branches")
    def attach_monitor_branch(transport_id: str, body: dict[str, Any]) -> dict[str, Any]:
        tap_id = body.get("tap_id")
        if not tap_id:
            raise HTTPException(status_code=422, detail="tap_id is required")
        audible = bool(body.get("audible", True))
        try:
            attached = media.start_monitor_branch(
                config=store.config,
                transport_id=transport_id,
                tap_id=str(tap_id),
                audible=audible,
            )
        except (RuntimeError, ValueError) as exc:
            _raise_media_error(exc)
        events.append(
            "info",
            "media",
            "monitor branch attached",
            transport_id=transport_id,
            tap_id=tap_id,
            handle=attached["handle"],
            audible=audible,
        )
        return attached

    @router.delete("/srt-transports/{transport_id}/monitor-branches/{handle}")
    def detach_monitor_branch(transport_id: str, handle: str) -> dict[str, Any]:
        removed = media.stop_monitor_branch(transport_id, handle)
        if not removed:
            raise HTTPException(status_code=404, detail="monitor branch not found")
        events.append(
            "info",
            "media",
            "monitor branch detached",
            transport_id=transport_id,
            handle=handle,
        )
        return {"monitor_branch": "detached", "handle": handle}

    # -----------------------------------------------------------------------
    # WebRTC monitor sessions (operator-side audio listen)
    # -----------------------------------------------------------------------

    @router.get("/monitor-sessions")
    def list_monitor_sessions() -> dict[str, Any]:
        return {"sessions": monitor.list_sessions()}

    @router.post("/monitor-sessions")
    async def create_monitor_session(body: dict[str, Any]) -> dict[str, Any]:
        transport_id = body.get("transport_id")
        tap_id = body.get("tap_id")
        if not transport_id:
            raise HTTPException(status_code=422, detail="transport_id is required")
        try:
            payload = await monitor.create_session(
                store.config,
                str(transport_id),
                str(tap_id) if tap_id else None,
            )
        except (RuntimeError, ValueError) as exc:
            _raise_media_error(exc)
        events.append(
            "info",
            "media",
            "monitor session created",
            transport_id=transport_id,
            tap_id=tap_id,
            session_id=payload["session_id"],
        )
        return payload

    @router.post("/monitor-sessions/{session_id}/answer")
    async def answer_monitor_session(session_id: str, body: dict[str, Any]) -> dict[str, str]:
        sdp = body.get("sdp")
        type_ = body.get("type", "answer")
        if not sdp:
            raise HTTPException(status_code=422, detail="sdp is required")
        try:
            await monitor.set_answer(session_id, str(sdp), str(type_))
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        events.append("info", "media", "monitor session answered", session_id=session_id)
        return {"monitor_session": "negotiated", "session_id": session_id}

    @router.delete("/monitor-sessions/{session_id}")
    async def close_monitor_session(session_id: str) -> dict[str, str]:
        removed = await monitor.close_session(session_id)
        if not removed:
            raise HTTPException(status_code=404, detail="monitor session not found")
        events.append("info", "media", "monitor session closed", session_id=session_id)
        return {"monitor_session": "closed", "session_id": session_id}

    @router.get("/webrtc-streams")
    def list_webrtc_streams() -> list[dict[str, Any]]:
        return [stream.model_dump(mode="json", exclude_none=True) for stream in store.config.webrtc_streams]

    @router.post("/webrtc-streams")
    def create_webrtc_stream(body: dict[str, Any]) -> dict[str, Any]:
        stream_id = body.get("id")
        if not stream_id:
            raise HTTPException(status_code=422, detail="WebRTC stream id is required")
        saved = _replace_collection_item("webrtc_streams", str(stream_id), body, create=True)
        events.append("info", "control", "WebRTC stream created", stream_id=stream_id)
        return next(stream.model_dump(mode="json", exclude_none=True) for stream in saved.webrtc_streams if stream.id == stream_id)

    @router.put("/webrtc-streams/{stream_id}")
    def update_webrtc_stream(stream_id: str, body: dict[str, Any]) -> dict[str, Any]:
        saved = _replace_collection_item("webrtc_streams", stream_id, body, create=False)
        events.append("info", "control", "WebRTC stream updated", stream_id=stream_id)
        return next(stream.model_dump(mode="json", exclude_none=True) for stream in saved.webrtc_streams if stream.id == stream_id)

    @router.delete("/webrtc-streams/{stream_id}")
    def delete_webrtc_stream(stream_id: str) -> dict[str, str]:
        media.stop_webrtc_stream(stream_id)
        _delete_collection_item("webrtc_streams", stream_id)
        events.append("info", "control", "WebRTC stream deleted", stream_id=stream_id)
        return {"webrtc_stream": "deleted"}

    @router.post("/webrtc-streams/{stream_id}/start")
    def start_webrtc_stream(stream_id: str) -> dict[str, Any]:
        try:
            result = media.start_webrtc_stream(store.config, stream_id)
        except (RuntimeError, ValueError) as exc:
            _raise_media_error(exc)
        events.append("info", "media", "WebRTC stream started", stream_id=stream_id)
        return {"stream": "running", "webrtc": result}

    @router.post("/webrtc-streams/{stream_id}/stop")
    def stop_webrtc_stream(stream_id: str) -> dict[str, Any]:
        media.stop_webrtc_stream(stream_id)
        events.append("info", "media", "WebRTC stream stopped", stream_id=stream_id)
        return {"stream": "stopped", "stream_id": stream_id}

    # -----------------------------------------------------------------------
    # Routing matrix
    # -----------------------------------------------------------------------

    @router.get("/routing")
    def get_routing() -> dict[str, Any]:
        return store.config.routes.model_dump(mode="json")

    @router.put("/routing")
    def put_routing(routes: RouteMap) -> dict[str, Any]:
        saved = store.update({"routes": routes.model_dump(mode="python")})
        events.append("info", "control", "routing matrix updated")
        return saved.routes.model_dump(mode="json")

    # -----------------------------------------------------------------------
    # Interface selection
    # -----------------------------------------------------------------------

    @router.get("/interfaces/audio")
    def list_audio_interfaces() -> dict[str, Any]:
        selected = store.config.audio.interface_name
        interfaces = [
            {**iface, "selected": iface["name"] == selected}
            for iface in media.discover_audio_interfaces()
        ]
        return {
            "interfaces": interfaces,
            "selected": {
                "name": selected,
                "driver": store.config.audio.interface_driver,
                "channel_count": store.config.audio.channel_count,
                "sample_rate": store.config.audio.sample_rate,
            },
        }

    @router.post("/interfaces/audio")
    def select_audio_interface(body: dict[str, Any]) -> dict[str, Any]:
        name = body.get("name")
        if not name:
            raise HTTPException(status_code=422, detail="name is required")
        interfaces = media.discover_audio_interfaces()
        match = next((i for i in interfaces if i["name"] == name or i.get("id") == name), None)
        if match is None:
            raise HTTPException(status_code=404, detail=f"audio interface '{name}' not found")
        channel_count = max(1, min(int(body.get("channel_count", store.config.audio.channel_count)), 64))
        saved = store.update({
            "audio": {
                "interface_name": match["name"],
                "interface_driver": normalize_config_driver(match.get("driver")),
                "interface_device_id": match.get("device_id"),
                "channel_count": channel_count,
            }
        })
        events.append("info", "system", f"audio interface set to '{name}' ({channel_count} ch)")
        return saved.audio.model_dump(mode="json", exclude_none=True)

    @router.get("/devices/audio")
    def list_registered_audio_devices() -> dict[str, Any]:
        """Distinct capture devices currently referenced by configured sources,
        with their source counts. The host's available audio interfaces come
        from /api/interfaces/audio — this endpoint is the *registered* set."""
        in_use: dict[str, dict[str, Any]] = {}
        for source in store.config.sources:
            if source.kind not in ("dante_input", "dante_output"):
                continue
            name = source.interface_name
            if not name:
                continue
            entry = in_use.setdefault(name, {
                "name": name,
                "driver": source.interface_driver or "unknown",
                "device_id": source.interface_device_id,
                "source_count": 0,
                "input_count": 0,
                "output_count": 0,
                "max_channel": 0,
            })
            entry["source_count"] += 1
            if source.kind == "dante_input":
                entry["input_count"] += 1
            else:
                entry["output_count"] += 1
            entry["max_channel"] = max(entry["max_channel"], source.dante_channel or 0)
        return {"devices": list(in_use.values())}

    @router.post("/devices/audio")
    def add_audio_device(body: dict[str, Any]) -> dict[str, Any]:
        """Register a capture device + seed dante_input sources for it. Uses
        '{slug}-in-NN' IDs (slug derived from device name) so multiple devices
        coexist without ID collision."""
        name = body.get("name")
        if not name:
            raise HTTPException(status_code=422, detail="name is required")
        interfaces = media.discover_audio_interfaces()
        match = next((i for i in interfaces if i["name"] == name or i.get("id") == name), None)
        if match is None:
            raise HTTPException(status_code=404, detail=f"audio interface '{name}' not found")
        channel_count = max(1, min(int(body.get("channel_count", match.get("channel_count") or 8)), 64))
        
        # Always sync the master spine device to the newly added device to ensure GStreamer starts up
        store.update({
            "audio": {
                "interface_name": match["name"],
                "interface_driver": normalize_config_driver(match.get("driver")),
                "interface_device_id": match.get("device_id"),
                "channel_count": channel_count,
            }
        })
            
        added = store.add_audio_device(
            interface_name=match["name"],
            interface_driver=normalize_config_driver(match.get("driver")),
            interface_device_id=match.get("device_id"),
            channel_count=channel_count,
        )
        events.append("info", "system", f"capture device '{name}' registered ({channel_count} ch, {len(added)} sources)")
        return {"device": {"name": match["name"], "driver": normalize_config_driver(match.get("driver")), "channel_count": channel_count}, "sources_added": added}

    @router.delete("/devices/audio/{name}")
    def remove_audio_device(name: str) -> dict[str, Any]:
        removed = store.remove_audio_device(name)
        events.append("info", "system", f"capture device '{name}' removed ({len(removed)} sources)")
        return {"removed_sources": removed}

    @router.get("/interfaces/network")
    def list_network_interfaces() -> dict[str, Any]:
        cfg = store.config.network
        return {
            "interfaces": _network_interfaces(),
            "selected": {
                "dante_nic": cfg.dante_nic,
                "wan_nic": cfg.wan_nic,
            },
        }

    @router.post("/interfaces/network")
    def select_network_interfaces(body: dict[str, Any]) -> dict[str, Any]:
        dante_nic = body.get("dante_nic")
        wan_nic = body.get("wan_nic")
        if not dante_nic or not wan_nic:
            raise HTTPException(status_code=422, detail="dante_nic and wan_nic are required")
        known = {i["name"] for i in _network_interfaces()}
        for nic, label in [(dante_nic, "dante_nic"), (wan_nic, "wan_nic")]:
            if nic not in known:
                raise HTTPException(status_code=404, detail=f"{label} '{nic}' not found")
        saved = store.update({"network": {"dante_nic": dante_nic, "wan_nic": wan_nic}})
        events.append("info", "system", f"NICs set: dante={dante_nic}, wan={wan_nic}")
        return {"dante_nic": saved.network.dante_nic, "wan_nic": saved.network.wan_nic}

    # -----------------------------------------------------------------------
    # Program / talkback control
    # -----------------------------------------------------------------------

    @router.post("/program/start")
    def start_program(body: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = body or {}
        host_value = payload.get("host")
        host = None if host_value in (None, "") else str(host_value)
        port = int(payload.get("port", store.config.network.srt_port))
        frequency_hz = float(payload.get("frequency_hz", 1000.0))
        level_dbfs = float(payload.get("level_dbfs", -18.0))
        waveform = str(payload.get("waveform", "sine"))
        try:
            pipeline = media.start_program(
                config=store.config,
                host=host,
                port=port,
                frequency_hz=frequency_hz,
                level_dbfs=level_dbfs,
                waveform=waveform,
            )
        except (RuntimeError, ValueError) as exc:
            _raise_media_error(exc)
        events.append("info", "media", "program path started", srt_mode=store.config.program.srt_mode.value, host=host, port=port)
        return {"program": "running", "pipeline": pipeline}

    @router.post("/program/stop")
    def stop_program() -> dict[str, Any]:
        media.stop_program()
        events.append("info", "media", "program path stopped")
        return {"program": "stopped"}

    @router.post("/program/srt-passphrase")
    def rotate_srt_passphrase() -> dict[str, Any]:
        passphrase = secrets.token_urlsafe(24)
        saved = store.update({"program": {"srt_passphrase": passphrase}})
        events.append("info", "control", "SRT passphrase rotated")
        return {
            "srt_passphrase_set": saved.program.srt_passphrase is not None,
            "encryption_strength": saved.program.encryption_strength.value,
        }

    @router.post("/talkback/start")
    def start_talkback() -> dict[str, str]:
        media.start_talkback()
        events.append("info", "media", "talkback path started")
        return {"talkback": "running"}

    @router.post("/talkback/stop")
    def stop_talkback() -> dict[str, str]:
        media.stop_talkback()
        events.append("info", "media", "talkback path stopped")
        return {"talkback": "stopped"}

    # -----------------------------------------------------------------------
    # Status
    # -----------------------------------------------------------------------

    @router.get("/status")
    def status() -> dict[str, Any]:
        return telemetry.snapshot(store.config, media.runtime_status(store.config))

    @router.get("/media/pipelines")
    def list_media_pipelines() -> list[dict[str, Any]]:
        return media.list_pipelines()

    @router.get("/media/runtime")
    def media_runtime() -> dict[str, Any]:
        return media.runtime_status(store.config)

    @router.get("/media/graph-plan")
    def media_graph_plan() -> dict[str, Any]:
        return media.plan_media_graph(store.config)

    @router.get("/events")
    def list_events() -> list[dict[str, Any]]:
        return events.list()

    # -----------------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------------

    @router.post("/diagnostics/spine/start")
    def spine_start() -> dict[str, Any]:
        try:
            result = media.start_spine(store.config)
        except (RuntimeError, ValueError) as exc:
            _raise_media_error(exc)
        events.append("info", "diagnostics", "spine started")
        return result

    @router.post("/diagnostics/spine/stop")
    def spine_stop() -> dict[str, str]:
        media.stop_spine()
        events.append("info", "diagnostics", "spine stopped")
        return {"spine": "stopped"}

    @router.get("/diagnostics/spine")
    def spine_status() -> dict[str, Any]:
        return media.describe_spine()

    @router.post("/diagnostics/tone")
    def tone_start(body: dict[str, Any]) -> dict[str, Any]:
        freq = float(body.get("frequency_hz", 1000))
        level = float(body.get("level_dbfs", -18))
        channel = int(body.get("channel", 1))
        waveform = str(body.get("waveform", "sine"))
        host = str(body.get("host", "127.0.0.1"))
        port = int(body.get("port", store.config.network.srt_port))
        srt_mode = str(body.get("srt_mode", "caller"))
        try:
            pipeline = media.start_tone(
                config=store.config,
                frequency_hz=freq,
                level_dbfs=level,
                channel=channel,
                waveform=waveform,
                host=host,
                port=port,
                srt_mode=srt_mode,
            )
        except (RuntimeError, ValueError) as exc:
            _raise_media_error(exc)
        events.append("info", "diagnostics", "tone started", frequency_hz=freq, level_dbfs=level, channel=channel, waveform=waveform)
        return {
            "tone": "running",
            "frequency_hz": freq,
            "level_dbfs": level,
            "channel": channel,
            "waveform": waveform,
            "transport": {"host": host, "port": port, "srt_mode": srt_mode},
            "pipeline": pipeline,
        }

    @router.post("/diagnostics/tone/stop")
    def tone_stop() -> dict[str, str]:
        media.stop_tone()
        events.append("info", "diagnostics", "tone stopped")
        return {"tone": "stopped"}

    @router.post("/diagnostics/loopback")
    def loopback_start(body: dict[str, Any]) -> dict[str, Any]:
        input_channels = [int(c) for c in body.get("input_channels", [1])]
        output_channels = [int(c) for c in body.get("output_channels", [1])]
        media.start_loopback(input_channels, output_channels)
        events.append("info", "diagnostics", "loopback started", input_channels=input_channels, output_channels=output_channels)
        return {"loopback": "running", "input_channels": input_channels, "output_channels": output_channels}

    @router.delete("/diagnostics/loopback")
    def loopback_stop() -> dict[str, str]:
        media.stop_loopback()
        events.append("info", "diagnostics", "loopback stopped")
        return {"loopback": "stopped"}

    @router.post("/diagnostics/monitor")
    def monitor_start(body: dict[str, Any]) -> dict[str, Any]:
        channel = int(body.get("channel", 1))
        is_input = bool(body.get("is_input", True))
        transport_id_value = body.get("transport_id")
        transport_id = None if transport_id_value in (None, "") else str(transport_id_value)
        host_value = body.get("host")
        host = None if host_value in (None, "") else str(host_value)
        port = int(body.get("port", store.config.network.srt_port))
        srt_mode = str(body.get("srt_mode", "listener"))
        try:
            pipeline = media.start_monitor(
                config=store.config,
                channel=channel,
                is_input=is_input,
                transport_id=transport_id,
                host=host,
                port=port,
                srt_mode=srt_mode,
            )
        except (RuntimeError, ValueError) as exc:
            _raise_media_error(exc)
        events.append("info", "diagnostics", "monitor started", channel=channel, is_input=is_input)
        return {
            "monitor": "running",
            "channel": channel,
            "is_input": is_input,
            "transport_id": transport_id,
            "transport": {"host": host, "port": port, "srt_mode": srt_mode},
            "pipeline": pipeline,
        }

    @router.delete("/diagnostics/monitor")
    def monitor_stop() -> dict[str, str]:
        media.stop_monitor()
        events.append("info", "diagnostics", "monitor stopped")
        return {"monitor": "stopped"}

    @router.post("/diagnostics/round-trip")
    def round_trip(body: dict[str, Any]) -> dict[str, Any]:
        result = media.run_round_trip(
            stimulus_channel=int(body.get("stimulus_channel", 1)),
            return_channel=int(body.get("return_channel", 1)),
        )
        events.append("info", "diagnostics", "round-trip test run")
        return result

    # -----------------------------------------------------------------------
    # Pairing
    # -----------------------------------------------------------------------

    @router.post("/pairing/bundle")
    def create_pairing_bundle() -> dict[str, Any]:
        _purge_expired_bundles()
        token = secrets.token_urlsafe(32)
        passphrase = secrets.token_urlsafe(24)
        store.update({"program": {"srt_passphrase": passphrase}})
        expires_at = time.time() + 900
        bundle: dict[str, Any] = {
            "endpoint_name": store.config.endpoint_name,
            "signaling_url": (
                f"wss://{store.config.network.public_address or 'host.example.com'}"
                f":{store.config.network.signaling_port}/api/ws/signaling"
            ),
            "bearer_token": token,
            "srt_passphrase": passphrase,
            "suggested_srt_mode": "caller" if store.config.program.srt_mode.value == "listener" else "listener",
            "srt_port": store.config.network.srt_port,
            "expires_in_seconds": 900,
            "single_use": True,
        }
        _pending_bundles[token] = (bundle, expires_at)
        events.append("info", "pairing", "pairing bundle generated")
        return bundle

    @router.post("/pairing/apply")
    def apply_pairing(bundle: dict[str, Any]) -> dict[str, Any]:
        token = bundle.get("bearer_token")
        signaling_url = bundle.get("signaling_url")
        passphrase = bundle.get("srt_passphrase")
        if not token or not signaling_url:
            raise HTTPException(status_code=422, detail="pairing bundle requires bearer_token and signaling_url")
        if store.config.program.encryption_enabled and not passphrase:
            raise HTTPException(status_code=422, detail="pairing bundle requires srt_passphrase when SRT encryption is enabled")
        update = {
            "pairing": {
                "peer_name": bundle.get("endpoint_name"),
                "peer_signaling_url": signaling_url,
                "bearer_token": token,
            },
            "program": {"srt_passphrase": passphrase},
        }
        saved = store.update(update)
        events.append("info", "pairing", "pairing bundle applied")
        return saved.model_dump(mode="json", exclude_none=True)

    @router.delete("/pairing")
    def clear_pairing() -> dict[str, str]:
        store.update({"pairing": {"peer_name": None, "peer_signaling_url": None, "bearer_token": None}})
        events.append("info", "pairing", "pairing cleared")
        return {"pairing": "cleared"}

    # -----------------------------------------------------------------------
    # WebSockets
    # -----------------------------------------------------------------------

    @router.websocket("/ws/status")
    async def status_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            async for snapshot in telemetry.stream(lambda: store.config, lambda: media.runtime_status(store.config)):
                await websocket.send_json(snapshot)
        except WebSocketDisconnect:
            return

    @router.websocket("/ws/signaling")
    async def signaling_ws(websocket: WebSocket) -> None:
        await websocket.accept()
        events.append("info", "signaling", "signaling websocket connected")
        try:
            while True:
                message = await websocket.receive_json()
                await websocket.send_json({"type": "ack", "received": message.get("type", "unknown")})
        except WebSocketDisconnect:
            events.append("info", "signaling", "signaling websocket disconnected")

    # -----------------------------------------------------------------------
    # Log streaming (SSE)
    # -----------------------------------------------------------------------

    @router.get("/logs/stream")
    async def stream_logs() -> StreamingResponse:
        async def generator():
            async with events.subscribe() as queue:
                while True:
                    event = await queue.get()
                    yield f"data: {json.dumps(event)}\n\n"

        return StreamingResponse(
            generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    return router
