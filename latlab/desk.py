"""Trade desk: a small local server that lets the website place orders in one click and run the auto-trader.

    # practice with Binance's free Spot Testnet (default, fake money)
    export BINANCE_TESTNET_KEY=...  BINANCE_TESTNET_SECRET=...
    python -m latlab.desk

    # real money — only with an API key that has Spot trading enabled and withdrawals DISABLED
    export BINANCE_API_KEY=...  BINANCE_API_SECRET=...
    python -m latlab.desk --live --max-order 25

Easiest: connect accounts once with ``python -m latlab.connect`` (keys are tested and stored privately),
then just run ``python -m latlab.desk``. Add ``--auto`` with ``--live`` to let the website's Auto-trader
use real money (it still only trades strategies whose paper record has opened the performance gate).

The desk prints a six-digit pairing code. Open the site, click "Trade now", choose "My Binance"
and enter the code. Why a local program and not the website itself: Binance refuses signed
requests from web pages (CORS), and a public website must never hold your secret key. Here
the key stays in this process on your computer; the site only sends "buy 25 USDT of BTC".

Safety rails: listens on 127.0.0.1 only; answers only the project site and localhost (CORS),
and only with the pairing code; caps every order at --max-order USDT; one order per second;
market orders only, on the six supported coins; every order is appended to data/trades/.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[1]
LIVE_URL = "https://api.binance.com"
TESTNET_URL = "https://testnet.binance.vision"
ASSETS = ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE"]
ALLOWED_ORIGINS = {"https://kaushik899.github.io"}
SITE_URL = "https://kaushik899.github.io/crypto-latency-lab/"
PORT = 8765

# Where each venue issues API keys, for the site's connect flow (opened in a new tab by the user).
KEY_PAGES = {
    "binance": ("https://www.binance.com/en/my/settings/api-management", "https://testnet.binance.vision/"),
    "okx": ("https://www.okx.com/account/my-api", "https://www.okx.com/demo-trading"),
    "bybit": ("https://www.bybit.com/app/user/api-management", "https://testnet.bybit.com/app/user/api-management"),
    "kraken": ("https://pro.kraken.com/app/settings/api", None),
    "coinbase": ("https://www.coinbase.com/settings/api", None),
    "trading212": ("https://app.trading212.com/", "https://app.trading212.com/"),
    "alpaca": ("https://app.alpaca.markets/brokerage/dashboard/overview", "https://app.alpaca.markets/paper/dashboard/overview"),
}


def sign(secret: str, query: str) -> str:
    """Binance SIGNED endpoint signature: hex HMAC-SHA256 of the query string."""
    return hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()


def order_params(asset: str, side: str, quote_usdt: float, ts_ms: int) -> dict:
    """Market order sized in USDT (Binance's ``quoteOrderQty`` works for both BUY and SELL)."""
    side = side.upper()
    if asset not in ASSETS:
        raise ValueError(f"unsupported coin {asset}")
    if side not in ("BUY", "SELL"):
        raise ValueError("side must be BUY or SELL")
    if not (quote_usdt > 0):
        raise ValueError("amount must be positive")
    return {"symbol": f"{asset}USDT", "side": side, "type": "MARKET", "quoteOrderQty": f"{quote_usdt:.2f}",
            "newOrderRespType": "FULL", "recvWindow": 5000, "timestamp": ts_ms}


def summarize_fill(resp: dict) -> dict:
    fills = resp.get("fills") or []
    qty = float(resp.get("executedQty") or 0)
    quote = float(resp.get("cummulativeQuoteQty") or 0)
    fees: dict[str, float] = {}
    for f in fills:
        fees[f["commissionAsset"]] = fees.get(f["commissionAsset"], 0.0) + float(f["commission"])
    return {"orderId": resp.get("orderId"), "status": resp.get("status"), "symbol": resp.get("symbol"),
            "side": resp.get("side"), "qty": qty, "quote": quote, "avgPrice": quote / qty if qty else None,
            "fees": fees, "fills": len(fills), "transactTime": resp.get("transactTime")}


class Binance:
    def __init__(self, key: str, secret: str, base: str):
        self.key, self.secret, self.base = key, secret, base
        self.offset_ms = 0
        self.session: aiohttp.ClientSession | None = None

    async def start(self):
        # keep one warm HTTPS connection so an order doesn't pay for a TLS handshake
        self.session = aiohttp.ClientSession(headers={"X-MBX-APIKEY": self.key},
                                             connector=aiohttp.TCPConnector(keepalive_timeout=60))
        await self.sync_clock()

    async def close(self):
        if self.session:
            await self.session.close()

    def now_ms(self) -> int:
        return int(time.time() * 1000) + self.offset_ms

    async def sync_clock(self):
        t0 = time.time()
        async with self.session.get(f"{self.base}/api/v3/time") as r:
            server = (await r.json())["serverTime"]
        t1 = time.time()
        self.offset_ms = int(server - (t0 + t1) / 2 * 1000)

    async def signed(self, method: str, path: str, params: dict) -> tuple[int, dict]:
        q = urlencode(params)
        url = f"{self.base}{path}?{q}&signature={sign(self.secret, q)}"
        async with self.session.request(method, url) as r:
            return r.status, await r.json(content_type=None)

    async def balances(self, assets) -> dict:
        status, data = await self.signed("GET", "/api/v3/account", {"recvWindow": 5000, "timestamp": self.now_ms(),
                                                                     "omitZeroBalances": "true"})
        if status != 200:
            raise RuntimeError(data.get("msg", status))
        want = set(assets) | {"USDT"}
        return {b["asset"]: float(b["free"]) for b in data.get("balances", []) if b["asset"] in want}

    async def market_order(self, asset: str, side: str, quote: float) -> tuple[int, dict]:
        return await self.signed("POST", "/api/v3/order", order_params(asset, side, quote, self.now_ms()))


class Desk:
    def __init__(self, client: Binance | None, mode: str, max_order: float, code: str, log_dir: Path,
                 extra_origins: set[str] | None = None, auto_live: bool = False, bot_factory=None,
                 stocks_client=None, max_stock_order: float = 200.0, stock_risk_pct: float = 1.0):
        self.client, self.mode, self.max_order, self.code = client, mode, max_order, code
        self.auto_live = auto_live
        self.bot = None
        self.bot_brokers: dict = {}
        self.bot_factory = bot_factory  # (mode) -> (runner, brokers); injectable for tests
        self.log_dir = log_dir
        self.origins = ALLOWED_ORIGINS | (extra_origins or set())
        self.last_order = 0.0
        self.lock = asyncio.Lock()
        from .stocks.desk import StockDesk
        self.stocks = StockDesk(self, stocks_client, max_stock_order, stock_risk_pct, log_dir)

    # --- HTTP plumbing -------------------------------------------------------------------
    def origin_ok(self, origin: str | None) -> bool:
        if not origin:
            return False
        return origin in self.origins or origin.startswith(("http://localhost:", "http://127.0.0.1:"))

    def cors(self, req: web.Request, resp: web.StreamResponse) -> web.StreamResponse:
        origin = req.headers.get("Origin")
        if self.origin_ok(origin):
            resp.headers.update({
                "Access-Control-Allow-Origin": origin, "Vary": "Origin",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, X-Desk-Code",
                "Access-Control-Allow-Private-Network": "true",
                "Access-Control-Max-Age": "600",
            })
        return resp

    def guard(self, req: web.Request) -> web.Response | None:
        if not self.origin_ok(req.headers.get("Origin")):
            return web.json_response({"error": "This desk only answers the Likely Profits site."}, status=403)
        if not secrets.compare_digest(req.headers.get("X-Desk-Code", ""), self.code):
            return web.json_response({"error": "Wrong pairing code. It is printed in the desk's terminal."}, status=401)
        return None

    async def options(self, req):
        return self.cors(req, web.Response(status=204))

    # --- endpoints -----------------------------------------------------------------------
    async def status(self, req):
        if (bad := self.guard(req)):
            return self.cors(req, bad)
        if self.client is None:
            return self.cors(req, web.json_response({"mode": self.mode, "maxOrder": self.max_order, "assets": ASSETS,
                                                     "balances": {}, "manual": False}))
        try:
            bal = await self.client.balances(ASSETS)
        except Exception as e:  # noqa: BLE001 — surface Binance's message to the user
            return self.cors(req, web.json_response({"error": f"Binance rejected the key: {e}"}, status=502))
        return self.cors(req, web.json_response({"mode": self.mode, "maxOrder": self.max_order, "assets": ASSETS,
                                                 "balances": bal, "manual": True}))

    async def order(self, req):
        if (bad := self.guard(req)):
            return self.cors(req, bad)
        t_in = time.perf_counter()
        try:
            body = await req.json()
            asset, side, quote = str(body["asset"]).upper(), str(body["side"]).upper(), float(body["quote"])
            order_params(asset, side, quote, 0)  # validate before touching the exchange
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
            return self.cors(req, web.json_response({"error": f"Bad order: {e}"}, status=400))
        if self.client is None:
            return self.cors(req, web.json_response(
                {"error": "No Binance account connected. Run: python -m latlab.connect"}, status=400))
        if quote > self.max_order:
            return self.cors(req, web.json_response(
                {"error": f"Order is above this desk's limit of {self.max_order:g} USDT. Restart with --max-order to change it."},
                status=400))
        async with self.lock:
            wait = 1.0 - (time.time() - self.last_order)
            if wait > 0:
                return self.cors(req, web.json_response({"error": "One order per second. Try again."}, status=429))
            self.last_order = time.time()
            t_sent = time.perf_counter()
            status, data = await self.client.market_order(asset, side, quote)
            t_ack = time.perf_counter()
        ok = status == 200
        result = {"ok": ok, "mode": self.mode,
                  "timing": {"desk_ms": round((t_sent - t_in) * 1000, 1),
                             "exchange_ms": round((t_ack - t_sent) * 1000, 1)}}
        result.update(summarize_fill(data) if ok else {"error": data.get("msg", f"HTTP {status}"), "code": data.get("code")})
        self.log(asset, side, quote, result)
        return self.cors(req, web.json_response(result, status=200 if ok else 400))

    def log(self, asset, side, quote, r):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"orders-{self.mode}.csv"
        new = not path.exists()
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "mode", "asset", "side", "quote_usdt", "ok", "status", "qty", "avg_price",
                            "exchange_ms", "error"])
            w.writerow([time.strftime("%Y-%m-%dT%H:%M:%S"), self.mode, asset, side, quote, r["ok"], r.get("status"),
                        r.get("qty"), r.get("avgPrice"), r["timing"]["exchange_ms"], r.get("error", "")])

    # --- accounts: connect any exchange or broker from the site ---------------------------
    def accounts_info(self) -> dict:
        from . import connect
        have = set(k.split(" ")[0] for k in connect.connected())
        out = []
        for ex, info in connect.EXCHANGES.items():
            live_url, test_url = KEY_PAGES.get(ex, (None, None))
            out.append({
                "id": ex, "name": info["name"], "market": "stocks" if ex in ("alpaca", "trading212") else "crypto",
                "practice": bool(info.get("testnet")), "passphrase": bool(info.get("password")),
                "help": info.get("help"), "practiceHelp": info.get("testnet_help"),
                "keyUrl": live_url, "practiceUrl": test_url,
                "connected": {"testnet": f"{ex}:testnet" in have, "live": f"{ex}:live" in have},
            })
        return {"deskMode": self.mode, "accounts": out}

    async def accounts_get(self, req):
        if (bad := self.guard(req)):
            return self.cors(req, bad)
        return self.cors(req, web.json_response(self.accounts_info()))

    async def accounts_connect(self, req):
        """The key travels browser -> 127.0.0.1 only. It is checked with the venue (keys that can
        withdraw are refused), saved on this computer, and put to use without a restart."""
        if (bad := self.guard(req)):
            return self.cors(req, bad)
        from . import connect
        try:
            b = await req.json()
            ex, mode = str(b["exchange"]).lower(), str(b["mode"]).lower()
            key, sec = str(b["key"]).strip(), str(b["secret"]).strip()
            pw = (str(b.get("passphrase") or "").strip() or None)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as e:
            return self.cors(req, web.json_response({"error": f"Missing field: {e}"}, status=400))
        info = connect.EXCHANGES.get(ex)
        if not info or mode not in ("testnet", "live") or (mode == "testnet" and not info.get("testnet")):
            return self.cors(req, web.json_response({"error": "Unknown account type."}, status=400))
        if not key or not sec or len(key) > 512 or len(sec) > 1024 or (info.get("password") and not pw):
            return self.cors(req, web.json_response({"error": "Paste the key and the secret" +
                                                     (" and the passphrase" if info.get("password") else "") + "."}, status=400))
        ok, msg, bal = await connect.verify(ex, mode, {"key": key, "secret": sec, "password": pw})
        if not ok:
            return self.cors(req, web.json_response({"ok": False, "error": msg}, status=400))
        where = connect.save(ex, mode, key, sec, pw)
        attached = await self.attach(ex, mode, key, sec)
        top = dict(sorted(bal.items(), key=lambda kv: -abs(kv[1]))[:6])
        return self.cors(req, web.json_response({"ok": True, "message": msg, "savedIn": where, "balances": top,
                                                 "attached": attached, "accounts": self.accounts_info()}))

    async def attach(self, ex: str, mode: str, key: str, sec: str) -> bool:
        """Use a freshly connected key at once when it matches this desk's mode."""
        if mode != self.mode:
            return False
        if ex == "binance":
            old = self.client
            self.client = Binance(key, sec, LIVE_URL if mode == "live" else TESTNET_URL)
            await self.client.start()
            if old:
                await old.close()
            return True
        if ex in ("alpaca", "trading212"):
            from .stocks.alpaca import Alpaca
            from .stocks.t212 import Trading212
            old = self.stocks.client
            self.stocks.client = (Alpaca if ex == "alpaca" else Trading212)(key, sec, live=mode == "live")
            await self.stocks.client.start()
            if old:
                await old.close()
            return True
        return True   # the auto-trader loads other venues' keys when it starts

    async def accounts_remove(self, req):
        if (bad := self.guard(req)):
            return self.cors(req, bad)
        from . import connect
        b = await req.json()
        name = f"{str(b.get('exchange', '')).lower()}:{str(b.get('mode', '')).lower()}"
        removed = connect.remove(name)
        if removed and name == f"binance:{self.mode}" and self.client:
            await self.client.close()
            self.client = None
        if removed and name in (f"alpaca:{self.mode}", f"trading212:{self.mode}") and self.stocks.client \
                and getattr(self.stocks.client, "kind", "alpaca") == name.split(":")[0]:
            await self.stocks.client.close()
            self.stocks.client = None
        return self.cors(req, web.json_response({"ok": removed, "accounts": self.accounts_info()}))

    # --- auto-trader -------------------------------------------------------------------
    def bot_info(self) -> dict:
        snap = self.bot.snapshot() if self.bot else {"running": False}
        from .bot.risk import KILL_FILE
        snap.update(deskMode=self.mode, autoLive=self.auto_live, killSwitch=KILL_FILE.exists(),
                    realAllowed=self.mode == "testnet" or self.auto_live)
        return snap

    async def bot_get(self, req):
        if (bad := self.guard(req)):
            return self.cors(req, bad)
        return self.cors(req, web.json_response(self.bot_info(), dumps=lambda o: json.dumps(o, default=float)))

    async def bot_start(self, req):
        if (bad := self.guard(req)):
            return self.cors(req, bad)
        body = await req.json() if req.can_read_body else {}
        real = bool(body.get("real"))
        if real and self.mode == "live" and not self.auto_live:
            return self.cors(req, web.json_response({"error": "This desk was started without --auto, so the "
                             "bot may not trade real money. Restart with: python -m latlab.desk --live --auto"}, status=400))
        if self.bot and self.bot.running:
            await self.bot.stop()
        mode = self.mode if real else "paper"
        try:
            self.bot, self.bot_brokers = await self.bot_factory(mode, body)
        except Exception as e:  # noqa: BLE001
            return self.cors(req, web.json_response({"error": f"Could not start: {e}"}, status=400))
        if real and not self.bot_brokers:
            return self.cors(req, web.json_response({"error": f"No {mode} account connected. Run: python -m latlab.connect"},
                                                    status=400))
        return self.cors(req, web.json_response(self.bot_info(), dumps=lambda o: json.dumps(o, default=float)))

    async def bot_stop(self, req):
        if (bad := self.guard(req)):
            return self.cors(req, bad)
        if self.bot:
            await self.bot.stop()
        return self.cors(req, web.json_response(self.bot_info(), dumps=lambda o: json.dumps(o, default=float)))

    async def bot_kill(self, req):
        if (bad := self.guard(req)):
            return self.cors(req, bad)
        from .bot.risk import KILL_FILE
        KILL_FILE.parent.mkdir(parents=True, exist_ok=True)
        KILL_FILE.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"))
        if self.bot:
            self.bot.kill()
            await self.bot.stop()
        return self.cors(req, web.json_response(self.bot_info(), dumps=lambda o: json.dumps(o, default=float)))

    async def bot_resume(self, req):
        if (bad := self.guard(req)):
            return self.cors(req, bad)
        from .bot.risk import KILL_FILE
        KILL_FILE.unlink(missing_ok=True)
        return self.cors(req, web.json_response(self.bot_info(), dumps=lambda o: json.dumps(o, default=float)))

    def app(self) -> web.Application:
        a = web.Application()
        a.router.add_route("OPTIONS", "/{tail:.*}", self.options)
        a.router.add_get("/status", self.status)
        a.router.add_post("/order", self.order)
        a.router.add_get("/bot", self.bot_get)
        a.router.add_post("/bot/start", self.bot_start)
        a.router.add_post("/bot/stop", self.bot_stop)
        a.router.add_post("/bot/kill", self.bot_kill)
        a.router.add_post("/bot/resume", self.bot_resume)
        a.router.add_get("/accounts", self.accounts_get)
        a.router.add_post("/accounts/connect", self.accounts_connect)
        a.router.add_post("/accounts/remove", self.accounts_remove)
        self.stocks.routes(a)
        return a


