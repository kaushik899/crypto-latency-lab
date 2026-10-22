"""The daily chart scan, made checkable: today's matches plus how each setup really performed.

1. Every US stock (Nasdaq Trader universe, ~5,900): two years of daily closes from Yahoo spark.
2. Today's matches use the Day Trader scanner's own rule (``find_setups``) on complete bars only.
3. Base rates: the same rules, computed vectorised on every past day of every liquid stock, then
   the return after the signal — entry at the NEXT session's close (no look-ahead), held 1, 5 or
   10 sessions, minus 10 bp round-trip costs — compared with the average liquid stock on the same
   days (so a rising market does not flatter a setup). Results are averaged per day first, the
   t-statistic uses a Newey-West variance for overlapping holds, and the first two thirds of the
   history are checked against the last third.
4. Every match is then enriched from its own daily OHLCV chart: relative volume, ATR, 50/200-day
   trend, distance to the 52-week high, and whether the breakout also cleared the intraday highs.

Free Yahoo data is unofficial and can be revised. A match is a chart that fits a rule, not a buy.
"""

from __future__ import annotations

import datetime as dt
import math
import time

import numpy as np

from .toolkit import scanner

SETUPS = ["breakout_20d", "bull_flag", "reclaim_ma20", "oversold", "oversold_bounce"]
LABELS = {
    "breakout_20d": "Closed at a 20-day high",
    "bull_flag": "Bull flag",
    "reclaim_ma20": "Back above its 20-day average",
    "oversold": "Oversold (RSI under 30)",
    "oversold_bounce": "Bounce after oversold",
}
HORIZONS = (1, 5, 10)
COST_BPS = 10.0          # round trip: half-spread + slippage on liquid US names, commission-free broker
SELECT_T = 2.9           # 5 setups x 3 horizons = 15 tests: roughly a Bonferroni 5 % bar


# ----------------------------------------------------------------------------- indicators
def rsi_series(c: np.ndarray, n: int = 14) -> np.ndarray:
    """Wilder RSI at every index, identical to the toolkit's rsi(closes[:i+1]) (seeded at the start)."""
    out = np.full(len(c), np.nan)
    if len(c) < n + 1:
        return out
    d = np.diff(c)
    g, lo = np.maximum(d, 0.0), np.maximum(-d, 0.0)
    ag, al = g[:n].mean(), lo[:n].mean()
    out[n] = 100 - 100 / (1 + ag / al) if al else 100.0
    for i in range(n, len(d)):
        ag = (ag * (n - 1) + g[i]) / n
        al = (al * (n - 1) + lo[i]) / n
        out[i + 1] = 100 - 100 / (1 + ag / al) if al else 100.0
    return out


def _roll(x: np.ndarray, w: int, fn) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) >= w:
        v = np.lib.stride_tricks.sliding_window_view(x, w)
        out[w - 1:] = fn(v, axis=1)
    return out


def setup_matrix(c: np.ndarray) -> dict[str, np.ndarray]:
    """Boolean series per setup: value at i == the toolkit's find_setups(closes[:i+1]) (for i >= 29)."""
    n = len(c)
    hi20_prev = np.full(n, np.nan)
    hi20_prev[20:] = _roll(c, 20, np.max)[19:-1]                 # max of the 20 closes before i
    ma20 = _roll(c, 20, np.mean)
    ma20_prev = np.full(n, np.nan)
    ma20_prev[1:] = ma20[:-1]
    run = np.full(n, np.nan)
    run[15:] = c[10:-5] / c[:-15] - 1                             # c[-6] / c[-16] - 1
    hi5, lo5 = _roll(c, 5, np.max), _roll(c, 5, np.min)
    rng5 = (hi5 - lo5) / c
    r = rsi_series(c)
    r_prev = np.full(n, np.nan)
    r_prev[1:] = r[:-1]
    prev = np.full(n, np.nan)
    prev[1:] = c[:-1]
    valid = np.arange(n) >= 29
    with np.errstate(invalid="ignore"):
        m = {
            "breakout_20d": c > hi20_prev,
            "bull_flag": (run > 0.10) & (rng5 < 0.05),
            "reclaim_ma20": (prev < ma20_prev) & (c > ma20),
            "oversold": r < 30,
            "oversold_bounce": (r_prev < 30) & (c > prev),
        }
    return {k: v & valid for k, v in m.items()}


