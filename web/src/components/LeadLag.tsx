import { useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ASSETS, type AssetKey } from "../lib/assets";
import { STEP_MS } from "../lib/real";
import { useReal } from "../lib/useReal";
import { VENUES } from "../lib/venues";
import recorded from "../data/real-session.json";

const OTHERS = VENUES.filter((v) => v.key !== "binance");
const axis = { stroke: "#6F675B", fontSize: 12, tickLine: false, axisLine: { stroke: "#3A332A" } } as const;
const tip = {
  contentStyle: { background: "#1E1A15", border: "1px solid #3A332A", borderRadius: 8, color: "#EDE6D8", fontSize: 13 },
  labelStyle: { color: "#A39A8B" },
} as const;
const fmtMs = (x: number | null | undefined) => (x != null && isFinite(x) ? `${Math.round(x)}` : "—");
const REC = recorded as any;

export function LeadLag() {
  const real = useReal(1000);
  const [asset, setAsset] = useState<AssetKey>("BTC");
  const xc = real.xcorr[asset] ?? {};
  const chart = Array.from({ length: 41 }, (_, i) => {
    const row: Record<string, number> = { lag: (i - 20) * STEP_MS };
    for (const v of OTHERS) row[v.key] = (xc as any)[v.key]?.series?.[i] ?? NaN;
    return row;
  });

  return (
    <div className="leadlag">

      <div className="table-wrap">
        <table>
          <caption className="sr">Median catch-up time in milliseconds, by exchange and coin</caption>
          <thead>
            <tr>
              <th scope="col">Catch-up time (ms)</th>
              {ASSETS.map((a) => <th key={a.key} scope="col" className="r">{a.key}</th>)}
              <th scope="col" className="r">Never followed within 3 s</th>
            </tr>
          </thead>
          <tbody>
            {OTHERS.map((v) => (
              <tr key={v.key}>
                <th scope="row"><i className="sw" style={{ background: v.color }} /> {v.name}</th>
                {ASSETS.map((a) => {
                  const xs = real.lags[v.key]?.[a.key] ?? [];
                  const s = [...xs].sort((p, q) => p - q);
                  const med = s.length ? s[s.length >> 1] : NaN;
                  return <td key={a.key} className="num r">{fmtMs(med)} <span className="muted">({xs.length})</span></td>;
                })}
                <td className="num r">{real.censored[v.key] ?? 0}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <figure className="fig wide">
        <figcaption>
          <h3>Lead-lag correlation</h3>
          <p>
            Correlation between each exchange&rsquo;s price changes and Binance&rsquo;s, shifted in time over the last five
            minutes. A peak to the right of zero means that exchange follows Binance by that many milliseconds.
          </p>
        </figcaption>
        <div className="seg" role="tablist" aria-label="Coin" style={{ marginBottom: 10 }}>
          {ASSETS.map((a) => (
            <button key={a.key} role="tab" aria-selected={asset === a.key} onClick={() => setAsset(a.key)}>{a.key}</button>
          ))}
        </div>
        <ResponsiveContainer width="100%" height={280}>
          <LineChart data={chart} margin={{ top: 8, right: 16, bottom: 22, left: 0 }}>
            <CartesianGrid stroke="rgba(163,154,139,0.12)" vertical={false} />
            <XAxis dataKey="lag" {...axis} tickFormatter={(v) => `${v}`}
              label={{ value: "Exchange follows Binance by (ms)", position: "insideBottom", offset: -12, fill: "#A39A8B", fontSize: 12 }} />
            <YAxis {...axis} width={44} tickFormatter={(v) => v.toFixed(2)} />
            <Tooltip {...tip} formatter={(v: any, n: any) => [isFinite(v) ? Number(v).toFixed(3) : "—", VENUES.find((x) => x.key === n)?.name ?? n]}
              labelFormatter={(l) => `${l} ms`} />
            <ReferenceLine x={0} stroke="#6F675B" strokeDasharray="4 4" />
            {OTHERS.map((v) => (
              <Line key={v.key} dataKey={v.key} stroke={v.color} dot={false} strokeWidth={2} isAnimationActive={false} connectNulls />
            ))}
          </LineChart>
        </ResponsiveContainer>
        <ul className="key">
          {OTHERS.map((v) => {
            const x = (xc as any)[v.key];
            return <li key={v.key}><i style={{ background: v.color }} />{v.name}{x && isFinite(x.corr) ? `: peak at ${x.lagMs} ms` : ""}</li>;
          })}
        </ul>
      </figure>

      {REC?.minutes > 0 && <RecordedSession />}
    </div>
  );
}

function RecordedSession() {
  const r = REC;
  const when = new Date(r.recordedAt);
  return (
    <div className="recorded">
      <h3>Recorded session</h3>
      <p className="verdict-blurb">
        {Math.round(r.minutes)} minutes of real data captured from Paris on{" "}
        {when.toLocaleDateString("en-GB", { day: "numeric", month: "long", year: "numeric" })}, starting{" "}
        {when.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Paris" })} Paris time.
      </p>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th scope="col">Exchange</th>
              <th scope="col" className="r">Feed delay, median</th>
              <th scope="col" className="r">Follows Binance in, median</th>
              <th scope="col" className="r">Moves measured</th>
              <th scope="col" className="r">Taker fee</th>
            </tr>
          </thead>
          <tbody>
            {(r.venues as any[]).map((v) => (
              <tr key={v.key}>
                <th scope="row">{v.name}</th>
                <td className="num r">{fmtMs(v.delayMs)} ms</td>
                <td className="num r">{v.key === "binance" ? "leader" : `${fmtMs(v.catchMs)} ms`}</td>
                <td className="num r">{v.key === "binance" ? "—" : v.catchN}</td>
                <td className="num r">{(v.takerBps / 100).toFixed(2)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="verdict-line">
        Across the session, {r.arb.gapsSeen.toLocaleString()} cross-exchange gaps beat both taker fees. After a {r.latencyMs} ms
        order delay, {r.arb.fills.toLocaleString()} of {r.arb.signals.toLocaleString()} were still there, for paper P&amp;L of{" "}
        <b className={r.arb.pnl >= 0 ? "pos" : "neg"}>${r.arb.pnl.toFixed(2)}</b> on $1,000 per trade.
      </p>
    </div>
  );
}
