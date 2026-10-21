// Public, keyless market-data websockets for five spot exchanges, normalised to one quote shape.
// Every connector reconnects on its own; a venue that is unreachable from the viewer's network
// simply reports "down" and the rest keep working.

import type { AssetKey } from "./assets";

export type VenueKey = "binance" | "coinbase" | "kraken" | "okx" | "bybit";

export type Quote = {
  venue: VenueKey;
  asset: AssetKey;
  bid: number;
  ask: number;
  exchTs?: number; // exchange timestamp (ms), when the venue provides one
  recvTs: number; // local receive time (ms), clock-corrected
};

export type VenueInfo = {
  key: VenueKey;
  name: string;
  quote: "USDT" | "USD";
  takerBps: number; // entry-level spot taker fee
  color: string;
  symbols: Partial<Record<AssetKey, string>>;
};

// Entry-level spot taker fees, checked October 2026: Binance/OKX/Bybit 0.10%;
// Coinbase Advanced 0.50% for EU users (Sept 2026 schedule); Kraken Pro tier 1 0.80% (July 2026).
export const VENUES: VenueInfo[] = [
  { key: "binance", name: "Binance", quote: "USDT", takerBps: 10, color: "#D9B26A",
    symbols: { BTC: "btcusdt", ETH: "ethusdt", SOL: "solusdt", XRP: "xrpusdt", BNB: "bnbusdt", DOGE: "dogeusdt" } },
  { key: "coinbase", name: "Coinbase", quote: "USD", takerBps: 50, color: "#6E93D6",
    symbols: { BTC: "BTC-USD", ETH: "ETH-USD", SOL: "SOL-USD", XRP: "XRP-USD", BNB: "BNB-USD", DOGE: "DOGE-USD" } },
  { key: "kraken", name: "Kraken", quote: "USD", takerBps: 80, color: "#5FAE97",
    symbols: { BTC: "BTC/USD", ETH: "ETH/USD", SOL: "SOL/USD", XRP: "XRP/USD", BNB: "BNB/USD", DOGE: "DOGE/USD" } },
  { key: "okx", name: "OKX", quote: "USDT", takerBps: 10, color: "#E9E2D4",
    symbols: { BTC: "BTC-USDT", ETH: "ETH-USDT", SOL: "SOL-USDT", XRP: "XRP-USDT", BNB: "BNB-USDT", DOGE: "DOGE-USDT" } },
  { key: "bybit", name: "Bybit", quote: "USDT", takerBps: 10, color: "#DD6A48",
    symbols: { BTC: "BTCUSDT", ETH: "ETHUSDT", SOL: "SOLUSDT", XRP: "XRPUSDT", BNB: "BNBUSDT", DOGE: "DOGEUSDT" } },
];

export const venueInfo = (k: VenueKey) => VENUES.find((v) => v.key === k)!;

export type VenueStatus = "connecting" | "live" | "down";

type Handler = (q: Quote) => void;

function reverse(v: VenueInfo, sym: string): AssetKey | undefined {
  const s = sym.toLowerCase();
  return (Object.keys(v.symbols) as AssetKey[]).find((a) => v.symbols[a]!.toLowerCase() === s);
}

const iso = (s: string | undefined) => (s ? Date.parse(s) : undefined);

export class VenueConn {
  status: VenueStatus = "connecting";
  lastMsg = 0;
  private ws?: WebSocket;
  private ping?: number;
  private retry = 1000;
  private stopped = false;

  constructor(public info: VenueInfo, private onQuote: Handler, private now: () => number, private onStatus: () => void) {}

  start() {
    this.stopped = false;
    this.open();
  }

  private setStatus(s: VenueStatus) {
    if (this.status !== s) {
      this.status = s;
      this.onStatus();
    }
  }

  private emit(asset: AssetKey | undefined, bid: number, ask: number, exchTs?: number) {
    if (!asset || !(bid > 0) || !(ask > 0) || ask < bid) return;
    this.lastMsg = this.now();
    this.setStatus("live");
    this.onQuote({ venue: this.info.key, asset, bid, ask, exchTs, recvTs: this.lastMsg });
  }

