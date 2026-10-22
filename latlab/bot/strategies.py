"""Strategies: each one turns the market state into candidate observations ``(venue, coin, x, horizon)``.

They never decide to trade on their own. The engine learns, per strategy and coin, how much ``x``
actually predicts the next ``horizon`` seconds (see models.py) and trades only when that calibrated
prediction beats the full cost of a round trip: two taker fees plus the bid-ask spread.

All strategies are long-only, because spot accounts cannot short.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ..feeds.exchanges import Quote
from .state import Ewma, MarketState


@dataclass
class Candidate:
    venue: str
    asset: str
    x: float
    horizon_s: float
    note: str = ""


class Strategy:
    name = "base"
    description = ""
    sample_every_s = 1.0

    def __init__(self):
        self._last: dict[tuple[str, str], float] = {}

    def due(self, key, t: float) -> bool:
        if t - self._last.get(key, -1e18) >= self.sample_every_s:
            self._last[key] = t
            return True
        return False

    def observe(self, st: MarketState, q: Quote) -> list[Candidate]:
        raise NotImplementedError


class LeadLag(Strategy):
    """When Binance moves and another exchange has not caught up yet, buy on the slower exchange."""

    name = "leadlag"
    description = "Buys on a slower exchange after Binance moves up and before it catches up."
    sample_every_s = 0.25

    def __init__(self, window_s: float = 1.0, horizon_s: float = 2.0, venues=("coinbase", "kraken", "okx", "bybit")):
        super().__init__()
        self.window_s, self.horizon_s, self.venues = window_s, horizon_s, venues

    def observe(self, st, q):
        if q.venue != "binance" or not self.due(q.asset, q.recv_ts):
            return []
        t = q.recv_ts
        b_now, b_then = st.adj_logmid("binance", q.asset), st.adj_logmid_at("binance", q.asset, t - self.window_s)
        if b_now is None or b_then is None:
            return []
        lead = (b_now - b_then) * 1e4
        out = []
        for v in self.venues:
            if st.fresh(v, q.asset, 1.5) is None:
                continue
            v_now, v_then = st.adj_logmid(v, q.asset), st.adj_logmid_at(v, q.asset, t - self.window_s)
            if v_now is None or v_then is None:
                continue
            gap = lead - (v_now - v_then) * 1e4     # move Binance made that this venue has not made yet
            out.append(Candidate(v, q.asset, gap, self.horizon_s, f"Binance {lead:+.1f} bp, {v} behind by {gap:+.1f} bp"))
        return out


class BookImbalance(Strategy):
    """More size on the bid than the ask tends to precede small up-moves."""

    name = "imbalance"
    description = "Buys when the best bid holds much more size than the best ask."
    sample_every_s = 1.0

    def __init__(self, horizon_s: float = 10.0, venue: str = "binance"):
        super().__init__()
        self.horizon_s, self.venue = horizon_s, venue

    def observe(self, st, q):
        if q.venue != self.venue or not q.bid_qty or not q.ask_qty or not self.due(q.asset, q.recv_ts):
            return []
        bv, av = q.bid_qty * q.bid, q.ask_qty * q.ask
        imb = (bv - av) / (bv + av)
        # scale by the spread: the same imbalance moves a wide market further
        x = imb * max(q.spread_bps, 0.1)
        return [Candidate(self.venue, q.asset, x, self.horizon_s, f"imbalance {imb:+.2f}, spread {q.spread_bps:.1f} bp")]


class Reversion(Strategy):
    """Sharp drops below the recent average tend to partly bounce back."""

    name = "reversion"
    description = "Buys sharp dips below the 5-minute average, expecting a partial bounce."
    sample_every_s = 2.0

    def __init__(self, horizon_s: float = 30.0, lookback_s: float = 300.0, venue: str = "binance"):
        super().__init__()
        self.horizon_s, self.venue = horizon_s, venue
        self.lookback_s = lookback_s
        self.avg: dict[str, Ewma] = {}

    def observe(self, st, q):
        if q.venue != self.venue:
            return []
        x = math.log(q.mid)
        e = self.avg.setdefault(q.asset, Ewma(self.lookback_s))
        e.update(q.recv_ts, x)
        if e.mean is None or not self.due(q.asset, q.recv_ts):
            return []
        dip = (e.mean - x) * 1e4                       # bps below the average (positive = below)
        if dip <= 0:
            return []
        return [Candidate(self.venue, q.asset, dip, self.horizon_s, f"{dip:.1f} bp below 5-min average")]


def default_strategies() -> list[Strategy]:
    return [LeadLag(), BookImbalance(), Reversion()]
