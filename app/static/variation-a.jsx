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

function VariationA() {
  const { CHANNELS, SYS, CLOCK } = window.AB;
  const cfg = window.AB.config || {};
  const status = window.AB.status || {};
  const [view, setView] = useState("streams");
  const [tab, setTab] = useState("all"); // all | rx | tx | issues
  const [query, setQuery] = useState("");
  const [adding, setAdding] = useState(false);
  // Per-card drawer state — supports multiple drawers open at once.
  const [openDrawers, setOpenDrawers] = useState(() => new Set());
  const toggleDrawer = (id) => setOpenDrawers(prev => {
    const next = new Set(prev);
    if (next.has(id)) next.delete(id); else next.add(id);
    return next;
  });
  const closeDrawer = (id) => setOpenDrawers(prev => {
    if (!prev.has(id)) return prev;
    const next = new Set(prev); next.delete(id); return next;
  });
  const [eventsOpen, setEventsOpen] = useState(false);
  const timeWindow = window.AB.timeWindow || "5m";
  const setTimeWindow = (id) => window.AB.setTimeWindow && window.AB.setTimeWindow(id);

  const filtered = useMemo(() => {
    let rows = CHANNELS;
    if (tab === "rx") rows = rows.filter(c => c.direction === "in");
    if (tab === "tx") rows = rows.filter(c => c.direction === "out");
    if (tab === "issues") rows = rows.filter(c => c.state === "warn" || c.state === "err");
    if (query) rows = rows.filter(c => c.name.toLowerCase().includes(query.toLowerCase()) || String(c.id).includes(query));
    return rows;
  }, [tab, query, CHANNELS]);

  const inputMeters = (status.meters && status.meters.inputs) || [];
  const outputMeters = (status.meters && status.meters.outputs) || [];
  const sources = cfg.sources || [];

  return (
    <div className="ab-frame ab-root">
      <TopBar active={view} alerts={0} onNavigate={(next) => setView(next === "settings" ? "settings" : "streams")} />

      {view === "settings" ? (
        <SettingsView onBack={() => setView("streams")} />
      ) : (
      <>
      <EndpointHeader
        tab={tab} setTab={setTab}
        query={query} setQuery={setQuery}
        adding={adding} onAdd={() => setAdding(v => !v)}
        timeWindow={timeWindow} onTimeWindow={setTimeWindow}
      />
      {adding && <div style={{ margin: "0 12px" }}><div className="ab-card"><AddStreamPanel onClose={() => setAdding(false)} /></div></div>}
      <div style={{ flex: 1, overflow: "auto", padding: "12px", minHeight: 0 }}>
        {filtered.length === 0 ? (
          <div className="ab-card" style={{ padding: 40, textAlign: "center", color: "var(--ab-fg-4)", fontSize: 12 }}>
            {CHANNELS.length === 0 ? "no streams configured — use + add object to create one" : "no streams match the current filter"}
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(480px, 1fr))", gap: 12, alignItems: "start" }}>
            {filtered.map(c => (
              <StreamCard
                key={c.id}
                ch={c}
                drawerOpen={openDrawers.has(c.id)}
                onToggleDrawer={() => toggleDrawer(c.id)}
                onCloseDrawer={() => closeDrawer(c.id)}
                sources={sources}
                inputMeters={inputMeters}
                outputMeters={outputMeters}
              />
            ))}
          </div>
        )}
      </div>
      <EventsRail open={eventsOpen} onToggle={() => setEventsOpen(v => !v)} />
      </>
      )}
    </div>
  );
}

