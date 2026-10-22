"""Order execution.

PaperBroker: an order "arrives" ``latency`` seconds after it is sent and fills at the price on the
book at that moment (ask for buys, bid for sells), limited to the size shown, minus the taker fee.
Nothing is sent anywhere.

CcxtBroker: real market orders on any exchange the ccxt library supports (Binance, OKX, Bybit,
Kraken, Coinbase, ...), on the exchange's testnet/sandbox or live. Keys come from latlab.connect.
"""

from __future__ import annotations

import asyncio
import heapq
import itertools
import time
from dataclasses import dataclass, field
from typing import Callable

from .state import MarketState


@dataclass
class Order:
    id: int
    venue: str
    asset: str
    side: str               # BUY or SELL
    usdt: float = 0.0       # for BUY: how much to spend
    qty: float = 0.0        # for SELL: how much to sell
    sent_at: float = 0.0
    pos_id: int = 0


@dataclass
class Fill:
    order: Order
    ok: bool
    qty: float = 0.0
    price: float = 0.0
    fee_usdt: float = 0.0
    at: float = 0.0
    error: str = ""
    latency_ms: float | None = None


QUOTE_CCY = {"coinbase": "USD", "kraken": "USD"}  # everything else trades against USDT


class PaperBroker:
    kind = "paper"

    def __init__(self, state: MarketState, fees_bps: dict[str, float], latency_s: float = 0.15):
        self.state, self.fees, self.latency = state, fees_bps, latency_s
        self.pending: list = []
        self._seq = itertools.count()

    def submit(self, order: Order, t: float, on_fill: Callable[[Fill], None]) -> None:
        heapq.heappush(self.pending, (t + self.latency, next(self._seq), order, on_fill))

    def tick(self, t: float) -> None:
        while self.pending and self.pending[0][0] <= t:
            due, _, o, cb = heapq.heappop(self.pending)
            q = self.state.fresh(o.venue, o.asset, 3.0)
            if q is None:
                cb(Fill(o, False, at=due, error="no fresh price")); continue
            fee = self.fees.get(o.venue, 10) / 1e4
            if o.side == "BUY":
                px = q.ask
                qty = o.usdt / px
                if q.ask_qty:
                    qty = min(qty, q.ask_qty)
            else:
                px, qty = q.bid, o.qty
            cb(Fill(o, True, qty=qty, price=px, fee_usdt=qty * px * fee, at=due, latency_ms=self.latency * 1000))


class CcxtBroker:
    """Real orders through ccxt's async API. One warm connection per exchange."""

    def __init__(self, venue: str, creds: dict, sandbox: bool):
        import ccxt.async_support as ccxt  # imported lazily: paper mode needs no ccxt

        self.venue = venue
        self.kind = "testnet" if sandbox else "live"
        cls = getattr(ccxt, venue)
        self.ex = cls({"apiKey": creds["key"], "secret": creds["secret"], "password": creds.get("password"),
                       "enableRateLimit": True, "options": {"defaultType": "spot"}})
        if sandbox:
            self.ex.set_sandbox_mode(True)
        self.ready = asyncio.Event()

    async def start(self):
        await self.ex.load_markets()
        self.ready.set()

    async def close(self):
        await self.ex.close()

    def market(self, asset: str) -> str:
        return f"{asset}/{QUOTE_CCY.get(self.venue, 'USDT')}"

    async def balance(self) -> dict:
        b = await self.ex.fetch_balance()
        return {k: v for k, v in (b.get("free") or {}).items() if v}

    def submit(self, order: Order, t: float, on_fill: Callable[[Fill], None]) -> None:
        asyncio.get_running_loop().create_task(self._run(order, on_fill))

    def tick(self, t: float) -> None:  # fills arrive asynchronously
        pass

    async def _run(self, o: Order, cb):
        t0 = time.perf_counter()
        sym = self.market(o.asset)
        try:
            await self.ready.wait()
            if o.side == "BUY":
                if self.ex.has.get("createMarketBuyOrderWithCost"):
                    r = await self.ex.create_market_buy_order_with_cost(sym, o.usdt)
                else:
                    tk = await self.ex.fetch_ticker(sym)
                    amt = float(self.ex.amount_to_precision(sym, o.usdt / tk["ask"]))
                    r = await self.ex.create_market_order(sym, "buy", amt)
            else:
                amt = float(self.ex.amount_to_precision(sym, o.qty))
                r = await self.ex.create_market_order(sym, "sell", amt)
            if not r.get("filled") and r.get("id"):
                r = await self.ex.fetch_order(r["id"], sym)
            filled = float(r.get("filled") or 0)
            price = float(r.get("average") or r.get("price") or 0)
            fee_usdt, fee_base = 0.0, 0.0
            for f in r.get("fees") or ([r["fee"]] if r.get("fee") else []):
                if not f or f.get("cost") is None:
                    continue
                if f.get("currency") == o.asset:
                    fee_base += float(f["cost"]); fee_usdt += float(f["cost"]) * price
                elif f.get("currency") in ("USDT", "USD", "USDC"):
                    fee_usdt += float(f["cost"])
            qty = filled - fee_base if o.side == "BUY" else filled
            cb(Fill(o, filled > 0, qty=qty, price=price, fee_usdt=fee_usdt, at=time.time(),
                    error="" if filled > 0 else f"order not filled ({r.get('status')})",
                    latency_ms=(time.perf_counter() - t0) * 1000))
        except Exception as e:  # noqa: BLE001 — report every exchange error to the engine
            cb(Fill(o, False, at=time.time(), error=f"{type(e).__name__}: {e}"[:300],
                    latency_ms=(time.perf_counter() - t0) * 1000))
