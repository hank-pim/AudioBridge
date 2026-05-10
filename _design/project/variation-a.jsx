// Variation A — "NOC". Big KPI strip + dense 64-channel table + live events
// rail. The default operator view: at-a-glance link health up top, click-to-
// drill-down on any channel below.

function VariationA({ density = 8, showEventsRail = true, showSystemCard = true, kpiCount = 6 }) {
  const { PROGRAM, TALKBACK, SYS, SLO, CHANNELS, SERIES } = window.AB;
  const [tab, setTab] = useState("all"); // all | rx | tx | issues
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    let rows = CHANNELS;
    if (tab === "rx") rows = rows.filter(c => c.direction === "in");
    if (tab === "tx") rows = rows.filter(c => c.direction === "out");
    if (tab === "issues")  rows = rows.filter(c => c.state === "warn" || c.state === "err");
    if (query) rows = rows.filter(c => c.name.toLowerCase().includes(query.toLowerCase()) || String(c.id).includes(query));
    return rows;
  }, [tab, query]);

  // Density 1..10 → row height 36..22
  const rowH = Math.round(38 - (density - 1) * (16 / 9));

  return (
    <div className="ab-frame ab-root">
      <TopBar active="streams" alerts={2} />

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
          <KpiTile
            label="Resampler PLL"
            value="−0.18"
            unit=" ppm"
            delta="lock"
            deltaTone="ok"
            spark={SERIES.rtt}
            footer="shared ratio · 48.000 kHz · 1 domain"
          />
            deltaTone="muted"
            spark={SERIES.cpu}
            sparkTone="muted"
            footer={`${SYS.temp_c}°C · up ${fmtUptime(SYS.uptime_s)}`}
          />
        )}
      </div>

      {/* Body grid: streams table | events rail */}
      <div style={{ flex: 1, display: "grid", gridTemplateColumns: (showEventsRail || showSystemCard) ? "1fr 320px" : "1fr", gap: 12, padding: 12, minHeight: 0 }}>
        {/* Streams */}
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
              <button className="ab-btn" style={{ height: 22, fontSize: 11 }}><Icon.refresh /> 1s</button>
            </>
          )}
        >
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
                {filtered.map(c => {
                  return (
                    <tr key={c.id} data-state={c.state === "idle" ? "muted" : null} style={{ height: rowH }}>
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
                      <td><RowActions ch={c} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </Card>

        {/* Events rail + system tile */}
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
                <div className="ab-mono" style={{ fontSize: 10, color: "var(--ab-fg-4)", marginTop: 3 }}>shared ratio · 48.000 kHz · last slew 6m ago</div>
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

window.VariationA = VariationA;

const TYPE_META = {
  "PGM":   { tone: "acc",   title: "Program feed (SRT)" },
  "MIX-":  { tone: "acc",   title: "Mix-minus return (SRT)" },
  "SRC":   { tone: "muted", title: "Source feed (SRT)" },
  "BUS":   { tone: "muted", title: "Submix bus (SRT)" },
  "AUX":   { tone: "muted", title: "Aux send (SRT)" },
  "IFB":   { tone: "info",  title: "Interruptible foldback · 1-way voice (WebRTC)" },
  "PL":    { tone: "info",  title: "Party line · 2-way voice (WebRTC)" },
  "TB":    { tone: "info",  title: "Talkback (WebRTC)" },
  "TONE":  { tone: "ok",    title: "Test tone (SRT)" },
  "SLATE": { tone: "ok",    title: "Slate mic (SRT)" },
  "GPI":   { tone: "muted", title: "GPIO control" },
  "SPARE": { tone: "muted", title: "Unrouted" },
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

function IconBtn({ tone = "ghost", title, disabled, children }) {
  const fg = disabled ? "var(--ab-fg-5)"
    : tone === "acc"  ? "var(--ab-accent)"
    : tone === "info" ? "var(--ab-info)"
    : tone === "err"  ? "var(--ab-err)"
    : tone === "ok"   ? "var(--ab-ok)"
    :                   "var(--ab-fg-2)";
  return (
    <button title={title} disabled={disabled} style={{
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

function RowActions({ ch }) {
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
      <IconBtn title="More…"><ActIcon.more /></IconBtn>
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
