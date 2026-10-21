// Trade now: practice fills on live Binance prices, or real orders through the local trade desk
// (latlab/desk.py), which holds the API key on the user's own computer.

import type { AssetKey } from "./assets";
import { real } from "./real";

export type Side = "BUY" | "SELL";
export type DeskMode = "testnet" | "live";

export type Receipt = {
  id: number;
  at: number;
  where: "practice" | DeskMode;
  asset: AssetKey;
  side: Side;
  quote: number;
  ok: boolean;
  status?: string;
  qty?: number;
  avgPrice?: number;
  feeUsdt?: number;
  error?: string;
  timing: { clickToSendMs: number; exchangeMs?: number; totalMs: number };
};

export const DESK_URL = "http://127.0.0.1:8765";
const PRACTICE_LATENCY_MS = 180; // a typical Paris → Binance order round trip
const PRACTICE_FEE = 0.001; // Binance regular spot taker fee, 0.10%

let seq = 0;

export async function practiceOrder(asset: AssetKey, side: Side, quote: number): Promise<Receipt> {
  const t0 = performance.now();
  await new Promise((r) => setTimeout(r, PRACTICE_LATENCY_MS));
  const q = real.quotes[asset]?.binance;
  const total = performance.now() - t0;
  if (!q || real.now() - q.recvTs > 5000) {
    return { id: ++seq, at: Date.now(), where: "practice", asset, side, quote, ok: false,
      error: "No live Binance price yet. Wait for the feed to connect.", timing: { clickToSendMs: 0, totalMs: total } };
  }
  const px = side === "BUY" ? q.ask : q.bid;
  return { id: ++seq, at: Date.now(), where: "practice", asset, side, quote, ok: true, status: "FILLED (practice)",
    qty: quote / px, avgPrice: px, feeUsdt: quote * PRACTICE_FEE,
    timing: { clickToSendMs: 0, exchangeMs: PRACTICE_LATENCY_MS, totalMs: total } };
}

export type DeskStatus = { mode: DeskMode; maxOrder: number; balances: Record<string, number> };

async function deskFetch(path: string, code: string, init?: RequestInit) {
  const r = await fetch(DESK_URL + path, {
    ...init,
    headers: { "Content-Type": "application/json", "X-Desk-Code": code, ...(init?.headers ?? {}) },
    // Chrome's Private Network Access: lets an https page talk to 127.0.0.1
    ...({ targetAddressSpace: "local" } as RequestInit),
  });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.error ?? `Desk answered ${r.status}`);
  return body;
}

export async function deskStatus(code: string): Promise<DeskStatus> {
  try {
    return await deskFetch("/status", code);
  } catch (e) {
    if (e instanceof TypeError) {
      throw new Error("Can't reach the trade desk on this computer. Start it with: python -m latlab.desk");
    }
    throw e;
  }
}

export async function deskOrder(code: string, mode: DeskMode, asset: AssetKey, side: Side, quote: number): Promise<Receipt> {
  const t0 = performance.now();
  try {
    const b = await deskFetch("/order", code, { method: "POST", body: JSON.stringify({ asset, side, quote }) });
    const total = performance.now() - t0;
    const feeUsdt = Object.entries(b.fees ?? {}).reduce((s, [a, v]) =>
      s + (a === "USDT" ? (v as number) : a === asset ? (v as number) * (b.avgPrice ?? 0) : 0), 0);
    return { id: ++seq, at: Date.now(), where: mode, asset, side, quote, ok: true, status: b.status, qty: b.qty,
      avgPrice: b.avgPrice, feeUsdt, timing: { clickToSendMs: 0, exchangeMs: b.timing?.exchange_ms, totalMs: total } };
  } catch (e) {
    return { id: ++seq, at: Date.now(), where: mode, asset, side, quote, ok: false,
      error: e instanceof TypeError ? "The trade desk stopped answering. Is it still running?" : (e as Error).message,
      timing: { clickToSendMs: 0, totalMs: performance.now() - t0 } };
  }
}
