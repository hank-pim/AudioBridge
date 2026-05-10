// Audiobridge — NOC dashboard. KPI strip across the top, dense streams
// table, system + live events rail.
console.log("[variation-a] build loaded", new Date().toISOString());

var useState = React.useState, useMemo = React.useMemo, useEffect = React.useEffect, useRef = React.useRef;

function VariationA({ density = 8, showEventsRail = true, showSystemCard = true, kpiCount = 6 }) {
  const { PROGRAM, TALKBACK, SYS, CHANNELS, SERIES } = window.AB;
  const [view, setView] = useState("streams");
  const [tab, setTab] = useState("all"); // all | rx | tx | issues
  const [query, setQuery] = useState("");
  const [expandedIds, setExpandedIds] = useState(() => new Set());
  const toggleExpanded = (id) => setExpandedIds(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const closeExpanded = (id) => setExpandedIds(prev => {
    if (!prev.has(id)) return prev;
    const next = new Set(prev);
    next.delete(id);
    return next;
  });
  const [adding, setAdding] = useState(false);

  const filtered = useMemo(() => {
    let rows = CHANNELS;
    if (tab === "rx") rows = rows.filter(c => c.direction === "in");
    if (tab === "tx") rows = rows.filter(c => c.direction === "out");
    if (tab === "issues")  rows = rows.filter(c => c.state === "warn" || c.state === "err");
    if (query) rows = rows.filter(c => c.name.toLowerCase().includes(query.toLowerCase()) || String(c.id).includes(query));
    return rows;
  }, [tab, query, CHANNELS]);

  // Density 1..10 → row height 36..22
  const rowH = Math.round(38 - (density - 1) * (16 / 9));

  return (
    <div className="ab-frame ab-root">
      <TopBar active={view} alerts={2} onNavigate={(next) => setView(next === "settings" ? "settings" : "streams")} />

      {view === "settings" ? (
        <SettingsView onBack={() => setView("streams")} />
      ) : (
      <>

      {/* KPI strip */}
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${kpiCount}, 1fr)`, gap: 1, padding: 12, paddingBottom: 0 }}>
        <KpiTile
          label="Program · SRT"
          value={fmtBitrate(PROGRAM.bitrate_kbps).split(" ")[0]}
          unit=" Mb/s"
          delta="+0.4%"
          deltaTone="ok"
          spark={SERIES.bitrate}
          sparkTone="ok"
          footer="OPUS 256k · 64ch · encrypted"
        />
        <KpiTile
          label="RTT · Program"
          value={PROGRAM.rtt_ms.toFixed(1)}
          unit=" ms"
          delta="−0.6 ms"
          deltaTone="ok"
          spark={SERIES.rtt}
          sparkTone="ok"
          footer="p99 · 41.2ms · TX + RX"
        />
        <KpiTile
          label="Jitter"
          value={PROGRAM.jitter_ms.toFixed(2)}
          unit=" ms"
          delta="+0.1 ms"
          deltaTone="warn"
          spark={SERIES.jitter}
          sparkTone="warn"
          footer="buffer hold 220 / 250ms"
        />
        <KpiTile
          label="Packet loss"
          value={(PROGRAM.loss_pct * 100).toFixed(2)}
          unit=" %"
          delta="−0.01"
          deltaTone="ok"
          spark={SERIES.loss}
          sparkTone="muted"
          footer="60s window"
        />
        {kpiCount >= 5 && (
          <KpiTile
            label="Talkback · WebRTC"
            value={TALKBACK.rtt_ms.toFixed(1)}
            unit=" ms"
            delta="lock"
            deltaTone="ok"
            spark={SERIES.tb_rtt}
            sparkTone="ok"
            footer="OPUS 64k · 2ch"
          />
        )}
        {kpiCount >= 6 && (
          <ClockKpiTile
            sync="lock"
            ppm={-0.18}
            codec="OPUS 256k"
            sampleRate="48.000 kHz"
            note="shared ratio · last slew 6m ago"
          />
        )}
      </div>

      {/* Body grid: streams table | events rail */}
      <div style={{ flex: 1, display: "grid", gridTemplateColumns: (showEventsRail || showSystemCard) ? "1fr 320px" : "1fr", gap: 12, padding: 12, minHeight: 0 }}>
        <Card
          title="Streams"
          hint={`${filtered.length} of ${CHANNELS.length} · ${CHANNELS.filter(c => c.state === "warn" || c.state === "err").length} alerts`}
          right={(
            <>
              <div style={{ display: "flex", gap: 0, padding: 2, background: "var(--ab-surface-2)", borderRadius: 4 }}>
                {(() => {
                  const rxN = CHANNELS.filter(c => c.direction === "in").length;
                  const txN = CHANNELS.filter(c => c.direction === "out").length;
                  const isN = CHANNELS.filter(c => c.state === "warn" || c.state === "err").length;
                  return [["all","All"],["rx",`RX · ${rxN}`],["tx",`TX · ${txN}`],["issues",`Issues · ${isN}`]];
                })().map(([k, lbl]) => (
                  <button key={k} onClick={() => setTab(k)}
                          className="ab-btn"
                          data-variant={tab === k ? "primary" : "ghost"}
                          style={{ height: 22, padding: "0 8px", fontSize: 11, ...(tab !== k ? { background: "transparent", border: "1px solid transparent", color: "var(--ab-fg-3)" } : {}) }}>
                    {lbl}
                  </button>
                ))}
              </div>
              <div style={{ position: "relative", display: "flex", alignItems: "center" }}>
                <Icon.search style={{ position: "absolute", left: 8, color: "var(--ab-fg-4)" }} />
                <input value={query} onChange={e => setQuery(e.target.value)} placeholder="filter…"
                       className="ab-mono"
                       style={{ height: 22, padding: "0 8px 0 24px", width: 140, fontSize: 11,
                                background: "var(--ab-surface-2)", color: "var(--ab-fg)",
                                border: "1px solid var(--ab-border-soft)", borderRadius: 4, outline: "none" }} />
              </div>
              <button className="ab-btn" data-variant={adding ? "primary" : undefined} style={{ height: 22, fontSize: 11 }} onClick={() => setAdding(v => !v)}>+ add stream</button>
              <button className="ab-btn" style={{ height: 22, fontSize: 11 }}><Icon.refresh /> 1s</button>
            </>
          )}
        >
          {adding && <AddStreamPanel onClose={() => setAdding(false)} />}
          <div style={{ maxHeight: 600, overflow: "auto" }}>
            <table className="ab-tbl">
              <thead>
                <tr>
                  <th style={{ width: 36 }}>#</th>
                  <th style={{ width: 36 }}>TX/RX</th>
                  <th style={{ width: 60 }}>Type</th>
                  <th>Name</th>
                  <th style={{ width: 64 }}>State</th>
                  <th style={{ width: 156 }}>Level (dBFS)</th>
                  <th className="ab-num" style={{ width: 56 }} title="Packet loss % over 5s window">Loss</th>
                  <th className="ab-num" style={{ width: 50 }} title="Inter-arrival jitter (ms)">Jit</th>
                  <th className="ab-num" style={{ width: 48 }} title="One-way audio latency (ms) — RTT/2 + jitter buffer hold + codec">Lat</th>
                  <th className="ab-num" style={{ width: 52 }} title="Adaptive jitter-buffer hold (ms) — grows under jitter, target 90–250ms">Buf</th>
                  <th style={{ width: 100 }}>Transport</th>
                  <th style={{ width: 100 }}>Route</th>
                  <th style={{ width: 110 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map(c => (
                  <React.Fragment key={c.id}>
                  <tr data-state={c.state === "idle" ? "muted" : null} style={{ height: rowH }}>
                    <td className="ab-mono" style={{ color: "var(--ab-fg-4)" }}>{String(c.id).padStart(2, "0")}</td>
                    <td>
                      {c.direction === "in" ? (
                        <span style={{ color: "var(--ab-info)", display: "inline-flex", alignItems: "center", gap: 4, fontFamily: "var(--ab-mono)", fontSize: 11, letterSpacing: 0.04 }}><Icon.arrow2 /> RX</span>
                      ) : (
                        <span style={{ color: "var(--ab-accent)", display: "inline-flex", alignItems: "center", gap: 4, fontFamily: "var(--ab-mono)", fontSize: 11, letterSpacing: 0.04 }}>
                          <span style={{ display: "inline-flex", transform: "scaleX(-1)" }}><Icon.arrow2 /></span>
                          TX
                        </span>
                      )}
                    </td>
                    <td><TypeChip type={c.type} transport={c.transport} /></td>
                    <td style={{ color: "var(--ab-fg)" }}>{c.name}</td>
                    <td><StateChip state={c.state} /></td>
                    <td>
                      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                        <Meter level={c.level_dbfs} peak={c.peak_dbfs} w={88} />
                        <span className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-fg-2)", width: 38, textAlign: "right" }}>{fmtDb(c.level_dbfs)}</span>
                      </div>
                    </td>
                    <td className="ab-num ab-mono" style={{ color: c.loss_pct > 1 ? "var(--ab-err)" : c.loss_pct > 0.3 ? "var(--ab-warn)" : "var(--ab-fg-3)" }}>{c.loss_pct.toFixed(2)}</td>
                    <td className="ab-num ab-mono" style={{ color: "var(--ab-fg-3)" }}>{c.jitter_ms ? c.jitter_ms.toFixed(1) : "—"}</td>
                    <td className="ab-num ab-mono" style={{ color: "var(--ab-fg-3)" }}>{c.latency_ms ? c.latency_ms.toFixed(0) : "—"}</td>
                    <td className="ab-num ab-mono" style={{ color: c.buffer_ms == null ? "var(--ab-fg-5)" : c.buffer_ms > 200 ? "var(--ab-warn)" : "var(--ab-fg-3)" }}>{c.buffer_ms == null ? "—" : c.buffer_ms.toFixed(0)}</td>
                    <td className="ab-mono" style={{ fontSize: 11 }}><TransportLabel transport={c.transport} codec={c.codec} /></td>
                    <td className="ab-mono" style={{ color: "var(--ab-fg-3)", fontSize: 11 }}>{c.route}</td>
                    <td><RowActions ch={c} expanded={expandedIds.has(c.id)} onToggle={() => toggleExpanded(c.id)} /></td>
                  </tr>
                  {expandedIds.has(c.id) && (
                    <tr key={c.id + "-cfg"}>
                      <td colSpan={13} style={{ padding: 0, height: "auto", background: "var(--ab-surface-2)", borderBottom: "1px solid var(--ab-border)" }}>
                        <StreamConfig ch={c} onClose={() => closeExpanded(c.id)} />
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </Card>

        {(showEventsRail || showSystemCard) && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>
          {showSystemCard && (
          <Card title="System" hint={SYS.audio_iface.name}>
            <div style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 10 }}>
              <SysRow label="CPU" value={SYS.cpu_pct + "%"} bar={SYS.cpu_pct} tone="ok" sub={`load avg 0.74`} />
              <SysRow label="MEM" value={SYS.mem_pct + "%"} bar={SYS.mem_pct} tone="ok" sub={`3.1 / 6.5 GiB`} />
              <SysRow label="TEMP" value={SYS.temp_c + "°C"} bar={(SYS.temp_c / 90) * 100} tone={SYS.temp_c > 70 ? "warn" : "ok"} sub="threshold 78°C" />
              <div className="ab-divider" />
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
                  <span className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-3)" }} title="Single shared resampler PLL — one clock domain for all interleaved channels on this bridge">Resampler PLL</span>
                  <ClockChip sync="lock" ppm={-0.18} />
                </div>
                <div className="ab-mono" style={{ fontSize: 10, color: "var(--ab-fg-4)", marginTop: 3 }}>shared ratio · 48.000 kHz · last slew 6m ago</div>
              </div>
              <div className="ab-divider" />
              <NicRow label="Dante NIC" nic={SYS.nic_dante} />
              <NicRow label="WAN NIC"   nic={SYS.nic_wan} />
            </div>
          </Card>
          )}

          {showEventsRail && (
          <Card
            title="Live events"
            hint="SSE · /api/logs/stream"
            right={<><Chip tone="ok">connected</Chip><button className="ab-btn" data-variant="ghost" style={{ height: 22, fontSize: 11 }}>pause</button></>}
            style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0 }}
          >
            <div style={{ borderTop: "1px solid var(--ab-border-soft)", flex: 1, overflow: "auto" }}>
              <EventsLog />
            </div>
          </Card>
          )}
        </div>
        )}
      </div>
      </>
      )}
    </div>
  );
}

function SysRow({ label, value, bar, tone, sub }) {
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
        <span className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-3)" }}>{label}</span>
        <span className="ab-mono" style={{ fontSize: 12, color: "var(--ab-fg)" }}>{value}</span>
      </div>
      <div style={{ height: 4, background: "var(--ab-surface-3)", borderRadius: 1, overflow: "hidden" }}>
        <div style={{ width: bar + "%", height: "100%", background: tone === "warn" ? "var(--ab-warn)" : "var(--ab-accent)" }} />
      </div>
      {sub && <div className="ab-mono" style={{ fontSize: 10, color: "var(--ab-fg-4)", marginTop: 3 }}>{sub}</div>}
    </div>
  );
}
function NicRow({ label, nic }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-3)" }}>{label}</span>
        <span className="ab-mono" style={{ fontSize: 11, color: "var(--ab-fg-2)" }}>{nic.name}</span>
      </div>
      <div className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-fg-4)", display: "flex", justifyContent: "space-between" }}>
        <span>{nic.ip}</span>
        <span>↓ {nic.rx_mbps} <span style={{ color: "var(--ab-fg-5)" }}>·</span> ↑ {nic.tx_mbps} <span style={{ color: "var(--ab-fg-5)" }}>Mb/s</span></span>
      </div>
    </div>
  );
}

function SettingsView({ onBack }) {
  const cfg = window.AB.config || {};
  const streams = (cfg.audio && cfg.audio.streams) || [];
  const programDefaults = (cfg.program && cfg.program.opus) || defaultOpus("srt");

  const makeState = () => ({
    endpoint_name: cfg.endpoint_name || "Dante Bridge Endpoint",
    network: {
      dante_nic: (cfg.network && cfg.network.dante_nic) || "",
      wan_nic: (cfg.network && cfg.network.wan_nic) || "",
      public_address: (cfg.network && cfg.network.public_address) || "",
      signaling_port: (cfg.network && cfg.network.signaling_port) || 8443,
      srt_port: (cfg.network && cfg.network.srt_port) || 9000,
    },
    program: {
      enabled: !!(cfg.program && cfg.program.enabled),
      srt_mode: (cfg.program && cfg.program.srt_mode) || "listener",
      srt_latency_ms: (cfg.program && cfg.program.srt_latency_ms) || 240,
      srt_bandwidth_mode: (cfg.program && cfg.program.srt_bandwidth_mode) || ((cfg.program && cfg.program.inbound_bandwidth_cap_kbps) ? "manual" : "auto"),
      srt_overhead_bandwidth_percent: (cfg.program && cfg.program.srt_overhead_bandwidth_percent) || 25,
      inbound_bandwidth_cap_kbps: (cfg.program && cfg.program.inbound_bandwidth_cap_kbps) || "",
      encryption_enabled: cfg.program ? !!cfg.program.encryption_enabled : true,
      encryption_strength: (cfg.program && cfg.program.encryption_strength) || "aes-256",
      clock_recovery_mode: (cfg.program && cfg.program.clock_recovery_mode) || "adaptive",
      free_running_clock: {
        jitter_buffer_ms: (cfg.program && cfg.program.free_running_clock && cfg.program.free_running_clock.jitter_buffer_ms) || 500,
      },
      opus: { ...programDefaults },
    },
    talkback: {
      enabled: !!(cfg.talkback && cfg.talkback.enabled),
      output_channel: (cfg.talkback && cfg.talkback.output_channel) || 1,
      opus_bitrate_kbps: (cfg.talkback && cfg.talkback.opus_bitrate_kbps) || 48,
      opus_bitrate_mode: (cfg.talkback && cfg.talkback.opus_bitrate_mode) || "cbr",
      frame_ms: (cfg.talkback && cfg.talkback.frame_ms) || 10,
      restricted_lowdelay: !!(cfg.talkback && cfg.talkback.restricted_lowdelay),
    },
  });

  const [form, setForm] = useState(makeState);
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");
  const [networkInterfaces, setNetworkInterfaces] = useState([]);
  const [keyStatus, setKeyStatus] = useState("idle");
  const [copyStatus, setCopyStatus] = useState("idle");
  const [pairingStatus, setPairingStatus] = useState("idle");
  const [inviteText, setInviteText] = useState("");
  const [acceptInviteText, setAcceptInviteText] = useState("");

  useEffect(() => {
    setForm(makeState());
    setStatus("idle");
    setError("");
  }, [JSON.stringify(cfg)]);

  useEffect(() => {
    let cancelled = false;
    fetch("/api/interfaces/network")
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
      .then(data => {
        if (!cancelled) setNetworkInterfaces((data && data.interfaces) || []);
      })
      .catch(e => {
        console.error("[settings] network interface fetch failed", e);
        if (!cancelled) setNetworkInterfaces([]);
      });
    return () => { cancelled = true; };
  }, []);

  const current = JSON.stringify(makeState());
  const dirty = JSON.stringify(form) !== current;
  const srtStreams = streams.filter(s => (s.transport || "srt") === "srt").length;
  const rtcStreams = streams.filter(s => (s.transport || "srt") === "webrtc").length;
  const overridden = streams.filter(s => !!s.opus).length;

  const update = (section, key, value) => {
    setForm(prev => ({ ...prev, [section]: { ...prev[section], [key]: value } }));
  };
  const updateProgram = (key, value) => update("program", key, value);
  const updateNetwork = (key, value) => update("network", key, value);
  const updateTalkback = (key, value) => update("talkback", key, value);
  const srtPassphraseSet = !!(cfg.program && cfg.program.srt_passphrase);
  const paired = !!(cfg.pairing && cfg.pairing.peer_signaling_url);
  const peerLabel = paired
    ? ((cfg.pairing && cfg.pairing.peer_name) || (cfg.pairing && cfg.pairing.peer_signaling_url))
    : "unpaired";
  const observedSignaling = getObservedSignalingEndpoint(cfg);

  const rotateSrtPassphrase = async () => {
    setKeyStatus("saving");
    setCopyStatus("idle");
    setError("");
    try {
      const r = await fetch("/api/program/srt-passphrase", { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      await (window.AB.refreshConfig && window.AB.refreshConfig());
      setKeyStatus("saved");
      setTimeout(() => setKeyStatus("idle"), 1400);
    } catch (e) {
      console.error("[settings] SRT passphrase rotate failed", e);
      setError(String(e.message || e));
      setKeyStatus("error");
    }
  };

  const copySrtPassphrase = async () => {
    setCopyStatus("copying");
    setError("");
    try {
      const r = await fetch("/api/config/export?include_secrets=true");
      if (!r.ok) throw new Error(await r.text());
      const fullConfig = await r.json();
      const passphrase = fullConfig && fullConfig.program && fullConfig.program.srt_passphrase;
      if (!passphrase || passphrase === "********") throw new Error("SRT passphrase is not available");
      await writeClipboardText(passphrase);
      setCopyStatus("copied");
      setTimeout(() => setCopyStatus("idle"), 1400);
    } catch (e) {
      console.error("[settings] SRT passphrase copy failed", e);
      setError(String(e.message || e));
      setCopyStatus("error");
    }
  };

  const generatePairingBundle = async () => {
    setPairingStatus("saving");
    setError("");
    try {
      const r = await fetch("/api/pairing/bundle", { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      const bundle = await r.json();
      const text = JSON.stringify(bundle, null, 2);
      setInviteText(text);
      try {
        await writeClipboardText(text);
      } catch (copyError) {
        console.warn("[settings] pairing bundle clipboard copy blocked", copyError);
        setError("Bundle generated. Select the bundle field and copy manually.");
        setPairingStatus("ready");
        await (window.AB.refreshConfig && window.AB.refreshConfig());
        return;
      }
      await (window.AB.refreshConfig && window.AB.refreshConfig());
      setPairingStatus("copied");
      setTimeout(() => setPairingStatus("idle"), 1600);
    } catch (e) {
      console.error("[settings] pairing bundle generation failed", e);
      setError(String(e.message || e));
      setPairingStatus("error");
    }
  };

  const applyPairingBundle = async () => {
    setPairingStatus("saving");
    setError("");
    try {
      const bundle = JSON.parse(acceptInviteText);
      const r = await fetch("/api/pairing/apply", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(bundle),
      });
      if (!r.ok) throw new Error(await r.text());
      await (window.AB.refreshConfig && window.AB.refreshConfig());
      setAcceptInviteText("");
      setPairingStatus("paired");
      setTimeout(() => setPairingStatus("idle"), 1600);
    } catch (e) {
      console.error("[settings] pairing bundle apply failed", e);
      setError(String(e.message || e));
      setPairingStatus("error");
    }
  };

  const clearPairing = async () => {
    setPairingStatus("saving");
    setError("");
    try {
      const r = await fetch("/api/pairing", { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      await (window.AB.refreshConfig && window.AB.refreshConfig());
      setPairingStatus("cleared");
      setTimeout(() => setPairingStatus("idle"), 1600);
    } catch (e) {
      console.error("[settings] pairing clear failed", e);
      setError(String(e.message || e));
      setPairingStatus("error");
    }
  };

  const save = async () => {
    setStatus("saving");
    setError("");
    try {
      const manualBandwidth = form.program.srt_bandwidth_mode === "manual";
      const inbound = String(form.program.inbound_bandwidth_cap_kbps).trim();
      const patch = {
        endpoint_name: form.endpoint_name.trim() || "Dante Bridge Endpoint",
        network: {
          dante_nic: emptyToNull(form.network.dante_nic),
          wan_nic: emptyToNull(form.network.wan_nic),
          public_address: emptyToNull(form.network.public_address),
          signaling_port: clampInt(form.network.signaling_port, 1, 65535),
          srt_port: clampInt(form.network.srt_port, 1, 65535),
        },
        program: {
          enabled: !!form.program.enabled,
          srt_mode: form.program.srt_mode,
          srt_latency_ms: clampInt(form.program.srt_latency_ms, 20, 8000),
          srt_bandwidth_mode: form.program.srt_bandwidth_mode,
          srt_overhead_bandwidth_percent: clampInt(form.program.srt_overhead_bandwidth_percent, 0, 100),
          inbound_bandwidth_cap_kbps: manualBandwidth && inbound ? clampInt(inbound, 64, 100000) : null,
          encryption_enabled: !!form.program.encryption_enabled,
          encryption_strength: form.program.encryption_strength,
          clock_recovery_mode: form.program.clock_recovery_mode,
          free_running_clock: {
            jitter_buffer_ms: clampInt(form.program.free_running_clock.jitter_buffer_ms, 20, 5000),
          },
          opus: {
            bitrate_kbps: clampInt(form.program.opus.bitrate_kbps, 16, 512),
            bitrate_mode: form.program.opus.bitrate_mode || "cbr",
            frame_ms: clampInt(form.program.opus.frame_ms, 2, 60),
            complexity: clampInt(form.program.opus.complexity, 0, 10),
            inband_fec: !!form.program.opus.inband_fec,
            expected_packet_loss_percent: clampInt(form.program.opus.expected_packet_loss_percent, 0, 30),
          },
        },
        talkback: {
          enabled: !!form.talkback.enabled,
          output_channel: clampInt(form.talkback.output_channel, 1, 64),
          opus_bitrate_kbps: clampInt(form.talkback.opus_bitrate_kbps, 12, 128),
          opus_bitrate_mode: form.talkback.opus_bitrate_mode,
          frame_ms: clampInt(form.talkback.frame_ms, 5, 20),
          restricted_lowdelay: !!form.talkback.restricted_lowdelay,
        },
      };
      const r = await fetch("/api/config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      if (!r.ok) throw new Error(await r.text());
      await (window.AB.refreshConfig && window.AB.refreshConfig());
      setStatus("saved");
      setTimeout(() => setStatus("idle"), 1400);
    } catch (e) {
      console.error("[settings] save failed", e);
      setError(String(e.message || e));
      setStatus("error");
    }
  };

  const saveLabel = status === "saving" ? "saving..." : status === "saved" ? "saved" : status === "error" ? "error" : dirty ? "save settings" : "no changes";

  return (
    <div style={{ flex: 1, display: "flex", flexDirection: "column", minHeight: 0, padding: 12, gap: 12, overflow: "auto" }}>
      <Card
        title="Settings"
        hint={`${streams.length} streams - ${srtStreams} SRT / ${rtcStreams} WebRTC - ${overridden} overrides`}
        right={(
          <>
            {error && <span className="ab-mono" style={{ color: "var(--ab-err)", fontSize: 10.5, maxWidth: 360, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{error}</span>}
            <button className="ab-btn" data-variant="ghost" style={{ height: 22, fontSize: 11 }} onClick={onBack}>streams</button>
            <button className="ab-btn" data-variant={dirty ? "primary" : "ghost"} disabled={status === "saving"} style={{ height: 22, fontSize: 11 }} onClick={save}>{saveLabel}</button>
          </>
        )}
      >
        <div style={{ padding: 14, display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(340px, 1fr))", gap: 16 }}>
          <CfgSection label="Endpoint">
            <CfgField label="Name">
              <input value={form.endpoint_name} onChange={e => setForm(prev => ({ ...prev, endpoint_name: e.target.value }))} className="ab-mono" style={cfgInputStyle} />
            </CfgField>
            <CfgField label="Dante NIC">
              <NicSelect
                value={form.network.dante_nic}
                interfaces={networkInterfaces}
                onChange={v => updateNetwork("dante_nic", v)}
              />
            </CfgField>
            <CfgField label="WAN NIC">
              <NicSelect
                value={form.network.wan_nic}
                interfaces={networkInterfaces}
                onChange={v => updateNetwork("wan_nic", v)}
              />
            </CfgField>
          </CfgSection>

          <CfgSection label="Program defaults" hint="SRT streams inherit these encode settings">
            <CfgField label="Program path">
              <ToggleButton value={form.program.enabled} onChange={v => updateProgram("enabled", v)} />
            </CfgField>
            <CfgField label="SRT mode">
              <Segmented value={form.program.srt_mode} onChange={v => updateProgram("srt_mode", v)} options={[["listener", "Listen"], ["caller", "Call"], ["rendezvous", "Rendezvous"]]} />
            </CfgField>
            <CfgField label={form.program.srt_mode === "caller" ? "Peer port" : "Listen port"}>
              <NumberField value={form.network.srt_port} min={1} max={65535} onChange={v => updateNetwork("srt_port", v)} suffix="port" />
            </CfgField>
            <CfgField label="Latency">
              <NumberField value={form.program.srt_latency_ms} min={20} max={8000} onChange={v => updateProgram("srt_latency_ms", v)} suffix="ms" />
            </CfgField>
            <CfgField label="Bandwidth">
              <Segmented value={form.program.srt_bandwidth_mode} onChange={v => updateProgram("srt_bandwidth_mode", v)} options={[["auto", "Auto"], ["manual", "Manual"]]} />
            </CfgField>
            {form.program.srt_bandwidth_mode === "manual" && (
              <CfgField label="Max bw">
                <NumberField value={form.program.inbound_bandwidth_cap_kbps} min={64} max={100000} onChange={v => updateProgram("inbound_bandwidth_cap_kbps", v)} suffix="kbps" placeholder="none" />
              </CfgField>
            )}
            <CfgField label="Overhead">
              <NumberField
                value={form.program.srt_overhead_bandwidth_percent}
                min={0}
                max={100}
                step={1}
                onChange={v => updateProgram("srt_overhead_bandwidth_percent", v)}
                suffix="%"
              />
            </CfgField>
            <CfgField label="Encryption">
              <div style={{ display: "grid", gridTemplateColumns: "96px 1fr", gap: 6 }}>
                <ToggleButton value={form.program.encryption_enabled} onChange={v => updateProgram("encryption_enabled", v)} />
                <select value={form.program.encryption_strength} onChange={e => updateProgram("encryption_strength", e.target.value)} disabled={!form.program.encryption_enabled} className="ab-mono" style={{ ...cfgInputStyle, opacity: form.program.encryption_enabled ? 1 : 0.5 }}>
                  <option value="aes-128">aes-128</option>
                  <option value="aes-192">aes-192</option>
                  <option value="aes-256">aes-256</option>
                </select>
              </div>
            </CfgField>
            <CfgField label="Passphrase">
              <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
                <Chip tone={srtPassphraseSet ? "ok" : "warn"}>{srtPassphraseSet ? "generated" : "missing"}</Chip>
                <button
                  className="ab-btn"
                  data-variant={srtPassphraseSet ? "ghost" : "primary"}
                  disabled={keyStatus === "saving"}
                  onClick={rotateSrtPassphrase}
                  style={{ height: 22, fontSize: 11 }}
                  title="Generate and save a new SRT passphrase locally. Pairing bundles also rotate this automatically."
                >
                  {keyStatus === "saving" ? "rotating..." : keyStatus === "saved" ? "rotated" : "rotate"}
                </button>
                <button
                  className="ab-btn"
                  data-variant="ghost"
                  disabled={!srtPassphraseSet || copyStatus === "copying"}
                  onClick={copySrtPassphrase}
                  style={{ height: 22, fontSize: 11 }}
                  title="Copy the SRT passphrase to the clipboard without showing it on screen."
                >
                  {copyStatus === "copying" ? "copying..." : copyStatus === "copied" ? "copied" : "copy"}
                </button>
              </div>
            </CfgField>
            <CfgField label="Clock">
              <Segmented value={form.program.clock_recovery_mode} onChange={v => updateProgram("clock_recovery_mode", v)} options={[["adaptive", "Adaptive"], ["free_running", "Free run"]]} />
            </CfgField>
            {form.program.clock_recovery_mode === "free_running" && (
              <CfgField label="Buffer">
                <NumberField
                  value={form.program.free_running_clock.jitter_buffer_ms}
                  min={20}
                  max={5000}
                  step={10}
                  onChange={v => updateProgram("free_running_clock", { ...form.program.free_running_clock, jitter_buffer_ms: v })}
                  suffix="ms"
                />
              </CfgField>
            )}
            <OpusFields opus={form.program.opus} disabled={false} onChange={opus => updateProgram("opus", opus)} />
          </CfgSection>

          <CfgSection label="Voice / Comms" hint="WebRTC streams inherit these encode settings">
            <CfgField label="Comms path">
              <ToggleButton value={form.talkback.enabled} onChange={v => updateTalkback("enabled", v)} />
            </CfgField>
            <CfgField label="Peer">
              <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
                <Chip tone={paired ? "ok" : "warn"}>{paired ? "paired" : "unpaired"}</Chip>
                <span className="ab-mono" style={{ ...cfgVal, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{peerLabel}</span>
              </div>
            </CfgField>
            <CfgField label="WAN hint">
              <input
                value={form.network.public_address}
                onChange={e => updateNetwork("public_address", e.target.value)}
                placeholder="auto / hostname / IP"
                title="Optional override for pairing bundles. WebRTC ICE still uses STUN/TURN candidates."
                className="ab-mono"
                style={cfgInputStyle}
              />
            </CfgField>
            <CfgField label="Signaling">
              <span
                className="ab-mono"
                style={cfgVal}
                title="Read-only observed control/signaling endpoint. Later this can show STUN/NAT-discovered public reachability."
              >
                {observedSignaling}
              </span>
            </CfgField>
            <CfgField label="Pairing">
              <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                <button className="ab-btn" data-variant="primary" disabled={pairingStatus === "saving"} onClick={generatePairingBundle} style={{ height: 22, fontSize: 11 }}>
                  {pairingStatus === "copied" ? "copied invite" : pairingStatus === "ready" ? "invite ready" : "create invite"}
                </button>
                <button className="ab-btn" data-variant="ghost" disabled={!paired || pairingStatus === "saving"} onClick={clearPairing} style={{ height: 22, fontSize: 11 }}>
                  {pairingStatus === "cleared" ? "cleared" : "clear"}
                </button>
              </div>
            </CfgField>
            <CfgField label="Our invite">
              <textarea
                value={inviteText}
                readOnly
                placeholder="create invite to generate bundle for remote endpoint"
                className="ab-mono"
                style={{ ...cfgInputStyle, height: 56, paddingTop: 6, resize: "vertical", lineHeight: 1.35 }}
              />
            </CfgField>
            <CfgField label="Accept invite">
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <textarea
                  value={acceptInviteText}
                  onChange={e => setAcceptInviteText(e.target.value)}
                  placeholder="paste invite from remote endpoint"
                  className="ab-mono"
                  style={{ ...cfgInputStyle, height: 56, paddingTop: 6, resize: "vertical", lineHeight: 1.35 }}
                />
                <button className="ab-btn" data-variant={acceptInviteText.trim() ? "primary" : "ghost"} disabled={!acceptInviteText.trim() || pairingStatus === "saving"} onClick={applyPairingBundle} style={{ height: 22, fontSize: 11, alignSelf: "flex-start" }}>
                  {pairingStatus === "paired" ? "paired" : "apply invite"}
                </button>
              </div>
            </CfgField>
            <CfgField label="Bitrate">
              <NumberField value={form.talkback.opus_bitrate_kbps} min={12} max={128} step={4} onChange={v => updateTalkback("opus_bitrate_kbps", v)} suffix="kbps" />
            </CfgField>
            <CfgField label="Rate mode">
              <Segmented value={form.talkback.opus_bitrate_mode} onChange={v => updateTalkback("opus_bitrate_mode", v)} options={OPUS_RATE_MODES} />
            </CfgField>
            <CfgField label="Frame">
              <NumberField value={form.talkback.frame_ms} min={5} max={20} onChange={v => updateTalkback("frame_ms", v)} suffix="ms" />
            </CfgField>
            <CfgField label="Low delay">
              <ToggleButton value={form.talkback.restricted_lowdelay} onChange={v => updateTalkback("restricted_lowdelay", v)} />
            </CfgField>
            <CfgField label="Applies to">
              <span className="ab-mono" style={cfgVal}>{rtcStreams} WebRTC stream(s)</span>
            </CfgField>
          </CfgSection>

        </div>
      </Card>
    </div>
  );
}

function ToggleButton({ value, onChange }) {
  return (
    <button className="ab-btn" data-variant={value ? "primary" : "ghost"} onClick={() => onChange(!value)} style={{ height: 22, fontSize: 11, minWidth: 72 }}>
      {value ? "on" : "off"}
    </button>
  );
}

function NicSelect({ value, interfaces, onChange }) {
  const names = (interfaces || []).map(i => i.name);
  const includeCurrent = value && !names.includes(value);
  return (
    <select value={value || ""} onChange={e => onChange(e.target.value)} className="ab-mono" style={cfgInputStyle}>
      <option value="">unselected</option>
      {includeCurrent && <option value={value}>{value}</option>}
      {(interfaces || []).map(iface => (
        <option key={iface.name} value={iface.name}>
          {iface.name}{iface.ip_address ? ` - ${iface.ip_address}` : ""}{iface.description ? ` - ${iface.description}` : ""}
        </option>
      ))}
    </select>
  );
}

function NumberField({ value, min, max, step = 1, onChange, suffix, placeholder }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={value}
        placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
        className="ab-mono"
        style={{ ...cfgInputStyle, width: 86 }}
      />
      {suffix && <span className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-fg-4)" }}>{suffix}</span>}
    </div>
  );
}

function clampInt(value, min, max) {
  const n = parseInt(value, 10);
  if (!Number.isFinite(n)) return min;
  return Math.max(min, Math.min(max, n));
}

function emptyToNull(value) {
  const s = String(value || "").trim();
  return s ? s : null;
}

function getObservedSignalingEndpoint(cfg) {
  const proto = window.location.protocol === "https:" ? "wss" : "ws";
  const host = (cfg.network && cfg.network.public_address) || window.location.hostname || "localhost";
  const port = (cfg.network && cfg.network.signaling_port)
            || window.location.port
            || (window.location.protocol === "https:" ? 443 : 80);
  return `${proto}://${host}:${port}/api/ws/signaling`;
}

async function writeClipboardText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    try {
      await navigator.clipboard.writeText(text);
      return;
    } catch (_) {
      // Fall through to the legacy copy path. Some embedded browser
      // surfaces report secure context but still deny async clipboard.
    }
  }
  const node = document.createElement("textarea");
  node.value = text;
  node.setAttribute("readonly", "");
  node.style.position = "fixed";
  node.style.left = "-9999px";
  node.style.top = "0";
  document.body.appendChild(node);
  node.select();
  const ok = document.execCommand("copy");
  document.body.removeChild(node);
  if (!ok) throw new Error("clipboard write failed");
}

window.VariationA = VariationA;

const TYPE_META = {
  "PGM":  { tone: "acc",   title: "Program feed" },
  "PL":   { tone: "info",  title: "Party line · 2-way voice" },
  "IFB":  { tone: "info",  title: "Interruptible foldback · 1-way voice" },
  "SRC":  { tone: "muted", title: "Source feed" },
  "BUS":  { tone: "muted", title: "Submix bus" },
  "AUX":  { tone: "muted", title: "Aux send" },
  "TONE": { tone: "ok",    title: "Test tone" },
};
function TypeChip({ type, transport }) {
  const meta = TYPE_META[type] || { tone: "muted", title: type };
  const fg = meta.tone === "acc"  ? "var(--ab-accent)"
           : meta.tone === "info" ? "var(--ab-info)"
           : meta.tone === "ok"   ? "var(--ab-ok)"
           : "var(--ab-fg-3)";
  const bg = meta.tone === "acc"  ? "var(--ab-accent-soft)"
           : meta.tone === "info" ? "var(--ab-info-soft)"
           : meta.tone === "ok"   ? "var(--ab-ok-soft)"
           : "var(--ab-surface-2)";
  const bd = meta.tone === "acc"  ? "var(--ab-accent-line)"
           : meta.tone === "info" ? "rgba(61,165,255,0.35)"
           : meta.tone === "ok"   ? "rgba(34,197,94,0.35)"
           : "var(--ab-border-soft)";
  return (
    <span title={meta.title} style={{
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      height: 16, padding: "0 6px", minWidth: 44,
      fontFamily: "var(--ab-mono)", fontSize: 10, letterSpacing: 0.04,
      borderRadius: 2, color: fg, background: bg, border: "1px solid " + bd,
    }}>{type}</span>
  );
}
function StateChip({ state }) {
  const meta = state === "active" ? { fg: "var(--ab-ok)",   bg: "var(--ab-ok-soft)",   bd: "rgba(34,197,94,0.35)",  label: "ACTIVE" }
             : state === "warn"   ? { fg: "var(--ab-warn)", bg: "var(--ab-warn-soft)", bd: "rgba(245,158,11,0.35)", label: "WARN"   }
             : state === "err"    ? { fg: "var(--ab-err)",  bg: "var(--ab-err-soft)",  bd: "rgba(239,68,68,0.35)",  label: "ERR"    }
             : state === "muted"  ? { fg: "var(--ab-fg-3)", bg: "var(--ab-surface-2)", bd: "var(--ab-border-soft)", label: "MUTE"   }
             :                      { fg: "var(--ab-fg-5)", bg: "var(--ab-surface-2)", bd: "var(--ab-border-soft)", label: "IDLE"   };
  return (
    <span title={`stream state: ${state}`} style={{
      display: "inline-flex", alignItems: "center", justifyContent: "center",
      height: 16, padding: "0 6px", minWidth: 50,
      fontFamily: "var(--ab-mono)", fontSize: 9.5, letterSpacing: 0.05,
      borderRadius: 2, color: meta.fg, background: meta.bg, border: "1px solid " + meta.bd,
    }}>{meta.label}</span>
  );
}

function IconBtn({ tone = "ghost", title, disabled, onClick, children }) {
  const fg = disabled ? "var(--ab-fg-5)"
    : tone === "acc"  ? "var(--ab-accent)"
    : tone === "info" ? "var(--ab-info)"
    : tone === "err"  ? "var(--ab-err)"
    : tone === "ok"   ? "var(--ab-ok)"
    :                   "var(--ab-fg-2)";
  return (
    <button title={title} disabled={disabled} onClick={onClick} style={{
      width: 18, height: 18, padding: 0, display: "inline-flex", alignItems: "center", justifyContent: "center",
      color: fg, background: "var(--ab-surface-2)", border: "1px solid var(--ab-border-soft)",
      borderRadius: 2, cursor: disabled ? "not-allowed" : "pointer", opacity: disabled ? 0.5 : 1,
    }}>{children}</button>
  );
}

const ActIcon = {
  play:    () => <svg width="9" height="9" viewBox="0 0 10 10"><polygon points="2,1.5 2,8.5 8.5,5" fill="currentColor"/></svg>,
  stop:    () => <svg width="8" height="8" viewBox="0 0 10 10"><rect x="2" y="2" width="6" height="6" fill="currentColor"/></svg>,
  mic:     () => <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.2"><rect x="4.5" y="1.5" width="3" height="6" rx="1.5"/><path d="M3 6.5a3 3 0 006 0"/><line x1="6" y1="9.5" x2="6" y2="11"/></svg>,
  listen:  () => <svg width="11" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.2"><path d="M2 7V5a4 4 0 018 0v2"/><rect x="1.5" y="7" width="2.5" height="3.5" rx="0.6" fill="currentColor" stroke="none"/><rect x="8" y="7" width="2.5" height="3.5" rx="0.6" fill="currentColor" stroke="none"/></svg>,
  more:    () => <svg width="10" height="10" viewBox="0 0 12 12"><circle cx="3" cy="6" r="1" fill="currentColor"/><circle cx="6" cy="6" r="1" fill="currentColor"/><circle cx="9" cy="6" r="1" fill="currentColor"/></svg>,
};

function RowActions({ ch, expanded, onToggle }) {
  const isVoice  = ch.type === "PL" || ch.type === "IFB" || ch.type === "TB";
  const running  = ch.state !== "idle";
  const canTalk  = isVoice && ch.direction === "out";
  return (
    <div style={{ display: "inline-flex", gap: 3 }}>
      {running
        ? <IconBtn tone="err" title="Stop stream"><ActIcon.stop /></IconBtn>
        : <IconBtn tone="acc" title="Start stream"><ActIcon.play /></IconBtn>}
      <IconBtn tone="info" title="Listen / monitor locally"><ActIcon.listen /></IconBtn>
      {canTalk && (
        <IconBtn tone="info" title="Push to talk"><ActIcon.mic /></IconBtn>
      )}
      <IconBtn tone={expanded ? "acc" : "ghost"} title={expanded ? "Hide settings" : "Stream settings"} onClick={onToggle}><ActIcon.more /></IconBtn>
    </div>
  );
}

function ClockChip({ sync, ppm }) {
  const meta = sync === "lock"  ? { fg: "var(--ab-ok)",   bg: "var(--ab-ok-soft)",     bd: "rgba(34,197,94,0.35)",  label: "LOCK"  }
             : sync === "slew"  ? { fg: "var(--ab-warn)", bg: "var(--ab-warn-soft)",   bd: "rgba(245,158,11,0.35)", label: "SLEW"  }
             : sync === "drift" ? { fg: "var(--ab-err)",  bg: "var(--ab-err-soft)",    bd: "rgba(239,68,68,0.35)",  label: "DRIFT" }
             :                    { fg: "var(--ab-fg-5)", bg: "var(--ab-surface-2)",   bd: "var(--ab-border-soft)", label: "—"     };
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <span style={{
        display: "inline-flex", alignItems: "center", justifyContent: "center",
        height: 14, padding: "0 5px", fontFamily: "var(--ab-mono)", fontSize: 9.5, letterSpacing: 0.05,
        borderRadius: 2, color: meta.fg, background: meta.bg, border: "1px solid " + meta.bd,
      }}>{meta.label}</span>
      {ppm != null && sync !== "off" && (
        <span className="ab-mono" style={{ fontSize: 10, color: "var(--ab-fg-4)" }}>
          {ppm >= 0 ? "+" : ""}{Math.abs(ppm) < 1 ? ppm.toFixed(2) : ppm.toFixed(1)}
        </span>
      )}
    </span>
  );
}

