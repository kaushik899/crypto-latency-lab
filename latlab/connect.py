"""Connect an exchange account in one command.

    python -m latlab.connect                 # guided: pick exchange, testnet or live, paste key
    python -m latlab.connect --list          # what is connected
    python -m latlab.connect --remove binance:live

What it does: checks the key really works (reads your balance), checks the key CANNOT withdraw
funds where the exchange lets us verify that (Binance, OKX, Bybit, Coinbase) and refuses it if it
can, then stores it in ~/.latlab/credentials.json readable only by you (or the system keychain if
the ``keyring`` package is installed). The secret is typed into your own terminal and never shown,
logged or sent anywhere except the exchange.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import stat
from pathlib import Path

HOME = Path(os.environ.get("LATLAB_HOME", Path.home() / ".latlab"))
STORE = HOME / "credentials.json"

EXCHANGES = {
    "binance": {"name": "Binance", "testnet": True, "password": False,
                "help": "binance.com → Profile → API Management. Enable Spot & Margin Trading; leave Withdrawals OFF.",
                "testnet_help": "https://testnet.binance.vision → log in with GitHub → Generate HMAC key."},
    "okx": {"name": "OKX", "testnet": True, "password": True,
            "help": "okx.com → Profile → API. Permissions: Read + Trade only. Note the passphrase.",
            "testnet_help": "OKX → Trade → Demo trading → API → create a demo key."},
    "bybit": {"name": "Bybit", "testnet": True, "password": False,
              "help": "bybit.com → API. Permissions: Spot trade only; no Withdraw.",
              "testnet_help": "https://testnet.bybit.com → API → create key."},
    "kraken": {"name": "Kraken", "testnet": False, "password": False,
               "help": "kraken.com → Settings → API. Allow Query Funds + Create & Modify Orders only."},
    "coinbase": {"name": "Coinbase Advanced", "testnet": False, "password": False,
                 "help": "coinbase.com/settings/api → New key with View + Trade (no Transfer)."},
    "alpaca": {"name": "Alpaca (US stocks)", "testnet": True, "password": False,
               "help": "app.alpaca.markets → switch to your Live account → API Keys → Generate. Trading-API "
                       "keys cannot withdraw money.",
               "testnet_help": "app.alpaca.markets → Paper Trading (free, any country) → API Keys → Generate."},
}

ENV = {  # environment variables still work and take precedence
    ("binance", "live"): ("BINANCE_API_KEY", "BINANCE_API_SECRET", None),
    ("binance", "testnet"): ("BINANCE_TESTNET_KEY", "BINANCE_TESTNET_SECRET", None),
    ("alpaca", "live"): ("APCA_LIVE_KEY_ID", "APCA_LIVE_SECRET_KEY", None),
    ("alpaca", "testnet"): ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY", None),
}


# ---------------------------------------------------------------------------- store
def _load_file() -> dict:
    try:
        return json.loads(STORE.read_text())
    except (OSError, ValueError):
        return {}


def _save_file(d: dict) -> None:
    HOME.mkdir(parents=True, exist_ok=True)
    tmp = STORE.with_suffix(".tmp")
    tmp.write_text(json.dumps(d, indent=1))
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)
    tmp.replace(STORE)


def _keyring():
    try:
        import keyring  # type: ignore
        keyring.get_keyring()
        return keyring
    except Exception:  # noqa: BLE001
        return None


def save(exchange: str, mode: str, key: str, secret: str, password: str | None = None) -> str:
    d = _load_file()
    entry = {"key": key, "password": password}
    kr = _keyring()
    if kr:
        kr.set_password("latlab", f"{exchange}:{mode}", secret)
        entry["secret_in"] = "keyring"
        where = "system keychain"
    else:
        entry["secret"] = secret
        where = str(STORE)
    d[f"{exchange}:{mode}"] = entry
    _save_file(d)
    return where


def load(exchange: str, mode: str) -> dict | None:
    env = ENV.get((exchange, mode))
    if env and os.environ.get(env[0]) and os.environ.get(env[1]):
        return {"key": os.environ[env[0]], "secret": os.environ[env[1]], "password": None}
    e = _load_file().get(f"{exchange}:{mode}")
    if not e:
        return None
    secret = e.get("secret")
    if e.get("secret_in") == "keyring":
        kr = _keyring()
        secret = kr.get_password("latlab", f"{exchange}:{mode}") if kr else None
    return {"key": e["key"], "secret": secret, "password": e.get("password")} if secret else None


def connected() -> list[str]:
    out = [k for k in _load_file()]
    for (ex, mode), (k, s, _) in ENV.items():
        if os.environ.get(k) and os.environ.get(s) and f"{ex}:{mode}" not in out:
            out.append(f"{ex}:{mode} (env)")
    return out


def remove(name: str) -> bool:
    d = _load_file()
    if name not in d:
        return False
    if d[name].get("secret_in") == "keyring" and (kr := _keyring()):
        try:
            kr.delete_password("latlab", name)
        except Exception:  # noqa: BLE001
            pass
    del d[name]
    _save_file(d)
    return True


# ---------------------------------------------------------------------------- verification
async def verify(exchange: str, mode: str, creds: dict) -> tuple[bool, str, dict]:
    """(ok, message, balances). Refuses keys that can withdraw, where the exchange tells us."""
    if exchange == "alpaca":
        return await verify_alpaca(mode, creds)
    import ccxt.async_support as ccxt

    ex = getattr(ccxt, exchange)({"apiKey": creds["key"], "secret": creds["secret"], "password": creds.get("password"),
                                  "enableRateLimit": True, "options": {"defaultType": "spot"}})
    if mode == "testnet":
        ex.set_sandbox_mode(True)
    try:
        bal = await ex.fetch_balance()
        free = {k: v for k, v in (bal.get("free") or {}).items() if v}
        note = "Withdrawal permission could not be checked automatically on this exchange — make sure it is OFF."
        if mode == "live":
            try:
                if exchange == "binance":
                    r = await ex.sapi_get_account_apirestrictions()
                    if r.get("enableWithdrawals"):
                        return False, "This key can WITHDRAW funds. Create a key with withdrawals disabled.", {}
                    if not r.get("enableSpotAndMarginTrading"):
                        return False, "This key cannot trade spot. Enable 'Spot & Margin Trading' for it.", {}
                    note = "Verified: trading on, withdrawals off."
                elif exchange == "okx":
                    r = await ex.private_get_account_config()
                    perm = (r.get("data") or [{}])[0].get("perm", "")
                    if "withdraw" in perm:
                        return False, "This key can WITHDRAW funds. Create a Read + Trade key instead.", {}
                    note = f"Verified permissions: {perm}."
                elif exchange == "bybit":
                    r = await ex.private_get_v5_user_query_api()
                    perms = (r.get("result") or {}).get("permissions") or {}
                    if any("Withdraw" in v for v in perms.values() if isinstance(v, list)):
                        return False, "This key can WITHDRAW funds. Create a key without Withdraw.", {}
                    note = "Verified: no withdraw permission."
                elif exchange == "coinbase":
                    r = await ex.v3_private_get_brokerage_key_permissions()
                    if r.get("can_transfer"):
                        return False, "This key can TRANSFER funds. Create a View + Trade key instead.", {}
                    if not r.get("can_trade"):
                        return False, "This key cannot trade. Add the Trade permission.", {}
                    note = "Verified: trade on, transfer off."
            except Exception as e:  # noqa: BLE001
                note = f"Could not read key permissions ({type(e).__name__}). Make sure withdrawals are OFF."
        return True, note, free
    except Exception as e:  # noqa: BLE001
        return False, f"The exchange rejected this key: {type(e).__name__}: {str(e)[:200]}", {}
    finally:
        await ex.close()


async def verify_alpaca(mode: str, creds: dict) -> tuple[bool, str, dict]:
    from .stocks.alpaca import Alpaca
    c = Alpaca(creds["key"], creds["secret"], live=mode == "live")
    await c.start()
    try:
        a = await c.account()
    except Exception as e:  # noqa: BLE001
        return False, f"Alpaca rejected this key: {e}", {}
    finally:
        await c.close()
    if a.get("trading_blocked") or a.get("account_blocked"):
        return False, "Alpaca says this account is blocked from trading.", {}
    note = (f"Verified {'live' if mode == 'live' else 'paper'} account ({a.get('status')}). Trading-API keys "
            "cannot withdraw. Equity ${:,.2f}.".format(float(a.get("equity") or 0)))
    if mode == "live" and float(a.get("equity") or 0) < 25000:
        note += (" Under $25,000 in a margin account, US rules allow at most 3 day trades in 5 business days "
                 "(pattern-day-trader rule); a cash account avoids it but must wait for settlement.")
    return True, note, {"USD": float(a.get("cash") or 0)}


# ---------------------------------------------------------------------------- CLI
def wizard():
    print("\nConnect an exchange or broker account to Likely Profits\n")
    names = list(EXCHANGES)
    for i, k in enumerate(names, 1):
        print(f"  {i}. {EXCHANGES[k]['name']}")
    while True:
        c = input("\nExchange number: ").strip()
        if c.isdigit() and 1 <= int(c) <= len(names):
            ex = names[int(c) - 1]; break
    info = EXCHANGES[ex]
    mode = "live"
    if info["testnet"]:
        m = input("Practice account (testnet, fake money) or real account? [T/r]: ").strip().lower()
        mode = "live" if m.startswith("r") else "testnet"
    print("\n  " + (info.get("testnet_help") if mode == "testnet" else info["help"]))
    key = input("\nAPI key: ").strip()
    secret = getpass.getpass("API secret (hidden): ").strip()
    password = getpass.getpass("API passphrase (hidden): ").strip() if info["password"] else None
    print("\nChecking the key with the exchange…")
    ok, msg, bal = asyncio.run(verify(ex, mode, {"key": key, "secret": secret, "password": password}))
    print(("  ✓ " if ok else "  ✗ ") + msg)
    if not ok:
        return 1
    shown = ", ".join(f"{v:g} {k}" for k, v in list(bal.items())[:6]) or "empty"
    print(f"  Balance: {shown}")
    where = save(ex, mode, key, secret, password)
    print(f"\nSaved {info['name']} ({mode}) to {where}.")
    print("Next: python -m latlab.desk" + (" --live" if mode == "live" else "") +
          "   then open the site → Trade now / Auto-trader" + (" / Stocks" if ex == "alpaca" else "") + ".\n")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--remove", metavar="EXCHANGE:MODE")
    a = ap.parse_args(argv)
    if a.list:
        print("\n".join(connected()) or "Nothing connected yet. Run: python -m latlab.connect")
        return 0
    if a.remove:
        print("Removed." if remove(a.remove) else "Not found.")
        return 0
    return wizard()


if __name__ == "__main__":
    raise SystemExit(main())
