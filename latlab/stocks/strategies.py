"""Long-horizon rules with decades of evidence, tested honestly on the longest free history available.

The short-term rules elsewhere in this project did not beat simply holding the market. These are the
well-known alternatives that have held up in academic work for decades, implemented with their
textbook settings (no parameter was tuned on this data, which is what keeps the test honest):

* SPY / QQQ with a 10-month moving-average filter (Faber, 2007): hold the fund while its month-end
  price is above its 10-month average, otherwise hold Treasury bills.
* Global dual momentum (Antonacci, 2014): if the S&P 500 beat T-bills over 12 months, hold whichever
  of US (SPY) or international stocks (EFA) did better; otherwise hold Treasury bonds (IEF).
* Sector momentum: each month, the 3 of the 9 original S&P sector funds with the best 6-month return.
* Bitcoin with a 200-day moving-average filter, checked daily, Binance fees on every switch.

Mechanics: decisions on the close of the signal day, executed at the NEXT day's close (no look-ahead),
dividend-adjusted prices, a cost on every switch. Each rule is compared with simply holding its own
asset over the same days, on the whole history and on its first and second halves separately.
"""

from __future__ import annotations

import datetime as dt
import math
import time

import numpy as np

from .toolkit import scanner

ETF_COST_BPS = 5.0      # per side, per switch: spread + slippage on very liquid ETFs
CRYPTO_COST_BPS = 10.0  # per side: Binance spot taker fee
SECTORS = ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"]
SYMBOLS = ["SPY", "QQQ", "EFA", "IEF", "BIL", *SECTORS, "BTC-USD"]


# ----------------------------------------------------------------------------- data
def fetch(sym: str, sc=None) -> tuple[list[str], np.ndarray]:
    """Dividend-adjusted daily closes, as far back as Yahoo serves."""
    sc = sc or scanner()
    import json
    doc = json.loads(sc.yahoo_get(sc.chart_path(sym, "max", "1d")))
    res = (doc.get("chart") or {}).get("result") or []
    if not res:
        raise RuntimeError(f"no chart for {sym}")
    r = res[0]
    ts = r.get("timestamp") or []
    ind = r.get("indicators") or {}
    adj = ((ind.get("adjclose") or [{}])[0] or {}).get("adjclose")
    close = ((ind.get("quote") or [{}])[0] or {}).get("close")
    px = adj if adj and any(v is not None for v in adj) else close
    crypto = sym.endswith("-USD")
    out = {}
    for t, p in zip(ts, px or []):
        if p is None or p <= 0:
            continue
        d = (dt.datetime.fromtimestamp(t, dt.timezone.utc).date() if crypto
             else sc.ts_to_eastern(t).date())
        out[d.isoformat()] = float(p)
    days = sorted(out)
    return days, np.array([out[d] for d in days])


def align(series: dict[str, tuple[list[str], np.ndarray]], calendar: list[str]) -> dict[str, np.ndarray]:
    """Each series on the calendar; NaN before it existed, last price carried over one-off gaps."""
    out = {}
    for sym, (days, px) in series.items():
        m = dict(zip(days, px))
        arr = np.full(len(calendar), np.nan)
        last = np.nan
        started = False
        for i, d in enumerate(calendar):
            if d in m:
                last, started = m[d], True
            arr[i] = last if started else np.nan
        out[sym] = arr
    return out


# ----------------------------------------------------------------------------- backtest core
def run_positions(calendar: list[str], prices: dict[str, np.ndarray], weights: list[dict[str, float]],
                  cost_bps: float) -> np.ndarray:
    """weights[i] = target weights decided at the close of day i (empty = keep). Executed at the close of
    day i+1, so they earn returns from day i+2. Returns the daily equity curve (starts at 1)."""
    n = len(calendar)
    eq = np.ones(n)
    held: dict[str, float] = {}
    pending: dict[str, float] | None = weights[0] if n and weights[0] else None
    for i in range(1, n):
        r = 0.0
        for s, w in held.items():
            p0, p1 = prices[s][i - 1], prices[s][i]
            if w and np.isfinite(p0) and np.isfinite(p1):
                r += w * (p1 / p0 - 1)
        eq[i] = eq[i - 1] * (1 + r)
        if pending is not None:                       # executed at today's close
            turnover = sum(abs(pending.get(s, 0) - held.get(s, 0)) for s in set(pending) | set(held))
            eq[i] *= 1 - turnover * cost_bps / 1e4
            held, pending = pending, None
        w = weights[i]
        if w is not None and w != held:
            pending = w
    return eq


def month_ends(calendar: list[str]) -> list[bool]:
    return [i == len(calendar) - 1 or calendar[i][:7] != calendar[i + 1][:7] for i in range(len(calendar))]


