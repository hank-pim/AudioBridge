# Dante Bridge

Prototype endpoint app for a point-to-point Dante WAN bridge. The current build is a runnable control-plane and operator UI skeleton based on `planv2.md`; the media plane is built around GStreamer pipelines and a single full-duplex DVS spine that owns Dante Virtual Soundcard directly.

JACK is not part of the current runtime. The earlier JACK plan is kept in `docs/` as archived investigation material only.

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

## Runtime Requirements

- Python 3.11 or newer.
- GStreamer 1.22+ installed locally, with `gst-launch-1.0` and `gst-inspect-1.0` available on `PATH` or in the default Windows install path.
- GStreamer plugins for SRT, Opus, MPEG-TS, audio conversion/resampling, audio test sources, and the host audio backend you plan to use.
- Windows Dante I/O testing: Dante Virtual Soundcard installed/licensed in ASIO mode only, Dante Controller installed for routing/verification, and a GStreamer build with ASIO support (`asiosrc`/`asiosink`).
- macOS Dante I/O testing is not currently packaged for this tester build.
- Network access between endpoints for SRT transport ports.

## Build a Distribution

```powershell
.\scripts\dist.ps1
```

Use `-SkipTests` to build without running the test suite first. The script cleans old build artifacts, ensures the `build` package is installed, builds a wheel, and writes `dist/dantebridge-tester.zip` for early Windows/macOS testing. The tester zip includes launcher scripts and creates a local virtual environment on first run; it expects the runtime requirements above to already be installed.

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
