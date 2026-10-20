import { useState } from "react";
import { ASSETS, fmtCents, fmtPrice, fmtUsd, type AssetKey } from "../lib/assets";
import { REGIMES } from "../lib/lab";
import { useLab } from "../lib/useLab";
import { Race } from "./Race";

export function PredictSim() {
  const lab = useLab(250);
  const [asset, setAsset] = useState<AssetKey>("BTC");
  const [win, setWin] = useState<"5m" | "15m">("5m");
  const markets = [...lab.markets.values()];
  const current = markets.find((m) => m.asset === asset && m.window === win);
  const s = lab.stats();
  const now = Date.now() / 1000;
  const fillRate = s.signals ? s.fills / s.signals : 0;

  return (
    <section className="live predict" id="predict" aria-labelledby="predict-title">
      <div className="hero">
        <div className="hero-copy">
          <h2 id="predict-title" className="h-sec">What if it were a prediction market?</h2>
          <p className="lede">
            The viral bots trade Polymarket&rsquo;s 5- and 15-minute &ldquo;up or down&rdquo; markets, which are blocked in
            France. Here the same live prices drive a <b>simulated</b> market maker that updates late, and a paper trader
            prices each market as a binary option and buys only when the gap beats Polymarket&rsquo;s fee.
          </p>
        </div>

        <div className="race-panel">
          <div className="race-head">
            <div className="seg" role="tablist" aria-label="Asset">
              {ASSETS.map((a) => (
                <button key={a.key} role="tab" aria-selected={asset === a.key} onClick={() => setAsset(a.key)}>
                  {a.key}
                </button>
              ))}
            </div>
            <div className="seg" role="tablist" aria-label="Window">
              {(["5m", "15m"] as const).map((w) => (
                <button key={w} role="tab" aria-selected={win === w} onClick={() => setWin(w)}>
                  {w}
                </button>
              ))}
            </div>
            {current && (
              <span className="race-meta">
                <span>Pays $1 if {asset} closes at or above {fmtPrice(current.strike)}</span>
                <span className="race-clock">{Math.max(0, Math.ceil(current.end - now))}s left</span>
              </span>
            )}
          </div>

          <Race marketId={current?.id} />

          <div className="legend">
            <span><i className="sw fair" /> Fair value from Binance spot</span>
            <span><i className="sw quote" /> Market bid / ask</span>
            <span><i className="sw gap" /> Lag gap</span>
            <span><i className="sw flight" /> Your order in flight</span>
            <span><i className="sw filled" /> Filled</span>
            <span><i className="sw missed" /> Missed</span>
          </div>

          <div className="controls">
            <label className="slider">
              <span>
                Your latency <b>{lab.latencyMs} ms</b>
              </span>
              <input
                type="range" min={0} max={2000} step={50} value={lab.latencyMs}
                onChange={(e) => { lab.latencyMs = +e.target.value; lab.resetLedger(); }}
              />
            </label>
            <label className="slider">
              <span>
                Minimum edge after fees <b>{(lab.entryEdge * 100).toFixed(1)}¢</b>
              </span>
              <input
                type="range" min={0} max={0.06} step={0.0025} value={lab.entryEdge}
                onChange={(e) => { lab.entryEdge = +e.target.value; lab.resetLedger(); }}
              />
            </label>
            <div className="seg regime" role="radiogroup" aria-label="Competition">
              {REGIMES.map((r) => (
                <button key={r.key} role="radio" aria-checked={lab.regime.key === r.key}
                  onClick={() => { lab.setRegime(r.key); lab.resetLedger(); }}>
                  {r.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        <aside className="ledger" aria-label="Paper trading results this session">
          <h2>This session, on paper</h2>
          <dl>
            <div><dt>Settled P&amp;L</dt><dd className={s.pnl >= 0 ? "pos" : "neg"}>{fmtUsd(s.pnl, 2)}</dd></div>
            <div><dt>Open, marked to model</dt><dd>{fmtUsd(s.mtm, 2)}</dd></div>
            <div><dt>Orders filled</dt><dd>{s.fills} of {s.signals} <small>({(fillRate * 100).toFixed(0)}%)</small></dd></div>
            <div><dt>Won / settled</dt><dd>{s.wins} / {s.settled}</dd></div>
            <div><dt>Taker fees paid</dt><dd>{fmtUsd(s.fees, 2)}</dd></div>
          </dl>
          <ol className="blotter">
            {lab.trades.slice(0, 7).map((t) => (
              <li key={t.id} className={t.filled ? "f" : "m"}>
                <span>{t.asset} {t.window}</span>
                <span>{t.side} {fmtCents(t.pxSignal)}</span>
                <span>{t.filled ? (t.pnl === undefined ? `${t.shares} sh` : fmtUsd(t.pnl, 2)) : "missed"}</span>
              </li>
            ))}
            {!lab.trades.length && <li className="empty">Signals appear here when the gap beats the fee.</li>}
          </ol>
          <p className="fine">Simulated quotes, ${lab.stake} per order. The real Polymarket recorder is in the Python engine.</p>
        </aside>
      </div>
    </section>
  );
}
