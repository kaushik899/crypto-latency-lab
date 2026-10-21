import { useMemo, useState } from "react";
import {
  Area, Bar, BarChart, CartesianGrid, Cell, ComposedChart, Line, LineChart, ReferenceLine, ResponsiveContainer,
  Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from "recharts";
import data from "../data/research.json";
import { fmtUsd } from "../lib/assets";

type AnyRec = Record<string, any>;
const R = data as AnyRec;
const KEYS = Object.keys(R.regimes) as string[];
const COLORS: Record<string, string> = { no_fees_slow: "#A39A8B", fees_slow: "#D9B26A", fees_crowded: "#E0694B", recorded: "#6E93D6" };

const axis = { stroke: "#6F675B", fontSize: 12, tickLine: false, axisLine: { stroke: "#3A332A" } } as const;
const grid = <CartesianGrid stroke="rgba(163,154,139,0.12)" vertical={false} />;
const tip = {
  contentStyle: { background: "#1E1A15", border: "1px solid #3A332A", borderRadius: 8, color: "#EDE6D8", fontSize: 13 },
  labelStyle: { color: "#A39A8B" },
  cursor: { stroke: "#3A332A" },
} as const;
const usd = (v: number) => fmtUsd(v);

function breakEven(rows: AnyRec[], key = "mean") {
  for (let i = 1; i < rows.length; i++) {
    const a = rows[i - 1], b = rows[i];
    if (a[key] > 0 && b[key] <= 0) {
      const f = a[key] / (a[key] - b[key]);
      return Math.round(a.latency_ms + f * (b.latency_ms - a.latency_ms));
    }
  }
  return rows[rows.length - 1][key] > 0 ? Infinity : rows[0][key] <= 0 ? 0 : null;
}

function Figure({ title, note, children, wide }: { title: string; note: string; children: React.ReactNode; wide?: boolean }) {
  return (
    <figure className={`fig ${wide ? "wide" : ""}`}>
      <figcaption>
        <h3>{title}</h3>
        <p>{note}</p>
      </figcaption>
      <div className="fig-plot">{children}</div>
    </figure>
  );
}

export function Research() {
  const [key, setKey] = useState(KEYS[KEYS.length - 1]);
  const reg = R.regimes[key];
  const s = reg.summary;
  const band: AnyRec[] = reg.latency_band ?? reg.latency.map((r: AnyRec) => ({ latency_ms: r.latency_ms, mean: r.pnl, lo: r.pnl, hi: r.pnl }));
  const be = breakEven(band);
  const halfLife = useMemo(() => {
    const sv: AnyRec[] = reg.gaps?.survival ?? [];
    for (let i = 1; i < sv.length; i++) if (sv[i].share <= 0.5) {
      const a = sv[i - 1], b = sv[i];
      return Math.round(a.ms + ((a.share - 0.5) / (a.share - b.share)) * (b.ms - a.ms));
    }
    return null;
  }, [reg]);

  const compare = R.latencies.map((lat: number, i: number) => {
    const row: AnyRec = { latency_ms: lat };
    for (const k of KEYS) row[k] = (R.regimes[k].latency_band ?? R.regimes[k].latency)[i]?.mean ?? R.regimes[k].latency[i].pnl;
    return row;
  });
  const baseLat = R.strategy.latency_ms;
  const hours = R.hours;

  return (
    <div className="research">

      <Figure
        wide
        title="Same strategy, three markets"
        note="Paper P&L on a $10,000 budget as order latency grows. In a slow, fee-free market almost any bot profits. Add the taker fee and fast rivals, and only near-instant orders survive."
      >
        <ResponsiveContainer width="100%" height={340}>
          <LineChart data={compare} margin={{ top: 10, right: 16, bottom: 22, left: 8 }}>
            {grid}
            <XAxis dataKey="latency_ms" {...axis} label={{ value: "Order latency (ms)", position: "insideBottom", offset: -12, fill: "#A39A8B", fontSize: 12 }} />
            <YAxis {...axis} tickFormatter={usd} width={70} />
            <Tooltip {...tip} formatter={(v: any, n: any) => [usd(v), R.regimes[n]?.title ?? n]} labelFormatter={(l) => `${l} ms`} />
            <ReferenceLine y={0} stroke="#A39A8B" strokeDasharray="4 4" />
            {KEYS.map((k) => (
              <Line key={k} dataKey={k} stroke={COLORS[k]} strokeWidth={k === key ? 3 : 1.6} dot={false} isAnimationActive={false} />
            ))}
          </LineChart>
        </ResponsiveContainer>
        <ul className="key">
          {KEYS.map((k) => (
            <li key={k}><i style={{ background: COLORS[k] }} />{R.regimes[k].title}</li>
          ))}
        </ul>
      </Figure>

      <div className="regime-tabs" role="tablist" aria-label="Scenario">
        {KEYS.map((k) => (
          <button key={k} role="tab" aria-selected={k === key} onClick={() => setKey(k)} style={{ ["--c" as any]: COLORS[k] }}>
            {R.regimes[k].title}
          </button>
        ))}
      </div>

      <div className="verdict" role="tabpanel">
        <p className="verdict-blurb">{reg.blurb}</p>
        <p className="verdict-line">
          At {baseLat} ms the bot {s.pnl >= 0 ? "made" : "lost"} <b className={s.pnl >= 0 ? "pos" : "neg"}>{fmtUsd(Math.abs(s.pnl))}</b> over{" "}
          {hours} hours from {s.fills.toLocaleString()} fills, winning {(s.hit_rate * 100).toFixed(1)}% of them and paying{" "}
          {fmtUsd(s.fees)} in fees.{" "}
          {s.t_stat != null && (Math.abs(s.t_stat) < 2
            ? `With t = ${s.t_stat.toFixed(1)}, that result is not statistically distinguishable from zero.`
            : `t = ${s.t_stat.toFixed(1)}, so the average trade is reliably ${s.t_stat > 0 ? "positive" : "negative"} under these assumptions.`)}
          {" "}
          {be === Infinity
            ? "It stays profitable across every latency tested."
            : be === 0
              ? "It loses money at every latency tested."
              : be != null && `Averaged over all simulated days it breaks even at about ${be} ms.`}
        </p>
      </div>

      <div className="figs">
        <Figure title="How fast the gap closes" note={`Share of fee-clearing gaps still open after a given delay.${halfLife != null ? ` Half are gone within ${halfLife} ms.` : ""}`}>
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={reg.gaps?.survival ?? []} margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
              {grid}
              <XAxis dataKey="ms" {...axis} type="number" domain={[0, 2000]} />
              <YAxis {...axis} tickFormatter={(v) => `${Math.round(v * 100)}%`} domain={[0, 1]} width={44} />
              <Tooltip {...tip} formatter={(v: any) => [`${(v * 100).toFixed(1)}%`, "still open"]} labelFormatter={(l) => `after ${l} ms`} />
              <Area dataKey="share" type="monotone" stroke="#C98BA6" fill="rgba(201,139,166,0.25)" strokeWidth={2} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </Figure>

        <Figure title="Where the fee bites" note="P&L by the price paid. The fee is 0.07 × p × (1 − p) per share, so it is heaviest near 50¢, exactly where these markets spend most of their life.">
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={reg.by_price ?? []} margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
              {grid}
              <XAxis dataKey="bucket" {...axis} tickFormatter={(b: string) => b.replace(/[(\]]/g, "").replace(", ", "–")} />
              <YAxis yAxisId="l" {...axis} tickFormatter={usd} width={62} />
              <YAxis yAxisId="r" orientation="right" {...axis} tickFormatter={(v) => `${(v * 100).toFixed(1)}¢`} width={46} />
              <Tooltip {...tip} formatter={(v: any, n: any) => (n === "pnl" ? [usd(v), "P&L"] : [`${(v * 100).toFixed(2)}¢`, "fee / share"])} />
              <ReferenceLine yAxisId="l" y={0} stroke="#6F675B" />
              <Bar yAxisId="l" dataKey="pnl" isAnimationActive={false} radius={[3, 3, 0, 0]}>
                {(reg.by_price ?? []).map((d: AnyRec, i: number) => <Cell key={i} fill={d.pnl >= 0 ? "#7FCBA4" : "#E0694B"} />)}
              </Bar>
              <Line yAxisId="r" dataKey="fee_per_share" stroke="#6E93D6" strokeWidth={2} dot={{ r: 3 }} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </Figure>

        <Figure title="Is the model right?" note="Model probability when the order filled against how often that side actually won. On the diagonal, the pricing is honest. Below it, the orders that still fill are the ones the market was right about — adverse selection, the tax on being slow.">
          <ResponsiveContainer width="100%" height={240}>
            <ScatterChart margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
              {grid}
              <XAxis dataKey="predicted" type="number" domain={[0, 1]} {...axis} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
              <YAxis dataKey="realised" type="number" domain={[0, 1]} {...axis} tickFormatter={(v) => `${Math.round(v * 100)}%`} width={44} />
              <ZAxis dataKey="n" range={[30, 260]} />
              <Tooltip {...tip} formatter={(v: any, n: any) => [n === "n" ? v : `${(v * 100).toFixed(1)}%`, n]} />
              <ReferenceLine segment={[{ x: 0, y: 0 }, { x: 1, y: 1 }]} stroke="#6F675B" strokeDasharray="4 4" />
              <Scatter data={reg.calibration ?? []} fill="#D9B26A" isAnimationActive={false} />
            </ScatterChart>
          </ResponsiveContainer>
        </Figure>

        <Figure title="If the model is wrong" note="The same markets traded with other volatility and tail assumptions. Shrinking the fair value toward 50% mimics a sloppy model, and it is always the worst line on the chart.">
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={reg.model_risk ?? []} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 4 }}>
              <CartesianGrid stroke="rgba(163,154,139,0.12)" horizontal={false} />
              <XAxis type="number" {...axis} tickFormatter={usd} />
              <YAxis type="category" dataKey="model" {...axis} width={150} />
              <Tooltip {...tip} formatter={(v: any) => [usd(v), "P&L"]} />
              <ReferenceLine x={0} stroke="#6F675B" />
              <Bar dataKey="pnl" isAnimationActive={false} radius={[0, 3, 3, 0]}>
                {(reg.model_risk ?? []).map((d: AnyRec, i: number) => <Cell key={i} fill={d.pnl >= 0 ? "#7FCBA4" : "#E0694B"} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Figure>

        <Figure title="By coin" note={`Paper P&L per coin at ${baseLat} ms. With six hours per scenario, differences between coins are mostly noise.`}>
          <ResponsiveContainer width="100%" height={240}>
            <BarChart data={reg.by_asset ?? []} margin={{ top: 8, right: 12, bottom: 4, left: 0 }}>
              {grid}
              <XAxis dataKey="asset" {...axis} />
              <YAxis {...axis} tickFormatter={usd} width={62} />
              <Tooltip {...tip} formatter={(v: any) => [usd(v), "P&L"]} />
              <ReferenceLine y={0} stroke="#6F675B" />
              <Bar dataKey="pnl" isAnimationActive={false} radius={[3, 3, 0, 0]}>
                {(reg.by_asset ?? []).map((d: AnyRec, i: number) => <Cell key={i} fill={d.pnl >= 0 ? "#7FCBA4" : "#E0694B"} />)}
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        </Figure>

        <Figure title="Equity over the session" note={`Cumulative P&L as markets settle. Worst drawdown: ${fmtUsd(s.max_drawdown ?? 0)}.`}>
          <ResponsiveContainer width="100%" height={240}>
            <ComposedChart data={reg.equity ?? []} margin={{ top: 8, right: 12, bottom: 18, left: 0 }}>
              {grid}
              <XAxis dataKey="t" type="number" domain={[0, "dataMax"]} {...axis} tickFormatter={(t) => `${(t / 3600).toFixed(0)}h`} />
              <YAxis {...axis} tickFormatter={usd} width={62} />
              <Tooltip {...tip} formatter={(v: any) => [usd(v), "equity"]} labelFormatter={(t: any) => `${(Number(t) / 3600).toFixed(2)} h`} />
              <ReferenceLine y={0} stroke="#6F675B" />
              <Line dataKey="equity" stroke={COLORS[key]} strokeWidth={2} dot={false} isAnimationActive={false} />
            </ComposedChart>
          </ResponsiveContainer>
        </Figure>
      </div>
    </div>
  );
}
