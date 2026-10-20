"""Public spot order-book feeds from five exchanges, normalised to one quote shape.

No accounts or API keys. Each venue has a pure ``parse_*`` function (unit-tested on real
captured messages) and shares one reconnecting websocket loop.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Callable

import websockets

log = logging.getLogger(__name__)

ASSETS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE"]

# Entry-level spot taker fees in basis points (checked October 2026).
TAKER_BPS = {"binance": 10, "coinbase": 50, "kraken": 80, "okx": 10, "bybit": 10}

SYMBOLS = {
    "binance": {a: f"{a.lower()}usdt" for a in ASSETS},
    "coinbase": {a: f"{a}-USD" for a in ASSETS},
    "kraken": {a: f"{a}/USD" for a in ASSETS},
    "okx": {a: f"{a}-USDT" for a in ASSETS},
    "bybit": {a: f"{a}USDT" for a in ASSETS},
}


@dataclass
class Quote:
    venue: str
    asset: str
    bid: float
    ask: float
    exch_ts: float | None  # seconds
    recv_ts: float         # seconds


def _rev(venue: str, sym: str) -> str | None:
    s = sym.lower()
    for a, v in SYMBOLS[venue].items():
        if v.lower() == s:
            return a
    return None


def _iso(s: str | None) -> float | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _q(venue, sym, bid, ask, ts, recv) -> Quote | None:
    a = _rev(venue, sym) if sym else None
    try:
        bid, ask = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if not a or bid <= 0 or ask < bid:
        return None
    return Quote(venue, a, bid, ask, ts, recv)


def parse_binance(m: dict, recv: float) -> list[Quote]:
    d = m.get("data") or {}
    if "b" in d and "a" in d and d.get("e") is None:
        q = _q("binance", d.get("s"), d["b"], d["a"], None, recv)
        return [q] if q else []
    return []


def parse_coinbase(m: dict, recv: float) -> list[Quote]:
    if m.get("type") != "ticker":
        return []
    q = _q("coinbase", m.get("product_id"), m.get("best_bid"), m.get("best_ask"), _iso(m.get("time")), recv)
    return [q] if q else []


def parse_kraken(m: dict, recv: float) -> list[Quote]:
    if m.get("channel") != "ticker":
        return []
    out = []
    for d in m.get("data") or []:
        q = _q("kraken", d.get("symbol"), d.get("bid"), d.get("ask"), _iso(d.get("timestamp")), recv)
        if q:
            out.append(q)
    return out


def parse_okx(m: dict, recv: float) -> list[Quote]:
    arg, data = m.get("arg") or {}, m.get("data")
    if not isinstance(data, list):
        return []
    out = []
    for d in data:
        bids, asks = d.get("bids") or [[d.get("bidPx")]], d.get("asks") or [[d.get("askPx")]]
        ts = float(d["ts"]) / 1000 if d.get("ts") else None
        q = _q("okx", arg.get("instId"), bids[0][0], asks[0][0], ts, recv)
        if q:
            out.append(q)
    return out


def parse_bybit(m: dict, recv: float) -> list[Quote]:
    d = m.get("data")
    if not m.get("topic") or not isinstance(d, dict) or not d.get("b") or not d.get("a"):
        return []
    ts = (m.get("cts") or m.get("ts"))
    q = _q("bybit", d.get("s"), d["b"][0][0], d["a"][0][0], ts / 1000 if ts else None, recv)
    return [q] if q else []


PARSERS: dict[str, Callable[[dict, float], list[Quote]]] = {
    "binance": parse_binance, "coinbase": parse_coinbase, "kraken": parse_kraken, "okx": parse_okx, "bybit": parse_bybit,
}


def connection(venue: str) -> tuple[str, dict | None]:
    syms = list(SYMBOLS[venue].values())
    if venue == "binance":
        return "wss://stream.binance.com:9443/stream?streams=" + "/".join(f"{s}@bookTicker" for s in syms), None
    if venue == "coinbase":
        return "wss://ws-feed.exchange.coinbase.com", {"type": "subscribe", "product_ids": syms, "channels": ["ticker"]}
    if venue == "kraken":
        return "wss://ws.kraken.com/v2", {"method": "subscribe",
                                         "params": {"channel": "ticker", "symbol": syms, "event_trigger": "bbo"}}
    if venue == "okx":
        return "wss://ws.okx.com:8443/ws/v5/public", {"op": "subscribe",
                                                      "args": [{"channel": "bbo-tbt", "instId": s} for s in syms]}
    if venue == "bybit":
        return "wss://stream.bybit.com/v5/public/spot", {"op": "subscribe", "args": [f"orderbook.1.{s}" for s in syms]}
    raise ValueError(venue)


async def stream(venue: str) -> AsyncIterator[Quote]:
    url, sub = connection(venue)
    parse = PARSERS[venue]
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(url, ping_interval=15, max_size=2**22) as ws:
                if sub:
                    await ws.send(json.dumps(sub))
                log.info("%s connected", venue)
                backoff = 1.0
                async for raw in ws:
                    recv = time.time()
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        continue
                    for q in parse(m, recv):
                        yield q
        except (OSError, websockets.WebSocketException) as e:
            log.warning("%s dropped (%s); retry in %.0fs", venue, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