def stats(calendar: list[str], eq: np.ndarray, per_year: int) -> dict:
    if len(eq) < 30:
        return {}
    r = eq[1:] / eq[:-1] - 1
    yrs = len(r) / per_year
    peak = np.maximum.accumulate(eq)
    dd = eq / peak - 1
    years: dict[str, list[float]] = {}
    for d, x in zip(calendar, eq):
        years.setdefault(d[:4], []).append(float(x))
    ylist = sorted(years.items())
    yr = {}
    for k, (y, v) in enumerate(ylist):
        start = ylist[k - 1][1][-1] if k else v[0]
        yr[y] = round((v[-1] / start - 1) * 100, 1)
    vol = float(r.std() * math.sqrt(per_year))
    cagr = float(eq[-1] ** (1 / yrs) - 1) if yrs > 0 else 0.0
    return {"from": calendar[0], "to": calendar[-1], "cagr_pct": round(cagr * 100, 2), "vol_pct": round(vol * 100, 2),
            "sharpe": round(float(r.mean() / r.std() * math.sqrt(per_year)), 2) if r.std() > 0 else None,
            "max_dd_pct": round(float(dd.min()) * 100, 1), "total_pct": round(float(eq[-1] - 1) * 100, 1),
            "worst_year": min(yr.items(), key=lambda kv: kv[1]) if yr else None, "years": yr}


