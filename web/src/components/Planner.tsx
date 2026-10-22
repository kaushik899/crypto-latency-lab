import { useEffect, useMemo, useRef, useState } from "react";
import { useScan } from "./Scan";

type AnyRec = Record<string, any>;
type Choice = { key: string; label: string; kind: "setup" | "any" | "index"; sym?: string; market: "stocks" | "crypto" };

const CHOICES: Choice[] = [
  { key: "SPY", label: "Hold an S&P 500 fund (SPY)", kind: "index", sym: "SPY", market: "stocks" },
  { key: "QQQ", label: "Hold a Nasdaq-100 fund (QQQ)", kind: "index", sym: "QQQ", market: "stocks" },
  { key: "any", label: "A random liquid US stock", kind: "any", market: "stocks" },
  { key: "breakout_20d", label: "Stock closed at a 20-day high", kind: "setup", market: "stocks" },
  { key: "bull_flag", label: "Stock in a bull flag", kind: "setup", market: "stocks" },
  { key: "reclaim_ma20", label: "Stock back above its 20-day average", kind: "setup", market: "stocks" },
  { key: "oversold", label: "Oversold stock (RSI under 30)", kind: "setup", market: "stocks" },
  { key: "oversold_bounce", label: "Stock bouncing after oversold", kind: "setup", market: "stocks" },
  { key: "BTC-USD", label: "Hold Bitcoin (BTC)", kind: "index", sym: "BTC-USD", market: "crypto" },
  { key: "ETH-USD", label: "Hold Ether (ETH)", kind: "index", sym: "ETH-USD", market: "crypto" },
  { key: "SOL-USD", label: "Hold Solana (SOL)", kind: "index", sym: "SOL-USD", market: "crypto" },
];
const HOLD_LABEL: Record<string, string> = {
  "1": "1 day", "5": "1 week", "10": "2 weeks", "21": "1 month", "63": "3 months", "126": "6 months", "252": "1 year",
};
const CRYPTO_HOLD: Record<string, string> = {
  "1": "1 day", "7": "1 week", "14": "2 weeks", "30": "1 month", "91": "3 months", "182": "6 months", "365": "1 year",
};
const holdLabel = (c: Choice | undefined, h: string) => (c?.market === "crypto" ? CRYPTO_HOLD[h] : HOLD_LABEL[h]) ?? `${h} days`;

const QI = { p5: 0, p10: 1, p25: 2, p50: 3, p75: 4, p90: 5, p95: 6 } as const;

const usd = (x: number) => `${x < 0 ? "−" : ""}$${Math.abs(x).toLocaleString("en-US", { maximumFractionDigits: Math.abs(x) < 100 ? 2 : 0, minimumFractionDigits: Math.abs(x) < 100 ? 2 : 0 })}`;
const signed = (x: number) => (x > 0 ? "+" : "") + usd(x);

/** Counts up to the value when it changes (respects reduced motion). */
function Count({ value, fmt }: { value: number; fmt: (x: number) => string }) {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) { setShown(value); return; }
    const a = from.current, t0 = performance.now(), dur = 650;
    let id = 0;
    const step = (t: number) => {
      const k = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - k, 3);
      setShown(a + (value - a) * e);
      if (k < 1) id = requestAnimationFrame(step); else from.current = value;
    };
    id = requestAnimationFrame(step);
    return () => { cancelAnimationFrame(id); from.current = value; };
  }, [value]);
  return <>{fmt(shown)}</>;
}

/** The outcome distribution, as a band on a dollar axis. */
function Band({ q, amount, ref2 }: { q: number[]; amount: number; ref2?: number[] | null }) {
  const vals = q.map((b) => (b / 1e4) * amount);
  const all = [...vals, ...(ref2 ?? []).map((b) => (b / 1e4) * amount), 0];
  const lo = Math.min(...all), hi = Math.max(...all), span = hi - lo || 1;
  const x = (v: number) => `${(((v - lo) / span) * 100).toFixed(2)}%`;
  const w = (a: number, b: number) => `${(((b - a) / span) * 100).toFixed(2)}%`;
  const r2 = ref2?.map((b) => (b / 1e4) * amount);
  return (
    <div className="band" aria-hidden>
      <div className="band-track">
        <span className="band-outer" style={{ left: x(vals[QI.p5]), width: w(vals[QI.p5], vals[QI.p95]) }} />
        <span className="band-mid" style={{ left: x(vals[QI.p25]), width: w(vals[QI.p25], vals[QI.p75]) }} />
        <span className="band-med" style={{ left: x(vals[QI.p50]) }} />
        <span className="band-zero" style={{ left: x(0) }}><i>$0</i></span>
      </div>
      {r2 && (
        <div className="band-track ref">
          <span className="band-outer" style={{ left: x(r2[QI.p5]), width: w(r2[QI.p5], r2[QI.p95]) }} />
          <span className="band-mid" style={{ left: x(r2[QI.p25]), width: w(r2[QI.p25], r2[QI.p75]) }} />
          <span className="band-med" style={{ left: x(r2[QI.p50]) }} />
        </div>
      )}
      <div className="band-axis"><span>{usd(lo)}</span><span>{usd(hi)}</span></div>
    </div>
  );
}

