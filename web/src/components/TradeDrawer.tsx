import { useEffect, useRef, useState } from "react";
import { ASSETS, fmtPrice, fmtUsd, type AssetKey } from "../lib/assets";
import { desk as shared, deskOrder, deskStatus, onDesk, practiceOrder, setDesk as share, type DeskStatus, type Receipt, type Side } from "../lib/trade";
import { useReal } from "../lib/useReal";

type Account = "practice" | "binance";
const AMOUNTS = [10, 25, 50, 100];

export function TradeDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const real = useReal(400);
  const [account, setAccount] = useState<Account>("practice");
  const [asset, setAsset] = useState<AssetKey>("BTC");
  const [amount, setAmount] = useState(25);
  const [code, setCode] = useState(shared.code);
  const [desk, setDeskLocal] = useState<DeskStatus | null>(shared.status);
  useEffect(() => onDesk(() => { setCode(shared.code); setDeskLocal(shared.status); }), []);
  const setDesk = (d: DeskStatus | null) => share(code.trim(), d);
  const [deskErr, setDeskErr] = useState("");
  const [connecting, setConnecting] = useState(false);
  const [askFirst, setAskFirst] = useState(true);
  const [armed, setArmed] = useState<Side | null>(null);
  const [busy, setBusy] = useState(false);
  const [receipts, setReceipts] = useState<Receipt[]>([]);
  const panel = useRef<HTMLDivElement>(null);
  const armTimer = useRef<number>(undefined);

  // focus management + Escape to close
  useEffect(() => {
    if (!open) return;
    const prev = document.activeElement as HTMLElement | null;
    const t = window.setTimeout(() => panel.current?.querySelector<HTMLElement>("button, input")?.focus({ preventScroll: true }), 60);
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => { window.clearTimeout(t); window.removeEventListener("keydown", onKey); prev?.focus({ preventScroll: true }); };
  }, [open, onClose]);

  // live money always asks; practice and testnet follow the toggle
  const live = account === "binance" && desk?.mode === "live";
  const confirmFirst = live || askFirst;
  const q = real.quotes[asset]?.binance;
  const max = account === "binance" && desk ? desk.maxOrder : Infinity;

  async function connect() {
    setConnecting(true);
    setDeskErr("");
    try {
      setDesk(await deskStatus(code.trim()));
    } catch (e) {
      setDesk(null);
      setDeskErr((e as Error).message);
    } finally {
      setConnecting(false);
    }
  }

  async function place(side: Side) {
    if (busy) return;
    if (confirmFirst && armed !== side) {
      setArmed(side);
      window.clearTimeout(armTimer.current);
      armTimer.current = window.setTimeout(() => setArmed(null), 4000);
      return;
    }
    setArmed(null);
    setBusy(true);
    const r = account === "practice"
      ? await practiceOrder(asset, side, amount)
      : await deskOrder(code.trim(), desk!.mode, asset, side, amount);
    setReceipts((x) => [r, ...x].slice(0, 8));
    setBusy(false);
    if (account === "binance" && r.ok) deskStatus(code.trim()).then(setDesk).catch(() => {});
  }

  const ready = account === "practice" || !!desk;
  const verb = (s: Side) => (s === "BUY" ? "Buy" : "Sell");
  const where = account === "practice" ? "practice" : desk?.mode === "live" ? "real money" : "Binance Testnet";

  return (
    <div className={`drawer-wrap ${open ? "open" : ""}`} aria-hidden={!open}>
      <div className="drawer-scrim" onClick={onClose} />
      <div ref={panel} className="drawer" data-lenis-prevent role="dialog" aria-modal="true" aria-labelledby="trade-title">
        <header className="drawer-head">
          <h2 id="trade-title">Trade now</h2>
          <button className="icon-btn" onClick={onClose} aria-label="Close trading panel">×</button>
        </header>

        <div className="seg wide" role="radiogroup" aria-label="Account">
          <button role="radio" aria-checked={account === "practice"} onClick={() => setAccount("practice")}>Practice</button>
          <button role="radio" aria-checked={account === "binance"} onClick={() => setAccount("binance")}>My account</button>
        </div>

        {account === "practice" ? (
          <p className="drawer-note">
            Fills at the live Binance price after a typical 180 ms order trip, minus the 0.10% fee. No money moves.
          </p>
        ) : !desk ? (
          <div className="desk-setup">
            <p className="drawer-note">
              Your Binance key never touches this website. A small trade desk runs on your computer, keeps the key, and
              places the order the moment you click.
            </p>
            <ol className="setup-steps">
              <li>Connect your account once: <code>python -m latlab.connect</code>. It tests the key and refuses keys that can withdraw. Start with the free <a href="https://testnet.binance.vision" target="_blank" rel="noreferrer">Binance Testnet</a>.</li>
              <li>Start the desk: <code>python -m latlab.desk</code> (add <code>--live</code> for real money).</li>
              <li>Enter the six-digit pairing code it prints.</li>
            </ol>
            <div className="code-row">
              <label className="sr" htmlFor="desk-code">Pairing code</label>
              <input id="desk-code" inputMode="numeric" autoComplete="one-time-code" maxLength={6} placeholder="Pairing code"
                value={code} onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                onKeyDown={(e) => { if (e.key === "Enter" && code.length === 6) connect(); }} />
              <button className="btn gold" disabled={code.length !== 6 || connecting} onClick={connect}>
                {connecting ? "Connecting…" : "Connect"}
              </button>
            </div>
            {deskErr && <p className="drawer-error" role="alert">{deskErr}</p>}
          </div>
        ) : (
          <div className={`desk-badge ${desk.mode}`}>
            <span>{desk.mode === "live" ? "Live account — real money" : "Binance Testnet — practice funds"}</span>
            <span className="muted">
              {fmtUsd(desk.balances.USDT ?? 0, 2)} USDT · up to {fmtUsd(desk.maxOrder)} per order
            </span>
            <button className="link" onClick={() => setDesk(null)}>Disconnect</button>
          </div>
        )}

        {ready && (
          <>
            <div className="coin-grid" role="radiogroup" aria-label="Coin">
              {ASSETS.map((a) => {
                const qa = real.quotes[a.key]?.binance;
                return (
                  <button key={a.key} role="radio" aria-checked={asset === a.key} onClick={() => setAsset(a.key)}>
                    <span className="coin-sym">{a.key}</span>
                    <span className="coin-px">{fmtPrice(qa ? (qa.bid + qa.ask) / 2 : undefined)}</span>
                  </button>
                );
              })}
            </div>

            <div className="amount-row" role="radiogroup" aria-label="Amount in USDT">
              {AMOUNTS.map((v) => (
                <button key={v} role="radio" aria-checked={amount === v} disabled={v > max} onClick={() => setAmount(v)}>
                  {v} USDT
                </button>
              ))}
            </div>

            <div className="trade-buttons">
              {(["BUY", "SELL"] as Side[]).map((s) => (
                <button key={s} className={`trade-btn ${s.toLowerCase()} ${armed === s ? "armed" : ""}`}
                  disabled={busy || !q || amount > max} onClick={() => place(s)}>
                  <span className="tb-verb">{armed === s ? `Confirm ${verb(s).toLowerCase()}` : verb(s)}</span>
                  <span className="tb-detail">
                    {amount} USDT of {asset} {q ? `at ~${fmtPrice(s === "BUY" ? q.ask : q.bid)}` : ""}
                  </span>
                </button>
              ))}
            </div>
            <p className="drawer-hint" aria-live="polite">
              {busy ? "Sending…" : armed ? `Click again within 4 s to ${verb(armed).toLowerCase()} with ${where}.` :
                `Market order, ${where}.`}
            </p>
            {!live && (
              <label className="toggle">
                <input type="checkbox" checked={askFirst} onChange={(e) => setAskFirst(e.target.checked)} />
                Ask me to confirm each order
              </label>
            )}
          </>
        )}

        {receipts.length > 0 && (
          <ol className="receipts" aria-label="Recent orders">
            {receipts.map((r) => (
              <li key={r.id} className={r.ok ? "ok" : "fail"}>
                <div className="rc-top">
                  <span className={`rc-side ${r.side.toLowerCase()}`}>{verb(r.side)}</span>
                  <span>{r.quote} USDT of {r.asset}</span>
                  <span className={`rc-where ${r.where}`}>{r.where === "practice" ? "Practice" : r.where === "live" ? "Live" : "Testnet"}</span>
                </div>
                {r.ok ? (
                  <div className="rc-body">
                    <span>{r.qty?.toPrecision(4)} {r.asset} at {fmtPrice(r.avgPrice)}</span>
                    <span>fee {fmtUsd(r.feeUsdt ?? 0, 3)}</span>
                    <span className="rc-time">{Math.round(r.timing.totalMs)} ms click to fill</span>
                  </div>
                ) : (
                  <div className="rc-body err">{r.error}</div>
                )}
              </li>
            ))}
          </ol>
        )}

        <p className="drawer-fine">
          Not investment advice. This lab&rsquo;s own data shows exchange price gaps rarely beat retail fees; trade only
          what you can afford to lose.
        </p>
      </div>
    </div>
  );
}
