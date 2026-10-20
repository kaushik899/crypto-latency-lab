"""Signal generation and paper execution with explicit latency.

For every live market the strategy computes a model fair probability from the exchange
spot, compares it with the prediction-market quotes, and flags a trade when the edge
*after the taker fee* clears a threshold. The order then "arrives" ``latency_ms`` later:
it fills only at the book that exists at that moment and only if the ask has not moved
beyond the limit. This is what turns a paper edge into a realistic one.
"""

from __future__ import annotations

import heapq
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .config import ASSETS
from .fees import CRYPTO_TAKER_RATE, TICK, fee_per_share, taker_fee
from .pricing import SECONDS_PER_YEAR, digital_prob, ewma_sigma_series
from .tape import Market, Tape


@dataclass
class StrategyParams:
    latency_ms: float = 150.0        # signal -> order at venue
    entry_edge: float = 0.015        # min expected profit per share after fees (USD)
    slippage_ticks: int = 1          # how far above the signal ask we accept a fill
    min_tau_s: float = 10.0          # don't trade the last seconds (oracle/settlement noise)
    min_px: float = 0.05             # skip lottery tickets: in the tails model and oracle risk dominate
    max_px: float = 0.95
    max_tau_s: float = 3600.0
    vol_halflife_s: float = 300.0
    dist: str = "normal"             # "normal" or "student" (fat-tailed)
    nu: float = 4.0
    fee_rate: float = CRYPTO_TAKER_RATE
    bankroll: float = 10_000.0       # fixed, not compounded; large enough that capital rarely binds
    kelly_fraction: float = 0.25
    max_stake: float = 50.0          # USD per order
    max_market_exposure: float = 150.0
    cooldown_s: float = 5.0
    model_haircut: float = 0.0       # shrink fair value toward 0.5 (robustness check)

    def to_dict(self):
        return asdict(self)


def fair_values(tape: Tape, m: Market, sigma: np.ndarray, p: StrategyParams) -> np.ndarray:
    """Model fair probability of YES over the market's window (aligned with its book arrays)."""
    t = tape.t[m.sl]
    tau_s = m.end - t
    live = (t >= m.start) & (tau_s > 0)
    fair = np.full(len(t), np.nan)
    spot = tape.spot[m.asset][m.sl]
    sig = sigma[m.sl]
    if live.any():
        fair[live] = digital_prob(spot[live], m.strike, tau_s[live] / SECONDS_PER_YEAR,
                                  sig[live], dist=p.dist, nu=p.nu)
    if p.model_haircut:
        fair = 0.5 + (1 - p.model_haircut) * (fair - 0.5)
    return fair


def sigma_by_asset(tape: Tape, p: StrategyParams) -> dict[str, np.ndarray]:
    out = {}
    for a, s in tape.spot.items():
        prior = ASSETS[a].sigma0 if a in ASSETS else 0.6
        out[a] = ewma_sigma_series(tape.t, s, halflife_s=p.vol_halflife_s, init_sigma=prior)
    return out


def _signals(tape: Tape, m: Market, sigma: np.ndarray, p: StrategyParams):
    fair = fair_values(tape, m, sigma, p)
    t = tape.t[m.sl]
    tau = m.end - t
    ok = (tau >= p.min_tau_s) & (tau <= p.max_tau_s) & ~np.isnan(fair)
    na = 1.0 - m.yes_bid                       # NO ask mirrors YES bid in Polymarket's single book
    with np.errstate(invalid="ignore"):
        e_yes = fair - m.yes_ask - fee_per_share(m.yes_ask, p.fee_rate)
        e_no = (1 - fair) - na - fee_per_share(na, p.fee_rate)
        ok_yes = ok & (m.yes_ask >= p.min_px) & (m.yes_ask <= p.max_px)
        ok_no = ok & (na >= p.min_px) & (na <= p.max_px)
        e_yes = np.where(ok_yes, e_yes, -np.inf)
        e_no = np.where(ok_no, e_no, -np.inf)
        sig = (e_yes > p.entry_edge) | (e_no > p.entry_edge)
    return dict(fair=fair, t=t, tau=tau, na=na, e_yes=e_yes, e_no=e_no, cand=np.flatnonzero(sig))