// Codec / resampling clock status — replaces the old CPU tile in the
// hero KPI strip. One indicator for the bridge's single shared resampler
// PLL: lock state, ppm offset, codec, sample rate, clock domain.
function ClockKpiTile({ sync = "lock", ppm = 0, codec = "OPUS 256k", sampleRate = "48.000 kHz", note }) {
  const tone = sync === "lock" ? "ok" : sync === "slew" ? "warn" : sync === "drift" ? "err" : "muted";
  return (
    <div className="ab-card" style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
        <span className="ab-kpi-label" title="Single shared resampler PLL — one clock domain for all interleaved channels on this bridge">Codec · Resampler PLL</span>
        <ClockChip sync={sync} ppm={null} />
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="ab-kpi-value">
          {ppm >= 0 ? "+" : "−"}{Math.abs(ppm) < 1 ? Math.abs(ppm).toFixed(2) : Math.abs(ppm).toFixed(1)}
          <span className="ab-kpi-unit"> ppm</span>
        </span>
        <span className="ab-kpi-delta" style={{ color: `var(--ab-${tone === "muted" ? "fg-3" : tone})` }}>
          {sync.toUpperCase()}
        </span>
      </div>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 2 }}>
        <span style={{
          display: "inline-flex", alignItems: "center", height: 16, padding: "0 6px",
          fontFamily: "var(--ab-mono)", fontSize: 10, letterSpacing: 0.04, borderRadius: 2,
          color: "var(--ab-accent)", background: "var(--ab-accent-soft)",
          border: "1px solid var(--ab-accent-line)",
        }}>{codec}</span>
        <span className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-fg-3)" }}>{sampleRate}</span>
      </div>
      {note && <div style={{ fontSize: 10.5, color: "var(--ab-fg-4)", fontFamily: "var(--ab-mono)" }}>{note}</div>}
    </div>
  );
}

