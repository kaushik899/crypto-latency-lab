"""Live monitor + paper trader + recorder.

    python -m latlab.live                        # watch live edges, paper-trade, print a dashboard
    python -m latlab.live --minutes 90 --record  # also save ticks/books for the backtester

Streams Binance spot for all assets and Polymarket Up/Down books, prices every market
with the same model as the backtester, and paper-trades signals with a simulated
latency: a paper order fills only if the ask is still at or below the limit when it
"arrives". Nothing is ever sent to any exchange.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import logging
import time
from dataclasses import asdict
from pathlib import Path

from .config import ASSETS, WINDOWS
from .fees import TICK, fee_per_share, taker_fee
from .pricing import SECONDS_PER_YEAR, EWMAVol, digital_prob
from .strategy import StrategyParams
from .feeds import binance, polymarket

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("latlab.live")


class Engine:
    def __init__(self, assets, windows, p: StrategyParams, record_dir: Path | None):
        self.assets, self.windows, self.p = assets, windows, p
        self.px: dict[str, float] = {}
        self.vol = {a: EWMAVol(halflife_s=p.vol_halflife_s, init_sigma=ASSETS[a].sigma0) for a in assets}
        self.markets: dict[str, polymarket.LiveMarket] = {}
        self.book: dict[str, tuple] = {}
        self.open_px: dict[str, float] = {}     # market -> spot at window open (the strike)
        self.paper: list[dict] = []
        self.next_ok: dict[str, float] = {}
        self.rec = None
        self.book_task: asyncio.Task | None = None
        if record_dir:
            record_dir.mkdir(parents=True, exist_ok=True)
            self.rec_dir = record_dir
            self.tick_f = open(record_dir / "ticks.csv", "w", newline="")
            self.book_f = open(record_dir / "books.csv", "w", newline="")
            self.tick_w = csv.writer(self.tick_f); self.tick_w.writerow(["ts", "asset", "price", "bid", "ask"])
            self.book_w = csv.writer(self.book_f)
            self.book_w.writerow(["ts", "market_id", "yes_bid", "bid_size", "yes_ask", "ask_size"])
            self.rec = True
            self._last_tick = {}

    # ---------------------------------------------------------------- spot
    async def run_spot(self):
        async for ts, a, mid, bid, ask in binance.book_tickers(self.assets):
            self.px[a] = mid
            self.vol[a].update(ts, mid)
            for m in self.markets.values():
                if m.asset == a and m.market_id not in self.open_px and ts >= m.start and ts - m.start < 2.0:
                    self.open_px[m.market_id] = mid
            if self.rec and ts - self._last_tick.get(a, 0) >= 0.05:
                self._last_tick[a] = ts
                self.tick_w.writerow([f"{ts:.3f}", a, mid, bid, ask])

    # ---------------------------------------------------------------- discovery + books
    async def run_discovery(self):
        while True:
            found = await polymarket.discover(self.assets, self.windows)
            now = time.time()
            changed = False
            for m in found:
                if m.market_id not in self.markets and m.end > now:
                    self.markets[m.market_id] = m
                    changed = True
            for k in [k for k, m in self.markets.items() if m.end < now - 5]:
                self.settle(self.markets.pop(k))
                changed = True
            if changed:
                if self.book_task:
                    self.book_task.cancel()
                self.book_task = asyncio.create_task(self.run_books(list(self.markets.values())))
                if self.rec:
                    (self.rec_dir / "markets.json").write_text(json.dumps(self._market_meta(), indent=1))
            await asyncio.sleep(30)

    async def run_books(self, mkts):
        if not mkts:
            return
        async for ts, m, (bb, bs, ba, as_) in polymarket.books(mkts):
            self.book[m.market_id] = (ts, bb, bs, ba, as_)
            if self.rec:
                self.book_w.writerow([f"{ts:.3f}", m.market_id, bb, bs, ba, as_])
            self.evaluate(m, ts)

    def _market_meta(self):
        meta = getattr(self, "_all_meta", {})
        for m in self.markets.values():
            meta[m.market_id] = dict(market_id=m.market_id, asset=m.asset, kind="updown", label=m.label,
                                     start=m.start, end=m.end, strike=self.open_px.get(m.market_id),
                                     meta={"window": m.window, **m.meta})
        self._all_meta = meta
        return list(meta.values())

    # ---------------------------------------------------------------- pricing + paper trading
    def fair(self, m, ts):
        S, K = self.px.get(m.asset), self.open_px.get(m.market_id)
        if S is None or K is None or ts < m.start:
            return None
        return float(digital_prob(S, K, max(m.end - ts, 0) / SECONDS_PER_YEAR, self.vol[m.asset].sigma, dist=self.p.dist))

    def edges(self, m, ts):
        q = self.fair(m, ts)
        b = self.book.get(m.market_id)
        if q is None or not b:
            return None
        _, bb, bs, ba, as_ = b
        e_yes = q - ba - fee_per_share(ba, self.p.fee_rate)
        e_no = (1 - q) - (1 - bb) - fee_per_share(1 - bb, self.p.fee_rate)
        return q, bb, ba, e_yes, e_no

    def evaluate(self, m, ts):
        e = self.edges(m, ts)
        if not e or ts < self.next_ok.get(m.market_id, 0):
            return
        q, bb, ba, e_yes, e_no = e
        tau = m.end - ts
        if not (self.p.min_tau_s <= tau <= self.p.max_tau_s) or max(e_yes, e_no) <= self.p.entry_edge:
            return
        self.next_ok[m.market_id] = ts + self.p.cooldown_s
        side = "YES" if e_yes >= e_no else "NO"
        px = ba if side == "YES" else 1 - bb
        asyncio.get_running_loop().call_later(self.p.latency_ms / 1000, self.arrive, m, side, px,
                                              max(e_yes, e_no), q, ts)

    def arrive(self, m, side, px_sig, edge, q, ts_sig):
        b = self.book.get(m.market_id)
        if not b:
            return
        _, bb, bs, ba, as_ = b
        px = ba if side == "YES" else 1 - bb
        size = as_ if side == "YES" else bs
        filled = px <= px_sig + self.p.slippage_ticks * TICK and size > 0
        stake = min(self.p.kelly_fraction * edge / (1 - px_sig) * self.p.bankroll, self.p.max_stake)
        shares = int(min(stake / px, size)) if filled else 0
        self.paper.append(dict(ts=ts_sig, market=m.market_id, label=m.label, side=side, fair=q,
                               px_signal=px_sig, px_fill=px if shares else None, shares=shares,
                               fee=taker_fee(shares, px, self.p.fee_rate) if shares else 0.0, pnl=None))
        log.info("%s %s %s @ %.2f (fair %.3f) -> %s", m.label, side, "FILL" if shares else "MISS", px_sig, q,
                 f"{shares} sh @ {px:.2f}" if shares else "moved")

    def settle(self, m):
        S, K = self.px.get(m.asset), self.open_px.get(m.market_id)
        if S is None or K is None:
            return
        up = 1.0 if S >= K else 0.0   # Binance proxy for the Chainlink settlement price
        for t in self.paper:
            if t["market"] == m.market_id and t["shares"] and t["pnl"] is None:
                out = up if t["side"] == "YES" else 1 - up
                t["pnl"] = t["shares"] * (out - t["px_fill"]) - t["fee"]

    # ---------------------------------------------------------------- console
    async def run_console(self, every=2.0):
        while True:
            await asyncio.sleep(every)
            now = time.time()
            lines = [f"\n{time.strftime('%H:%M:%S')}  " + "  ".join(f"{a} {self.px.get(a, float('nan')):,.4g} "
                     f"σ={self.vol[a].sigma:.0%}" for a in self.assets)]
            rows = []
            for m in self.markets.values():
                e = self.edges(m, now)
                if e:
                    q, bb, ba, ey, en = e
                    rows.append((max(ey, en), f"  {m.label[:44]:44s} τ={m.end - now:5.0f}s  fair {q:.3f}  "
                                 f"bid/ask {bb:.2f}/{ba:.2f}  edge YES {ey:+.3f} NO {en:+.3f}"))
            lines += [r for _, r in sorted(rows, reverse=True)[:12]] or ["  (waiting for markets / open prices)"]
            done = [t for t in self.paper if t["pnl"] is not None]
            lines.append(f"  paper: {len(self.paper)} signals, {sum(1 for t in self.paper if t['shares'])} fills, "
                         f"settled P&L ${sum(t['pnl'] for t in done):,.2f}")
            print("\n".join(lines), flush=True)
            if self.rec:
                self.tick_f.flush(); self.book_f.flush()

    def close(self):
        if self.rec:
            self.tick_f.close(); self.book_f.close()
            (self.rec_dir / "markets.json").write_text(json.dumps(self._market_meta(), indent=1))
            (self.rec_dir / "paper_trades.json").write_text(json.dumps(self.paper, indent=1))
            (self.rec_dir / "params.json").write_text(json.dumps(asdict(self.p), indent=1))


async def main_async(a):
    p = StrategyParams(latency_ms=a.latency_ms, entry_edge=a.entry_edge)
    rec = ROOT / "data/recordings" / time.strftime("%Y%m%d-%H%M%S") if a.record else None
    eng = Engine(a.assets, a.windows, p, rec)
    tasks = [asyncio.create_task(c) for c in (eng.run_spot(), eng.run_discovery(), eng.run_console())]
    try:
        await asyncio.wait_for(asyncio.gather(*tasks), timeout=a.minutes * 60 if a.minutes else None)
    except asyncio.TimeoutError:
        pass
    finally:
        for t in tasks:
            t.cancel()
        eng.close()
        if rec:
            print(f"recording saved to {rec}\n  build a tape:  python -m latlab.tapes {rec}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--assets", nargs="+", default=list(ASSETS))
    ap.add_argument("--windows", nargs="+", default=["5m", "15m"], choices=list(WINDOWS))
    ap.add_argument("--minutes", type=float, default=0, help="stop after N minutes (0 = run until Ctrl-C)")
    ap.add_argument("--record", action="store_true")
    ap.add_argument("--latency-ms", type=float, default=150)
    ap.add_argument("--entry-edge", type=float, default=0.015)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(main_async(a))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
