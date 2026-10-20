"""Assets, venues and default parameters."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Asset:
    symbol: str          # "BTC"
    binance: str         # Binance spot pair, lower-case for stream names
    name: str
    sigma0: float        # prior annualised volatility used before the EWMA warms up
    polymarket_slug: str | None  # prefix of Polymarket's short-dated Up/Down events, if listed


ASSETS: dict[str, Asset] = {a.symbol: a for a in [
    Asset("BTC", "btcusdt", "Bitcoin", 0.50, "btc"),
    Asset("ETH", "ethusdt", "Ethereum", 0.65, "eth"),
    Asset("SOL", "solusdt", "Solana", 0.85, "sol"),
    Asset("XRP", "xrpusdt", "XRP", 0.80, "xrp"),
    Asset("BNB", "bnbusdt", "BNB", 0.55, None),
    Asset("DOGE", "dogeusdt", "Dogecoin", 1.00, None),
]}

# Short-dated Polymarket Up/Down windows (seconds). Event slugs look like
# "btc-updown-15m-<unix start>" — discovery in feeds/polymarket.py verifies them at runtime.
WINDOWS = {"5m": 300, "15m": 900, "1h": 3600}

BINANCE_WS = "wss://stream.binance.com:9443/stream?streams="
POLY_GAMMA = "https://gamma-api.polymarket.com"
POLY_CLOB_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
