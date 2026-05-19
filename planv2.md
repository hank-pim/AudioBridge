# Dante WAN Bridge — Design Plan (rev 2)

## Goal

A point-to-point bridge that extends a Dante audio network across the open internet to another Dante network, with a self-contained per-endpoint web UI for configuration, diagnostics, and operation. Two complementary media paths: a high-reliability program path (SRT/OPUS) and a low-latency talkback path (WebRTC/OPUS). No mandatory cloud services. Symmetric endpoints — either side can initiate, configure, or operate the link. The product is an *audio link* between independently-clocked facilities, not a *clock link*; sample-accurate timing across the WAN is explicitly out of scope and requires a different class of transport.

## System shape

- Two identical endpoint applications, one per site.
- Each endpoint runs three logical layers in one process:
  - **Media plane**: GStreamer pipelines for capture, encode, transport, decode, resample, playback.
  - **Control plane**: Python + FastAPI service exposing REST + WebSocket APIs for configuration, status, and inter-endpoint signaling.
  - **UI**: static web app (SvelteKit or React+Vite) served by the control plane, accessed by the operator via browser.
- Endpoints talk to each other over a single authenticated WebSocket (`wss://`) for signaling and remote control, plus the SRT and WebRTC media flows.
- Two NICs per endpoint: one on the Dante LAN, one on the WAN. Non-negotiable.
- DVS or PCIe (Brooklyn/Broadway) Dante interface, selected per deployment.

## Audio I/O and Dante integration

- Enumerate available audio interfaces on host (WASAPI/CoreAudio/ALSA), with explicit identification of Dante Virtual Soundcard or Dante PCIe driver.
- Per-endpoint configurable channel count up to interface maximum (typ. 64×64).
- Sample rate fixed at 48 kHz (Dante standard); 44.1/96 out of scope.
- Per-channel labels persisted in config and visible throughout UI.
- Channel routing matrix: which input channels feed which encoded streams; which decoded streams feed which output channels. Independent for program and talkback paths.

## Program path (SRT + OPUS)

- Single OPUS multichannel encoder feeds one MPEG-TS/Opus bytestream over SRT using channel mapping family 255 (discrete channels, no surround layout). All channels share encoder framing, which preserves inter-channel phase alignment — critical for stereo imaging and multi-mic capture. Per-channel taps live pre-encode (after `level`, before `interleave`) so monitoring and metering remain per-channel. MPEG-TS repeats stream metadata so receivers can join a running SRT stream, unlike raw Ogg/Opus headers that are only sent at stream start. This avoids family 1's LFE/surround assumptions, which would corrupt one slot in 6/7/8-channel groups carrying unrelated mono feeds. Trade-off: discrete mode gives up stereo intensity coupling efficiency for paired program audio, acceptable because the bridge primarily carries discrete Dante channels.
- Configurable per stream: OPUS bitrate, frame size, complexity, in-band FEC on/off, expected packet-loss percentage.
- SRT mode selectable: caller, listener, or rendezvous. Defaults presented based on which side has a public IP/open port.
- Configurable SRT latency window (the dominant tuning knob); UI surfaces it on the main screen with a recommended starting value derived from measured RTT.
- SRT encryption: AES-128 / AES-192 / AES-256 with shared passphrase, on by default. "Disable encryption" toggle hidden behind an Advanced section with a persistent visible warning when active; auto-reverts to encrypted on service restart.
- Stream key / passphrase auto-generated on first run, regeneratable from UI.
- Configurable inbound bandwidth cap to leave headroom for talkback when sharing a WAN link.
- Receive side: OPUS decode → multichannel resample (clock recovery, see below) → output channel routing → DVS/PCIe.

### Clock recovery (program path)

The receiver's Dante audio clock and the sender's Dante audio clock are independent crystals running at nominally 48 kHz with realistic mismatch on the order of ±20 ppm. SRT smooths network jitter on the input side, leaving a clean, slowly-varying drift signal that the resampler must absorb. Two clock-recovery modes are exposed; mode is selectable per direction.

**Resampler structure (shared by both modes):**

