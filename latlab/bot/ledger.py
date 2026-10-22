"""Every closed trade, in SQLite. The performance gate reads its paper history from here."""

from __future__ import annotations

import math
import sqlite3
import time
from pathlib import Path

SCHEMA = """
create table if not exists trades (
  id integer primary key autoincrement,
  mode text, strategy text, venue text, asset text,
  opened_at real, closed_at real, notional real, qty real,
  entry_px real, exit_px real, fees real, pnl real, pnl_bps real,
  predicted_bps real, cost_bps real, reason text, exit_reason text
);
create index if not exists trades_mode_strategy on trades(mode, strategy, closed_at);
"""


class Ledger:
    def __init__(self, path: str | Path = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript(SCHEMA)

    def add(self, **row) -> None:
        cols = ",".join(row)
        self.db.execute(f"insert into trades ({cols}) values ({','.join('?' * len(row))})", list(row.values()))
        self.db.commit()

    def stats(self, mode: str | None = None, strategy: str | None = None, since: float | None = None) -> dict:
        q, args = "select pnl, pnl_bps from trades where 1=1", []
        if mode:
            q += " and mode = ?"; args.append(mode)
        if strategy:
            q += " and strategy = ?"; args.append(strategy)
        if since:
            q += " and closed_at >= ?"; args.append(since)
        rows = self.db.execute(q, args).fetchall()
        n = len(rows)
        if not n:
            return {"n": 0, "pnl": 0.0, "mean_bps": None, "t": None, "win_rate": None}
        bps = [r[1] for r in rows]
        mean = sum(bps) / n
        sd = math.sqrt(sum((b - mean) ** 2 for b in bps) / (n - 1)) if n > 1 else 0.0
        return {"n": n, "pnl": round(sum(r[0] for r in rows), 4), "mean_bps": round(mean, 3),
                "t": round(mean / (sd / math.sqrt(n)), 2) if sd > 0 else None,
                "win_rate": round(sum(1 for b in bps if b > 0) / n, 4)}

    def recent(self, limit: int = 25, mode: str | None = None) -> list[dict]:
        q = "select * from trades" + (" where mode = ?" if mode else "") + " order by closed_at desc limit ?"
        cur = self.db.execute(q, ([mode] if mode else []) + [limit])
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


class Gate:
    """A strategy may route real orders only after its paper record clears costs with confidence."""

    def __init__(self, ledger: Ledger, min_trades: int = 100, min_t: float = 2.0, window_days: float = 7.0):
        self.ledger, self.min_trades, self.min_t, self.window = ledger, min_trades, min_t, window_days

    def status(self, strategy: str, now: float | None = None) -> dict:
        now = now or time.time()
        s = self.ledger.stats("paper", strategy, now - self.window * 86400)
        if s["n"] < self.min_trades:
            why = f"needs {self.min_trades - s['n']} more paper trades"
        elif s["mean_bps"] is None or s["mean_bps"] <= 0:
            why = "paper trades lose money after fees"
        elif s["t"] is None or s["t"] < self.min_t:
            why = f"profit not yet statistically clear (t = {s['t']}, needs {self.min_t})"
        else:
            why = ""
        return {"open": not why, "why": why or "paper record beats costs", **s}
