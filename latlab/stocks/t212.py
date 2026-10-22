"""Trading 212 (Invest and Stocks ISA accounts) for the local desk, through its official public API.

Practice: https://demo.trading212.com/api/v0   Real money: https://live.trading212.com/api/v0
Auth: HTTP Basic with the API key and secret made in the Trading 212 app (Settings -> API).
The API cannot move money out of the account.

Limits of the API that shape this module (docs.trading212.com, read 2026-10-22):
* Real-money accounts accept MARKET orders only, so no stop or target can rest at the broker.
  The desk therefore watches each position it opened and sells it at market when the approved stop
  or target is crossed, or at 15:55 New York time. That works only while the desk is running.
* Practice accounts accept stop orders too, so there the stop also rests at the broker.
* Orders are not idempotent: the desk never retries a failed order by itself.
* US shares trade in the account's own currency (EUR/GBP...): Trading 212 converts, with its FX fee.
"""

from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import time

import aiohttp

from .alpaca import SYMBOL

LIVE_URL = "https://live.trading212.com/api/v0"
DEMO_URL = "https://demo.trading212.com/api/v0"
FLAT_ET = dt.time(15, 55)


def t212_ticker(symbol: str) -> str:
    """AAPL -> AAPL_US_EQ, BRK.B -> BRK.B_US_EQ (Trading 212's naming for US equities)."""
    return f"{symbol.upper()}_US_EQ"


def market_body(symbol: str, qty: float) -> dict:
    if not SYMBOL.match(symbol):
        raise ValueError(f"bad symbol {symbol!r}")
    if qty == 0:
        raise ValueError("quantity is zero")
    return {"ticker": t212_ticker(symbol), "quantity": round(qty, 4), "extendedHours": False}


def stop_body(symbol: str, qty: float, stop: float) -> dict:
    return {"ticker": t212_ticker(symbol), "quantity": -abs(round(qty, 4)), "stopPrice": round(stop, 2),
            "timeValidity": "DAY"}


def _yahoo_last(symbols: list[str]) -> dict[str, dict]:
    """Last price and day change from Yahoo spark (the desk runs on the user's computer, which reaches Yahoo)."""
    from .toolkit import scanner
    sc = scanner()
    data, _ = sc.fetch_spark(symbols, "1d", "5m", 0)
    out = {}
    for s, v in data.items():
        m = v.get("meta") or {}
        last, prev = m.get("regularMarketPrice"), m.get("chartPreviousClose") or m.get("previousClose")
        out[s] = {"last": last, "prev_close": prev, "chg_pct": round((last / prev - 1) * 100, 2) if last and prev else None,
                  "time": m.get("regularMarketTime")}
    return out


def _now_et() -> dt.datetime:
    from .toolkit import timemachine
    return timemachine().to_eastern(dt.datetime.utcnow())