- All N decoded mono OPUS streams routed through a GStreamer `interleave` element into a single N-channel buffer.
- Single multichannel resampler (`audioresample` with SOXR backend, or direct `gst-soxr`) operates on the interleaved buffer with one shared ratio. This guarantees phase coherence across all channels and exploits SIMD throughput.
- `deinterleave` after resampling, back to N mono streams routed to outputs.
- Phase coherence comes from the shared ratio source, not from SOXR specifically; the multichannel structure is a throughput optimization.

**Adaptive mode (default):**

- Hybrid clock-recovery loop:
  - **Frequency lock (primary)**: long-window measurement of sender timestamps (from SRT buffer PTS or, fallback, decoded-sample-count over time) versus local Dante audio clock. Window starts ~10 s for fast initial convergence, lengthens to ~5 min in steady state. Output: estimated sender/receiver frequency ratio in ppm.
  - **Phase trim (secondary)**: slow PI controller on jitter buffer occupancy, very low gain, time constant 30+ seconds. Adds a small correction to the frequency-lock ratio to keep buffer occupancy at its target. Effectively zero in steady state; absorbs thermal drift and rare frequency-lock hiccups.
  - **Slip-buffer fallback**: for catastrophic stalls only (multi-second link loss). Logs each slip event with a sample-accurate timestamp.
- Ratio output clamped to ±50 ppm.
- Audibly transparent. Sub-sample drift relative to wall clock accumulates as resampling adjustments — handled transparently by any downstream lip-sync corrector in a video chain.
- Cold-start convergence: ~10–30 s. Wider buffer occupancy target during convergence, narrowing once locked.
- UI exposes a "clock locking → locked" indicator, green when frequency estimate is stable within ±1 ppm for 30 s.

**Free-running fixed-buffer mode:**

- No resampling, no clock recovery. Resampler element passes through 1:1.
- Fixed jitter buffer depth, configurable.
- When sender outruns receiver, buffer overflows and a chunk is dropped; when receiver outruns sender, buffer underruns and silence is inserted.
- Glitch interval is predictable from crystal mismatch (typ. every 30–90 min at ±20 ppm).
- Sample-accurate timing preserved between glitches. Each glitch logged with a sample-accurate timestamp for downstream re-alignment.
- Use case: workflows that forbid micro-resampling and have discrete re-sync points downstream (file-based, segment boundaries).

**Mode selection guidance (in customer docs):**

- Adaptive for almost everyone. Audibly invisible. Compatible with continuous live downstream chains.
- Free-running for workflows with explicit glitch-tolerant re-sync points.
- Sample-accurate sync across the bridge is **not possible** on open-internet point-to-point; that requires a shared timing reference (PTP grandmaster, GPS-disciplined references, or equivalent), which by definition means both sites are on a single logical network and should route Dante natively over a layer-2 transport rather than encode/decode through this bridge.

**Implementation status (as of multichannel-opus pre-commit):**

- Spine playback chain instantiates one `queue name=rx_clkbuf_K` per output channel between `interaudiosrc` and `audiomixer.sink_K`. This is the post-bridge clock-recovery reservoir; its `max-size-time` is the configured jitter-buffer depth, and `current-level-time` is the observable for the adaptive loop. Default depth at spine build time: 60 ms.
- Diagnostic surface: `GET /api/diagnostics/rx-clock-buffers` reports `max-size-time / current-level-time / current-level-buffers` per channel. Used to verify per-transport overrides land.
- Free-running mode is wired end-to-end: `SrtTransportConfig.clock_recovery_mode = free_running` + `free_running_clock.jitter_buffer_ms` → resolved at RX attach in `media.py` → pushed onto the spine queue via `CtypesManagedPipeline.set_queue_max_size_time`. Per-transport override falls back to `program.clock_recovery_mode` / `program.free_running_clock` when unset.
- Adaptive mode currently does **nothing** in the pipeline. `AdaptiveClockConfig` (convergence window, lock PPM threshold, lock hold, ratio clamp) is declared in `config.py` but not yet wired. RX legs in adaptive mode currently fall through to the default 60 ms queue depth — i.e. equivalent to a 60 ms free-run today.

**Remaining work to honor the documented adaptive design:**

