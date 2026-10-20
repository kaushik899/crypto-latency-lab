// In-browser version of the lab: live Binance spot, a simulated Polymarket-style maker driven
// by that spot, and a paper trader with an adjustable latency. Mirrors latlab/sim.py and
// latlab/strategy.py so what you see here is the same mechanism the Python backtester measures.

import { ASSETS, WINDOWS, type AssetKey } from "./assets";
import { EwmaVol, TICK, digitalProb, feePerShare, randn, roundAsk, roundBid } from "./model";

export type FeedStatus = "connecting" | "live" | "simulated";

export type Regime = {
  key: string;
  label: string;
  refreshS: number; // maker re-quote interval (mean)
  makerLagMs: number; // maker's data delay
  compLatencyMs: number; // rival bot latency (median)
  compThreshold: number; // rival trigger, fee-inclusive
  feeRate: number;
};

export const REGIMES: Regime[] = [
  { key: "slow", label: "Slow market (2025-style)", refreshS: 2.0, makerLagMs: 300, compLatencyMs: 400, compThreshold: 0.02, feeRate: 0.07 },
  { key: "crowded", label: "Crowded market (today)", refreshS: 0.3, makerLagMs: 50, compLatencyMs: 20, compThreshold: 0.008, feeRate: 0.07 },
];

export type Sample = { t: number; fair: number; bid: number; ask: number };
export type Marker = { t: number; kind: "signal" | "fill" | "miss"; side: "YES" | "NO"; px: number; tArrive?: number };

export type Market = {
  id: string;
  asset: AssetKey;
  window: string;
  start: number;
  end: number;
  strike?: number;
  joinedLate: boolean;
  bid: number;
  ask: number;
  bidSize: number;
  askSize: number;
  fair?: number;
  edgeYes?: number;
  edgeNo?: number;
  nextRefresh: number;
  pendingSnipe?: number;
  history: Sample[];
  markers: Marker[];
  nextOk: number;
};

export type Trade = {
  id: number;
  market: string;
  asset: AssetKey;
  window: string;
  side: "YES" | "NO";
  tSignal: number;
  pxSignal: number;
  pxFill?: number;
  shares: number;
  fee: number;
  fair: number;
  filled: boolean;
  pnl?: number;
  end: number;
};

const HISTORY_S = 90;
const STEP_MS = 100;

type PriceHist = { t: number[]; p: number[] };

class Lab {
  status: FeedStatus = "connecting";
  regime: Regime = REGIMES[0];
  latencyMs = 150;
  entryEdge = 0.015;
  stake = 50;
  px: Partial<Record<AssetKey, number>> = {};
  prevPx: Partial<Record<AssetKey, number>> = {};
  lastTickAt: Partial<Record<AssetKey, number>> = {};
  hist: Record<string, PriceHist> = {};
  vol: Record<string, EwmaVol> = {};
  markets = new Map<string, Market>();
  trades: Trade[] = [];
  private tradeId = 0;
  private simTimer?: number;
  private loopTimer?: number;
  private listeners = new Set<() => void>();
  version = 0;

  start() {
    if (this.loopTimer) return;
    for (const a of ASSETS) {
      this.hist[a.key] = { t: [], p: [] };
      this.vol[a.key] = new EwmaVol(a.sigma0);
    }
    this.connect();
    this.loopTimer = window.setInterval(() => this.step(), STEP_MS);
  }

