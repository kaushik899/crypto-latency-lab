import { useEffect, useState } from "react";
import { deskCall } from "../lib/trade";

type Acct = {
  id: string; name: string; market: "crypto" | "stocks"; practice: boolean; passphrase: boolean;
  help?: string; practiceHelp?: string; keyUrl?: string; practiceUrl?: string;
  connected: { testnet: boolean; live: boolean };
};

const INSTALL_SH = "curl -fsSL https://raw.githubusercontent.com/kaushik899/crypto-latency-lab/main/scripts/install-desk.sh | bash";
const INSTALL_PS = "irm https://raw.githubusercontent.com/kaushik899/crypto-latency-lab/main/scripts/install-desk.ps1 | iex";

function Copy({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  return (
    <button className="copy" onClick={() => { navigator.clipboard?.writeText(text).then(() => { setDone(true); setTimeout(() => setDone(false), 1600); }); }}>
      {done ? "Copied" : "Copy"}
    </button>
  );
}

/** Step 1, before pairing: one command starts the desk and opens this site already paired. */
export function StartDesk() {
  const isWin = typeof navigator !== "undefined" && /Win/i.test(navigator.platform || navigator.userAgent);
  const [os, setOs] = useState<"mac" | "win">(isWin ? "win" : "mac");
  const cmd = os === "win" ? INSTALL_PS : INSTALL_SH;
  return (
    <div className="start-desk">
      <p className="sd-step"><b>1</b> Start your private trade desk. Paste this into {os === "win" ? "PowerShell" : "Terminal"}:</p>
      <div className="seg small" role="radiogroup" aria-label="Your computer">
        <button role="radio" aria-checked={os === "mac"} onClick={() => setOs("mac")}>Mac or Linux</button>
        <button role="radio" aria-checked={os === "win"} onClick={() => setOs("win")}>Windows</button>
      </div>
      <div className="cmd-row"><code className="cmd one">{cmd}</code><Copy text={cmd} /></div>
      <p className="sd-step"><b>2</b> Your browser reopens this page, already paired. No code to type.</p>
      <p className="fine">
        It installs into a folder in your home directory and runs only on your computer. Run the same line again any
        time to update and restart. For real money, only when you are ready:{" "}
        <code>{os === "win" ? "& $HOME\\likely-profits\\.venv\\Scripts\\python.exe -m latlab.desk --live" : "… | bash -s -- --live"}</code>
      </p>
    </div>
  );
}

/** After pairing: connect any exchange or broker. The key goes from this page to your desk (127.0.0.1) only. */
export function Accounts() {
  const [list, setList] = useState<Acct[]>([]);
  const [deskMode, setDeskMode] = useState("testnet");
  const [open, setOpen] = useState<string | null>(null);
  const [mode, setMode] = useState<"testnet" | "live">("testnet");
  const [form, setForm] = useState({ key: "", secret: "", passphrase: "" });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string; bal?: Record<string, number> } | null>(null);

  const load = () => deskCall("/accounts").then((j) => { setList(j.accounts); setDeskMode(j.deskMode); }).catch(() => {});
  useEffect(() => { load(); }, []);

  const pick = (a: Acct) => {
    setOpen(open === a.id ? null : a.id);
    setMode(a.practice ? "testnet" : "live");
    setForm({ key: "", secret: "", passphrase: "" });
    setMsg(null);
  };

  const submit = async (a: Acct) => {
    setBusy(true); setMsg(null);
    try {
      const j = await deskCall("/accounts/connect", { exchange: a.id, mode, key: form.key, secret: form.secret, passphrase: form.passphrase });
      setList(j.accounts.accounts);
      setForm({ key: "", secret: "", passphrase: "" });
      const note = j.attached ? " Ready to trade now." : mode !== deskMode
        ? ` Saved for ${mode === "live" ? "real money" : "practice"}: restart the desk${mode === "live" ? " with --live" : " without --live"} to use it.`
        : " The auto-trader will use it the next time it starts.";
      setMsg({ ok: true, text: `${j.message}${note}`, bal: j.balances });
    } catch (e) {
      setMsg({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  };

  const remove = async (a: Acct, m: "testnet" | "live") => {
    const j = await deskCall("/accounts/remove", { exchange: a.id, mode: m }).catch(() => null);
    if (j) setList(j.accounts.accounts);
  };

  const groups: [string, Acct[]][] = [["Crypto exchanges", list.filter((a) => a.market === "crypto")], ["US stocks", list.filter((a) => a.market === "stocks")]];
  return (
    <div className="accounts">
      <div className="acc-head">
        <h3>Your accounts</h3>
        <span className={`desk-pill ${deskMode}`}>{deskMode === "live" ? "Desk: real money" : "Desk: practice"}</span>
      </div>
      {groups.map(([title, items]) => (
        <section key={title} className="acc-group">
          <h4>{title}</h4>
          <ul className="acc-list">
            {items.map((a) => {
              const on = a.connected.testnet || a.connected.live;
              return (
                <li key={a.id} className={`acc ${open === a.id ? "open" : ""} ${on ? "on" : ""}`}>
                  <button className="acc-row" onClick={() => pick(a)} aria-expanded={open === a.id}>
                    <span className="acc-mono" aria-hidden>{a.name.slice(0, 1)}</span>
                    <span className="acc-name">{a.name}</span>
                    <span className="acc-state">
                      {a.connected.live && <span className="tag live">Real</span>}
                      {a.connected.testnet && <span className="tag">Practice</span>}
                      {!on && <span className="muted">Connect</span>}
                    </span>
                  </button>
                  {open === a.id && (
                    <div className="acc-body">
                      {a.practice && (
                        <div className="seg small" role="radiogroup" aria-label="Account type">
                          <button role="radio" aria-checked={mode === "testnet"} onClick={() => setMode("testnet")}>
                            {a.id === "alpaca" ? "Paper (free)" : "Practice (testnet)"}</button>
                          <button role="radio" aria-checked={mode === "live"} onClick={() => setMode("live")}>Real money</button>
                        </div>
                      )}
                      <ol className="acc-steps">
                        <li>
                          Open <a href={(mode === "testnet" ? a.practiceUrl : a.keyUrl) ?? a.keyUrl} target="_blank" rel="noreferrer noopener">
                            {a.name} {mode === "testnet" ? (a.id === "alpaca" ? "paper" : "practice") : ""} API keys ↗</a>
                        </li>
                        <li>{(mode === "testnet" ? a.practiceHelp : a.help) ?? a.help}</li>
                        <li>Paste the key below. Your desk checks it with {a.name} and refuses any key that can withdraw money.</li>
                      </ol>
                      <form className="acc-form" autoComplete="off" onSubmit={(e) => { e.preventDefault(); submit(a); }}>
                        <label><span>API key</span>
                          <input value={form.key} spellCheck={false} onChange={(e) => setForm({ ...form, key: e.target.value })} /></label>
                        <label><span>Secret</span>
                          <input type="password" value={form.secret} autoComplete="new-password" onChange={(e) => setForm({ ...form, secret: e.target.value })} /></label>
                        {a.passphrase && (
                          <label><span>Passphrase</span>
                            <input type="password" value={form.passphrase} autoComplete="new-password" onChange={(e) => setForm({ ...form, passphrase: e.target.value })} /></label>
                        )}
                        <button className="btn gold" disabled={busy || !form.key || !form.secret || (a.passphrase && !form.passphrase)}>
                          {busy ? "Checking with " + a.name + "…" : `Connect ${a.name}`}</button>
                      </form>
                      {msg && (
                        <div className={msg.ok ? "acc-ok" : "drawer-error"} role="status">
                          <p>{msg.text}</p>
                          {msg.bal && Object.keys(msg.bal).length > 0 && (
                            <p className="fine">Balance: {Object.entries(msg.bal).map(([k, v]) => `${v.toLocaleString("en-US", { maximumFractionDigits: 6 })} ${k}`).join(" · ")}</p>
                          )}
                        </div>
                      )}
                      {(a.connected.testnet || a.connected.live) && (
                        <p className="fine acc-rm">
                          {a.connected.testnet && <button className="link" onClick={() => remove(a, "testnet")}>Forget practice key</button>}
                          {a.connected.live && <button className="link" onClick={() => remove(a, "live")}>Forget real key</button>}
                        </p>
                      )}
                    </div>
                  )}
                </li>
              );
            })}
          </ul>
        </section>
      ))}
      <p className="fine">
        Keys are sent from this page to the desk on your own computer (127.0.0.1) and saved there, in your system keychain
        when available. They never reach this website or GitHub. Orders still need your click, every time.
      </p>
    </div>
  );
}
