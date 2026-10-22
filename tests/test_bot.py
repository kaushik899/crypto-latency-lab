"""Auto-trader: calibration, the cost filter, risk limits, the performance gate, and the credential store."""

import importlib
import math
import time

import numpy as np
import pytest

from latlab.bot.brokers import Order, PaperBroker
from latlab.bot.engine import Engine
from latlab.bot.ledger import Gate, Ledger
from latlab.bot.models import OnlineBeta
from latlab.bot.risk import RiskLimits, RiskManager
from latlab.bot.state import MarketState
from latlab.feeds.exchanges import Quote, parse_binance, parse_bybit
from latlab.universe import pick

T0 = 1_792_600_000.0


def test_online_beta_learns_and_refuses_noise():
    rng = np.random.default_rng(0)
    m = OnlineBeta()
    for _ in range(2000):
        x = rng.normal()
        m.update(x, 0.8 * x + rng.normal() * 0.5)
    assert m.ready and m.beta == pytest.approx(0.8, abs=0.05)
    noise = OnlineBeta()
    for _ in range(2000):
        noise.update(rng.normal(), rng.normal())
    assert not noise.ready and noise.predict(1.0) is None


def lagging_market(seconds=1800, lag_s=0.6, fee_bps=None, seed=1):
    """Binance random walk with sharp moves; OKX copies Binance ``lag_s`` later with its own small noise."""
    rng = np.random.default_rng(seed)
    dt = 0.05
    n = int(seconds / dt)
    r = rng.normal(0, 1.2e-4, n) + (rng.random(n) < 0.004) * rng.choice([-1, 1], n) * rng.uniform(15e-4, 40e-4, n)
    px = 80000 * np.exp(np.cumsum(r))
    k = int(lag_s / dt)
    quotes = []
    for i in range(n):
        t = T0 + i * dt
        quotes.append(Quote("binance", "BTC", px[i] - 0.5, px[i] + 0.5, None, t, 5.0, 5.0))
        j = max(0, i - k)
        o = px[j] * (1 + rng.normal(0, 2e-5))
        quotes.append(Quote("okx", "BTC", o - 0.5, o + 0.5, t, t + 0.001, 5.0, 5.0))
    return quotes


def run_engine(quotes, fees):
    from latlab.bot.strategies import LeadLag
    e = Engine(strategies=[LeadLag(venues=("okx",))], fees_bps=fees, ledger=Ledger(),
               limits=RiskLimits(max_trades_per_hour=10_000, max_open=5), paper_latency_s=0.1)
    for q in quotes:
        e.on_quote(q)
        e.tick(q.recv_ts)
    end = quotes[-1].recv_ts
    for k in range(40):
        e.tick(end + k * 0.5)
    return e


def test_engine_finds_a_real_lag_and_profits_when_costs_allow():
    e = run_engine(lagging_market(), {"binance": 0, "okx": 0})
    m = e.models[("leadlag", "okx", "BTC")]
    assert m.ready and m.beta > 0.3
    st = e.ledger.stats("paper", "leadlag")
    assert st["n"] > 20
    assert st["pnl"] > 0 and st["mean_bps"] > 0


def test_engine_stays_out_when_fees_eat_the_edge():
    e = run_engine(lagging_market(), {"binance": 10, "okx": 200})
    assert e.ledger.stats("paper", "leadlag")["n"] == 0
    assert e.counts["below_cost"] > 0


def test_paper_broker_waits_for_latency_and_uses_book_size():
    st = MarketState()
    st.update(Quote("binance", "ETH", 99.0, 100.0, None, T0, 1.0, 0.05))
    b = PaperBroker(st, {"binance": 10}, latency_s=0.2)
    got = []
    b.submit(Order(1, "binance", "ETH", "BUY", usdt=20), T0, got.append)
    b.tick(T0 + 0.1)
    assert got == []
    st.update(Quote("binance", "ETH", 100.0, 101.0, None, T0 + 0.15, 1.0, 0.05))
    b.tick(T0 + 0.2)
    f = got[0]
    assert f.ok and f.price == 101.0           # the price when the order arrives, not when it was sent
    assert f.qty == pytest.approx(0.05)        # capped at the displayed size
    assert f.fee_usdt == pytest.approx(0.05 * 101 * 0.001)