  subscribe(fn: () => void) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }

  private emit() {
    this.version++;
    this.listeners.forEach((f) => f());
  }

  // ---------------------------------------------------------------- spot feed
  private connect() {
    const streams = ASSETS.map((a) => `${a.pair}@bookTicker`).join("/");
    const fallback = window.setTimeout(() => this.useSimulatedSpot(), 5000);
    try {
      const ws = new WebSocket(`wss://stream.binance.com:9443/stream?streams=${streams}`);
      ws.onmessage = (ev) => {
        const d = JSON.parse(ev.data)?.data;
        if (!d?.s) return;
        const a = ASSETS.find((x) => x.pair === String(d.s).toLowerCase());
        if (!a) return;
        if (this.status !== "live") {
          window.clearTimeout(fallback);
          this.stopSim();
          this.status = "live";
        }
        this.onPrice(a.key, (parseFloat(d.b) + parseFloat(d.a)) / 2);
      };
      ws.onerror = ws.onclose = () => {
        if (this.status === "live") {
          // dropped after being live: retry, simulate meanwhile
          this.useSimulatedSpot();
          window.setTimeout(() => this.connect(), 5000);
        }
      };
    } catch {
      this.useSimulatedSpot();
    }
  }

  private useSimulatedSpot() {
    if (this.status === "simulated" && this.simTimer) return;
    this.status = "simulated";
    const z = () => randn();
    this.simTimer = window.setInterval(() => {
      const common = z();
      for (const a of ASSETS) {
        const last = this.px[a.key] ?? a.start;
        const sd = a.sigma0 * Math.sqrt(0.1 / (365 * 24 * 3600)) * 1.6;
        const jump = Math.random() < 0.0004 ? z() * 0.002 : 0;
        this.onPrice(a.key, last * Math.exp(sd * (0.8 * common + 0.6 * z()) + jump));
      }
    }, 100);
    this.emit();
  }

  private stopSim() {
    if (this.simTimer) window.clearInterval(this.simTimer);
    this.simTimer = undefined;
  }

  private onPrice(a: AssetKey, p: number) {
    const now = Date.now() / 1000;
    this.prevPx[a] = this.px[a];
    this.px[a] = p;
    this.lastTickAt[a] = now;
    this.vol[a].update(now, p);
    const h = this.hist[a];
    if (!h.t.length || now - h.t[h.t.length - 1] >= 0.05) {
      h.t.push(now);
      h.p.push(p);
      while (h.t.length && now - h.t[0] > HISTORY_S + 30) {
        h.t.shift();
        h.p.shift();
      }
    }
  }

  /** Spot price as it was ``lagS`` seconds ago. */
  private spotAt(a: AssetKey, t: number) {
    const h = this.hist[a];
    if (!h.t.length) return undefined;
    let i = h.t.length - 1;
    while (i > 0 && h.t[i] > t) i--;
    return h.p[i];
  }

  // ---------------------------------------------------------------- markets
  private ensureMarkets(now: number) {
    for (const a of ASSETS) {
      if (this.px[a.key] === undefined) continue;
      for (const w of WINDOWS) {
        const start = Math.floor(now / w.sec) * w.sec;
        const id = `${a.key}-${w.key}-${start}`;
        if (this.markets.has(id)) continue;
        const joinedLate = now - start > 2;
        const strike = this.px[a.key];
        this.markets.set(id, {
          id, asset: a.key, window: w.key, start, end: start + w.sec, strike, joinedLate,
          bid: 0.49, ask: 0.51, bidSize: 0, askSize: 0, nextRefresh: now, history: [], markers: [], nextOk: 0,
        });
      }
    }
    for (const [id, m] of this.markets) {
      if (now >= m.end) {
        this.settle(m);
        this.markets.delete(id);
      }
    }
  }

  private requote(m: Market, now: number) {
    const lagged = this.spotAt(m.asset, now - this.regime.makerLagMs / 1000);
    if (lagged === undefined || m.strike === undefined) return;
    const mf = digitalProb(lagged, m.strike, m.end - now, this.vol[m.asset].sigma);
    let bid = roundBid(mf - 0.01);
    let ask = roundAsk(mf + 0.01);
    if (ask <= bid + 1e-9) {
      if (bid >= 1 - 2 * TICK - 1e-9) {
        bid = 1 - 2 * TICK;
        ask = 1 - TICK;
      } else ask = bid + TICK;
    }
    m.bid = bid;
    m.ask = ask;
    m.bidSize = Math.round(250 * Math.exp(0.6 * randn()));
    m.askSize = Math.round(250 * Math.exp(0.6 * randn()));
    m.nextRefresh = now - Math.log(Math.random()) * this.regime.refreshS;
    m.pendingSnipe = undefined;
  }

  // ---------------------------------------------------------------- main loop
  private step() {
    const now = Date.now() / 1000;
    if (this.status === "connecting") return;
    this.ensureMarkets(now);
    for (const m of this.markets.values()) {
      const S = this.px[m.asset];
      if (S === undefined || m.strike === undefined) continue;
      if (now >= m.nextRefresh || (m.pendingSnipe && now >= m.pendingSnipe)) this.requote(m, now);
      const fair = digitalProb(S, m.strike, m.end - now, this.vol[m.asset].sigma);
      m.fair = fair;
      const fee = (p: number) => feePerShare(p, this.regime.feeRate);
      m.edgeYes = fair - m.ask - fee(m.ask);
      m.edgeNo = 1 - fair - (1 - m.bid) - fee(1 - m.bid);
      // rival latency bots
      if (!m.pendingSnipe && Math.max(m.edgeYes, m.edgeNo) > this.regime.compThreshold) {
        const lat = this.regime.compLatencyMs * Math.exp(0.6 * randn());
        m.pendingSnipe = now + lat / 1000;
      }
      m.history.push({ t: now, fair, bid: m.bid, ask: m.ask });
      while (m.history.length && now - m.history[0].t > HISTORY_S) m.history.shift();
      while (m.markers.length && now - m.markers[0].t > HISTORY_S) m.markers.shift();
      this.maybeTrade(m, now);
    }
    this.emit();
  }

  private maybeTrade(m: Market, now: number) {
    const tau = m.end - now;
    if (tau < 10 || now < m.nextOk || m.fair === undefined) return;
    const eY = m.edgeYes!, eN = m.edgeNo!;
    if (Math.max(eY, eN) <= this.entryEdge) return;
    const side = eY >= eN ? "YES" : "NO";
    const pxSignal = side === "YES" ? m.ask : 1 - m.bid;
    const fair = side === "YES" ? m.fair : 1 - m.fair;
    m.nextOk = now + 5;
    const marker: Marker = { t: now, kind: "signal", side, px: side === "YES" ? m.ask : m.bid, tArrive: now + this.latencyMs / 1000 };
    m.markers.push(marker);
    const id = ++this.tradeId;
    window.setTimeout(() => {
      const t2 = Date.now() / 1000;
      const live = this.markets.get(m.id);
      const pxArr = side === "YES" ? m.ask : 1 - m.bid;
      const size = side === "YES" ? m.askSize : m.bidSize;
      const ok = !!live && t2 < m.end && pxArr <= pxSignal + TICK + 1e-9 && size > 0;
      const shares = ok ? Math.floor(Math.min(this.stake / pxArr, size)) : 0;
      marker.kind = shares > 0 ? "fill" : "miss";
      this.trades.unshift({
        id, market: m.id, asset: m.asset, window: m.window, side, tSignal: now, pxSignal, pxFill: shares ? pxArr : undefined,
        shares, fee: shares ? shares * feePerShare(pxArr, this.regime.feeRate) : 0, fair, filled: shares > 0, end: m.end,
      });
      if (this.trades.length > 400) this.trades.pop();
    }, this.latencyMs);
  }

  private settle(m: Market) {
    const S = this.px[m.asset];
    if (S === undefined || m.strike === undefined) return;
    const up = S >= m.strike ? 1 : 0;
    for (const t of this.trades) {
      if (t.market === m.id && t.filled && t.pnl === undefined) {
        const out = t.side === "YES" ? up : 1 - up;
        t.pnl = t.shares * (out - t.pxFill!) - t.fee;
      }
    }
  }

  // ---------------------------------------------------------------- stats
  stats() {
    const filled = this.trades.filter((t) => t.filled);
    const settled = filled.filter((t) => t.pnl !== undefined);
    const pnl = settled.reduce((s, t) => s + (t.pnl ?? 0), 0);
    const open = filled.filter((t) => t.pnl === undefined);
    const mtm = open.reduce((s, t) => {
      const m = this.markets.get(t.market);
      if (!m?.fair) return s;
      const q = t.side === "YES" ? m.fair : 1 - m.fair;
      return s + t.shares * (q - t.pxFill!) - t.fee;
    }, 0);
    return {
      signals: this.trades.length,
      fills: filled.length,
      misses: this.trades.length - filled.length,
      settled: settled.length,
      wins: settled.filter((t) => (t.pnl ?? 0) > 0).length,
      pnl,
      mtm,
      fees: filled.reduce((s, t) => s + t.fee, 0),
    };
  }

  setRegime(key: string) {
    this.regime = REGIMES.find((r) => r.key === key) ?? REGIMES[0];
    this.emit();
  }

  resetLedger() {
    this.trades = [];
    for (const m of this.markets.values()) m.markers = [];
    this.emit();
  }
}

export const lab = new Lab();
