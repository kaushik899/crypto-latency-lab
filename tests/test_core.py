import json
import math

import numpy as np
import pandas as pd
import pytest

from latlab.fees import breakeven_gap, fee_per_share, net_edge_buy, taker_fee
from latlab.feeds.binance import stream_url
from latlab.feeds.polymarket import Book
from latlab.metrics import summarize
from latlab.pricing import SECONDS_PER_YEAR, EWMAVol, digital_delta, digital_prob, ewma_sigma_series
from latlab.sim import SimConfig, simulate
from latlab.strategy import StrategyParams, gap_lifetimes, run
from latlab.tape import Tape


# ---------------------------------------------------------------- pricing
def test_digital_at_the_money_is_just_below_half():
    p = digital_prob(100.0, 100.0, 900 / SECONDS_PER_YEAR, 0.5)
    assert 0.49 < p < 0.5  # the -sigma^2/2 drift nudges it below 0.5


def test_digital_settles_to_payoff():
    assert digital_prob(101, 100, 0.0, 0.5) == 1.0
    assert digital_prob(99, 100, 0.0, 0.5) == 0.0


def test_digital_monotone_in_spot_and_symmetric():
    tau = 300 / SECONDS_PER_YEAR
    xs = digital_prob(np.linspace(99, 101, 21), 100.0, tau, 0.6)
    assert np.all(np.diff(xs) > 0)
    up = digital_prob(100.2, 100, tau, 0.6)
    dn = digital_prob(100 / 1.002, 100, tau, 0.6)
    assert up + dn == pytest.approx(1.0, abs=2e-3)


def test_student_t_has_fatter_tails():
    tau = 300 / SECONDS_PER_YEAR
    far = 100 * math.exp(4 * 0.6 * math.sqrt(tau))  # four sigma away
    assert 1 - digital_prob(far, 100, tau, 0.6, dist="student") > 1 - digital_prob(far, 100, tau, 0.6)


def test_delta_matches_finite_difference():
    tau = 600 / SECONDS_PER_YEAR
    h = 0.01
    fd = (digital_prob(100 + h, 100, tau, 0.5) - digital_prob(100 - h, 100, tau, 0.5)) / (2 * h)
    assert digital_delta(100, 100, tau, 0.5) == pytest.approx(fd, rel=1e-3)


def test_ewma_vol_recovers_known_sigma():
    rng = np.random.default_rng(0)
    dt, sigma = 0.25, 0.8
    r = rng.standard_normal(200_000) * sigma * math.sqrt(dt / SECONDS_PER_YEAR)
    px = 100 * np.exp(np.cumsum(r))
    t = np.arange(len(px)) * dt
    v = EWMAVol(halflife_s=600, init_sigma=0.3)
    for ti, pi in zip(t[-20000:], px[-20000:]):
        v.update(ti, pi)
    assert v.sigma == pytest.approx(sigma, rel=0.1)
    assert ewma_sigma_series(t, px, halflife_s=600)[-1] == pytest.approx(sigma, rel=0.1)


# ---------------------------------------------------------------- fees
def test_fee_formula_matches_polymarket_docs():
    assert taker_fee(100, 0.5) == pytest.approx(1.75)      # 100 * 0.07 * 0.5 * 0.5
    assert taker_fee(100, 0.3) == pytest.approx(taker_fee(100, 0.7))
    assert fee_per_share(0.99) < fee_per_share(0.5)
    assert breakeven_gap(0.5) == pytest.approx(0.0175)
    assert net_edge_buy(0.55, 0.5) == pytest.approx(0.05 - 0.0175)


# ---------------------------------------------------------------- feeds (offline parts)
def test_binance_stream_url():
    url = stream_url(["BTC", "SOL"])
    assert "btcusdt@bookTicker/solusdt@bookTicker" in url


def test_book_snapshot_and_deltas():
    b = Book()
    b.snapshot([{"price": "0.48", "size": "100"}, {"price": "0.47", "size": "50"}],
               [{"price": "0.50", "size": "80"}])
    assert b.top() == (0.48, 100.0, 0.50, 80.0)
    b.change("BUY", 0.49, 10)
    b.change("SELL", 0.50, 0)
    b.change("SELL", 0.52, 30)
    assert b.top() == (0.49, 10.0, 0.52, 30.0)


