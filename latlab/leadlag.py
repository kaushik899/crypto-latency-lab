"""Record real quotes from five exchanges and measure who follows whom.

    python -m latlab.leadlag record --minutes 60          # writes data/recordings/leadlag-<time>/quotes.csv
    python -m latlab.leadlag analyze data/recordings/leadlag-<time>

Analysis (same definitions as the website):
* feed delay: receive time minus exchange timestamp (clock-dependent; sync your clock)
* catch-up time: after Binance moves >= k bps within 1 s, the time until each venue covers half of
  that move, measured from when Binance itself crossed the half-way mark
* lead-lag: cross-correlation of 50 ms returns at shifts of -1 s .. +1 s
* gaps: buy on one venue's ask, sell on another's bid, minus the pair's rolling price difference and
  both taker fees; a gap counts as captured only if it is still positive ``latency`` ms later
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd

from .feeds.exchanges import ASSETS, TAKER_BPS, stream

ROOT = Path(__file__).resolve().parents[1]
STEP = 0.05
EVENT_BPS = {"BTC": 2.5, "ETH": 3, "SOL": 4, "XRP": 4, "BNB": 4, "DOGE": 5}
VENUES = list(TAKER_BPS)


# ---------------------------------------------------------------------------------- record
async def _record(folder: Path, minutes: float):
    folder.mkdir(parents=True, exist_ok=True)
    f = open(folder / "quotes.csv", "w", newline="")
    w = csv.writer(f)
    w.writerow(["recv_ts", "exch_ts", "venue", "asset", "bid", "ask"])
    n = 0

    async def pump(v):
        nonlocal n
        async for q in stream(v):
            w.writerow([f"{q.recv_ts:.4f}", f"{q.exch_ts:.4f}" if q.exch_ts else "", q.venue, q.asset, q.bid, q.ask])
            n += 1

    tasks = [asyncio.create_task(pump(v)) for v in VENUES]
    t_end = time.time() + minutes * 60
    try:
        while time.time() < t_end:
            await asyncio.sleep(5)
            f.flush()
            print(f"\r{n:,} quotes, {(t_end - time.time()) / 60:4.1f} min left", end="", flush=True)
    finally:
        for t in tasks:
            t.cancel()
        f.close()
    print(f"\nsaved {folder / 'quotes.csv'}")


# ---------------------------------------------------------------------------------- analyze
def grid(df: pd.DataFrame, step: float = STEP):
    """Forward-filled log mids and quotes on an even grid: dict[(asset, venue)] -> DataFrame."""
    t0, t1 = df.recv_ts.min(), df.recv_ts.max()
    g = np.arange(t0, t1, step)
    out = {}
    for (a, v), d in df.groupby(["asset", "venue"]):
        d = d.sort_values("recv_ts")
        idx = np.searchsorted(d.recv_ts.values, g, side="right") - 1
        ok = idx >= 0
        age = np.where(ok, g - d.recv_ts.values[np.clip(idx, 0, None)], np.inf)
        bid = np.where(ok, d.bid.values[np.clip(idx, 0, None)], np.nan)
        ask = np.where(ok, d.ask.values[np.clip(idx, 0, None)], np.nan)
        out[(a, v)] = pd.DataFrame({"t": g, "bid": bid, "ask": ask, "age": age})
    return g, out


def rolling_basis(x: np.ndarray, xb: np.ndarray, halflife_s: float = 30, step: float = STEP) -> np.ndarray:
    w = 1 - 0.5 ** (step / halflife_s)
    d = x - xb
    out = np.full_like(d, np.nan)
    b = np.nan
    for i, v in enumerate(d):
        if v == v:
            b = v if b != b else (1 - w) * b + w * v
        out[i] = b
    return out


def catchup(adj: dict[str, np.ndarray], asset: str, step: float = STEP, window_s=1.0, timeout_s=3.0):
    xb = adj["binance"]
    k = int(window_s / step)
    thr = EVENT_BPS.get(asset, 6) / 1e4
    lags = {v: [] for v in adj if v != "binance"}
    censored = {v: 0 for v in lags}
    last = -10**9
    i = k
    while i < len(xb):
        x0, x1 = xb[i - k], xb[i]
        if x0 == x0 and x1 == x1 and abs(x1 - x0) >= thr and i - last >= int(3 / step):
            last = i
            move, sgn = abs(x1 - x0), math.copysign(1, x1 - x0)
            ih = next((j for j in range(i - k, i + 1) if sgn * (xb[j] - x0) >= move / 2), i)
            for v in lags:
                s = adj[v][i - k]
                if s != s:
                    continue
                hit = None
                for j in range(i - k, min(len(xb), i + int(timeout_s / step))):
                    x = adj[v][j]
                    if x == x and sgn * (x - s) >= move / 2:
                        hit = j
                        break
                if hit is None:
                    censored[v] += 1
                elif hit >= i - k:
                    lags[v].append((hit - ih) * step * 1000)
        i += 1
    return lags, censored


def xcorr(xb: np.ndarray, xv: np.ndarray, max_lag: int = 20):
    db, dv = np.diff(xb), np.diff(xv)
    res = []
    for L in range(-max_lag, max_lag + 1):
        if L >= 0:
            a, b = dv[L:], db[: len(db) - L]
        else:
            a, b = dv[: len(dv) + L], db[-L:]
        m = np.isfinite(a) & np.isfinite(b)
        res.append(float(np.corrcoef(a[m], b[m])[0, 1]) if m.sum() > 50 else float("nan"))
    best = int(np.nanargmax(res)) if np.isfinite(res).any() else max_lag
    return (best - max_lag) * STEP * 1000, res


def gaps(raw: dict, basis: dict, asset: str, latency_ms: float, step=STEP, stale_s=2.0, notional=1000.0):
    lat = int(round(latency_ms / 1000 / step))
    n = len(next(iter(raw.values())))
    signals = fills = 0
    pnl = 0.0
    next_ok = {}
    for buy in raw:
        for sell in raw:
            if buy == sell:
                continue
            A, B = raw[buy], raw[sell]
            bA = basis.get(buy, np.zeros(n))
            bB = basis.get(sell, np.zeros(n))
            net = (np.log(B.bid.values) - np.log(A.ask.values) - (bB - bA)) * 1e4 - TAKER_BPS[buy] - TAKER_BPS[sell]
            fresh = (A.age.values < stale_s) & (B.age.values < stale_s)
            net = np.where(fresh, net, np.nan)
            for i in np.flatnonzero(net > 0):
                if i < next_ok.get((buy, sell), -1):
                    continue
                next_ok[(buy, sell)] = i + int(2 / step)
                signals += 1
                j = i + lat
                if j < n and net[j] > 0:
                    fills += 1
                    pnl += net[j] / 1e4 * notional
    return signals, fills, pnl


def analyze(folder: Path, latency_ms: float = 100) -> dict:
    df = pd.read_csv(folder / "quotes.csv")
    delay = (df.recv_ts - df.exch_ts).dropna() * 1000
    g, Q = grid(df)
    res = {"minutes": float((g[-1] - g[0]) / 60), "latency_ms": latency_ms,
           "feed_delay_ms": {v: float(delay[df.venue == v].median()) for v in VENUES if (df.venue == v).any()},
           "assets": {}}
    tot = {"signals": 0, "fills": 0, "pnl": 0.0}
    for a in ASSETS:
        if (a, "binance") not in Q:
            continue
        mid = {v: np.log((Q[(a, v)].bid + Q[(a, v)].ask).values / 2) for v in VENUES if (a, v) in Q}
        basis = {v: rolling_basis(mid[v], mid["binance"]) for v in mid if v != "binance"}
        adj = {v: mid[v] - basis.get(v, 0) for v in mid}
        lags, cens = catchup(adj, a)
        xc = {v: xcorr(adj["binance"], adj[v])[0] for v in adj if v != "binance"}
        s, f, p = gaps({v: Q[(a, v)] for v in mid}, basis, a, latency_ms)
        tot["signals"] += s; tot["fills"] += f; tot["pnl"] += p
        res["assets"][a] = {
            "catchup_ms": {v: {"median": float(np.median(x)) if x else None, "n": len(x), "censored": cens[v]}
                           for v, x in lags.items()},
            "xcorr_peak_ms": xc,
            "gaps": {"signals": s, "fills": f, "pnl": p},
        }
    res["gaps_total"] = tot
    (folder / "leadlag.json").write_text(json.dumps(res, indent=1))
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("record")
    r.add_argument("--minutes", type=float, default=30)
    z = sub.add_parser("analyze")
    z.add_argument("folder")
    z.add_argument("--latency-ms", type=float, default=100)
    a = ap.parse_args(argv)
    if a.cmd == "record":
        folder = ROOT / "data/recordings" / time.strftime("leadlag-%Y%m%d-%H%M%S")
        asyncio.run(_record(folder, a.minutes))
        print(f"next: python -m latlab.leadlag analyze {folder}")
    else:
        print(json.dumps(analyze(Path(a.folder), a.latency_ms), indent=1))


if __name__ == "__main__":
    main()
