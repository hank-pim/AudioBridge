# Multichannel Opus via family=255 / MPEG-TS over SRT — Implementation Plan

## Why

The current code path (`opusenc → rtpopuspay`) forces `channel-mapping-family=1` for N>2 channels, which assumes a Vorbis/SMPTE surround layout. For encode groups of 6/7/8 channels, the LFE slot is heavily lowpassed (~120 Hz) by `opusenc` — corrupting one channel for users routing discrete mono feeds (mics, talkback buses, etc.). Production code has additionally never worked at N>2 due to three latent bugs (see "Production bugs found" below).

`channel-mapping-family=255` is Opus's "discrete channels, no layout" mode — no LFE treatment, no surround assumptions, supports up to 255 channels. RTP's stock GStreamer payloader (`rtpopuspay`) rejects family=255 caps; both Ogg/Opus and MPEG-TS/Opus can carry it. The production SRT path uses MPEG-TS because TS repeats stream metadata and allows receivers to join a running stream after startup; raw Ogg headers are one-shot and can be missed when `srtsink wait-for-connection=false` is used to keep the always-on DVS spine from stalling.

Verified in loopback ([scripts/opus_8ch_loopback.ps1](../scripts/opus_8ch_loopback.ps1)): 8 discrete channels round-trip cleanly with no LFE corruption, latency parity with the RTP path.

Verified on DVS hardware ([scripts/opus_2ch_dvs_loopback.ps1](../scripts/opus_2ch_dvs_loopback.ps1)): DVS input channels 1/2 round-trip through `asiosrc -> opusenc -> oggmux -> SRT localhost -> oggdemux -> opusdec -> asiosink` and are audible on DVS output channels 1/2 in one full-duplex GStreamer process. The app production path now uses `mpegtsmux/tsdemux` for late-join safety.

## Production bugs the current code has (all become moot under this plan)

1. **Wrong 7.1 mask** at [media_graph.py:1295](../app/services/media_graph.py): `_channel_mask_hex(8) = 0x63F` (LFE2 + SL); correct 7.1 is `0xC3F` (SL + SR). Per-channel bit list at [media_graph.py:1312](../app/services/media_graph.py) has the same swap.
2. **Missing `audioconvert`** between `interleave` and `opusenc` at [media_graph.py:281](../app/services/media_graph.py) — needed for caps fixity at N>2.
3. **Wrong RX caps** at [media_graph.py:809](../app/services/media_graph.py) — hard-codes `encoding-name=OPUS, encoding-params=(string)1`; multichannel needs `MULTIOPUS` plus `num_streams`, `coupled_streams`, `channel_mapping`.

All three are obsolete the moment we move off `rtpopuspay`/`rtpopusdepay`.

## Pipeline changes

### TX leg ([media_graph.py:246-384](../app/services/media_graph.py) `_build_tx_argv`)

Before:
```
interleave → caps(channels=N, mask=<surround-layout>) → opusenc → rtpopuspay → srtsink
```

After:
```
interleave → caps(channels=N, mask=0) → audioconvert → opusenc → mpegtsmux(alignment=7, pat/pmt interval=900 ticks) → srtsink
```

Per-channel source legs also use `channel-mask=(bitmask)0x0` instead of position bits.

### RX leg ([media_graph.py:776-875](../app/services/media_graph.py) `plan_rx_leg_branch`)

Before:
```
srtsrc → application/x-rtp,encoding-name=OPUS,... → rtpjitterbuffer → rtpopusdepay → opusdec → deinterleave
```

After:
```
srtsrc → tsdemux → opusdec → audio/x-raw,channels=N → deinterleave
```