const STREAM_TYPES = [
  { value: "PGM",  label: "PGM" },
  { value: "PL",   label: "PL" },
  { value: "IFB",  label: "IFB" },
  { value: "SRC",  label: "SRC" },
  { value: "BUS",  label: "BUS" },
  { value: "AUX",  label: "AUX" },
  { value: "TONE", label: "TONE" },
];

const OPUS_RATE_MODES = [["cbr", "CBR"], ["cvbr", "CVBR"], ["vbr", "VBR"]];

// Suggested OPUS defaults per transport. SRT-side gets the music-grade
// program defaults; WebRTC-side gets the low-latency voice defaults.
function defaultOpus(transport) {
  if (transport === "webrtc") {
    return { bitrate_kbps: 48, bitrate_mode: "cbr", frame_ms: 10, complexity: 5, inband_fec: true, expected_packet_loss_percent: 5 };
  }
  return   { bitrate_kbps: 96, bitrate_mode: "cbr", frame_ms: 20, complexity: 7, inband_fec: true, expected_packet_loss_percent: 5 };
}

function AddStreamPanel({ onClose }) {
  const cfg = window.AB.config || {};
  const streams = (cfg.audio && cfg.audio.streams) || [];
  const channelCount = (cfg.audio && cfg.audio.channel_count) || 0;
  const max = 128;
  const atCap = streams.length >= max;

  const [name, setName]           = useState("");
  const [transport, setTransport] = useState("srt");
  const [type, setType]           = useState("PGM");
  const [dir,  setDir]            = useState("tx");
  const [dante, setDante]         = useState("");
  const [opus, setOpus]           = useState(defaultOpus("srt"));
  const [opusOverride, setOpusOverride] = useState(false);
  const [status, setStatus]       = useState("idle");
  const [error, setError]         = useState(null);

  // When transport flips, reset OPUS suggestion if the operator hasn't
  // explicitly diverged from the bridge default.
  useEffect(() => {
    if (!opusOverride) setOpus(defaultOpus(transport));
  }, [transport, opusOverride]);

  const trimmed = name.trim();
  const danteN = dante === "" ? null : Math.max(1, Math.min(64, parseInt(dante, 10) || 0));

  const submit = async () => {
    console.log("[add-stream] submit click", { trimmed, atCap, type, transport, dir, danteN, opusOverride });
    if (!trimmed) { setError("name is required"); setStatus("error"); return; }
    if (atCap)    { setError("at stream cap");    setStatus("error"); return; }
    setStatus("saving"); setError(null);
    try {
      const stream = {
        name: trimmed, type, transport, direction: dir,
        ...(danteN ? { dante_channel: danteN } : {}),
        ...(opusOverride ? { opus } : {}),
      };
      const next = streams.concat([stream]);
      const patch = { audio: { streams: next } };
      if (danteN && danteN > channelCount) patch.audio.channel_count = danteN;
      console.log("[add-stream] PATCH /api/config", patch);
      const r = await fetch("/api/config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
      });
      const text = await r.text();
      console.log("[add-stream] response", r.status, text.slice(0, 400));
      if (!r.ok) throw new Error(text);
      await (window.AB.refreshConfig && window.AB.refreshConfig());
      setName(""); setDante(""); setOpusOverride(false);
      setStatus("idle");
      onClose();
    } catch (e) {
      console.error("[add-stream] failed", e);
      setError(String(e.message || e));
      setStatus("error");
    }
  };

  return (
    <div style={{ padding: "12px 14px 14px", background: "var(--ab-surface-2)", borderBottom: "1px solid var(--ab-border)", display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-3)" }}>
          New stream
        </span>
        <span className="ab-mono" style={{ fontSize: 10, color: "var(--ab-fg-5)" }}>
          {atCap ? `at stream cap (${max})` : `slot ${streams.length + 1} of ${max}`}
        </span>
        <button className="ab-btn" data-variant="ghost" onClick={onClose} style={{ marginLeft: "auto", height: 22, fontSize: 11 }}>cancel</button>
        <button className="ab-btn" data-variant="primary" disabled={!trimmed || atCap || status === "saving"} onClick={submit} style={{ height: 22, fontSize: 11 }}>
          {status === "saving" ? "adding…" : "add stream"}
        </button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 14 }}>
        <CfgSection label="Identity">
          <CfgField label="Name">
            <input
              autoFocus
              value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") submit(); if (e.key === "Escape") onClose(); }}
              placeholder="e.g. PGM L, Caller 1 mic, IFB Talent A"
              className="ab-mono"
              style={cfgInputStyle}
            />
          </CfgField>
          <CfgField label="Type">
            <select value={type} onChange={e => setType(e.target.value)} className="ab-mono" style={cfgInputStyle}>
              {STREAM_TYPES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </CfgField>
          <CfgField label="Direction">
            <Segmented value={dir} onChange={setDir} options={[["tx", "TX"], ["rx", "RX"]]} />
          </CfgField>
          <CfgField label="Dante ch">
            <input
              value={dante}
              onChange={e => setDante(e.target.value.replace(/[^0-9]/g, ""))}
              placeholder="optional · 1–64"
              className="ab-mono"
              style={cfgInputStyle}
              title={dir === "tx" ? "Local Dante input channel feeding this stream" : "Local Dante output channel this stream lands on"}
            />
          </CfgField>
        </CfgSection>

        <CfgSection label="Transport" hint="how this stream rides the WAN">
          <CfgField label="Path">
            <Segmented
              value={transport}
              onChange={setTransport}
              options={[["srt", "SRT"], ["webrtc", "WebRTC"]]}
            />
          </CfgField>
          {transport === "srt" ? (
            <>
              <CfgField label="SRT mode"><span className="ab-mono" style={cfgVal}>{(cfg.program && cfg.program.srt_mode) || "listener"}</span></CfgField>
              <CfgField label="SRT port"><span className="ab-mono" style={cfgVal}>{(cfg.network && cfg.network.srt_port) || 9000}</span></CfgField>
              <CfgField label="Latency"><span className="ab-mono" style={cfgVal}>{(cfg.program && cfg.program.srt_latency_ms) || 240} ms</span></CfgField>
              <CfgField label="Encryption"><span className="ab-mono" style={cfgVal}>{(cfg.program && cfg.program.encryption_enabled) ? ((cfg.program && cfg.program.encryption_strength) || "on") : "off"}</span></CfgField>
            </>
          ) : (
            <>
              <CfgField label="STUN"><span className="ab-mono" style={cfgVal}>{((cfg.network && cfg.network.stun_servers) || []).length} server(s)</span></CfgField>
              <CfgField label="TURN"><span className="ab-mono" style={cfgVal}>{(cfg.network && cfg.network.turn_server) || "—"}</span></CfgField>
              <CfgField label="Encryption"><span className="ab-mono" style={cfgVal}>DTLS-SRTP (mandatory)</span></CfgField>
            </>
          )}
        </CfgSection>

        <CfgSection
          label="OPUS codec"
          hint={opusOverride ? "per-stream override" : "inheriting bridge defaults"}
        >
          <div style={{ display: "flex", alignItems: "center", gap: 6, paddingBottom: 2 }}>
            <button className="ab-btn" data-variant={opusOverride ? "primary" : "ghost"} onClick={() => setOpusOverride(v => !v)} style={{ height: 22, fontSize: 11 }}>
              {opusOverride ? "using override" : "override defaults"}
            </button>
            {opusOverride && (
              <button className="ab-btn" data-variant="ghost" onClick={() => setOpus(defaultOpus(transport))} style={{ height: 22, fontSize: 11 }}>
                reset
              </button>
            )}
          </div>
          <OpusFields opus={opus} disabled={!opusOverride} onChange={setOpus} />
        </CfgSection>
      </div>

      <div className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-fg-5)", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <span>preview:</span>
        <TypeChip type={type} transport={transport === "srt" ? "SRT" : "WebRTC"} />
        <span style={{ color: dir === "tx" ? "var(--ab-accent)" : "var(--ab-info)" }}>{dir.toUpperCase()}</span>
        <span>· {transport === "srt" ? "SRT" : "WebRTC"}</span>
        <span>· OPUS {opus.bitrate_kbps}k / {opus.frame_ms}ms</span>
        {danteN && <span>· Dante ch {danteN}</span>}
      </div>
      {error && <div className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-err)" }}>error: {error}</div>}
    </div>
  );
}

