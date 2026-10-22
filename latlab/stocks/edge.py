"""Does any opening-range-breakout rule actually make money after costs? A walk-forward test.

For every liquid stock and every rule in a small grid, replay the last ~60 sessions bar by bar:

* entry at the OPEN of the bar after the first 5-minute close above the opening-range high
  (the toolkit's history replay enters at that close; waiting one bar is what a real order gets),
  plus slippage; no new entries after 15:30 ET;
* stop checked before target inside each bar (the conservative order);
* otherwise flat at the 15:55 bar's close;
* costs: slippage each side, SEC fee on the sale value, FINRA TAF per share sold, broker commission.

Rules are ranked on the first two thirds of the days only (in-sample); the last third is held out
(out-of-sample). Because 18 rules are tried, a rule must clear t >= 3 in-sample — roughly a
Bonferroni-corrected 5% — and then stay positive out-of-sample before anything is called an edge.
t-statistics are clustered by day, because breakouts across stocks on one day are not independent.
"""

from __future__ import annotations

import itertools
import math
import statistics
from dataclasses import asdict, dataclass

from .toolkit import timemachine

SEC_FEE = 0.0000278        # $ per $ of sale value (SEC Section 31 rate in force since May 2024)
FINRA_TAF = 0.000166       # $ per share sold, capped at $8.30 per trade
LAST_ENTRY_SLOT = 72       # 15:30 ET: no new entries in the last half hour
FLAT_SLOT = 76             # the 15:55 bar: everything is closed at its close
SELECT_T = 3.0             # in-sample bar after testing 18 rules
OOS_T = 1.0


@dataclass(frozen=True)
class Rule:
    or_minutes: int        # opening range length: 5, 15 or 30
    stop: str              # "or_mid" or "or_low"
    target_r: float        # 1.0, 2.0, or 0 = no target, hold to 15:55

    @property
    def key(self) -> str:
        t = f"{self.target_r:g}R" if self.target_r else "close"
        return f"ORB{self.or_minutes}-{self.stop.replace('or_', '')}-{t}"


GRID = [Rule(m, s, t) for m, s, t in itertools.product((5, 15, 30), ("or_mid", "or_low"), (1.0, 2.0, 0.0))]


@dataclass
class Costs:
    slippage_bps: float = 2.0       # per side, on a liquid large cap with a marketable order
    commission: float = 0.0         # per order (Alpaca, IBKR Lite: 0)
    shares: int = 10                # for the per-share fees; small-account sized

    def round_trip_bps(self, entry: float, exit_: float) -> float:
        fees = self.commission * 2 + min(FINRA_TAF * self.shares, 8.30) + SEC_FEE * exit_ * self.shares
        return 2 * self.slippage_bps + 1e4 * fees / (entry * self.shares)


@dataclass
class Trade:
    symbol: str
    date: str
    rule: str
    entry_slot: int
    entry: float
    stop: float
    target: float | None
    exit: float
    outcome: str             # target / stop / close
    R: float                 # net of costs, in units of the planned risk
    net_bps: float           # net of costs, in basis points of the entry


def run_rule(sym: str, sessions: list[tuple[str, list[dict]]], rule: Rule, costs: Costs) -> list[Trade]:
    tm = timemachine()
    n = rule.or_minutes // 5
    slip = costs.slippage_bps / 1e4
    out = []
    for d, D in sessions:
        oh, ol = tm.opening_range(D, n)
        k = next((i for i in range(n, LAST_ENTRY_SLOT) if D[i]["c"] > oh), None)
        if k is None:
            continue
        raw = D[k + 1]["o"]
        e = raw * (1 + slip)
        s = tm.rule_stop({"stop_rule": rule.stop}, oh, ol, e, True)
        risk = e - s
        if risk <= e * 0.0005:          # a stop inside 5 bp of the entry is noise, skip
            continue
        t = e + rule.target_r * risk if rule.target_r else None
        outcome, px = "close", D[FLAT_SLOT]["c"]
        for i in range(k + 1, FLAT_SLOT + 1):
            b = D[i]
            if b["l"] <= s:
                outcome, px = "stop", min(s, b["o"])        # a gap through the stop fills at the open
                break
            if t is not None and b["h"] >= t:
                outcome, px = "target", max(t, b["o"]) if i > k + 1 else t
                break
        x = px * (1 - slip)
        cost_bps = costs.round_trip_bps(e, x) - 2 * costs.slippage_bps   # slippage already in e and x
        net_bps = 1e4 * (x - e) / e - cost_bps
        R = net_bps * e / 1e4 / risk
        out.append(Trade(sym, d, rule.key, k + 1, round(e, 4), round(s, 2), round(t, 2) if t else None,
                         round(x, 4), outcome, round(R, 4), round(net_bps, 2)))
    return out