**⚠️ CRITICAL RX CAPS CONSTRAINT**:
Do **not** apply `channel-mask=(bitmask)0x0` to the RX legs downstream of `opusdec` or the shared RX spine. The `tx_ic` leg strips channel masks easily, but `opusdec` dynamically restores standard pseudo-stereo/surround multichannel layouts upon decode. Forcing `0x0` on the RX spine causes `audioconvert` to fail its format negotiation (`reason not-negotiated (-4)`), which bubbles upstream and causes the demuxer to throw a fatal delayed-linking error. RX caps should tightly define `format`, `rate`, and `channels` and let GStreamer handle the spatial mask downstream naturally.

The `tee` for monitor taps sits between `tsdemux` and `opusdec` (or stays where it is — same allow-not-linked semantics either way).

### DVS playback/output spine findings

The working 2-channel DVS hardware smoke test uses a static output path:

```
rx_d.src_0 -> queue -> level rx_lvl0 -> audioconvert -> mono caps -> spine_out.sink_0
rx_d.src_1 -> queue -> level rx_lvl1 -> audioconvert -> mono caps -> spine_out.sink_1
interleave name=spine_out -> level out_lvl -> audioconvert -> audioresample -> audio/x-raw,rate=48000,channels=2,layout=interleaved,channel-mask=(bitmask)0x0 -> asiosink
```

Two details from hardware testing are load-bearing:

- Do **not** force `format=S16LE` on the final caps immediately before `asiosink`. DVS negotiated `S24LE` on the test host; leaving `format` open lets `audioconvert` satisfy the ASIO driver.
- The static smoke test should route decoded mono channels directly into the output `interleave`. A prior version routed each decoded channel through per-channel `audiomixer` elements before `interleave`; `rx_lvl*` meters showed decoded audio, but Dante Controller showed no DVS output and no audio was heard. The direct `rx_d -> interleave -> asiosink` path is the confirmed working model.

This does not automatically forbid production's dynamic mixer/adder spine, because the app needs attach/detach behavior that the static smoke test does not. It does mean the production playback spine must be validated with a post-interleave `level` element immediately before `asiosink` **and** with Dante Controller/audio output, not only with pre-mixer or post-decode meters. If the dynamic mixer path still fails to drive DVS, use the direct interleave model as the baseline and reintroduce dynamic fan-in only after proving it preserves actual DVS output.

### Validation ([media_graph.py:90-105](../app/services/media_graph.py) `_validate_transport`)

- Raise channel cap from **8 → 32**.
- Rename error code `opus_multichannel_max_8` → `opus_channels_exceeds_limit`.
- Update error message to point at the new limit.

### Helpers to delete

- `_channel_mask_hex` ([media_graph.py:1284](../app/services/media_graph.py))
- `_channel_mask_bit_hex` ([media_graph.py:1300](../app/services/media_graph.py))

Both only existed to coerce opusenc into family=1 surround layouts. Unused under family=255.

## Clock control & ASIO Sync

### ASIO Master Clock Conflict 
When implementing the combined `asiosrc` and `asiosink` loops on a single Dante Virtual Soundcard device, **the `asiosink` must be given `provide-clock=false` and `sync=false`**. Both `asiosrc` and `asiosink` default to `provide-clock=true`. If both endpoints query the same DVS driver attempting to become the master pipeline clock, it initiates an unrecoverable dual-clocking loop preventing the pipeline from advancing to `PAUSED`/`PLAYING`.
Similarly, long-running single-process pipelines heavily rely on branch isolation buffers. `srtsink`, `srtsrc`, and `asiosink` branches must explicitly implement `async=false` or state changes will hang asynchronously waiting for data across the `deinterleave` loops.

The audible 2-channel DVS smoke test kept `asiosink provide-clock=false sync=false async=false`, so those flags are compatible with real DVS playback when the decoded channels feed the output `interleave` directly.

### DVS Zombie Locks
ASIO endpoints effectively enforce single-client constraints. If a pipeline crashes or isn't successfully torn down, that DVS instance falls into a locked state and yields `Failed to init IASIO instance` on the next run until forcibly bounced. The supervisor implementation managing the GST pipeline subprocesses must definitively tear down processes rather than relying on GStreamer error bus recovery if it fails to bind.

