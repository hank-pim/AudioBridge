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
               bitrate_kbps: null, rtt_ms: null, jitter_ms: null, loss_pct: null, buffer_ms: null, buffer_target_ms: 0,
               encrypted: true, uptime_s: 0 },
    TALKBACK:{ state: "stopped", transport: "WebRTC", codec: "—", channels: 2,
               rtt_ms: null, jitter_ms: null, loss_pct: null, pli_count: null, uptime_s: 0 },
    SYS:     { cpu_pct: null, mem_pct: null, mem_mb: null, temp_c: null,
               nic_dante: { name: "—", ip: "—", speed_gbps: null, rx_mbps: null, tx_mbps: null },
               nic_wan:   { name: "—", ip: "—", speed_gbps: null, rx_mbps: null, tx_mbps: null },
               audio_iface: { name: "(no interface selected)", driver: "—", sr: 48000, ch: 0 },
               uptime_s: 0 },
    CLOCK:   { mode: "adaptive", lock_state: "idle", frequency_ratio_ppm: null,
               phase_trim_ppm: null, buffer_occupancy_ms: null, slip_events: null },
    CHANNELS: [],
    EVENTS:  [],
    SERIES:  { bitrate: [], rtt: [], jitter: [], loss: [], buffer: [],
               cpu: [], mem: [], tb_rtt: [], tb_jitter: [] },
    CONNECTED: { ws: false, sse: false },
    config: null,
    status: null,
    runtime: null,
  };

  const notify = () => subs.forEach(fn => fn());
  const pushSeries = (key, val) => {
    if (!Number.isFinite(val)) return;
    const s = store.SERIES[key];
    s.push(Number(val));
    while (s.length > HISTORY) s.shift();
  };
  const toMbps = (kbps) => Number.isFinite(kbps) ? +((kbps || 0) / 1000).toFixed(2) : null;

  function buildChannels(cfg, st) {
    const srtTransports = (st && st.srt_transports) || [];
    const webrtcStreams = (st && st.webrtc_streams) || [];
    const encodeGroups = (st && st.encode_groups) || [];
    const inputs  = (st && st.meters && st.meters.inputs)  || [];
    const outputs = (st && st.meters && st.meters.outputs) || [];
    const defaultProgramOpus = (cfg && cfg.program && cfg.program.opus) || {};
    const defaultTalkbackBitrate = (cfg && cfg.talkback && cfg.talkback.opus_bitrate_kbps) || 48;
    const maxFinite = (values) => {
      const nums = values.filter(Number.isFinite);
      return nums.length ? Math.max(...nums) : null;
    };

    const srtRows = srtTransports.map((transport, index) => {
      const dir = transport.direction === "tx" ? "out" : "in";
      const group = encodeGroups.find((item) => (transport.encode_group_ids || []).includes(item.id));
      const meterSet = dir === "in" ? inputs : outputs;
      const channelCount = Math.max(1, (group && group.channel_count) || 1);
      const meters = meterSet.slice(0, channelCount);
      const level = maxFinite(meters.map(m => m && m.rms_dbfs));
      const peak  = maxFinite(meters.map(m => m && m.peak_dbfs));
      const bitrate = (defaultProgramOpus && defaultProgramOpus.bitrate_kbps) || 96;
      const running = transport.state === "running";
      const enabled = true;
      const state = !running ? "idle"
                  : "running";
      const route = `${transport.id} · ${(transport.encode_group_ids || []).join(", ") || "no groups"}`;
      return {
        id: `srt-${index + 1}`,
        runtime_id: transport.id,
        entity_kind: "srt_transport",
        name: transport.name,
        type: "SRT",
        direction: dir,
        transport: "SRT",
        dante_channel: 1,
        srt_slot: index + 1,
        rtc_track: null,
        route,
        codec: state === "idle" ? "—" : `OPUS ${bitrate}k target`,
        bitrate_kbps: transport.bitrate_kbps,
        configured_bitrate_kbps: transport.configured_bitrate_kbps,
        level_dbfs: level, peak_dbfs: peak, gain_db: 0, state, enabled,
        opus: defaultProgramOpus,
        jitter_ms: null,
        loss_pct: null,
        latency_ms: transport.rtt_ms != null ? transport.rtt_ms / 2 : null,
        buffer_ms: transport.latency_ms || null,
        sync: state === "idle" ? "off" : "unknown", ppm: null,
        details: transport,
      };
    });

    const webrtcRows = webrtcStreams.map((stream, index) => {
      const dir = stream.direction === "tx" ? "out" : "in";
      const dante = index + 1;
      const meter = (dir === "in" ? inputs : outputs)[dante - 1] || { peak_dbfs: null, rms_dbfs: null };
      const level = meter.rms_dbfs;
      const peak  = meter.peak_dbfs;
      const running = stream.state === "running";
      const state = !running ? "idle"
                  : "running";
      return {
        id: `rtc-${index + 1}`,
        runtime_id: stream.id,
        entity_kind: "webrtc_stream",
        name: stream.name,
        type: "RTC",
        direction: dir,
        transport: "WebRTC",
        dante_channel: dante,
        srt_slot: null,
        rtc_track: index + 1,
        route: `${stream.id} · ${stream.source_id || "unpatched"}`,
        codec: state === "idle" ? "—" : `OPUS ${defaultTalkbackBitrate}k target`,
        bitrate_kbps: stream.bitrate_kbps,
        configured_bitrate_kbps: stream.configured_bitrate_kbps,
        level_dbfs: level, peak_dbfs: peak, gain_db: 0, state, enabled: true,
        opus: { bitrate_kbps: defaultTalkbackBitrate },
        jitter_ms: null,
        loss_pct: null,
        latency_ms: stream.rtt_ms != null ? stream.rtt_ms / 2 : null,
        buffer_ms: null,
        sync: state === "idle" ? "off" : "unknown", ppm: null,
        details: stream,
      };
    });

    return srtRows.concat(webrtcRows);
  }

  function rebuild() {
    const cfg = store.config, st = store.status;
    if (!cfg || !st) return;
    store.runtime = st.runtime || null;

    store.PEER = {
      self: { name: cfg.endpoint_name || "endpoint", addr: (cfg.network && cfg.network.public_address) || "—" },
      peer: { name: (cfg.pairing && cfg.pairing.peer_name) || "—",
              addr: (cfg.pairing && cfg.pairing.peer_signaling_url) || "—" },
      version: "0.1.0",
    };

    const srtTransports = st.srt_transports || [];
    const webrtcStreams = st.webrtc_streams || [];
    const primaryTransport = srtTransports[0] || null;
    const sendKbps = st.srt && st.srt.send_bitrate_kbps;
    const recvKbps = st.srt && st.srt.receive_bitrate_kbps;
    const observedProgramKbps =
      Number.isFinite(sendKbps) && Number.isFinite(recvKbps) ? sendKbps + recvKbps
      : Number.isFinite(sendKbps) ? sendKbps
      : Number.isFinite(recvKbps) ? recvKbps
      : null;
    const activeEncodeGroups = (st.encode_groups || []).filter((group) => Array.isArray(group.transport_ids) && group.transport_ids.length > 0);
    const programChannels = activeEncodeGroups.reduce((total, group) => total + (group.channel_count || 0), 0)
      || ((cfg.encode_groups || []).reduce((total, group) => total + (group.channel_count || 0), 0));
    store.PROGRAM = {
      state: (st.link && st.link.program) || "stopped",
      transport: "SRT",
      codec: `OPUS ${(cfg.program && cfg.program.opus && cfg.program.opus.bitrate_kbps) || 96}k · 48kHz`,
      channels: programChannels || 0,
      srt_mode: (primaryTransport && primaryTransport.mode) || (cfg.program && cfg.program.srt_mode) || "listener",
      srt_port: (primaryTransport && primaryTransport.port) || (cfg.network && cfg.network.srt_port) || 9000,
      bitrate_kbps: observedProgramKbps,
      rtt_ms:    st.srt && st.srt.rtt_ms,
      jitter_ms: st.srt && st.srt.rtt_variance_ms,
      loss_pct:  st.srt && Number.isFinite(st.srt.packet_loss_percent) ? st.srt.packet_loss_percent : null,
      buffer_ms: st.srt && st.srt.buffer_occupancy_ms,
      buffer_target_ms: (cfg.program && cfg.program.srt_latency_ms) || 250,
      encrypted: !!(cfg.program && cfg.program.encryption_enabled),
      uptime_s: st.uptime_seconds || 0,
    };
    store.TALKBACK = {
      state: (st.link && st.link.talkback) || "stopped",
      transport: "WebRTC",
      codec: `OPUS ${(cfg.talkback && cfg.talkback.opus_bitrate_kbps) || 48}k · 48kHz`,
      channels: webrtcStreams.length,
      rtt_ms:    st.webrtc && st.webrtc.rtt_ms,
      jitter_ms: st.webrtc && st.webrtc.jitter_ms,
      loss_pct:  st.webrtc && st.webrtc.packet_loss_percent,
      pli_count: null,
      uptime_s: st.uptime_seconds || 0,
    };
    store.SYS = {
      cpu_pct: st.system && st.system.cpu_percent,
      mem_mb: st.system && st.system.memory_mb,
      mem_pct: null,
      temp_c: null,
      nic_dante: {
        name: (cfg.network && cfg.network.dante_nic) || "—",
        ip: "—", speed_gbps: null,
        rx_mbps: toMbps(st.system && st.system.dante_rx_kbps),
        tx_mbps: toMbps(st.system && st.system.dante_tx_kbps),
      },
      nic_wan: {
        name: (cfg.network && cfg.network.wan_nic) || "—",
        ip: (cfg.network && cfg.network.public_address) || "—", speed_gbps: null,
        rx_mbps: toMbps(st.system && st.system.wan_rx_kbps),
        tx_mbps: toMbps(st.system && st.system.wan_tx_kbps),
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
  g.AB.refreshStatus = async () => {
    try {
      store.status = await fetch("/api/status").then(r => r.json());
      rebuild();
    } catch (e) { console.error("[realtime] refreshStatus failed", e); }
  };
  g.AB.refreshAll = async () => {
    await Promise.all([g.AB.refreshConfig(), g.AB.refreshStatus()]);
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})(window);
