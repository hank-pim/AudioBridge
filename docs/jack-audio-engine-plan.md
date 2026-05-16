# JACK Audio Engine Plan

## Status (2026-05-11): deferred

This plan is on hold. The Phase 1 spike found that the smoke-test failures attributed below to "Windows named-pipe client handshake" are actually a **DVS ASIO ↔ PortAudio incompatibility** in the only currently-available JACK2 Windows binary (jackaudio.org 1.9.22, Feb 2023). With DVS, `Pa_StartStream` returns `paNoError` and 128 system ports register, but no ASIO callback ever fires, so JACK rejects every client with `Driver is not running`. The same JACK + PortAudio path was verified working end-to-end against a Focusrite USB ASIO device, isolating the issue to DVS specifically. Newer JACK2 binaries are not available — `develop` has had no commits since 2026-01-07, and every CI artifact has aged past the 90-day retention window. Details in the "Phase 1: Feasibility spike" checkpoint near the bottom of this doc.

The underlying product problem (two simultaneous GStreamer subprocesses can't both open DVS ASIO) is addressed by a simpler design that does not introduce JACK: a single per-endpoint GStreamer process owns DVS once and fans out internally to multiple SRT TX legs. See [single-pipeline-tx-plan.md](single-pipeline-tx-plan.md). Revisit JACK only if dynamic add/remove of streams without rebuilding the pipeline becomes a hard requirement.

## Problem

Dante Virtual Soundcard's ASIO driver is effectively a single-client device. The current media direction, where independent TX and RX GStreamer pipelines each try to open DVS through `asiosrc` or `asiosink`, cannot support the product model. TX alone can work, but starting RX attempts to open the same ASIO driver again and DVS refuses the second instance.

The product still requires dynamic multi-stream operation:

- Multiple SRT program streams.
- Multiple WebRTC/talkback/IFB style streams.
- Independent stream start/stop.
- Operator-visible Dante input/output routing.
- Low-latency DVS ASIO operation.

The fix is not "one stream." The fix is one audio device owner.

## Decision

Use JACK as the persistent local audio engine. JACK owns Dante Virtual Soundcard ASIO once, and Dante Bridge manages dynamic GStreamer workers and JACK routes through the backend.

```text
Dante Virtual Soundcard ASIO
  owned once by JACK

JACK physical capture ports
  -> TX GStreamer workers
  -> OPUS/RTP/SRT

SRT/RTP/OPUS
  -> RX GStreamer workers
  -> JACK physical playback ports
```

JACK is internal infrastructure. The operator UI must continue to expose Dante concepts only:

```text
Dante Input 1 -> PGM Main channel 1
PGM Main channel 1 -> Dante Output 1
```

The UI must not expose JACK port names such as `system:capture_1`, `system:playback_1`, `jackaudiosrc`, or `jackaudiosink` in normal operation.

## Local Setup

### Required local software

- Dante Virtual Soundcard installed and licensed.
- JACK2 for Windows installed.
- GStreamer installed with the JACK plugin available.
- Python package for JACK control:

```powershell
.\.venv\Scripts\Activate.ps1
pip install JACK-Client
```

The code imports this package as `jack`.

### Start JACK against DVS

Initial manual test command:

```powershell
jackd.exe -R -S -d portaudio -d "ASIO::Dante Virtual Soundcard" -r 48000 -p 256
```

On the current Windows test host, PortAudio reports the DVS ASIO device as:

```text
ASIO::Dante Virtual Soundcard (x64)
```

Use the exact detected name for local testing:

```powershell
& "C:\Program Files\JACK2\jackd.exe" -R -S -d portaudio -d "ASIO::Dante Virtual Soundcard (x64)" -r 48000 -p 256
```

Meaning:

- `-R`: request realtime scheduling where available.
- `-S`: synchronous mode.
- `-d portaudio`: use JACK's PortAudio backend on Windows.
- `-d "ASIO::Dante Virtual Soundcard"`: select the DVS ASIO device through PortAudio.
- `-r 48000`: Dante Bridge sample rate.
- `-p 256`: initial buffer size. This is a starting point, not a final latency target.

For app-managed startup, the backend should spawn the same command as a background subprocess, capture stdout/stderr, and report its state through the diagnostics API.

### Validate JACK ports

After JACK starts, verify that physical DVS ports are present:

```powershell
jack_lsp.exe
```

Expected shape:

```text
system:capture_1
system:capture_2
...
system:playback_1
system:playback_2
...
```

The exact count should match the DVS channel configuration.

### Validate GStreamer JACK support

```powershell
gst-inspect-1.0 jackaudiosrc
gst-inspect-1.0 jackaudiosink
```

Both commands must find a plugin. If either element is missing, the local GStreamer install cannot run the planned media graphs yet.

### Smoke test TX capture

This verifies that GStreamer can become a JACK client and read from the JACK graph:

```powershell
gst-launch-1.0 -m jackaudiosrc connect=0 client-name=DbTxSmoke ! audioconvert ! audioresample ! level message=true ! fakesink sync=false
```

Then connect a Dante/JACK capture port to the GStreamer input port:

```powershell
jack_connect.exe system:capture_1 DbTxSmoke:input_1
```

### Smoke test RX playback

This verifies that GStreamer can write to JACK and route to DVS playback:

```powershell
gst-launch-1.0 -m audiotestsrc is-live=true wave=sine freq=1000 volume=0.05 ! audioconvert ! audioresample ! jackaudiosink connect=0 client-name=DbRxSmoke
```

Then connect the GStreamer output port to a Dante/JACK playback port:

```powershell
jack_connect.exe DbRxSmoke:output_1 system:playback_1
```

## Backend Services

### JackDaemonService

Responsibilities:

- Start `jackd.exe` when JACK mode is enabled.
- Detect an already-running JACK server and attach without taking ownership.
- Capture stdout/stderr into diagnostics.
- Report health, command line, sample rate, buffer size, and DVS device name.
- Stop JACK on app shutdown only if this app started it.
- Never restart JACK automatically while streams are running unless the operator explicitly requests it.

Initial configuration fields:

```toml
[audio_engine]
backend = "jack"
jackd_executable = "jackd.exe"
driver = "portaudio"
device = "ASIO::Dante Virtual Soundcard"
sample_rate = 48000
buffer_size = 256
realtime = true
synchronous = true
autostart = true
```

### JackRouterService

Responsibilities:

- Maintain one long-lived passive JACK client for route control.
- List JACK ports.
- Connect and disconnect ports.
- Translate JACK errors into Dante Bridge domain errors.
- Use a lock around list/connect/disconnect operations.

The routing service should never expose raw JACK port names to normal UI endpoints. Raw ports are allowed only in an advanced diagnostics endpoint.

### AudioRouteService

Responsibilities:

- Store and resolve user-facing route intent.
- Translate Dante Bridge domain routes to JACK port names.
- Reapply configured routes when JACK or stream workers restart.
- Detect missing stream ports and report route state as `pending`, not as a fatal config error.

Persist this:

```json
{
  "source": {
    "kind": "dante_input",
    "channel": 1
  },
  "destination": {
    "kind": "stream_input",
    "stream_id": "pgm-main",
    "channel": 1
  }
}
```

Do not persist this:

```json
{
  "source_port": "system:capture_1",
  "destination_port": "srt_tx_pgm_main:input_1"
}
```

JACK port names are runtime resolution details and may change when workers restart.

## JACK Client Naming

`jackaudiosrc` and `jackaudiosink` do not share a JACK client context by default. Each element creates its own JACK client. Never give two JACK elements the same `client-name`, even if they belong to the same logical Dante Bridge stream or the same GStreamer pipeline.

Use deterministic, unique names per direction and element role:

```text
db_tx_pgm_main
db_rx_pgm_main
db_tx_pgm_main_monitor
db_rx_pgm_main_monitor
```

Do not use this:

```text
jackaudiosrc client-name=db_pgm_main ... jackaudiosink client-name=db_pgm_main
```

The first JACK client may register successfully, while the second fails with a JACK client-name conflict and causes the GStreamer pipeline to fail preroll.

## Port Translation

### Dante hardware

```text
Dante Input N  -> system:capture_N
Dante Output N -> system:playback_N
```

These names should be discovered from JACK physical ports rather than hard-coded blindly. The `system:*` pattern is expected for JACK's PortAudio backend, but the resolver should still validate it.

### TX stream worker

User route:

```text
Dante Input 1 -> PGM Main channel 1
```

Internal route:

```text
system:capture_1 -> db_tx_pgm_main:input_1
```

TX GStreamer graph shape:

```text
jackaudiosrc connect=0 client-name=db_tx_pgm_main
  -> audioconvert
  -> audioresample
  -> audio/x-raw,rate=48000
  -> opusenc
  -> rtpopuspay
  -> srtsink
```

### RX stream worker

User route:

```text
PGM Main channel 1 -> Dante Output 1
```

Internal route:

```text
db_rx_pgm_main:output_1 -> system:playback_1
```

RX GStreamer graph shape:

```text
srtsrc
  -> rtpjitterbuffer
  -> rtpopusdepay
  -> opusdec
  -> audioconvert
  -> audioresample
  -> jackaudiosink connect=0 client-name=db_rx_pgm_main
```

## API Shape

Normal operator API:

- `GET /api/audio-engine/status`
- `POST /api/audio-engine/start`
- `POST /api/audio-engine/stop`
- `GET /api/audio/routes`
- `POST /api/audio/routes`
- `DELETE /api/audio/routes`
- `GET /api/audio/dante-ports`

Advanced diagnostics API:

- `GET /api/audio-engine/jack/ports`
- `GET /api/audio-engine/jack/connections`
- `POST /api/audio-engine/jack/reconnect-routes`

Normal route response should use Dante Bridge names:

```json
{
  "routes": [
    {
      "source": {"kind": "dante_input", "channel": 1, "label": "Dante Input 1"},
      "destination": {"kind": "stream_input", "stream_id": "pgm-main", "channel": 1, "label": "PGM Main 1"},
      "state": "connected"
    }
  ]
}
```

Advanced diagnostics may include raw JACK names:

```json
{
  "source_port": "system:capture_1",
  "destination_port": "db_tx_pgm_main:input_1"
}
```

## UI Rules

- Show "Audio engine: Running" rather than "JACK server running."
- Show "Driver: Dante Virtual Soundcard" rather than the JACK backend device string.
- Show sample rate and buffer size because they affect latency.
- Show route problems in Dante terms.
- Hide raw JACK ports unless the operator opens an advanced diagnostics view.

Good error:

```text
Could not route Dante Input 1 to PGM Main channel 1 because the stream is not running.
```

Bad error:

```text
Cannot connect system:capture_1 to db_tx_pgm_main:input_1.
```

## Implementation Phases

### Phase 1: Feasibility spike

- Install/run JACK against DVS with the command above.
- Verify physical port discovery.
- Verify `jackaudiosrc` and `jackaudiosink` are available.
- Run simultaneous TX and RX smoke graphs.
- Start and stop two GStreamer JACK clients dynamically while JACK remains running.
- Measure stable buffer sizes at `-p 512`, `-p 256`, and `-p 128`.

Local checkpoint from 2026-05-11 (revised):

Earlier in the session this checkpoint blamed a "Windows named-pipe client handshake" issue. Deeper diagnosis later the same day showed that hypothesis was wrong; the corrected findings are below. The original speculative entries have been removed to avoid future readers acting on them.

Ruled out:

- JACK2 IPC on this Windows 11 build. A dummy-driver jackd starts cleanly and `jack_lsp` lists the dummy `system:capture_1/2` / `system:playback_1/2` ports.
- Win11 named-pipe permissions. Apparent handshake failures (`err = 5` `ERROR_ACCESS_DENIED`) only occurred when jackd was launched from an elevated PowerShell and clients from a non-elevated one. Matching integrity levels fixed the handshake completely.
- Version mismatch between `jackd.exe` and `C:\Windows\libjack64.dll`. Both have identical 2023-02-03 timestamps from the same JACK2 1.9.22 installer.
- JackRouter ASIO. Uninstalling the JackRouter ASIO driver eliminated some incidental "Cannot connect to named pipe" log noise during PortAudio ASIO enumeration but did not change the failure mode.
- Sample-rate or buffer-size mismatch. The same failure reproduces at `-r 48000 -p 256`, `-r 48000 -p 128`, and `-r 44100 -p 256`.
- Other ASIO consumers holding DVS. Closing `dvs_gui.exe` and verifying no other ASIO host was running did not change behavior. Dante background services (`conmon`, `DanteDiscovery`, `dvs.manager`, `dvs_service`) do not hold ASIO.

Root cause (still believed):

- DVS ASIO is incompatible with the PortAudio host shipped in JACK2 1.9.22's `jack_portaudio.dll`. After `JackPortAudioDriver::Open` and `OpenStream`, all 128 `system:capture_*` / `system:playback_*` ports register, the named-pipe server binds, and `JackPortAudioDriver::Start` is called. PortAudio reports no error. But no ASIO callback ever fires, so JACK's engine considers the driver unstarted and rejects any external client with `Driver is not running` (returned to the client as status `0x21`, `JackFailure | JackNameNotUnique`, which misleads readers into thinking it's a client-name conflict — the cause is unrelated).
- The same JACK + PortAudio binaries, against `ASIO::Focusrite USB ASIO` instead of DVS, work end to end: `jack_lsp` connects and lists the Focusrite's 10 capture / 2 playback ports. This isolates the failure to DVS specifically.

Other facts that remain true:

- JACK2 is installed at `C:\Program Files\JACK2`.
- GStreamer JACK elements `jackaudiosrc` and `jackaudiosink` are available after rebuilding the GStreamer registry cache.
- Python `JACK-Client` 0.5.5 imports as `jack`.
- PortAudio detects DVS as `ASIO::Dante Virtual Soundcard (x64)` with 64 inputs and 64 outputs. DVS does not expose itself as a Windows WASAPI or WDM-KS endpoint, so PortAudio's non-ASIO host APIs are not a fallback.
- The JACK2 installer places the client DLL at `C:\Windows\libjack64.dll`. No `libjack64.dll` exists under `C:\Program Files\JACK2`.

Newer-binary path is not currently viable:

- JACK2 `develop` branch's most recent commit is 2026-01-07.
- All GitHub Actions artifacts (`jack2-win64-*.zip`) are past the 90-day retention window. There is no published nightly binary newer than the installed 2023-02-03 build.
- Forking and re-running CI would rebuild the same 4-month-old source; the likelihood that a fix landed in that window for this DVS/PortAudio interaction is low.

Exit criteria:

- JACK owns DVS full-duplex.
- Multiple GStreamer clients can attach/detach.
- No second ASIO instance errors.
- Audio remains stable for a basic two-direction test.

### Phase 2: Backend infrastructure

- Add `JackDaemonService`.
- Add `JackRouterService`.
- Add `AudioRouteService`.
- Add status and route APIs.
- Add tests with fake JACK client objects.

### Phase 3: GStreamer graph migration

- Add JACK-backed TX graph generation.
- Add JACK-backed RX graph generation.
- Keep direct ASIO graph generation as diagnostic-only while migration is in progress.
- Ensure stream worker client names are deterministic and sanitized.

### Phase 4: UI abstraction

- Update routing UI to show Dante inputs/outputs and stream channels only.
- Add audio engine status panel.
- Add advanced diagnostics drawer for raw JACK ports/connections.
- Translate all route errors into Dante Bridge domain language.

### Phase 5: Reliability and recovery

- Reapply intended routes after stream restart.
- Mark routes `pending` while stream ports are absent.
- Detect JACK server loss and mark all audio routes unavailable.
- Require operator action before restarting JACK while streams are active.

## Open Questions

- What is the lowest stable JACK buffer size with DVS on the target Windows machines?
- Does JACK's PortAudio backend expose all configured DVS channels consistently by number?
- Are channel names stable across DVS channel-count changes?
- Can GStreamer JACK client port names be made deterministic enough for route reconciliation?
- Should audio engine startup be automatic on app boot or explicit from the operator UI?
