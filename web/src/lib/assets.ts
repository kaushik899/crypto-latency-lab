export type AssetKey = "BTC" | "ETH" | "SOL" | "XRP" | "BNB" | "DOGE";

export const ASSETS: { key: AssetKey; name: string; pair: string; sigma0: number; start: number; poly: string | null }[] = [
  { key: "BTC", name: "Bitcoin", pair: "btcusdt", sigma0: 0.5, start: 112000, poly: "btc" },
  { key: "ETH", name: "Ethereum", pair: "ethusdt", sigma0: 0.65, start: 4100, poly: "eth" },
  { key: "SOL", name: "Solana", pair: "solusdt", sigma0: 0.85, start: 195, poly: "sol" },
  { key: "XRP", name: "XRP", pair: "xrpusdt", sigma0: 0.8, start: 2.6, poly: "xrp" },
  { key: "BNB", name: "BNB", pair: "bnbusdt", sigma0: 0.55, start: 1050, poly: null },
  { key: "DOGE", name: "Dogecoin", pair: "dogeusdt", sigma0: 1.0, start: 0.21, poly: null },
];

export const WINDOWS = [
  { key: "5m", sec: 300 },
  { key: "15m", sec: 900 },
] as const;

export function fmtPrice(p: number | undefined) {
  if (p === undefined || !isFinite(p)) return "—";
  if (p >= 1000) return p.toLocaleString("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 1 });
  if (p >= 10) return p.toFixed(2);
  if (p >= 1) return p.toFixed(4);
  return p.toFixed(5);
}

export const fmtUsd = (x: number, dp = 0) =>
  (x < 0 ? "−$" : "$") + Math.abs(x).toLocaleString("en-US", { maximumFractionDigits: dp, minimumFractionDigits: dp });

export const fmtCents = (p: number) => `${(p * 100).toFixed(0)}¢`;
