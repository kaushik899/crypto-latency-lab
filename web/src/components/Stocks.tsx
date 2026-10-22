import { useEffect, useMemo, useRef, useState } from "react";
import edgeBundled from "../data/stocks-edge.json";
import todayBundled from "../data/stocks-today.json";
import trackBundled from "../data/stocks-track.json";
import { desk, deskCall, onDesk } from "../lib/trade";
import { Planner } from "./Planner";
import { Scan } from "./Scan";

type AnyRec = Record<string, any>;
const RAW = "https://raw.githubusercontent.com/kaushik899/crypto-latency-lab/main/web/src/data/";

/** The bundled JSON, refreshed from the repo every few minutes so an open page keeps up with the schedule. */
function useLive<T>(name: string, bundled: T, everyMs = 5 * 60_000): T {
  const [doc, setDoc] = useState<T>(bundled);
  useEffect(() => {
    let alive = true;
    const pull = async () => {
      try {
        const r = await fetch(`${RAW}${name}?t=${Date.now()}`, { cache: "no-store" });
        if (r.ok) {
          const j = await r.json();
          if (alive && !j.pending) setDoc(j);
        }
      } catch { /* offline: keep what we have */ }
    };
    pull();
    const id = window.setInterval(pull, everyMs);
    return () => { alive = false; window.clearInterval(id); };
  }, [name, everyMs]);
  return doc;
}

const usd = (x: number, dp = 2) => `$${x.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp })}`;
const R = (x: number | null | undefined, dp = 2) => (x == null ? "—" : `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(dp)}R`);
const pct = (x: number | null | undefined) => (x == null ? "—" : `${(100 * x).toFixed(0)}%`);
const ago = (iso?: string) => {
  if (!iso) return "";
  const m = (Date.now() - Date.parse(iso)) / 60000;
  if (m < 90) return `${Math.max(1, Math.round(m))} min ago`;
  if (m < 60 * 36) return `${Math.round(m / 60)} h ago`;
  return `${Math.round(m / 1440)} days ago`;
};
const RULE_WORDS = (key?: string) => {
  if (!key) return "";
  const [orb, stop, tgt] = key.split("-");
  return `buy the first close above the ${orb.slice(3)}-minute opening range, stop at its ${stop === "mid" ? "middle" : "low"}, ` +
    (tgt === "close" ? "hold to 15:55" : `take profit at ${tgt}`);
};

/** Same rule as latlab/stocks/daily.py size(): whole shares when they fit, else fractional. */
function sizeFor(entry: number, stop: number, account: number, riskPct: number, budget: number) {
  const rps = entry - stop;
  if (!(rps > 0)) return { shares: 0, cost: 0, maxLoss: 0, fractional: false };
  const maxLoss = (account * riskPct) / 100;
  const cash = Math.min(budget || account, account);
  const whole = Math.min(Math.floor(maxLoss / rps), Math.floor(cash / entry));
  if (whole >= 1) return { shares: whole, cost: whole * entry, maxLoss: whole * rps, fractional: false };
  const f = Math.floor(Math.min(maxLoss / rps, cash / entry) * 1e4) / 1e4;
  return { shares: f, cost: f * entry, maxLoss: f * rps, fractional: f > 0 };
}