def test_risk_limits():
    rm = RiskManager(RiskLimits(max_open=1, max_daily_loss_usdt=5, max_spread_bps=10))
    q = Quote("binance", "BTC", 100, 100.05, None, T0, 1, 1)
    assert rm.check(T0, "BTC", [], q, live=False) is None
    assert "stale" in rm.check(T0 + 5, "BTC", [], q, live=False)
    wide = Quote("binance", "BTC", 100, 100.5, None, T0, 1, 1)
    assert "Spread" in rm.check(T0, "BTC", [], wide, live=False)

    class P:
        asset = "ETH"
    assert "open positions" in rm.check(T0, "BTC", [P()], q, live=False)
    rm.record_close(T0, -6)
    assert "Daily loss" in rm.check(T0, "BTC", [], q, live=True)
    rm.record_close(T0 + 86400, 0)              # next UTC day resets
    assert rm.check(T0 + 86400, "BTC", [], Quote("binance", "BTC", 100, 100.05, None, T0 + 86400, 1, 1), live=True) is None


def test_gate_opens_only_on_a_clear_paper_record():
    lg = Ledger()
    g = Gate(lg, min_trades=50, min_t=2.0)
    now = time.time()
    assert not g.status("x", now)["open"]
    rng = np.random.default_rng(3)
    for b in rng.normal(-2, 10, 60):
        lg.add(mode="paper", strategy="x", closed_at=now - 60, pnl=b / 1e3, pnl_bps=float(b))
    s = g.status("x", now)
    assert not s["open"] and "lose" in s["why"]
    for b in rng.normal(8, 5, 200):
        lg.add(mode="paper", strategy="y", closed_at=now - 60, pnl=b / 1e3, pnl_bps=float(b))
    assert g.status("y", now)["open"]


def test_universe_pick_filters_stables_and_leveraged():
    rows = [{"symbol": "PEPEUSDT", "quoteVolume": "9e9", "lastPrice": "0.00001"},
            {"symbol": "USDCUSDT", "quoteVolume": "8e9", "lastPrice": "1"},
            {"symbol": "BTCUPUSDT", "quoteVolume": "7e9", "lastPrice": "5"},
            {"symbol": "SUIUSDT", "quoteVolume": "6e9", "lastPrice": "3"},
            {"symbol": "ETHBTC", "quoteVolume": "9e12", "lastPrice": "0.03"}]
    got = pick(rows, 8)
    assert got[:6] == ["BTC", "ETH", "SOL", "XRP", "BNB", "DOGE"]
    assert got[6:] == ["PEPE", "SUI"]


def test_parsers_keep_book_sizes():
    (q,) = parse_binance({"stream": "x", "data": {"s": "BTCUSDT", "b": "1", "B": "2.5", "a": "1.1", "A": "0.5"}}, T0)
    assert (q.bid_qty, q.ask_qty) == (2.5, 0.5)
    (q,) = parse_bybit({"topic": "orderbook.1.BTCUSDT", "data": {"s": "BTCUSDT", "b": [["1", "3"]], "a": [["1.1", "4"]]}}, T0)
    assert (q.bid_qty, q.ask_qty) == (3.0, 4.0)


def test_credentials_store_is_private(tmp_path, monkeypatch):
    monkeypatch.setenv("LATLAB_HOME", str(tmp_path))
    import latlab.connect as c
    c = importlib.reload(c)
    monkeypatch.setattr(c, "_keyring", lambda: None)
    c.save("okx", "testnet", "k1", "s1", "p1")
    assert c.load("okx", "testnet") == {"key": "k1", "secret": "s1", "password": "p1"}
    assert (c.STORE.stat().st_mode & 0o077) == 0       # only the owner can read it
    assert "okx:testnet" in c.connected()
    assert c.remove("okx:testnet") and c.load("okx", "testnet") is None


def test_realistic_fees_keep_the_bot_out_and_the_gate_shut():
    """At 0.10% per side the 0.6 s lag is worth less than the round trip; the bot should rarely trade and
    its paper record should never be good enough to unlock real money."""
    total = 0
    for seed in (1, 2, 3):
        e = run_engine(lagging_market(seed=seed), {"binance": 10, "okx": 10})
        total += e.ledger.stats("paper", "leadlag")["n"]
        assert not e.gate.status("leadlag", time.time())["open"]
    assert total <= 6
