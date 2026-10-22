"""Stock lab: the walk-forward engine, sizing, Alpaca order bodies, and the desk's approval flow."""

import asyncio
import datetime as dt
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

from latlab.desk import Desk
from latlab.stocks import alpaca, daily, data, edge
from latlab.stocks import desk as sdesk

FX = Path(__file__).resolve().parents[1] / "vendor/day-trader-skill/tests/fixtures/trader-timemachine/TSLA-60d5m.json.gz"
SITE = "https://kaushik899.github.io"


@pytest.fixture(scope="module")
def tsla():
    _, days = data.bars_60d("TSLA", now_utc=dt.datetime(2030, 1, 1), offline=FX)
    return days


def bar(slot, o, h, l, c):
    return {"date": "2026-01-05", "slot": slot, "start": f"{9 + (30 + 5 * slot) // 60}:{(30 + 5 * slot) % 60:02d}",
            "o": o, "h": h, "l": l, "c": c, "v": 1000}


def flat_day(price=100.0):
    return [bar(i, price, price + 0.05, price - 0.05, price) for i in range(78)]


def test_fixture_sessions(tsla):
    full = data.full_sessions(tsla, "2030-01-01")
    assert len(full) >= 55 and all(len(b) == 78 for _, b in full)


def test_entry_is_next_bar_open_and_stop_first():
    D = flat_day()
    D[3] = bar(3, 100, 101.2, 100, 101)          # closes above the 5-minute range high (100.05)
    D[4] = bar(4, 101.5, 101.6, 99.0, 99.5)       # opens 101.5, then trades through both levels
    t = edge.run_rule("X", [("2026-01-05", D)], edge.Rule(5, "or_low", 1.0), edge.Costs(slippage_bps=0))
    assert len(t) == 1 and t[0].entry_slot == 4
    assert t[0].entry == pytest.approx(101.5)      # not the breakout close of 101
    assert t[0].outcome == "stop" and t[0].R < -0.99


def test_costs_make_a_flat_trade_lose():
    D = flat_day()
    D[3] = bar(3, 100, 100.3, 100, 100.2)
    t = edge.run_rule("X", [("2026-01-05", D)], edge.Rule(5, "or_low", 0.0), edge.Costs(slippage_bps=2))
    assert t and t[0].net_bps < -3.9


def test_walk_forward_rejects_noise(tsla):
    res = edge.walk_forward({"TSLA": data.full_sessions(tsla, "2030-01-01")})
    assert len(res["rules"]) == 18
    assert res["chosen"] == []          # one stock, 60 days: nothing clears t >= 3, and nothing should


def test_size_small_account():
    s = daily.size(400, 396, account=100, risk_pct=1)
    assert s["fractional"] and s["max_loss"] <= 1.0 and s["cost"] <= 100
    w = daily.size(20, 19.5, account=1000, risk_pct=1)
    assert w == {"shares": 20, "cost": 400, "max_loss": 10.0, "risk_per_share": 0.5, "fractional": False, "note": None}


def test_plan_matches_score(tsla):
    days = sorted(tsla)
    hits = 0
    for d in days[-8:-1]:
        D = tsla[d]
        for cut in range(2, 72):
            p = daily.plan_for("TSLA", D[:cut], data.full_sessions(tsla, d), daily.parse_rule("ORB5-low-1R"),
                               500, 1, None, replays=300)
            if p:
                pick = {"date": d, "symbol": "TSLA", "rule": p["rule"], "status": "WATCH",
                        "breakout_slot": p["breakout_slot"]}
                assert len(daily.score_day([pick], {"TSLA": D})) == 1
                hits += 1
                break
    assert hits >= 2


def test_alpaca_bodies():
    b = alpaca.bracket_body("AAPL", 3, 180.0, 190.0)
    assert b["order_class"] == "bracket" and b["qty"] == "3" and b["stop_loss"]["stop_price"] == "180.00"
    with pytest.raises(ValueError):
        alpaca.bracket_body("AAPL", 0.5, 180, 190)
    with pytest.raises(ValueError):
        alpaca.bracket_body("rm -rf", 1, 1, 2)
    buy, stp = alpaca.fractional_bodies("TSLA", 0.24391, 383.48)
    assert buy["qty"] == stp["qty"] == "0.2439" and stp["type"] == "stop"


class FakeAlpaca:
    live = False

    def __init__(self):
        self.sent = []

    async def account(self):
        return {"equity": "1000", "cash": "1000", "daytrade_count": 0, "status": "ACTIVE"}

    async def positions(self):
        return []

    async def open_orders(self):
        return []

    async def submit(self, body):
        self.sent.append(body)
        return 200, {"id": str(len(self.sent)), "status": "accepted"}


def test_desk_ticket_flow(tmp_path, monkeypatch):
    monkeypatch.setattr(sdesk, "_now_et", lambda: dt.datetime(2026, 10, 21, 10, 30))   # a Wednesday
    fake = FakeAlpaca()

    async def go():
        desk = Desk(None, "testnet", 50, "123456", tmp_path, stocks_client=fake, max_stock_order=200)
        c = TestClient(TestServer(desk.app()))
        await c.start_server()
        h = {"Origin": SITE, "X-Desk-Code": "123456"}
        try:
            r = await c.post("/stocks/order", json={"token": "x", "confirm": True}, headers=h)
            assert r.status == 400                                    # no ticket, no order
            r = await c.post("/stocks/preview", json={"symbol": "AAPL", "qty": 2, "entry": 50, "stop": 49,
                                                      "target": 52}, headers=h)
            t = await r.json()
            assert t["ok"] and t["token"] and fake.sent == []         # preview sends nothing
            r = await c.post("/stocks/order", json={"token": t["token"]}, headers=h)
            assert r.status == 400 and fake.sent == []                # needs confirm: true
            tok = t["token"]
            r = await c.post("/stocks/order", json={"token": tok, "confirm": True}, headers=h)
            assert r.status == 200 and len(fake.sent) == 1 and fake.sent[0]["order_class"] == "bracket"
            r = await c.post("/stocks/order", json={"token": tok, "confirm": True}, headers=h)
            assert r.status == 400 and len(fake.sent) == 1            # one ticket, one order
            r = await c.post("/stocks/preview", json={"symbol": "AAPL", "qty": 10, "entry": 50, "stop": 45,
                                                      "target": 60}, headers=h)
            t = await r.json()
            assert not t["ok"] and t["token"] is None                 # $500 > cap, $50 risk > 1%
            r = await c.post("/stocks/preview", json={"symbol": "AAPL", "qty": 1, "entry": 50, "stop": 49,
                                                      "target": 52}, headers={"Origin": "https://evil.example",
                                                                              "X-Desk-Code": "123456"})
            assert r.status == 403
        finally:
            await c.close()

    asyncio.run(go())


def test_ticket_rejects_weekend():
    c = sdesk.check_ticket("AAPL", 1, 50, 49, 52, 1000, 1, 200, dt.datetime(2026, 10, 24, 11, 0))
    assert not next(x for x in c if x["check"].startswith("market open"))["ok"]
