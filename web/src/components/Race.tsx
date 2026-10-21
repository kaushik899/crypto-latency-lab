import { useEffect, useRef } from "react";
import { lab } from "../lib/lab";

const SPAN_S = 60;
const C = {
  grid: "rgba(163,154,139,0.14)",
  axis: "#A39A8B",
  fair: "#D9B26A",
  quote: "#6E93D6",
  quoteBand: "rgba(110,147,214,0.12)",
  gap: "rgba(201,139,166,0.42)",
  fill: "#7FCBA4",
  miss: "#E0694B",
  text: "#EDE6D8",
  flight: "rgba(201,139,166,0.10)",
};

export function Race({ marketId }: { marketId: string | undefined }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const idRef = useRef(marketId);
  idRef.current = marketId;

  useEffect(() => {
    const cv = ref.current!;
    const ctx = cv.getContext("2d")!;
    let raf = 0;
    let lo = 0.3, hi = 0.7;

    const draw = () => {
      raf = requestAnimationFrame(draw);
      const dpr = window.devicePixelRatio || 1;
      const W = cv.clientWidth, H = cv.clientHeight;
      if (cv.width !== Math.round(W * dpr) || cv.height !== Math.round(H * dpr)) {
        cv.width = Math.round(W * dpr);
        cv.height = Math.round(H * dpr);
      }
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const m = idRef.current ? lab.markets.get(idRef.current) : undefined;
      const padL = 8, padR = 74, padT = 14, padB = 26;
      const pw = W - padL - padR, ph = H - padT - padB;
      const now = Date.now() / 1000;
      const flightS = lab.latencyMs / 1000;
      const x = (t: number) => padL + ((t - (now - SPAN_S)) / SPAN_S) * pw;

      // grid: every 10 s
      ctx.strokeStyle = C.grid;
      ctx.lineWidth = 1;
      ctx.font = "500 11px 'Schibsted Grotesk Variable', system-ui";
      ctx.fillStyle = C.axis;
      for (let s = 0; s <= SPAN_S; s += 10) {
        const gx = x(now - s);
        ctx.beginPath(); ctx.moveTo(gx, padT); ctx.lineTo(gx, padT + ph); ctx.stroke();
        ctx.textAlign = "center";
        ctx.fillText(s === 0 ? "now" : `−${s}s`, gx, H - 8);
      }
      if (!m || m.history.length < 2) {
        ctx.fillStyle = C.axis;
        ctx.textAlign = "left";
        ctx.font = "500 14px 'Schibsted Grotesk Variable', system-ui";
        ctx.fillText(lab.status === "connecting" ? "Connecting to Binance…" : "Waiting for the first quotes…", padL + 16, padT + ph / 2);
        return;
      }
      const hs = m.history.filter((h) => h.t >= now - SPAN_S - 1);
      // smooth y-range
      let mn = Infinity, mx = -Infinity;
      for (const h of hs) {
        mn = Math.min(mn, h.fair, h.bid);
        mx = Math.max(mx, h.fair, h.ask);
      }
      const pad = Math.max(0.03, (mx - mn) * 0.25);
      lo += (Math.max(0, mn - pad) - lo) * 0.08;
      hi += (Math.min(1, mx + pad) - hi) * 0.08;
      if (hi - lo < 0.06) { const c = (hi + lo) / 2; lo = c - 0.03; hi = c + 0.03; }
      const y = (p: number) => padT + (1 - (p - lo) / (hi - lo)) * ph;

      // horizontal grid in cents
      const stepC = hi - lo > 0.4 ? 0.1 : hi - lo > 0.15 ? 0.05 : 0.02;
      ctx.textAlign = "left";
      for (let p = Math.ceil(lo / stepC) * stepC; p <= hi; p += stepC) {
        ctx.strokeStyle = C.grid;
        ctx.beginPath(); ctx.moveTo(padL, y(p)); ctx.lineTo(padL + pw, y(p)); ctx.stroke();
        ctx.fillStyle = C.axis;
        ctx.fillText(`${Math.round(p * 100)}¢`, padL + pw + 8, y(p) + 4);
      }

      // order-in-flight strip: everything inside it is what your order will find when it lands
      ctx.fillStyle = C.flight;
      ctx.fillRect(x(now - flightS), padT, x(now) - x(now - flightS), ph);

      // bid/ask band (step)
      ctx.beginPath();
      hs.forEach((h, i) => {
        const X = x(h.t);
        if (i === 0) ctx.moveTo(X, y(h.ask));
        else { ctx.lineTo(X, y(hs[i - 1].ask)); ctx.lineTo(X, y(h.ask)); }
      });
      for (let i = hs.length - 1; i >= 0; i--) {
        const X = x(hs[i].t);
        ctx.lineTo(X, y(hs[i].bid));
        if (i > 0) ctx.lineTo(X, y(hs[i - 1].bid));
      }
      ctx.closePath();
      ctx.fillStyle = C.quoteBand;
      ctx.fill();

      // the gap: fair outside the quotes
      ctx.fillStyle = C.gap;
      for (let i = 1; i < hs.length; i++) {
        const a = hs[i - 1], b = hs[i];
        const x0 = x(a.t), x1 = x(b.t);
        if (b.fair > b.ask) ctx.fillRect(x0, y(b.fair), x1 - x0 + 0.5, y(b.ask) - y(b.fair));
        else if (b.fair < b.bid) ctx.fillRect(x0, y(b.bid), x1 - x0 + 0.5, y(b.fair) - y(b.bid));
      }

      // quote step lines
      ctx.strokeStyle = C.quote;
      ctx.lineWidth = 1.6;
      for (const key of ["ask", "bid"] as const) {
        ctx.beginPath();
        hs.forEach((h, i) => {
          const X = x(h.t);
          if (i === 0) ctx.moveTo(X, y(h[key]));
          else { ctx.lineTo(X, y(hs[i - 1][key])); ctx.lineTo(X, y(h[key])); }
        });
        ctx.stroke();
      }

      // fair value from Binance spot
      ctx.strokeStyle = C.fair;
      ctx.lineWidth = 2.4;
      ctx.lineJoin = "round";
      ctx.beginPath();
      hs.forEach((h, i) => (i ? ctx.lineTo(x(h.t), y(h.fair)) : ctx.moveTo(x(h.t), y(h.fair))));
      ctx.stroke();

      // orders: signal -> arrival
      for (const k of m.markers) {
        if (k.t < now - SPAN_S) continue;
        const x0 = x(k.t), x1 = x(Math.min(k.tArrive ?? k.t, now));
        const Y = y(k.px);
        const col = k.kind === "fill" ? C.fill : k.kind === "miss" ? C.miss : C.text;
        ctx.strokeStyle = col;
        ctx.lineWidth = 1.5;
        ctx.setLineDash([3, 3]);
        ctx.beginPath(); ctx.moveTo(x0, Y); ctx.lineTo(x1, Y); ctx.stroke();
        ctx.setLineDash([]);
        ctx.beginPath(); ctx.arc(x0, Y, 3.5, 0, Math.PI * 2); ctx.stroke();
        if (k.kind === "fill") {
          ctx.fillStyle = col;
          ctx.beginPath(); ctx.arc(x1, Y, 4.5, 0, Math.PI * 2); ctx.fill();
        } else if (k.kind === "miss") {
          ctx.beginPath();
          ctx.moveTo(x1 - 4, Y - 4); ctx.lineTo(x1 + 4, Y + 4);
          ctx.moveTo(x1 + 4, Y - 4); ctx.lineTo(x1 - 4, Y + 4);
          ctx.stroke();
        }
      }

      // live value tags on the right edge
      const last = hs[hs.length - 1];
      const tags = [
        { v: last.fair, c: C.fair, t: "fair" },
        { v: last.ask, c: C.quote, t: "ask" },
        { v: last.bid, c: C.quote, t: "bid" },
      ].sort((a, b) => b.v - a.v);
      let prevY = -99;
      ctx.font = "600 11px 'Schibsted Grotesk Variable', system-ui";
      for (const tg of tags) {
        let Y = y(tg.v);
        if (Y - prevY < 15) Y = prevY + 15;
        prevY = Y;
        ctx.fillStyle = tg.c;
        ctx.fillRect(padL + pw + 2, Y - 8, padR - 4, 16);
        ctx.fillStyle = "#13100D";
        ctx.textAlign = "left";
        ctx.fillText(`${tg.t} ${(tg.v * 100).toFixed(1)}`, padL + pw + 6, Y + 4);
      }
    };
    raf = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(raf);
  }, []);

  return <canvas ref={ref} className="race-canvas" role="img" aria-label="Live chart of model fair value versus market bid and ask, with your simulated orders" />;
}
