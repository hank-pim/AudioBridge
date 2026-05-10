# Dante Bridge

Prototype endpoint app for a point-to-point Dante WAN bridge. The first build is a runnable control-plane and operator UI skeleton based on `planv2.md`; the GStreamer/Dante media plane is represented by explicit controller boundaries and simulated telemetry so the product surface can be exercised early.

## What Exists

- FastAPI service with REST and WebSocket APIs.
- Versioned TOML config model with atomic saves.
- Static dark operator UI served by the control plane.
- Program and talkback start/stop controls.
- Pairing bundle generation and application endpoints.
- Diagnostics stubs for interfaces and tone generation.
- Live status stream, event log, and mock clock-recovery telemetry.

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
- `POST /api/program/start`
- `POST /api/program/stop`
- `POST /api/talkback/start`
- `POST /api/talkback/stop`
- `GET /api/status`
- `GET /api/events`
- `POST /api/pairing/bundle`
- `POST /api/pairing/apply`
- `GET /api/diagnostics/interfaces`
- `POST /api/diagnostics/tone`
- `WS /api/ws/status`
- `WS /api/ws/signaling`

## Next Implementation Steps

1. Replace simulated telemetry with platform probes for NICs, audio devices, CPU, memory, and network counters.
2. Add the media pipeline builder for GStreamer program path capture, OPUS encode/decode, SRT transport, and shared-ratio resampling.
3. Add WebRTC signaling message types, browser talkback capture, and endpoint-to-endpoint SDP/ICE forwarding.
4. Add authentication, HTTPS certificate generation, token hashing, and log redaction.
5. Add tests around config migrations, route validation, pairing bundle expiry, and API behavior.
