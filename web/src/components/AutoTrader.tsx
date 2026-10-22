import { useEffect, useMemo, useRef, useState } from "react";
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import history from "../data/live-history.json";
import { fmtUsd } from "../lib/assets";
import { desk, deskCall, onDesk } from "../lib/trade";
import { VENUES } from "../lib/venues";

type AnyRec = Record<string, any>;
const H = history as AnyRec;
const SESSIONS: AnyRec[] = H.sessions ?? [];
const NAMES: Record<string, string> = {
  leadlag: "Lead-lag", imbalance: "Order-book imbalance", reversion: "Dip reversion",
};
const axis = { stroke: "#6F675B", fontSize: 12, tickLine: false, axisLine: { stroke: "#3A332A" } } as const;
const tip = {
  contentStyle: { background: "#1E1A15", border: "1px solid #3A332A", borderRadius: 8, color: "#EDE6D8", fontSize: 13 },
  labelStyle: { color: "#A39A8B" },
} as const;

const ago = (iso: string) => {
  const m = (Date.now() - Date.parse(iso)) / 60000;
  if (m < 90) return `${Math.max(1, Math.round(m))} minutes ago`;
  if (m < 60 * 36) return `${Math.round(m / 60)} hours ago`;
  return `${Math.round(m / 1440)} days ago`;
};
const pnl = (x: number | null | undefined, dp = 2) => (x == null ? "—" : `${x >= 0 ? "+" : "−"}${fmtUsd(Math.abs(x), dp).slice(x >= 0 ? 0 : 1)}`);