// ── Endpoint header (replaces the old KPI strip) ───────────────────────────
function EndpointHeader({ tab, setTab, query, setQuery, adding, onAdd, timeWindow, onTimeWindow }) {
  const { SYS, CLOCK, CHANNELS } = window.AB;
  const windows = window.AB.TIME_WINDOWS || [{ id: "5m", label: "5m" }];
  const counts = useMemo(() => ({
    rx: CHANNELS.filter(c => c.direction === "in").length,
    tx: CHANNELS.filter(c => c.direction === "out").length,
    issues: CHANNELS.filter(c => c.state === "warn" || c.state === "err").length,
    total: CHANNELS.length,
  }), [CHANNELS]);
  const hasClockTelemetry = ["running", "lock", "slew", "drift"].includes(CLOCK.lock_state)
    || Number.isFinite(CLOCK.frequency_ratio_ppm);
  const stat = (label, value, hint) => (
    <div title={hint} style={{ display: "flex", flexDirection: "column", lineHeight: 1.1, minWidth: 0 }}>
      <span className="ab-mono" style={{ fontSize: 9.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-4)" }}>{label}</span>
      <span className="ab-mono" style={{ fontSize: 12, color: "var(--ab-fg)", whiteSpace: "nowrap" }}>{value}</span>
    </div>
  );
  const div = () => <span style={{ width: 1, height: 26, background: "var(--ab-border-soft)" }} />;
  return (
    <div className="ab-card" style={{ margin: "12px 12px 0", padding: "8px 12px", display: "flex", alignItems: "center", gap: 14, flexWrap: "wrap" }}>
      {stat("CPU", fmtPercent(SYS.cpu_pct), "Endpoint CPU usage")}
      {stat("MEM", SYS.mem_mb == null ? "—" : `${SYS.mem_mb} MB`, "Endpoint memory in use")}
      {stat("TEMP", SYS.temp_c == null ? "—" : `${SYS.temp_c}°C`, "Endpoint temperature (probe pending)")}
      {div()}
      {stat("Dante", `↓ ${SYS.nic_dante.rx_mbps ?? "—"} · ↑ ${SYS.nic_dante.tx_mbps ?? "—"} Mb/s`, SYS.nic_dante.name)}
      {stat("WAN", `↓ ${SYS.nic_wan.rx_mbps ?? "—"} · ↑ ${SYS.nic_wan.tx_mbps ?? "—"} Mb/s`, SYS.nic_wan.name)}
      {div()}
      <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.1 }} title={hasClockTelemetry ? "Clock telemetry observed" : "Waiting for clock telemetry"}>
        <span className="ab-mono" style={{ fontSize: 9.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-4)" }}>Clock</span>
        <ClockChip sync={CLOCK.lock_state || "off"} ppm={CLOCK.frequency_ratio_ppm} />
      </div>
      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
        <div style={{ display: "flex", padding: 2, background: "var(--ab-surface-2)", borderRadius: 4 }}>
          {[["all", `All · ${counts.total}`], ["rx", `RX · ${counts.rx}`], ["tx", `TX · ${counts.tx}`], ["issues", `Issues · ${counts.issues}`]].map(([k, lbl]) => (
            <button key={k} onClick={() => setTab(k)} className="ab-btn"
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
        <div style={{ display: "flex", padding: 2, background: "var(--ab-surface-2)", borderRadius: 4 }} title="Sparkline time window">
          {windows.map(w => (
            <button key={w.id} onClick={() => onTimeWindow(w.id)} className="ab-btn"
                    data-variant={timeWindow === w.id ? "primary" : "ghost"}
                    style={{ height: 22, padding: "0 8px", fontSize: 11, ...(timeWindow !== w.id ? { background: "transparent", border: "1px solid transparent", color: "var(--ab-fg-3)" } : {}) }}>
              {w.label}
            </button>
          ))}
        </div>
        <button className="ab-btn" data-variant={adding ? "primary" : undefined} style={{ height: 22, fontSize: 11 }} onClick={onAdd}>+ add object</button>
      </div>
    </div>
  );
}

// ── Bottom events rail (collapsible) ───────────────────────────────────────
function EventsRail({ open, onToggle }) {
  const connected = window.AB.CONNECTED && window.AB.CONNECTED.sse;
  return (
    <div className="ab-card" style={{ margin: "0 12px 12px", display: "flex", flexDirection: "column", maxHeight: open ? 200 : 32, transition: "max-height 0.15s ease" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "6px 10px", cursor: "pointer", borderBottom: open ? "1px solid var(--ab-border-soft)" : "none" }} onClick={onToggle}>
        <span className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-3)" }}>Live events</span>
        <Chip tone={connected ? "ok" : "muted"}>{connected ? "connected" : "—"}</Chip>
        <span className="ab-mono" style={{ marginLeft: "auto", fontSize: 10.5, color: "var(--ab-fg-4)" }}>{open ? "collapse" : "expand"}</span>
      </div>
      {open && (
        <div style={{ flex: 1, overflow: "auto" }}>
          <EventsLog />
        </div>
      )}
    </div>
  );
}


// ── Stream card (the grid tile) ────────────────────────────────────────────
function StreamCard({ ch, drawerOpen, onToggleDrawer, onCloseDrawer, sources, inputMeters, outputMeters }) {
  const cfgRoot = window.AB.config || {};
  const isSrt = ch.entity_kind === "srt_transport";
  const transportCfg = isSrt ? (cfgRoot.srt_transports || []).find(t => t.id === ch.runtime_id) : null;
  const groupId = transportCfg && (transportCfg.encode_group_ids || [])[0];
  const groupCfg = groupId ? (cfgRoot.encode_groups || []).find(g => g.id === groupId) : null;
  const channelCount = groupCfg ? (groupCfg.channel_count || 1) : (ch.direction === "in" ? 2 : 1);
  const perPage = 4;
  const pageCount = Math.max(1, Math.ceil(channelCount / perPage));
  const [page, setPage] = useState(0);
  useEffect(() => { if (page >= pageCount) setPage(0); }, [pageCount]);

  return (
    <div className="ab-card" style={{ display: "flex", flexDirection: "column", minWidth: 0 }}>
      <StreamCardHeader ch={ch} drawerOpen={drawerOpen} onToggleDrawer={onToggleDrawer} />
      <SparklineStrip ch={ch} />
      {groupCfg ? (
        <ChannelStrip
          streamId={ch.runtime_id}
          transportRunning={ch.state !== "idle"}
          group={groupCfg}
          sources={sources}
          inputMeters={inputMeters}
          outputMeters={outputMeters}
          meterDirection={ch.direction}
          page={page}
          perPage={perPage}
        />
      ) : (
        <div style={{ padding: 12, fontSize: 11, color: "var(--ab-fg-4)" }}>no channel group — assign one in settings</div>
      )}
      {pageCount > 1 && (
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 8,
                      padding: "4px 10px", borderTop: "1px solid var(--ab-border-soft)" }}>
          <button className="ab-btn" data-variant="ghost" style={{ height: 18, fontSize: 10.5, padding: "0 6px" }}
                  disabled={page === 0} onClick={() => setPage(p => Math.max(0, p - 1))}>‹</button>
          <span className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-fg-4)" }}>
            page {page + 1} / {pageCount} · {channelCount} ch
          </span>
          <button className="ab-btn" data-variant="ghost" style={{ height: 18, fontSize: 10.5, padding: "0 6px" }}
                  disabled={page >= pageCount - 1} onClick={() => setPage(p => Math.min(pageCount - 1, p + 1))}>›</button>
        </div>
      )}
      {drawerOpen && (
        <div style={{ borderTop: "2px solid var(--ab-border)", background: "var(--ab-surface-2)" }}>
          <StreamDrawer ch={ch} onClose={onCloseDrawer} />
        </div>
      )}
    </div>
  );
}

function StreamCardHeader({ ch, drawerOpen, onToggleDrawer }) {
  const dirChip = ch.direction === "in"
    ? <span style={{ color: "var(--ab-info)", display: "inline-flex", alignItems: "center", gap: 4, fontFamily: "var(--ab-mono)", fontSize: 11, letterSpacing: 0.04 }}><Icon.arrow2 /> RX</span>
    : <span style={{ color: "var(--ab-accent)", display: "inline-flex", alignItems: "center", gap: 4, fontFamily: "var(--ab-mono)", fontSize: 11, letterSpacing: 0.04 }}>
        <span style={{ display: "inline-flex", transform: "scaleX(-1)" }}><Icon.arrow2 /></span>TX
      </span>;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "8px 10px",
                  borderBottom: "1px solid var(--ab-border-soft)", minWidth: 0 }}>
      {dirChip}
      <TypeChip type={ch.type} transport={ch.transport} />
      <span style={{ color: "var(--ab-fg)", fontSize: 12, fontWeight: 500, minWidth: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", flex: 1 }} title={ch.name}>
        {ch.name}
      </span>
      <StateChip state={ch.state} />
      <RowActions ch={ch} expanded={drawerOpen} onToggle={onToggleDrawer} />
    </div>
  );
}

