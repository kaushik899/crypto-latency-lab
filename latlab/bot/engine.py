"""The auto-trader's engine. Same code for live trading and for replaying recorded data.

Flow per quote:
  1. update market state
  2. resolve due outcomes -> teach each strategy's model what really happened
  3. strategies emit candidates -> calibrated prediction -> must beat fees + spread + buffer
  4. risk checks -> pick a broker: real only if the strategy's paper record has opened its gate
  5. manage open positions: take profit, stop loss, or time exit
"""

from __future__ import annotations

import itertools
import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field

from ..feeds.exchanges import TAKER_BPS, Quote
from .brokers import Fill, Order, PaperBroker
from .ledger import Gate, Ledger
from .models import OnlineBeta
from .risk import RiskLimits, RiskManager
from .state import MarketState
from .strategies import Strategy, default_strategies


@dataclass
class Position:
    id: int
    strategy: str
    venue: str
    asset: str
    broker: str
    predicted_bps: float
    cost_bps: float
    reason: str
    opened_at: float
    exit_due: float
    usdt: float
    status: str = "opening"          # opening -> open -> closing -> closed
    qty: float = 0.0
    entry_px: float = 0.0
    entry_fee: float = 0.0
    take_px: float = 0.0
    stop_px: float = 0.0
    exit_reason: str = ""


