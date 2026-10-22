"""Live market state shared by every strategy: latest quote per (venue, coin), short price histories,
and each venue's rolling price difference ("basis") to Binance, so USD and USDT markets compare fairly."""

from __future__ import annotations

import math
from bisect import bisect_right
from collections import deque

from ..feeds.exchanges import Quote


class MarketState:
    def __init__(self, keep_s: float = 600.0, basis_halflife_s: float = 30.0):
        self.keep_s = keep_s
        self.hl = basis_halflife_s
        self.quotes: dict[tuple[str, str], Quote] = {}
        self.hist: dict[tuple[str, str], tuple[deque, deque]] = {}  # (times, adjusted log mids)
        self.basis: dict[tuple[str, str], float] = {}
        self._basis_t: dict[tuple[str, str], float] = {}
        self.n_quotes = 0
        self.now = 0.0

    # ------------------------------------------------------------------ updates
    def update(self, q: Quote) -> None:
        k = (q.venue, q.asset)
        self.quotes[k] = q
        self.now = max(self.now, q.recv_ts)
        self.n_quotes += 1
        x = math.log(q.mid)
        if q.venue != "binance":
            b = self.quotes.get(("binance", q.asset))
            if b is not None and abs(q.recv_ts - b.recv_ts) < 2.0:
                d = x - math.log(b.mid)
                last = self._basis_t.get(k)
                if last is None:
                    self.basis[k] = d
                else:
                    w = 1 - 0.5 ** (max(q.recv_ts - last, 0.0) / self.hl)
                    self.basis[k] = (1 - w) * self.basis[k] + w * d
                self._basis_t[k] = q.recv_ts
            if k not in self.basis:
                return  # can't compare to Binance yet
            x -= self.basis[k]
        ts, xs = self.hist.setdefault(k, (deque(), deque()))
        ts.append(q.recv_ts)
        xs.append(x)
        while ts and q.recv_ts - ts[0] > self.keep_s:
            ts.popleft()
            xs.popleft()

    # ------------------------------------------------------------------ reads
    def quote(self, venue: str, asset: str) -> Quote | None:
        return self.quotes.get((venue, asset))

    def fresh(self, venue: str, asset: str, max_age: float = 2.0) -> Quote | None:
        q = self.quotes.get((venue, asset))
        return q if q is not None and self.now - q.recv_ts <= max_age else None

    def adj_logmid(self, venue: str, asset: str) -> float | None:
        h = self.hist.get((venue, asset))
        return h[1][-1] if h and h[1] else None

    def adj_logmid_at(self, venue: str, asset: str, t: float) -> float | None:
        """Adjusted log mid as it stood at time ``t`` (last value at or before t)."""
        h = self.hist.get((venue, asset))
        if not h or not h[0]:
            return None
        i = bisect_right(h[0], t) - 1
        return h[1][i] if i >= 0 else None

    def assets(self, venue: str) -> list[str]:
        return [a for (v, a) in self.quotes if v == venue]


class Ewma:
    """Exponentially weighted mean/variance on an irregular clock."""

    def __init__(self, halflife_s: float):
        self.hl = halflife_s
        self.mean: float | None = None
        self.var = 0.0
        self.t: float | None = None

    def update(self, t: float, x: float) -> None:
        if self.mean is None:
            self.mean, self.t = x, t
            return
        w = 1 - 0.5 ** (max(t - self.t, 0.0) / self.hl)
        d = x - self.mean
        self.mean += w * d
        self.var = (1 - w) * (self.var + w * d * d)
        self.t = t

    @property
    def sd(self) -> float:
        return math.sqrt(self.var)