function cell(doc: AnyRec, c: Choice, hold: string): AnyRec | null {
  if (c.kind === "index") return doc.index?.[c.sym!]?.periods?.[hold] ?? null;
  if (c.kind === "any") return doc.base_rates?.any_stock?.[hold] ?? null;
  return doc.base_rates?.setups?.[c.key]?.horizons?.[hold]?.all ?? null;
}

function holdsFor(doc: AnyRec, c: Choice): string[] {
  if (c.kind === "index") return Object.keys(doc.index?.[c.sym!]?.periods ?? {});
  return ["1", "5", "10"];
}

export function Planner() {
  const doc = useScan();
  const ready = !!doc.base_rates?.setups && !!doc.base_rates?.setups?.breakout_20d?.horizons?.["1"]?.all?.q_bps;
  const [amount, setAmount] = useState(1000);
  const [key, setKey] = useState("SPY");
  const [hold, setHold] = useState("252");
  const choices = CHOICES.filter((c) => (c.kind === "index" ? !!doc.index?.[c.sym!] : true));
  const choice = choices.find((c) => c.key === key) ?? choices[0];
  const holds = choice ? holdsFor(doc, choice) : [];
  const h = holds.includes(hold) ? hold : holds[holds.length - 1] ?? "5";
  const c = choice ? cell(doc, choice, h) : null;
  const refSym = choice?.market === "crypto" ? "BTC-USD" : "SPY";
  const spy = doc.index?.[refSym]?.periods?.[h] ?? null;

  const ranked = useMemo(() => {
    if (!ready) return [];
    const rows: AnyRec[] = [];
    for (const ch of CHOICES) {
      for (const hh of holdsFor(doc, ch)) {
        if (ch.kind !== "index" && !["1", "5", "10"].includes(hh)) continue;
        if (ch.kind === "index" && ch.market === "stocks" && !["5", "21", "252"].includes(hh)) continue;
        if (ch.kind === "index" && ch.market === "crypto" && !["7", "30", "365"].includes(hh)) continue;
        const cc = cell(doc, ch, hh);
        if (!cc?.q_bps) continue;
        const setupCell = ch.kind === "setup" ? doc.base_rates.setups[ch.key].horizons[hh] : null;
        rows.push({
          // per calendar day the money is tied up: 5 trading days ~ 7 calendar days
          ch, hold: hh, perDay: cc.mean_bps / (ch.market === "crypto" ? Number(hh) : Number(hh) * 7 / 5), median: cc.q_bps[QI.p50], win: cc.win, bad: cc.q_bps[QI.p10],
          verdict: ch.kind === "index" ? "Market" : ch.kind === "any" ? "Benchmark"
            : setupCell?.held ? "Beat the average stock" : (setupCell?.out_of_sample?.excess_bps ?? 0) > 0 ? "Recently ahead, not proven" : "Did not beat the average stock",
        });
      }
    }
    return rows.sort((a, b) => b.perDay - a.perDay);
  }, [doc, ready]);

  if (!ready || !c) {
    return (
      <section className="plan" aria-labelledby="plan-h">
        <h4 id="plan-h">What could I make?</h4>
        <p className="muted">The outcome history is being computed. It appears here after the next scan.</p>
      </section>
    );
  }
  const q = c.q_bps as number[];
  const at = (b: number) => (b / 1e4) * amount;
  const days = Number(h);
  const when = choice.market === "crypto"
    ? `Any day: crypto never closes. These results buy at the daily close (02:00 Paris) and sell ${holdLabel(choice, h)} later.`
    : choice.kind === "index"
      ? `Any trading day. Buy at the close, sell ${holdLabel(choice, h)} later at the close.`
      : `The scan lists the stocks each evening. Buy at the next day’s close (22:00 Paris, 16:00 New York) and sell ${holdLabel(choice, h)} later at the close.`;
  const where = choice.market === "crypto"
    ? "A crypto exchange such as Binance (fees here: 0.10% each way). Practise first on Binance Testnet through your desk."
    : "A US-stock broker account. Practise first on a free Alpaca paper account through your desk (below); fees here assume a commission-free broker.";

  return (
    <section className="plan" aria-labelledby="plan-h">
      <div className="auto-head">
        <h4 id="plan-h">What could I make?</h4>
        <span className="muted">from {doc.base_rates.first_date} to {doc.base_rates.last_date}</span>
      </div>
      <p className="auto-meta">
        Nobody can tell you what one trade will make. What history can tell you is the spread of results the same move
        gave every time before, after costs. Pick an amount, a strategy and how long you would hold.
      </p>

      <div className="plan-in">
        <label className="plan-amt">
          <span>Amount</span>
          <div><b>$</b><input type="number" min={10} step={50} value={amount}
            onChange={(e) => setAmount(Math.max(10, Number(e.target.value) || 10))} aria-label="Amount in dollars" /></div>
        </label>
        <label>
          <span>Strategy</span>
          <select value={choice.key} onChange={(e) => setKey(e.target.value)}>
            {choices.map((ch) => <option key={ch.key} value={ch.key}>{ch.label}</option>)}
          </select>
        </label>
        <div className="plan-hold" role="radiogroup" aria-label="How long to hold">
          <span>Hold for</span>
          <div>
            {holds.map((x) => (
              <button key={x} role="radio" aria-checked={x === h} className={`chip ${x === h ? "on" : ""}`} onClick={() => setHold(x)}>
                {holdLabel(choice, x)}
              </button>
            ))}
          </div>
        </div>
      </div>

      <div className="plan-out">
        <div className="po main">
          <span>Typical result</span>
          <b className={at(q[QI.p50]) >= 0 ? "pos" : "neg"}><Count value={at(q[QI.p50])} fmt={signed} /></b>
          <em>half the time better, half worse</em>
        </div>
        <div className="po">
          <span>Chance of a profit</span>
          <b><Count value={c.win * 100} fmt={(x) => `${Math.round(x)}%`} /></b>
          <em>{c.n?.toLocaleString()} past cases</em>
        </div>
        <div className="po">
          <span>Bad case, 1 in 10</span>
          <b className="neg"><Count value={at(q[QI.p10])} fmt={signed} /></b>
          <em>or worse</em>
        </div>
        <div className="po">
          <span>Good case, 1 in 10</span>
          <b className="pos"><Count value={at(q[QI.p90])} fmt={signed} /></b>
          <em>or better</em>
        </div>
        <div className="po">
          <span>Average</span>
          <b className={c.mean_bps >= 0 ? "pos" : "neg"}><Count value={at(c.mean_bps)} fmt={signed} /></b>
          <em>after costs</em>
        </div>
        <div className="po">
          <span>Money tied up</span>
          <b>{holdLabel(choice, h)}</b>
          <em>{days} {choice.market === "crypto" ? "calendar" : "trading"} day{days > 1 ? "s" : ""}</em>
        </div>
      </div>

      <Band q={q} amount={amount} ref2={choice.key !== refSym ? spy?.q_bps : null} />
      <p className="band-key">
        <span className="k k-mid" /> middle half of results <span className="k k-out" /> 9 in 10 results <span className="k k-med" /> typical
        {choice.key !== refSym && spy && <> · lower bar: {refSym === "SPY" ? "SPY" : "Bitcoin"} held for the same time</>}
      </p>

      <dl className="plan-rules">
        <div><dt>When to put money in</dt><dd>{when}</dd></div>
        <div><dt>When to take it out</dt><dd>On the planned day, win or lose. The results above assume exactly that; holding a loser “until it comes back” is a different, untested strategy.</dd></div>
        <div><dt>Where</dt><dd>{where}</dd></div>
        {choice.kind === "index" && doc.index?.[choice.sym!] && (
          <div><dt>Worst drop in this history</dt><dd>{doc.index[choice.sym!].max_drawdown_pct}% from a peak, on the way to {doc.index[choice.sym!].total_pct > 0 ? "+" : ""}{doc.index[choice.sym!].total_pct}% over the period.</dd></div>
        )}
      </dl>

      <div className="rank">
        <h5>Ranked by evidence: what each choice returned per day held</h5>
        <div className="table-wrap">
          <table className="auto-table">
            <thead><tr><th scope="col">#</th><th scope="col">Strategy</th><th scope="col">Hold</th>
              <th scope="col" className="r">Typical on {usd(amount)}</th><th scope="col" className="r">Chance of profit</th>
              <th scope="col" className="r">Bad 1 in 10</th><th scope="col">Verdict</th></tr></thead>
            <tbody>
              {ranked.slice(0, 12).map((r, i) => (
                <tr key={r.ch.key + r.hold} className={r.ch.key === choice.key && r.hold === h ? "sel" : ""}
                  onClick={() => { setKey(r.ch.key); setHold(r.hold); }} style={{ cursor: "pointer" }}>
                  <td className="num">{i + 1}</td>
                  <th scope="row">{r.ch.label}</th>
                  <td>{holdLabel(r.ch, r.hold)}</td>
                  <td className={`num r ${r.median >= 0 ? "pos" : "neg"}`}>{signed(at(r.median))}</td>
                  <td className="num r">{Math.round(r.win * 100)}%</td>
                  <td className="num r neg">{signed(at(r.bad))}</td>
                  <td>{r.verdict === "Beat the average stock" ? <span className="gate open">{r.verdict}</span> : <span className="muted">{r.verdict}</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="fine">
          Ranked by average result per calendar day the money was invested, after costs, over this two-year history.
          Crypto swings far more than stocks: compare the bad cases, not just the typical ones. Click a row
          to load it above. These are past results of rules, not forecasts and not personal advice: a strong market
          flatters every “buy” rule, and the next two years can look nothing like the last two.
        </p>
      </div>
    </section>
  );
}
