"""Trade desk: request signing, order sizing, and the server's safety rails, against a fake exchange."""

import asyncio

import pytest
from aiohttp.test_utils import TestClient, TestServer

from latlab.desk import Desk, order_params, sign, summarize_fill

SITE = "https://kaushik899.github.io"


def test_signature_matches_binance_docs_example():
    secret = "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j"
    q = "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1&recvWindow=5000&timestamp=1499827319559"
    assert sign(secret, q) == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"


def test_order_params():
    p = order_params("BTC", "buy", 25, 123)
    assert p == {"symbol": "BTCUSDT", "side": "BUY", "type": "MARKET", "quoteOrderQty": "25.00",
                 "newOrderRespType": "FULL", "recvWindow": 5000, "timestamp": 123}
    for bad in [("SHIB", "BUY", 10), ("BTC", "HOLD", 10), ("BTC", "BUY", 0), ("BTC", "BUY", float("nan"))]:
        with pytest.raises(ValueError):
            order_params(*bad, 0)


def test_summarize_fill():
    r = {"symbol": "BTCUSDT", "orderId": 7, "status": "FILLED", "side": "BUY", "executedQty": "0.00030000",
         "cummulativeQuoteQty": "24.90", "transactTime": 1, "fills": [
             {"price": "83000", "qty": "0.0002", "commission": "0.0000002", "commissionAsset": "BTC"},
             {"price": "83000", "qty": "0.0001", "commission": "0.0000001", "commissionAsset": "BTC"}]}
    s = summarize_fill(r)
    assert s["avgPrice"] == pytest.approx(83000)
    assert s["fees"]["BTC"] == pytest.approx(3e-7)
    assert s["fills"] == 2


class FakeBinance:
    def __init__(self):
        self.orders = []

    async def balances(self, assets):
        return {"USDT": 1000.0, "BTC": 0.01}

    async def market_order(self, asset, side, quote):
        self.orders.append((asset, side, quote))
        return 200, {"symbol": f"{asset}USDT", "orderId": len(self.orders), "status": "FILLED", "side": side,
                     "executedQty": "0.0003", "cummulativeQuoteQty": str(quote), "fills": []}


def run(coro):
    return asyncio.run(coro)


async def _client(tmp_path, fake, max_order=50):
    desk = Desk(fake, "testnet", max_order, "123456", tmp_path)
    c = TestClient(TestServer(desk.app()))
    await c.start_server()
    return c


def test_desk_happy_path_and_log(tmp_path):
    async def go():
        fake = FakeBinance()
        c = await _client(tmp_path, fake)
        h = {"Origin": SITE, "X-Desk-Code": "123456"}
        r = await c.get("/status", headers=h)
        assert r.status == 200 and (await r.json())["mode"] == "testnet"
        assert r.headers["Access-Control-Allow-Origin"] == SITE
        r = await c.post("/order", json={"asset": "BTC", "side": "BUY", "quote": 25}, headers=h)
        body = await r.json()
        assert r.status == 200 and body["ok"] and body["status"] == "FILLED"
        assert "exchange_ms" in body["timing"]
        assert fake.orders == [("BTC", "BUY", 25.0)]
        await c.close()
    run(go())
    assert "BTC,BUY,25.0,True" in (tmp_path / "orders-testnet.csv").read_text()


def test_desk_refuses_other_sites_wrong_code_and_big_orders(tmp_path):
    async def go():
        fake = FakeBinance()
        c = await _client(tmp_path, fake, max_order=20)
        r = await c.post("/order", json={"asset": "BTC", "side": "BUY", "quote": 5},
                         headers={"Origin": "https://evil.example", "X-Desk-Code": "123456"})
        assert r.status == 403 and "Access-Control-Allow-Origin" not in r.headers
        r = await c.post("/order", json={"asset": "BTC", "side": "BUY", "quote": 5},
                         headers={"Origin": SITE, "X-Desk-Code": "000000"})
        assert r.status == 401
        r = await c.post("/order", json={"asset": "BTC", "side": "BUY", "quote": 25},
                         headers={"Origin": SITE, "X-Desk-Code": "123456"})
        assert r.status == 400 and "limit" in (await r.json())["error"]
        r = await c.post("/order", json={"asset": "PEPE", "side": "BUY", "quote": 5},
                         headers={"Origin": SITE, "X-Desk-Code": "123456"})
        assert r.status == 400
        assert fake.orders == []
        await c.close()
    run(go())


def test_desk_one_order_per_second(tmp_path):
    async def go():
        fake = FakeBinance()
        c = await _client(tmp_path, fake)
        h = {"Origin": SITE, "X-Desk-Code": "123456"}
        r1 = await c.post("/order", json={"asset": "ETH", "side": "SELL", "quote": 10}, headers=h)
        r2 = await c.post("/order", json={"asset": "ETH", "side": "SELL", "quote": 10}, headers=h)
        assert r1.status == 200 and r2.status == 429
        assert len(fake.orders) == 1
        await c.close()
    run(go())


def test_preflight_allows_private_network(tmp_path):
    async def go():
        c = await _client(tmp_path, FakeBinance())
        r = await c.options("/order", headers={"Origin": SITE, "Access-Control-Request-Method": "POST",
                                               "Access-Control-Request-Private-Network": "true"})
        assert r.status == 204
        assert r.headers["Access-Control-Allow-Private-Network"] == "true"
        assert "X-Desk-Code" in r.headers["Access-Control-Allow-Headers"]
        await c.close()
    run(go())


class FakeRunner:
    def __init__(self, mode):
        self.mode, self.running, self.killed = mode, True, False

    def snapshot(self):
        return {"running": self.running, "mode": self.mode}

    async def stop(self):
        self.running = False

    def kill(self):
        self.killed = True


def test_bot_endpoints_paper_and_real_rules(tmp_path, monkeypatch):
    import latlab.bot.risk as risk
    monkeypatch.setattr(risk, "KILL_FILE", tmp_path / "STOP")

    async def factory(mode, body):
        return FakeRunner(mode), ({"binance": object()} if mode != "paper" else {})

    async def go():
        h = {"Origin": SITE, "X-Desk-Code": "123456"}
        live = Desk(FakeBinance(), "live", 50, "123456", tmp_path, auto_live=False, bot_factory=factory)
        c = TestClient(TestServer(live.app()))
        await c.start_server()
        r = await c.post("/bot/start", json={"real": False}, headers=h)
        assert r.status == 200 and (await r.json())["mode"] == "paper"
        r = await c.post("/bot/start", json={"real": True}, headers=h)
        assert r.status == 400 and "--auto" in (await r.json())["error"]       # live desk without --auto
        r = await c.post("/bot/kill", headers=h)
        body = await r.json()
        assert body["killSwitch"] and not body["running"]
        r = await c.post("/bot/resume", headers=h)
        assert not (await r.json())["killSwitch"]
        r = await c.get("/bot", headers={"Origin": "https://evil.example", "X-Desk-Code": "123456"})
        assert r.status == 403
        await c.close()

        auto = Desk(FakeBinance(), "live", 50, "123456", tmp_path, auto_live=True, bot_factory=factory)
        c = TestClient(TestServer(auto.app()))
        await c.start_server()
        r = await c.post("/bot/start", json={"real": True}, headers=h)
        assert r.status == 200 and (await r.json())["mode"] == "live"
        await c.close()
    run(go())