# ----------------------------------------------------------------------------- base rates
def _day_means(values: np.ndarray, days: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    uniq, inv = np.unique(days, return_inverse=True)
    return uniq, np.bincount(inv, weights=values) / np.bincount(inv)


def nw_t(series: np.ndarray, lag: int) -> float | None:
    """t-statistic of the mean with a Newey-West (Bartlett) variance: overlapping h-day holds on
    neighbouring days are correlated, so a plain t would be too confident."""
    n = len(series)
    if n < max(10, 2 * lag + 2):
        return None
    x = series - series.mean()
    var = (x @ x) / n
    for k in range(1, lag + 1):
        var += 2 * (1 - k / (lag + 1)) * (x[k:] @ x[:-k]) / n
    if var <= 0:
        return None
    return float(series.mean() / math.sqrt(var / n))


QS = (5, 10, 25, 50, 75, 90, 95)


def quantiles_bps(x: np.ndarray) -> list[float]:
    return [round(float(v) * 1e4, 1) for v in np.percentile(x, QS)]


def summarize(ret: np.ndarray, excess: np.ndarray, days: np.ndarray, lag: int = 0, dist: bool = False) -> dict:
    """Averages per day first (a crash day with 300 oversold stocks counts once), then over days."""
    if len(ret) == 0:
        return {"n": 0}
    _, r_day = _day_means(ret, days)
    _, e_day = _day_means(excess, days)
    t = nw_t(e_day, lag)
    return {"n": int(len(ret)), "days": int(len(e_day)), "mean_bps": round(float(r_day.mean()) * 1e4, 1),
            "excess_bps": round(float(e_day.mean()) * 1e4, 1),
            "pooled_excess_bps": round(float(excess.mean()) * 1e4, 1),
            "win": round(float((ret > 0).mean()), 3), "t": round(t, 2) if t is not None else None,
            **({"q_bps": quantiles_bps(ret)} if dist else {})}


def base_rates(closes: dict[str, tuple[list[str], np.ndarray]], liquid: set[str]) -> dict:
    """closes: {sym: (dates, close array)}. Uses liquid symbols only."""
    all_dates = sorted({d for s in liquid for d in closes[s][0]})
    if len(all_dates) < 60:
        return {}
    di = {d: i for i, d in enumerate(all_dates)}
    T = len(all_dates)
    # forward returns on a common date grid, entry at t+1 close, exit at t+1+h close
    fwd = {h: [] for h in HORIZONS}
    sig = {k: [] for k in SETUPS}
    for s in liquid:
        dates, c = closes[s]
        idx = np.array([di[d] for d in dates])
        m = setup_matrix(c)
        for h in HORIZONS:
            r = np.full(len(c), np.nan)
            if len(c) > h + 1:
                r[:-(h + 1)] = c[h + 1:] / c[1:-h] - 1
            fwd[h].append((idx, r))
        for k in SETUPS:
            sig[k].append((idx, m[k]))
    # same-day average of every liquid stock: the benchmark
    bench = {}
    for h in HORIZONS:
        tot, cnt = np.zeros(T), np.zeros(T)
        for idx, r in fwd[h]:
            ok = ~np.isnan(r)
            np.add.at(tot, idx[ok], r[ok])
            np.add.at(cnt, idx[ok], 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            bench[h] = tot / cnt
    cut = int(T * 2 / 3)
    out = {"first_date": all_dates[0], "last_date": all_dates[-1], "split": all_dates[cut],
           "stocks": len(liquid), "cost_bps": COST_BPS, "select_t": SELECT_T, "setups": {}}
    for k in SETUPS:
        row = {"label": LABELS[k], "horizons": {}}
        for h in HORIZONS:
            rets, exs, days = [], [], []
            for (idx, r), (_, m) in zip(fwd[h], sig[k]):
                ok = m & ~np.isnan(r)
                if ok.any():
                    rr = r[ok] - COST_BPS / 1e4
                    rets.append(rr)
                    exs.append(r[ok] - bench[h][idx[ok]])
                    days.append(idx[ok])
            if not rets:
                row["horizons"][str(h)] = {"all": {"n": 0}}
                continue
            R, E, D = np.concatenate(rets), np.concatenate(exs), np.concatenate(days)
            ins, oos = D < cut, D >= cut
            a, b = summarize(R[ins], E[ins], D[ins], h), summarize(R[oos], E[oos], D[oos], h)
            held = bool(a.get("t") is not None and a["t"] >= SELECT_T and a["excess_bps"] > 0
                        and b.get("n", 0) > 0 and b["excess_bps"] > 0)
            row["horizons"][str(h)] = {"all": summarize(R, E, D, h, dist=True), "in_sample": a, "out_of_sample": b, "held": held}
        out["setups"][k] = row
    # the benchmark itself: buying a random liquid stock on a random day (same entry, same costs)
    mk = {}
    for h in HORIZONS:
        rets = np.concatenate([r[~np.isnan(r)] for _, r in fwd[h]]) - COST_BPS / 1e4
        days = np.concatenate([idx[~np.isnan(r)] for idx, r in fwd[h]])
        _, r_day = _day_means(rets, days)
        mk[str(h)] = {"n": int(len(rets)), "mean_bps": round(float(r_day.mean()) * 1e4, 1),
                      "win": round(float((rets > 0).mean()), 3), "q_bps": quantiles_bps(rets)}
    out["any_stock"] = mk
    return out


HOLD_DAYS = (1, 5, 10, 21, 63, 126, 252)


def index_stats(dates: list[str], c: np.ndarray) -> dict:
    """Buy-and-hold an index fund: every holding period from every start day, plus drawdown."""
    out = {"first_date": dates[0], "last_date": dates[-1], "periods": {}}
    for h in HOLD_DAYS:
        if len(c) <= h + 1:
            continue
        r = c[h:] / c[:-h] - 1 - 2 / 1e4          # 2 bp round trip on SPY
        out["periods"][str(h)] = {"n": int(len(r)), "mean_bps": round(float(r.mean()) * 1e4, 1),
                                  "win": round(float((r > 0).mean()), 3), "q_bps": quantiles_bps(r)}
    peak = np.maximum.accumulate(c)
    out["max_drawdown_pct"] = round(float((c / peak - 1).min()) * 100, 1)
    yrs = len(c) / 252
    out["total_pct"] = round(float(c[-1] / c[0] - 1) * 100, 1)
    out["cagr_pct"] = round(float((c[-1] / c[0]) ** (1 / yrs) - 1) * 100, 1) if yrs > 0 else None
    out["spark"] = [round(float(x), 2) for x in c[:: max(1, len(c) // 120)]]
    return out


# ----------------------------------------------------------------------------- enrichment from v8 OHLCV
def enrich(sym: str, sc, now_utc) -> dict:
    s = sc.parse_chart(sc.yahoo_get(sc.chart_path(sym, "1y", "1d")), sym)
    tz_dates = [sc.ts_to_eastern(t).date() for t in s["t"]]
    today = sc.to_eastern(now_utc).date()
    sess = sc.session(today)
    done = sess is None or sc.to_eastern(now_utc).time() >= sess[1]
    rows = []
    for i, d in enumerate(tz_dates):
        o, h, l, c, v = (s[k][i] if s[k] else None for k in ("o", "h", "l", "c", "v"))
        if None in (o, h, l, c) or d > today or (d == today and not done):
            continue
        rows.append((d.isoformat(), o, h, l, c, v or 0))
    if len(rows) < 60:
        return {}
    o, h, l, c, v = (np.array([r[i] for r in rows], float) for i in range(1, 6))
    tr = np.maximum(h[1:] - l[1:], np.maximum(abs(h[1:] - c[:-1]), abs(l[1:] - c[:-1])))
    atr = tr[-14:].mean()
    vol20 = v[-21:-1].mean()
    ma50 = c[-50:].mean()
    ma200 = c[-200:].mean() if len(c) >= 200 else None
    hi52 = h[-252:].max()
    out = {
        "bar_date": rows[-1][0], "open": round(o[-1], 2), "high": round(h[-1], 2), "low": round(l[-1], 2),
        "close": round(c[-1], 2), "volume": int(v[-1]),
        "rel_volume": round(v[-1] / vol20, 2) if vol20 > 0 else None,
        "avg_dollar_volume": round(float((c[-20:] * v[-20:]).mean())),
        "atr_pct": round(100 * atr / c[-1], 2),
        "chg_1d": round(100 * (c[-1] / c[-2] - 1), 2), "chg_5d": round(100 * (c[-1] / c[-6] - 1), 2),
        "vs_ma50": round(100 * (c[-1] / ma50 - 1), 2),
        "vs_ma200": round(100 * (c[-1] / ma200 - 1), 2) if ma200 else None,
        "from_52w_high": round(100 * (c[-1] / hi52 - 1), 2),
        "breakout_intraday_high": bool(h[-1] > h[-21:-1].max()),
        "close_in_range": round(float((c[-1] - l[-1]) / (h[-1] - l[-1])), 2) if h[-1] > l[-1] else None,
        "spark": [round(float(x), 2) for x in c[-60:]],
    }
    return out


# ----------------------------------------------------------------------------- the run
def run(now_utc: dt.datetime | None = None, min_price: float = 5.0, min_volume: float = 1_000_000,
        rng: str = "2y", pause: float = 0.3, enrich_max: int = 600, log=print) -> dict:
    from . import data
    sc = scanner()
    now_utc = now_utc or dt.datetime.utcnow()
    syms = data.universe()
    log(f"universe {len(syms)}; fetching {rng} of daily closes...")
    t0 = time.time()
    got, failed = sc.fetch_spark(syms, rng, "1d", pause)
    log(f"  {len(got)} charts in {time.time() - t0:.0f}s, {len(failed)} failed")
    closes, meta, liquid = {}, {}, set()
    counts = {"universe": len(syms), "fetched": len(got), "failed": len(failed), "illiquid": 0, "short": 0}
    for sym, s in got.items():
        bars = sc.daily_closes(s, now_utc)
        if len(bars) < 40:
            counts["short"] += 1
            continue
        d = [b[0].isoformat() if hasattr(b[0], "isoformat") else str(b[0]) for b in bars]
        closes[sym] = (d, np.array([b[1] for b in bars], float))
        m = s.get("meta") or {}
        meta[sym] = m
        price = m.get("regularMarketPrice") or bars[-1][1]
        vol = m.get("regularMarketVolume")
        if price >= min_price and vol and vol >= min_volume:
            liquid.add(sym)
        else:
            counts["illiquid"] += 1
    counts["scanned"] = len(liquid)
    last = max((closes[s][0][-1] for s in liquid), default=None)
    hits = []
    for sym in liquid:
        d, c = closes[sym]
        if d[-1] != last:
            continue                                           # stale chart: not today's bar
        tags, nums = sc.find_setups(list(c))
        if not tags:
            continue
        m = meta[sym]
        hits.append({"symbol": sym, "name": m.get("shortName") or m.get("longName"), "setups": tags,
                     "price": round(float(c[-1]), 2), "rsi": round(nums["rsi"], 1) if nums["rsi"] is not None else None,
                     "high20": round(nums["high20"], 2), "ma20": round(nums["ma20"], 2),
                     "run_pct": round(nums["run_pct"], 1), "range5_pct": round(nums["range5_pct"], 1),
                     "last_volume": m.get("regularMarketVolume"),
                     "dollar_volume": round(float(c[-1]) * (m.get("regularMarketVolume") or 0)),
                     "exchange": m.get("fullExchangeName")})
    hits.sort(key=lambda x: -x["dollar_volume"])
    log(f"{len(hits)} matches on {last}; base rates over {len(liquid)} liquid stocks...")
    rates = base_rates(closes, liquid)
    index = {}
    try:
        idx_data, _ = sc.fetch_spark(["SPY", "QQQ"], rng, "1d", pause)
        for sym, s_ in idx_data.items():
            bars = sc.daily_closes(s_, now_utc)
            if len(bars) > 260:
                index[sym] = index_stats([b[0].isoformat() for b in bars], np.array([b[1] for b in bars], float))
    except Exception as exc:  # noqa: BLE001
        log(f"  index funds unavailable: {exc}")
    log(f"enriching up to {enrich_max} matches from their own daily OHLCV charts...")
    ok = 0
    for i, h in enumerate(hits[:enrich_max]):
        try:
            e = enrich(h["symbol"], sc, now_utc)
            if e:
                if e["bar_date"] != last:
                    e["stale"] = True
                h.update(e)
                ok += 1
        except Exception as exc:  # noqa: BLE001 - one symbol never stops the scan
            h["enrich_error"] = str(exc)[:120]
        if pause:
            time.sleep(pause)
        if (i + 1) % 100 == 0:
            log(f"  {i + 1}/{min(len(hits), enrich_max)}")
    counts["enriched"] = ok
    by = {k: sum(k in h["setups"] for h in hits) for k in SETUPS}
    return {"generated": now_utc.isoformat(timespec="seconds") + "Z", "as_of": last, "counts": counts,
            "min_price": min_price, "min_volume": min_volume, "by_setup": by, "labels": LABELS,
            "base_rates": rates, "index": index, "hits": hits}