function Candles({ s }: { s: AnyRec }) {
  const bars: [string, number, number, number, number][] = s.bars ?? [];
  const W = 320, H = 120, pad = 4;
  if (!bars.length) return null;
  const lo = Math.min(...bars.map((b) => b[3]), s.stop), hi = Math.max(...bars.map((b) => b[2]), s.target);
  const y = (v: number) => pad + (H - 2 * pad) * (1 - (v - lo) / (hi - lo || 1));
  const bw = Math.max(2, Math.min(8, (W - 40) / 78 - 1));
  const x = (i: number) => 2 + i * (bw + 1);
  const line = (v: number, cls: string, label: string) => (
    <g className={cls}>
      <line x1={0} x2={W} y1={y(v)} y2={y(v)} />
      <text x={W - 2} y={y(v) < 14 ? y(v) + 10 : y(v) - 3} textAnchor="end">{label} {v.toFixed(2)}</text>
    </g>
  );
  return (
    <svg className="candles" viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label={`${s.symbol} today: ${bars.length} five-minute bars, entry ${s.entry}, stop ${s.stop}, target ${s.target}`}>
      <rect className="or-band" x={0} width={x(s.rule ? Number(s.rule.split("-")[0].slice(3)) / 5 : 1)}
        y={y(s.or_high)} height={Math.max(1, y(s.or_low) - y(s.or_high))} />
      {bars.map((b, i) => {
        const up = b[4] >= b[1];
        return (
          <g key={i} className={up ? "up" : "down"}>
            <line x1={x(i) + bw / 2} x2={x(i) + bw / 2} y1={y(b[2])} y2={y(b[3])} />
            <rect x={x(i)} width={bw} y={y(Math.max(b[1], b[4]))} height={Math.max(1, Math.abs(y(b[1]) - y(b[4])))} />
          </g>
        );
      })}
      {line(s.target, "lv target", "target")}
      {line(s.entry, "lv entry", "entry")}
      {line(s.stop, "lv stop", "stop")}
    </svg>
  );
}

function Odds({ tm }: { tm: AnyRec | null }) {
  if (!tm) return <p className="fine">Not enough history for the Time Machine.</p>;
  return (
    <div className="odds">
      <div className="odds-bar" aria-hidden>
        <span className="t" style={{ width: pct(tm.p_target) }} />
        <span className="n" style={{ width: pct(tm.p_neither) }} />
        <span className="s" style={{ width: pct(tm.p_stop) }} />
      </div>
      <p className="odds-key">
        <span className="pos">target first {pct(tm.p_target)}</span> · <span>neither {pct(tm.p_neither)}</span> ·{" "}
        <span className="neg">stop first {pct(tm.p_stop)}</span> · average {R(tm.avg_R)}
      </p>
    </div>
  );
}