The two-Dante-LAN clock-bridging mechanism in [planv2.md:44-58](../planv2.md) is **downstream of the decoder and operates on PCM**. It is unaffected by the codec/framing choice. What matters for the clock-recovery path:

| Requirement | RTP/Opus path | MPEG-TS/Opus path |
|---|---|---|
| Regular PCM frame cadence from decoder | Yes (Opus 20 ms frames) | Yes (same Opus frames) |
| Coherent buffer timestamps | Yes | Yes |
| No mid-stream sample drops/dupes | Yes | Yes — tsdemux passes packets through |
| Network jitter absorption | SRT `latency_ms` + `rtpjitterbuffer` | SRT `latency_ms` only |

The load-bearing jitter buffer in this app is **SRT's**, not the RTP one — confirmed by the existing config flow ([media_graph.py:801](../app/services/media_graph.py)). Dropping `rtpjitterbuffer` removes a second-order buffer; SRT's `latency_ms` continues to do the real work.

**Latency**: verified equivalent in standalone loopback ([scripts/opus_latency_compare.ps1](../scripts/opus_latency_compare.ps1)) — the Opus frame cadence is unchanged, and MPEG-TS adds only lightweight framing while preserving SRT as the load-bearing jitter buffer.

## Tests

### Tests to update ([tests/test_diagnostics_media_routes.py](../tests/test_diagnostics_media_routes.py))

Assertions matching the following strings need rewriting against the new pipeline:
- `rtpopuspay`, `rtpopusdepay`, `rtpjitterbuffer`
- `application/x-rtp,...,encoding-name=OPUS,...`
- `channel-mask=(bitmask)0x` (anything except `0x0`)
- `opus_multichannel_max_8`

Replacement assertions:
- `mpegtsmux alignment=7 pat-interval=900 pmt-interval=900`, `tsdemux`
- Input caps with `channel-mask=(bitmask)0x0`
- 8-channel happy-path test (was previously impossible)
- 32-channel boundary test (validate cap)
- 33-channel failure test (new error code)

### New end-to-end test

Port [scripts/opus_8ch_loopback.ps1](../scripts/opus_8ch_loopback.ps1) to Python + pytest, marked `@pytest.mark.requires_gstreamer` so it skips in environments without `gst-launch-1.0`. Tests TX→RX round-trip on a local SRT loop with N=8 discrete tones at distinct levels; asserts per-channel RMS in tolerance and no channel collapse.

Keep [scripts/opus_2ch_dvs_loopback.ps1](../scripts/opus_2ch_dvs_loopback.ps1) as the manual hardware smoke test for Windows + DVS. Pass criteria:

- Pipeline reaches PLAYING in one process with both `asiosrc` and `asiosink` open against DVS.
- `tx_lvl0/1`, `rx_lvl0/1`, and `out_lvl` all show program-level audio.
- Dante Controller shows output activity on DVS output channels 1/2.
- Audio is audible on a known-good DVS output route.

## Documentation

Update [planv2.md:28](../planv2.md) to reflect:
- Single-encoder choice retained (still one `opusenc` per encode group), but using family=255 (discrete) rather than family=1 (surround).
- Reasoning: family=1's LFE position would corrupt one channel in 6/7/8-channel groups for users routing unrelated mono feeds.
- Trade-off: discrete mode loses stereo intensity coupling (~30% bitrate efficiency for paired-stereo program audio). Acceptable given the bridge primarily carries discrete Dante channels, not pre-mixed surround.

## Sequence of work

