# Single-Pipeline TX Plan

## Problem

Each TX SRT transport is currently spawned as its own `gst-launch-1.0` subprocess (see [`media.py`](../app/services/media.py)'s `_spawn_pipeline`, fed by `MediaGraphBuilder._build_tx_argv` in [`media_graph.py`](../app/services/media_graph.py)). Every TX subprocess opens its own `asiosrc` (or `wasapisrc`) against DVS. ASIO is single-client by spec; the second TX subprocess fails to acquire DVS and the pipeline crashes on preroll.

The JACK plan (see [`jack-audio-engine-plan.md`](jack-audio-engine-plan.md)) was an attempt to solve this by funneling all GStreamer clients through a JACK server that owns DVS once. That path is on hold — see the status note in that doc.

This plan solves the same problem with no new runtime infrastructure by collapsing all of an endpoint's TX legs into one GStreamer subprocess that opens DVS exactly once.

## Approach

One GStreamer subprocess per endpoint. Inside that one process:

```text
asiosrc (single, shared)
  -> audioconvert -> audioresample -> deinterleave name=dante_in_shared

dante_in_shared.src_N -> queue -> level (per-channel meter) -> tee -> branch into each TX leg

per TX transport:
  branches -> interleave -> caps(channel-mask=0x0) -> audioconvert -> opusenc -> mpegtsmux -> srtsink (per-transport URI)
```

`_build_tx_argv` already constructs this exact shape for a single transport with multiple channels. The change is to lift the construction one level up: build per-endpoint instead of per-transport, and let the shared deinterleave feed every transport's per-channel branches.

RX is unchanged. RX pipelines today terminate at `fakesink` ([`media_graph.py:425`](../app/services/media_graph.py:425)) — they never touch DVS, so they neither cause nor benefit from this refactor.

## Code-level changes

### `MediaGraphBuilder` (`app/services/media_graph.py`)

- Add `plan_endpoint_tx(config)` that returns one argv + one graph + the list of per-transport monitor taps and srt element names. It iterates `config.srt_transports`, filters TX-direction, validates each, and emits one combined argv.
- Keep `plan_srt_transport(...)` for RX. The existing TX path through `plan_srt_transport` becomes a thin wrapper that returns "see endpoint TX bundle" or is removed once callers are migrated.
- Element naming already uses transport- and group-scoped suffixes (`_srt_element_name`, `_interleave_element_name`, `_deinterleave_element_name`, `_meter_element_name`, `_tx_monitor_tap_name`). No clashes when multiple transports share one process — verify with a test.
- The shared dante capture node previously emitted per encode-group becomes emitted once per endpoint. `_dante_capture_plan` needs to take the union of every TX leg's requested Dante channels, not just one group's, so the deinterleave pad map covers every consumer.

### `MediaService` (`app/services/media.py`)

- Replace the per-TX-transport `_spawn_pipeline` calls with a single per-endpoint TX subprocess spawn. Lifecycle becomes:
  - Start the TX bundle when at least one TX transport is enabled and config is valid.
  - Stop the TX bundle when no TX transport is enabled.
  - Restart on config change (sources, encode groups, channel maps, audio driver, URIs, bitrate).
- Track the bundle as a single `ManagedPipeline` keyed by endpoint id, not per transport.
- Per-transport telemetry continues to read named elements (`srtstats_tx_<id>`, `dbmeter_out_<group>_<channel>`, etc.) from the same `gst-launch` stdout/`GstBus` stream. The element-name → transport-id mapping already exists at the planner level — pass it through to the telemetry layer.

### API surface (`app/api/routes.py`)

- "Start TX <transport>" and "Stop TX <transport>" become flips of a per-transport `enabled` flag that, in aggregate, drive the bundle lifecycle. The route handlers should keep their current per-transport semantics from the operator's perspective; the bundle is an internal detail.

### Tests

- Add a test that an endpoint with two TX transports plans **one** argv containing one `asiosrc` and two `srtsink`s with distinct URIs and distinct element names.
- Add a test that a config with zero enabled TX transports plans no TX subprocess.
- Adjust any existing test asserting per-transport argv shape to assert the new endpoint-bundle shape.

## Failure isolation tradeoff

In the current per-transport-subprocess design, one TX leg crashing leaves the others running. In the bundle design, an unrecoverable error in any element (`opusenc` segfault, `srtsink` panic, etc.) takes down all TX legs for that endpoint.

This is the same tradeoff JACK was supposed to win back via independent GStreamer clients. We are accepting it for v1 because:

- The status-quo "one crash per leg" benefit is conditional on the legs being able to coexist in the first place, which they can't with DVS.
- Per-element fault tolerance can be added later (`fallbackswitch`, dynamic pad remove, or moving back to a JACK design once DVS↔PortAudio compatibility is resolved) without breaking the bundle's external API.

The supervisor should restart the bundle on subprocess exit, with a short backoff, and emit a clear event so the UI can mark all of that endpoint's TX legs as `restarting` rather than silently down.

## Out of scope for v1

- **RX → Dante Output playback.** Today's RX legs go to `fakesink`. Adding `asiosink` for RX-to-Dante is a separate change. When it lands, it joins the same per-endpoint bundle so DVS is still opened exactly once across both directions.
- **Dynamic add/remove of a TX leg without rebuilding.** v1 rebuilds the bundle on any TX-affecting config change. Dynamic pad add/remove can come later if operator UX demands it.
- **Multiple endpoints sharing DVS within one process.** If/when more than one endpoint is wired up on the same host, they remain separate bundles — DVS will then need either a JACK-style multiplexer or a single super-bundle. Out of scope until the product needs it.

## Open questions

- WASAPI / non-ASIO drivers do not have the single-client constraint. Should the planner still bundle when `audio.interface_driver != "asio"`? Bundling everywhere keeps the code paths uniform but pays the failure-isolation cost unnecessarily. Suggest: bundle only when the driver requires it (`asio` today, anything else added later). Decide before implementation.
- Buffer-size matching across legs. `asiosrc` exposes one upstream block size; per-leg `opusenc` rates can still differ, but verify that `audioresample` is sufficient between the shared deinterleave and per-leg interleave for mixed sample-rate target streams (none today, but planned).
- Telemetry attribution under unified bus messages. The `gst-launch -m` stdout already carries element names in each message; confirm the telemetry parser keys on element name (not on subprocess identity) before relying on it.