class Trading212:
    """Same interface the stock desk uses for Alpaca, plus place_ticket() and an exit watcher."""

    kind = "trading212"

    def __init__(self, key: str, secret: str, live: bool = False, price_fn=None, clock=None):
        self.base = LIVE_URL if live else DEMO_URL
        self.live = live
        token = base64.b64encode(f"{key}:{secret}".encode()).decode()
        self.headers = {"Authorization": f"Basic {token}", "Content-Type": "application/json"}
        self.http: aiohttp.ClientSession | None = None
        self.guards: dict[str, dict] = {}            # symbol -> {qty, stop, target}
        self.price_fn = price_fn or (lambda syms: asyncio.to_thread(_yahoo_last, syms))
        self.clock = clock or _now_et
        self.events: list[dict] = []
        self._watch: asyncio.Task | None = None

    async def start(self):
        self.http = aiohttp.ClientSession(headers=self.headers, timeout=aiohttp.ClientTimeout(total=10))
        self._watch = asyncio.create_task(self._watcher())

    async def close(self):
        if self._watch:
            self._watch.cancel()
        if self.http:
            await self.http.close()

    async def call(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        async with self.http.request(method, self.base + path, data=json.dumps(body) if body is not None else None) as r:
            try:
                data = await r.json(content_type=None)
            except Exception:  # noqa: BLE001
                data = {"message": (await r.text())[:300]}
            if isinstance(data, dict) and r.status >= 400 and "message" not in data:
                data["message"] = data.get("code") or data.get("detail") or f"HTTP {r.status}"
            return r.status, data

    # --- the interface the desk expects ------------------------------------------------
    async def account(self) -> dict:
        st, a = await self.call("GET", "/equity/account/summary")
        if st != 200:
            raise RuntimeError((a or {}).get("message", f"HTTP {st}") if isinstance(a, dict) else f"HTTP {st}")
        cash = a.get("cash") or {}
        total = a.get("totalValue") or 0
        return {"status": "ACTIVE", "currency": a.get("currency"), "cash": cash.get("availableToTrade"),
                "buying_power": cash.get("availableToTrade"), "equity": total, "last_equity": total,
                "daytrade_count": 0, "broker": "Trading 212"}

    async def positions(self) -> list[dict]:
        st, p = await self.call("GET", "/equity/positions")
        out = []
        for x in p if st == 200 and isinstance(p, list) else []:
            ins, w = x.get("instrument") or {}, x.get("walletImpact") or {}
            tk = ins.get("ticker") or ""
            out.append({"symbol": tk.replace("_US_EQ", ""), "qty": x.get("quantity"), "avg_entry_price": x.get("averagePricePaid"),
                        "current_price": x.get("currentPrice"), "market_value": w.get("currentValue"),
                        "unrealized_pl": w.get("unrealizedProfitLoss"), "currency": w.get("currency")})
        return out

    async def open_orders(self) -> list[dict]:
        st, o = await self.call("GET", "/equity/orders")
        keep = ("id", "ticker", "quantity", "type", "status", "stopPrice", "limitPrice", "creationTime")
        return [{k: x.get(k) for k in keep} for x in o] if st == 200 and isinstance(o, list) else []

    async def quote(self, symbol: str) -> dict:
        if not SYMBOL.match(symbol):
            raise ValueError("bad symbol")
        q = (await self.price_fn([symbol])).get(symbol)
        if not q or not q.get("last"):
            raise RuntimeError(f"no price for {symbol}")
        return {"symbol": symbol, "bid": None, "ask": q["last"], "last": q["last"], "time": q.get("time"), "feed": "yahoo"}

    async def snapshots(self, symbols: list[str]) -> dict:
        syms = [s for s in symbols if SYMBOL.match(s)][:100]
        return await self.price_fn(syms) if syms else {}

    def ticket_kind(self, fractional: bool) -> str:
        if self.live:
            return ("Trading 212 real money: market buy now; your desk then watches the price and sells at market at "
                    "the stop, the target, or 15:55 New York. Keep the desk running until it is closed.")
        return ("Trading 212 practice: market buy plus a resting stop order; the desk sells at the target or "
                "15:55 New York while it runs.")

    async def place_ticket(self, t: dict) -> dict:
        st, r = await self.call("POST", "/equity/orders/market", market_body(t["symbol"], t["qty"]))
        if st != 200:
            return {"ok": False, "orders": [r], "error": (r or {}).get("message", f"HTTP {st}")}
        orders = [r]
        stop_id = None
        if not self.live:
            st2, r2 = await self.call("POST", "/equity/orders/stop", stop_body(t["symbol"], t["qty"], t["stop"]))
            orders.append(r2)
            if st2 == 200:
                stop_id = (r2 or {}).get("id")
        self.guards[t["symbol"]] = {"qty": t["qty"], "stop": t["stop"], "target": t["target"], "stop_id": stop_id,
                                    "opened": time.time()}
        return {"ok": True, "orders": orders, "watching": True}

    async def sell(self, symbol: str, qty: float, why: str) -> tuple[int, object]:
        g = self.guards.pop(symbol, None)
        if g and g.get("stop_id"):
            await self.call("DELETE", f"/equity/orders/{g['stop_id']}")
        st, r = await self.call("POST", "/equity/orders/market", market_body(symbol, -abs(qty)))
        self.events.append({"t": time.time(), "symbol": symbol, "why": why, "ok": st == 200,
                            "error": None if st == 200 else (r or {}).get("message")})
        return st, r

    async def close_symbol(self, symbol: str) -> tuple[int, object]:
        pos = {p["symbol"]: p for p in await self.positions()}
        if symbol not in pos:
            self.guards.pop(symbol, None)
            return 404, {"message": f"no {symbol} position"}
        return await self.sell(symbol, float(pos[symbol]["qty"]), "closed by you")

    async def close_all(self) -> tuple[int, object]:
        res = [await self.sell(p["symbol"], float(p["qty"]), "closed by you") for p in await self.positions()
               if p["symbol"] in self.guards]
        return (200 if all(s == 200 for s, _ in res) else 207), [r for _, r in res]

    # --- the exit watcher: carries out the exits the user approved on the ticket ---------
    async def check_exits(self) -> None:
        if not self.guards:
            return
        held = {p["symbol"]: p for p in await self.positions()}
        for sym in [s for s in self.guards if s not in held and time.time() - self.guards[s]["opened"] > 30]:
            g = self.guards.pop(sym)          # gone: the resting stop filled, or it was sold in the app
            self.events.append({"t": time.time(), "symbol": sym, "why": "position no longer open", "ok": True})
        if not self.guards:
            return
        prices = await self.price_fn(list(self.guards))
        now_et = self.clock()
        for sym, g in list(self.guards.items()):
            last = (prices.get(sym) or {}).get("last")
            if now_et.time() >= FLAT_ET:
                await self.sell(sym, g["qty"], "15:55 New York: flat for the day")
            elif last and last <= g["stop"] and (self.live or not g.get("stop_id")):
                await self.sell(sym, g["qty"], f"stop {g['stop']:.2f} reached ({last:.2f})")
            elif last and last >= g["target"]:
                await self.sell(sym, g["qty"], f"target {g['target']:.2f} reached ({last:.2f})")

    async def _watcher(self):
        while True:
            try:
                await self.check_exits()
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001 - keep watching; the error is shown on the site
                self.events.append({"t": time.time(), "symbol": "-", "why": f"watcher error: {e}", "ok": False})
            await asyncio.sleep(5)
