"""Parsers tested on messages captured from the live feeds (October 2026), plus the lead-lag analysis
on synthetic data with a known delay."""

import numpy as np
import pandas as pd
import pytest

from latlab.feeds.exchanges import parse_binance, parse_bybit, parse_coinbase, parse_kraken, parse_okx
from latlab.leadlag import analyze

R = 1792530440.0


def test_binance_book_ticker():
    m = {"stream": "btcusdt@bookTicker", "data": {"u": 1, "s": "BTCUSDT", "b": "82882.00", "B": "7.9", "a": "82882.01", "A": "1.9"}}
    (q,) = parse_binance(m, R)
    assert (q.venue, q.asset, q.bid, q.ask, q.exch_ts) == ("binance", "BTC", 82882.0, 82882.01, None)
    assert parse_binance({"stream": "x", "data": {"e": "aggTrade", "s": "BTCUSDT", "p": "1"}}, R) == []


def test_coinbase_ticker():
    m = {"type": "ticker", "product_id": "BTC-USD", "best_bid": "82786.99", "best_ask": "82787.00",
         "time": "2026-10-20T21:07:20.109112Z"}
    (q,) = parse_coinbase(m, R)
    assert q.asset == "BTC" and q.bid == 82786.99 and abs(q.exch_ts - 1792530440.109) < 1e-3
    assert parse_coinbase({"type": "subscriptions"}, R) == []


def test_kraken_ticker():
    m = {"channel": "ticker", "type": "snapshot", "data": [{"symbol": "BTC/USD", "bid": 82787.1, "ask": 82787.2,
                                                             "timestamp": "2026-10-20T21:07:19.539724Z"}]}
    (q,) = parse_kraken(m, R)
    assert q.asset == "BTC" and q.ask == 82787.2


def test_okx_bbo_and_tickers():
    m = {"arg": {"channel": "bbo-tbt", "instId": "BTC-USDT"},
         "data": [{"asks": [["82905.8", "1", "0", "1"]], "bids": [["82905.7", "2", "0", "1"]], "ts": "1792530440068"}]}
    (q,) = parse_okx(m, R)
    assert q.bid == 82905.7 and q.ask == 82905.8 and q.exch_ts == pytest.approx(1792530440.068)
    t = {"arg": {"channel": "tickers", "instId": "BNB-USDT"}, "data": [{"bidPx": "740.3", "askPx": "740.4", "ts": "1792530440068"}]}
    assert parse_okx(t, R)[0].asset == "BNB"


def test_bybit_orderbook():
    m = {"topic": "orderbook.1.DOGEUSDT", "ts": 1792530437439, "type": "snapshot",
         "data": {"s": "DOGEUSDT", "b": [["0.08193", "12452.2"]], "a": [["0.08194", "6141.3"]]}, "cts": 1792530437438}
    (q,) = parse_bybit(m, R)
    assert q.asset == "DOGE" and q.exch_ts == pytest.approx(1792530437.438)
    assert parse_bybit({"success": True, "op": "subscribe"}, R) == []


def test_crossed_or_bad_quotes_dropped():
    assert parse_coinbase({"type": "ticker", "product_id": "BTC-USD", "best_bid": "10", "best_ask": "9"}, R) == []
    assert parse_coinbase({"type": "ticker", "product_id": "ZZZ-USD", "best_bid": "1", "best_ask": "2"}, R) == []


def test_analyze_recovers_known_delay(tmp_path):
    rng = np.random.default_rng(1)
    dt, lag_s = 0.05, 0.25
    t = R + np.arange(0, 600, dt)
    # bursty random walk so there are clear moves to time
    r = rng.standard_normal(len(t)) * 2e-5 + (rng.random(len(t)) < 0.003) * rng.choice([-1, 1], len(t)) * 8e-4
    px = 80000 * np.exp(np.cumsum(r))
    lagged = np.interp(t - lag_s, t, px)
    rows = []
    for i in range(len(t)):
        rows.append((t[i], np.nan, "binance", "BTC", px[i] - 0.5, px[i] + 0.5))
        rows.append((t[i], t[i], "coinbase", "BTC", lagged[i] * 0.999 - 0.5, lagged[i] * 0.999 + 0.5))
    pd.DataFrame(rows, columns=["recv_ts", "exch_ts", "venue", "asset", "bid", "ask"]).to_csv(tmp_path / "quotes.csv", index=False)
    res = analyze(tmp_path, latency_ms=100)
    cu = res["assets"]["BTC"]["catchup_ms"]["coinbase"]
    assert cu["n"] > 5
    assert cu["median"] == pytest.approx(250, abs=60)
    assert res["assets"]["BTC"]["xcorr_peak_ms"]["coinbase"] == pytest.approx(250, abs=60)
    assert res["feed_delay_ms"]["coinbase"] == pytest.approx(0, abs=1)
