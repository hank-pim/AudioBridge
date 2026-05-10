// Audiobridge — shared components used across all three variations.
// Exports to window so each variation script can use them.

const { useState, useEffect, useMemo, useRef } = React;

// ── Iconography (tiny stroke icons; no emoji) ─────────────────────────────
const Icon = {
  arrow:    (p) => (<svg width="10" height="10" viewBox="0 0 10 10" fill="none" {...p}><path d="M2 5h6m0 0L5.5 2.5M8 5L5.5 7.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>),
  arrow2:   (p) => (<svg width="14" height="10" viewBox="0 0 14 10" fill="none" {...p}><path d="M0 5h13m0 0L9 1.5M13 5L9 8.5" stroke="currentColor" strokeWidth="1.2" strokeLinecap="round"/></svg>),
  bidir:    (p) => (<svg width="14" height="10" viewBox="0 0 14 10" fill="none" {...p}><path d="M0 3.5h11M11 3.5L8 1M11 3.5L8 6M14 6.5H3M3 6.5l3-2.5M3 6.5l3 2.5" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round"/></svg>),
  play:     (p) => (<svg width="9" height="9" viewBox="0 0 9 9" {...p}><path d="M2 1.5l5 3-5 3z" fill="currentColor"/></svg>),
  stop:     (p) => (<svg width="9" height="9" viewBox="0 0 9 9" {...p}><rect x="2" y="2" width="5" height="5" fill="currentColor" rx="0.5"/></svg>),
  refresh:  (p) => (<svg width="11" height="11" viewBox="0 0 11 11" fill="none" {...p}><path d="M9 5.5A3.5 3.5 0 1 1 5.5 2M9 2v3.2H6" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round" strokeLinejoin="round"/></svg>),
  alert:    (p) => (<svg width="11" height="11" viewBox="0 0 11 11" fill="none" {...p}><path d="M5.5 1.5L10 9.5H1z" stroke="currentColor" strokeWidth="1.1" strokeLinejoin="round"/><path d="M5.5 4.6v2.2M5.5 8.1v.4" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round"/></svg>),
  lock:     (p) => (<svg width="10" height="11" viewBox="0 0 10 11" fill="none" {...p}><rect x="1.5" y="5" width="7" height="5" rx="1" stroke="currentColor" strokeWidth="1.1"/><path d="M3 5V3.5a2 2 0 0 1 4 0V5" stroke="currentColor" strokeWidth="1.1"/></svg>),
  dot3:     (p) => (<svg width="12" height="3" viewBox="0 0 12 3" {...p}><circle cx="1.5" cy="1.5" r="1" fill="currentColor"/><circle cx="6"   cy="1.5" r="1" fill="currentColor"/><circle cx="10.5"cy="1.5" r="1" fill="currentColor"/></svg>),
  search:   (p) => (<svg width="11" height="11" viewBox="0 0 11 11" fill="none" {...p}><circle cx="4.5" cy="4.5" r="3" stroke="currentColor" strokeWidth="1.1"/><path d="M7 7l3 3" stroke="currentColor" strokeWidth="1.1" strokeLinecap="round"/></svg>),
  cog:      (p) => (<svg width="11" height="11" viewBox="0 0 11 11" fill="none" {...p}><circle cx="5.5" cy="5.5" r="1.5" stroke="currentColor" strokeWidth="1.1"/><path d="M5.5 1v1.5M5.5 8.5V10M1 5.5h1.5M8.5 5.5H10M2.3 2.3l1 1M7.7 7.7l1 1M2.3 8.7l1-1M7.7 3.3l1-1" stroke="currentColor" strokeWidth="1"/></svg>),
};

// ── Pill / chip ────────────────────────────────────────────────────────────
function Chip({ tone="muted", children, mono=true }) {
  return <span className="ab-chip" data-tone={tone} style={{ fontFamily: mono ? "var(--ab-mono)" : "var(--ab-font)" }}>{children}</span>;
}
function Dot({ tone="ok" }) { return <span className="ab-dot" data-tone={tone} />; }

// ── Sparkline (SVG path, area + line) ─────────────────────────────────────
function Sparkline({ data, w=84, h=22, tone, area=true }) {
  const nums = (data || []).filter(v => Number.isFinite(v));
  if (nums.length < 2) {
    // Reserve the space so layout doesn't jump once samples accumulate.
    return <svg className="ab-spark" data-tone={tone} width={w} height={h} viewBox={`0 0 ${w} ${h}`} />;
  }
  const min = Math.min(...nums), max = Math.max(...nums);
  const span = (max - min) || 1;
  const stepX = w / (nums.length - 1);
  const pts = nums.map((v, i) => {
    const x = i * stepX;
    const y = h - ((v - min) / span) * (h - 2) - 1;
    return [x, y];
  });
  const line = pts.map((p, i) => (i ? "L" : "M") + p[0].toFixed(1) + " " + p[1].toFixed(1)).join(" ");
  const fill = line + ` L ${w} ${h} L 0 ${h} Z`;
  return (
    <svg className="ab-spark" data-tone={tone} width={w} height={h} viewBox={`0 0 ${w} ${h}`}>
      {area && <path className="ab-spark-area" d={fill} />}
      <path className="ab-spark-line" d={line} />
    </svg>
  );
}

// ── Audio meter (level + peak hold) ────────────────────────────────────────
// Level in dBFS [-60, 0]; we treat -60 → 0%, 0dBFS → 100%.
function Meter({ level, peak, w, h=6 }) {
  const norm = (db) => Math.max(0, Math.min(1, (db + 60) / 60));
  const lv = norm(level);
  const pk = peak != null ? norm(peak) : null;
  return (
    <div className="ab-meter" style={{ width: w, height: h }}>
      <div className="ab-meter-fill" style={{ width: (lv * 100) + "%" }} />
      {pk !== null && <div className="ab-meter-peak" style={{ left: "calc(" + (pk * 100) + "% - 0.5px)" }} />}
      <div className="ab-meter-ticks" />
    </div>
  );
}

// ── KPI tile (label + big number + sparkline + delta) ─────────────────────
function KpiTile({ label, value, unit, delta, deltaTone="muted", spark, sparkTone, footer, hint }) {
  return (
    <div className="ab-card" style={{ padding: "10px 12px", display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <span className="ab-kpi-label">{label}</span>
        {hint && <span className="ab-mono" style={{ fontSize: 10, color: "var(--ab-fg-4)" }}>{hint}</span>}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span className="ab-kpi-value">{value}{unit && <span className="ab-kpi-unit">{unit}</span>}</span>
        {delta && (
          <span className="ab-kpi-delta" style={{ color: `var(--ab-${deltaTone === "muted" ? "fg-3" : deltaTone})` }}>
            {delta}
          </span>
        )}
      </div>
      {spark && (
        <div style={{ marginTop: 2 }}>
          <Sparkline data={spark} tone={sparkTone} w={220} h={28} />
        </div>
      )}
      {footer && <div style={{ fontSize: 10.5, color: "var(--ab-fg-4)", fontFamily: "var(--ab-mono)" }}>{footer}</div>}
    </div>
  );
}

// ── Card shell ─────────────────────────────────────────────────────────────
function Card({ title, right, children, padding=0, style, hint }) {
  return (
    <div className="ab-card" style={style}>
      {title && (
        <div className="ab-card-h">
          <span>{title}</span>
          {hint && <span className="ab-mono" style={{ textTransform: "none", letterSpacing: 0, color: "var(--ab-fg-4)", fontSize: 10.5 }}>{hint}</span>}
          {right && <span className="ab-card-h-r">{right}</span>}
        </div>
      )}
      <div style={{ padding }}>{children}</div>
    </div>
  );
}

// ── Topbar (endpoint ↔ peer link, nav) ─────────────────────────────────────
function TopBar({ active="streams", alerts=2, onNavigate }) {
  const { PEER, PROGRAM, TALKBACK } = window.AB;
  const tone = PROGRAM.state === "running" && TALKBACK.state === "running" ? "ok"
              : PROGRAM.state === "stopped" ? "muted" : "warn";
  return (
    <header className="ab-topbar">
      <div className="ab-brand"><span className="ab-brand-mark"/>AUDIOBRIDGE</div>
      <div style={{ width: 1, height: 18, background: "var(--ab-border-soft)" }} />
      <div className="ab-link" title="link state">
        <Dot tone={tone} />
        <span style={{ color: "var(--ab-fg)" }}>{PEER.self.name}</span>
        <Icon.bidir style={{ color: "var(--ab-fg-3)" }} />
        <span style={{ color: "var(--ab-fg)" }}>{PEER.peer.name}</span>
        <span style={{ color: "var(--ab-fg-4)" }}>· SRT/WebRTC · paired 18d</span>
      </div>
      <nav>
        {["streams", "diagnostics", "events", "settings"].map(k => (
          <a key={k} data-active={k === active ? "" : null} onClick={() => onNavigate && onNavigate(k)}>{k}</a>
        ))}
      </nav>
      <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
        <Chip tone={alerts ? "warn" : "ok"}>
          <Icon.alert /> {alerts} active
        </Chip>
        <Chip tone="muted">v0.1.0</Chip>
        <button className="ab-btn" data-variant="ghost" style={{ padding: "0 8px" }} onClick={() => onNavigate && onNavigate("settings")}><Icon.cog /></button>
      </div>
    </header>
  );
}

// ── Live events log (rolling) ──────────────────────────────────────────────
function EventsLog({ rows, max, dense=false }) {
  const list = (rows || window.AB.EVENTS).slice(0, max);
  return (
    <div style={{ overflow: "auto" }}>
      {list.map((r, i) => {
        const [t, lvl, src, msg] = r;
        const tone = lvl === "err" ? "err" : lvl === "warn" ? "warn" : null;
        return (
          <div key={i} className="ab-log-row" data-level={lvl}
               style={dense ? { padding: "2px 10px", fontSize: 10.5, gridTemplateColumns: "52px 50px 56px 1fr" } : null}>
            <span className="ab-log-t">{t}</span>
            <span className="ab-log-c" style={{ color: tone ? `var(--ab-${tone})` : "var(--ab-fg-3)" }}>{lvl.toUpperCase()}</span>
            <span className="ab-log-c">{src}</span>
            <span style={{ overflow: "hidden", textOverflow: "ellipsis" }}>{msg}</span>
          </div>
        );
      })}
    </div>
  );
}

// ── State pill for program/talkback transports ─────────────────────────────
function StatePill({ state, label }) {
  const tone = state === "running" ? "ok" : state === "stopped" ? "muted" : state === "reconnecting" ? "warn" : "err";
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
      <Dot tone={tone === "muted" ? "off" : tone} />
      <span className="ab-mono" style={{ fontSize: 11, letterSpacing: 0.04, textTransform: "uppercase", color: tone === "muted" ? "var(--ab-fg-3)" : `var(--ab-${tone})` }}>
        {label || state}
      </span>
    </span>
  );
}

// ── Format helpers ─────────────────────────────────────────────────────────
function fmtUptime(s) {
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h ${m}m`;
  return `${h}h ${m}m`;
}
function fmtBitrate(kbps) {
  return kbps >= 1000 ? (kbps / 1000).toFixed(2) + " Mb/s" : kbps.toFixed(0) + " kb/s";
}
function fmtDb(v) { return v <= -100 ? "—" : v.toFixed(1); }

Object.assign(window, {
  Icon, Chip, Dot, Sparkline, Meter, KpiTile, Card, TopBar, EventsLog, StatePill,
  fmtUptime, fmtBitrate, fmtDb,
});