function Segmented({ value, onChange, options }) {
  return (
    <div style={{ display: "flex", padding: 2, background: "var(--ab-surface)", border: "1px solid var(--ab-border-soft)", borderRadius: 4, minWidth: 0, maxWidth: "100%", overflow: "hidden" }}>
      {options.map(([k, lbl]) => (
        <button key={k} onClick={() => onChange(k)} className="ab-btn"
                data-variant={value === k ? "primary" : "ghost"}
                style={{ height: 22, padding: "0 10px", fontSize: 11, flex: "1 1 0", minWidth: 0, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis",
                         ...(value !== k ? { background: "transparent", border: "1px solid transparent", color: "var(--ab-fg-3)" } : {}) }}>
          {lbl}
        </button>
      ))}
    </div>
  );
}

function OpusFields({ opus, disabled, onChange }) {
  const set = (k, v) => onChange({ ...opus, [k]: v });
  const numStyle = { ...cfgInputStyle, opacity: disabled ? 0.5 : 1 };
  return (
    <>
      <CfgField label="Bitrate">
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input type="number" min={16} max={512} step={8}
                 value={opus.bitrate_kbps} disabled={disabled}
                 onChange={e => set("bitrate_kbps", Math.max(16, Math.min(512, parseInt(e.target.value, 10) || 0)))}
                 className="ab-mono" style={{ ...numStyle, width: 72 }} />
          <span className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-fg-4)" }}>kbps</span>
        </div>
      </CfgField>
      <CfgField label="Rate mode">
        <div style={{ opacity: disabled ? 0.5 : 1, pointerEvents: disabled ? "none" : "auto" }}>
          <Segmented value={opus.bitrate_mode || "cbr"} onChange={v => set("bitrate_mode", v)} options={OPUS_RATE_MODES} />
        </div>
      </CfgField>
      <CfgField label="Frame">
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input type="number" min={2} max={60} step={1}
                 value={opus.frame_ms} disabled={disabled}
                 onChange={e => set("frame_ms", Math.max(2, Math.min(60, parseInt(e.target.value, 10) || 0)))}
                 className="ab-mono" style={{ ...numStyle, width: 56 }} />
          <span className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-fg-4)" }}>ms</span>
        </div>
      </CfgField>
      <CfgField label="Complexity">
        <input type="number" min={0} max={10} step={1}
               value={opus.complexity} disabled={disabled}
               onChange={e => set("complexity", Math.max(0, Math.min(10, parseInt(e.target.value, 10) || 0)))}
               className="ab-mono" style={{ ...numStyle, width: 56 }} />
      </CfgField>
      <CfgField label="In-band FEC">
        <button className="ab-btn" disabled={disabled}
                data-variant={opus.inband_fec ? "primary" : "ghost"}
                onClick={() => set("inband_fec", !opus.inband_fec)}
                style={{ height: 22, fontSize: 11, opacity: disabled ? 0.5 : 1 }}>
          {opus.inband_fec ? "on" : "off"}
        </button>
      </CfgField>
      <CfgField label="Expected loss">
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <input type="number" min={0} max={30} step={1}
                 value={opus.expected_packet_loss_percent} disabled={disabled}
                 onChange={e => set("expected_packet_loss_percent", Math.max(0, Math.min(30, parseInt(e.target.value, 10) || 0)))}
                 className="ab-mono" style={{ ...numStyle, width: 56 }} />
          <span className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-fg-4)" }}>%</span>
        </div>
      </CfgField>
    </>
  );
}

