// Audiobridge — NOC dashboard. KPI strip across the top, dense streams
// table, system + live events rail.
console.log("[variation-a] build loaded", new Date().toISOString());

var useState = React.useState, useMemo = React.useMemo, useEffect = React.useEffect, useRef = React.useRef, useCallback = React.useCallback;

function fmtMetric(value, digits = 1) {
  return Number.isFinite(value) ? value.toFixed(digits) : "—";
}

function fmtPercent(value) {
  return Number.isFinite(value) ? `${value.toFixed(0)}%` : "—";
}

function VariationA({ density = 8, showEventsRail = true, showSystemCard = true, kpiCount = 6 }) {
  const { PROGRAM, TALKBACK, SYS, CLOCK, CHANNELS, SERIES } = window.AB;
  const runtime = window.AB.runtime || {};
  const caps = runtime.capabilities || {};
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
  const hasProgramBitrate = Number.isFinite(PROGRAM.bitrate_kbps);
  const hasProgramRtt = Number.isFinite(PROGRAM.rtt_ms);
  const hasProgramJitter = Number.isFinite(PROGRAM.jitter_ms);
  const hasProgramLoss = Number.isFinite(PROGRAM.loss_pct);
  const hasClockTelemetry = Number.isFinite(CLOCK.frequency_ratio_ppm) || Number.isFinite(CLOCK.buffer_occupancy_ms);
  const activeMeterCount = CHANNELS.filter(c => Number.isFinite(c.level_dbfs) || Number.isFinite(c.peak_dbfs)).length;

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
      <TopBar active={view} alerts={0} onNavigate={(next) => setView(next === "settings" ? "settings" : "streams")} />

      {view === "settings" ? (
        <SettingsView onBack={() => setView("streams")} />
      ) : (
      <>

      {/* KPI strip */}
      <div style={{ display: "grid", gridTemplateColumns: `repeat(${kpiCount}, 1fr)`, gap: 1, padding: 12, paddingBottom: 0 }}>
        <KpiTile
          label="Program · SRT"
          value={fmtBitrate(PROGRAM.bitrate_kbps)}
          delta={PROGRAM.state}
          deltaTone={PROGRAM.state === "running" ? "warn" : "muted"}
          spark={SERIES.bitrate}
          sparkTone="muted"
          liveFooter={`${PROGRAM.codec} | ${PROGRAM.channels || 0} configured ch | ${hasProgramBitrate ? "observed" : "waiting for SRT stats"}`}
          footer={`${PROGRAM.codec} · ${PROGRAM.channels || 0} configured ch · observed bitrate pending`}
        />
        <KpiTile
          label="RTT · Program"
          value={fmtMetric(PROGRAM.rtt_ms, 1)}
          unit=" ms"
          delta="unobserved"
          liveDelta={hasProgramRtt ? "observed" : "unobserved"}
          deltaTone="muted"
          liveDeltaTone={hasProgramRtt ? "ok" : "muted"}
          spark={SERIES.rtt}
          sparkTone="muted"
          liveFooter={hasProgramRtt ? "SRT RTT from media runtime" : "waiting for SRT socket stats"}
          footer="SRT socket statistics not wired yet"
        />
        <KpiTile
          label="Jitter"
          value={fmtMetric(PROGRAM.jitter_ms, 2)}
          unit=" ms"
          delta="unobserved"
          liveDelta={hasProgramJitter ? "observed" : "unobserved"}
          deltaTone="muted"
          liveDeltaTone={hasProgramJitter ? "ok" : "muted"}
          spark={SERIES.jitter}
          sparkTone="muted"
          liveFooter={hasProgramJitter ? "SRT variance from media runtime" : "waiting for jitter probe"}
          footer="No runtime jitter probe yet"
        />
        <KpiTile
          label="Packet loss"
          value={fmtMetric(PROGRAM.loss_pct, 2)}
          unit=" %"
          delta="unobserved"
          liveDelta={hasProgramLoss ? "observed" : "unobserved"}
          deltaTone="muted"
          liveDeltaTone={hasProgramLoss ? "ok" : "muted"}
          spark={SERIES.loss}
          sparkTone="muted"
          liveFooter={hasProgramLoss ? "packet counters active" : "waiting for packet counters"}
          footer="No packet counters wired yet"
        />
        {kpiCount >= 5 && (
          <KpiTile
            label="Talkback · WebRTC"
            value={fmtMetric(TALKBACK.rtt_ms, 1)}
            unit=" ms"
            delta={caps.webrtc_media ? TALKBACK.state : "control only"}
            deltaTone={caps.webrtc_media ? "ok" : "muted"}
            spark={SERIES.tb_rtt}
            sparkTone="muted"
            footer={`${TALKBACK.codec} · media runtime ${caps.webrtc_media ? "available" : "not wired"}`}
          />
        )}
        {kpiCount >= 6 && (
          <ClockKpiTile
            sync={CLOCK.lock_state || "off"}
            ppm={CLOCK.frequency_ratio_ppm}
            codec={PROGRAM.codec}
            sampleRate="48.000 kHz"
            liveNote={hasClockTelemetry ? "clock telemetry observed" : "waiting for clock telemetry"}
            note={caps.clock_recovery ? "runtime clock recovery active" : "clock recovery not wired yet"}
          />
        )}
      </div>

      {/* Body grid: streams table | events rail */}
      <div style={{ flex: 1, display: "grid", gridTemplateColumns: (showEventsRail || showSystemCard) ? "1fr 320px" : "1fr", gap: 12, padding: 12, minHeight: 0 }}>
        <Card
          title="Streams"
          liveHint={`${filtered.length} of ${CHANNELS.length} | ${activeMeterCount} metered | ${CHANNELS.filter(c => c.state === "warn" || c.state === "err").length} alerts`}
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
              <button className="ab-btn" data-variant={adding ? "primary" : undefined} style={{ height: 22, fontSize: 11 }} onClick={() => setAdding(v => !v)}>+ add object</button>
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
                {filtered.map(c => {
                  const isSrtTx = c.entity_kind === "srt_transport" && c.direction === "out";
                  const cfgRoot = window.AB.config || {};
                  const stRoot = window.AB.status || {};
                  const inputMeters = (stRoot.meters && stRoot.meters.inputs) || [];
                  const transportCfg = isSrtTx ? (cfgRoot.srt_transports || []).find(t => t.id === c.runtime_id) : null;
                  const groupId = transportCfg && (transportCfg.encode_group_ids || [])[0];
                  const groupCfg = groupId ? (cfgRoot.encode_groups || []).find(g => g.id === groupId) : null;
                  return (
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
                    <td className="ab-num ab-mono" style={{ color: c.loss_pct > 1 ? "var(--ab-err)" : c.loss_pct > 0.3 ? "var(--ab-warn)" : "var(--ab-fg-3)" }}>{fmtMetric(c.loss_pct, 2)}</td>
                    <td className="ab-num ab-mono" style={{ color: "var(--ab-fg-3)" }}>{fmtMetric(c.jitter_ms, 1)}</td>
                    <td className="ab-num ab-mono" style={{ color: "var(--ab-fg-3)" }}>{fmtMetric(c.latency_ms, 0)}</td>
                    <td className="ab-num ab-mono" style={{ color: c.buffer_ms == null ? "var(--ab-fg-5)" : c.buffer_ms > 200 ? "var(--ab-warn)" : "var(--ab-fg-3)" }}>{c.buffer_ms == null ? "—" : c.buffer_ms.toFixed(0)}</td>
                    <td className="ab-mono" style={{ fontSize: 11 }}><TransportLabel transport={c.transport} codec={c.codec} /></td>
                    <td className="ab-mono" style={{ color: "var(--ab-fg-3)", fontSize: 11 }}>{c.route}</td>
                    <td><RowActions ch={c} expanded={expandedIds.has(c.id)} onToggle={() => toggleExpanded(c.id)} /></td>
                  </tr>
                  {groupCfg && (
                    <ChannelSubRows
                      streamId={c.runtime_id}
                      transportRunning={c.state !== "idle"}
                      group={groupCfg}
                      sources={cfgRoot.sources || []}
                      inputMeters={inputMeters}
                      colSpan={12}
                    />
                  )}
                  {expandedIds.has(c.id) && (
                    <tr key={c.id + "-cfg"}>
                      <td colSpan={12} style={{ padding: 0, height: "auto", background: "var(--ab-surface-2)", borderBottom: "1px solid var(--ab-border)" }}>
                        <StreamConfig ch={c} onClose={() => closeExpanded(c.id)} />
                      </td>
                    </tr>
                  )}
                  </React.Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>

        {(showEventsRail || showSystemCard) && (
        <div style={{ display: "flex", flexDirection: "column", gap: 12, minHeight: 0 }}>
          {showSystemCard && (
          <Card title="System" hint={SYS.audio_iface.name}>
            <div style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 10 }}>
              <SysRow label="CPU" value={fmtPercent(SYS.cpu_pct)} bar={SYS.cpu_pct} tone="muted" sub="probe pending" />
              <SysRow label="MEM" value={SYS.mem_mb == null ? "—" : `${SYS.mem_mb} MB`} bar={SYS.mem_pct} tone="muted" sub="probe pending" />
              <SysRow label="TEMP" value={SYS.temp_c == null ? "—" : `${SYS.temp_c}°C`} bar={SYS.temp_c == null ? null : (SYS.temp_c / 90) * 100} tone="muted" sub="probe pending" />
              <div className="ab-divider" />
              <div>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
                  <span className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-3)" }} title={hasClockTelemetry ? "Clock telemetry observed" : "Waiting for clock telemetry"}>Clock Runtime</span>
                  <ClockChip sync={CLOCK.lock_state || "off"} ppm={CLOCK.frequency_ratio_ppm} />
                </div>
                <div className="ab-mono" style={{ fontSize: 10, color: "var(--ab-fg-4)", marginTop: 3 }}>{CLOCK.mode || "adaptive"} · {hasClockTelemetry ? "telemetry observed" : "telemetry pending"}</div>
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
  const width = Number.isFinite(bar) ? Math.max(0, Math.min(100, bar)) : 0;
  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 4 }}>
        <span className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-3)" }}>{label}</span>
        <span className="ab-mono" style={{ fontSize: 12, color: "var(--ab-fg)" }}>{value}</span>
      </div>
      <div style={{ height: 4, background: "var(--ab-surface-3)", borderRadius: 1, overflow: "hidden" }}>
        <div style={{ width: width + "%", height: "100%", background: tone === "warn" ? "var(--ab-warn)" : "var(--ab-accent)" }} />
      </div>
      {sub && <div className="ab-mono" style={{ fontSize: 10, color: "var(--ab-fg-4)", marginTop: 3 }}>{sub}</div>}
    </div>
  );
}
function NicRow({ label, nic }) {
  const rx = Number.isFinite(nic.rx_mbps) ? nic.rx_mbps : "—";
  const tx = Number.isFinite(nic.tx_mbps) ? nic.tx_mbps : "—";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
      <div style={{ display: "flex", justifyContent: "space-between" }}>
        <span className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-3)" }}>{label}</span>
        <span className="ab-mono" style={{ fontSize: 11, color: "var(--ab-fg-2)" }}>{nic.name}</span>
      </div>
      <div className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-fg-4)", display: "flex", justifyContent: "space-between" }}>
        <span>{nic.ip}</span>
        <span>↓ {rx} <span style={{ color: "var(--ab-fg-5)" }}>·</span> ↑ {tx} <span style={{ color: "var(--ab-fg-5)" }}>Mb/s</span></span>
      </div>
    </div>
  );
}

