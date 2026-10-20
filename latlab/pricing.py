"""Fair value of short-dated BTC binary markets.

A Polymarket "Bitcoin Up or Down" market pays $1 if the reference price at the end of
the window is >= the price at the start. A strike market ("BTC above K at 16:00") pays
$1 if the final price is >= K. Both are cash-or-nothing digital calls, so with zero
drift over minutes the fair probability is

    P(S_T >= K) = Phi(d2),   d2 = [ln(S/K) - sigma^2 tau / 2] / (sigma sqrt(tau))

where S is the current spot, K the strike (or window open), tau the time left in years
and sigma the annualised volatility. Short horizons have fat tails, so a Student-t
variant (variance-matched) is provided as well.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy import stats

SECONDS_PER_YEAR = 365.0 * 24 * 3600


def _phi(x):
    return stats.norm.cdf(x)


def digital_prob(spot, strike, tau_years, sigma, dist: str = "normal", nu: float = 4.0):
    """Probability that the terminal price finishes at or above ``strike``.

    Works on scalars or numpy arrays. ``tau_years <= 0`` collapses to the settled payoff.
    ``dist='student'`` uses a Student-t with ``nu`` degrees of freedom scaled to the same
    variance, which puts more weight on large short-horizon moves.
    """
    spot = np.asarray(spot, dtype=float)
    strike = np.asarray(strike, dtype=float)
    tau = np.asarray(tau_years, dtype=float)
    sigma = np.asarray(sigma, dtype=float)

    settled = tau <= 0
    sd = sigma * np.sqrt(np.where(settled, 1.0, tau))
    sd = np.where(sd <= 0, 1e-12, sd)
    drift = -0.5 * sd**2
    z = (np.log(spot / strike) + drift) / sd
    if dist == "student":
        scale = math.sqrt((nu - 2.0) / nu)  # unit variance
        p = stats.t.cdf(z / scale, df=nu)
    else:
        p = _phi(z)
    p = np.where(settled, (spot >= strike).astype(float), p)
    return p if p.ndim else float(p)


def digital_prob_jump(spot, strike, tau_years, sigma, jump_rate_y: float, jump_sd: float, kmax: int = 3):
    """Digital probability under Merton jump-diffusion (zero-mean normal jumps).

    Conditions on the number of jumps k ~ Poisson(lambda * tau) in the window and mixes
    lognormal digitals with variance sigma^2 tau + k J^2. For minute-scale windows the
    no-jump term dominates, so this stays close to the diffusive price near the money
    while putting the right mass in the tails.
    """
    spot = np.asarray(spot, dtype=float)
    tau = np.maximum(np.asarray(tau_years, dtype=float), 0.0)
    sigma = np.asarray(sigma, dtype=float)
    jump_sd = np.asarray(jump_sd, dtype=float)
    lam_t = jump_rate_y * tau
    total = np.zeros(np.broadcast(spot, tau, sigma).shape)
    weight = np.zeros_like(total)
    for k in range(kmax + 1):
        w = np.exp(-lam_t) * lam_t**k / math.factorial(k)
        var = sigma**2 * tau + k * jump_sd**2
        sd = np.sqrt(np.maximum(var, 1e-18))
        z = (np.log(spot / strike) - 0.5 * var) / sd
        total = total + w * _phi(z)
        weight = weight + w
    p = total / weight
    p = np.where(tau <= 0, (spot >= strike).astype(float), p)
    return p if p.ndim else float(p)


def digital_delta(spot, strike, tau_years, sigma):
    """dP/dS — how fast the fair probability moves per $1 of BTC."""
    sd = sigma * math.sqrt(max(tau_years, 1e-12))
    d2 = (math.log(spot / strike) - 0.5 * sd**2) / sd
    return float(stats.norm.pdf(d2) / (spot * sd))


def digital_vega(spot, strike, tau_years, sigma):
    """dP/dsigma — sensitivity of the fair value to the volatility estimate."""
    sd = sigma * math.sqrt(max(tau_years, 1e-12))
    d1 = (math.log(spot / strike) + 0.5 * sd**2) / sd
    return float(-stats.norm.pdf(d1 - sd) * d1 / sigma)


@dataclass
class EWMAVol:
    """Exponentially weighted realised volatility from irregular ticks.

    Variance is accumulated per second of elapsed time so that bursts of ticks do not
    over-count. ``halflife_s`` controls memory; ``floor``/``cap`` keep the estimate sane
    during quiet or chaotic stretches.
    """

    halflife_s: float = 300.0
    init_sigma: float = 0.50
    floor: float = 0.10
    cap: float = 3.00
    clip_sd: float = 4.0     # winsorise single moves beyond 4 sd so one jump can't blow up the estimate

    def __post_init__(self):
        self._var_per_s = (self.init_sigma**2) / SECONDS_PER_YEAR
        self._last_t: float | None = None
        self._last_p: float | None = None
        self.n = 0

    def update(self, t: float, price: float) -> float:
        if self._last_t is not None and t > self._last_t and price > 0:
            dt = t - self._last_t
            r = math.log(price / self._last_p)
            inst = min((r * r) / dt, self.clip_sd**2 * self._var_per_s)
            w = 1.0 - 0.5 ** (dt / self.halflife_s)
            self._var_per_s = (1 - w) * self._var_per_s + w * inst
            self.n += 1
        if price > 0:
            self._last_t, self._last_p = t, price
        return self.sigma

    @property
    def sigma(self) -> float:
        s = math.sqrt(self._var_per_s * SECONDS_PER_YEAR)
        return min(max(s, self.floor), self.cap)


def ewma_sigma_series(t: np.ndarray, price: np.ndarray, halflife_s: float = 300.0,
                      init_sigma: float = 0.5, floor: float = 0.10, cap: float = 3.0,
                      clip_sd: float = 4.0) -> np.ndarray:
    """Jump-robust EWMA volatility along a price path (annualised).

    Each squared return is winsorised at ``clip_sd`` standard deviations of the current
    estimate before it enters the average, so a single jump moves the price (and the fair
    value) but does not make the model think the next five minutes will be wild.
    """
    r2 = np.diff(np.log(price)) ** 2
    dts = np.diff(t)
    out = np.empty(len(price))
    var = init_sigma**2 / SECONDS_PER_YEAR
    out[0] = init_sigma
    hl = halflife_s
    c2 = clip_sd**2
    for i in range(len(r2)):
        dt = dts[i]
        if dt > 0 and r2[i] == r2[i]:
            w = 1.0 - 0.5 ** (dt / hl)
            var = (1 - w) * var + w * min(r2[i] / dt, c2 * var)
        out[i + 1] = var
    sig = np.sqrt(out[1:] * SECONDS_PER_YEAR)
    out[1:] = np.clip(sig, floor, cap)
    return out
