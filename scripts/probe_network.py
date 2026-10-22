"""Which exchange endpoints can this machine reach? Used to pick feeds for the scheduled research run."""
import asyncio, json, time
import aiohttp, websockets

REST = {
    "binance": "https://api.binance.com/api/v3/time",
    "binance_vision": "https://data-api.binance.vision/api/v3/time",
    "coinbase": "https://api.exchange.coinbase.com/time",
    "kraken": "https://api.kraken.com/0/public/Time",
    "okx": "https://www.okx.com/api/v5/public/time",
    "bybit": "https://api.bybit.com/v5/market/time",
}
WS = {
    "binance": ("wss://stream.binance.com:443/ws/btcusdt@bookTicker", None),
    "binance_vision": ("wss://data-stream.binance.vision/ws/btcusdt@bookTicker", None),
    "coinbase": ("wss://ws-feed.exchange.coinbase.com", {"type": "subscribe", "product_ids": ["BTC-USD"], "channels": ["ticker"]}),
    "kraken": ("wss://ws.kraken.com/v2", {"method": "subscribe", "params": {"channel": "ticker", "symbol": ["BTC/USD"], "event_trigger": "bbo"}}),
    "okx": ("wss://ws.okx.com/ws/v5/public", {"op": "subscribe", "args": [{"channel": "bbo-tbt", "instId": "BTC-USDT"}]}),
    "bybit": ("wss://stream.bybit.com/v5/public/spot", {"op": "subscribe", "args": ["orderbook.1.BTCUSDT"]}),
}

async def rest(s, k, u):
    t = time.time()
    try:
        async with s.get(u, timeout=10) as r:
            return k, f"{r.status} {int((time.time()-t)*1000)}ms {(await r.text())[:60]!r}"
    except Exception as e:
        return k, f"ERR {type(e).__name__} {e}"[:120]

async def ws(k, u, sub):
    try:
        async with websockets.connect(u, open_timeout=10) as w:
            if sub: await w.send(json.dumps(sub))
            for _ in range(5):
                m = await asyncio.wait_for(w.recv(), 10)
                if "BTC" in m or "btc" in m: return k, "OK " + m[:60]
            return k, "no data " + m[:60]
    except Exception as e:
        return k, f"ERR {type(e).__name__} {e}"[:120]

async def main():
    async with aiohttp.ClientSession() as s:
        r = await asyncio.gather(*(rest(s, k, u) for k, u in REST.items()))
    w = await asyncio.gather(*(ws(k, u, sub) for k, (u, sub) in WS.items()))
    print(json.dumps({"rest": dict(r), "ws": dict(w)}, indent=1))

asyncio.run(main())
