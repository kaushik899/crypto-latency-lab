// Real-data lab: five live exchange feeds, lead-lag measurement and latency-checked paper
// arbitrage. Everything here runs on actual market data streamed to the viewer's browser.

import { ASSETS, type AssetKey } from "./assets";
import { VENUES, VenueConn, type Quote, type VenueKey } from "./venues";

export const STEP_MS = 50; // sampling grid
const RING = 6000; // 5 minutes of 50 ms samples
const BASIS_HL_S = 30; // half-life of the rolling cross-venue basis
const STALE_MS = 2000; // a quote older than this is not tradable
const EVENT_WINDOW_S = 1;
const EVENT_TIMEOUT_S = 3;
const NOTIONAL = 1000;

const ASSET_KEYS = ASSETS.map((a) => a.key);
const VENUE_KEYS = VENUES.map((v) => v.key);
const EVENT_BPS: Record<AssetKey, number> = { BTC: 2.5, ETH: 3, SOL: 4, XRP: 4, BNB: 4, DOGE: 5 };

type Ring = { buf: Float64Array; n: number };
const ring = (): Ring => ({ buf: new Float64Array(RING).fill(NaN), n: 0 });
const push = (r: Ring, x: number) => { r.buf[r.n % RING] = x; r.n++; };
/** value k steps ago (0 = latest) */
const ago = (r: Ring, k: number) => (k < r.n && k < RING ? r.buf[(r.n - 1 - k) % RING] : NaN);

type CatchEvent = {
  asset: AssetKey;
  dir: number;
  move: number; // log move of Binance over the window
  tBinance: number; // time Binance crossed half the move
  start: Partial<Record<VenueKey, number>>; // each venue's level at window start
  done: Partial<Record<VenueKey, number>>; // lag in ms (Infinity = did not follow in time)
  tDetect: number;
};

export type ArbTrade = {
  id: number;
  asset: AssetKey;
  buy: VenueKey;
  sell: VenueKey;
  t: number;
  netSignal: number; // bps after fees at signal
  netArrival?: number; // bps after fees when the order would arrive
  filled?: boolean;
  pnl?: number;
};

const median = (xs: number[]) => {
  if (!xs.length) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
};
const quantile = (xs: number[], q: number) => {
  if (!xs.length) return NaN;
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.min(s.length - 1, Math.floor(q * s.length))];
};

class RealLab {
  started = 0;
  clockOffset = 0; // ms to add to Date.now() to match exchange time
  clockRtt = NaN;
  latencyMs = 100;
  conns: Partial<Record<VenueKey, VenueConn>> = {};
  quotes: Partial<Record<AssetKey, Partial<Record<VenueKey, Quote>>>> = {};
  delays: Partial<Record<VenueKey, number[]>> = {};
  /** log mid, adjusted for each venue's rolling basis to Binance */
  rings: Record<string, Ring> = {};
  basis: Record<string, number> = {};
  bestGap: Record<string, Ring> = {}; // per asset: best net gap (bps) across all pairs, sampled
  events: CatchEvent[] = [];
  lags: Partial<Record<VenueKey, Partial<Record<AssetKey, number[]>>>> = {};
  censored: Partial<Record<VenueKey, number>> = {};
  xcorr: Partial<Record<AssetKey, Partial<Record<VenueKey, { lagMs: number; corr: number; series: number[] }>>>> = {};
  trades: ArbTrade[] = [];
  gapsSeen = 0; // fee-clearing signals over the session
  private nextOk: Record<string, number> = {};
  private tradeId = 0;
  private lastEvent: Partial<Record<AssetKey, number>> = {};
  private listeners = new Set<() => void>();
  version = 0;
  private ticks = 0;

  now = () => Date.now() + this.clockOffset;

  start() {
    if (this.started) return;
    this.started = this.now();
    for (const a of ASSET_KEYS) {
      this.quotes[a] = {};
      this.bestGap[a] = ring();
      for (const v of VENUE_KEYS) this.rings[`${a}:${v}`] = ring();
    }
    this.syncClock();
    for (const info of VENUES) {
      const c = new VenueConn(info, (q) => this.onQuote(q), this.now, () => this.emit());
      if (info.key === "binance") c.onDelay = (ts) => this.addDelay("binance", this.now() - ts);
      this.conns[info.key] = c;
      c.start();
    }
    window.setInterval(() => this.step(), STEP_MS);
  }

