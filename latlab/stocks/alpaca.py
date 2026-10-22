"""A small Alpaca Trading API client (US stocks), used only by the local desk.

Paper: https://paper-api.alpaca.markets (free, open worldwide, fake money).
Live:  https://api.alpaca.markets.
Trading-API keys can place and cancel orders; they cannot move money out of the account.
"""

from __future__ import annotations

import math
import re

import aiohttp

PAPER_URL = "https://paper-api.alpaca.markets"
LIVE_URL = "https://api.alpaca.markets"
SYMBOL = re.compile(r"^[A-Z]{1,5}([.-][A-Z])?$")


def bracket_body(symbol: str, qty: float, stop: float, target: float, limit: float | None = None) -> dict:
    """Whole shares: one bracket order (entry + take-profit + stop-loss, OCO), day only."""
    if not SYMBOL.match(symbol):
        raise ValueError(f"bad symbol {symbol!r}")
    if qty < 1 or qty != int(qty):
        raise ValueError("bracket orders need whole shares")
    if not (0 < stop < target):
        raise ValueError("need 0 < stop < target")
    body = {"symbol": symbol, "qty": str(int(qty)), "side": "buy", "time_in_force": "day",
            "order_class": "bracket", "take_profit": {"limit_price": f"{target:.2f}"},
            "stop_loss": {"stop_price": f"{stop:.2f}"}}
    if limit:
        body.update(type="limit", limit_price=f"{limit:.2f}")
    else:
        body["type"] = "market"
    return body


def fractional_bodies(symbol: str, qty: float, stop: float) -> tuple[dict, dict]:
    """Fractional shares: a market buy, then a separate day stop for the same quantity."""
    if not SYMBOL.match(symbol):
        raise ValueError(f"bad symbol {symbol!r}")
    q = f"{math.floor(qty * 1e4) / 1e4:.4f}".rstrip("0").rstrip(".")
    if float(q) <= 0:
        raise ValueError("quantity rounds to zero")
    buy = {"symbol": symbol, "qty": q, "side": "buy", "type": "market", "time_in_force": "day"}
    stp = {"symbol": symbol, "qty": q, "side": "sell", "type": "stop", "stop_price": f"{stop:.2f}",
           "time_in_force": "day"}
    return buy, stp


class Alpaca:
    def __init__(self, key: str, secret: str, live: bool = False):
        self.base = LIVE_URL if live else PAPER_URL
        self.live = live
        self.headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self.http: aiohttp.ClientSession | None = None

    async def start(self):
        self.http = aiohttp.ClientSession(headers=self.headers, timeout=aiohttp.ClientTimeout(total=10))

    async def close(self):
        if self.http:
            await self.http.close()

    async def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        async with self.http.request(method, self.base + path, json=body) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:  # noqa: BLE001
                data = {"message": (await r.text())[:300]}
            return r.status, data

    async def account(self) -> dict:
        st, a = await self.call("GET", "/v2/account")
        if st != 200:
            raise RuntimeError((a or {}).get("message", f"HTTP {st}"))
        keep = ("status", "currency", "cash", "buying_power", "equity", "last_equity", "pattern_day_trader",
                "daytrade_count", "trading_blocked", "account_blocked", "shorting_enabled")
        return {k: a.get(k) for k in keep}

    async def positions(self) -> list[dict]:
        st, p = await self.call("GET", "/v2/positions")
        keep = ("symbol", "qty", "avg_entry_price", "current_price", "market_value", "unrealized_pl", "unrealized_plpc")
        return [{k: x.get(k) for k in keep} for x in p] if st == 200 else []

    async def open_orders(self) -> list[dict]:
        st, o = await self.call("GET", "/v2/orders?status=open&nested=true&limit=50")
        keep = ("id", "symbol", "qty", "side", "type", "order_class", "status", "limit_price", "stop_price",
                "submitted_at")
        return [{k: x.get(k) for k in keep} for x in o] if st == 200 else []

    async def submit(self, body: dict) -> tuple[int, dict]:
        return await self.call("POST", "/v2/orders", body)

    async def close_all(self) -> tuple[int, object]:
        return await self.call("DELETE", "/v2/positions?cancel_orders=true")

    async def close_symbol(self, symbol: str) -> tuple[int, object]:
        if not SYMBOL.match(symbol):
            raise ValueError("bad symbol")
        # cancel the symbol's open orders first so its stop does not fire after the close
        st, orders = await self.call("GET", f"/v2/orders?status=open&symbols={symbol}")
        for o in orders if st == 200 else []:
            await self.call("DELETE", f"/v2/orders/{o['id']}")
        return await self.call("DELETE", f"/v2/positions/{symbol}")
