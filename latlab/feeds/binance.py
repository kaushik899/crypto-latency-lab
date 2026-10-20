"""Binance public market-data stream (no account, no API key).

Subscribes to ``<pair>@bookTicker`` for every asset over one combined websocket and
yields ``(local_ts, asset, mid, bid, ask)`` for each top-of-book update. Reconnects with
exponential backoff.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator

import websockets

from ..config import ASSETS, BINANCE_WS

log = logging.getLogger(__name__)


def stream_url(assets: list[str]) -> str:
    return BINANCE_WS + "/".join(f"{ASSETS[a].binance}@bookTicker" for a in assets)


async def book_tickers(assets: list[str]) -> AsyncIterator[tuple[float, str, float, float, float]]:
    pair_to_asset = {ASSETS[a].binance.upper(): a for a in assets}
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(stream_url(assets), ping_interval=20, max_size=2**20) as ws:
                log.info("binance connected: %s", ",".join(assets))
                backoff = 1.0
                async for raw in ws:
                    d = json.loads(raw).get("data", {})
                    a = pair_to_asset.get(d.get("s"))
                    if not a:
                        continue
                    bid, ask = float(d["b"]), float(d["a"])
                    yield time.time(), a, (bid + ask) / 2, bid, ask
        except (OSError, websockets.WebSocketException) as e:
            log.warning("binance stream dropped (%s); retrying in %.0fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
