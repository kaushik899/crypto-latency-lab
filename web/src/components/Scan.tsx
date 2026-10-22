import { useMemo, useState } from "react";
import { useScanData } from "../lib/scanData";

type AnyRec = Record<string, any>;
const SETUPS = ["breakout_20d", "bull_flag", "reclaim_ma20", "oversold", "oversold_bounce"] as const;
const SHORT: Record<string, string> = {
  breakout_20d: "20-day high", bull_flag: "Bull flag", reclaim_ma20: "Above 20-day avg",
  oversold: "Oversold", oversold_bounce: "Oversold bounce",
};
const RULE: Record<string, string> = {
  breakout_20d: "the close is above every close of the previous 20 sessions",
  bull_flag: "up more than 10% over 10 sessions, then 5 closes inside a 5% range",
  reclaim_ma20: "yesterday's close was below its 20-day average, today's is above it",
  oversold: "14-day RSI (Wilder) under 30",
  oversold_bounce: "RSI was under 30 yesterday and today closed higher",
};
type SortKey = "dollar_volume" | "rel_volume" | "chg_1d" | "rsi" | "from_52w_high" | "atr_pct";
const SORTS: [SortKey, string][] = [
  ["dollar_volume", "Most traded"], ["rel_volume", "Volume vs normal"], ["chg_1d", "Day change"],
  ["from_52w_high", "Nearest 52-week high"], ["rsi", "Lowest RSI"], ["atr_pct", "Most volatile"],
];

const f = (x: number | null | undefined, dp = 1, unit = "") => (x == null ? "—" : `${x.toFixed(dp)}${unit}`);
const sgn = (x: number | null | undefined, dp = 1) => (x == null ? "—" : `${x > 0 ? "+" : x < 0 ? "−" : ""}${Math.abs(x).toFixed(dp)}%`);
const bp = (x: number | null | undefined, plus = false) => (x == null ? "—" : `${x > 0 && plus ? "+" : x < 0 ? "−" : ""}${Math.abs(x / 100).toFixed(2)}%`);
const money = (x?: number) => (x == null ? "—" : x >= 1e9 ? `$${(x / 1e9).toFixed(1)}B` : x >= 1e6 ? `$${(x / 1e6).toFixed(0)}M` : `$${(x / 1e3).toFixed(0)}K`);

export const useScan = useScanData;

function Spark({ v, up }: { v?: number[]; up: boolean }) {
  if (!v || v.length < 2) return <span className="muted">—</span>;
  const lo = Math.min(...v), hi = Math.max(...v), W = 84, H = 26;
  const pts = v.map((x, i) => `${((i / (v.length - 1)) * W).toFixed(1)},${(H - 2 - ((x - lo) / (hi - lo || 1)) * (H - 4)).toFixed(1)}`).join(" ");
  return (
    <svg className={`spark ${up ? "up" : "down"}`} viewBox={`0 0 ${W} ${H}`} width={W} height={H} aria-hidden>
      <polyline points={pts} fill="none" />
    </svg>
  );
}