function StreamConfig({ ch, onClose }) {
  const cfg = window.AB.config || {};
  const srtLatency = (cfg.program && cfg.program.srt_latency_ms) || 240;
  const clockMode  = (cfg.program && cfg.program.clock_recovery_mode) || "adaptive";

  const streams = (cfg.audio && cfg.audio.streams) || [];
  const idx = ch.id - 1;
  const streamCfg = streams[idx] || {
    name: ch.name, type: ch.type, transport: ch.transport === "WebRTC" ? "webrtc" : "srt",
    direction: ch.direction === "in" ? "rx" : "tx", dante_channel: ch.dante_channel, opus: null,
  };

  const [label, setLabel]         = useState(streamCfg.name);
  const [type,  setType]          = useState(streamCfg.type);
  const [transport, setTransport] = useState(streamCfg.transport || "srt");
  const [dir,   setDir]           = useState(streamCfg.direction);
  const [dante, setDante]         = useState(streamCfg.dante_channel ? String(streamCfg.dante_channel) : "");
  const [opusOverride, setOpusOverride] = useState(!!streamCfg.opus);
  const [opus, setOpus]           = useState(streamCfg.opus || defaultOpus(streamCfg.transport || "srt"));
  const [labelStatus, setLabelStatus] = useState("idle");

  const danteN = dante === "" ? null : Math.max(1, Math.min(64, parseInt(dante, 10) || 0));

  const dirty = label !== streamCfg.name
             || type !== streamCfg.type
             || transport !== (streamCfg.transport || "srt")
             || dir !== streamCfg.direction
             || danteN !== (streamCfg.dante_channel || null)
             || (opusOverride !== !!streamCfg.opus)
             || (opusOverride && JSON.stringify(opus) !== JSON.stringify(streamCfg.opus));

  const saveLabel = async () => {
    setLabelStatus("saving");
    try {
      const next = streams.slice();
      next[idx] = {
        name: label, type, transport, direction: dir,
        ...(danteN ? { dante_channel: danteN } : {}),
        ...(opusOverride ? { opus } : {}),
        enabled: streamCfg.enabled !== false,
      };
      const body = { audio: { streams: next } };
      const currentChannelCount = (cfg.audio && cfg.audio.channel_count) || 0;
      if (danteN && danteN > currentChannelCount) body.audio.channel_count = danteN;
      console.log("[stream-config] PATCH /api/config", body);
      const r = await fetch("/api/config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const text = await r.text();
      console.log("[stream-config] response", r.status, text.slice(0, 400));
      if (!r.ok) throw new Error(text);
      setLabelStatus("saved");
      window.AB.refreshConfig && window.AB.refreshConfig();
      setTimeout(() => setLabelStatus("idle"), 1500);
    } catch (e) {
      console.error("[stream-config] save failed", e);
      setLabelStatus("error");
    }
  };

  const duplicateStream = async () => {
    setLabelStatus("saving");
    try {
      // Use the in-flight (possibly dirty) form values so the duplicate
      // matches what the operator sees, not the stale persisted version.
      const clone = {
        name: `${label} (copy)`, type, transport, direction: dir,
        ...(danteN ? { dante_channel: danteN } : {}),
        ...(opusOverride ? { opus } : {}),
        enabled: streamCfg.enabled !== false,
      };
      const next = streams.slice();
      next.splice(idx + 1, 0, clone);
      const r = await fetch("/api/config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ audio: { streams: next } }),
      });
      if (!r.ok) throw new Error(await r.text());
      window.AB.refreshConfig && window.AB.refreshConfig();
      setLabelStatus("saved");
      setTimeout(() => setLabelStatus("idle"), 1500);
    } catch (e) {
      console.error("[stream-config] duplicate failed", e);
      setLabelStatus("error");
    }
  };

  const removeStream = async () => {
    setLabelStatus("saving");
    try {
      const next = streams.slice();
      next.splice(idx, 1);
      const r = await fetch("/api/config", {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ audio: { streams: next } }),
      });
      if (!r.ok) throw new Error(await r.text());
      window.AB.refreshConfig && window.AB.refreshConfig();
      onClose();
    } catch (e) {
      console.error("[stream-config] remove failed", e);
      setLabelStatus("error");
    }
  };

  const startTone = () => fetch("/api/diagnostics/tone", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ frequency_hz: 1000, level_dbfs: -18, channel: ch.id, waveform: "sine" }),
  });
  const startMonitor = () => fetch("/api/diagnostics/monitor", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ channel: ch.id, is_input: ch.direction === "in" }),
  });
  const startLoopback = () => fetch("/api/diagnostics/loopback", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ input_channels: [ch.id], output_channels: [ch.id] }),
  });

  return (
    <div style={{ padding: "14px 16px 16px", display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-3)" }}>
          Stream {String(ch.id).padStart(2, "0")} settings
        </span>
        <TypeChip type={ch.type} transport={ch.transport} />
        <span className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-fg-4)" }}>
          {ch.direction === "in" ? "RX · peer → us" : "TX · us → peer"} · {ch.transport} · {ch.route}
        </span>
        <button className="ab-btn" data-variant="ghost" style={{ marginLeft: "auto", height: 22, fontSize: 11 }} onClick={onClose}>close</button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 14 }}>
        <CfgSection label="Identity">
          <CfgField label="Name">
            <input value={label} onChange={e => setLabel(e.target.value)} className="ab-mono" style={cfgInputStyle} />
          </CfgField>
          <CfgField label="Type">
            <select value={type} onChange={e => setType(e.target.value)} className="ab-mono" style={cfgInputStyle}>
              {STREAM_TYPES.map(o => <option key={o.value} value={o.value}>{o.label}</option>)}
            </select>
          </CfgField>
          <CfgField label="Direction">
            <Segmented value={dir} onChange={setDir} options={[["tx","TX"], ["rx","RX"]]} />
          </CfgField>
          <CfgField label="Dante ch">
            <input value={dante} onChange={e => setDante(e.target.value.replace(/[^0-9]/g, ""))}
                   placeholder="1–64" className="ab-mono" style={cfgInputStyle} />
          </CfgField>
          <CfgField label="Route"><span className="ab-mono" style={cfgVal}>{ch.route}</span></CfgField>
        </CfgSection>

        <CfgSection label="Transport" hint="how this stream rides the WAN">
          <CfgField label="Path">
            <Segmented value={transport} onChange={setTransport}
                       options={[["srt", "SRT"], ["webrtc", "WebRTC"]]} />
          </CfgField>
          {transport === "srt" ? (
            <>
              <CfgField label="Slot">
                <span className="ab-mono" style={cfgVal} title="Encoder/decoder slot in the per-direction multichannel SRT multiplex. TX and RX slot indices are independent. Assigned in list order.">
                  {ch.srt_slot != null ? `SRT/${String(ch.srt_slot).padStart(2, "0")}  (${dir.toUpperCase()})` : "—"}
                </span>
              </CfgField>
              <CfgField label="SRT mode"><span className="ab-mono" style={cfgVal}>{(cfg.program && cfg.program.srt_mode) || "—"}</span></CfgField>
              <CfgField label="SRT port"><span className="ab-mono" style={cfgVal}>{(cfg.network && cfg.network.srt_port) || "—"}</span></CfgField>
              <CfgField label="Latency"><span className="ab-mono" style={cfgVal}>{srtLatency} ms</span></CfgField>
              <CfgField label="Encryption"><span className="ab-mono" style={cfgVal}>{(cfg.program && cfg.program.encryption_enabled) ? ((cfg.program && cfg.program.encryption_strength) || "on") : "off"}</span></CfgField>
              <CfgField label="Clock recovery"><span className="ab-mono" style={cfgVal}>{clockMode}</span></CfgField>
            </>
          ) : (
            <>
              <CfgField label="Track">
                <span className="ab-mono" style={cfgVal} title="OPUS track index within the WebRTC peer connection. TX and RX track indices are independent. Assigned in list order.">
                  {ch.rtc_track != null ? `WRTC/${String(ch.rtc_track).padStart(2, "0")}  (${dir.toUpperCase()})` : "—"}
                </span>
              </CfgField>
              <CfgField label="STUN"><span className="ab-mono" style={cfgVal}>{((cfg.network && cfg.network.stun_servers) || []).length} server(s)</span></CfgField>
              <CfgField label="TURN"><span className="ab-mono" style={cfgVal}>{(cfg.network && cfg.network.turn_server) || "—"}</span></CfgField>
              <CfgField label="Encryption"><span className="ab-mono" style={cfgVal}>DTLS-SRTP (mandatory)</span></CfgField>
            </>
          )}
        </CfgSection>

        <CfgSection label="OPUS codec" hint={opusOverride ? "per-stream override" : "inheriting bridge defaults"}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, paddingBottom: 2 }}>
            <button className="ab-btn" data-variant={opusOverride ? "primary" : "ghost"} onClick={() => setOpusOverride(v => !v)} style={{ height: 22, fontSize: 11 }}>
              {opusOverride ? "using override" : "override defaults"}
            </button>
            {opusOverride && (
              <button className="ab-btn" data-variant="ghost" onClick={() => setOpus(defaultOpus(transport))} style={{ height: 22, fontSize: 11 }}>reset</button>
            )}
          </div>
          <OpusFields opus={opus} disabled={!opusOverride} onChange={setOpus} />
        </CfgSection>
      </div>

      {/* Unified action bar — diagnostics on the left, save/delete on the right. */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, paddingTop: 10, borderTop: "1px solid var(--ab-border-soft)", flexWrap: "wrap" }}>
        <span className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-3)", marginRight: 4 }}>Diagnostics</span>
        <button className="ab-btn" style={{ height: 24, fontSize: 11 }} onClick={startTone}>tone → ch {ch.id}</button>
        <button className="ab-btn" style={{ height: 24, fontSize: 11 }} onClick={startMonitor}>monitor</button>
        <button className="ab-btn" style={{ height: 24, fontSize: 11 }} onClick={startLoopback}>loopback {ch.id}↔{ch.id}</button>

        <div style={{ flex: 1 }} />

        <button className="ab-btn" onClick={duplicateStream} style={{ height: 24, fontSize: 11 }} title="Duplicate this stream below">duplicate</button>
        <button className="ab-btn" data-variant="danger" onClick={removeStream} style={{ height: 24, fontSize: 11 }}>delete</button>
        <button className="ab-btn" data-variant={dirty ? "primary" : "ghost"} disabled={labelStatus === "saving"} onClick={saveLabel} style={{ height: 24, fontSize: 11 }}>
          {labelStatus === "saving" ? "saving…" : labelStatus === "saved" ? "saved ✓" : labelStatus === "error" ? "error" : (dirty ? "save changes" : "no changes")}
        </button>
      </div>
    </div>
  );
}

