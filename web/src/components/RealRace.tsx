import { useEffect, useRef } from "react";
import type { AssetKey } from "../lib/assets";
import { real } from "../lib/real";
import { VENUES } from "../lib/venues";

const SPAN_S = 60;
const AXIS = "#8BA3B8";
const GRID = "rgba(139,163,184,0.14)";

/** Live chart: each exchange's price (basis-adjusted, in bps from the window start) and,
 * underneath, the best cross-exchange gap after fees. */
export function RealRace({ asset }: { asset: AssetKey }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const assetRef = useRef(asset);
  assetRef.current = asset;

  useEffect(() => {
    const cv = ref.current!;
    const ctx = cv.getContext("2d")!;
    let raf = 0;
    let lo = -5, hi = 5;
    let last = 0;

    const draw = (ts: number) => {
      raf = requestAnimationFrame(draw);
      if (ts - last < 33) return; // ~30 fps is plenty
      last = ts;
      const dpr = window.devicePixelRatio || 1;
      const W = cv.clientWidth, H = cv.clientHeight;
      if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
        cv.width = Math.round(W * dpr);
        cv.height = Math.round(H * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const padL = 8, padR = 56, padT = 10, padB = 24;
      const stripH = Math.max(54, H * 0.2);
      const ph = H - padT - padB - stripH - 14;
      const pw = W - padL - padR;
      const now = real.now();
      const x = (t: number) => padL + ((t - (now - SPAN_S * 1000)) / (SPAN_S * 1000)) * pw;
      const { prices, gap } = real.raceSeries(assetRef.current, SPAN_S);

      ctx.font = "500 11px 'Archivo Variable', system-ui";
      // time grid
      for (let s = 0; s <= SPAN_S; s += 10) {
        const gx = x(now - s * 1000);
        ctx.strokeStyle = GRID;
        ctx.beginPath(); ctx.moveTo(gx, padT); ctx.lineTo(gx, H - padB); ctx.stroke();
        ctx.fillStyle = AXIS;
        ctx.textAlign = "center";
        ctx.fillText(s === 0 ? "now" : `−${s}s`, gx, H - 7);
      }

      const all = Object.values(prices).flat();
      if (all.length < 10) {
        ctx.fillStyle = AXIS;
        ctx.textAlign = "left";
        ctx.font = "500 14px 'Archivo Variable', system-ui";
        ctx.fillText("Connecting to Binance, Coinbase, Kraken, OKX and Bybit…", padL + 16, padT + ph / 2);
        return;
      }
      let mn = Infinity, mx = -Infinity;
      for (const p of all) { mn = Math.min(mn, p.y); mx = Math.max(mx, p.y); }
      const pad = Math.max(1, (mx - mn) * 0.15);
      lo += (mn - pad - lo) * 0.12;
      hi += (mx + pad - hi) * 0.12;
      const y = (v: number) => padT + (1 - (v - lo) / (hi - lo)) * ph;

      // price grid in bps
      const range = hi - lo;
      const step = range > 60 ? 20 : range > 30 ? 10 : range > 12 ? 5 : range > 5 ? 2 : 1;
      ctx.textAlign = "left";
      for (let v = Math.ceil(lo / step) * step; v <= hi; v += step) {
        ctx.strokeStyle = GRID;
        ctx.beginPath(); ctx.moveTo(padL, y(v)); ctx.lineTo(padL + pw, y(v)); ctx.stroke();
        ctx.fillStyle = AXIS;
        ctx.fillText(`${v > 0 ? "+" : ""}${v} bp`, padL + pw + 6, y(v) + 4);
      }

      // venue lines, Binance last so it sits on top
      const order = [...VENUES].sort((a, b) => (a.key === "binance" ? 1 : b.key === "binance" ? -1 : 0));
      for (const v of order) {
        const pts = prices[v.key] ?? [];
        if (pts.length < 2) continue;
        ctx.strokeStyle = v.color;
        ctx.lineWidth = v.key === "binance" ? 2.6 : 1.5;
        ctx.globalAlpha = v.key === "binance" ? 1 : 0.9;
        ctx.beginPath();
        pts.forEach((p, i) => {
          const X = x(p.t), Y = y(p.y);
          if (i === 0) ctx.moveTo(X, Y);
          else { ctx.lineTo(X, y(pts[i - 1].y)); ctx.lineTo(X, Y); } // step: prices hold until updated
        });
        ctx.stroke();
        ctx.globalAlpha = 1;
      }

      // gap strip
      const sTop = padT + ph + 14, sBot = sTop + stripH;
      const gMin = -40, gMax = 10;
      const gy = (v: number) => sTop + (1 - (Math.max(gMin, Math.min(gMax, v)) - gMin) / (gMax - gMin)) * stripH;
      ctx.fillStyle = "rgba(19,38,58,0.9)";
      ctx.fillRect(padL, sTop, pw, stripH);
      ctx.strokeStyle = "rgba(61,214,140,0.6)";
      ctx.setLineDash([4, 4]);
      ctx.beginPath(); ctx.moveTo(padL, gy(0)); ctx.lineTo(padL + pw, gy(0)); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = AXIS;
      ctx.fillText("0 bp", padL + pw + 6, gy(0) + 4);
      ctx.fillText(`${gMin}`, padL + pw + 6, sBot);
      if (gap.length > 1) {
        ctx.strokeStyle = "#B07CFF";
        ctx.lineWidth = 1.4;
        ctx.beginPath();
        gap.forEach((p, i) => (i ? ctx.lineTo(x(p.t), gy(p.y)) : ctx.moveTo(x(p.t), gy(p.y))));
        ctx.stroke();
        ctx.fillStyle = "rgba(61,214,140,0.85)";
        for (const p of gap) if (p.y > 0) ctx.fillRect(x(p.t) - 1, gy(p.y), 2.5, gy(0) - gy(p.y));
      }
      ctx.fillStyle = AXIS;
      ctx.fillText("best gap after fees", padL + 8, sTop + 14);
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  return (
    <canvas
      ref={ref}
      className="race-canvas real"
      role="img"
      aria-label="Live prices of the selected coin on five exchanges, and the best price gap between exchanges after fees"
    />
  );
}