1. **Per-transport adaptive surface.** Add `adaptive_clock: AdaptiveClockConfig | None` override to `SrtTransportConfig` (mirrors the existing per-transport `free_running_clock` field). Add an `initial_buffer_ms` field to `AdaptiveClockConfig` so adaptive mode has an explicit starting depth applied to `rx_clkbuf_K.max-size-time` at attach time, the same way free-running's `jitter_buffer_ms` is.
2. **Rate-slewing element insertion.** Insert one `audioresample` per channel between `interaudiosrc` and the `rx_clkbuf_K` queue (or move the whole chain through a single multichannel resampler post-interleave, per the "Resampler structure" section above — TBD by the spine-shape work). The resample ratio is the control-loop output.
3. **Adaptive control loop.** Periodic task (sub-second tick) per active RX leg:
   - Read `rx_clkbuf_K.current-level-time` and tail of underrun events.
   - Frequency-lock estimator: integrate fill drift over the configured `convergence_window_seconds` to derive ppm offset.
   - Phase-trim PI: low-gain correction toward a target depth (typ. half `initial_buffer_ms`).
   - Output ratio clamped to `±ratio_clamp_ppm`; written to the per-channel `audioresample` via `g_object_set` on a runtime-settable property (likely needs the `audioresample` ratio surfaced as a custom property or driven via `gst_segment` math — confirm during impl).
   - Lock state emitted to telemetry (`telemetry.observe_clock(lock_state=...)`); UI's existing "locking → locked" indicator already consumes it.
4. **Telemetry.** Expose per-RX-channel `buffer_fill_ms`, `estimated_ppm_offset`, and `slip_count` on the existing status snapshot. The diagnostic endpoint added in step 1 (`/api/diagnostics/rx-clock-buffers`) becomes the per-channel-fill source; the ppm/slip data is loop-side bookkeeping.

**Test plan (low-vs-high-latency sweep):** today, switch the transport to `free_running` and sweep `jitter_buffer_ms`. End-to-end wiring is validated. Once step 1 lands, the same sweep works under `adaptive` mode via `initial_buffer_ms`.

**Clock-related telemetry surface (lands with queue-fill work, regardless of mode):**

Per output channel (driven off the existing `rx_clkbuf_K` queues):

- `buffer_fill_ms` — `current-level-time / 1e6`. Sparkline source.
- `buffer_max_ms` — `max-size-time / 1e6`. Sparkline Y-scale and warning threshold reference.
- `overrun_count` — count of `overrun` GstSignal emissions on `rx_clkbuf_K`. Reset on RX-leg start. The single number that says "free-run buffer is too tight" before the operator hears a click.
- `underrun_count` — count of `underrun` GstSignal emissions on `rx_clkbuf_K`. Symmetric to overrun.
- `recent_slips` — bounded ring (last ~50) of `(timestamp, kind: overrun|underrun)`. Drives a "recent slips" panel; lets an operator answer "what happened at HH:MM?" without scraping logs.

Per RX leg (derived in `telemetry.py` from the above + decoder hooks):

- `estimated_drift_ppm` — rolling integration of fill-level slope (e.g. last 60 s), converted to ppm. Sign indicates direction (positive = remote faster than local). Useful even in free-run as an operator forecast ("+4 ppm sustained → next overrun in ~70 min at current buffer").
- `opus_plc_count` — opusdec PLC event count. Distinct from queue under/overruns: PLC = "we missed an Opus frame upstream"; queue slip = "the buffer policy gave up." Both can happen independently and operators benefit from seeing the distinction.

Adaptive-mode-only (lands with the rate-slewing element + control loop):

- `lock_state` — `unlocked | converging | locked`. Already plumbed through `telemetry.observe_clock(lock_state=...)`; just needs the loop to drive it.
- `applied_ratio_ppm` — current resample ratio in ppm. Confirms the loop is acting and quantifies how much rate offset it's compensating for.

System-wide (single value on `/api/status`):

- `asiosrc_measured_rate_hz` — samples-delivered / wall-elapsed over a rolling window. Nominally 48000.000. Deviations flag local PTP / DVS clock health independent of any RX leg.

