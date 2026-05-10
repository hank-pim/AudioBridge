// Audiobridge — mock data shaped like the API's telemetry / config snapshots.
// Telemetry mirrors what /api/status + /api/ws/status would return; events
// mirror /api/events; channels mirror routing + audio interface info.

(function (global) {
  const PEER = {
    self: { name: "STUDIO-A", addr: "audio-a.lan", role: "operator" },
    peer: { name: "REMOTE-MTL", addr: "mtl-edge.example.net", role: "responder" },
    paired_at: "2026-05-08T19:14:02Z",
    schema_version: 3,
    version: "0.1.0",
  };

  const PROGRAM = {
    state: "running",            // running | stopped | error | reconnecting
    transport: "SRT",
    codec: "OPUS 256k · 48kHz",
    channels: 64,
    direction: "bidirectional",
    srt_mode: "listener",
    srt_port: 9710,
    bitrate_kbps: 16384,         // aggregate
    rtt_ms: 38.4,
    jitter_ms: 1.7,
    loss_pct: 0.012,
    buffer_ms: 220,
    buffer_target_ms: 250,
    encrypted: true,
    uptime_s: 392418,
  };

  const TALKBACK = {
    state: "running",
    transport: "WebRTC",
    codec: "OPUS 64k · 48kHz",
    channels: 2,
    rtt_ms: 24.1,
    jitter_ms: 0.6,
    loss_pct: 0.0,
    pli_count: 0,
    uptime_s: 12044,
  };

  const SYS = {
    cpu_pct: 31,
    mem_pct: 47,
    temp_c: 54,
    nic_dante:  { name: "Ethernet",   ip: "10.20.0.14",   speed_gbps: 1, rx_mbps: 312, tx_mbps: 281 },
    nic_wan:    { name: "Ethernet 2", ip: "203.0.113.41", speed_gbps: 1, rx_mbps: 22.4, tx_mbps: 19.1 },
    audio_iface: { name: "Dante Virtual Soundcard", driver: "wasapi", sr: 48000, ch: 64 },
    uptime_s: 593011,
  };

  const SLO = { current_pct: 99.987, window: "30d", error_budget_pct: 0.013, mttr_min: 4.2 };

  // Synthesized 64-channel manifest. Names give the dashboard texture.
  const CH_NAMES = [
    "PGM L", "PGM R", "Mix-Minus L", "Mix-Minus R",
    "Anchor 1", "Anchor 2", "Co-Host", "Field Mic A", "Field Mic B",
    "Caller 1", "Caller 2", "Caller 3", "Caller 4",
    "Stinger Bus", "Music Bed", "SFX A", "SFX B",
    "Crowd Amb L", "Crowd Amb R",
    "Ref Mic L", "Ref Mic R", "Talent IFB",
    "Translator EN L", "Translator EN R", "Translator FR L", "Translator FR R",
    "Submix Music L", "Submix Music R",
    "Submix VO L", "Submix VO R",
    "Pro Tools 1-2 L", "Pro Tools 1-2 R", "Pro Tools 3-4 L", "Pro Tools 3-4 R",
    "Pro Tools 5-6 L", "Pro Tools 5-6 R", "Pro Tools 7-8 L", "Pro Tools 7-8 R",
    "Aux Send 1", "Aux Send 2", "Aux Send 3", "Aux Send 4",
    "Bus 1 L", "Bus 1 R", "Bus 2 L", "Bus 2 R",
    "Bus 3 L", "Bus 3 R", "Bus 4 L", "Bus 4 R",
    "Spare 1", "Spare 2", "Spare 3", "Spare 4",
    "Tally Tone", "Slate Mic",
    "GPI 1", "GPI 2", "GPI 3", "GPI 4",
    "Reserve A", "Reserve B", "Reserve C", "Reserve D",
  ];
  while (CH_NAMES.length < 64) CH_NAMES.push("Spare " + (CH_NAMES.length + 1));

  // Type / transport derived from name. PGM/MIX-/SRC/BUS/AUX ride SRT
  // (program path); IFB/PL/TB ride WebRTC (talkback path); TONE/SLATE
  // are SRT diagnostics; SPARE is unrouted.
  function typeOf(name) {
    if (/^PGM/i.test(name))                     return "PGM";
    if (/^Mix-Minus/i.test(name))               return "MIX-";
    if (/IFB|Translator/i.test(name))           return "IFB";
    if (/^Caller/i.test(name))                  return "PL";
    if (/^Aux Send/i.test(name))                return "AUX";
    if (/^Bus|Submix|Stinger|Music Bed|SFX/i.test(name)) return "BUS";
    if (/^Pro Tools|Anchor|Co-Host|Field Mic|Crowd|Ref Mic/i.test(name)) return "SRC";
    if (/Tally Tone/i.test(name))               return "TONE";
    if (/Slate/i.test(name))                    return "SLATE";
    if (/^GPI/i.test(name))                     return "GPI";
    if (/^Spare|^Reserve/i.test(name))          return "SPARE";
    return "SRC";
  }
  // Direction is operator-perspective at the local bridge end:
  //   IN  = local Dante  → bridge → WAN (we're sending it to the peer)
  //   OUT = WAN → bridge → local Dante (peer is sending it to us)
  // Stereo pairs (L/R, EN, FR) always share direction.
  function dirOf(name, type) {
    // Direction follows network convention: IN = arriving at this endpoint
    // from the WAN/peer (RX), OUT = leaving this endpoint toward the
    // WAN/peer (TX). PL/TB are bidirectional roles and are split into
    // two rows below (one IN, one OUT) so this placeholder isn't used.
    if (type === "PL" || type === "TB") return "in";
    // Things WE send TO the peer
    if (/^PGM/i.test(name))                return "out";
    if (type === "AUX" || type === "BUS")  return "out";
    if (/^Pro Tools|Submix|Stinger|Music Bed|SFX|Tally Tone|Slate/i.test(name)) return "out";
    // Everything else is something the peer sends to us
    return "in";
  }
  function transportOf(type) {
    if (type === "IFB" || type === "PL" || type === "TB") return "WebRTC";
    if (type === "GPI") return "\u2014";
    return "SRT";
  }

  // Deterministic pseudo-random so hot-reloads stay visually stable.
  function mulberry(seed){return function(){let t=seed+=0x6D2B79F5;t=Math.imul(t^t>>>15,t|1);t^=t+Math.imul(t^t>>>7,t|61);return((t^t>>>14)>>>0)/4294967296;};}
  const rng = mulberry(42);

  // Build one row at a time. PL/TB roles are bidirectional, so they
  // become TWO rows: a SEND (caller → us) and a RETURN (us → caller),
  // each with its own metrics. Everything else is one row.
  function buildRow(name, type, dir, slot) {
    const r = rng();
    let state = "active";
    if (slot >= 56) state = "idle";
    // Sprinkle a handful of stopped streams across the active range so
    // the mock shows both start and stop states in the actions column.
    else if ([3, 14, 22, 35, 48].includes(slot)) state = "idle";
    else if (r < 0.06) state = "warn";
    else if (r < 0.085) state = "err";
    else if (r < 0.12) state = "muted";
    const baseLevel = state === "idle" ? -120
      : state === "muted" ? -80
      : state === "err"   ? -3 + rng() * 2.5
      : state === "warn"  ? -8 + rng() * 5
      : -36 + rng() * 28;
    const peak = Math.min(0, baseLevel + 1.5 + rng() * 4);
    const transport = transportOf(type);
    return {
      name, type, transport, direction: dir,
      route: dir === "out" ? `dante://${slot+1}` : `peer://${slot+1}`,
      codec: state === "idle" ? "—" : (transport === "WebRTC" ? "OPUS 64k" : "OPUS 256k"),
      bitrate_kbps: state === "idle" ? 0 : (transport === "WebRTC" ? 64 : 256),
      level_dbfs: state === "idle" ? -120 : baseLevel,
      peak_dbfs: state === "idle" ? -120 : peak,
      gain_db: 0, state,
      jitter_ms: state === "idle" ? null : 0.5 + rng() * 2.5,
      loss_pct: state === "err" ? 1.4 + rng()*1.5 : state === "warn" ? 0.4 + rng()*0.6 : rng() * 0.05,
      latency_ms: state === "idle" ? null : 30 + rng() * 18,
      // Adaptive jitter buffer hold (ms) — grows under jitter, target 90–250ms
      buffer_ms: state === "idle" ? null
        : state === "err"  ? 220 + rng() * 60
        : state === "warn" ? 160 + rng() * 50
        : 90 + rng() * 50,
      // Receive-clock PLL state vs. source presentation timestamps
      sync: state === "idle" ? "off"
        : state === "err"  ? "drift"
        : state === "warn" ? "slew"
        : "lock",
      // PPM offset of receive PLL when locked / slewing
      ppm: state === "idle" ? null
        : state === "err"  ? (rng() * 14 - 7)
        : state === "warn" ? (rng() * 4 - 2)
        : (rng() * 0.4 - 0.2),
    };
  }

  const CHANNELS = [];
  for (let i = 0; i < 64; i++) {
    const name = CH_NAMES[i];
    const type = typeOf(name);
    if (type === "PL" || type === "TB") {
      // SEND: remote caller → us (mic). RETURN: us → remote caller (IFB/PGM mix).
      CHANNELS.push({ ...buildRow(name + " → send",   type, "out", i), pair: name });
      CHANNELS.push({ ...buildRow(name + " ← return", type, "in",  i), pair: name });
    } else {
      CHANNELS.push(buildRow(name, type, dirOf(name, type), i));
    }
  }
  // Sequential ids reflecting the real stream count.
  CHANNELS.forEach((c, idx) => { c.id = idx + 1; });

  // Routing matrix — sparse 64×64 routes (each input mapped 0–2 outputs)
  const ROUTES = [];
  for (let i = 0; i < 56; i++) {
    if (rng() > 0.85) continue;
    const o1 = i; // diagonal-ish
    ROUTES.push([i, o1, 1.0]);
    if (rng() > 0.75) ROUTES.push([i, (o1 + 1) % 64, 0.5]);
    if (rng() > 0.92) ROUTES.push([i, (o1 + 8) % 64, 0.3]);
  }

  const EVENTS = [
    ["19:42:11", "info",  "media",    "program path peer hello received"],
    ["19:41:58", "info",  "media",    "talkback path started"],
    ["19:41:57", "info",  "signaling","signaling websocket connected"],
    ["19:40:02", "warn",  "media",    "buffer drained 220→90ms (jitter spike +6.4ms)"],
    ["19:38:44", "info",  "control",  "configuration patched · routes[34]"],
    ["19:38:11", "warn",  "media",    "ch 11 'Caller 2' loss 1.8% (5s sustained)"],
    ["19:36:00", "info",  "diagnostics","round-trip test · 41.2ms"],
    ["19:35:12", "info",  "system",   "NICs reaffirmed: dante=Ethernet, wan=Ethernet 2"],
    ["19:34:01", "info",  "pairing",  "pairing bundle generated · expires 900s"],
    ["19:33:12", "err",   "media",    "ch 9 'Field Mic B' decode error · skipped frame"],
    ["19:32:00", "info",  "media",    "program path started"],
    ["19:31:48", "info",  "system",   "audio interface set to Dante Virtual Soundcard (64 ch)"],
    ["19:30:10", "info",  "control",  "configuration replaced (schema v3)"],
    ["19:28:02", "info",  "system",   "endpoint boot · v0.1.0"],
  ];

  // Sparkline series — 60 samples each, last 60s.
  function series(seed, base, span, drift) {
    const r = mulberry(seed);
    const out = []; let v = base;
    for (let i = 0; i < 60; i++) {
      v += (r() - 0.5) * span * 0.6 + drift * 0.02;
      v = Math.max(0, v);
      out.push(v);
    }
    return out;
  }

  const SERIES = {
    bitrate:  series(1,  16380, 90,  0.4),
    rtt:      series(2,  38,    3,   0),
    jitter:   series(3,  1.5,   0.6, 0),
    loss:     series(4,  0.012, 0.02,0),
    buffer:   series(5,  220,   18,  0),
    cpu:      series(6,  30,    6,   0),
    mem:      series(7,  47,    1.5, 0),
    tb_rtt:   series(8,  24,    2,   0),
    tb_jitter:series(9,  0.6,   0.3, 0),
  };

  global.AB = { PEER, PROGRAM, TALKBACK, SYS, SLO, CHANNELS, ROUTES, EVENTS, SERIES };
})(window);
