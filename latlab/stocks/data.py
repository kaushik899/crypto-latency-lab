"""Stock data: the US universe, a liquidity ranking, and 60 days of 5-minute bars per stock.

Everything comes from free public sources through the vendored toolkit (Nasdaq Trader symbol
directories, Yahoo Finance spark and chart endpoints), with polite pauses and an on-disk cache.
"""

from __future__ import annotations

import datetime as dt
import gzip
import json
import time
from pathlib import Path

from .toolkit import scanner, timemachine

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "data" / "stocks" / "cache"

# Index ETFs are handy context and very liquid; the risk check allows US stocks and ETFs.
ETFS = ["SPY", "QQQ", "IWM", "DIA", "XLK", "XLF", "XLE", "SMH", "ARKK", "TLT"]


def ny_now(now_utc: dt.datetime | None = None) -> dt.datetime:
    tm = timemachine()
    return tm.to_eastern(now_utc or dt.datetime.utcnow())


def universe(max_age_days: float = 7.0) -> list[str]:
    """US common stocks and ADRs from Nasdaq Trader (the toolkit's filter), cached for a week."""
    sc = scanner()
    path = CACHE / "universe.json"
    if path.exists() and time.time() - path.stat().st_mtime < max_age_days * 86400:
        return json.loads(path.read_text())
    texts = {f: sc.http_get(sc.NASDAQ_URL + f) for f in sc.NASDAQ_FILES}
    kept, _, _ = sc.build_universe(texts)
    syms = [s for s, _ in kept]
    CACHE.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(syms))
    return syms


def liquid(symbols: list[str], top: int = 150, min_price: float = 5.0, max_price: float | None = None,
           pause: float = 0.3) -> list[dict]:
    """Rank by average daily dollar volume over the last 5 sessions (Yahoo spark, 20 symbols a request)."""
    sc = scanner()
    data, _ = sc.fetch_spark(symbols, "5d", "1d", pause)
    rows = []
    for sym, s in data.items():
        closes = [c for c in s["c"] if c]
        vols = [v for v in (s.get("v") or []) if v]
        meta = s.get("meta") or {}
        price = meta.get("regularMarketPrice") or (closes[-1] if closes else None)
        if not price or price < min_price or (max_price and price > max_price):
            continue
        if vols:
            dv = price * sum(vols) / len(vols)
        else:  # spark sometimes omits volume: fall back to the last session's
            dv = price * (meta.get("regularMarketVolume") or 0)
        if dv > 0:
            rows.append({"symbol": sym, "price": round(price, 2), "dollar_volume": dv})
    rows.sort(key=lambda r: -r["dollar_volume"])
    return rows[:top]


def bars_60d(sym: str, now_utc: dt.datetime | None = None, offline: Path | None = None,
             cache_minutes: float = 0) -> tuple[dict, dict]:
    """(meta, {date: [78 bars or fewer]}) for regular-session 5-minute bars, oldest first."""
    tm = timemachine()
    now_utc = now_utc or dt.datetime.utcnow()
    if offline:
        text = tm.read_offline(str(offline), sym)
    else:
        path = CACHE / "bars" / f"{sym}.json.gz"
        if cache_minutes and path.exists() and time.time() - path.stat().st_mtime < cache_minutes * 60:
            text = gzip.open(path, "rt").read()
        else:
            text = tm.fetch_chart(sym)
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt") as fh:
                fh.write(text)
    meta, bars, _ = tm.parse_bars(tm.chart_result(text, sym), now_utc)
    days = tm.split_sessions(bars)
    out = {d: [s[i] for i in sorted(s)] for d, s in days.items()}
    return meta, out


def full_sessions(days: dict, before: str) -> list[tuple[str, list[dict]]]:
    """Complete 78-bar sessions strictly before ``before`` (YYYY-MM-DD), oldest first."""
    tm = timemachine()
    return [(d, b) for d, b in sorted(days.items()) if d < before and len(b) == tm.SESSION_BARS
            and [x["slot"] for x in b] == list(range(tm.SESSION_BARS))]