function SettingsView({ onBack }) {
  const cfg = window.AB.config || {};
  const srtTransports = cfg.srt_transports || [];
  const webrtcStreams = cfg.webrtc_streams || [];
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
  const srtStreams = srtTransports.length;
  const rtcStreams = webrtcStreams.length;
  const overridden = 0;

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
        hint={`${srtStreams + rtcStreams} objects - ${srtStreams} SRT / ${rtcStreams} WebRTC - ${overridden} overrides`}
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
             : state === "running"? { fg: "var(--ab-warn)", bg: "var(--ab-warn-soft)", bd: "rgba(245,158,11,0.35)", label: "RUN"    }
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
  const running = ch.state !== "idle";
  const runtime = window.AB.runtime || {};
  const caps = runtime.capabilities || {};
  const supportsLifecycle = ch.entity_kind === "srt_transport"
    || (ch.entity_kind === "webrtc_stream" && !!caps.webrtc_media);
  const diagnostics = (window.AB.status && window.AB.status.diagnostics) || {};
  const endpointBase = ch.entity_kind === "srt_transport"
    ? `/api/srt-transports/${encodeURIComponent(ch.runtime_id)}`
    : ch.entity_kind === "webrtc_stream"
      ? `/api/webrtc-streams/${encodeURIComponent(ch.runtime_id)}`
      : null;
  const supportsListen = ch.entity_kind === "srt_transport" && ch.direction === "in";
  const [monitorSession, setMonitorSession] = useState(null);
  const monitorActiveForRow = !!monitorSession;
  const [actionState, setActionState] = useState({ tone: "idle", message: "" });

  useEffect(() => {
    return () => {
      if (!monitorSession) return;
      try { fetch(`/api/monitor-sessions/${monitorSession.sessionId}`, { method: "DELETE" }).catch(() => {}); } catch {}
      try { monitorSession.pc.close(); } catch {}
      if (monitorSession.audio && monitorSession.audio.parentNode) {
        monitorSession.audio.parentNode.removeChild(monitorSession.audio);
      }
    };
  }, [monitorSession]);

  // If the transport stops while we're listening, drop the dead session.
  useEffect(() => {
    if (monitorSession && ch.state === "idle") {
      try { fetch(`/api/monitor-sessions/${monitorSession.sessionId}`, { method: "DELETE" }).catch(() => {}); } catch {}
      try { monitorSession.pc.close(); } catch {}
      if (monitorSession.audio && monitorSession.audio.parentNode) {
        monitorSession.audio.parentNode.removeChild(monitorSession.audio);
      }
      setMonitorSession(null);
    }
  }, [ch.state]);

  const refreshProgramView = async () => {
    await (window.AB.refreshAll ? window.AB.refreshAll() : Promise.resolve());
  };

  const showActionState = (tone, message) => {
    setActionState({ tone, message });
    window.setTimeout(() => {
      setActionState((current) => current.message === message ? { tone: "idle", message: "" } : current);
    }, 2500);
  };

  const handleProgramStart = async () => {
    if (!endpointBase) return;
    try {
      const response = await fetch(`${endpointBase}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: ch.entity_kind === "srt_transport" && ch.direction === "out"
          ? JSON.stringify({ frequency_hz: 1000, level_dbfs: -18, waveform: "sine" })
          : undefined,
      });
      if (!response.ok) throw new Error(await response.text());
      await refreshProgramView();
      showActionState("ok", "started");
    } catch (error) {
      console.error("[program] start failed", error);
      showActionState("err", "start failed");
    }
  };

  const handleProgramStop = async () => {
    if (!endpointBase) return;
    try {
      const response = await fetch(`${endpointBase}/stop`, { method: "POST" });
      if (!response.ok) throw new Error(await response.text());
      await refreshProgramView();
      showActionState("ok", "stopped");
    } catch (error) {
      console.error("[program] stop failed", error);
      showActionState("err", "stop failed");
    }
  };

  const handleListenToggle = async () => {
    if (!supportsListen) return;

    try {
      if (monitorActiveForRow) {
        const { sessionId, pc, audio } = monitorSession;
        try { await fetch(`/api/monitor-sessions/${sessionId}`, { method: "DELETE" }); } catch {}
        try { pc.close(); } catch {}
        if (audio && audio.parentNode) audio.parentNode.removeChild(audio);
        setMonitorSession(null);
        showActionState("ok", "listen stopped");
        return;
      }

      const pc = new RTCPeerConnection();
      pc.addTransceiver("audio", { direction: "recvonly" });
      const audio = document.createElement("audio");
      audio.autoplay = true;
      audio.style.display = "none";
      document.body.appendChild(audio);
      pc.ontrack = (event) => { audio.srcObject = event.streams[0]; };

      const create = await fetch("/api/monitor-sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ transport_id: ch.runtime_id }),
      });
      if (!create.ok) {
        pc.close();
        if (audio.parentNode) audio.parentNode.removeChild(audio);
        throw new Error(await create.text());
      }
      const offer = await create.json();
      await pc.setRemoteDescription({ sdp: offer.sdp, type: offer.type });
      const answer = await pc.createAnswer();
      await pc.setLocalDescription(answer);
      await new Promise((resolve) => {
        if (pc.iceGatheringState === "complete") return resolve();
        pc.addEventListener("icegatheringstatechange", () => {
          if (pc.iceGatheringState === "complete") resolve();
        });
      });
      const ans = await fetch(`/api/monitor-sessions/${offer.session_id}/answer`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ sdp: pc.localDescription.sdp, type: pc.localDescription.type }),
      });
      if (!ans.ok) {
        try { await fetch(`/api/monitor-sessions/${offer.session_id}`, { method: "DELETE" }); } catch {}
        pc.close();
        if (audio.parentNode) audio.parentNode.removeChild(audio);
        throw new Error(await ans.text());
      }
      setMonitorSession({ sessionId: offer.session_id, pc, audio });
      showActionState("ok", "listening");
    } catch (error) {
      console.error("[monitor] toggle failed", error);
      showActionState("err", "listen failed");
    }
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3, alignItems: "flex-start" }}>
      <div style={{ display: "inline-flex", gap: 3 }}>
        {running
          ? <IconBtn tone="err" title={supportsLifecycle ? "Stop stream" : "Runtime start/stop is not available for this stream type"} disabled={!supportsLifecycle} onClick={handleProgramStop}><ActIcon.stop /></IconBtn>
          : <IconBtn tone="acc" title={supportsLifecycle ? "Start stream" : "Runtime start/stop is not available for this stream type"} disabled={!supportsLifecycle} onClick={handleProgramStart}><ActIcon.play /></IconBtn>}
        <IconBtn tone={monitorActiveForRow ? "ok" : "info"} title={supportsListen ? (monitorActiveForRow ? "Stop monitor" : "Listen in browser") : "Listen is only available for RX SRT transports"} disabled={!supportsListen} onClick={handleListenToggle}><ActIcon.listen /></IconBtn>
        <IconBtn tone="info" title="Push to talk is not wired yet" disabled={true}><ActIcon.mic /></IconBtn>
        <IconBtn tone={expanded ? "acc" : "ghost"} title={expanded ? "Hide settings" : "Stream settings"} onClick={onToggle}><ActIcon.more /></IconBtn>
      </div>
      {actionState.message && (
        <span className="ab-mono" style={{ fontSize: 9.5, color: actionState.tone === "err" ? "var(--ab-err)" : "var(--ab-ok)" }}>
          {actionState.message}
        </span>
      )}
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
      {Number.isFinite(ppm) && sync !== "off" && (
        <span className="ab-mono" style={{ fontSize: 10, color: "var(--ab-fg-4)" }}>
          {ppm >= 0 ? "+" : ""}{Math.abs(ppm) < 1 ? ppm.toFixed(2) : ppm.toFixed(1)}
        </span>
      )}
    </span>
  );
}

// Codec / clock status. Most values stay blank until the media runtime
// has real clock and resampler telemetry.
function ClockKpiTile({ sync = "off", ppm = null, codec = "—", sampleRate = "48.000 kHz", note, liveNote }) {
  const tone = sync === "lock" ? "ok" : sync === "slew" ? "warn" : sync === "drift" ? "err" : "muted";
  const ppmLabel = Number.isFinite(ppm)
    ? `${ppm >= 0 ? "+" : "−"}${Math.abs(ppm) < 1 ? Math.abs(ppm).toFixed(2) : Math.abs(ppm).toFixed(1)}`
    : "—";
  return (
    <div className="ab-card" style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 6 }}>
        <span className="ab-kpi-label" title={Number.isFinite(ppm) ? "Clock telemetry observed" : "Waiting for clock telemetry"}>Codec · Clock</span>
        <ClockChip sync={sync} ppm={null} />
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="ab-kpi-value">
          {ppmLabel}
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
      {(liveNote || note) && <div style={{ fontSize: 10.5, color: "var(--ab-fg-4)", fontFamily: "var(--ab-mono)" }}>{liveNote || note}</div>}
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

const SILENCE_DEFAULT_SOURCE_ID = "silence-default";

function ChannelSubRows({ streamId, transportRunning, group, sources, inputMeters, colSpan }) {
  const [pending, setPending] = useState({}); // index -> { source_id?, label?, gain_db? }
  const [savingIdx, setSavingIdx] = useState(null);
  const [error, setError] = useState("");

  const channelByIndex = useMemo(() => {
    const map = new Map();
    (group.channels || []).forEach(c => map.set(c.index, c));
    return map;
  }, [group]);

  const sourceOptions = useMemo(() => {
    const opts = [[SILENCE_DEFAULT_SOURCE_ID, "Silence"]];
    sources
      .filter(s => s.id !== SILENCE_DEFAULT_SOURCE_ID)
      .forEach(s => {
        const label = s.kind === "dante_input" && s.dante_channel
          ? `Dante In ${String(s.dante_channel).padStart(2, "0")}`
          : (s.name || s.id);
        opts.push([s.id, label]);
      });
    return opts;
  }, [sources]);

  const sourceMeterChannel = useCallback((sourceId) => {
    const src = sources.find(s => s.id === sourceId);
    if (!src || src.kind !== "dante_input") return null;
    return src.dante_channel || null;
  }, [sources]);

  const persistChannel = async (idx, patch) => {
    setSavingIdx(idx);
    setError("");
    try {
      const merged = (group.channels || []).map(ch => ch.index === idx ? { ...ch, ...patch } : ch);
      // Backfill silence-default for any unassigned slot up to channel_count.
      const present = new Set(merged.map(c => c.index));
      for (let i = 1; i <= (group.channel_count || merged.length); i++) {
        if (!present.has(i)) merged.push({ index: i, source_id: SILENCE_DEFAULT_SOURCE_ID, label: `Ch ${String(i).padStart(2, "0")}`, gain_db: 0 });
      }
      const body = {
        id: group.id,
        name: group.name,
        channel_count: group.channel_count,
        channels: merged.sort((a, b) => a.index - b.index),
        opus: group.opus,
        enabled: group.enabled !== false,
      };
      const res = await fetch(`/api/encode-groups/${encodeURIComponent(group.id)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(await res.text());
      setPending(p => { const n = { ...p }; delete n[idx]; return n; });
      if (transportRunning) {
        // Hot-restart so encode-group changes take effect on the running pipeline.
        await fetch(`/api/srt-transports/${encodeURIComponent(streamId)}/stop`, { method: "POST" });
        await fetch(`/api/srt-transports/${encodeURIComponent(streamId)}/start`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      }
      if (window.AB.refreshAll) await window.AB.refreshAll();
    } catch (e) {
      console.error("[channel-edit] failed", e);
      setError(String(e.message || e));
    } finally {
      setSavingIdx(null);
    }
  };

  const setSource = (idx, source_id) => {
    setPending(p => ({ ...p, [idx]: { ...(p[idx] || {}), source_id } }));
    persistChannel(idx, { source_id });
  };
  const setGain = (idx, gain_db) => {
    setPending(p => ({ ...p, [idx]: { ...(p[idx] || {}), gain_db } }));
  };
  const commitGain = (idx) => {
    const v = pending[idx] && pending[idx].gain_db;
    if (v == null) return;
    persistChannel(idx, { gain_db: Number(v) });
  };

  const startMonitor = async (idx, sourceId) => {
    // Per-channel monitor uses the existing monitor-branch API. The transport must be running.
    if (!transportRunning) return;
    const tapId = `${group.id}-ch-${idx}`;
    try {
      const res = await fetch(`/api/srt-transports/${encodeURIComponent(streamId)}/monitor-branches`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tap_id: tapId, audible: true }),
      });
      if (!res.ok) throw new Error(await res.text());
    } catch (e) {
      console.error("[monitor] failed", e);
    }
  };

  const rows = [];
  for (let i = 1; i <= (group.channel_count || 0); i++) {
    const ch = channelByIndex.get(i) || { index: i, source_id: SILENCE_DEFAULT_SOURCE_ID, label: `Ch ${String(i).padStart(2, "0")}`, gain_db: 0 };
    const effectiveSource = (pending[i] && pending[i].source_id) || ch.source_id;
    const isSilence = effectiveSource === SILENCE_DEFAULT_SOURCE_ID;
    const meterCh = sourceMeterChannel(effectiveSource);
    const meter = meterCh ? (inputMeters[meterCh - 1] || {}) : {};
    const gainVal = pending[i] && pending[i].gain_db != null ? pending[i].gain_db : (ch.gain_db ?? 0);
    rows.push(
      <tr key={`${streamId}-ch-${i}`} style={{ background: "var(--ab-surface-2)", opacity: isSilence ? 0.55 : 1 }}>
        <td colSpan={colSpan} style={{ padding: "3px 12px 3px 28px", borderBottom: "1px solid var(--ab-border-soft)" }}>
          <div style={{ display: "grid", gridTemplateColumns: "44px 1fr 200px 80px 200px 80px", gap: 10, alignItems: "center", fontSize: 11 }}>
            <span className="ab-mono" style={{ color: "var(--ab-fg-4)" }}>ch-{String(i).padStart(2, "0")}</span>
            <input
              className="ab-mono"
              value={(pending[i] && pending[i].label) ?? (ch.label || "")}
              onChange={e => setPending(p => ({ ...p, [i]: { ...(p[i] || {}), label: e.target.value } }))}
              onBlur={() => { const v = pending[i] && pending[i].label; if (v != null && v !== ch.label) persistChannel(i, { label: v }); }}
              placeholder={`Ch ${String(i).padStart(2, "0")}`}
              style={{ ...cfgInputStyle, height: 20, fontSize: 11 }}
            />
            <select
              className="ab-mono"
              value={effectiveSource}
              onChange={e => setSource(i, e.target.value)}
              style={{ ...cfgInputStyle, height: 20, fontSize: 11 }}
            >
              {sourceOptions.map(([k, lbl]) => <option key={k} value={k}>{lbl}</option>)}
            </select>
            <div style={{ display: "flex", alignItems: "center", gap: 4 }} title="Per-channel gain (dB)">
              <input
                type="number" step={0.5} min={-60} max={20}
                className="ab-mono"
                value={gainVal}
                onChange={e => setGain(i, e.target.value)}
                onBlur={() => commitGain(i)}
                style={{ ...cfgInputStyle, height: 20, fontSize: 11, textAlign: "right", flex: 1, minWidth: 0 }}
              />
              <span className="ab-mono" style={{ fontSize: 10, color: "var(--ab-fg-4)" }}>dB</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
              <Meter level={meter.rms_dbfs} peak={meter.peak_dbfs} w={140} />
              <span className="ab-mono" style={{ fontSize: 10, color: "var(--ab-fg-3)", width: 38, textAlign: "right" }}>{fmtDb(meter.rms_dbfs)}</span>
            </div>
            <button
              className="ab-btn"
              data-variant="ghost"
              disabled={!transportRunning || isSilence}
              onClick={() => startMonitor(i, effectiveSource)}
              title={transportRunning ? (isSilence ? "Assign a source first" : "Listen to this channel") : "Stream must be running"}
              style={{ height: 20, fontSize: 11 }}
            >
              {savingIdx === i ? "…" : "🎧 monitor"}
            </button>
          </div>
        </td>
      </tr>
    );
  }
  if (error) {
    rows.push(
      <tr key={`${streamId}-err`}><td colSpan={colSpan} style={{ padding: "3px 12px 3px 28px", background: "var(--ab-surface-2)", color: "var(--ab-err)", fontSize: 10.5 }} className="ab-mono">{error}</td></tr>
    );
  }
  return <>{rows}</>;
}