  subscribe(fn: () => void) {
    this.listeners.add(fn);
    return () => this.listeners.delete(fn);
  }
  private emit() {
    this.version++;
    this.listeners.forEach((f) => f());
  }

  /** NTP-style offset against Binance server time: keep the sample with the shortest round trip. */
  private async syncClock() {
    let best = Infinity;
    for (let i = 0; i < 5; i++) {
      try {
        const t0 = Date.now();
        const r = await fetch("https://api.binance.com/api/v3/time", { cache: "no-store" });
        const { serverTime } = await r.json();
        const t1 = Date.now();
        if (t1 - t0 < best) {
          best = t1 - t0;
          this.clockOffset = serverTime - (t0 + t1) / 2;
          this.clockRtt = best;
        }
      } catch {
        break;
      }
    }
  }

  private addDelay(v: VenueKey, d: number) {
    if (!isFinite(d) || d < -2000 || d > 10000) return;
    const arr = (this.delays[v] ??= []);
    arr.push(d);
    if (arr.length > 600) arr.shift();
  }

  private onQuote(q: Quote) {
    this.quotes[q.asset]![q.venue] = q;
    if (q.exchTs) this.addDelay(q.venue, q.recvTs - q.exchTs);
  }

  private fresh(q: Quote | undefined, t: number) {
    return q && t - q.recvTs < STALE_MS ? q : undefined;
  }

  // ------------------------------------------------------------------ sampling loop
  private step() {
    const t = this.now();
    const w = 1 - Math.pow(0.5, STEP_MS / 1000 / BASIS_HL_S);
    for (const a of ASSET_KEYS) {
      const qs = this.quotes[a]!;
      const qb = qs.binance;
      const xb = qb ? Math.log((qb.bid + qb.ask) / 2) : NaN;
      for (const v of VENUE_KEYS) {
        const q = qs[v];
        const key = `${a}:${v}`;
        if (!q || t - q.recvTs > 30000) { push(this.rings[key], NaN); continue; }
        const x = Math.log((q.bid + q.ask) / 2);
        if (v !== "binance" && isFinite(xb)) {
          const d = x - xb;
          this.basis[key] = this.basis[key] === undefined ? d : (1 - w) * this.basis[key] + w * d;
        }
        if (v !== "binance" && this.basis[key] === undefined) { push(this.rings[key], NaN); continue; }
        push(this.rings[key], x - (v === "binance" ? 0 : this.basis[key]));
      }
      this.scanGaps(a, t);
      this.detectEvent(a, t);
    }
    this.trackEvents(t);
    if (++this.ticks % 60 === 0) this.computeXcorr();
    if (this.ticks % 4 === 0) this.emit();
  }

  // ------------------------------------------------------------------ cross-exchange gaps
  /** Net edge in bps of buying on ``buy`` at its ask and selling on ``sell`` at its bid, after both taker fees
   * and after removing the normal (rolling) price difference between the two venues. */
  netGap(a: AssetKey, buy: VenueKey, sell: VenueKey, t: number) {
    const qa = this.fresh(this.quotes[a]![buy], t), qb = this.fresh(this.quotes[a]![sell], t);
    if (!qa || !qb) return NaN;
    const bA = buy === "binance" ? 0 : this.basis[`${a}:${buy}`];
    const bB = sell === "binance" ? 0 : this.basis[`${a}:${sell}`];
    if (bA === undefined || bB === undefined) return NaN;
    const gross = (Math.log(qb.bid) - Math.log(qa.ask) - (bB - bA)) * 1e4;
    const fee = VENUES.find((x) => x.key === buy)!.takerBps + VENUES.find((x) => x.key === sell)!.takerBps;
    return gross - fee;
  }

