"""Performance and diagnostics for a set of paper trades."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def summarize(trades: pd.DataFrame, bankroll: float, hours: float) -> dict:
    if trades.empty:
        return dict(signals=0, fills=0, fill_rate=0.0, pnl=0.0)
    f = trades[trades.filled & trades.pnl.notna()].copy()
    out = dict(
        signals=int(len(trades)),
        fills=int(len(f)),
        fill_rate=float(len(f) / len(trades)),
        missed_moved=float((trades.reason == "moved").mean()),
        pnl=float(f.pnl.sum()),
        fees=float(f.fee.sum()) if len(f) else 0.0,
        volume=float(f.cost.sum()) if len(f) else 0.0,
        hit_rate=float((f.pnl > 0).mean()) if len(f) else 0.0,
        pnl_per_trade=float(f.pnl.mean()) if len(f) else 0.0,
        edge_signal=float(f.edge_signal.mean()) if len(f) else 0.0,
        edge_arrival=float(f.edge_arrival.mean()) if len(f) else 0.0,
        return_pct=float(f.pnl.sum() / bankroll * 100),
        hours=hours,
    )
    if len(f):
        f = f.sort_values("t_end")
        eq = f.pnl.cumsum().values
        out["max_drawdown"] = float(np.max(np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]))
        hourly = f.groupby((f.t_end // 3600).astype(int)).pnl.sum().reindex(range(int(math.ceil(hours))), fill_value=0)
        sd = hourly.std(ddof=1)
        out["sharpe_annual"] = float(hourly.mean() / sd * math.sqrt(24 * 365)) if sd > 0 else None
        # t-stat on per-trade pnl: is the average different from zero?
        out["t_stat"] = float(f.pnl.mean() / (f.pnl.std(ddof=1) / math.sqrt(len(f)))) if len(f) > 2 else None
        out["brier"] = float(((f.fair - f.outcome) ** 2).mean())
    return out


def calibration(trades: pd.DataFrame, bins: int = 10) -> list[dict]:
    """Reliability of the model's fair probability at trade time vs realised outcomes."""
    f = trades[trades.filled & trades.outcome.notna()]
    if f.empty:
        return []
    edges = np.linspace(0, 1, bins + 1)
    g = f.groupby(pd.cut(f.fair, edges, include_lowest=True), observed=True)
    return [dict(p=float(iv.mid), predicted=float(d.fair.mean()), realised=float(d.outcome.mean()), n=int(len(d)))
            for iv, d in g]


def equity_curve(trades: pd.DataFrame, points: int = 400) -> list[dict]:
    f = trades[trades.filled & trades.pnl.notna()].sort_values("t_end")
    if f.empty:
        return []
    eq = f.pnl.cumsum()
    idx = np.unique(np.linspace(0, len(f) - 1, min(points, len(f))).astype(int))
    return [dict(t=float(f.t_end.iloc[i]), equity=float(eq.iloc[i])) for i in idx]