function Published() {
  const last = SESSIONS[SESSIONS.length - 1];
  const chart = useMemo(() => SESSIONS.map((s) => {
    const row: AnyRec = { t: new Date(s.recordedAt).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) };
    for (const v of VENUES) if (v.key !== "binance") row[v.key] = s.catchupMs?.[v.key]?.median ?? null;
    return row;
  }), []);
  const totals = useMemo(() => {
    const t: Record<string, { n: number; pnl: number }> = {};
    for (const s of SESSIONS) for (const b of s.bot ?? []) {
      t[b.name] ??= { n: 0, pnl: 0 };
      t[b.name].n += b.paper?.n ?? 0;
      t[b.name].pnl += b.paper?.pnl ?? 0;
    }
    return t;
  }, []);
  if (!last) {
    return (
      <div className="auto-pub">
        <h4>Published sessions</h4>
        <p className="muted">The first scheduled session is on its way. GitHub records the markets every four hours and
          this panel fills in automatically.</p>
      </div>
    );
  }
  const markets = Object.values(last.markets ?? {}).reduce((a: number, b: any) => a + b, 0);
  return (
    <div className="auto-pub">
      <h4>Published sessions</h4>
      <p className="auto-meta">
        Every four hours GitHub records {last.minutes} minutes of live quotes ({markets} markets across five exchanges),
        replays every strategy on them in paper mode and republishes this page. Last run {ago(last.recordedAt)};{" "}
        {SESSIONS.length} session{SESSIONS.length === 1 ? "" : "s"} so far.
      </p>
      <table className="auto-table">
        <thead>
          <tr><th scope="col">Strategy</th><th scope="col" className="r">Markets where it predicts</th>
            <th scope="col" className="r">Beat fees</th><th scope="col" className="r">Paper trades, all sessions</th>
            <th scope="col" className="r">Paper P&amp;L</th></tr>
        </thead>
        <tbody>
          {(last.bot ?? []).map((b: AnyRec) => (
            <tr key={b.name}>
              <th scope="row">{NAMES[b.name] ?? b.name}</th>
              <td className="num r">{b.ready} of {b.markets}</td>
              <td className="num r">{b.paper?.n ?? 0}</td>
              <td className="num r">{totals[b.name]?.n ?? 0}</td>
              <td className={`num r ${(totals[b.name]?.pnl ?? 0) >= 0 ? "pos" : "neg"}`}>{pnl(totals[b.name]?.pnl, 3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="fine">
        Last session: {last.botCounts?.observations?.toLocaleString() ?? 0} predictions checked against what happened next;{" "}
        {last.botCounts?.signals ?? 0} were confident enough to beat fees and spread, {last.botCounts?.below_cost?.toLocaleString() ?? 0} were real but too small.
        Recorded from {last.where}, so times include the trip from the US.
      </p>
      {chart.length > 1 && (
        <figure className="auto-fig">
          <figcaption>Median time each exchange takes to follow a Binance move, per session (ms)</figcaption>
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={chart} margin={{ top: 6, right: 8, bottom: 0, left: -10 }}>
              <CartesianGrid stroke="rgba(163,154,139,0.12)" vertical={false} />
              <XAxis dataKey="t" {...axis} minTickGap={40} />
              <YAxis {...axis} width={48} />
              <Tooltip {...tip} />
              {VENUES.filter((v) => v.key !== "binance").map((v) => (
                <Line key={v.key} dataKey={v.key} name={v.name} stroke={v.color} dot={false} strokeWidth={1.8} isAnimationActive={false} connectNulls />
              ))}
            </LineChart>
          </ResponsiveContainer>
        </figure>
      )}
    </div>
  );
}

function Stat({ s }: { s: AnyRec | undefined }) {
  if (!s || !s.n) return <span className="muted">no trades</span>;
  return <span>{s.n} · <b className={s.pnl >= 0 ? "pos" : "neg"}>{pnl(s.pnl, 3)}</b>{s.mean_bps != null ? ` · ${s.mean_bps.toFixed(1)} bp` : ""}</span>;
}

function MyBot({ onConnect }: { onConnect: () => void }) {
  const [connected, setConnected] = useState(!!desk.status);
  const [bot, setBot] = useState<AnyRec | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [armed, setArmed] = useState(false);
  const [cfg, setCfg] = useState({ order: 20, maxDailyLoss: 10, maxOpen: 3, top: 30 });
  const timer = useRef<number>(undefined);

  useEffect(() => onDesk(() => setConnected(!!desk.status)), []);
  useEffect(() => {
    if (!connected) return;
    let alive = true;
    const poll = async () => {
      try {
        const b = await deskCall("/bot");
        if (alive) { setBot(b); setErr(""); }
      } catch (e) {
        if (alive) setErr((e as Error).message);
      }
      if (alive) timer.current = window.setTimeout(poll, 1500);
    };
    poll();
    return () => { alive = false; window.clearTimeout(timer.current); };
  }, [connected]);

  const act = async (path: string, body?: unknown) => {
    setBusy(true);
    setErr("");
    try {
      setBot(await deskCall(path, body ?? {}));
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
      setArmed(false);
    }
  };

  if (!connected) {
    return (
      <div className="auto-mine">
        <h4>Your bot</h4>
        <p className="muted">
          The bot runs inside your trade desk, on your computer, so your keys never leave it. Connect the desk once and
          start, stop or kill it from here.
        </p>
        <pre className="cmd">{`python -m latlab.connect      # once per account
python -m latlab.desk         # then enter its code here`}</pre>
        <button className="btn gold" onClick={onConnect}>Connect my desk</button>
      </div>
    );
  }

  const running = bot?.running;
  const realLabel = bot?.deskMode === "live" ? "Start with real money" : "Start on Testnet";
  const realOk = bot?.realAllowed;
  const markets = bot?.markets ? Object.values(bot.markets).reduce((a: number, b: any) => a + b, 0) : 0;

  return (
    <div className="auto-mine">
      <div className="auto-head">
        <h4>Your bot</h4>
        <span className={`bot-state ${running ? (bot?.mode === "live" ? "live" : "on") : "off"}`}>
          {running ? `Running · ${bot?.mode === "paper" ? "paper" : bot?.mode}` : "Stopped"}
        </span>
      </div>

      <div className="auto-controls">
        <button className="btn ghost" disabled={busy} onClick={() => act("/bot/start", { real: false, ...cfg })}>Start paper</button>
        <button className={`btn ${armed ? "gold" : "ghost"}`} disabled={busy || !realOk}
          title={realOk ? "" : "Start the desk with --live --auto (or without --live for Testnet) to allow this"}
          onClick={() => (bot?.deskMode === "live" && !armed ? setArmed(true) : act("/bot/start", { real: true, ...cfg }))}>
          {armed ? "Confirm: real money" : realLabel}
        </button>
        <button className="btn ghost" disabled={busy || !running} onClick={() => act("/bot/stop")}>Stop</button>
        {bot?.killSwitch
          ? <button className="btn ghost" disabled={busy} onClick={() => act("/bot/resume")}>Turn kill switch off</button>
          : <button className="btn kill" disabled={busy} onClick={() => act("/bot/kill")}>Kill switch</button>}
      </div>

      <div className="auto-settings" role="group" aria-label="Risk limits">
        {([["order", "USDT per trade"], ["maxDailyLoss", "Daily loss cap, USDT"], ["maxOpen", "Open at once"], ["top", "Binance markets"]] as const).map(([k, label]) => (
          <label key={k}>
            <span>{label}</span>
            <input type="number" min={1} value={cfg[k]} disabled={running}
              onChange={(e) => setCfg((c) => ({ ...c, [k]: Math.max(1, Number(e.target.value) || 1) }))} />
          </label>
        ))}
      </div>

      {err && <p className="drawer-error" role="alert">{err}</p>}
      {bot?.risk?.halted && <p className="drawer-error">{bot.risk.halted}</p>}

      {bot?.strategies && (
        <>
          <p className="auto-meta">
            Watching {markets} markets · {bot.quotes?.toLocaleString()} quotes · {bot.counts?.observations?.toLocaleString() ?? 0} predictions
            checked · {bot.counts?.signals ?? 0} beat costs
          </p>
          <ul className="strat-list">
            {bot.strategies.map((s: AnyRec) => (
              <li key={s.name}>
                <div className="sl-top">
                  <b>{NAMES[s.name] ?? s.name}</b>
                  <span className={`gate ${s.gate?.open ? "open" : ""}`}>{s.gate?.open ? "Real money unlocked" : "Paper only"}</span>
                </div>
                <p className="sl-desc">{s.description}</p>
                <dl>
                  <div><dt>Predicts in</dt><dd>{s.ready} of {s.markets} markets</dd></div>
                  <div><dt>Paper</dt><dd><Stat s={s.paper} /></dd></div>
                  {(s.testnet?.n ?? 0) > 0 && <div><dt>Testnet</dt><dd><Stat s={s.testnet} /></dd></div>}
                  {(s.live?.n ?? 0) > 0 && <div><dt>Real money</dt><dd><Stat s={s.live} /></dd></div>}
                </dl>
                {!s.gate?.open && <p className="sl-why">To trade real money it {s.gate?.why}.</p>}
              </li>
            ))}
          </ul>
          {bot.open?.length > 0 && (
            <ol className="blotter">
              {bot.open.map((p: AnyRec, i: number) => (
                <li key={i} className="f"><span>{p.asset} on {p.venue}</span><span>{p.strategy} · {p.broker}</span><span>{p.status}</span></li>
              ))}
            </ol>
          )}
          {bot.events?.length > 0 && (
            <ol className="events">
              {bot.events.slice(0, 8).map((e: AnyRec, i: number) => (
                <li key={i} className={e.kind}><time>{new Date(e.t * 1000).toLocaleTimeString("en-GB")}</time> {e.msg}</li>
              ))}
            </ol>
          )}
        </>
      )}
    </div>
  );
}

export function AutoTrader({ onConnect }: { onConnect: () => void }) {
  return (
    <div className="block auto" id="auto">
      <header className="block-head">
        <h3>Auto-trader</h3>
        <p>
          Three strategies watch every market and learn, coin by coin, how much their signal really predicts. A trade
          happens only when the prediction, minus two standard errors, beats both fees and the spread. Real money unlocks
          per strategy only after 100+ paper trades prove a profit with statistical confidence; a daily loss cap and a
          kill switch sit on top. Nothing here promises profit, and most of the time the right answer is to do nothing.
        </p>
      </header>
      <div className="auto-grid">
        <Published />
        <MyBot onConnect={onConnect} />
      </div>
    </div>
  );
}
