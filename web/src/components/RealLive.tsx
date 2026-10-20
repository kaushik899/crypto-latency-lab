import { useState } from "react";
import { ASSETS, fmtPrice, fmtUsd, type AssetKey } from "../lib/assets";
import { VENUES, venueInfo } from "../lib/venues";
import { useReal } from "../lib/useReal";
import { RealRace } from "./RealRace";

const ms = (x: number) => (isFinite(x) ? `${Math.round(x)} ms` : "—");

export function RealLive() {
  const real = useReal(300);
  const [asset, setAsset] = useState<AssetKey>("BTC");
  const stats = real.venueStats();
  const arb = real.arbStats();
  const live = stats.filter((s) => s.status === "live").length;
  const bq = real.quotes[asset]?.binance;

  return (
    <section className="live" id="live" aria-labelledby="live-title">
      <div className="tickers">
        <span className={`dot ${live ? "live" : "connecting"}`} aria-hidden />
        <span className="feed-status">
          {live ? `Live: ${live} of ${VENUES.length} exchanges streaming to this page` : "Connecting to exchanges…"}
        </span>
        {ASSETS.map((a) => {
          const q = real.quotes[a.key]?.binance;
          return (
            <span className="tick" key={a.key}>
              <span className="tick-sym">{a.key}</span>
              <span className="tick-px">{fmtPrice(q ? (q.bid + q.ask) / 2 : undefined)}</span>
            </span>
          );
        })}
      </div>

      <div className="hero">
        <div className="hero-copy">
          <h1 id="live-title">Prices move on Binance first. Everyone else catches up.</h1>
          <p className="lede">
            This page streams real order books from five exchanges straight into your browser and times how long each
            one takes to follow a move on Binance. Then it checks whether the price gaps that open in between are big
            enough to trade after fees, at your own connection speed.
          </p>
        </div>

        <div className="race-panel">
          <div className="race-head">
            <div className="seg" role="tablist" aria-label="Coin">
              {ASSETS.map((a) => (
                <button key={a.key} role="tab" aria-selected={asset === a.key} onClick={() => setAsset(a.key)}>
                  {a.key}
                </button>
              ))}
            </div>
            <span className="race-meta">
              <span>Binance {asset}</span>
              <span className="race-clock spot">{bq ? fmtPrice((bq.bid + bq.ask) / 2) : "—"}</span>
            </span>
          </div>

          <RealRace asset={asset} />

          <ul className="venue-legend" aria-label="Exchanges">
            {stats.map((s) => (
              <li key={s.key}>
                <i className="sw" style={{ background: s.color }} />
                <span className="vn">{s.name}</span>
                <span className={`vs ${s.status}`}>
                  {s.status === "live" ? `feed delay ${ms(s.delayMs)}` : s.status === "down" ? "unreachable" : "connecting"}
                </span>
              </li>
            ))}
          </ul>
          <p className="fine">
            Each line is that exchange&rsquo;s mid-price, minus its usual price difference to Binance (USD vs USDT, fees),
            in basis points from a minute ago. A line that bends later than the blue one is following Binance.
          </p>

          <div className="controls">
            <label className="slider">
              <span>Your order latency <b>{real.latencyMs} ms</b></span>
              <input type="range" min={0} max={1000} step={10} value={real.latencyMs}
                onChange={(e) => { real.latencyMs = +e.target.value; }} />
            </label>
          </div>
        </div>

        <aside className="ledger" aria-label="Live results from real data">
          <h2>Live, from real data</h2>
          <dl>
            {stats.filter((s) => s.key !== "binance").map((s) => (
              <div key={s.key}>
                <dt>{s.name} follows Binance in</dt>
                <dd>{isFinite(s.catchMs) ? ms(s.catchMs) : "measuring…"} <small>({s.catchN})</small></dd>
              </div>
            ))}
          </dl>
          <h3 className="ledger-sub">Cross-exchange gaps after fees</h3>
          <dl>
            <div><dt>Gaps that beat fees</dt><dd>{arb.signals}</dd></div>
            <div><dt>Still there after {real.latencyMs} ms</dt><dd>{arb.fills}</dd></div>
            <div><dt>Paper P&amp;L, $1,000 per trade</dt><dd className={arb.pnl >= 0 ? "pos" : "neg"}>{fmtUsd(arb.pnl, 2)}</dd></div>
          </dl>
          <ol className="blotter">
            {real.trades.filter((t) => t.filled !== undefined).slice(0, 5).map((t) => (
              <li key={t.id} className={t.filled ? "f" : "m"}>
                <span>{t.asset}</span>
                <span>{venueInfo(t.buy).name} → {venueInfo(t.sell).name}</span>
                <span>{t.filled ? `+${t.netArrival!.toFixed(1)} bp` : "gone"}</span>
              </li>
            ))}
            {!arb.signals && <li className="empty">No gap has beaten the fees yet. That is the usual state of things.</li>}
          </ol>
          <p className="fine">Fees: Binance, OKX, Bybit 0.10%; Coinbase 0.50%; Kraken 0.80% per side (entry-level taker).</p>
        </aside>
      </div>
    </section>
  );
}