function Rates({ br, setup }: { br: AnyRec; setup: string }) {
  const row = br?.setups?.[setup];
  if (!row) return null;
  const held = Object.entries(row.horizons as AnyRec).filter(([, c]: any) => c.held).map(([h]) => h);
  return (
    <div className="rates">
      <p className="rates-head">
        <b>{row.label}</b>: {RULE[setup]}. Every time this happened on {br.stocks?.toLocaleString()} liquid US stocks from{" "}
        {br.first_date} to {br.last_date}, buying at the <i>next</i> day’s close:
      </p>
      <div className="table-wrap">
        <table className="auto-table rates-t">
          <thead><tr><th scope="col">Held for</th><th scope="col" className="r">Times</th><th scope="col" className="r">Avg after costs</th>
            <th scope="col" className="r">vs average stock</th><th scope="col" className="r">t</th><th scope="col" className="r">Up</th>
            <th scope="col">Later third</th></tr></thead>
          <tbody>
            {Object.entries(row.horizons as AnyRec).map(([h, c]: [string, any]) => (
              <tr key={h}>
                <th scope="row">{h} day{h === "1" ? "" : "s"}</th>
                <td className="num r">{c.all?.n?.toLocaleString() ?? 0}</td>
                <td className={`num r ${(c.all?.mean_bps ?? 0) >= 0 ? "pos" : "neg"}`}>{bp(c.all?.mean_bps)}</td>
                <td className={`num r ${(c.all?.excess_bps ?? 0) >= 0 ? "pos" : "neg"}`}>{bp(c.all?.excess_bps, true)}</td>
                <td className="num r">{c.all?.t == null ? "—" : String(c.all.t).replace("-", "−")}</td>
                <td className="num r">{c.all?.win == null ? "—" : `${Math.round(c.all.win * 100)}%`}</td>
                <td>{c.held ? <span className="gate open">Edge held</span>
                  : `${bp(c.out_of_sample?.excess_bps, true)} vs avg`}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="fine">
        {held.length
          ? `Beat the average stock with t ≥ ${br.select_t} on the first two thirds and stayed ahead on the last third (from ${br.split}) when held ${held.join(" and ")} day(s). History is not a promise.`
          : `No holding period beat the average stock convincingly (t ≥ ${br.select_t} on the first two thirds and still ahead after ${br.split}). Treat these matches as a watchlist, not an edge.`}{" "}
        Costs: {br.cost_bps} bp round trip. Averages are per day first (a crash day with hundreds of signals counts once); t allows for overlapping holds.
      </p>
    </div>
  );
}

export function Scan() {
  const doc = useScan();
  const hits: AnyRec[] = doc.hits ?? [];
  const [setup, setSetup] = useState<string>("all");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<SortKey>("dollar_volume");
  const [hot, setHot] = useState(false);
  const [trend, setTrend] = useState(false);
  const [n, setN] = useState(25);
  const enriched = hits.some((h) => h.rel_volume !== undefined);

  const rows = useMemo(() => {
    const qq = q.trim().toUpperCase();
    let r = hits.filter((h) => (setup === "all" || h.setups.includes(setup))
      && (!qq || h.symbol.includes(qq) || (h.name ?? "").toUpperCase().includes(qq))
      && (!hot || (h.rel_volume ?? 0) >= 1.5)
      && (!trend || (h.vs_ma200 ?? -1) > 0));
    const dir = sort === "rsi" || sort === "from_52w_high" ? (sort === "rsi" ? 1 : -1) : -1;
    r = [...r].sort((a, b) => {
      const x = a[sort] ?? (dir > 0 ? Infinity : -Infinity), y = b[sort] ?? (dir > 0 ? Infinity : -Infinity);
      return dir > 0 ? x - y : y - x;
    });
    return r;
  }, [hits, setup, q, sort, hot, trend]);

  if (doc.pending || !hits.length) {
    return (
      <section className="stk-scan" aria-labelledby="scan-h">
        <h4 id="scan-h">Today’s scan</h4>
        <p className="muted">The first full scan is on its way.</p>
      </section>
    );
  }
  const c = doc.counts ?? {};
  const scanned = c.scanned ?? doc.scanned;
  return (
    <section className="stk-scan" aria-labelledby="scan-h">
      <div className="auto-head">
        <h4 id="scan-h">Every US stock, scanned</h4>
        <span className="muted">closes of {doc.as_of}</span>
      </div>
      <p className="auto-meta">
        {(c.universe ?? doc.universe)?.toLocaleString()} US stocks and ADRs read; {scanned?.toLocaleString()} trade at least
        $5 and {(doc.min_volume ?? 1e6).toLocaleString()} shares a day; <b>{hits.length}</b> match one of the Day Trader
        scanner’s five chart rules{enriched ? `, each checked against its own daily price-and-volume chart` : ""}.
        A match fits a rule. It is not a buy.
      </p>

      <div className="chips" role="tablist" aria-label="Setup">
        <button role="tab" aria-selected={setup === "all"} className={`chip ${setup === "all" ? "on" : ""}`} onClick={() => { setSetup("all"); setN(25); }}>
          All <span>{hits.length}</span></button>
        {SETUPS.map((s) => (
          <button key={s} role="tab" aria-selected={setup === s} className={`chip ${setup === s ? "on" : ""}`} onClick={() => { setSetup(s); setN(25); }}>
            {SHORT[s]} <span>{hits.filter((h) => h.setups.includes(s)).length}</span>
          </button>
        ))}
      </div>

      {setup !== "all" && doc.base_rates && <Rates br={doc.base_rates} setup={setup} />}
      {setup === "all" && doc.base_rates?.setups && (
        <p className="fine">Pick a setup to see how it actually performed over the last two years, against the average stock.</p>
      )}

      <div className="scan-tools">
        <input type="search" placeholder="Find a ticker or name" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Find a ticker or company" />
        <select value={sort} onChange={(e) => setSort(e.target.value as SortKey)} aria-label="Sort by">
          {SORTS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
        {enriched && <label className="tog"><input type="checkbox" checked={hot} onChange={(e) => setHot(e.target.checked)} /> Volume ≥ 1.5× normal</label>}
        {enriched && <label className="tog"><input type="checkbox" checked={trend} onChange={(e) => setTrend(e.target.checked)} /> Above 200-day average</label>}
        <span className="muted">{rows.length} shown</span>
      </div>

      <div className="table-wrap">
        <table className="auto-table scan-t">
          <thead>
            <tr><th scope="col">Stock</th><th scope="col" className="hide-s">60 days</th><th scope="col" className="r">Close</th>
              <th scope="col" className="r">Day</th><th scope="col" className="r">RSI</th>
              <th scope="col" className="r hide-s">Volume vs normal</th><th scope="col" className="r hide-s">Daily range</th>
              <th scope="col" className="r hide-s">vs 200-day</th><th scope="col" className="r hide-s">From 52-wk high</th>
              <th scope="col">Setups</th></tr>
          </thead>
          <tbody>
            {rows.slice(0, n).map((h) => (
              <tr key={h.symbol}>
                <th scope="row"><b>{h.symbol}</b><small>{h.name}</small></th>
                <td className="hide-s"><Spark v={h.spark} up={(h.chg_5d ?? 0) >= 0} /></td>
                <td className="num r">{h.price?.toFixed(2)}</td>
                <td className={`num r ${(h.chg_1d ?? 0) >= 0 ? "pos" : "neg"}`}>{sgn(h.chg_1d)}</td>
                <td className="num r">{f(h.rsi, 0)}</td>
                <td className={`num r hide-s ${(h.rel_volume ?? 0) >= 1.5 ? "hot" : ""}`}>{h.rel_volume == null ? "—" : `${h.rel_volume.toFixed(1)}×`}</td>
                <td className="num r hide-s">{f(h.atr_pct, 1, "%")}</td>
                <td className={`num r hide-s ${(h.vs_ma200 ?? 0) >= 0 ? "pos" : "neg"}`}>{sgn(h.vs_ma200)}</td>
                <td className="num r hide-s">{sgn(h.from_52w_high)}</td>
                <td className="tags">
                  {h.setups.map((s: string) => <span key={s} className="tag">{SHORT[s]}</span>)}
                  {h.setups.includes("breakout_20d") && h.breakout_intraday_high === false && <span className="tag weak" title="Closed at a 20-day closing high, but not above the 20-day intraday high">close only</span>}
                  {h.stale && <span className="tag weak">stale chart</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length > n && <button className="link" onClick={() => setN((x) => x + 50)}>Show {Math.min(50, rows.length - n)} more</button>}
      <p className="fine">
        Free Yahoo Finance data, unofficial and end of day; {money(hits[0]?.dollar_volume)} traded in the busiest match.
        “Daily range” is the 14-day average true range as a share of price. Check the news before acting on any chart: a
        drop to RSI 20 is often a headline, not a bargain. Not investment advice.
      </p>
    </section>
  );
}