function AddStreamPanel({ onClose }) {
  const cfg = window.AB.config || {};
  const programDefaults = (cfg.program && cfg.program.opus) || defaultOpus("srt");
  const [kind, setKind] = useState("srt_transport");
  const [name, setName] = useState("");
  const [direction, setDirection] = useState("tx");
  const [mode, setMode] = useState("listener");
  const [host, setHost] = useState("");
  const [port, setPort] = useState(String((cfg.network && cfg.network.srt_port) || 9000));
  const [latencyMs, setLatencyMs] = useState(String((cfg.program && cfg.program.srt_latency_ms) || 240));
  const [channelCount, setChannelCount] = useState("2");
  const [opusOverride, setOpusOverride] = useState(false);
  const [opus, setOpus] = useState({ ...programDefaults });
  const [sourceId, setSourceId] = useState("");
  const [status, setStatus] = useState("idle");
  const [error, setError] = useState("");

  const trimmedName = name.trim();
  const previewId = `${kind === "srt_transport" ? "srt" : "wrtc"}-${(trimmedName || "item").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") || "item"}`;
  const groupPreviewId = `enc-${previewId}`;
  const srtTxNeedsHost = kind === "srt_transport" && direction === "tx" && mode !== "listener";
  const canSubmit = !!trimmedName && (!srtTxNeedsHost || !!host.trim()) && status !== "saving";

  const postJson = async (path, body) => {
    const response = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw new Error(await response.text());
    return response.json();
  };

  const submit = async () => {
    if (!trimmedName) {
      setError("name is required");
      setStatus("error");
      return;
    }
    if (srtTxNeedsHost && !host.trim()) {
      setError("host is required for caller and rendezvous TX");
      setStatus("error");
      return;
    }

    setStatus("saving");
    setError("");

    try {
      if (kind === "srt_transport") {
        const encodeGroupIds = [];
        if (direction === "tx") {
          const n = clampInt(channelCount, 1, 8);
          const channels = Array.from({ length: n }, (_, i) => ({
            index: i + 1,
            source_id: SILENCE_DEFAULT_SOURCE_ID,
            label: `Ch ${String(i + 1).padStart(2, "0")}`,
            gain_db: 0.0,
          }));
          await postJson("/api/encode-groups", {
            id: groupPreviewId,
            name: `${trimmedName} (${n}ch)`,
            channel_count: n,
            channels,
            opus: opusOverride ? {
              bitrate_kbps: clampInt(opus.bitrate_kbps, 16, 512),
              bitrate_mode: opus.bitrate_mode || "cbr",
              frame_ms: clampInt(opus.frame_ms, 2, 60),
              complexity: clampInt(opus.complexity, 0, 10),
              inband_fec: !!opus.inband_fec,
              expected_packet_loss_percent: clampInt(opus.expected_packet_loss_percent, 0, 30),
            } : { ...programDefaults },
            enabled: true,
          });
          encodeGroupIds.push(groupPreviewId);
        }
        await postJson("/api/srt-transports", {
          id: previewId,
          name: trimmedName,
          direction,
          mode,
          port: clampInt(port, 1, 65535),
          latency_ms: clampInt(latencyMs, 20, 8000),
          ...(host.trim() ? { host: host.trim() } : {}),
          encode_group_ids: encodeGroupIds,
        });
      } else {
        await postJson("/api/webrtc-streams", {
          id: previewId,
          name: trimmedName,
          direction,
          ...(sourceId.trim() ? { source_id: sourceId.trim() } : {}),
        });
      }

      await (window.AB.refreshAll ? window.AB.refreshAll() : Promise.resolve());
      setStatus("idle");
      onClose();
    } catch (e) {
      console.error("[add-object] failed", e);
      setError(String(e.message || e));
      setStatus("error");
    }
  };

  return (
    <div style={{ padding: "12px 14px 14px", background: "var(--ab-surface-2)", borderBottom: "1px solid var(--ab-border)", display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-3)" }}>
          New object
        </span>
        <button className="ab-btn" data-variant="ghost" onClick={onClose} style={{ marginLeft: "auto", height: 22, fontSize: 11 }}>cancel</button>
        <button className="ab-btn" data-variant="primary" disabled={!canSubmit} onClick={submit} style={{ height: 22, fontSize: 11 }}>
          {status === "saving" ? "creating..." : "create"}
        </button>
      </div>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0, 1fr))", gap: 14 }}>
        <CfgSection label="Identity">
          <CfgField label="Kind">
            <Segmented value={kind} onChange={setKind} options={[["srt_transport", "SRT"], ["webrtc_stream", "WebRTC"]]} />
          </CfgField>
          <CfgField label="Name">
            <input autoFocus value={name} onChange={e => setName(e.target.value)} className="ab-mono" style={cfgInputStyle} placeholder="operator label" />
          </CfgField>
          <CfgField label="Direction">
            <Segmented value={direction} onChange={setDirection} options={[["tx", "TX"], ["rx", "RX"]]} />
          </CfgField>
        </CfgSection>
        {kind === "srt_transport" ? (
          <CfgSection label="Transport">
            <CfgField label="Mode">
              <Segmented value={mode} onChange={setMode} options={[["listener", "Listen"], ["caller", "Call"], ["rendezvous", "Rendezvous"]]} />
            </CfgField>
            <CfgField label="Host">
              <input value={host} onChange={e => setHost(e.target.value)} className="ab-mono" style={cfgInputStyle} placeholder={mode === "listener" ? "optional" : "required for call/rendezvous"} />
            </CfgField>
            <CfgField label="Port">
              <NumberField value={port} min={1} max={65535} onChange={setPort} suffix="port" />
            </CfgField>
            <CfgField label="Latency">
              <NumberField value={latencyMs} min={20} max={8000} onChange={setLatencyMs} suffix="ms" />
            </CfgField>
            {direction === "tx" && (
              <>
                <CfgField label="Channels">
                  <NumberField value={channelCount} min={1} max={8} onChange={setChannelCount} suffix="ch" />
                </CfgField>
                <CfgField label="Codec">
                  <Segmented value={opusOverride ? "override" : "default"} onChange={v => setOpusOverride(v === "override")} options={[["default", "Defaults"], ["override", "Override"]]} />
                </CfgField>
                {opusOverride && (
                  <OpusFields opus={opus} disabled={false} onChange={setOpus} />
                )}
              </>
            )}
          </CfgSection>
        ) : (
          <CfgSection label="Stream">
            <CfgField label="Source id">
              <input value={sourceId} onChange={e => setSourceId(e.target.value)} className="ab-mono" style={cfgInputStyle} placeholder="optional source link" />
            </CfgField>
            <CfgField label="Codec"><span className="ab-mono" style={cfgVal}>inherits talkback defaults</span></CfgField>
          </CfgSection>
        )}
        <CfgSection label="Preview">
          <CfgField label="ID"><span className="ab-mono" style={cfgVal}>{previewId}</span></CfgField>
          <CfgField label="Path"><span className="ab-mono" style={cfgVal}>{kind === "srt_transport" && direction === "tx" ? `${clampInt(channelCount, 1, 8)}ch silence-filled · group -> SRT` : kind === "srt_transport" ? "POST /api/srt-transports" : "POST /api/webrtc-streams"}</span></CfgField>
          {kind === "srt_transport" && direction === "tx" && (
            <CfgField label="Group ID"><span className="ab-mono" style={cfgVal}>{groupPreviewId}</span></CfgField>
          )}
          <CfgField label="State"><span className="ab-mono" style={cfgVal}>created stopped · assign sources after</span></CfgField>
        </CfgSection>
      </div>
      {error && <div className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-err)" }}>{error}</div>}
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
  const details = ch.details || {};
  const [name, setName] = useState(ch.name || "");
  const [direction, setDirection] = useState(details.direction || (ch.direction === "out" ? "tx" : "rx"));
  const [mode, setMode] = useState(details.mode || "listener");
  const [host, setHost] = useState(details.host || "");
  const [port, setPort] = useState(String(details.port || ((cfg.network && cfg.network.srt_port) || 9000)));
  const [latencyMs, setLatencyMs] = useState(String(details.latency_ms || ((cfg.program && cfg.program.srt_latency_ms) || 240)));
  const [sourceId, setSourceId] = useState(details.source_id || "");
  const [saveState, setSaveState] = useState("idle");
  const [saveError, setSaveError] = useState("");
  const fields = ch.entity_kind === "srt_transport"
    ? [
        ["ID", details.id || ch.runtime_id],
        ["Direction", details.direction || (ch.direction === "out" ? "tx" : "rx")],
        ["Mode", details.mode || "listener"],
        ["Host", details.host || "—"],
        ["Port", details.port || ((cfg.network && cfg.network.srt_port) || "—")],
        ["Latency", details.latency_ms != null ? `${details.latency_ms} ms` : "—"],
        ["Groups", (details.encode_group_ids || []).join(", ") || "—"],
        ["State", details.state || ch.state],
      ]
    : [
        ["ID", details.id || ch.runtime_id],
        ["Direction", details.direction || (ch.direction === "out" ? "tx" : "rx")],
        ["Source", details.source_id || "—"],
        ["Bitrate", details.bitrate_kbps != null ? `${details.bitrate_kbps} kbps` : "—"],
        ["RTT", details.rtt_ms != null ? `${details.rtt_ms} ms` : "—"],
        ["State", details.state || ch.state],
      ];

  const saveObject = async () => {
    setSaveState("saving");
    setSaveError("");
    try {
      const endpoint = ch.entity_kind === "srt_transport"
        ? `/api/srt-transports/${encodeURIComponent(ch.runtime_id)}`
        : `/api/webrtc-streams/${encodeURIComponent(ch.runtime_id)}`;
      const body = ch.entity_kind === "srt_transport"
        ? {
            id: details.id || ch.runtime_id,
            name: name.trim() || ch.name,
            direction,
            mode,
            port: clampInt(port, 1, 65535),
            latency_ms: clampInt(latencyMs, 20, 8000),
            ...(host.trim() ? { host: host.trim() } : {}),
            encode_group_ids: details.encode_group_ids || [],
          }
        : {
            id: details.id || ch.runtime_id,
            name: name.trim() || ch.name,
            direction,
            ...(sourceId.trim() ? { source_id: sourceId.trim() } : {}),
          };
      const response = await fetch(endpoint, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!response.ok) throw new Error(await response.text());
      await (window.AB.refreshAll ? window.AB.refreshAll() : Promise.resolve());
      setSaveState("saved");
      window.setTimeout(() => setSaveState("idle"), 1500);
    } catch (e) {
      console.error("[object-config] save failed", e);
      setSaveError(String(e.message || e));
      setSaveState("error");
    }
  };

  const deleteObject = async () => {
    setSaveState("saving");
    setSaveError("");
    try {
      const endpoint = ch.entity_kind === "srt_transport"
        ? `/api/srt-transports/${encodeURIComponent(ch.runtime_id)}`
        : `/api/webrtc-streams/${encodeURIComponent(ch.runtime_id)}`;
      const response = await fetch(endpoint, { method: "DELETE" });
      if (!response.ok) throw new Error(await response.text());
      await (window.AB.refreshAll ? window.AB.refreshAll() : Promise.resolve());
      onClose();
    } catch (e) {
      console.error("[object-config] delete failed", e);
      setSaveError(String(e.message || e));
      setSaveState("error");
    }
  };

  return (
    <div style={{ padding: "14px 16px 16px", display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-3)" }}>
          {ch.entity_kind === "srt_transport" ? "SRT transport" : "WebRTC stream"} details
        </span>
        <TypeChip type={ch.type} transport={ch.transport} />
        <span className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-fg-4)" }}>
          {ch.direction === "in" ? "RX · peer → us" : "TX · us → peer"} · {ch.transport} · {ch.route}
        </span>
        <button className="ab-btn" data-variant="ghost" style={{ marginLeft: "auto", height: 22, fontSize: 11 }} onClick={onClose}>close</button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "repeat(2, minmax(0, 1fr))", gap: 14 }}>
        <CfgSection label="Identity">
          <CfgField label="Name"><input value={name} onChange={e => setName(e.target.value)} className="ab-mono" style={cfgInputStyle} /></CfgField>
          <CfgField label="Kind"><span className="ab-mono" style={cfgVal}>{ch.entity_kind}</span></CfgField>
          <CfgField label="Route"><span className="ab-mono" style={cfgVal}>{ch.route}</span></CfgField>
          <CfgField label="State"><span className="ab-mono" style={cfgVal}>{ch.state}</span></CfgField>
        </CfgSection>

        <CfgSection label="Config" hint="persisted object editor">
          <CfgField label="Direction">
            <Segmented value={direction} onChange={setDirection} options={[["tx", "TX"], ["rx", "RX"]]} />
          </CfgField>
          {ch.entity_kind === "srt_transport" ? (
            <>
              <CfgField label="Mode">
                <Segmented value={mode} onChange={setMode} options={[["listener", "Listen"], ["caller", "Call"], ["rendezvous", "Rendezvous"]]} />
              </CfgField>
              <CfgField label="Host"><input value={host} onChange={e => setHost(e.target.value)} className="ab-mono" style={cfgInputStyle} placeholder="optional for listener" /></CfgField>
              <CfgField label="Port"><NumberField value={port} min={1} max={65535} onChange={setPort} suffix="port" /></CfgField>
              <CfgField label="Latency"><NumberField value={latencyMs} min={20} max={8000} onChange={setLatencyMs} suffix="ms" /></CfgField>
            </>
          ) : (
            <>
              <CfgField label="Source id"><input value={sourceId} onChange={e => setSourceId(e.target.value)} className="ab-mono" style={cfgInputStyle} placeholder="optional source link" /></CfgField>
              <CfgField label="Codec"><span className="ab-mono" style={cfgVal}>inherits talkback defaults</span></CfgField>
            </>
          )}
        </CfgSection>

        <CfgSection label="Runtime" hint="read-only summary from /api/status">
          {fields.map(([label, value]) => (
            <CfgField key={label} label={label}><span className="ab-mono" style={cfgVal}>{String(value)}</span></CfgField>
          ))}
        </CfgSection>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, paddingTop: 10, borderTop: "1px solid var(--ab-border-soft)", flexWrap: "wrap" }}>
        {saveError && <span className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-err)" }}>{saveError}</span>}
        <div style={{ flex: 1 }} />
        <button className="ab-btn" data-variant="danger" disabled={saveState === "saving"} onClick={deleteObject} style={{ height: 24, fontSize: 11 }}>delete</button>
        <button className="ab-btn" data-variant="primary" disabled={saveState === "saving"} onClick={saveObject} style={{ height: 24, fontSize: 11 }}>
          {saveState === "saving" ? "saving..." : saveState === "saved" ? "saved" : saveState === "error" ? "retry save" : "save changes"}
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
