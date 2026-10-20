"""A time-aligned record of spot prices and prediction-market order books.

Both the synthetic simulator and the live recorder produce a ``Tape``; the strategy and
backtester only ever see this structure, so research on simulated and on real data runs
through identical code.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class Market:
    market_id: str
    asset: str             # "BTC", "ETH", "SOL", ...
    kind: str              # "updown" or "strike"
    label: str             # e.g. "BTC Up/Down 15m 14:30" or "BTC > 112,000 @ 16:00"
    start: float           # seconds on the tape clock when the window opens
    end: float             # seconds when it resolves
    strike: float          # window-open reference price (updown) or K (strike)
    yes_bid: np.ndarray
    yes_ask: np.ndarray
    bid_size: np.ndarray
    ask_size: np.ndarray
    settle_price: float = float("nan")   # reference (oracle) price at ``end``
    meta: dict = field(default_factory=dict)
    i0: int = 0            # tape index of the first element of the book arrays (arrays cover the window only)

    @property
    def n(self) -> int:
        return len(self.yes_ask)

    @property
    def sl(self) -> slice:
        return slice(self.i0, self.i0 + self.n)

    @property
    def outcome(self) -> float:
        if np.isnan(self.settle_price):
            return float("nan")
        return 1.0 if self.settle_price >= self.strike else 0.0


@dataclass
class Tape:
    t: np.ndarray                    # seconds, strictly increasing, evenly spaced
    spot: dict[str, np.ndarray]      # asset -> exchange spot (Binance <ASSET>USDT)
    reference: dict[str, np.ndarray] # asset -> oracle/reference price (Chainlink in production)
    markets: list[Market]
    source: str = "simulation"
    meta: dict = field(default_factory=dict)

    @property
    def dt(self) -> float:
        return float(self.t[1] - self.t[0])

    def index_of(self, seconds: float) -> int:
        return int(np.clip(np.searchsorted(self.t, seconds), 0, len(self.t) - 1))

    # --- persistence -------------------------------------------------------------------
    def save(self, folder: str | Path) -> Path:
        folder = Path(folder)
        folder.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            folder / "tape.npz",
            t=self.t,
            **{f"spot__{a}": v for a, v in self.spot.items()},
            **{f"ref__{a}": v for a, v in self.reference.items()},
            **{f"{m.market_id}__{k}": getattr(m, k) for m in self.markets
               for k in ("yes_bid", "yes_ask", "bid_size", "ask_size")},
        )
        meta = {
            "source": self.source, "meta": self.meta,
            "markets": [{k: getattr(m, k) for k in
                         ("market_id", "asset", "kind", "label", "start", "end", "strike", "settle_price", "meta", "i0")}
                        for m in self.markets],
        }
        (folder / "tape.json").write_text(json.dumps(meta, indent=1, default=float))
        return folder

    @classmethod
    def load(cls, folder: str | Path) -> "Tape":
        folder = Path(folder)
        arr = np.load(folder / "tape.npz")
        meta = json.loads((folder / "tape.json").read_text())
        markets = [Market(yes_bid=arr[f"{m['market_id']}__yes_bid"], yes_ask=arr[f"{m['market_id']}__yes_ask"],
                          bid_size=arr[f"{m['market_id']}__bid_size"], ask_size=arr[f"{m['market_id']}__ask_size"],
                          **m) for m in meta["markets"]]
        spot = {k[6:]: arr[k] for k in arr.files if k.startswith("spot__")}
        ref = {k[5:]: arr[k] for k in arr.files if k.startswith("ref__")}
        return cls(arr["t"], spot, ref, markets, meta["source"], meta.get("meta", {}))

    @classmethod
    def from_recording(cls, ticks_csv: str | Path, books_csv: str | Path, markets_json: str | Path,
                       step: float = 0.25) -> "Tape":
        """Build a tape from the live recorder's CSV output, resampled onto a fixed grid.

        Books are forward-filled (last known quote) and go stale-to-NaN after 30 s with no update.
        """
        ticks = pd.read_csv(ticks_csv)
        books = pd.read_csv(books_csv)
        mkts = json.loads(Path(markets_json).read_text())
        t0 = float(min(ticks.ts.min(), books.ts.min() if len(books) else ticks.ts.min()))
        t1 = float(ticks.ts.max())
        grid = np.arange(0.0, t1 - t0, step)
        abs_grid = grid + t0

        def ffill(ts, vals, max_age=30.0):
            idx = np.searchsorted(ts, abs_grid, side="right") - 1
            out = np.where(idx >= 0, vals[np.clip(idx, 0, None)], np.nan)
            age = abs_grid - np.where(idx >= 0, ts[np.clip(idx, 0, None)], -np.inf)
            return np.where(age <= max_age, out, np.nan)

        # ticks.csv columns: ts, asset, price  (Binance trade/bookTicker mid)
        spot, ref = {}, {}
        for asset, tk in ticks.sort_values("ts").groupby("asset"):
            spot[asset] = ffill(tk.ts.values, tk.price.values, max_age=1e9)
            ref[asset] = spot[asset].copy()  # Binance used as the oracle proxy (see README: basis risk)

        markets = []
        for m in mkts:
            asset = m["asset"]
            if asset not in ref:
                continue
            b = books[books.market_id == m["market_id"]].sort_values("ts")
            if b.empty:
                continue
            ts = b.ts.values
            start, end = float(m["start"]) - t0, float(m["end"]) - t0
            strike = m.get("strike")
            if strike is None or (isinstance(strike, float) and np.isnan(strike)):
                if start < 0:          # window opened before we started recording: open price unknown
                    continue
                strike = float(ref[asset][min(np.searchsorted(grid, start), len(grid) - 1)])
            settle = float("nan")
            if end <= grid[-1]:
                settle = float(ref[asset][np.searchsorted(grid, end)])
            i0 = int(np.searchsorted(grid, max(start, 0.0)))
            i1 = int(min(np.searchsorted(grid, end) + 1, len(grid)))
            if i1 - i0 < 2:
                continue
            sl = slice(i0, i1)
            markets.append(Market(
                market_id=m["market_id"], asset=asset, kind=m["kind"], label=m["label"], start=start,
                end=end, strike=float(strike), settle_price=settle, meta=m.get("meta", {}), i0=i0,
                yes_bid=ffill(ts, b.yes_bid.values)[sl], yes_ask=ffill(ts, b.yes_ask.values)[sl],
                bid_size=ffill(ts, b.bid_size.values)[sl], ask_size=ffill(ts, b.ask_size.values)[sl],
            ))
        return cls(grid, spot, ref, markets, source="recorded", meta={"t0_unix": t0})