// ── Per-card sparkline strip ───────────────────────────────────────────────
function SparklineStrip({ ch }) {
  const get = (m) => window.AB.getStreamSeries ? window.AB.getStreamSeries(ch.runtime_id, m) : [];
  const finite = (arr) => arr.filter(v => Number.isFinite(v));
  const last = (arr) => {
    const f = finite(arr);
    return f.length ? f[f.length - 1] : null;
  };
  const bitrate = get("bitrate");
  const rtt = get("rtt");
  const loss = get("loss");
  const buffer = get("buffer");
  const tile = (label, value, unit, data, tone, hint) => (
    <div style={{ display: "flex", flexDirection: "column", gap: 2, padding: "6px 8px",
                  borderRight: "1px solid var(--ab-border-soft)", minWidth: 0, flex: 1, overflow: "hidden" }} title={hint}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 6, minWidth: 0 }}>
        <span className="ab-mono" style={{ fontSize: 9.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-4)" }}>{label}</span>
        <span className="ab-mono" style={{ fontSize: 11, color: value == null ? "var(--ab-fg-5)" : "var(--ab-fg)", marginLeft: "auto", whiteSpace: "nowrap" }}>
          {value == null ? "—" : value}{value != null && unit ? <span style={{ color: "var(--ab-fg-4)" }}> {unit}</span> : null}
        </span>
      </div>
      <Sparkline data={finite(data)} tone={tone} w={200} h={20} fluid />
    </div>
  );
  return (
    <div style={{ display: "flex", borderBottom: "1px solid var(--ab-border-soft)" }}>
      {tile("Bitrate", last(bitrate) != null ? (last(bitrate) >= 1000 ? (last(bitrate)/1000).toFixed(2) : last(bitrate).toFixed(0)) : null,
            last(bitrate) != null ? (last(bitrate) >= 1000 ? "Mb/s" : "kb/s") : "",
            bitrate, "muted", "SRT send/receive bitrate")}
      {tile("RTT", last(rtt) != null ? last(rtt).toFixed(1) : null, "ms", rtt, "muted", "SRT round-trip time")}
      {tile("Loss", last(loss) != null ? last(loss).toFixed(2) : null, "%", loss, "muted", "Packet loss / retransmit rate")}
      {tile("Buffer", last(buffer) != null ? last(buffer).toFixed(0) : null, "ms", buffer, "muted", "Jitter buffer occupancy")}
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
  const [audioInterfaces, setAudioInterfaces] = useState([]);
  const [registeredDevices, setRegisteredDevices] = useState([]);
  const [audioStatus, setAudioStatus] = useState("idle");
  const [audioError, setAudioError] = useState("");
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

  const refreshAudioInterfaces = useCallback(() => {
    return Promise.all([
      fetch("/api/interfaces/audio")
        .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
        .then(data => setAudioInterfaces((data && data.interfaces) || []))
        .catch(e => { console.error("[settings] audio interfaces fetch failed", e); setAudioInterfaces([]); }),
      fetch("/api/devices/audio")
        .then(r => r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`)))
        .then(data => setRegisteredDevices((data && data.devices) || []))
        .catch(e => { console.error("[settings] registered devices fetch failed", e); setRegisteredDevices([]); }),
    ]);
  }, []);
  useEffect(() => { refreshAudioInterfaces(); }, [refreshAudioInterfaces, JSON.stringify(cfg.sources || [])]);

  const addAudioDevice = async (name, channelCount) => {
    if (!name) return;
    setAudioStatus("saving"); setAudioError("");
    try {
      const r = await fetch("/api/devices/audio", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(channelCount ? { name, channel_count: channelCount } : { name }),
      });
      if (!r.ok) throw new Error(await r.text());
      await (window.AB.refreshConfig && window.AB.refreshConfig());
      await refreshAudioInterfaces();
      setAudioStatus("saved");
      setTimeout(() => setAudioStatus("idle"), 1400);
    } catch (e) {
      console.error("[settings] add audio device failed", e);
      setAudioError(String(e.message || e));
      setAudioStatus("error");
    }
  };

  const removeAudioDevice = async (name) => {
    if (!name) return;
    if (!window.confirm(`Remove device "${name}" and all its dante_input sources?`)) return;
    setAudioStatus("saving"); setAudioError("");
    try {
      const r = await fetch(`/api/devices/audio/${encodeURIComponent(name)}`, { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      await (window.AB.refreshConfig && window.AB.refreshConfig());
      await refreshAudioInterfaces();
      setAudioStatus("saved");
      setTimeout(() => setAudioStatus("idle"), 1400);
    } catch (e) {
      console.error("[settings] remove audio device failed", e);
      setAudioError(String(e.message || e));
      setAudioStatus("error");
    }
  };

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

          <CfgSection label="Capture devices" hint="dante_input sources route through these. Each registered device opens one OS audio capture in the TX pipeline.">
            <CaptureDevicesPanel
              available={audioInterfaces}
              status={audioStatus}
              error={audioError}
              onAdd={addAudioDevice}
              onRemove={removeAudioDevice}
              onRefresh={refreshAudioInterfaces}
            />
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

function CaptureDevicesPanel({ available, status, error, onAdd, onRemove, onRefresh }) {
  const cfg = window.AB.config || {};
  const registered = useMemo(() => {
    const map = new Map();
    (cfg.sources || []).forEach(s => {
      if (s.kind !== "dante_input" || !s.interface_name) return;
      const key = s.interface_name;
      const entry = map.get(key) || { name: key, driver: s.interface_driver || "unknown", device_id: s.interface_device_id || null, source_count: 0, max_channel: 0 };
      entry.source_count++;
      entry.max_channel = Math.max(entry.max_channel, s.dante_channel || 0);
      map.set(key, entry);
    });
    return Array.from(map.values()).sort((a, b) => a.name.localeCompare(b.name));
  }, [JSON.stringify(cfg.sources || [])]);

  const [pickName, setPickName] = useState("");
  const [pickCount, setPickCount] = useState("");
  const availableUnregistered = (available || []).filter(iface => !registered.some(r => r.name === iface.name));

  const submit = () => {
    if (!pickName) return;
    const n = pickCount ? Math.max(1, Math.min(255, parseInt(pickCount, 10) || 0)) : null;
    onAdd(pickName, n);
    setPickName("");
    setPickCount("");
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      {registered.length === 0 && (
        <div className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-warn)", padding: "4px 0" }}>
          no capture devices registered — dante_input sources will fail validation
        </div>
      )}
      {registered.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          {registered.map(d => (
            <div key={d.name} style={{
              display: "grid", gridTemplateColumns: "1fr auto auto auto", gap: 8, alignItems: "center",
              padding: "4px 8px", background: "var(--ab-surface-2)", borderRadius: 3,
              border: "1px solid var(--ab-border-soft)"
            }}>
              <span className="ab-mono" style={{ fontSize: 11.5, color: "var(--ab-fg)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={d.name}>{d.name}</span>
              <Chip tone="muted">{d.driver}</Chip>
              <span className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-fg-3)" }}>{d.source_count} src · max ch {d.max_channel}</span>
              <button className="ab-btn" data-variant="ghost" disabled={status === "saving"} onClick={() => onRemove(d.name)} style={{ height: 20, fontSize: 11, color: "var(--ab-err)" }}>remove</button>
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "grid", gridTemplateColumns: "1fr 80px auto auto", gap: 6, alignItems: "center", paddingTop: 4, borderTop: "1px solid var(--ab-border-soft)" }}>
        <select value={pickName} onChange={e => setPickName(e.target.value)} className="ab-mono" style={cfgInputStyle}>
          <option value="">add a device…</option>
          {availableUnregistered.length === 0 && <option value="" disabled>no unregistered devices detected</option>}
          {availableUnregistered.map(iface => (
            <option key={iface.name} value={iface.name}>
              {iface.name}{iface.driver ? ` · ${iface.driver}` : ""}{iface.channel_count ? ` · ${iface.channel_count}ch` : ""}
            </option>
          ))}
        </select>
        <input type="number" min={1} max={255} value={pickCount} onChange={e => setPickCount(e.target.value)} placeholder="ch" title="Channels to seed (defaults to detected device channel count)" className="ab-mono" style={{ ...cfgInputStyle }} />
        <button className="ab-btn" data-variant="primary" disabled={!pickName || status === "saving"} onClick={submit} style={{ height: 24, fontSize: 11 }}>add</button>
        <button className="ab-btn" data-variant="ghost" disabled={status === "saving"} onClick={onRefresh} style={{ height: 24, fontSize: 11 }} title="Re-scan host audio devices">refresh</button>
      </div>

      <div className="ab-mono" style={{ fontSize: 10, color: status === "error" ? "var(--ab-err)" : "var(--ab-fg-4)" }}>
        {status === "saving" ? "saving…"
          : status === "saved" ? "saved"
          : error || `${registered.length} device${registered.length === 1 ? "" : "s"} registered · ${availableUnregistered.length} unregistered detected`}
      </div>
    </div>
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

  const actionTimeout = useRef(null);
  const showActionState = (tone, message, opts = {}) => {
    if (actionTimeout.current) {
      window.clearTimeout(actionTimeout.current);
      actionTimeout.current = null;
    }
    setActionState({ tone, message, detail: opts.detail || "", sticky: !!opts.sticky });
    if (!opts.sticky) {
      actionTimeout.current = window.setTimeout(() => {
        setActionState((current) => current.message === message ? { tone: "idle", message: "", detail: "", sticky: false } : current);
        actionTimeout.current = null;
      }, tone === "err" ? 6000 : 2200);
    }
  };

  const summariseStream = () => {
    if (ch.entity_kind === "srt_transport") {
      const cfgRoot = window.AB.config || {};
      const t = (cfgRoot.srt_transports || []).find(x => x.id === ch.runtime_id);
      if (t) {
        const host = t.host || (t.mode === "listener" ? "*" : "—");
        return `${t.mode || "listener"} ${host}:${t.port || ""}`;
      }
    }
    return ch.runtime_id;
  };

  const handleProgramStart = async () => {
    if (!endpointBase) return;
    showActionState("info", "starting…", { sticky: true });
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
      showActionState("ok", `started · ${summariseStream()}`);
    } catch (error) {
      console.error("[program] start failed", error);
      const msg = String(error && error.message || error);
      showActionState("err", "start failed", { detail: msg });
    }
  };

  const handleProgramStop = async () => {
    if (!endpointBase) return;
    showActionState("info", "stopping…", { sticky: true });
    try {
      const response = await fetch(`${endpointBase}/stop`, { method: "POST" });
      if (!response.ok) throw new Error(await response.text());
      await refreshProgramView();
      showActionState("ok", "stopped");
    } catch (error) {
      console.error("[program] stop failed", error);
      const msg = String(error && error.message || error);
      showActionState("err", "stop failed", { detail: msg });
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
        showActionState("ok", "monitor stopped");
        return;
      }
      showActionState("info", "connecting monitor…", { sticky: true });

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
      showActionState("ok", "monitor connected");
    } catch (error) {
      console.error("[monitor] toggle failed", error);
      const msg = String(error && error.message || error);
      showActionState("err", "monitor failed", { detail: msg });
    }
  };

  const toneColor = actionState.tone === "err"  ? "var(--ab-err)"
                  : actionState.tone === "info" ? "var(--ab-fg-3)"
                  : "var(--ab-ok)";
  const visible = !!actionState.message;
  return (
    <div style={{ position: "relative", display: "inline-flex", gap: 3 }}>
      {running
        ? <IconBtn tone="err" title={supportsLifecycle ? "Stop stream" : "Runtime start/stop is not available for this stream type"} disabled={!supportsLifecycle} onClick={handleProgramStop}><ActIcon.stop /></IconBtn>
        : <IconBtn tone="acc" title={supportsLifecycle ? "Start stream" : "Runtime start/stop is not available for this stream type"} disabled={!supportsLifecycle} onClick={handleProgramStart}><ActIcon.play /></IconBtn>}
      <IconBtn tone={monitorActiveForRow ? "ok" : "info"} title={supportsListen ? (monitorActiveForRow ? "Stop monitor" : "Listen in browser") : "Listen is only available for RX SRT transports"} disabled={!supportsListen} onClick={handleListenToggle}><ActIcon.listen /></IconBtn>
      <IconBtn tone="info" title="Push to talk is not wired yet" disabled={true}><ActIcon.mic /></IconBtn>
      <IconBtn tone={expanded ? "acc" : "ghost"} title={expanded ? "Hide settings" : "Stream settings"} onClick={onToggle}><ActIcon.more /></IconBtn>
      <span
        className="ab-mono"
        title={actionState.detail || ""}
        style={{
          position: "absolute",
          top: "calc(100% + 4px)",
          right: 0,
          maxWidth: 280,
          padding: "3px 6px",
          borderRadius: 4,
          background: "var(--ab-surface-2)",
          border: `1px solid ${actionState.tone === "err" ? "rgba(239,68,68,0.4)" : "var(--ab-border-soft)"}`,
          color: toneColor,
          fontSize: 10,
          lineHeight: 1.2,
          whiteSpace: "nowrap",
          overflow: "hidden",
          textOverflow: "ellipsis",
          pointerEvents: actionState.detail ? "auto" : "none",
          opacity: visible ? 1 : 0,
          transform: visible ? "translateY(0)" : "translateY(-2px)",
          transition: "opacity 0.15s ease, transform 0.15s ease",
          zIndex: 5,
        }}>
        {actionState.message || " "}
      </span>
    </div>
  );
}

function ClockChip({ sync, ppm }) {
  const meta = sync === "lock"  ? { fg: "var(--ab-ok)",   bg: "var(--ab-ok-soft)",     bd: "rgba(34,197,94,0.35)",  label: "LOCK"  }
             : sync === "running" ? { fg: "var(--ab-ok)", bg: "var(--ab-ok-soft)",     bd: "rgba(34,197,94,0.35)",  label: "RUN"   }
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
  const tone = sync === "lock" || sync === "running" ? "ok" : sync === "slew" ? "warn" : sync === "drift" ? "err" : "muted";
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

function ChannelStrip({ streamId, transportRunning, group, sources, inputMeters, outputMeters, meterDirection, page = 0, perPage = 4 }) {
  const [pending, setPending] = useState({}); // index -> { source_id?, label? }
  const [savingIdx, setSavingIdx] = useState(null);
  const [error, setError] = useState("");
  // Per-channel mute is UI-only — backend has no `muted` flag yet.
  // We swap source_id to silence and remember the prior value so the source
  // can be restored when the user un-mutes within this session.
  const [muteMemo, setMuteMemo] = useState({}); // index -> { prevSource }

  const channelByIndex = useMemo(() => {
    const map = new Map();
    (group.channels || []).forEach(c => map.set(c.index, c));
    return map;
  }, [group]);

  // Group sources by capture device so the dropdown reads "Device → Ch N",
  // grouped under <optgroup> labels. Non-dante sources (silence/tone) fall
  // into a "Generators" group.
  const sourceGroups = useMemo(() => {
    const groups = new Map();
    const generators = [];
    sources.forEach(s => {
      if (s.id === SILENCE_DEFAULT_SOURCE_ID) { generators.unshift([s.id, "Silence"]); return; }
      if (s.kind === "dante_input") {
        const deviceLabel = s.interface_name || "(no device)";
        const label = s.dante_channel ? `Ch ${String(s.dante_channel).padStart(2, "0")}${s.name && !s.name.startsWith(s.interface_name || "") ? " · " + s.name : ""}` : (s.name || s.id);
        if (!groups.has(deviceLabel)) groups.set(deviceLabel, []);
        groups.get(deviceLabel).push([s.id, label]);
      } else {
        generators.push([s.id, s.name || s.id]);
      }
    });
    // Stable: silence/tone group first, then devices alphabetically.
    const out = [];
    if (generators.length) out.push(["Generators", generators]);
    for (const name of Array.from(groups.keys()).sort()) out.push([name, groups.get(name)]);
    return out;
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
  const toggleMute = (idx, currentSource) => {
    const muted = currentSource === SILENCE_DEFAULT_SOURCE_ID;
    if (muted) {
      const restore = (muteMemo[idx] && muteMemo[idx].prevSource) || SILENCE_DEFAULT_SOURCE_ID;
      setMuteMemo(m => { const n = { ...m }; delete n[idx]; return n; });
      setSource(idx, restore);
    } else {
      setMuteMemo(m => ({ ...m, [idx]: { prevSource: currentSource } }));
      setSource(idx, SILENCE_DEFAULT_SOURCE_ID);
    }
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

  const isRx = meterDirection === "in";
  const channelCount = group.channel_count || 0;
  const start = page * perPage;
  const end = Math.min(channelCount, start + perPage);
  const rows = [];
  for (let i = start + 1; i <= end; i++) {
    const ch = channelByIndex.get(i) || { index: i, source_id: SILENCE_DEFAULT_SOURCE_ID, label: `Ch ${String(i).padStart(2, "0")}` };
    const effectiveSource = (pending[i] && pending[i].source_id) || ch.source_id;
    const isMuted = !isRx && effectiveSource === SILENCE_DEFAULT_SOURCE_ID;
    const meterCh = sourceMeterChannel(effectiveSource);
    const meter = isRx
      ? ((inputMeters || [])[i - 1] || {})
      : ((outputMeters || [])[i - 1] || {});
    rows.push(
      <div key={`${streamId}-ch-${i}`}
           style={{ display: "grid", gridTemplateColumns: "38px 110px 140px 1fr 44px 22px 22px",
                    gap: 8, alignItems: "center", fontSize: 11,
                    padding: "4px 10px", borderTop: "1px solid var(--ab-border-soft)",
                    opacity: isMuted ? 0.55 : 1 }}>
        <span className="ab-mono" style={{ color: "var(--ab-fg-4)" }}>ch-{String(i).padStart(2, "0")}</span>
        <input
          className="ab-mono"
          value={(pending[i] && pending[i].label) ?? (ch.label || "")}
          onChange={e => setPending(p => ({ ...p, [i]: { ...(p[i] || {}), label: e.target.value } }))}
          onBlur={() => { const v = pending[i] && pending[i].label; if (v != null && v !== ch.label) persistChannel(i, { label: v }); }}
          placeholder={`Ch ${String(i).padStart(2, "0")}`}
          style={{ ...cfgInputStyle, height: 20, fontSize: 11 }}
        />
        {isRx ? (
          <span className="ab-mono"
                title="RX channels currently route to a decode sink. Per-channel Dante output routing is pending backend support."
                style={{ fontSize: 11, color: "var(--ab-fg-4)", height: 20, lineHeight: "20px", paddingLeft: 4, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
            → Dante Out {String(i).padStart(2, "0")}
          </span>
        ) : (
          <select
            className="ab-mono"
            value={effectiveSource}
            onChange={e => setSource(i, e.target.value)}
            style={{ ...cfgInputStyle, height: 20, fontSize: 11 }}
          >
            {sourceGroups.map(([groupLabel, opts]) => (
              <optgroup key={groupLabel} label={groupLabel}>
                {opts.map(([k, lbl]) => <option key={k} value={k}>{lbl}</option>)}
              </optgroup>
            ))}
          </select>
        )}
        <div style={{ display: "flex", alignItems: "center", gap: 6, minWidth: 0 }}>
          <Meter level={meter.rms_dbfs} peak={meter.peak_dbfs} w={null} />
        </div>
        <span className="ab-mono" style={{ fontSize: 10, color: "var(--ab-fg-3)", textAlign: "right" }}>{fmtDb(meter.rms_dbfs)}</span>
        <IconBtn
          tone={isMuted ? "err" : "ghost"}
          disabled={isRx}
          title={isRx ? "Mute on RX is not wired yet" : (isMuted ? "Unmute (restore source)" : "Mute channel (route to silence)")}
          onClick={() => !isRx && toggleMute(i, effectiveSource)}>
          <svg width="10" height="10" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.2">
            <path d="M2 4.5h2L7 2v8L4 7.5H2z" fill={isMuted ? "currentColor" : "none"} />
            {isMuted && <path d="M9 4l2 4M11 4l-2 4" />}
          </svg>
        </IconBtn>
        <IconBtn
          tone="info"
          disabled={!transportRunning || isMuted || isRx}
          title={isRx ? "Per-channel monitor for RX is not wired yet" : (transportRunning ? (isMuted ? "Unmute first" : "Listen to this channel") : "Stream must be running")}
          onClick={() => !isRx && startMonitor(i, effectiveSource)}>
          <ActIcon.listen />
        </IconBtn>
      </div>
    );
  }
  // Reserve slots so card height stays static when channel_count < perPage.
  for (let i = end; i < start + perPage; i++) {
    rows.push(<div key={`${streamId}-empty-${i}`} style={{ height: 28, borderTop: "1px solid var(--ab-border-soft)" }} />);
  }
  return (
    <div>
      {rows}
      {error && <div className="ab-mono" style={{ padding: "3px 10px", color: "var(--ab-err)", fontSize: 10.5 }}>{error}</div>}
    </div>
  );
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
        const n = clampInt(channelCount, 1, 255);
        // Both TX and RX get a channel group — RX uses it as a channel-count
        // carrier (channels are silence-filled and unused on the decode path
        // until backend exposes Dante output routing).
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
          opus: (direction === "tx" && opusOverride) ? {
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
            <CfgField label="Channels">
              <NumberField value={channelCount} min={1} max={255} onChange={setChannelCount} suffix="ch" />
            </CfgField>
            {direction === "tx" && (
              <>
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
          <CfgField label="Path"><span className="ab-mono" style={cfgVal}>{kind === "srt_transport" && direction === "tx" ? `${clampInt(channelCount, 1, 255)}ch silence-filled · group -> SRT` : kind === "srt_transport" ? "POST /api/srt-transports" : "POST /api/webrtc-streams"}</span></CfgField>
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

function StreamDrawer({ ch, onClose }) {
  const cfg = window.AB.config || {};
  const details = ch.details || {};
  const isSrt = ch.entity_kind === "srt_transport";
  const isTx = ch.direction === "out";
  const programDefaults = (cfg.program && cfg.program.opus) || defaultOpus("srt");
  const programSrt = cfg.program || {};
  const groupId = isSrt ? ((details.encode_group_ids || [])[0]) : null;
  const groupCfg = groupId ? (cfg.encode_groups || []).find(g => g.id === groupId) : null;

  // Identity
  const [name, setName] = useState(ch.name || "");

  // Transport
  const [direction, setDirection] = useState(details.direction || (isTx ? "tx" : "rx"));
  const [mode, setMode] = useState(details.mode || "listener");
  const [host, setHost] = useState(details.host || "");
  const [port, setPort] = useState(String(details.port || ((cfg.network && cfg.network.srt_port) || 9000)));
  const [latencyMs, setLatencyMs] = useState(String(details.latency_ms || (programSrt.srt_latency_ms || 240)));

  // SRT extras (per-transport overrides; backend may not persist all of these yet).
  const hasEncryptionOverride = details.encryption_enabled != null;
  const [overrideEncryption, setOverrideEncryption] = useState(hasEncryptionOverride);
  const [encEnabled, setEncEnabled] = useState(details.encryption_enabled != null ? !!details.encryption_enabled : !!programSrt.encryption_enabled);
  const [encStrength, setEncStrength] = useState(details.encryption_strength || programSrt.encryption_strength || "aes-256");

  const hasOverheadOverride = details.srt_overhead_bandwidth_percent != null;
  const [overrideOverhead, setOverrideOverhead] = useState(hasOverheadOverride);
  const [overheadPct, setOverheadPct] = useState(String(details.srt_overhead_bandwidth_percent ?? (programSrt.srt_overhead_bandwidth_percent ?? 25)));

  const hasBandwidthOverride = details.srt_bandwidth_mode != null || details.inbound_bandwidth_cap_kbps != null;
  const [overrideBandwidth, setOverrideBandwidth] = useState(hasBandwidthOverride);
  const [bandwidthMode, setBandwidthMode] = useState(details.srt_bandwidth_mode || programSrt.srt_bandwidth_mode || "auto");
  const [bandwidthCap, setBandwidthCap] = useState(String(details.inbound_bandwidth_cap_kbps ?? programSrt.inbound_bandwidth_cap_kbps ?? ""));

  // Clock (RX only — only meaningful when decoding)
  const hasClockOverride = details.clock_recovery_mode != null || (details.free_running_clock && details.free_running_clock.jitter_buffer_ms != null);
  const [overrideClock, setOverrideClock] = useState(hasClockOverride);
  const [clockMode, setClockMode] = useState(details.clock_recovery_mode || programSrt.clock_recovery_mode || "adaptive");
  const [clockBufferMs, setClockBufferMs] = useState(String(
    (details.free_running_clock && details.free_running_clock.jitter_buffer_ms)
    ?? (programSrt.free_running_clock && programSrt.free_running_clock.jitter_buffer_ms)
    ?? 500));

  // OPUS encode (TX only — bound to the encode group)
  const groupOpus = (groupCfg && groupCfg.opus) || programDefaults;
  const hasOpusOverride = groupCfg && groupCfg.opus
    && JSON.stringify(groupCfg.opus) !== JSON.stringify(programDefaults);
  const [overrideOpus, setOverrideOpus] = useState(!!hasOpusOverride);
  const [opus, setOpus] = useState({ ...groupOpus });

  // Channel count (editable post-create; bound to the encode group)
  const [channelCount, setChannelCount] = useState(String(groupCfg ? groupCfg.channel_count : 2));

  // WebRTC source
  const [sourceId, setSourceId] = useState(details.source_id || "");

  const [saveState, setSaveState] = useState("idle");
  const [saveError, setSaveError] = useState("");

  const endpointBase = isSrt
    ? `/api/srt-transports/${encodeURIComponent(ch.runtime_id)}`
    : `/api/webrtc-streams/${encodeURIComponent(ch.runtime_id)}`;

  const buildBody = () => {
    if (!isSrt) {
      return {
        id: details.id || ch.runtime_id,
        name: name.trim() || ch.name,
        direction,
        ...(sourceId.trim() ? { source_id: sourceId.trim() } : {}),
      };
    }
    const body = {
      id: details.id || ch.runtime_id,
      name: name.trim() || ch.name,
      direction,
      mode,
      port: clampInt(port, 1, 65535),
      latency_ms: clampInt(latencyMs, 20, 8000),
      ...(host.trim() ? { host: host.trim() } : {}),
      encode_group_ids: details.encode_group_ids || [],
    };
    if (overrideEncryption) {
      body.encryption_enabled = !!encEnabled;
      body.encryption_strength = encStrength;
    }
    if (overrideOverhead) {
      body.srt_overhead_bandwidth_percent = clampInt(overheadPct, 0, 100);
    }
    if (overrideBandwidth) {
      body.srt_bandwidth_mode = bandwidthMode;
      const cap = String(bandwidthCap).trim();
      body.inbound_bandwidth_cap_kbps = bandwidthMode === "manual" && cap ? clampInt(cap, 64, 100000) : null;
    }
    if (!isTx && overrideClock) {
      body.clock_recovery_mode = clockMode;
      body.free_running_clock = { jitter_buffer_ms: clampInt(clockBufferMs, 20, 5000) };
    }
    return body;
  };

  const saveGroup = async () => {
    if (!groupCfg) return;
    const newCount = clampInt(channelCount, 1, 255);
    const existing = new Map((groupCfg.channels || []).map(c => [c.index, c]));
    const channels = [];
    for (let i = 1; i <= newCount; i++) {
      channels.push(existing.get(i) || {
        index: i,
        source_id: SILENCE_DEFAULT_SOURCE_ID,
        label: `Ch ${String(i).padStart(2, "0")}`,
        gain_db: 0,
      });
    }
    const body = {
      id: groupCfg.id,
      name: groupCfg.name,
      channel_count: newCount,
      channels,
      enabled: groupCfg.enabled !== false,
      opus: overrideOpus ? {
        bitrate_kbps: clampInt(opus.bitrate_kbps, 16, 512),
        bitrate_mode: opus.bitrate_mode || "cbr",
        frame_ms: clampInt(opus.frame_ms, 2, 60),
        complexity: clampInt(opus.complexity, 0, 10),
        inband_fec: !!opus.inband_fec,
        expected_packet_loss_percent: clampInt(opus.expected_packet_loss_percent, 0, 30),
      } : { ...programDefaults },
    };
    const r = await fetch(`/api/encode-groups/${encodeURIComponent(groupCfg.id)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(await r.text());
  };

  const save = async () => {
    setSaveState("saving"); setSaveError("");
    try {
      const r = await fetch(endpointBase, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(buildBody()),
      });
      if (!r.ok) throw new Error(await r.text());
      if (isSrt && groupCfg) await saveGroup();
      await (window.AB.refreshAll ? window.AB.refreshAll() : Promise.resolve());
      setSaveState("saved");
      window.setTimeout(() => setSaveState("idle"), 1500);
    } catch (e) {
      console.error("[stream-drawer] save failed", e);
      setSaveError(String(e.message || e));
      setSaveState("error");
    }
  };

  const remove = async () => {
    setSaveState("saving"); setSaveError("");
    try {
      const r = await fetch(endpointBase, { method: "DELETE" });
      if (!r.ok) throw new Error(await r.text());
      await (window.AB.refreshAll ? window.AB.refreshAll() : Promise.resolve());
      onClose();
    } catch (e) {
      console.error("[stream-drawer] delete failed", e);
      setSaveError(String(e.message || e));
      setSaveState("error");
    }
  };

  const duplicate = async () => {
    setSaveState("saving"); setSaveError("");
    try {
      const baseId = details.id || ch.runtime_id;
      const newId = `${baseId}-copy`;
      const newName = `${name || ch.name} (copy)`;
      // Clone the encode group first (TX path), then the transport pointing at it.
      let newGroupIds = [];
      if (isSrt && isTx && groupCfg) {
        const newGroupId = `${groupCfg.id}-copy`;
        const groupBody = {
          id: newGroupId,
          name: `${groupCfg.name} (copy)`,
          channel_count: groupCfg.channel_count,
          channels: groupCfg.channels,
          opus: groupCfg.opus,
          enabled: groupCfg.enabled !== false,
        };
        const gr = await fetch(`/api/encode-groups`, {
          method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify(groupBody),
        });
        if (!gr.ok) throw new Error(await gr.text());
        newGroupIds = [newGroupId];
      }
      const body = { ...buildBody(), id: newId, name: newName };
      if (isSrt) body.encode_group_ids = newGroupIds;
      const post = await fetch(isSrt ? "/api/srt-transports" : "/api/webrtc-streams", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!post.ok) throw new Error(await post.text());
      await (window.AB.refreshAll ? window.AB.refreshAll() : Promise.resolve());
      setSaveState("saved");
      window.setTimeout(() => setSaveState("idle"), 1500);
    } catch (e) {
      console.error("[stream-drawer] duplicate failed", e);
      setSaveError(String(e.message || e));
      setSaveState("error");
    }
  };

  const inheritEncSummary = `${programSrt.encryption_enabled ? "on" : "off"}${programSrt.encryption_strength ? " · " + programSrt.encryption_strength : ""}`;
  const inheritBwSummary = `${programSrt.srt_bandwidth_mode || "auto"} · overhead ${programSrt.srt_overhead_bandwidth_percent ?? 25}%`;
  const inheritClkSummary = `${programSrt.clock_recovery_mode || "adaptive"}`;
  const inheritOpusSummary = `${programDefaults.bitrate_kbps} kbps · ${(programDefaults.bitrate_mode || "cbr").toUpperCase()} · ${programDefaults.frame_ms}ms`;

  return (
    <div style={{ padding: "10px 12px 12px", display: "flex", flexDirection: "column", gap: 10 }}>
      {/* Identity row */}
      <div style={{ display: "grid", gridTemplateColumns: "1fr auto", gap: 10, alignItems: "center" }}>
        <div style={{ display: "grid", gridTemplateColumns: "auto 1fr", gap: "4px 10px", alignItems: "center" }}>
          <span className="ab-mono" style={dlbl}>Name</span>
          <input value={name} onChange={e => setName(e.target.value)} className="ab-mono" style={{ ...cfgInputStyle, height: 22 }} />
          <span className="ab-mono" style={dlbl}>ID</span>
          <span className="ab-mono" style={{ ...cfgVal, color: "var(--ab-fg-3)" }}>{details.id || ch.runtime_id}</span>
        </div>
        <button className="ab-btn" data-variant="ghost" style={{ height: 22, fontSize: 11, alignSelf: "start" }} onClick={onClose}>close</button>
      </div>

      {/* Transport — packed horizontally */}
      {isSrt && (
        <DrawerSection title="Transport">
          <FieldRow>
            <Field label="Direction" w={160}><Segmented value={direction} onChange={setDirection} options={[["tx", "TX"], ["rx", "RX"]]} /></Field>
            <Field label="Mode" w={240}><Segmented value={mode} onChange={setMode} options={[["listener", "Listen"], ["caller", "Call"], ["rendezvous", "Rendez."]]} /></Field>
          </FieldRow>
          <FieldRow>
            <Field label="Host" grow><input value={host} onChange={e => setHost(e.target.value)} className="ab-mono" style={{ ...cfgInputStyle, height: 22 }} placeholder={mode === "listener" ? "optional" : "required"} /></Field>
            <Field label="Port" w={90}><input type="number" min={1} max={65535} value={port} onChange={e => setPort(e.target.value)} className="ab-mono" style={{ ...cfgInputStyle, height: 22 }} /></Field>
            <Field label="Latency" w={90}><input type="number" min={20} max={8000} value={latencyMs} onChange={e => setLatencyMs(e.target.value)} className="ab-mono" style={{ ...cfgInputStyle, height: 22 }} /></Field>
            {groupCfg && (
              <Field label="Channels" w={90}>
                <input type="number" min={1} max={255} value={channelCount}
                       onChange={e => setChannelCount(e.target.value)}
                       title="OPUS supports up to 255 channels (multistream). TX currently encodes up to 8 channels natively; >8 requires multistream encoding in the gstreamer pipeline — not yet wired, will fail to start."
                       className="ab-mono" style={{ ...cfgInputStyle, height: 22 }} />
              </Field>
            )}
          </FieldRow>
        </DrawerSection>
      )}

      {/* Compact override sections — collapsed by default, fields appear on toggle */}
      {isSrt && (
        <OverrideSection
          title="Encryption"
          inheritSummary={inheritEncSummary}
          on={overrideEncryption}
          onToggle={setOverrideEncryption}>
          <FieldRow>
            <Field label="Encryption" w={120}><ToggleButton value={encEnabled} onChange={setEncEnabled} /></Field>
            <Field label="Strength" grow>
              <select value={encStrength} onChange={e => setEncStrength(e.target.value)} disabled={!encEnabled} className="ab-mono" style={{ ...cfgInputStyle, height: 22, opacity: encEnabled ? 1 : 0.5 }}>
                <option value="aes-128">aes-128</option>
                <option value="aes-192">aes-192</option>
                <option value="aes-256">aes-256</option>
              </select>
            </Field>
          </FieldRow>
        </OverrideSection>
      )}

      {isSrt && (
        <OverrideSection
          title="Bandwidth & Overhead"
          inheritSummary={inheritBwSummary}
          on={overrideOverhead || overrideBandwidth}
          onToggle={v => { setOverrideOverhead(v); setOverrideBandwidth(v); }}>
          <FieldRow>
            <Field label="Overhead" w={120}><input type="number" min={0} max={100} value={overheadPct} onChange={e => setOverheadPct(e.target.value)} className="ab-mono" style={{ ...cfgInputStyle, height: 22 }} /></Field>
            <Field label="Bandwidth" w={180}><Segmented value={bandwidthMode} onChange={setBandwidthMode} options={[["auto", "Auto"], ["manual", "Manual"]]} /></Field>
            {bandwidthMode === "manual" && (
              <Field label="Cap (kbps)" grow><input type="number" min={64} max={100000} value={bandwidthCap} onChange={e => setBandwidthCap(e.target.value)} className="ab-mono" style={{ ...cfgInputStyle, height: 22 }} placeholder="none" /></Field>
            )}
          </FieldRow>
        </OverrideSection>
      )}

      {isSrt && !isTx && (
        <OverrideSection
          title="Clock recovery"
          inheritSummary={inheritClkSummary}
          on={overrideClock}
          onToggle={setOverrideClock}>
          <FieldRow>
            <Field label="Mode" w={220}><Segmented value={clockMode} onChange={setClockMode} options={[["adaptive", "Adaptive"], ["free_running", "Free run"]]} /></Field>
            {clockMode === "free_running" && (
              <Field label="Buffer (ms)" grow><input type="number" min={20} max={5000} step={10} value={clockBufferMs} onChange={e => setClockBufferMs(e.target.value)} className="ab-mono" style={{ ...cfgInputStyle, height: 22 }} /></Field>
            )}
          </FieldRow>
        </OverrideSection>
      )}

      {isSrt && isTx && groupCfg && (
        <OverrideSection
          title="OPUS encode"
          inheritSummary={inheritOpusSummary}
          on={overrideOpus}
          onToggle={setOverrideOpus}>
          <FieldRow>
            <Field label="Bitrate" w={120}><input type="number" min={16} max={512} step={8} value={opus.bitrate_kbps} onChange={e => setOpus({ ...opus, bitrate_kbps: parseInt(e.target.value, 10) || 0 })} className="ab-mono" style={{ ...cfgInputStyle, height: 22 }} /></Field>
            <Field label="Mode" grow><Segmented value={opus.bitrate_mode || "cbr"} onChange={v => setOpus({ ...opus, bitrate_mode: v })} options={OPUS_RATE_MODES} /></Field>
          </FieldRow>
          <FieldRow>
            <Field label="Frame (ms)" w={100}><input type="number" min={2} max={60} value={opus.frame_ms} onChange={e => setOpus({ ...opus, frame_ms: parseInt(e.target.value, 10) || 0 })} className="ab-mono" style={{ ...cfgInputStyle, height: 22 }} /></Field>
            <Field label="Complexity" w={100}><input type="number" min={0} max={10} value={opus.complexity} onChange={e => setOpus({ ...opus, complexity: parseInt(e.target.value, 10) || 0 })} className="ab-mono" style={{ ...cfgInputStyle, height: 22 }} /></Field>
            <Field label="FEC" w={64}><ToggleButton value={!!opus.inband_fec} onChange={v => setOpus({ ...opus, inband_fec: v })} /></Field>
            <Field label="Exp. loss %" grow><input type="number" min={0} max={30} value={opus.expected_packet_loss_percent} onChange={e => setOpus({ ...opus, expected_packet_loss_percent: parseInt(e.target.value, 10) || 0 })} className="ab-mono" style={{ ...cfgInputStyle, height: 22 }} /></Field>
          </FieldRow>
        </OverrideSection>
      )}

      {!isSrt && (
        <DrawerSection title="Stream">
          <FieldRow>
            <Field label="Direction" w={160}><Segmented value={direction} onChange={setDirection} options={[["tx", "TX"], ["rx", "RX"]]} /></Field>
            <Field label="Source id" grow><input value={sourceId} onChange={e => setSourceId(e.target.value)} className="ab-mono" style={{ ...cfgInputStyle, height: 22 }} placeholder="optional" /></Field>
          </FieldRow>
        </DrawerSection>
      )}

      {/* Footer actions */}
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        {saveError && <span className="ab-mono" style={{ fontSize: 10.5, color: "var(--ab-err)", flex: "1 1 100%" }}>{saveError}</span>}
        <button className="ab-btn" data-variant="danger" disabled={saveState === "saving"} onClick={remove} style={{ height: 24, fontSize: 11 }}>delete</button>
        <button className="ab-btn" data-variant="ghost" disabled={saveState === "saving"} onClick={duplicate} style={{ height: 24, fontSize: 11 }}>duplicate</button>
        <div style={{ flex: 1 }} />
        <button className="ab-btn" data-variant="primary" disabled={saveState === "saving"} onClick={save} style={{ height: 24, fontSize: 11 }}>
          {saveState === "saving" ? "saving…" : saveState === "saved" ? "saved" : saveState === "error" ? "retry save" : "save changes"}
        </button>
      </div>
    </div>
  );
}

// Compact drawer primitives
const dlbl = { fontSize: 10, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-4)" };

function DrawerSection({ title, right, children }) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", borderBottom: "1px solid var(--ab-border-soft)", marginBottom: 6 }}>
        <span className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-2)", fontWeight: 600 }}>{title}</span>
        {right && <div style={{ marginLeft: "auto" }}>{right}</div>}
      </div>
      <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>{children}</div>
    </div>
  );
}

function OverrideSection({ title, inheritSummary, on, onToggle, children }) {
  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "4px 0", borderBottom: "1px solid var(--ab-border-soft)", marginBottom: on ? 6 : 0 }}>
        <span className="ab-mono" style={{ fontSize: 10.5, letterSpacing: 0.06, textTransform: "uppercase", color: "var(--ab-fg-2)", fontWeight: 600 }}>{title}</span>
        {!on && (
          <span className="ab-mono" style={{ fontSize: 10, color: "var(--ab-fg-5)" }} title="Value inherited from global defaults">
            inherits · {inheritSummary}
          </span>
        )}
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 6 }}>
          <span className="ab-mono" style={{ fontSize: 10, color: "var(--ab-fg-4)" }}>override</span>
          <ToggleButton value={on} onChange={onToggle} />
        </div>
      </div>
      {on && <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>{children}</div>}
    </div>
  );
}

function FieldRow({ children }) {
  return <div style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}>{children}</div>;
}
function Field({ label, w, grow, children }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0, width: w || undefined, flex: grow ? "1 1 0" : "0 0 auto" }}>
      <span className="ab-mono" style={dlbl}>{label}</span>
      {children}
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
