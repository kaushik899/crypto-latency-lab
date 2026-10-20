"""Polymarket short-dated crypto Up/Down markets: discovery (Gamma REST) and order books (CLOB websocket).

Read-only. Public endpoints, no wallet or key. Note that Polymarket is blocked for users
in some jurisdictions (including France since July 2026); check what applies to you.

Discovery uses the event-slug convention ``<asset>-updown-<window>-<unix window start>``
(e.g. ``btc-updown-15m-1760970600``). If Polymarket changes the convention the
discovery step logs a warning and the rest of the system keeps running on spot alone.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

import aiohttp
import websockets

from ..config import ASSETS, POLY_CLOB_WS, POLY_GAMMA, WINDOWS

log = logging.getLogger(__name__)


@dataclass
class LiveMarket:
    market_id: str        # condition id / slug
    asset: str
    window: str
    label: str
    start: float          # unix seconds
    end: float
    up_token: str
    down_token: str
    strike: float | None = None   # window-open reference price, filled in by the engine
    meta: dict = field(default_factory=dict)


def _loads(x):
    return json.loads(x) if isinstance(x, str) else (x or [])


async def fetch_window(session: aiohttp.ClientSession, asset: str, window: str, start: int) -> LiveMarket | None:
    slug = f"{ASSETS[asset].polymarket_slug}-updown-{window}-{start}"
    async with session.get(f"{POLY_GAMMA}/events", params={"slug": slug}, timeout=10) as r:
        if r.status != 200:
            return None
        events = await r.json()
    if not events:
        return None
    ev = events[0]
    mk = (ev.get("markets") or [None])[0]
    if not mk:
        return None
    outcomes = [o.lower() for o in _loads(mk.get("outcomes"))]
    tokens = _loads(mk.get("clobTokenIds"))
    if len(tokens) != 2:
        return None
    up = tokens[outcomes.index("up")] if "up" in outcomes else tokens[0]
    down = tokens[1] if up == tokens[0] else tokens[0]
    L = WINDOWS[window]
    return LiveMarket(market_id=slug, asset=asset, window=window, label=ev.get("title", slug),
                      start=float(start), end=float(start + L), up_token=up, down_token=down,
                      meta={"condition_id": mk.get("conditionId"), "fees_enabled": mk.get("feesEnabled")})


async def discover(assets: list[str], windows: list[str], now: float | None = None,
                   ahead: int = 1) -> list[LiveMarket]:
    """Markets whose window is open now (plus ``ahead`` upcoming windows each)."""
    now = now or time.time()
    found = []
    async with aiohttp.ClientSession() as s:
        jobs = []
        for a in assets:
            if not ASSETS[a].polymarket_slug:
                continue
            for w in windows:
                L = WINDOWS[w]
                base = int(now // L * L)
                for k in range(ahead + 1):
                    jobs.append(fetch_window(s, a, w, base + k * L))
        for res in await asyncio.gather(*jobs, return_exceptions=True):
            if isinstance(res, LiveMarket):
                found.append(res)
            elif isinstance(res, Exception):
                log.debug("discovery error: %s", res)
    if not found:
        log.warning("no Polymarket Up/Down markets found — endpoint blocked or slug convention changed")
    return found


class Book:
    """Top of book for one outcome token, kept from 'book' snapshots and 'price_change' deltas."""

    def __init__(self):
        self.bids: dict[float, float] = {}
        self.asks: dict[float, float] = {}

    def snapshot(self, bids, asks):
        self.bids = {float(x["price"]): float(x["size"]) for x in bids}
        self.asks = {float(x["price"]): float(x["size"]) for x in asks}

    def change(self, side: str, price: float, size: float):
        book = self.bids if side.upper() == "BUY" else self.asks
        if size <= 0:
            book.pop(price, None)
        else:
            book[price] = size

    def top(self):
        bb = max(self.bids) if self.bids else float("nan")
        ba = min(self.asks) if self.asks else float("nan")
        return bb, self.bids.get(bb, 0.0), ba, self.asks.get(ba, 0.0)


async def books(markets: list[LiveMarket]) -> AsyncIterator[tuple[float, LiveMarket, tuple]]:
    """Yield ``(ts, market, (yes_bid, bid_size, yes_ask, ask_size))`` on every Up-token book change."""
    by_token = {m.up_token: m for m in markets}
    state = {tok: Book() for tok in by_token}
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(POLY_CLOB_WS, ping_interval=10, max_size=2**22) as ws:
                await ws.send(json.dumps({"assets_ids": list(by_token), "type": "market"}))
                backoff = 1.0
                async for raw in ws:
                    if raw in ("PONG", "pong"):
                        continue
                    msgs = json.loads(raw)
                    for msg in msgs if isinstance(msgs, list) else [msgs]:
                        touched = set()
                        et = msg.get("event_type")
                        if et == "book" and msg.get("asset_id") in state:
                            state[msg["asset_id"]].snapshot(msg.get("bids", []), msg.get("asks", []))
                            touched.add(msg["asset_id"])
                        elif et == "price_change":
                            for ch in msg.get("price_changes") or msg.get("changes") or []:
                                tok = ch.get("asset_id", msg.get("asset_id"))
                                if tok in state:
                                    state[tok].change(ch["side"], float(ch["price"]), float(ch["size"]))
                                    touched.add(tok)
                        for tok in touched:
                            yield time.time(), by_token[tok], state[tok].top()
        except (OSError, websockets.WebSocketException) as e:
            log.warning("polymarket stream dropped (%s); retrying in %.0fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