def curve(calendar: list[str], eq: np.ndarray, points: int = 160) -> list:
    step = max(1, len(eq) // points)
    idx = list(range(0, len(eq), step))
    if idx[-1] != len(eq) - 1:
        idx.append(len(eq) - 1)
    return [[calendar[i], round(float(eq[i]), 4)] for i in idx]


# ----------------------------------------------------------------------------- the rules
def sma_filter(calendar, prices, asset, cash, months=10):
    """Month-end price above its average of the last `months` month-end prices -> asset, else cash."""
    me = month_ends(calendar)
    px = prices[asset]
    closes: list[float] = []
    w: list[dict | None] = [None] * len(calendar)
    for i, d in enumerate(calendar):
        if not me[i] or not np.isfinite(px[i]):
            continue
        closes.append(px[i])
        if len(closes) < months:
            continue
        avg = sum(closes[-months:]) / months
        safe = cash if np.isfinite(prices[cash][i]) else None
        w[i] = {asset: 1.0} if px[i] > avg else ({safe: 1.0} if safe else {})
    return w


def ret_over(px: np.ndarray, i: int, lookback: int) -> float | None:
    j = i - lookback
    if j < 0 or not (np.isfinite(px[i]) and np.isfinite(px[j])):
        return None
    return px[i] / px[j] - 1


def dual_momentum(calendar, prices):
    me = month_ends(calendar)
    w: list[dict | None] = [None] * len(calendar)
    for i in range(len(calendar)):
        if not me[i]:
            continue
        spy, efa = ret_over(prices["SPY"], i, 252), ret_over(prices["EFA"], i, 252)
        bil = ret_over(prices["BIL"], i, 252)
        if spy is None or efa is None or not np.isfinite(prices["IEF"][i]):
            continue
        if spy > (bil if bil is not None else 0.0):
            w[i] = {"SPY": 1.0} if spy >= efa else {"EFA": 1.0}
        else:
            w[i] = {"IEF": 1.0}
    return w


def sector_momentum(calendar, prices, top=3, lookback=126):
    me = month_ends(calendar)
    w: list[dict | None] = [None] * len(calendar)
    for i in range(len(calendar)):
        if not me[i]:
            continue
        rets = {s: ret_over(prices[s], i, lookback) for s in SECTORS}
        rets = {s: r for s, r in rets.items() if r is not None}
        if len(rets) < top:
            continue
        best = sorted(rets, key=rets.get, reverse=True)[:top]
        w[i] = {s: 1.0 / top for s in best}
    return w


def daily_sma_filter(calendar, prices, asset, days=200):
    px = prices[asset]
    w: list[dict | None] = [None] * len(calendar)
    for i in range(days, len(calendar)):
        win = px[i - days + 1:i + 1]
        if not np.all(np.isfinite(win)):
            continue
        w[i] = {asset: 1.0} if px[i] > win.mean() else {}
    return w


def hold(calendar, prices, asset):
    w: list[dict | None] = [None] * len(calendar)
    first = next((i for i, p in enumerate(prices[asset]) if np.isfinite(p)), None)
    if first is not None:
        w[first] = {asset: 1.0}
    return w


# ----------------------------------------------------------------------------- evaluation
def evaluate(name, calendar, prices, weights, bench_asset, cost_bps, per_year, desc, rule):
    start = next((i for i, w in enumerate(weights) if w is not None), None)
    if start is None:
        return None
    cal = calendar[start:]
    pr = {s: v[start:] for s, v in prices.items()}
    wts = weights[start:]
    eq = run_positions(cal, pr, wts, cost_bps)
    bw = hold(cal, pr, bench_asset)
    beq = run_positions(cal, pr, bw, cost_bps)
    half = len(cal) // 2
    parts = {}
    for label, sl in (("first_half", slice(0, half + 1)), ("second_half", slice(half, None))):
        e, b = eq[sl] / eq[sl][0], beq[sl] / beq[sl][0]
        parts[label] = {"rule": stats(cal[sl], e, per_year), "hold": stats(cal[sl], b, per_year)}
    s_all, b_all = stats(cal, eq, per_year), stats(cal, beq, per_year)

    def better(a, b):
        return (a.get("sharpe") or -9) > (b.get("sharpe") or -9) and a["max_dd_pct"] > b["max_dd_pct"]
    steadier = all(better(parts[p]["rule"], parts[p]["hold"]) for p in parts)
    richer = all(parts[p]["rule"]["cagr_pct"] > parts[p]["hold"]["cagr_pct"] for p in parts)
    # current position: the last decision and when it took effect
    last_i = max((i for i, w in enumerate(wts) if w is not None), default=None)
    cur = wts[last_i] if last_i is not None else {}
    first_same = last_i
    for i in range(last_i or 0, -1, -1):
        if wts[i] is None:
            continue
        if wts[i] != cur:
            break
        first_same = i
    since = cal[min(first_same + 1, len(cal) - 1)] if first_same is not None else None
    switches = sum(1 for i in range(1, len(wts)) if wts[i] is not None
                   and wts[i] != next((wts[j] for j in range(i - 1, -1, -1) if wts[j] is not None), None))
    years = len(cal) / per_year
    return {
        "name": name, "desc": desc, "rule": rule, "benchmark": bench_asset, "cost_bps": cost_bps,
        "stats": s_all, "hold_stats": b_all, "halves": parts,
        "verdict": ("steadier_and_richer" if steadier and richer else "steadier" if steadier
                    else "richer" if richer else "no_better"),
        "signal": {"hold": {k: round(v, 3) for k, v in (cur or {}).items()}, "decided": cal[last_i] if last_i is not None else None,
                   "since": since or cal[0]},
        "switches_per_year": round(switches / years, 1) if years else None,
        "curve": curve(cal, eq), "hold_curve": curve(cal, beq),
    }


def run(log=print, pause: float = 0.4) -> dict:
    sc = scanner()
    series = {}
    for s in SYMBOLS:
        try:
            series[s] = fetch(s, sc)
            log(f"  {s}: {len(series[s][0])} days from {series[s][0][0]}")
        except Exception as exc:  # noqa: BLE001
            log(f"  {s} unavailable: {exc}")
        time.sleep(pause)
    out = {"generated": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", "rules": []}
    if "SPY" in series:
        cal = series["SPY"][0]
        today = sc.to_eastern(dt.datetime.utcnow()).date().isoformat()
        cal = [d for d in cal if d < today] or cal           # complete sessions only
        P = align({k: v for k, v in series.items() if not k.endswith("-USD")}, cal)
        P.setdefault("BIL", np.full(len(cal), np.nan))
        cands = [
            ("S&P 500 with a 10-month trend filter", sma_filter(cal, P, "SPY", "BIL"), "SPY",
             "Hold SPY while its month-end price is above its 10-month average; otherwise Treasury bills (BIL).",
             "Faber (2007)"),
            ("Nasdaq-100 with a 10-month trend filter", sma_filter(cal, P, "QQQ", "BIL"), "QQQ",
             "Hold QQQ while its month-end price is above its 10-month average; otherwise Treasury bills.",
             "Faber (2007), applied to QQQ"),
        ]
        if all(k in P for k in ("EFA", "IEF")):
            cands.append(("Global dual momentum", dual_momentum(cal, P), "SPY",
                          "Each month: if SPY beat T-bills over 12 months, hold the better of SPY and EFA "
                          "(international); otherwise hold Treasury bonds (IEF).", "Antonacci (2014)"))
        if all(s in P for s in SECTORS):
            cands.append(("Sector momentum, top 3 of 9", sector_momentum(cal, P), "SPY",
                          "Each month: hold the 3 S&P sector funds with the best 6-month return, equal weights.",
                          "Moskowitz & Grinblatt (1999)"))
        for name, w, bench, desc, src in cands:
            e = evaluate(name, cal, P, w, bench, ETF_COST_BPS, 252, desc, src)
            if e:
                out["rules"].append(e)
    if "BTC-USD" in series:
        cal_c, px = series["BTC-USD"]
        today_utc = dt.datetime.utcnow().date().isoformat()
        keep = [i for i, d in enumerate(cal_c) if d < today_utc]
        cal_c, px = [cal_c[i] for i in keep], px[keep]
        Pc = {"BTC-USD": px}
        e = evaluate("Bitcoin with a 200-day trend filter", cal_c, Pc, daily_sma_filter(cal_c, Pc, "BTC-USD"),
                     "BTC-USD", CRYPTO_COST_BPS, 365,
                     "Hold Bitcoin while its daily close is above its 200-day average; otherwise cash (USDT).",
                     "Classic trend following, as in Faber (2007)")
        if e:
            out["rules"].append(e)
    return out
