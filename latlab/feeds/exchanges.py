"""Public spot order-book feeds from five exchanges, normalised to one quote shape.

No accounts or API keys. Each venue has a pure ``parse_*`` function (unit-tested on real
captured messages) and shares one reconnecting websocket loop. Any list of coins can be
streamed; Binance is usually given the widest list (top pairs by volume), the other venues
the core coins they all list.

``LATLAB_BINANCE_WS`` / ``LATLAB_BINANCE_REST`` switch Binance to its public market-data mirror
(``wss://data-stream.binance.vision``, ``https://data-api.binance.vision``), which also works
from regions where binance.com is restricted (used by the scheduled GitHub research run).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncIterator, Callable

import websockets

log = logging.getLogger(__name__)

ASSETS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE"]
VENUES = ["binance", "coinbase", "kraken", "okx", "bybit"]

# Entry-level spot taker fees in basis points (checked October 2026). Override per account in
# ~/.latlab/fees.json if you are on a better tier.
TAKER_BPS = {"binance": 10, "coinbase": 50, "kraken": 80, "okx": 10, "bybit": 10}

BINANCE_WS = os.environ.get("LATLAB_BINANCE_WS", "wss://stream.binance.com:443")
BINANCE_REST = os.environ.get("LATLAB_BINANCE_REST", "https://api.binance.com")

_allowed: set[str] = set(ASSETS)


def allow(assets) -> None:
    """Accept quotes for these coins (in addition to the core six)."""
    _allowed.update(a.upper() for a in assets)


def symbol(venue: str, asset: str) -> str:
    a = asset.upper()
    return {"binance": f"{a.lower()}usdt", "coinbase": f"{a}-USD", "kraken": f"{a}/USD",
            "okx": f"{a}-USDT", "bybit": f"{a}USDT"}[venue]


# kept for callers that want the core-six table
SYMBOLS = {v: {a: symbol(v, a) for a in ASSETS} for v in VENUES}


@dataclass
class Quote:
    venue: str
    asset: str
    bid: float
    ask: float
    exch_ts: float | None  # seconds
    recv_ts: float         # seconds
    bid_qty: float | None = None
    ask_qty: float | None = None

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self) -> float:
        return (self.ask - self.bid) / self.mid * 1e4


def _rev(venue: str, sym: str) -> str | None:
    s = sym.upper()
    if venue == "binance" or venue == "bybit":
        a = s[:-4] if s.endswith("USDT") else None
    elif venue == "coinbase":
        a = s[:-4] if s.endswith("-USD") else None
    elif venue == "kraken":
        a = s[:-4] if s.endswith("/USD") else None
    elif venue == "okx":
        a = s[:-5] if s.endswith("-USDT") else None
    else:
        a = None
    return a if a in _allowed else None


def _iso(s: str | None) -> float | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _q(venue, sym, bid, ask, ts, recv, bq=None, aq=None) -> Quote | None:
    a = _rev(venue, sym) if sym else None
    bid, ask = _f(bid), _f(ask)
    if not a or bid is None or ask is None or bid <= 0 or ask < bid:
        return None
    return Quote(venue, a, bid, ask, ts, recv, _f(bq), _f(aq))


def parse_binance(m: dict, recv: float) -> list[Quote]:
    d = m.get("data", m) or {}
    if "b" in d and "a" in d and d.get("e") is None:
        q = _q("binance", d.get("s"), d["b"], d["a"], None, recv, d.get("B"), d.get("A"))
        return [q] if q else []
    return []


def parse_coinbase(m: dict, recv: float) -> list[Quote]:
    if m.get("type") != "ticker":
        return []
    q = _q("coinbase", m.get("product_id"), m.get("best_bid"), m.get("best_ask"), _iso(m.get("time")), recv,
           m.get("best_bid_size"), m.get("best_ask_size"))
    return [q] if q else []


def parse_kraken(m: dict, recv: float) -> list[Quote]:
    if m.get("channel") != "ticker":
        return []
    out = []
    for d in m.get("data") or []:
        q = _q("kraken", d.get("symbol"), d.get("bid"), d.get("ask"), _iso(d.get("timestamp")), recv,
               d.get("bid_qty"), d.get("ask_qty"))
        if q:
            out.append(q)
    return out


def parse_okx(m: dict, recv: float) -> list[Quote]:
    arg, data = m.get("arg") or {}, m.get("data")
    if not isinstance(data, list):
        return []
    out = []
    for d in data:
        bids = d.get("bids") or [[d.get("bidPx"), d.get("bidSz")]]
        asks = d.get("asks") or [[d.get("askPx"), d.get("askSz")]]
        ts = float(d["ts"]) / 1000 if d.get("ts") else None
        q = _q("okx", arg.get("instId"), bids[0][0], asks[0][0], ts, recv,
               bids[0][1] if len(bids[0]) > 1 else None, asks[0][1] if len(asks[0]) > 1 else None)
        if q:
            out.append(q)
    return out


def parse_bybit(m: dict, recv: float) -> list[Quote]:
    d = m.get("data")
    if not m.get("topic") or not isinstance(d, dict) or not d.get("b") or not d.get("a"):
        return []
    ts = (m.get("cts") or m.get("ts"))
    q = _q("bybit", d.get("s"), d["b"][0][0], d["a"][0][0], ts / 1000 if ts else None, recv,
           d["b"][0][1] if len(d["b"][0]) > 1 else None, d["a"][0][1] if len(d["a"][0]) > 1 else None)
    return [q] if q else []


PARSERS: dict[str, Callable[[dict, float], list[Quote]]] = {
    "binance": parse_binance, "coinbase": parse_coinbase, "kraken": parse_kraken, "okx": parse_okx, "bybit": parse_bybit,
}


def connection(venue: str, assets: list[str] | None = None) -> tuple[str, dict | None]:
    assets = assets or ASSETS
    syms = [symbol(venue, a) for a in assets]
    if venue == "binance":
        return f"{BINANCE_WS}/stream?streams=" + "/".join(f"{s}@bookTicker" for s in syms), None
    if venue == "coinbase":
        return "wss://ws-feed.exchange.coinbase.com", {"type": "subscribe", "product_ids": syms, "channels": ["ticker"]}
    if venue == "kraken":
        return "wss://ws.kraken.com/v2", {"method": "subscribe",
                                         "params": {"channel": "ticker", "symbol": syms, "event_trigger": "bbo"}}
    if venue == "okx":
        return "wss://ws.okx.com/ws/v5/public", {"op": "subscribe",
                                                 "args": [{"channel": "bbo-tbt", "instId": s} for s in syms]}
    if venue == "bybit":
        # Bybit allows at most 10 topics per subscribe message; the loop below splits it
        return "wss://stream.bybit.com/v5/public/spot", {"op": "subscribe", "args": [f"orderbook.1.{s}" for s in syms]}
    raise ValueError(venue)


async def stream(venue: str, assets: list[str] | None = None) -> AsyncIterator[Quote]:
    allow(assets or [])
    url, sub = connection(venue, assets)
    parse = PARSERS[venue]
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(url, ping_interval=15, max_size=2**22) as ws:
                if sub:
                    if venue == "bybit":
                        for i in range(0, len(sub["args"]), 10):
                            await ws.send(json.dumps({"op": "subscribe", "args": sub["args"][i:i + 10]}))
                    else:
                        await ws.send(json.dumps(sub))
                log.info("%s connected (%d markets)", venue, len(assets or ASSETS))
                backoff = 1.0
                async for raw in ws:
                    recv = time.time()
                    try:
                        m = json.loads(raw)
                    except ValueError:
                        continue
                    for q in parse(m, recv):
                        yield q
        except (OSError, websockets.WebSocketException, asyncio.TimeoutError) as e:
            log.warning("%s dropped (%s); retry in %.0fs", venue, e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
