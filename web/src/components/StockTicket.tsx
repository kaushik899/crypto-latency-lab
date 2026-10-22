import { useEffect, useState } from "react";
import { deskCall } from "../lib/trade";

type AnyRec = Record<string, any>;
const usd = (x: number) => `${x < 0 ? "−" : ""}$${Math.abs(x).toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;

/** A US-stock day trade from the drawer: symbol, amount, stop and target, then the desk's ticket and one click. */
export function StockTicket() {
  const [sym, setSym] = useState("AAPL");
  const [dollars, setDollars] = useState(100);
  const [stopPct, setStopPct] = useState(1.0);
  const [rr, setRr] = useState(2);
  const [q, setQ] = useState<AnyRec | null>(null);
  const [ticket, setTicket] = useState<AnyRec | null>(null);
  const [done, setDone] = useState<AnyRec | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [left, setLeft] = useState(0);

  useEffect(() => {
    setQ(null); setTicket(null); setErr("");
    const s = sym.trim().toUpperCase();
    if (!/^[A-Z]{1,5}([.-][A-Z])?$/.test(s)) return;
    const id = window.setTimeout(() => {
      deskCall(`/stocks/quote?symbol=${encodeURIComponent(s)}`).then(setQ).catch((e) => setErr((e as Error).message));
    }, 350);
    return () => window.clearTimeout(id);
  }, [sym]);
  useEffect(() => {
    if (!ticket?.token) return;
    setLeft(ticket.expiresIn);
    const id = window.setInterval(() => setLeft((l) => Math.max(0, l - 1)), 1000);
    return () => window.clearInterval(id);
  }, [ticket]);

  const px = q?.ask || q?.last || 0;
  const stop = px ? Math.floor(px * (1 - stopPct / 100) * 100) / 100 : 0;
  const target = px ? Math.round((px + rr * (px - stop)) * 100) / 100 : 0;
  const whole = px ? Math.floor(dollars / px) : 0;
  const qty = whole >= 1 ? whole : px ? Math.floor((dollars / px) * 1e4) / 1e4 : 0;
  const win = qty * (target - px), loss = qty * (stop - px);

  const preview = async () => {
    setBusy(true); setErr(""); setDone(null);
    try {
      setTicket(await deskCall("/stocks/preview", { symbol: sym.trim().toUpperCase(), qty, entry: px, stop, target }));
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const place = async () => {
    setBusy(true); setErr("");
    try { setDone(await deskCall("/stocks/order", { token: ticket!.token, confirm: true })); setTicket(null); }
    catch (e) { setErr((e as Error).message); setTicket(null); } finally { setBusy(false); }
  };

  return (
    <div className="st">
      <div className="st-in">
        <label><span>Stock</span><input value={sym} maxLength={7} spellCheck={false} onChange={(e) => setSym(e.target.value.toUpperCase())} /></label>
        <label><span>Amount $</span><input type="number" min={1} value={dollars} onChange={(e) => setDollars(Math.max(1, Number(e.target.value) || 1))} /></label>
        <label><span>Stop below %</span><input type="number" min={0.2} max={20} step={0.1} value={stopPct} onChange={(e) => setStopPct(Math.min(20, Math.max(0.2, Number(e.target.value) || 1)))} /></label>
        <label><span>Target, × risk</span><input type="number" min={1.5} max={5} step={0.5} value={rr} onChange={(e) => setRr(Math.min(5, Math.max(1.5, Number(e.target.value) || 2)))} /></label>
      </div>
      {q ? (
        <dl className="setup-pnl">
          <div><dt>Price now (IEX)</dt><dd className="num">{px.toFixed(2)}{q.bid ? <small className="muted"> · bid {q.bid.toFixed(2)}</small> : null}</dd></div>
          <div><dt>Shares</dt><dd className="num">{qty} {whole < 1 && qty > 0 ? "(fractional)" : ""}</dd></div>
          <div><dt>If the target hits ({target.toFixed(2)})</dt><dd className="num pos">+{usd(win)}</dd></div>
          <div><dt>If the stop hits ({stop.toFixed(2)})</dt><dd className="num neg">{usd(loss)}</dd></div>
        </dl>
      ) : !err && <p className="fine">Fetching a price from your Alpaca account…</p>}
      {!ticket && !done && <button className="btn gold" disabled={busy || !q || qty <= 0} onClick={preview}>Preview ticket</button>}
      {ticket && (
        <div className="ticket-card" role="dialog" aria-label="Order ticket">
          <p className="ticket-sum">{ticket.summary}</p>
          <p className="fine">{ticket.kind}</p>
          <ul className="checks">
            {ticket.checks.map((c: AnyRec) => <li key={c.check} className={c.ok ? "ok" : "no"}><span>{c.ok ? "✓" : "✗"}</span> {c.check} <em>{c.detail}</em></li>)}
          </ul>
          <div className="ticket-actions">
            {ticket.ok && left > 0 && <button className={`btn ${ticket.live ? "kill" : "gold"}`} disabled={busy} onClick={place}>
              Place this order{ticket.live ? " — real money" : ""} ({left}s)</button>}
            <button className="btn ghost" onClick={() => setTicket(null)}>{ticket.ok && left > 0 ? "Cancel" : "Close"}</button>
          </div>
        </div>
      )}
      {done && <p className="ticket-ok" role="status">Sent in {done.ms} ms: {done.summary} <button className="link" onClick={() => setDone(null)}>New ticket</button></p>}
      {err && <p className="drawer-error" role="alert">{err}</p>}
      <p className="fine">Every stock order is a day order with its stop{qty >= 1 ? " and target attached" : ""}, re-checked by your desk
        (size cap, 1% max loss, 9:45–15:30 New York, day-trade limit) and sent only when you press <i>Place this order</i>.
        Close by 15:55 New York to stay a day trade.</p>
    </div>
  );
}