# ---------------------------------------------------------------- simulation + strategy
@pytest.fixture(scope="module")
def tape():
    return simulate(SimConfig(hours=1.0, seed=3, assets=("BTC", "SOL"), windows=("5m", "15m")))


def test_sim_is_deterministic(tape):
    t2 = simulate(SimConfig(hours=1.0, seed=3, assets=("BTC", "SOL"), windows=("5m", "15m")))
    assert np.array_equal(tape.spot["BTC"], t2.spot["BTC"])
    assert np.array_equal(tape.markets[5].yes_ask, t2.markets[5].yes_ask)


def test_sim_books_are_valid(tape):
    for m in tape.markets:
        assert np.all(m.yes_bid < m.yes_ask)
        assert np.all((m.yes_bid >= 0.01) & (m.yes_ask <= 0.99))
        assert m.outcome in (0.0, 1.0)
        assert m.n == int(round((m.end - m.start) / tape.dt)) + 1


def test_capital_never_exceeds_bankroll(tape):
    p = StrategyParams(bankroll=300, max_stake=50, latency_ms=0)
    tr = run(tape, p)
    f = tr[tr.filled]
    assert len(f)
    events = sorted([(r.t_signal, r.cost) for r in f.itertuples()] + [(r.t_end, -r.cost) for r in f.itertuples()],
                    key=lambda x: (x[0], x[1]))
    locked = np.cumsum([c for _, c in events])
    assert locked.max() <= 300 + 1e-6


def test_more_latency_means_more_misses(tape):
    fast = run(tape, StrategyParams(latency_ms=0))
    slow = run(tape, StrategyParams(latency_ms=1500))
    assert (fast.reason == "moved").mean() < (slow.reason == "moved").mean()


def test_pnl_accounting(tape):
    tr = run(tape, StrategyParams(latency_ms=100))
    f = tr[tr.filled]
    recomputed = f.shares * (f.outcome - f.px_arrival) - f.fee
    assert np.allclose(recomputed, f.pnl)
    s = summarize(tr, 10_000, 1.0)
    assert s["pnl"] == pytest.approx(f.pnl.sum())
    assert 0 <= s["hit_rate"] <= 1


def test_gap_lifetimes_positive(tape):
    g = gap_lifetimes(tape, StrategyParams())
    assert (g.duration_s > 0).all()


def test_tape_roundtrip(tape, tmp_path):
    tape.save(tmp_path / "t")
    t2 = Tape.load(tmp_path / "t")
    assert set(t2.spot) == set(tape.spot)
    assert len(t2.markets) == len(tape.markets)
    assert np.array_equal(t2.markets[3].yes_bid, tape.markets[3].yes_bid)
    assert t2.markets[3].i0 == tape.markets[3].i0


def test_from_recording(tmp_path):
    t = np.arange(0, 400, 0.5) + 1_760_000_000
    ticks = pd.DataFrame({"ts": t, "asset": "BTC", "price": 100_000 + np.sin(np.arange(len(t)) / 30) * 50})
    books = pd.DataFrame({"ts": t[::4], "market_id": "btc-updown-5m-x", "yes_bid": 0.48, "bid_size": 100,
                          "yes_ask": 0.50, "ask_size": 120})
    ticks.to_csv(tmp_path / "ticks.csv", index=False)
    books.to_csv(tmp_path / "books.csv", index=False)
    (tmp_path / "markets.json").write_text(json.dumps([dict(
        market_id="btc-updown-5m-x", asset="BTC", kind="updown", label="BTC 5m", start=t[0] + 60,
        end=t[0] + 360, strike=None, meta={"window": "5m"})]))
    tp = Tape.from_recording(tmp_path / "ticks.csv", tmp_path / "books.csv", tmp_path / "markets.json", step=0.05)
    m = tp.markets[0]
    assert m.start == pytest.approx(60) and m.end == pytest.approx(360)
    assert m.outcome in (0.0, 1.0)
    assert np.nanmax(m.yes_ask) == 0.5
    run(tp, StrategyParams())  # runs end to end on recorded data
