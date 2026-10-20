"""Synthetic multi-asset world for testing the strategy end to end.

This is a *model* of the market, not data. It exists so the engine, backtester and
dashboard can be exercised anywhere, and so we can ask "how does the edge depend on
latency, fees and competition?" under explicit assumptions. Every assumption is a
parameter in ``SimConfig`` and is exported alongside the results.

Pieces:
* Correlated GBM spot prices with stochastic volatility and Poisson jumps (Binance).
* A reference/oracle price = spot plus a small mean-reverting basis (Chainlink).
* A market maker per prediction market that re-quotes the digital fair value on a
  random refresh clock using a slightly stale view of spot (the source of latency gaps).
* Competing latency traders: when the stale ask is mispriced by more than their
  threshold, one of them lifts it after its own latency and forces the maker to re-quote.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np
from scipy.signal import lfilter

from .config import ASSETS, WINDOWS
from .fees import TICK
from .pricing import SECONDS_PER_YEAR, digital_prob_jump
from .tape import Market, Tape

CORR = {  # rough daily-return correlations among majors
    ("BTC", "ETH"): 0.82, ("BTC", "SOL"): 0.75, ("BTC", "XRP"): 0.65, ("BTC", "BNB"): 0.70,
    ("BTC", "DOGE"): 0.68, ("ETH", "SOL"): 0.75, ("ETH", "XRP"): 0.62, ("ETH", "BNB"): 0.68,
    ("ETH", "DOGE"): 0.65, ("SOL", "XRP"): 0.60, ("SOL", "BNB"): 0.62, ("SOL", "DOGE"): 0.64,
    ("XRP", "BNB"): 0.58, ("XRP", "DOGE"): 0.62, ("BNB", "DOGE"): 0.58,
}
START_PX = {"BTC": 112_000.0, "ETH": 4_100.0, "SOL": 195.0, "XRP": 2.6, "BNB": 1_050.0, "DOGE": 0.21}


@dataclass
class SimConfig:
    hours: float = 6.0
    dt: float = 0.05                 # 50 ms grid so latencies down to 50 ms are resolved
    seed: int = 7
    assets: tuple = ("BTC", "ETH", "SOL", "XRP", "BNB", "DOGE")
    windows: tuple = ("5m", "15m", "1h")
    # volatility dynamics
    vol_of_vol: float = 1.2          # annualised vol of log-vol (OU)
    vol_mean_rev_h: float = 2.0      # mean-reversion half-life of log-vol, hours
    jumps_per_day: float = 8.0
    jump_sd: float = 0.0035
    basis_sd: float = 0.00015        # oracle vs exchange basis (1.5 bp)
    basis_halflife_s: float = 20.0
    # market maker
    mm_refresh_mean_s: float = 1.5   # average time between re-quotes
    mm_data_lag_s: float = 0.30      # maker's own feed delay
    mm_half_spread: float = 0.01
    mm_depth_median: float = 250.0   # shares at best
    fee_rate: float = 0.07          # venue taker fee rate (competitors price it in too)
    # competition
    competitors: bool = True
    comp_threshold: float = 0.025    # mispricing (fee-inclusive) that triggers a competitor
    comp_latency_median_ms: float = 120.0
    comp_latency_sigma: float = 0.6  # lognormal shape

    def to_dict(self):
        return asdict(self)


def _corr_matrix(assets):
    n = len(assets)
    c = np.eye(n)
    for i, a in enumerate(assets):
        for j, b in enumerate(assets):
            if i != j:
                c[i, j] = CORR.get((a, b), CORR.get((b, a), 0.6))
    return c


def simulate_prices(cfg: SimConfig, rng: np.random.Generator):
    n = int(cfg.hours * 3600 / cfg.dt) + 1
    t = np.arange(n) * cfg.dt
    A = list(cfg.assets)
    L = np.linalg.cholesky(_corr_matrix(A))
    z = rng.standard_normal((n - 1, len(A))) @ L.T
    dt_y = cfg.dt / SECONDS_PER_YEAR
    # shared + idiosyncratic stochastic vol (OU on log-vol)
    k = np.log(2) / (cfg.vol_mean_rev_h * 3600)
    shocks = rng.standard_normal((n - 1, len(A))) * 0.5 + rng.standard_normal((n - 1, 1)) * 0.85
    lv = np.vstack([np.zeros(len(A)),
                    lfilter([1.0], [1.0, -(1 - k * cfg.dt)], cfg.vol_of_vol * np.sqrt(dt_y) * shocks, axis=0)])
    sig = np.array([ASSETS[a].sigma0 for a in A]) * np.exp(lv)
    # jumps: common arrival, asset-scaled size
    lam = cfg.jumps_per_day / 86400 * cfg.dt
    jump_on = rng.random(n - 1) < lam
    jsize = rng.standard_normal((n - 1, 1)) * cfg.jump_sd * (sig[1:] / sig[1:].mean(axis=1, keepdims=True))
    r = -0.5 * sig[1:] ** 2 * dt_y + sig[1:] * np.sqrt(dt_y) * z + jump_on[:, None] * jsize
    logp = np.vstack([np.zeros(len(A)), np.cumsum(r, axis=0)])
    spot = {a: START_PX[a] * np.exp(logp[:, i]) for i, a in enumerate(A)}
    # oracle basis: AR(1)
    phi = 0.5 ** (cfg.dt / cfg.basis_halflife_s)
    ref = {}
    for a in A:
        e = rng.standard_normal(n) * cfg.basis_sd * np.sqrt(1 - phi**2)
        b = lfilter([1.0], [1.0, -phi], e)
        ref[a] = spot[a] * (1 + b)
    # the maker knows the true diffusive vol and the jump process, and prices with the jump-diffusion digital
    scale = sig / sig.mean(axis=1, keepdims=True)
    true_sig = {a: sig[:, i] for i, a in enumerate(A)}
    jump_sd = {a: cfg.jump_sd * scale[:, i] for i, a in enumerate(A)}
    return t, spot, ref, (true_sig, jump_sd)


def _round_bid(x):
    return np.clip(np.floor(x / TICK + 1e-9) * TICK, TICK, 1 - TICK)


def _round_ask(x):
    return np.clip(np.ceil(x / TICK - 1e-9) * TICK, TICK, 1 - TICK)


def simulate_market(t, spot, ref, sig, jsd, start, end, cfg: SimConfig, rng) -> dict:
    """Quotes for one Up/Down window, with maker staleness and competitor sniping."""
    dt = cfg.dt
    i0 = int(round(start / dt))
    i1 = min(int(round(end / dt)) + 1, len(t))
    strike = ref[i0]
    n = i1 - i0
    lag = int(round(cfg.mm_data_lag_s / dt))
    tau = (end - t[i0:i1]) / SECONDS_PER_YEAR
    lam_y = cfg.jumps_per_day * 365
    # Best-informed probability: the oracle basis mean-reverts within seconds, so the expected
    # settlement price is today's exchange spot (plus basis noise at the close, added as variance).
    tau_s = np.maximum(end - t[i0:i1], 0)
    phi = 0.5 ** (tau_s / cfg.basis_halflife_s)
    basis_now = ref[i0:i1] / spot[i0:i1] - 1
    s_eff = spot[i0:i1] * (1 + basis_now * phi)
    sig_eff = np.sqrt(sig[i0:i1] ** 2 + cfg.basis_sd**2 * (1 - phi**2) / np.maximum(tau, 1e-12))
    true_fair = digital_prob_jump(s_eff, strike, np.maximum(tau, 0), sig_eff, lam_y, jsd[i0:i1])
    view_idx = np.maximum(np.arange(i0, i1) - lag, 0)
    maker_fair = digital_prob_jump(spot[view_idx], strike, np.maximum(tau, 0), sig[view_idx], lam_y, jsd[view_idx])

    bid = np.empty(n); ask = np.empty(n); bsz = np.empty(n); asz = np.empty(n)
    fee = lambda x: cfg.fee_rate * x * (1 - x)
    lat_med = np.log(cfg.comp_latency_median_ms / 1000)
    k = 0
    while k < n:  # event-driven: one iteration per maker re-quote
        m = maker_fair[k]
        cb, ca = _round_bid(m - cfg.mm_half_spread), _round_ask(m + cfg.mm_half_spread)
        if ca <= cb + 1e-9:  # never post a locked/crossed book
            if cb >= 1 - 2 * TICK - 1e-9:
                cb, ca = 1 - 2 * TICK, 1 - TICK
            else:
                ca = cb + TICK
        nxt = min(n, k + max(1, int(rng.exponential(cfg.mm_refresh_mean_s) / dt)))
        if cfg.competitors and nxt - k > 1:
            f = true_fair[k:nxt]
            mis = np.maximum(f - ca - fee(ca), (1 - f) - (1 - cb) - fee(cb))
            hit = np.flatnonzero(mis > cfg.comp_threshold)
            if hit.size:
                lat = rng.lognormal(lat_med, cfg.comp_latency_sigma)
                nxt = min(nxt, k + hit[0] + max(1, int(round(lat / dt))))
        bid[k:nxt], ask[k:nxt] = cb, ca
        bsz[k:nxt] = np.round(rng.lognormal(np.log(cfg.mm_depth_median), 0.6))
        asz[k:nxt] = np.round(rng.lognormal(np.log(cfg.mm_depth_median), 0.6))
        k = nxt
    settle = ref[i1 - 1] if i1 - 1 == int(round(end / dt)) else float("nan")
    return dict(i0=i0, strike=float(strike), settle=float(settle), yes_bid=bid, yes_ask=ask,
                bid_size=bsz, ask_size=asz)


def simulate(cfg: SimConfig | None = None) -> Tape:
    cfg = cfg or SimConfig()
    rng = np.random.default_rng(cfg.seed)
    t, spot, ref, (true_sig, jump_sd) = simulate_prices(cfg, rng)
    T = t[-1]
    markets = []
    for a in cfg.assets:
        for w in cfg.windows:
            L = WINDOWS[w]
            s = 0.0
            while s + L <= T:
                d = simulate_market(t, spot[a], ref[a], true_sig[a], jump_sd[a], s, s + L, cfg, rng)
                hh, mm = divmod(int(s // 60), 60)
                markets.append(Market(
                    market_id=f"{a}-{w}-{int(s)}", asset=a, kind="updown",
                    label=f"{a} Up/Down {w} +{hh:02d}:{mm:02d}", start=s, end=s + L, strike=d["strike"],
                    settle_price=d["settle"], i0=d["i0"], meta={"window": w},
                    yes_bid=d["yes_bid"], yes_ask=d["yes_ask"], bid_size=d["bid_size"], ask_size=d["ask_size"]))
                s += L
    return Tape(t, spot, ref, markets, source="simulation",
                meta={"sim_config": cfg.to_dict(), "true_sigma_mean": {a: float(true_sig[a].mean()) for a in cfg.assets}})