  private open() {
    const v = this.info;
    const syms = Object.values(v.symbols) as string[];
    let url = "";
    let sub: unknown = null;
    switch (v.key) {
      case "binance":
        url = `wss://stream.binance.com:443/stream?streams=${syms.flatMap((s) => [`${s}@bookTicker`, `${s}@aggTrade`]).join("/")}`;
        break;
      case "coinbase":
        url = "wss://ws-feed.exchange.coinbase.com";
        sub = { type: "subscribe", product_ids: syms, channels: ["ticker"] };
        break;
      case "kraken":
        url = "wss://ws.kraken.com/v2";
        sub = { method: "subscribe", params: { channel: "ticker", symbol: syms, event_trigger: "bbo" } };
        break;
      case "okx":
        url = "wss://ws.okx.com/ws/v5/public";
        sub = { op: "subscribe", args: syms.map((instId) => ({ channel: "bbo-tbt", instId })) };
        break;
      case "bybit":
        url = "wss://stream.bybit.com/v5/public/spot";
        sub = { op: "subscribe", args: syms.map((s) => `orderbook.1.${s}`) };
        break;
    }
    let ws: WebSocket;
    try {
      ws = new WebSocket(url);
    } catch {
      this.setStatus("down");
      return this.reconnect();
    }
    this.ws = ws;
    const watchdog = window.setTimeout(() => { if (this.status !== "live") this.setStatus("down"); }, 8000);
    ws.onopen = () => {
      if (sub) ws.send(JSON.stringify(sub));
      if (v.key === "bybit") this.ping = window.setInterval(() => ws.send('{"op":"ping"}'), 18000);
      if (v.key === "okx") this.ping = window.setInterval(() => ws.send("ping"), 20000);
    };
    ws.onmessage = (ev) => {
      if (typeof ev.data !== "string" || ev.data === "pong") return;
      let m: any;
      try { m = JSON.parse(ev.data); } catch { return; }
      this.retry = 1000;
      this.parse(m);
    };
    ws.onclose = () => {
      window.clearTimeout(watchdog);
      if (this.ping) window.clearInterval(this.ping);
      if (!this.stopped) {
        this.setStatus("down");
        this.reconnect();
      }
    };
    ws.onerror = () => ws.close();
  }

  private reconnect() {
    if (this.stopped) return;
    window.setTimeout(() => this.open(), this.retry);
    this.retry = Math.min(this.retry * 2, 30000);
  }

  // Binance bookTicker carries no timestamp; its aggTrade stream does, so trade events feed the
  // delay estimate and book events feed prices.
  private lastBinanceTrade: Partial<Record<AssetKey, number>> = {};

  private parse(m: any) {
    const v = this.info;
    switch (v.key) {
      case "binance": {
        const d = m.data;
        if (!d?.s) return;
        const a = reverse(v, d.s);
        if (d.e === "aggTrade") {
          if (a) this.lastBinanceTrade[a] = d.E;
          this.onDelay?.(d.E);
          return;
        }
        return this.emit(a, +d.b, +d.a);
      }
      case "coinbase":
        if (m.type !== "ticker") return;
        return this.emit(reverse(v, m.product_id), +m.best_bid, +m.best_ask, iso(m.time));
      case "kraken":
        if (m.channel !== "ticker" || !Array.isArray(m.data)) return;
        for (const d of m.data) this.emit(reverse(v, d.symbol), +d.bid, +d.ask, iso(d.timestamp));
        return;
      case "okx":
        if (!m.arg || !Array.isArray(m.data)) return;
        for (const d of m.data) this.emit(reverse(v, m.arg.instId), +d.bids?.[0]?.[0], +d.asks?.[0]?.[0], +d.ts);
        return;
      case "bybit": {
        const d = m.data;
        if (!m.topic || !d?.s) return;
        return this.emit(reverse(v, d.s), +d.b?.[0]?.[0], +d.a?.[0]?.[0], m.cts ?? m.ts);
      }
    }
  }

  /** Set by the lab to receive exchange timestamps that don't come with a quote (Binance trades). */
  onDelay?: (exchTs: number) => void;

  stop() {
    this.stopped = true;
    this.ws?.close();
  }
}
