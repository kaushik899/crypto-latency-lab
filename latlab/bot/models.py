"""Online-calibrated predictions.

Every strategy turns market data into one number ``x`` (a signal strength) per coin. Whether that
number predicts anything is not assumed: the engine records every observation, waits for the outcome
(the actual move over the strategy's horizon), and fits ``future_bps = beta * x`` with exponential
forgetting. A model only predicts once beta is statistically positive; until then the strategy cannot
trade at all. This is what keeps the bot from trading on a pattern that only looked good once.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class OnlineBeta:
    halflife_obs: float = 3000.0
    min_obs: float = 300.0
    min_t: float = 3.0
    sxx: float = 0.0
    sxy: float = 0.0
    syy: float = 0.0
    n: float = 0.0       # effective sample size
    count: int = 0       # total observations ever

    def update(self, x: float, y: float) -> None:
        if not (math.isfinite(x) and math.isfinite(y)):
            return
        lam = 0.5 ** (1.0 / self.halflife_obs)
        self.sxx = lam * self.sxx + x * x
        self.sxy = lam * self.sxy + x * y
        self.syy = lam * self.syy + y * y
        self.n = lam * self.n + 1.0
        self.count += 1

    @property
    def beta(self) -> float:
        return self.sxy / self.sxx if self.sxx > 0 else 0.0

    @property
    def t_stat(self) -> float:
        if self.sxx <= 0 or self.n < 3:
            return 0.0
        rss = max(self.syy - self.beta * self.sxy, 0.0)
        s2 = rss / max(self.n - 1, 1.0)
        se = math.sqrt(s2 / self.sxx) if s2 > 0 else float("inf")
        return self.beta / se if se > 0 else 0.0

    @property
    def ready(self) -> bool:
        return self.n >= self.min_obs and self.beta > 0 and self.t_stat >= self.min_t

    @property
    def se(self) -> float:
        t = self.t_stat
        return abs(self.beta / t) if t else float("inf")

    def predict(self, x: float) -> float | None:
        return self.beta * x if self.ready else None

    def predict_low(self, x: float, z: float = 2.0) -> float | None:
        """Conservative prediction: beta minus ``z`` standard errors (for positive x)."""
        if not self.ready:
            return None
        return max(self.beta - z * self.se, 0.0) * x

    def summary(self) -> dict:
        return {"beta": round(self.beta, 4), "t": round(self.t_stat, 2), "n": round(self.n), "count": self.count,
                "ready": self.ready}