function Ticket({ s, size, onDone }: { s: AnyRec; size: ReturnType<typeof sizeFor>; onDone: () => void }) {
  const [ticket, setTicket] = useState<AnyRec | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [left, setLeft] = useState(0);
  const [done, setDone] = useState<AnyRec | null>(null);
  useEffect(() => {
    if (!ticket?.token) return;
    setLeft(ticket.expiresIn);
    const id = window.setInterval(() => setLeft((l) => Math.max(0, l - 1)), 1000);
    return () => window.clearInterval(id);
  }, [ticket]);

  const preview = async () => {
    setBusy(true); setErr(""); setDone(null);
    try {
      setTicket(await deskCall("/stocks/preview", { symbol: s.symbol, qty: size.shares, entry: s.entry, stop: s.stop, target: s.target }));
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const place = async () => {
    setBusy(true); setErr("");
    try {
      setDone(await deskCall("/stocks/order", { token: ticket!.token, confirm: true }));
      setTicket(null);
      onDone();
    } catch (e) { setErr((e as Error).message); setTicket(null); } finally { setBusy(false); }
  };

  if (done) return <p className="ticket-ok" role="status">Sent in {done.ms} ms: {done.summary}</p>;
  return (
    <div className="ticket">
      {!ticket && <button className="btn ghost" disabled={busy || size.shares <= 0} onClick={preview}>Preview ticket</button>}
      {ticket && (
        <div className="ticket-card" role="dialog" aria-label={`Order ticket for ${s.symbol}`}>
          <p className="ticket-sum">{ticket.summary}</p>
          <p className="fine">{ticket.kind}</p>
          <ul className="checks">
            {ticket.checks.map((c: AnyRec) => (
              <li key={c.check} className={c.ok ? "ok" : "no"}><span>{c.ok ? "✓" : "✗"}</span> {c.check} <em>{c.detail}</em></li>
            ))}
          </ul>
          <div className="ticket-actions">
            {ticket.ok && left > 0 && (
              <button className={`btn ${ticket.live ? "kill" : "gold"}`} disabled={busy} onClick={place}>
                Place this order{ticket.live ? " — real money" : ""} ({left}s)
              </button>
            )}
            <button className="btn ghost" onClick={() => setTicket(null)}>{ticket.ok && left > 0 ? "Cancel" : "Close"}</button>
          </div>
          {!ticket.ok && <p className="fine">The desk refused this ticket: every check has to pass.</p>}
        </div>
      )}
      {err && <p className="drawer-error" role="alert">{err}</p>}
    </div>
  );
}

function Setup({ s, account, riskPct, budget, paired, refresh }:
  { s: AnyRec; account: number; riskPct: number; budget: number; paired: boolean; refresh: () => void }) {
  const size = sizeFor(s.entry, s.stop, account, riskPct, budget);
  const validated = s.status === "VALIDATED";
  return (
    <li className={`setup ${validated ? "is-valid" : "is-watch"}`}>
      <div className="setup-top">
        <b className="sym">{s.symbol}</b>
        <span className={`gate ${validated ? "open" : ""}`}>{validated ? "Validated rule" : "Watch only"}</span>
        <span className="muted">broke out {s.breakout_time} ET</span>
      </div>
      <Candles s={s} />
      <dl className="setup-plan">
        <div><dt>Entry</dt><dd className="num">{s.entry.toFixed(2)}</dd></div>
        <div><dt>Stop</dt><dd className="num neg">{s.stop.toFixed(2)}</dd></div>
        <div><dt>Target</dt><dd className="num pos">{s.target.toFixed(2)}</dd></div>
        <div><dt>Your size</dt><dd className="num">{size.shares > 0 ? `${size.shares} sh · ${usd(size.cost)}` : "too big"}</dd></div>
        <div><dt>Max loss</dt><dd className="num">{usd(size.maxLoss)}</dd></div>
      </dl>
      <Odds tm={s.time_machine} />
      {size.shares > 0 && (() => {
        const sh = size.shares, slip = 0.0004;                       // 2 bp each way, as in the research
        const win = sh * (s.target * (1 - slip) - s.entry * (1 + slip));
        const loss = sh * (s.stop * (1 - slip) - s.entry * (1 + slip));
        const avgR = s.time_machine?.avg_R;
        const exp = avgR == null ? null : avgR * sh * (s.entry - s.stop);
        const pnl = (x: number) => `${x >= 0 ? "+" : "−"}${usd(Math.abs(x))}`;
        return (
          <dl className="setup-pnl" aria-label="Profit and loss for your size">
            <div><dt>If the target hits</dt><dd className="num pos">{pnl(win)}</dd></div>
            <div><dt>If the stop hits</dt><dd className="num neg">{pnl(loss)}</dd></div>
            <div><dt>Average of 10,000 replays</dt><dd className={`num ${(exp ?? 0) >= 0 ? "pos" : "neg"}`}>{exp == null ? "—" : pnl(exp)}</dd></div>
            <div><dt>Money tied up</dt><dd className="num">until 15:55 New York at most</dd></div>
          </dl>
        );
      })()}
      {size.fractional && <p className="fine">Fractional shares: a market buy plus a separate stop; close the rest yourself.</p>}
      {!validated && <p className="fine">This rule has not passed the out-of-sample test. Study it; do not fund it.</p>}
      {validated && paired && <Ticket s={s} size={size} onDone={refresh} />}
    </li>
  );
}

function Account({ refreshKey }: { refreshKey: number }) {
  const [st, setSt] = useState<AnyRec | null>(null);
  const [err, setErr] = useState("");
  const [armed, setArmed] = useState(false);
  const load = async () => {
    try { setSt(await deskCall("/stocks/status")); setErr(""); } catch (e) { setErr((e as Error).message); }
  };
  useEffect(() => { load(); const id = window.setInterval(load, 10_000); return () => window.clearInterval(id); }, [refreshKey]);
  if (err) return <p className="drawer-error">{err}</p>;
  if (!st) return <p className="muted">Reading your account…</p>;
  if (!st.connected) {
    return (
      <div className="stk-acct">
        <h4>Your Alpaca account</h4>
        <p className="muted">The desk is paired but has no Alpaca key. A paper account is free from any country.</p>
        <pre className="cmd">{`python -m latlab.connect      # choose Alpaca, then Practice
python -m latlab.desk         # restart, pair again`}</pre>
      </div>
    );
  }
  const a = st.account ?? {};
  const day = Number(a.equity) - Number(a.last_equity);
  return (
    <div className="stk-acct">
      <div className="auto-head">
        <h4>Your Alpaca account</h4>
        <span className={`bot-state ${st.live ? "live" : "on"}`}>{st.live ? "Real money" : "Paper"}</span>
      </div>
      <dl className="setup-plan">
        <div><dt>Equity</dt><dd className="num">{usd(Number(a.equity))}</dd></div>
        <div><dt>Today</dt><dd className={`num ${day >= 0 ? "pos" : "neg"}`}>{day >= 0 ? "+" : "−"}{usd(Math.abs(day))}</dd></div>
        <div><dt>Buying power</dt><dd className="num">{usd(Number(a.buying_power))}</dd></div>
        <div><dt>Day trades (5 d)</dt><dd className="num">{a.daytrade_count ?? 0}</dd></div>
      </dl>
      {st.positions?.length > 0 && (
        <ol className="blotter">
          {st.positions.map((p: AnyRec) => (
            <li key={p.symbol} className="f"><span>{p.qty} {p.symbol} @ {Number(p.avg_entry_price).toFixed(2)}</span>
              <span className={Number(p.unrealized_pl) >= 0 ? "pos" : "neg"}>{usd(Number(p.unrealized_pl))}</span></li>
          ))}
        </ol>
      )}
      <p className="fine">{st.orders?.length ?? 0} open orders · tickets capped at {usd(st.maxOrder, 0)} and {st.riskPct}% max loss by the desk.</p>
      {st.positions?.length > 0 && (
        <button className={`btn ${armed ? "kill" : "ghost"}`} onClick={async () => {
          if (!armed) { setArmed(true); return; }
          setArmed(false);
          try { await deskCall("/stocks/close", {}); load(); } catch (e) { setErr((e as Error).message); }
        }}>{armed ? "Confirm: close every position" : "Close all positions"}</button>
      )}
    </div>
  );
}

export function Stocks({ onConnect }: { onConnect: () => void }) {
  const edge = useLive<AnyRec>("stocks-edge.json", edgeBundled as AnyRec);
  const today = useLive<AnyRec>("stocks-today.json", todayBundled as AnyRec, 2 * 60_000);
  const track = useLive<AnyRec>("stocks-track.json", trackBundled as AnyRec);
  const [paired, setPaired] = useState(!!desk.status);
  const [acct, setAcct] = useState({ account: 500, riskPct: 1, budget: 200 });
  const [showAll, setShowAll] = useState(false);
  const [allRules, setAllRules] = useState(false);
  const [tick, setTick] = useState(0);
  const first = useRef(true);
  useEffect(() => onDesk(() => setPaired(!!desk.status)), []);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      try { const v = JSON.parse(localStorage.getItem("stk-acct") ?? "null"); if (v) setAcct(v); } catch { /* private mode */ }
      return;
    }
    try { localStorage.setItem("stk-acct", JSON.stringify(acct)); } catch { /* private mode */ }
  }, [acct]);

  const setups: AnyRec[] = today.setups ?? [];
  const valid = setups.filter((s) => s.status === "VALIDATED");
  const watch = setups.filter((s) => s.status !== "VALIDATED");
  const rules: AnyRec[] = edge.rules ?? [];
  const best = rules[0];
  const rows: AnyRec[] = track.rows ?? [];
  const recent = useMemo(() => [...rows].reverse().slice(0, 12), [rows]);
  const sumV = track.summary?.VALIDATED, sumW = track.summary?.WATCH;

  return (
    <div className="block stk" id="stocks-lab">
      <header className="block-head">
        <h3>Stock lab</h3>
        <p>
          Every trading morning GitHub ranks the {edge.universe ? edge.universe.toLocaleString() : "~6,000"} US stocks by
          dollar volume, pulls 60 days of five-minute bars for the most liquid {edge.researched ?? 150}, and tests 18
          opening-range-breakout rules with real costs. A rule only counts if it wins on the first 40 days{" "}
          <b>and</b> on the 20 it never saw. During the session the lab posts each fresh breakout with a plan,
          10,000-replay odds and a size for a small account. You approve every order yourself.
        </p>
      </header>

      <Planner />

      <div className="stk-grid">
        <section className="stk-today" aria-labelledby="stk-today-h">
          <div className="auto-head">
            <h4 id="stk-today-h">Today’s setups</h4>
            <span className="muted">
              {today.pending ? "first run pending" : `${today.date ?? ""} · ${today.market ?? ""} · updated ${ago(today.generated)}`}
            </span>
          </div>
          <div className="auto-settings stk-acct-in" role="group" aria-label="Your account for sizing">
            {([["account", "Account $"], ["riskPct", "Max loss %"], ["budget", "Max per trade $"]] as const).map(([k, label]) => (
              <label key={k}>
                <span>{label}</span>
                <input type="number" min={k === "riskPct" ? 0.1 : 10} step={k === "riskPct" ? 0.1 : 10} value={acct[k]}
                  onChange={(e) => setAcct((a) => ({ ...a, [k]: Math.max(0.1, Number(e.target.value) || 0.1) }))} />
              </label>
            ))}
          </div>
          {setups.length === 0 && (
            <p className="muted">
              {today.market === "open"
                ? "No fresh breakout on the researched stocks right now. The lab checks every 30 minutes; no setup is a valid answer."
                : "The US market is closed. Setups appear between 9:45 and 15:30 New York time (15:45–21:30 in Paris)."}
            </p>
          )}
          {valid.length > 0 && (
            <ul className="setups">
              {valid.map((s) => <Setup key={s.symbol} s={s} {...acct} paired={paired} refresh={() => setTick((t) => t + 1)} />)}
            </ul>
          )}
          {watch.length > 0 && (
            <>
              <p className="fine">
                {valid.length === 0 ? "No rule has passed validation yet, so nothing is fundable today. " : ""}
                {watch.length} breakout{watch.length === 1 ? "" : "s"} under the best untested rule, for study:
              </p>
              <ul className="setups">
                {(showAll ? watch : watch.slice(0, 4)).map((s) => (
                  <Setup key={s.symbol} s={s} {...acct} paired={paired} refresh={() => setTick((t) => t + 1)} />
                ))}
              </ul>
              {watch.length > 4 && <button className="link" onClick={() => setShowAll((v) => !v)}>
                {showAll ? "Show fewer" : `Show all ${watch.length}`}</button>}
            </>
          )}
        </section>

        <aside className="stk-side">
          {paired ? <Account refreshKey={tick} /> : (
            <div className="stk-acct">
              <h4>Trade it</h4>
              <p className="muted">
                Orders go from your own trade desk to Alpaca, with a stop and a target attached. The desk re-checks every
                ticket against your live account; nothing is sent until you press <i>Place this order</i>.
              </p>
              <pre className="cmd">{`python -m latlab.connect      # Alpaca → Practice (free)
python -m latlab.desk         # then pair it here`}</pre>
              <button className="btn gold" onClick={onConnect}>Pair my desk</button>
            </div>
          )}

          <div className="stk-track">
            <h4>Track record</h4>
            <p className="auto-meta">Every published setup, replayed on the real bars after the close, net of costs.</p>
            <dl className="setup-plan">
              <div><dt>Validated</dt><dd className="num">{sumV?.n ?? 0} trades · {R(sumV?.mean_R)}</dd></div>
              <div><dt>Watch</dt><dd className="num">{sumW?.n ?? 0} trades · {R(sumW?.mean_R)}</dd></div>
              <div><dt>Watch win rate</dt><dd className="num">{pct(sumW?.win)}</dd></div>
            </dl>
            {recent.length > 0 && (
              <ol className="events">
                {recent.map((r, i) => (
                  <li key={i}><time>{r.date.slice(5)}</time> {r.symbol} · {r.outcome} ·{" "}
                    <b className={r.R >= 0 ? "pos" : "neg"}>{R(r.R)}</b></li>
                ))}
              </ol>
            )}
          </div>
        </aside>
      </div>

      <Scan />

      <section className="stk-edge" aria-labelledby="stk-edge-h">
        <div className="auto-head">
          <h4 id="stk-edge-h">Which rules survive</h4>
          <span className="muted">{edge.generated ? `tested ${ago(edge.generated)} · held out from ${edge.split}` : "first test pending"}</span>
        </div>
        {rules.length > 0 ? (
          <>
            <p className="auto-meta">
              {edge.chosen?.length
                ? `${edge.chosen.length} rule${edge.chosen.length > 1 ? "s" : ""} passed: ${edge.chosen.join(", ")}, on ${Object.keys(edge.eligible ?? {}).length} stocks.`
                : `None of the 18 rules cleared t ≥ ${edge.select_t} in-sample and stayed positive out-of-sample, so the lab funds nothing. That is the honest result most retail day-trading rules get.`}{" "}
              Best in-sample: <b>{best?.rule}</b> — {RULE_WORDS(best?.rule)}.
            </p>
            <div className="table-wrap">
              <table className="auto-table stk-rules">
                <thead>
                  <tr><th scope="col">Rule</th><th scope="col" className="r">Trades</th><th scope="col" className="r">Avg, first 40 days</th>
                    <th scope="col" className="r">t</th><th scope="col" className="r">Avg, last 20 days</th><th scope="col" className="r">t</th>
                    <th scope="col" className="r">Win</th><th scope="col">Verdict</th></tr>
                </thead>
                <tbody>
                  {rules.slice(0, allRules ? 18 : 8).map((r) => (
                    <tr key={r.rule}>
                      <th scope="row">{r.rule}</th>
                      <td className="num r">{r.in_sample.n + r.out_of_sample.n}</td>
                      <td className={`num r ${(r.in_sample.mean_R ?? 0) >= 0 ? "pos" : "neg"}`}>{R(r.in_sample.mean_R, 3)}</td>
                      <td className="num r">{r.in_sample.t ?? "—"}</td>
                      <td className={`num r ${(r.out_of_sample.mean_R ?? 0) >= 0 ? "pos" : "neg"}`}>{R(r.out_of_sample.mean_R, 3)}</td>
                      <td className="num r">{r.out_of_sample.t ?? "—"}</td>
                      <td className="num r">{pct(r.out_of_sample.win)}</td>
                      <td>{r.validated ? <span className="gate open">Fundable</span> : r.selected ? "Failed out-of-sample" : "Not significant"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {rules.length > 8 && <button className="link" onClick={() => setAllRules((v) => !v)}>
              {allRules ? "Show the top 8" : `Show all ${rules.length} rules`}</button>}
            <p className="fine">
              R is profit in units of the planned risk, after {edge.costs?.slippage_bps ?? 2} bp slippage each way and SEC and
              FINRA fees. t is clustered by day. Entries are at the next bar’s open, stops are checked before targets, and
              everything is flat by 15:55. Built on Jake Schincariol’s MIT-licensed Day Trader toolkit.
            </p>
          </>
        ) : <p className="muted">The first research run is on its way.</p>}
      </section>
    </div>
  );
}
