"""Polymarket trading costs.

Polymarket charges takers on fee-enabled categories:

    fee = shares * fee_rate * p * (1 - p)

with ``fee_rate = 0.07`` for Crypto and zero maker fee
(docs.polymarket.com, Maker Rebates Program). The fee peaks at p = 0.5
(1.75 cents per share) and vanishes at the extremes, which is precisely where
latency gaps on Up/Down markets are largest.
"""

from __future__ import annotations

import numpy as np

CRYPTO_TAKER_RATE = 0.07
TICK = 0.01


def taker_fee(shares, price, rate: float = CRYPTO_TAKER_RATE):
    """Fee in USD for a taker fill of ``shares`` at ``price``. Rounded to 5 dp like the venue."""
    p = np.asarray(price, dtype=float)
    fee = np.asarray(shares, dtype=float) * rate * p * (1.0 - p)
    fee = np.round(fee, 5)
    return fee if np.ndim(fee) else float(fee)


def fee_per_share(price, rate: float = CRYPTO_TAKER_RATE):
    p = np.asarray(price, dtype=float)
    out = rate * p * (1.0 - p)
    return out if np.ndim(out) else float(out)


def net_edge_buy(fair_prob, ask, rate: float = CRYPTO_TAKER_RATE):
    """Expected profit per share of buying an outcome at ``ask`` when it pays 1 with ``fair_prob``."""
    return np.asarray(fair_prob) - np.asarray(ask) - fee_per_share(ask, rate)


def breakeven_gap(price, rate: float = CRYPTO_TAKER_RATE):
    """Minimum (fair - ask) needed just to cover the taker fee at a given price."""
    return fee_per_share(price, rate)
