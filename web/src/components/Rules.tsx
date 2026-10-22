import { useEffect, useMemo, useState } from "react";

type AnyRec = Record<string, any>;
const LOCAL = `${import.meta.env.BASE_URL}data/strategies.json`;
const RAW = "https://raw.githubusercontent.com/kaushik899/crypto-latency-lab/main/web/public/data/strategies.json";
const NAMES: Record<string, string> = {
  SPY: "S&P 500 (SPY)", QQQ: "Nasdaq-100 (QQQ)", EFA: "International stocks (EFA)", IEF: "Treasury bonds (IEF)",
  BIL: "Treasury bills (BIL)", "BTC-USD": "Bitcoin", XLB: "Materials", XLE: "Energy", XLF: "Financials", XLI: "Industrials",
  XLK: "Technology", XLP: "Staples", XLU: "Utilities", XLV: "Health care", XLY: "Consumer discretionary",
};
const pct = (x?: number | null, dp = 1) => (x == null ? "—" : `${x < 0 ? "−" : ""}${Math.abs(x).toFixed(dp)}%`);
const date = (d?: string) => (d ? new Date(d + "T12:00:00Z").toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" }) : "—");

function useRules(): AnyRec | null {
  const [doc, setDoc] = useState<AnyRec | null>(null);
  useEffect(() => {
    let alive = true;
    const get = (u: string) => fetch(u, { cache: "no-cache" }).then((r) => (r.ok ? r.json() : null)).catch(() => null);
    get(LOCAL).then((a) => {
      if (alive && a?.rules) setDoc(a);
      return get(`${RAW}?t=${Date.now()}`).then((b) => { if (alive && b?.rules && (!a || b.generated > a.generated)) setDoc(b); });
    });
    return () => { alive = false; };
  }, []);
  return doc;
}

/** Two growth curves on a log scale: the rule (gold) and simply holding (blue). */
function Curves({ a, b }: { a: [string, number][]; b: [string, number][] }) {
  const W = 360, H = 120, P = 4;
  const all = [...a, ...b].map((p) => Math.log(p[1]));
  const lo = Math.min(...all), hi = Math.max(...all);
  const path = (s: [string, number][]) => s.map((p, i) =>
    `${i ? "L" : "M"}${(P + (i / (s.length - 1)) * (W - 2 * P)).toFixed(1)},${(H - P - ((Math.log(p[1]) - lo) / (hi - lo || 1)) * (H - 2 * P)).toFixed(1)}`).join("");
  return (
    <svg className="curves" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden>
      <path className="c-hold" d={path(b)} pathLength={1} />
      <path className="c-rule" d={path(a)} pathLength={1} />
    </svg>
  );
}

function Rule({ r }: { r: AnyRec }) {
  const st = r.stats, hs = r.hold_stats, f = r.facts ?? {};
  const held = Object.keys(r.signal?.hold ?? {});
  const inCash = held.length === 0 || (held.length === 1 && held[0] === "BIL");
  const growth = (x: number) => `$${Math.round(1000 * x).toLocaleString("en-US")}`;
  const lastA = r.curve?.[r.curve.length - 1]?.[1], lastB = r.hold_curve?.[r.hold_curve.length - 1]?.[1];
  return (
    <li className="rule">
      <div className="rule-top">
        <div>
          <h5>{r.name}</h5>
          <p className="fine">{r.rule} · tested from {date(st.from)}</p>
        </div>
        <div className={`rule-now ${inCash ? "out" : "in"}`}>
          <span>Rule says now</span>
          <b>{inCash ? (held[0] === "BIL" ? "Out: in T-bills" : "Out: in cash") : held.map((h) => NAMES[h] ?? h).join(" · ")}</b>
          <em>since {date(r.signal?.since)} · next check {r.next_check}</em>
        </div>
      </div>
      <p className="rule-desc">{r.desc}</p>
      <Curves a={r.curve} b={r.hold_curve} />
      <p className="curves-key"><span className="k-rule" /> the rule: $1,000 became {growth(lastA)} <span className="k-hold" /> just holding {NAMES[r.benchmark] ?? r.benchmark}: {growth(lastB)}</p>
      <table className="auto-table rule-t">
        <thead><tr><th scope="col" /><th scope="col" className="r">Per year</th><th scope="col" className="r">Worst fall</th>
          <th scope="col" className="r">Worst year</th><th scope="col" className="r">Return ÷ swings</th></tr></thead>
        <tbody>
          <tr><th scope="row">Rule</th><td className="num r">{pct(st.cagr_pct)}</td><td className="num r neg">{pct(st.max_dd_pct, 0)}</td>
            <td className="num r">{st.worst_year ? `${st.worst_year[0]}: ${pct(st.worst_year[1], 0)}` : "—"}</td><td className="num r">{st.sharpe}</td></tr>
          <tr><th scope="row">Just hold</th><td className="num r">{pct(hs.cagr_pct)}</td><td className="num r neg">{pct(hs.max_dd_pct, 0)}</td>
            <td className="num r">{hs.worst_year ? `${hs.worst_year[0]}: ${pct(hs.worst_year[1], 0)}` : "—"}</td><td className="num r">{hs.sharpe}</td></tr>
        </tbody>
      </table>
      <ul className="facts">
        <li className={f.smaller_crashes_both_halves ? "ok" : "no"}>Smaller worst fall before and after {f.split?.slice(0, 4)}</li>
        <li className={f.higher_return_both_halves ? "ok" : "no"}>Higher return before and after {f.split?.slice(0, 4)}</li>
        <li className={f.better_sharpe_both_halves ? "ok" : "no"}>Better return for the swings before and after {f.split?.slice(0, 4)}</li>
      </ul>
      <p className="fine">About {r.switches_per_year} switches a year, {r.cost_bps} bp cost on each side included. Dividends included.</p>
    </li>
  );
}

export function Rules() {
  const doc = useRules();
  const rules: AnyRec[] = useMemo(() => doc?.rules ?? [], [doc]);
  if (!rules.length) return null;
  return (
    <section className="rules" aria-labelledby="rules-h">
      <div className="auto-head">
        <h4 id="rules-h">When to be in, when to be out</h4>
        <span className="muted">rules with decades of evidence · updated {date(doc?.generated?.slice(0, 10))}</span>
      </div>
      <p className="auto-meta">
        The short-term rules above do not beat the market. These are the long-horizon rules that have held up in
        published research for decades, run here with their textbook settings on the full free history (back to 1993
        for the S&amp;P 500), buying and selling at the next day’s close. What they reliably did was <b>cut the big
        crashes roughly in half</b>; what they did not do is reliably earn more than simply holding.
      </p>
      <ul className="rule-list">{rules.map((r) => <Rule key={r.name} r={r} />)}</ul>
      <p className="fine">Past results of fixed rules, not a forecast or personal advice. The signal is checked again on the date shown;
        nothing here places an order.</p>
    </section>
  );
}