  private scanGaps(a: AssetKey, t: number) {
    let best = -Infinity;
    for (const buy of VENUE_KEYS) for (const sell of VENUE_KEYS) {
      if (buy === sell) continue;
      const g = this.netGap(a, buy, sell, t);
      if (!isFinite(g)) continue;
      best = Math.max(best, g);
      const k = `${a}:${buy}>${sell}`;
      if (g > 0 && t >= (this.nextOk[k] ?? 0)) {
        this.nextOk[k] = t + 2000;
        this.gapsSeen++;
        const tr: ArbTrade = { id: ++this.tradeId, asset: a, buy, sell, t, netSignal: g };
        this.trades.unshift(tr);
        if (this.trades.length > 500) this.trades.pop();
        window.setTimeout(() => {
          const g2 = this.netGap(a, buy, sell, this.now());
          tr.netArrival = g2;
          tr.filled = isFinite(g2) && g2 > 0;
          tr.pnl = tr.filled ? (g2 / 1e4) * NOTIONAL : 0;
        }, this.latencyMs);
      }
    }
    push(this.bestGap[a], isFinite(best) ? best : NaN);
  }

  // ------------------------------------------------------------------ catch-up events
  private detectEvent(a: AssetKey, t: number) {
    const r = this.rings[`${a}:binance`];
    const k = (EVENT_WINDOW_S * 1000) / STEP_MS;
    const x0 = ago(r, k), x1 = ago(r, 0);
    if (!isFinite(x0) || !isFinite(x1)) return;
    const move = x1 - x0;
    if (Math.abs(move) * 1e4 < EVENT_BPS[a] || t - (this.lastEvent[a] ?? 0) < 3000) return;
    this.lastEvent[a] = t;
    // when did Binance cross half of the move?
    let tHalf = t;
    for (let i = k; i >= 0; i--) {
      if (Math.sign(move) * (ago(r, i) - x0) >= Math.abs(move) / 2) { tHalf = t - i * STEP_MS; break; }
    }
    const start: CatchEvent["start"] = {};
    for (const v of VENUE_KEYS) if (v !== "binance") start[v] = ago(this.rings[`${a}:${v}`], k);
    this.events.push({ asset: a, dir: Math.sign(move), move: Math.abs(move), tBinance: tHalf, start, done: {}, tDetect: t });
  }

  private trackEvents(t: number) {
    for (const e of this.events) {
      for (const v of VENUE_KEYS) {
        if (v === "binance" || e.done[v] !== undefined) continue;
        const s = e.start[v];
        const x = ago(this.rings[`${e.asset}:${v}`], 0);
        if (s === undefined || !isFinite(s) || !isFinite(x)) { e.done[v] = NaN; continue; }
        if (e.dir * (x - s) >= e.move / 2) {
          e.done[v] = t - e.tBinance;
          const per = ((this.lags[v] ??= {})[e.asset] ??= []);
          per.push(e.done[v]!);
          if (per.length > 2000) per.shift();
        } else if (t - e.tDetect > EVENT_TIMEOUT_S * 1000) {
          e.done[v] = Infinity;
          this.censored[v] = (this.censored[v] ?? 0) + 1;
        }
      }
    }
    this.events = this.events.filter((e) => VENUE_KEYS.some((v) => v !== "binance" && e.done[v] === undefined));
  }

  // ------------------------------------------------------------------ lead-lag cross-correlation
  private computeXcorr(maxLag = 20) {
    const n = Math.min(RING - maxLag - 2, this.ticks - maxLag - 2);
    if (n < 200) return;
    for (const a of ASSET_KEYS) {
      const rb = this.rings[`${a}:binance`];
      const db = new Float64Array(n + maxLag);
      for (let i = 0; i < n + maxLag; i++) db[i] = ago(rb, i) - ago(rb, i + 1);
      for (const v of VENUE_KEYS) {
        if (v === "binance") continue;
        const rv = this.rings[`${a}:${v}`];
        const dv = new Float64Array(n + maxLag);
        for (let i = 0; i < n + maxLag; i++) dv[i] = ago(rv, i) - ago(rv, i + 1);
        const series: number[] = [];
        let best = -Infinity, bestLag = 0;
        for (let L = -maxLag; L <= maxLag; L++) {
          // corr(venue_t, binance_{t-L}); positive L = venue follows Binance by L steps
          let sxy = 0, sxx = 0, syy = 0, m = 0;
          for (let i = Math.max(0, -L); i < n - Math.max(0, L); i++) {
            const y = dv[i], x = db[i + L];
            if (!isFinite(x) || !isFinite(y)) continue;
            sxy += x * y; sxx += x * x; syy += y * y; m++;
          }
          const c = m > 50 && sxx > 0 && syy > 0 ? sxy / Math.sqrt(sxx * syy) : NaN;
          series.push(c);
          if (c > best) { best = c; bestLag = L; }
        }
        ((this.xcorr[a] ??= {})[v] = { lagMs: bestLag * STEP_MS, corr: best, series });
      }
    }
  }