Explicitly skipped (record so we don't relitigate):

- PTP step-correction detection. Dante PTP slews; step corrections are exceptional. Defer until evidence demands it.
- End-to-end one-way latency. Requires a shared timing reference between sites; we don't have one. Don't expose a number that silently drifts with the clocks.

UI shape: per RX leg, a buffer sparkline next to the existing SRT stats (rtt / loss / bitrate), with a drift-ppm badge and overrun/underrun counters underneath. Sparkline color thresholds drive the operator's visual scan:

- Green: fill near 0, no recent slips.
- Yellow: sustained fill above ~50% of `buffer_max_ms` (drifting), or `estimated_drift_ppm` magnitude > 10.
- Red: fill above ~90% of `buffer_max_ms`, or any slip in the last sample window.

## Talkback path (WebRTC + OPUS)

- Bidirectional, peer-to-peer, one OPUS stream per direction.
- Short OPUS frames (10–20 ms) with aggressive in-band FEC; OPUS "restricted-lowdelay" mode available as an option.
- Mono only.
- Browser-based talkback UI: operator can press-to-talk or latch from the local browser's mic; remote talkback feeds an output channel selectable in the routing matrix.
- DTLS-SRTP encryption mandatory and not exposed.
- Free-running clock — no Dante PTP slaving on this path. Relies on GStreamer `rtpjitterbuffer` adaptive sizing and PLC for jitter and loss handling. Asymmetry with the program path is intentional.
- ICE configuration: list of public STUN servers (Google, Cloudflare) plus optional user-configured TURN server (host, port, credentials). No bundled TURN.
- Independent on/off from program path; either can run without the other.

## Signaling

- Direct WebSocket between endpoints, hosted on the same FastAPI service. No third-party signaling server.
- Used for: WebRTC SDP exchange, ICE candidate trickle, remote control commands, status streaming, key/config negotiation during pairing.
- Authenticated with bearer token established during pairing.
- Reconnects automatically with exponential backoff; reports link state in UI.
- Fallback "manual SDP" mode for diagnostics: copy/paste WebRTC offer and answer between two browser tabs when signaling WS is unavailable. Vanilla ICE (full gather before SDP copy).

## Standalone / diagnostic features

These work on a single endpoint with no peer connected, and remain available while a peer is connected.

- **Tone generator**: configurable frequency, level, target output channel(s). Sine, pink noise, white noise, sweep.
- **Interface listen / monitor**: route any input or output channel to the operator's browser audio (local-only WebRTC from endpoint to browser) for headphone check.
- **Meters**: per-channel peak + RMS for all inputs and outputs at 30 Hz update over WebSocket.
- **Loopback test**: route specified input channels to specified output channels through the full encode/decode chain locally, validating pipeline integrity without a peer.
- **Round-trip test** (when peer connected): send a known stimulus, measure round-trip latency and any artifacts on return.
- **Link diagnostics**: continuous display of SRT RTT, RTT variance, packets lost, packets retransmitted, send/receive bitrate, buffer occupancy; WebRTC RTT, jitter, packet loss, current bitrate, ICE state.
- **Clock-recovery telemetry** (Adaptive mode): frequency-lock ratio (ppm), phase-trim contribution (ppm), buffer occupancy (ms), occupancy bias from target (ms), slip event counter, time since last slip, lock state.
- **Clock-recovery telemetry** (Free-running mode): glitch event counter, glitch interval (rolling average and last), accumulated offset since stream start, buffer occupancy.

## Pairing and setup

- One endpoint generates a "pairing bundle" — a single copyable blob (or QR) containing: signaling URL, bearer token, SRT passphrase, suggested SRT mode/port. Other endpoint pastes it.
- Bundles are single-use and time-limited.
- "Test connection" button hits peer health endpoint, reports cert fingerprint, software version, and clock skew before any audio is configured.
- Re-pair flow rotates all secrets atomically.

## Encryption and security

- Web UI served over HTTPS. First-run self-signed cert auto-generated; UI exposes paths for Let's Encrypt (DNS-01) and customer-provided cert. Tailscale/WireGuard-tunnel deployment documented as supported.
- Single admin bearer token for the API, generated on first run, shown once, stored hashed.
- Per-user tokens supported but not required.
- All secrets (SRT passphrase, API tokens, TURN credentials) regeneratable from UI.
- Logs redact passphrases, tokens, and full SDPs by default.
- Config file on disk readable only by service account.
- Documented threat model: WAN leg is encrypted (SRT AES + WebRTC DTLS-SRTP); LAN-side Dante is unencrypted by Dante's design.

## Configuration and persistence

- One config file per endpoint (TOML or YAML), human-readable, edits via UI write atomically.
- Configuration scope:
  - Network: NIC selection per role, WAN public address/port hints, STUN/TURN servers.
  - Audio: interface selection, channel count, channel labels, routing matrix.
  - Program: SRT mode, port, latency, encryption parameters, OPUS parameters per stream, bandwidth cap, **clock-recovery mode (Adaptive / Free-running) and per-mode parameters**.
  - Talkback: enabled, OPUS parameters, channel assignments.
  - Pairing: peer signaling URL, bearer token, passphrases.
  - UI: theme, meter ballistics, units.
- Import/export of full configuration (with secrets redacted or included, operator's choice) for backup and cloning.
- Config schema versioned; migrations on upgrade.

## Monitoring and telemetry

- Live status panel: link up/down, SRT and WebRTC stats, per-channel signal presence, CPU, memory, NIC throughput per interface, clock-recovery state.
- Event log in UI: link state changes, errors, configuration changes, pairing events, slip/glitch events with timestamps.
- Optional Prometheus metrics endpoint for ops integration.
- No outbound telemetry by default.

## Logging

- Structured logs (JSON) at the service, plain-text view in UI.
- Log levels per subsystem (gstreamer, srt, webrtc, clock-recovery, control, signaling).
- Rotating file logs with retention cap.
- "Bundle support package" button: collects last N hours of logs, redacted config, and live diagnostics into a single downloadable archive.

## UI shape

- Symmetric two-pane layout: "this endpoint" (the one serving the page) on one side, "remote endpoint" on the other, swap button to flip perspective.
- Top-level views:
  - **Dashboard**: link health, key metrics, talkback PTT, clock-recovery state.
  - **Routing**: matrix view of input → encoder → stream and stream → output assignments.
  - **Program**: SRT and OPUS parameters per stream, aggregate bandwidth, encryption, clock-recovery mode and parameters.
  - **Talkback**: WebRTC parameters, mic/output assignment, ICE/TURN config.
  - **Diagnostics**: tone gen, listen, loopback, round-trip, meters, raw stats, clock-recovery telemetry.
  - **System**: network, audio interface, certs, tokens, software update, logs, support bundle.
- Operates correctly with one endpoint connected (standalone mode) and gracefully degrades when peer is unreachable.
- Clock-recovery mode is surfaced prominently on the Program view — never buried — because choosing it wrong is a workflow-breaking error.
- Dark theme default; meters and status indicators readable in dim control rooms.

## Platform support

- Primary: Windows.
- Secondary: macOS.
- Tertiary: Linux (PCIe-only, no DVS).
- Single signed installer per platform; service auto-starts on boot; UI accessible immediately.

## Non-goals (explicit)

- Many-to-many or broadcast-style topologies. Strictly point-to-point.
- Video.
- SIP / PSTN integration.
- SFU / cloud relay infrastructure for media.
- Sample rates other than 48 kHz.
- AES67 / ST 2110 native interop on the WAN side. AES67 may be considered later for LAN-side ingestion as an alternative to Dante.
- Operator-to-operator chat or text messaging in the web UI.
- Mobile-native apps. The web UI must be tablet-usable; no native iOS/Android.
- **Sample-accurate timing across the bridge.** Customers requiring this must use a layer-2 transport between facilities and route Dante natively, not encode/decode through this bridge.
- **Locked / shared-reference clock mode.** The deployment scenarios where this would be useful are scenarios where the bridge itself is unnecessary.

## Open questions to resolve before build

- Cert UX default: ship self-signed and accept browser warning, or require a setup-wizard step that produces a real cert via mkcert / Let's Encrypt? Affects out-of-box experience materially.
- Channel-count ceiling for v1: aim for full 64×64 from day one, or cap at 16×16 to simplify pipeline construction and DSP load testing? Architecture is the same; testing burden is not.
- Update mechanism: in-app updater (signed bundles fetched from a URL the operator configures), or rely on platform installer reruns?
- Crash recovery semantics: on media-pipeline crash, auto-restart silently or surface as an error and require operator ack?
- Free-running mode glitch policy: insert silence on underrun, or repeat last sample? Repeat-last has a click; silence has a pop. Default and configurability TBD.