async def make_bot(mode: str, body: dict):
    from .bot.__main__ import real_brokers
    from .bot.risk import RiskLimits
    from .bot.runner import BotRunner
    limits = RiskLimits(order_usdt=float(body.get("order", 20)), max_daily_loss_usdt=float(body.get("maxDailyLoss", 10)),
                        max_open=int(body.get("maxOpen", 3)))
    r = BotRunner(mode, int(body.get("top", 30)), limits=limits)
    brokers = await real_brokers(mode, r.venues) if mode != "paper" else {}
    if mode == "paper" or brokers:
        await r.start(brokers)
    return r, brokers


async def serve(a):
    from . import connect
    live = a.live
    mode = "live" if live else "testnet"
    creds = connect.load("binance", mode)
    if live:
        print("\n  LIVE MODE — orders spend real money on your account.")
        print(f"  Manual orders are capped at {a.max_order:g} USDT. Use keys with withdrawals disabled.")
        print("  Auto-trader may use real money: " + ("YES (--auto); only gated strategies trade" if a.auto else
                                                       "no (add --auto to allow)") + "\n")
    client = None
    if creds:
        client = Binance(creds["key"], creds["secret"], LIVE_URL if live else TESTNET_URL)
        await client.start()
    else:
        print(f"  No Binance {mode} key found — manual Binance orders are off. Connect one with: python -m latlab.connect")
    stocks = None
    if (sc := connect.load("alpaca", mode)):
        from .stocks.alpaca import Alpaca
        stocks = Alpaca(sc["key"], sc["secret"], live=live)
        await stocks.start()
    elif (sc := connect.load("trading212", mode)):
        from .stocks.t212 import Trading212
        stocks = Trading212(sc["key"], sc["secret"], live=live)
        await stocks.start()
        print(f"  Trading 212 {'REAL-MONEY' if live else 'practice'} account connected.")
        print(f"  Alpaca {'LIVE' if live else 'paper'} account connected: stock tickets capped at "
              f"${a.max_stock_order:g}, {a.stock_risk_pct:g}% max loss, one approval per order.")
    else:
        print(f"  No Alpaca {'live' if live else 'paper'} key — stock orders are off. Connect: python -m latlab.connect")
    code = f"{secrets.randbelow(10**6):06d}"
    desk = Desk(client, mode, a.max_order, code, ROOT / "data/trades", auto_live=a.auto, bot_factory=make_bot,
                stocks_client=stocks, max_stock_order=a.max_stock_order, stock_risk_pct=a.stock_risk_pct)
    runner = web.AppRunner(desk.app())
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", a.port).start()
    print(f"  Trade desk ready on http://127.0.0.1:{a.port}  ({mode})")
    print(f"  Pairing code: {code}\n  Leave this window open while you trade. Ctrl-C to stop.")
    if not a.no_browser:
        # open the site already paired: the code rides in the URL fragment, which never leaves the browser
        import webbrowser
        url = f"{SITE_URL}#pair={code}" + (f"&port={a.port}" if a.port != PORT else "")
        try:
            webbrowser.open(url)
            print(f"  Opened {SITE_URL} in your browser, already paired.")
        except Exception:  # noqa: BLE001
            pass
    try:
        while True:
            await asyncio.sleep(300)
            if client:
                await client.sync_clock()
    finally:
        if desk.bot:
            await desk.bot.stop()
        for b in desk.bot_brokers.values():
            await b.close()
        if client:
            await client.close()
        if stocks:
            await stocks.close()
        await runner.cleanup()


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="trade real money (default: Binance Spot Testnet)")
    ap.add_argument("--max-order", type=float, default=50.0, help="largest single order in USDT (default 50)")
    ap.add_argument("--auto", action="store_true", help="with --live: let the website's Auto-trader use real money")
    ap.add_argument("--max-stock-order", type=float, default=200.0, help="largest stock order in $ (default 200)")
    ap.add_argument("--stock-risk-pct", type=float, default=1.0, help="max loss per stock trade, %% of equity")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--no-browser", action="store_true", help="do not open the site automatically")
    a = ap.parse_args(argv)
    try:
        asyncio.run(serve(a))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