def run(tape: Tape, p: StrategyParams, sigmas: dict[str, np.ndarray] | None = None) -> pd.DataFrame:
    """Paper-trade every market on the tape, in global time order, under one capital budget.

    Capital committed to a position is locked until the market resolves, so the total
    open cost can never exceed ``bankroll``. Returns one row per attempted order.
    """
    sigmas = sigmas or sigma_by_asset(tape, p)
    lat_steps = int(round(p.latency_ms / 1000.0 / tape.dt))
    cool = p.cooldown_s
    S = [_signals(tape, m, sigmas[m.asset], p) for m in tape.markets]
    events = sorted((float(S[k]["t"][i]), k, int(i)) for k, s in enumerate(S) for i in s["cand"])

    rows, open_pos = [], []          # open_pos: heap of (t_end, cost)
    locked = 0.0
    exposure = np.zeros(len(tape.markets))
    next_ok = np.full(len(tape.markets), -np.inf)
    for t_sig, k, i in events:
        while open_pos and open_pos[0][0] <= t_sig:
            locked -= heapq.heappop(open_pos)[1]
        if t_sig < next_ok[k]:
            continue
        m, s = tape.markets[k], S[k]
        room = min(p.max_market_exposure - exposure[k], p.bankroll - locked)
        if room < 1.0:
            continue
        yes = s["e_yes"][i] >= s["e_no"][i]
        side = "YES" if yes else "NO"
        book = m.yes_ask if yes else s["na"]
        px_sig = float(book[i])
        q = float(s["fair"][i] if yes else 1 - s["fair"][i])
        edge = float(s["e_yes"][i] if yes else s["e_no"][i])
        kelly = max(0.0, edge / (1 - px_sig))
        stake = min(p.kelly_fraction * kelly * p.bankroll, p.max_stake, room)
        next_ok[k] = t_sig + cool
        row = dict(market_id=m.market_id, asset=m.asset, kind=m.kind, window=m.meta.get("window", ""),
                   label=m.label, side=side, t_signal=t_sig, tau_s=float(s["tau"][i]), fair=q,
                   px_signal=px_sig, edge_signal=edge, sigma=float(sigmas[m.asset][m.i0 + i]))
        j = i + lat_steps
        if j >= m.n or s["t"][j] >= m.end:
            rows.append({**row, "filled": False, "reason": "expired"}); continue
        px_arr = float(book[j])
        size_arr = (m.ask_size if yes else m.bid_size)[j]
        if np.isnan(px_arr) or px_arr > px_sig + p.slippage_ticks * TICK + 1e-9 or not size_arr > 0:
            rows.append({**row, "filled": False, "reason": "moved", "px_arrival": px_arr}); continue
        shares = float(np.floor(min(stake / px_arr, size_arr)))
        if shares < 1:
            rows.append({**row, "filled": False, "reason": "size"}); continue
        fee = taker_fee(shares, px_arr, p.fee_rate)
        out = m.outcome if yes else 1 - m.outcome
        pnl = shares * (out - px_arr) - fee if not np.isnan(out) else np.nan
        cost = shares * px_arr
        exposure[k] += cost
        locked += cost
        heapq.heappush(open_pos, (m.end, cost))
        rows.append({**row, "filled": True, "reason": "", "px_arrival": px_arr, "shares": shares,
                     "cost": cost, "fee": fee, "outcome": out, "pnl": pnl, "t_end": m.end,
                     "edge_arrival": (q - px_arr - fee / shares)})
    return pd.DataFrame(rows)


def gap_lifetimes(tape: Tape, p: StrategyParams, sigmas=None) -> pd.DataFrame:
    """Every contiguous stretch where a fee-clearing edge was on the screen, and how long it lasted."""
    sigmas = sigmas or sigma_by_asset(tape, p)
    out = []
    for m in tape.markets:
        fair = fair_values(tape, m, sigmas[m.asset], p)
        t = tape.t[m.sl]
        tau = m.end - t
        ok = (tau >= p.min_tau_s) & ~np.isnan(fair)
        e = np.fmax(fair - m.yes_ask - fee_per_share(m.yes_ask, p.fee_rate),
                    (1 - fair) - (1 - m.yes_bid) - fee_per_share(1 - m.yes_bid, p.fee_rate))
        on = ok & (e > p.entry_edge)
        if not on.any():
            continue
        d = np.diff(np.r_[0, on.astype(int), 0])
        starts, ends = np.flatnonzero(d == 1), np.flatnonzero(d == -1)
        for s, e_ in zip(starts, ends):
            out.append(dict(market_id=m.market_id, asset=m.asset, t=float(t[s]),
                            duration_s=float((e_ - s) * tape.dt), peak_edge=float(np.nanmax(e[s:e_]))))
    return pd.DataFrame(out)