@dataclass
class Engine:
    strategies: list[Strategy] = field(default_factory=default_strategies)
    fees_bps: dict = field(default_factory=lambda: dict(TAKER_BPS))
    limits: RiskLimits = field(default_factory=RiskLimits)
    ledger: Ledger = field(default_factory=Ledger)
    paper_latency_s: float = 0.15
    real_brokers: dict = field(default_factory=dict)   # venue -> CcxtBroker (testnet or live)
    allow_real: bool = False                           # user's switch: route gated strategies to real brokers
    trade_venues: set | None = None                    # restrict where trading may happen (None = everywhere)
    label_delay_s: float | None = None                 # order arrival delay used when learning (default: paper latency)

    def __post_init__(self):
        if self.label_delay_s is None:
            self.label_delay_s = self.paper_latency_s
        self.state = MarketState()
        self.paper = PaperBroker(self.state, self.fees_bps, self.paper_latency_s)
        self.risk = RiskManager(self.limits)
        self.gate = Gate(self.ledger)
        self.models: dict[tuple[str, str, str], OnlineBeta] = {}
        self.pending_labels: dict[tuple[str, str], deque] = defaultdict(deque)
        self.positions: dict[int, Position] = {}
        self._ids = itertools.count(1)
        self.counts = defaultdict(int)          # observations, signals, blocked, ...
        self.blocked: dict[str, int] = defaultdict(int)
        self.events: deque = deque(maxlen=60)
        self.started = None
        self._gate_cache: dict[str, tuple[float, dict]] = {}

    # ------------------------------------------------------------------ helpers
    def model(self, strat: str, venue: str, asset: str) -> OnlineBeta:
        k = (strat, venue, asset)
        if k not in self.models:
            self.models[k] = OnlineBeta()
        return self.models[k]

    def gate_status(self, strat: str, t: float) -> dict:
        c = self._gate_cache.get(strat)
        if c is None or t - c[0] > 30:
            c = (t, self.gate.status(strat, t))
            self._gate_cache[strat] = c
        return c[1]

    def log(self, t, kind, msg):
        self.events.appendleft({"t": t, "kind": kind, "msg": msg})

    # ------------------------------------------------------------------ main entry points
    def on_quote(self, q: Quote) -> None:
        t = q.recv_ts
        if self.started is None:
            self.started = t
        self.state.update(q)
        self._resolve_labels(q.venue, q.asset, t)
        for s in self.strategies:
            for c in s.observe(self.state, q):
                self._candidate(s, c, t)
        self._manage(q, t)
        self.paper.tick(t)

    def tick(self, t: float) -> None:
        """Advance the clock without a quote (paper fills, time exits)."""
        self.state.now = max(self.state.now, t)
        self.paper.tick(t)
        for p in list(self.positions.values()):
            if p.status == "open" and t >= p.exit_due:
                self._close(p, t, "time")

    # ------------------------------------------------------------------ learning
    def _resolve_labels(self, venue, asset, t):
        dq = self.pending_labels.get((venue, asset))
        if not dq:
            return
        now = self.state.adj_logmid(venue, asset)
        while dq and dq[0][0] <= t:
            _, strat, x, t_obs = dq.popleft()
            # learn the move an order could actually catch: from when it would arrive, not from the signal
            x0 = self.state.adj_logmid_at(venue, asset, t_obs + self.label_delay_s)
            if now is not None and x0 is not None:
                self.model(strat, venue, asset).update(x, (now - x0) * 1e4)
                self.counts["labels"] += 1

    def _candidate(self, s: Strategy, c, t):
        self.counts["observations"] += 1
        if self.state.adj_logmid(c.venue, c.asset) is None:
            return
        self.pending_labels[(c.venue, c.asset)].append((t + c.horizon_s, s.name, c.x, t))
        m = self.model(s.name, c.venue, c.asset)
        pred = m.predict_low(c.x)       # trade only on what the model is confident about
        if pred is None or pred <= 0:
            return
        q = self.state.quote(c.venue, c.asset)
        if q is None:
            return
        cost = 2 * self.fees_bps.get(c.venue, 10) + q.spread_bps
        if pred < cost + self.limits.min_edge_buffer_bps:
            self.counts["below_cost"] += 1
            return
        self.counts["signals"] += 1
        if self.trade_venues is not None and c.venue not in self.trade_venues:
            self.blocked["venue not enabled"] += 1
            return
        broker = self.paper
        real = self.real_brokers.get(c.venue)
        if real is not None and self.allow_real and self.gate_status(s.name, t)["open"]:
            broker = real
        open_pos = [p for p in self.positions.values() if p.status != "closed"]
        why = self.risk.check(t, c.asset, open_pos, q, live=broker is not self.paper)
        if why:
            self.blocked[why.split(" (")[0]] += 1
            return
        self._open(s.name, c, pred, cost, q, broker, t)

    # ------------------------------------------------------------------ trading
    def _open(self, strat, c, pred, cost, q, broker, t):
        pid = next(self._ids)
        p = Position(pid, strat, c.venue, c.asset, broker.kind, pred, cost, c.note, t, t + c.horizon_s,
                     self.limits.order_usdt)
        self.positions[pid] = p
        self.risk.note_trade(t)
        self.log(t, "open", f"{strat}: buy {p.usdt:g} USDT of {c.asset} on {c.venue} ({broker.kind}), "
                            f"expects +{pred:.1f} bp vs {cost:.1f} bp cost — {c.note}")
        broker.submit(Order(next(self._ids), c.venue, c.asset, "BUY", usdt=p.usdt, sent_at=t, pos_id=pid),
                      t, lambda f: self._on_entry(p, f, broker))

    def _on_entry(self, p: Position, f: Fill, broker):
        if not f.ok or f.qty <= 0:
            p.status = "closed"
            self.positions.pop(p.id, None)
            self.log(f.at, "error", f"{p.strategy}: entry on {p.venue} failed: {f.error}")
            return
        p.status, p.qty, p.entry_px, p.entry_fee = "open", f.qty, f.price, f.fee_usdt
        # hold for the horizon the model was trained on (no profit cap, which would bias results down);
        # a wide protective stop guards against a broken feed or a crash
        p.take_px = float("inf")
        p.stop_px = f.price * (1 - max(3 * p.predicted_bps, 30.0) / 1e4)
        p._broker = broker

    def _manage(self, q: Quote, t: float):
        for p in list(self.positions.values()):
            if p.status != "open" or p.venue != q.venue or p.asset != q.asset:
                continue
            if q.bid >= p.take_px:
                self._close(p, t, "target")
            elif q.bid <= p.stop_px:
                self._close(p, t, "stop")
            elif t >= p.exit_due:
                self._close(p, t, "time")

    def _close(self, p: Position, t: float, why: str):
        p.status, p.exit_reason = "closing", why
        broker = getattr(p, "_broker", self.paper)
        broker.submit(Order(next(self._ids), p.venue, p.asset, "SELL", qty=p.qty, sent_at=t, pos_id=p.id),
                      t, lambda f: self._on_exit(p, f))

    def _on_exit(self, p: Position, f: Fill):
        if not f.ok:
            p.status = "open"  # try again on the next quote
            p.exit_due = f.at + 2
            self.log(f.at, "error", f"{p.strategy}: exit on {p.venue} failed: {f.error}")
            return
        p.status = "closed"
        self.positions.pop(p.id, None)
        gross = p.qty * (f.price - p.entry_px)
        pnl = gross - p.entry_fee - f.fee_usdt
        notional = p.qty * p.entry_px
        bps = pnl / notional * 1e4 if notional else 0.0
        self.ledger.add(mode=p.broker, strategy=p.strategy, venue=p.venue, asset=p.asset, opened_at=p.opened_at,
                        closed_at=f.at, notional=notional, qty=p.qty, entry_px=p.entry_px, exit_px=f.price,
                        fees=p.entry_fee + f.fee_usdt, pnl=pnl, pnl_bps=bps, predicted_bps=p.predicted_bps,
                        cost_bps=p.cost_bps, reason=p.reason, exit_reason=p.exit_reason)
        if p.broker != "paper":
            self.risk.record_close(f.at, pnl)
        self.counts[f"closed_{p.broker}"] += 1
        self.log(f.at, "close", f"{p.strategy}: sold {p.asset} on {p.venue} ({p.broker}, {p.exit_reason}) "
                                f"{'+' if pnl >= 0 else '−'}{abs(pnl):.3f} USDT ({bps:+.1f} bp)")

    # ------------------------------------------------------------------ reporting
    def snapshot(self, t: float | None = None, top_models: int = 8) -> dict:
        t = t or self.state.now or time.time()
        strat = []
        for s in self.strategies:
            ms = [(k, m) for k, m in self.models.items() if k[0] == s.name]
            ready = [(k, m) for k, m in ms if m.ready]
            best = sorted(ms, key=lambda km: -km[1].t_stat)[:top_models]
            strat.append({
                "name": s.name, "description": s.description,
                "markets": len(ms), "ready": len(ready), "observations": sum(m.count for _, m in ms),
                "best": [{"venue": k[1], "asset": k[2], **m.summary()} for k, m in best],
                "paper": self.ledger.stats("paper", s.name),
                "testnet": self.ledger.stats("testnet", s.name),
                "live": self.ledger.stats("live", s.name),
                "gate": self.gate_status(s.name, t),
            })
        venues = defaultdict(set)
        for (v, a) in self.state.quotes:
            venues[v].add(a)
        return {
            "t": t, "uptime_s": (t - self.started) if self.started else 0,
            "markets": {v: len(a) for v, a in venues.items()}, "quotes": self.state.n_quotes,
            "counts": dict(self.counts), "blocked": dict(self.blocked),
            "strategies": strat, "risk": self.risk.status(),
            "open": [{k: getattr(p, k) for k in ("strategy", "venue", "asset", "broker", "status", "usdt",
                                                  "entry_px", "predicted_bps", "cost_bps", "opened_at")}
                     for p in self.positions.values()],
            "recent": self.ledger.recent(20), "events": list(self.events)[:30],
            "fees_bps": self.fees_bps, "allow_real": self.allow_real,
            "real_venues": {v: b.kind for v, b in self.real_brokers.items()},
        }