def summarize(trades: list[Trade]) -> dict:
    """Mean R, a day-clustered t-statistic, win rate, profit factor, worst drawdown in R."""
    if not trades:
        return {"n": 0, "days": 0, "mean_R": None, "t": None, "win": None, "pf": None, "max_dd_R": None,
                "mean_bps": None, "total_R": 0.0}
    by_day: dict[str, list[float]] = {}
    for t in trades:
        by_day.setdefault(t.date, []).append(t.R)
    day_means = [statistics.mean(v) for v in by_day.values()]
    Rs = [t.R for t in trades]
    mean = statistics.mean(Rs)
    if len(day_means) > 1 and statistics.pstdev(day_means) > 0:
        tstat = statistics.mean(day_means) / (statistics.stdev(day_means) / math.sqrt(len(day_means)))
    else:
        tstat = None
    gains = sum(r for r in Rs if r > 0)
    losses = -sum(r for r in Rs if r < 0)
    eq, peak, dd = 0.0, 0.0, 0.0
    for d in sorted(by_day):
        eq += sum(by_day[d])
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    return {"n": len(trades), "days": len(by_day), "mean_R": round(mean, 4),
            "t": round(tstat, 2) if tstat is not None else None,
            "win": round(sum(r > 0 for r in Rs) / len(Rs), 3),
            "pf": round(gains / losses, 2) if losses else None,
            "max_dd_R": round(dd, 2), "mean_bps": round(statistics.mean(t.net_bps for t in trades), 2),
            "total_R": round(sum(Rs), 2)}


def split_days(dates: list[str], frac: float = 2 / 3) -> str:
    """The first out-of-sample date."""
    ds = sorted(set(dates))
    return ds[min(len(ds) - 1, max(1, int(len(ds) * frac)))]


def walk_forward(universe: dict[str, list[tuple[str, list[dict]]]], costs: Costs | None = None,
                 grid: list[Rule] = GRID, min_sym_trades: int = 5) -> dict:
    """universe: {symbol: full sessions}. Returns rule table, chosen rules, per-symbol eligibility."""
    costs = costs or Costs()
    all_dates = [d for s in universe.values() for d, _ in s]
    if not all_dates:
        return {"rules": [], "chosen": [], "symbols": {}, "split": None}
    cut = split_days(all_dates)
    trades = {r.key: [] for r in grid}
    for sym, sessions in universe.items():
        for r in grid:
            trades[r.key].extend(run_rule(sym, sessions, r, costs))
    table = []
    for r in grid:
        tr = trades[r.key]
        ins = [t for t in tr if t.date < cut]
        oos = [t for t in tr if t.date >= cut]
        a, b = summarize(ins), summarize(oos)
        passed_is = a["t"] is not None and a["t"] >= SELECT_T and a["mean_R"] > 0
        passed_oos = b["t"] is not None and b["mean_R"] > 0 and b["t"] >= OOS_T
        table.append({"rule": r.key, **asdict(r), "in_sample": a, "out_of_sample": b,
                      "selected": passed_is, "validated": passed_is and passed_oos})
    table.sort(key=lambda x: -(x["in_sample"]["t"] or -99))
    chosen = [x["rule"] for x in table if x["validated"]]
    symbols = {}
    for key in chosen:
        per: dict[str, list[Trade]] = {}
        for t in trades[key]:
            per.setdefault(t.symbol, []).append(t)
        for sym, tr in per.items():
            ins = summarize([t for t in tr if t.date < cut])
            oos = summarize([t for t in tr if t.date >= cut])
            ok = ins["n"] >= min_sym_trades and ins["mean_R"] > 0 and (oos["n"] == 0 or oos["mean_R"] > -0.25)
            if ok and (sym not in symbols or ins["mean_R"] > symbols[sym]["in_sample"]["mean_R"]):
                symbols[sym] = {"rule": key, "in_sample": ins, "out_of_sample": oos}
    return {"split": cut, "costs": asdict(costs), "rules": table, "chosen": chosen, "symbols": symbols,
            "select_t": SELECT_T, "trades": {k: v for k, v in trades.items()}}
