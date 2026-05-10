from __future__ import annotations

import json
import secrets
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse

from app.core.config import EndpointConfig, RouteMap
from app.core.config_store import ConfigStore
from app.services.events import EventLog
from app.services.media import MediaController
from app.services.telemetry import TelemetryService

# ---------------------------------------------------------------------------
# Stub audio / network interface catalogue
# Replace with real enumeration once GStreamer / WASAPI / ALSA layers exist.
# ---------------------------------------------------------------------------
_STUB_AUDIO_INTERFACES = [
    {"name": "Dante Virtual Soundcard", "driver": "wasapi", "max_channels": 64},
    {"name": "System Default Output", "driver": "wasapi", "max_channels": 2},
]
_STUB_NETWORK_INTERFACES = [
    {"name": "Ethernet", "description": "Intel I219-V"},
    {"name": "Ethernet 2", "description": "Realtek PCIe GbE"},
    {"name": "Wi-Fi", "description": "Intel AX201"},
]


def create_api_router(
    store: ConfigStore,
    media: MediaController,
    telemetry: TelemetryService,
    events: EventLog,
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
    def list_audio_interfaces() -> list[dict[str, Any]]:
        selected = store.config.audio.interface_name
        return [
            {**iface, "selected": iface["name"] == selected}
            for iface in _STUB_AUDIO_INTERFACES
        ]

    @router.post("/interfaces/audio")
    def select_audio_interface(body: dict[str, Any]) -> dict[str, Any]:
        name = body.get("name")
        if not name:
            raise HTTPException(status_code=422, detail="name is required")
        match = next((i for i in _STUB_AUDIO_INTERFACES if i["name"] == name), None)
        if match is None:
            raise HTTPException(status_code=404, detail=f"audio interface '{name}' not found")
        channel_count = min(int(body.get("channel_count", match["max_channels"])), match["max_channels"])
        saved = store.update({
            "audio": {
                "interface_name": match["name"],
                "interface_driver": match["driver"],
                "channel_count": channel_count,
            }
        })
        events.append("info", "system", f"audio interface set to '{name}' ({channel_count} ch)")
        return saved.audio.model_dump(mode="json", exclude_none=True)

    @router.get("/interfaces/network")
    def list_network_interfaces() -> dict[str, Any]:
        cfg = store.config.network
        return {
            "interfaces": _STUB_NETWORK_INTERFACES,
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
        known = {i["name"] for i in _STUB_NETWORK_INTERFACES}
        for nic, label in [(dante_nic, "dante_nic"), (wan_nic, "wan_nic")]:
            if nic not in known:
                raise HTTPException(status_code=404, detail=f"{label} '{nic}' not found")
        if dante_nic == wan_nic:
            raise HTTPException(status_code=422, detail="dante_nic and wan_nic must be different interfaces")
        saved = store.update({"network": {"dante_nic": dante_nic, "wan_nic": wan_nic}})
        events.append("info", "system", f"NICs set: dante={dante_nic}, wan={wan_nic}")
        return {"dante_nic": saved.network.dante_nic, "wan_nic": saved.network.wan_nic}

    # -----------------------------------------------------------------------
    # Program / talkback control
    # -----------------------------------------------------------------------

    @router.post("/program/start")
    def start_program() -> dict[str, Any]:
        media.start_program()
        events.append("info", "media", "program path started")
        return {"program": "running", "pipeline": media.describe_program_pipeline(store.config)}

    @router.post("/program/stop")
    def stop_program() -> dict[str, Any]:
        media.stop_program()
        events.append("info", "media", "program path stopped")
        return {"program": "stopped"}

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
        return telemetry.snapshot(store.config.audio.channel_count)

    @router.get("/events")
    def list_events() -> list[dict[str, Any]]:
        return events.list()

    # -----------------------------------------------------------------------
    # Diagnostics
    # -----------------------------------------------------------------------

    @router.post("/diagnostics/tone")
    def tone_start(body: dict[str, Any]) -> dict[str, Any]:
        freq = float(body.get("frequency_hz", 1000))
        level = float(body.get("level_dbfs", -18))
        channel = int(body.get("channel", 1))
        waveform = str(body.get("waveform", "sine"))
        media.start_tone(frequency_hz=freq, level_dbfs=level, channel=channel, waveform=waveform)
        events.append("info", "diagnostics", "tone started", frequency_hz=freq, level_dbfs=level, channel=channel, waveform=waveform)
        return {"tone": "running", "frequency_hz": freq, "level_dbfs": level, "channel": channel, "waveform": waveform}

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
        media.start_monitor(channel=channel, is_input=is_input)
        events.append("info", "diagnostics", "monitor started", channel=channel, is_input=is_input)
        return {"monitor": "running", "channel": channel, "is_input": is_input}

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
        expires_at = time.time() + 900
        bundle: dict[str, Any] = {
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
        _purge_expired_bundles()
        token = bundle.get("bearer_token")
        if not token or token not in _pending_bundles:
            raise HTTPException(status_code=401, detail="invalid or expired pairing bundle")
        stored_bundle, _ = _pending_bundles.pop(token)
        update = {
            "pairing": {
                "peer_signaling_url": stored_bundle.get("signaling_url"),
                "bearer_token": stored_bundle.get("bearer_token"),
            },
            "program": {"srt_passphrase": stored_bundle.get("srt_passphrase")},
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
            async for snapshot in telemetry.stream(lambda: store.config.audio.channel_count):
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