const cfgInputStyle = {
  flex: 1, width: "100%", minWidth: 0, boxSizing: "border-box",
  height: 24, padding: "0 8px", fontSize: 11.5,
  background: "var(--ab-surface)", color: "var(--ab-fg)",
  border: "1px solid var(--ab-border-soft)", borderRadius: 3, outline: "none",
};
const cfgVal = { fontSize: 11.5, color: "var(--ab-fg)" };

function CfgSection({ label, hint, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      <div>
        <div className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-3)" }}>{label}</div>
        {hint && <div className="ab-mono" style={{ fontSize: 9.5, color: "var(--ab-fg-5)", marginTop: 1 }}>{hint}</div>}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 4, paddingTop: 2, borderTop: "1px solid var(--ab-border-soft)" }}>
        {children}
      </div>
    </div>
  );
}

function CfgField({ label, children }) {
  return (
    <div style={{ display: "grid", gridTemplateColumns: "110px 1fr", alignItems: "center", gap: 8, minHeight: 22 }}>
      <span className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-fg-4)" }}>{label}</span>
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}

function TransportLabel({ transport, codec }) {
  if (transport === "—" || !transport) return <span style={{ color: "var(--ab-fg-5)" }}>—</span>;
  const color = transport === "SRT" ? "var(--ab-accent)" : "var(--ab-info)";
  const bitrate = codec && codec !== "—" ? codec.replace("OPUS ", "") : "";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}>
      <span style={{ width: 4, height: 4, borderRadius: 1, background: color }} />
      <span style={{ color }}>{transport}</span>
      <span style={{ color: "var(--ab-fg-4)" }}>{bitrate}</span>
    </span>
  );
}
