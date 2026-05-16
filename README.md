# Dante Bridge

Prototype endpoint app for a point-to-point Dante WAN bridge. The current build is a runnable control-plane and operator UI skeleton based on `planv2.md`; the GStreamer/Dante media plane is represented by explicit controller boundaries, subprocess diagnostics, and truthful null telemetry for observations that are not wired yet.

The current media architecture pivot is documented in `docs/jack-audio-engine-plan.md`: JACK owns Dante Virtual Soundcard's ASIO driver once, while the app manages dynamic stream workers and Dante-facing routes.

## What Exists

- FastAPI service with REST and WebSocket APIs.
- Versioned TOML config model with atomic saves.
- Static dark operator UI served by the control plane.
- Transport-oriented API for sources, encode groups, SRT transports, and WebRTC streams.
- Legacy program and talkback start/stop controls retained while the UI catches up.
- Pairing bundle generation and application endpoints.
- Diagnostics stubs for interfaces and tone generation.
- Live status stream, event log, per-transport runtime state, and explicit media-runtime capability reporting.

## Run Locally

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
uvicorn app.main:app --reload --host 127.0.0.1 --port 8443
```

Open `http://127.0.0.1:8443`.

The app writes its endpoint config to `config/endpoint.toml` the first time settings are saved.

## API Shape

- `GET /api/health`
- `GET /api/config`
- `PUT /api/config`
- `PATCH /api/config`
- `GET /api/sources`
- `POST /api/sources`
- `PUT /api/sources/{source_id}`
- `DELETE /api/sources/{source_id}`
- `GET /api/encode-groups`
- `POST /api/encode-groups`
- `PUT /api/encode-groups/{group_id}`
- `DELETE /api/encode-groups/{group_id}`
- `GET /api/srt-transports`
- `POST /api/srt-transports`
- `PUT /api/srt-transports/{transport_id}`
- `DELETE /api/srt-transports/{transport_id}`
- `POST /api/srt-transports/{transport_id}/start`
- `POST /api/srt-transports/{transport_id}/stop`
- `GET /api/webrtc-streams`
- `POST /api/webrtc-streams`
- `PUT /api/webrtc-streams/{stream_id}`
- `DELETE /api/webrtc-streams/{stream_id}`
- `POST /api/webrtc-streams/{stream_id}/start`
- `POST /api/webrtc-streams/{stream_id}/stop`
- `POST /api/program/start`
- `POST /api/program/stop`
- `POST /api/talkback/start`
- `POST /api/talkback/stop`
- `GET /api/status`
- `GET /api/media/runtime`
- `GET /api/media/pipelines`
- `GET /api/events`
- `POST /api/pairing/bundle`
- `POST /api/pairing/apply`
- `GET /api/diagnostics/interfaces`
- `POST /api/diagnostics/tone`
- `WS /api/ws/status`
- `WS /api/ws/signaling`

## Next Implementation Steps

1. Add platform probes for NICs, audio devices, CPU, memory, network counters, SRT socket stats, and audio meters.
2. Add a first-class media runtime and graph builder for configured sources, encode groups, OPUS encode/decode, SRT transport, and shared-ratio resampling.
3. Add WebRTC signaling message types, browser talkback capture, and endpoint-to-endpoint SDP/ICE forwarding.
4. Add authentication, HTTPS certificate generation, token hashing, and log redaction.
5. Add tests around config migrations, route validation, pairing bundle expiry, and API behavior.