  // ------------------------------------------------------------------ read models for the UI
  venueStats() {
    return VENUES.map((info) => {
      const d = this.delays[info.key] ?? [];
      const all = Object.values(this.lags[info.key] ?? {}).flat();
      return {
        ...info,
        status: this.conns[info.key]?.status ?? "connecting",
        delayMs: median(d),
        delayP90: quantile(d, 0.9),
        catchMs: median(all),
        catchN: all.length,
        censored: this.censored[info.key] ?? 0,
      };
    });
  }

  arbStats() {
    const done = this.trades.filter((x) => x.filled !== undefined);
    const fills = done.filter((x) => x.filled);
    return {
      signals: done.length,
      fills: fills.length,
      pnl: fills.reduce((s, x) => s + (x.pnl ?? 0), 0),
      avgSignal: done.length ? done.reduce((s, x) => s + x.netSignal, 0) / done.length : NaN,
      avgArrival: done.length ? done.filter((x) => isFinite(x.netArrival!)).reduce((s, x) => s + x.netArrival!, 0) / done.length : NaN,
    };
  }

  /** Last ``seconds`` of basis-adjusted prices for one asset, as % change from the window start. */
  raceSeries(a: AssetKey, seconds = 60) {
    const k = Math.min((seconds * 1000) / STEP_MS, this.ticks, RING - 1);
    const t = this.now();
    const out: Partial<Record<VenueKey, { t: number; y: number }[]>> = {};
    // reference level: the oldest Binance sample in the window that exists
    let x0 = NaN;
    for (let i = k - 1; i >= 0 && !isFinite(x0); i--) x0 = ago(this.rings[`${a}:binance`], i);
    for (const v of VENUE_KEYS) {
      const r = this.rings[`${a}:${v}`];
      const pts: { t: number; y: number }[] = [];
      for (let i = k - 1; i >= 0; i--) {
        const x = ago(r, i);
        if (isFinite(x) && isFinite(x0)) pts.push({ t: t - i * STEP_MS, y: (x - x0) * 1e4 });
      }
      out[v] = pts;
    }
    const g = this.bestGap[a];
    const gap: { t: number; y: number }[] = [];
    for (let i = k - 1; i >= 0; i--) {
      const x = ago(g, i);
      if (isFinite(x)) gap.push({ t: t - i * STEP_MS, y: x });
    }
    return { prices: out, gap };
  }

  /** Session summary used for the recorded-results file. */
  exportResults() {
    const lagsByAsset: Record<string, Record<string, { median: number; p25: number; p75: number; n: number }>> = {};
    for (const v of VENUE_KEYS) {
      if (v === "binance") continue;
      for (const a of ASSET_KEYS) {
        const xs = this.lags[v]?.[a] ?? [];
        ((lagsByAsset[v] ??= {})[a] = { median: median(xs), p25: quantile(xs, 0.25), p75: quantile(xs, 0.75), n: xs.length });
      }
    }
    const done = this.trades.filter((x) => x.filled !== undefined);
    return {
      recordedAt: new Date(this.started).toISOString(),
      minutes: (this.now() - this.started) / 60000,
      clockRttMs: this.clockRtt,
      latencyMs: this.latencyMs,
      venues: this.venueStats().map(({ key, name, takerBps, delayMs, delayP90, catchMs, catchN, censored }) =>
        ({ key, name, takerBps, delayMs, delayP90, catchMs, catchN, censored })),
      lagsByAsset,
      xcorr: Object.fromEntries(ASSET_KEYS.map((a) => [a, Object.fromEntries(
        Object.entries(this.xcorr[a] ?? {}).map(([v, x]) => [v, { lagMs: x!.lagMs, corr: x!.corr }]))])),
      arb: { ...this.arbStats(), gapsSeen: this.gapsSeen, sample: done.slice(0, 40) },
    };
  }
}

export const real = new RealLab();
(window as any).__realLab = real;
