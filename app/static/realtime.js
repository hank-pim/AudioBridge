// Live API adapter — replaces the static mock. Fetches /api/config and
// /api/status, then keeps a WebSocket on /api/ws/status and an SSE on
// /api/logs/stream. Exposes window.AB in the same shape the prototype
// expected, plus AB.subscribe(fn) so the React tree can re-render.
//
// The backend telemetry is shallower than the prototype mock — the API
// has no per-channel jitter/loss/lat/buffer or per-channel codec, and
// no temperature, no NIC IPs, no SLO. We map what exists and leave the
// rest as null/"—" so the UI dims those cells rather than fabricating.

(function (g) {
  const subs = new Set();
  const HISTORY = 60;

  const store = {
    PEER:    { self: { name: "endpoint", addr: "—" }, peer: { name: "—", addr: "—" }, version: "0.1.0" },
    PROGRAM: { state: "stopped", transport: "SRT", codec: "—", channels: 0, srt_mode: "listener", srt_port: 9000,
               bitrate_kbps: 0, rtt_ms: 0, jitter_ms: 0, loss_pct: 0, buffer_ms: 0, buffer_target_ms: 0,
               encrypted: true, uptime_s: 0 },
    TALKBACK:{ state: "stopped", transport: "WebRTC", codec: "—", channels: 2,
               rtt_ms: 0, jitter_ms: 0, loss_pct: 0, pli_count: 0, uptime_s: 0 },
    SYS:     { cpu_pct: 0, mem_pct: 0, mem_mb: 0, temp_c: null,
               nic_dante: { name: "—", ip: "—", speed_gbps: null, rx_mbps: 0, tx_mbps: 0 },
               nic_wan:   { name: "—", ip: "—", speed_gbps: null, rx_mbps: 0, tx_mbps: 0 },
               audio_iface: { name: "(no interface selected)", driver: "—", sr: 48000, ch: 0 },
               uptime_s: 0 },
    CLOCK:   { mode: "adaptive", lock_state: "idle", frequency_ratio_ppm: 0,
               phase_trim_ppm: 0, buffer_occupancy_ms: null, slip_events: 0 },
    CHANNELS: [],
    EVENTS:  [],
    SERIES:  { bitrate: [], rtt: [], jitter: [], loss: [], buffer: [],
               cpu: [], mem: [], tb_rtt: [], tb_jitter: [] },
    CONNECTED: { ws: false, sse: false },
    config: null,
    status: null,
  };

  const notify = () => subs.forEach(fn => fn());
  const pushSeries = (key, val) => {
    const s = store.SERIES[key];
    s.push(Number(val) || 0);
    while (s.length > HISTORY) s.shift();
  };

  function buildChannels(cfg, st) {
    const streams = (cfg && cfg.audio && cfg.audio.streams) || [];
    const inputs  = (st && st.meters && st.meters.inputs)  || [];
    const outputs = (st && st.meters && st.meters.outputs) || [];
    const programRunning  = st && st.link && st.link.program  === "running";
    const talkbackRunning = st && st.link && st.link.talkback === "running";
    const programOpus  = (cfg && cfg.program && cfg.program.opus) || {};
    const talkbackBitrate = (cfg && cfg.talkback && cfg.talkback.opus_bitrate_kbps) || 48;

    // Slot assignment per transport. SRT streams ride one shared mono-OPUS
    // multiplex per direction (separate sender/receiver multichannel
    // buffers, planv2.md:42-46). WebRTC streams ride independent OPUS
    // tracks (transceivers) on a single peer connection — track index is
    // analogous, also counted per direction. All counters reset to 1 on
    // each rebuild so they stay compact when streams are reordered or
    // deleted.
    let nextTxSrtSlot = 1, nextRxSrtSlot = 1;
    let nextTxRtcTrack = 1, nextRxRtcTrack = 1;

    return streams.map((s, i) => {
      const dir = s.direction === "tx" ? "out" : "in";
      const dante = s.dante_channel || (i + 1);
      const meter = (dir === "in" ? inputs : outputs)[dante - 1] || { peak_dbfs: -120, rms_dbfs: -120 };
      const level = (meter.rms_dbfs  != null) ? meter.rms_dbfs  : -120;
      const peak  = (meter.peak_dbfs != null) ? meter.peak_dbfs : -120;
      const transport = (s.transport || "srt").toUpperCase() === "WEBRTC" ? "WebRTC" : "SRT";
      const onProgram = transport === "SRT";
      const transportRunning = onProgram ? programRunning : talkbackRunning;
      const enabled = s.enabled !== false;
      const state = !enabled || !transportRunning ? "idle"
                  : (level > -90)                 ? "active"
                  :                                 "muted";
      const opus = s.opus || (onProgram ? programOpus : { bitrate_kbps: talkbackBitrate });
      const bitrate = (opus && opus.bitrate_kbps) || (onProgram ? 96 : talkbackBitrate);
      let srt_slot = null, rtc_track = null, slotLabel;
      if (onProgram) {
        srt_slot = (dir === "out") ? nextTxSrtSlot++ : nextRxSrtSlot++;
        slotLabel = `SRT/${String(srt_slot).padStart(2, "0")}`;
      } else {
        rtc_track = (dir === "out") ? nextTxRtcTrack++ : nextRxRtcTrack++;
        slotLabel = `WRTC/${String(rtc_track).padStart(2, "0")}`;
      }
      const route = dir === "out"
        ? `${slotLabel} ← dante:${String(dante).padStart(2, "0")}`
        : `${slotLabel} → dante:${String(dante).padStart(2, "0")}`;
      return {
        id: i + 1, name: s.name, type: s.type, direction: dir,
        transport,
        dante_channel: dante,
        srt_slot, rtc_track,
        route,
        codec: state === "idle" ? "—" : `OPUS ${bitrate}k`,
        bitrate_kbps: state === "idle" ? 0 : bitrate,
        level_dbfs: level, peak_dbfs: peak, gain_db: 0, state, enabled,
        opus,
        // Per-channel jitter/loss/latency aren't surfaced by the control
        // plane yet; UI renders "—" until they are.
        jitter_ms: null, loss_pct: 0, latency_ms: null, buffer_ms: null,
        sync: state === "idle" ? "off" : "lock", ppm: null,
      };
    });
  }

  function rebuild() {
    const cfg = store.config, st = store.status;
    if (!cfg || !st) return;

    store.PEER = {
      self: { name: cfg.endpoint_name || "endpoint", addr: (cfg.network && cfg.network.public_address) || "—" },
      peer: { name: (cfg.pairing && cfg.pairing.peer_name) || "—",
              addr: (cfg.pairing && cfg.pairing.peer_signaling_url) || "—" },
      version: "0.1.0",
    };

    const sendKbps = (st.srt && st.srt.send_bitrate_kbps)    || 0;
    const recvKbps = (st.srt && st.srt.receive_bitrate_kbps) || 0;
    store.PROGRAM = {
      state: (st.link && st.link.program) || "stopped",
      transport: "SRT",
      codec: `OPUS ${(cfg.program && cfg.program.opus && cfg.program.opus.bitrate_kbps) || 96}k · 48kHz`,
      channels: (cfg.audio && cfg.audio.channel_count) || 0,
      srt_mode: (cfg.program && cfg.program.srt_mode) || "listener",
      srt_port: (cfg.network && cfg.network.srt_port) || 9000,
      bitrate_kbps: sendKbps + recvKbps,
      rtt_ms:    (st.srt && st.srt.rtt_ms)              || 0,
      jitter_ms: (st.srt && st.srt.rtt_variance_ms)     || 0,
      loss_pct:  (st.srt && st.srt.packets_lost)        || 0,
      buffer_ms: (st.srt && st.srt.buffer_occupancy_ms) || 0,
      buffer_target_ms: (cfg.program && cfg.program.srt_latency_ms) || 250,
      encrypted: !!(cfg.program && cfg.program.encryption_enabled),
      uptime_s: st.uptime_seconds || 0,
    };
    store.TALKBACK = {
      state: (st.link && st.link.talkback) || "stopped",
      transport: "WebRTC",
      codec: `OPUS ${(cfg.talkback && cfg.talkback.opus_bitrate_kbps) || 48}k · 48kHz`,
      channels: 2,
      rtt_ms:    (st.webrtc && st.webrtc.rtt_ms)              || 0,
      jitter_ms: (st.webrtc && st.webrtc.jitter_ms)           || 0,
      loss_pct:  (st.webrtc && st.webrtc.packet_loss_percent) || 0,
      pli_count: 0,
      uptime_s: st.uptime_seconds || 0,
    };
    store.SYS = {
      cpu_pct: Math.round((st.system && st.system.cpu_percent) || 0),
      mem_mb: (st.system && st.system.memory_mb) || 0,
      mem_pct: 0,
      temp_c: null,
      nic_dante: {
        name: (cfg.network && cfg.network.dante_nic) || "—",
        ip: "—", speed_gbps: null,
        rx_mbps: +(((st.system && st.system.dante_rx_kbps) || 0) / 1000).toFixed(2),
        tx_mbps: +(((st.system && st.system.dante_tx_kbps) || 0) / 1000).toFixed(2),
      },
      nic_wan: {
        name: (cfg.network && cfg.network.wan_nic) || "—",
        ip: (cfg.network && cfg.network.public_address) || "—", speed_gbps: null,
        rx_mbps: +(((st.system && st.system.wan_rx_kbps) || 0) / 1000).toFixed(2),
        tx_mbps: +(((st.system && st.system.wan_tx_kbps) || 0) / 1000).toFixed(2),
      },
      audio_iface: {
        name: (cfg.audio && cfg.audio.interface_name) || "(no interface selected)",
        driver: (cfg.audio && cfg.audio.interface_driver) || "—",
        sr: (cfg.audio && cfg.audio.sample_rate) || 48000,
        ch: (cfg.audio && cfg.audio.channel_count) || 0,
      },
      uptime_s: st.uptime_seconds || 0,
    };
    store.CLOCK = st.clock || store.CLOCK;
    store.CHANNELS = buildChannels(cfg, st);

    pushSeries("bitrate", store.PROGRAM.bitrate_kbps);
    pushSeries("rtt",     store.PROGRAM.rtt_ms);
    pushSeries("jitter",  store.PROGRAM.jitter_ms);
    pushSeries("loss",    store.PROGRAM.loss_pct);
    pushSeries("buffer",  store.PROGRAM.buffer_ms);
    pushSeries("cpu",     store.SYS.cpu_pct);
    pushSeries("mem",     store.SYS.mem_mb);
    pushSeries("tb_rtt",  store.TALKBACK.rtt_ms);
    pushSeries("tb_jitter", store.TALKBACK.jitter_ms);

    notify();
  }

  function fmtT(iso) {
    try { return new Date(iso).toLocaleTimeString("en-GB", { hour12: false }); }
    catch (_) { return ""; }
  }
  function eventTuple(e) {
    return [fmtT(e.timestamp), (e.level || "info"), (e.subsystem || "system"), (e.message || "")];
  }

  async function init() {
    try {
      const [cfg, st, ev] = await Promise.all([
        fetch("/api/config").then(r => r.json()),
        fetch("/api/status").then(r => r.json()),
        fetch("/api/events").then(r => r.json()),
      ]);
      store.config = cfg;
      store.status = st;
      store.EVENTS = ev.map(eventTuple);
      rebuild();
    } catch (e) {
      console.error("[realtime] initial fetch failed", e);
    }

    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    let ws;
    const openWS = () => {
      try {
        ws = new WebSocket(`${proto}//${location.host}/api/ws/status`);
        ws.onopen    = () => { store.CONNECTED.ws = true;  notify(); };
        ws.onmessage = (m) => {
          try { store.status = JSON.parse(m.data); rebuild(); } catch (_) {}
        };
        ws.onclose   = () => { store.CONNECTED.ws = false; notify(); setTimeout(openWS, 2000); };
        ws.onerror   = () => { try { ws.close(); } catch (_) {} };
      } catch (e) { console.error("[realtime] ws open failed", e); }
    };
    openWS();

    let es;
    const openSSE = () => {
      try {
        es = new EventSource("/api/logs/stream");
        es.onopen    = () => { store.CONNECTED.sse = true;  notify(); };
        es.onmessage = (m) => {
          try {
            const e = JSON.parse(m.data);
            store.EVENTS = [eventTuple(e), ...store.EVENTS].slice(0, 200);
            notify();
          } catch (_) {}
        };
        es.onerror   = () => { store.CONNECTED.sse = false; notify(); };
      } catch (e) { console.error("[realtime] sse open failed", e); }
    };
    openSSE();
  }

  g.AB = store;
  g.AB.subscribe = (fn) => { subs.add(fn); return () => subs.delete(fn); };
  g.AB.refreshConfig = async () => {
    try {
      store.config = await fetch("/api/config").then(r => r.json());
      rebuild();
    } catch (e) { console.error("[realtime] refreshConfig failed", e); }
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(window);
