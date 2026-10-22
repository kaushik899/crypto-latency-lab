"""Risk limits. Every order passes through here; any failed check blocks it and says why."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

KILL_FILE = Path.home() / ".latlab" / "STOP"


@dataclass
class RiskLimits:
    order_usdt: float = 20.0          # size of each trade
    max_open: int = 3                 # positions open at once
    max_per_coin: int = 1
    max_daily_loss_usdt: float = 10.0 # halts trading for the rest of the UTC day
    max_trades_per_hour: int = 30
    max_spread_bps: float = 15.0
    max_quote_age_s: float = 1.5
    min_edge_buffer_bps: float = 2.0  # extra margin over fees + spread before a trade is allowed

    def to_dict(self):
        return asdict(self)


@dataclass
class RiskManager:
    limits: RiskLimits = field(default_factory=RiskLimits)
    halted: str = ""
    day: str = ""
    day_pnl: float = 0.0
    recent: deque = field(default_factory=deque)

    def _roll_day(self, t: float):
        d = time.strftime("%Y-%m-%d", time.gmtime(t))
        if d != self.day:
            self.day, self.day_pnl = d, 0.0
            if self.halted.startswith("Daily loss"):
                self.halted = ""

    def record_close(self, t: float, pnl: float):
        self._roll_day(t)
        self.day_pnl += pnl
        if self.day_pnl <= -self.limits.max_daily_loss_usdt:
            self.halted = f"Daily loss limit reached ({self.day_pnl:.2f} USDT). Trading resumes tomorrow (UTC)."

    def kill(self, why: str = "Stopped by kill switch."):
        self.halted = why

    def check(self, t: float, asset: str, open_positions: list, quote, live: bool) -> str | None:
        """None if the order may go ahead, otherwise the reason it is blocked."""
        self._roll_day(t)
        if live and KILL_FILE.exists():
            self.halted = f"Kill switch file present ({KILL_FILE}). Delete it to resume."
        elif self.halted.startswith("Kill switch file") and not KILL_FILE.exists():
            self.halted = ""   # resumed
        if self.halted:
            return self.halted
        L = self.limits
        if len(open_positions) >= L.max_open:
            return "Too many open positions"
        if sum(1 for p in open_positions if p.asset == asset) >= L.max_per_coin:
            return f"Already holding {asset}"
        while self.recent and t - self.recent[0] > 3600:
            self.recent.popleft()
        if len(self.recent) >= L.max_trades_per_hour:
            return "Hourly trade limit"
        if quote is None or t - quote.recv_ts > L.max_quote_age_s:
            return "Price data is stale"
        if quote.spread_bps > L.max_spread_bps:
            return f"Spread too wide ({quote.spread_bps:.1f} bp)"
        return None

    def note_trade(self, t: float):
        self.recent.append(t)

    def status(self) -> dict:
        return {"halted": self.halted, "day": self.day, "day_pnl": round(self.day_pnl, 4), "limits": self.limits.to_dict()}