1. Implement TX/RX pipeline changes in [media_graph.py](../app/services/media_graph.py).
2. Raise validation cap to 32, update error code.
3. Update unit tests in [test_diagnostics_media_routes.py](../tests/test_diagnostics_media_routes.py).
4. Run existing test suite — expect a wide spread of string-match assertion failures, fix each.
5. Delete dead mask helpers.
6. Add the Python pytest port of the 8-channel loopback test.
7. Smoke-test the static DVS hardware model with [scripts/opus_2ch_dvs_loopback.ps1](../scripts/opus_2ch_dvs_loopback.ps1), confirming both `out_lvl` and Dante Controller output activity.
8. Revalidate the production dynamic playback spine against that static model. If mixer/adder fan-in is used, prove it drives DVS output, not just internal GStreamer meters.
9. Smoke-test on actual Dante hardware (single endpoint, then full bridge between two Dante LANs).
10. Update [planv2.md](../planv2.md) and any references in [docs/](.) that mention rtpopuspay/MULTIOPUS/family=1.

## Clock-recovery wiring carried in alongside this work

The multichannel-opus refactor lands at the same time as the spine's per-channel `rx_clkbuf_K` queue (post-bridge clock-recovery reservoir) and the per-transport `clock_recovery_mode` / `free_running_clock` override path. Both modes are exercised by the loopback and Dante smoke tests above. Status carried forward into this commit:

- **Free-running mode**: fully wired. Per-RX-transport `jitter_buffer_ms` lands at `rx_clkbuf_K.max-size-time` via `CtypesManagedPipeline.set_queue_max_size_time` at attach time. Verified by `GET /api/diagnostics/rx-clock-buffers`.
- **Adaptive mode**: schema present (`AdaptiveClockConfig` in `config.py`), pipeline scaffolding present (queue exists, fill is observable), but no rate-slewing element and no control loop yet. RX legs in adaptive mode currently behave as a fixed 60 ms free-run.
- **Per-transport adaptive override**: not yet — `adaptive_clock` is program-only today. Add `adaptive_clock: AdaptiveClockConfig | None` to `SrtTransportConfig` and an `initial_buffer_ms` field to `AdaptiveClockConfig` so adaptive mode has an explicit, per-transport starting depth applied to `rx_clkbuf_K` at attach time (same code path as free-running).
- **Rate-slewing**: deferred to its own workstream after the multichannel-opus commit. Inserts `audioresample` per channel (or one multichannel resampler post-interleave per the planv2 "Resampler structure" note), driven by a per-RX-leg control loop that watches `rx_clkbuf_K.current-level-time` and emits a ratio clamped to `±ratio_clamp_ppm`. Lock state pipes to existing `telemetry.observe_clock(lock_state=...)`.

Tests added during multichannel-opus work should assert: (a) the queue is named `rx_clkbuf_K` and is present per output channel, (b) `max-size-time` matches the resolved per-transport override at attach time, and (c) `/api/diagnostics/rx-clock-buffers` reports the expected depths for a fixture with mixed free-running and adaptive RX transports.

**Telemetry surface added in this commit alongside the queue (drives operator-facing UI for both modes):**

- Per output channel on `/api/status`: `buffer_fill_ms`, `buffer_max_ms`, `overrun_count`, `underrun_count`, `recent_slips[]`. Subscribe to the queue's `overrun` / `underrun` GstSignals at RX attach in `CtypesManagedPipeline` and increment counters; the ring of recent slip events lives in `telemetry.py` next to the existing meter observations.
- Per RX leg: `estimated_drift_ppm` (rolling fill-slope integration in `telemetry.py`) and `opus_plc_count` (hooked off opusdec's PLC accounting).
- System-wide: `asiosrc_measured_rate_hz` from samples-delivered / wall-elapsed.

UI sparkline + color thresholds are detailed in [planv2.md](../planv2.md) under "Clock-related telemetry surface". The full adaptive-only fields (`lock_state`, `applied_ratio_ppm`) wait for the rate-slewing workstream; the field names are reserved so they can land without a snapshot-shape change.

## Out of scope (deferred)

- Per-encode-group "discrete vs paired" toggle for bitrate efficiency on stereo-program use cases. Add later if bandwidth becomes a practical concern.
- WebRTC transport plumbing — separate work, but this plan keeps the door open (we control both ends, so the wire format can carry family=255 Opus in whatever framing we choose).
