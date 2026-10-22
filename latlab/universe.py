"""Which markets to watch: the most traded Binance USDT pairs, minus stablecoins and leveraged tokens."""

from __future__ import annotations

import aiohttp

from .feeds.exchanges import ASSETS, BINANCE_REST

STABLE = {"USDC", "FDUSD", "TUSD", "DAI", "USDP", "BUSD", "EUR", "EURI", "AEUR", "USD1", "XUSD", "PYUSD", "USDE",
          "RLUSD", "BFUSD", "USDS", "GBP", "TRY", "BRL", "U", "USD"}
LEVERAGED = ("UP", "DOWN", "BULL", "BEAR")


def pick(tickers: list[dict], n: int) -> list[str]:
    """Top ``n`` base assets by 24 h USDT volume. Always includes the core six."""
    rows = []
    for t in tickers:
        s = t.get("symbol", "")
        if not s.endswith("USDT"):
            continue
        base = s[:-4]
        if base in STABLE or (base.endswith(LEVERAGED) and len(base) > 4):
            continue
        if len(base) >= 5 and base.endswith("B"):   # tokenised stocks and ETFs (e.g. CRCLB, SOXLB), not crypto
            continue
        try:
            vol = float(t.get("quoteVolume") or 0)
            last = float(t.get("lastPrice") or 0)
        except ValueError:
            continue
        if vol <= 0 or last <= 0:
            continue
        rows.append((vol, base))
    rows.sort(reverse=True)
    out = list(ASSETS)
    for _, b in rows:
        if len(out) >= max(n, len(ASSETS)):
            break
        if b not in out:
            out.append(b)
    return out


async def top_assets(n: int = 30) -> list[str]:
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{BINANCE_REST}/api/v3/ticker/24hr", timeout=aiohttp.ClientTimeout(total=15)) as r:
                r.raise_for_status()
                return pick(await r.json(), n)
    except Exception:  # noqa: BLE001 — offline or restricted: fall back to the core six
        return list(ASSETS)
