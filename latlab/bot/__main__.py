"""Crypto Latency Lab auto-trader.

    python -m latlab.bot run                       # paper trading on live data, top 30 markets (no keys)
    python -m latlab.bot run --mode testnet        # real orders on exchange testnets (fake money)
    python -m latlab.bot run --mode live --i-understand-real-money
                                                    # real money, only via strategies whose paper record
                                                    # has opened the performance gate
    python -m latlab.bot status                     # gate and P&L per strategy
    python -m latlab.bot kill                       # stop all real trading now (creates ~/.latlab/STOP)
    python -m latlab.bot resume                     # remove the kill switch
    python -m latlab.bot replay data/recordings/leadlag-<time>   # run every strategy on a recording

Most people control it from the website instead: run ``python -m latlab.desk`` and open Auto-trader.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

from .. import connect
from ..feeds.exchanges import Quote, allow
from .brokers import CcxtBroker
from .engine import Engine
from .ledger import Ledger
from .risk import KILL_FILE, RiskLimits
from .runner import ROOT, BotRunner, load_fees


async def real_brokers(mode: str, venues) -> dict:
    out = {}
    for v in venues:
        creds = connect.load(v, mode)
        if not creds:
            continue
        b = CcxtBroker(v, creds, sandbox=(mode == "testnet"))
        try:
            await b.start()
            out[v] = b
            print(f"  {v}: connected ({mode})")
        except Exception as e:  # noqa: BLE001
            print(f"  {v}: could not connect ({type(e).__name__}: {e})")
            await b.close()
    return out


async def run(a):
    limits = RiskLimits(order_usdt=a.order, max_daily_loss_usdt=a.max_daily_loss, max_open=a.max_open)
    r = BotRunner(a.mode, a.top, a.venues.split(","), a.trade_venues.split(",") if a.trade_venues else None, limits)
    brokers = {}
    if a.mode != "paper":
        brokers = await real_brokers(a.mode, r.venues)
        if not brokers:
            print(f"No {a.mode} account connected. Run: python -m latlab.connect")
            return
    await r.start(brokers)
    t_end = time.time() + a.minutes * 60 if a.minutes else float("inf")
    try:
        while time.time() < t_end:
            await asyncio.sleep(10)
            s = r.snapshot()
            line = " | ".join(f"{x['name']}: {x['ready']}/{x['markets']} calibrated, paper {x['paper']['n']} trades "
                              f"{x['paper']['pnl']:+.2f}" for x in s["strategies"])
            print(f"{time.strftime('%H:%M:%S')} {sum(s['markets'].values())} markets · {line}"
                  + (f" · HALTED: {s['risk']['halted']}" if s["risk"]["halted"] else ""), flush=True)
    finally:
        await r.stop()
        for b in brokers.values():
            await b.close()


def replay(folder: Path, out: Path | None, latency_ms: float, models: Path | None = None) -> dict:
    import pandas as pd

    df = pd.read_csv(folder / "quotes.csv").sort_values("recv_ts")
    allow(df.asset.unique())
    e = Engine(fees_bps=load_fees(), ledger=Ledger(), paper_latency_s=latency_ms / 1000)
    if models and models.exists():
        print(f"continuing from {e.import_models(json.loads(models.read_text()))} calibrated models")
    has_qty = "bid_qty" in df.columns
    for row in df.itertuples(index=False):
        exch = getattr(row, "exch_ts", None)
        q = Quote(row.venue, row.asset, float(row.bid), float(row.ask),
                  float(exch) if exch == exch and exch is not None else None, float(row.recv_ts),
                  float(row.bid_qty) if has_qty and row.bid_qty == row.bid_qty else None,
                  float(row.ask_qty) if has_qty and row.ask_qty == row.ask_qty else None)
        e.on_quote(q)
        e.tick(q.recv_ts)
    end = float(df.recv_ts.max()) + 120
    for k in range(240):          # let open positions finish
        e.tick(end - 120 + k * 0.5)
    snap = e.snapshot(end)
    snap["minutes"] = float((df.recv_ts.max() - df.recv_ts.min()) / 60)
    snap["latency_ms"] = latency_ms
    if out:
        out.write_text(json.dumps(snap, indent=1, default=float))
    if models:
        models.parent.mkdir(parents=True, exist_ok=True)
        models.write_text(json.dumps(e.export_models()))
    return snap


def status():
    lg = Ledger(ROOT / "data/bot/ledger.sqlite")
    from .ledger import Gate
    g = Gate(lg)
    for s in ("leadlag", "imbalance", "reversion"):
        st = g.status(s)
        live = lg.stats("live", s)
        print(f"{s:10s} gate {'OPEN ' if st['open'] else 'shut '} ({st['why']}) · paper {st['n']} trades "
              f"{st['pnl']:+.2f} USDT · live {live['n']} trades {live['pnl']:+.2f} USDT")
    print("kill switch:", "ON" if KILL_FILE.exists() else "off")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--mode", choices=["paper", "testnet", "live"], default="paper")
    r.add_argument("--i-understand-real-money", action="store_true")
    r.add_argument("--top", type=int, default=30, help="how many Binance markets to watch")
    r.add_argument("--venues", default="binance,coinbase,kraken,okx,bybit")
    r.add_argument("--trade-venues", default="", help="only trade on these (default: any connected)")
    r.add_argument("--order", type=float, default=20.0, help="USDT per trade")
    r.add_argument("--max-daily-loss", type=float, default=10.0)
    r.add_argument("--max-open", type=int, default=3)
    r.add_argument("--minutes", type=float, default=0)
    p = sub.add_parser("replay")
    p.add_argument("folder")
    p.add_argument("--latency-ms", type=float, default=150)
    p.add_argument("--out")
    p.add_argument("--models", help="JSON file to load calibration from and save it back to (learning across sessions)")
    sub.add_parser("status")
    sub.add_parser("kill")
    sub.add_parser("resume")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    if a.cmd == "run":
        if a.mode == "live" and not a.i_understand_real_money:
            raise SystemExit("Live mode spends real money. Add --i-understand-real-money to confirm.")
        try:
            asyncio.run(run(a))
        except KeyboardInterrupt:
            pass
    elif a.cmd == "replay":
        f = Path(a.folder)
        s = replay(f, Path(a.out) if a.out else f / "bot.json", a.latency_ms, Path(a.models) if a.models else None)
        for x in s["strategies"]:
            print(f"{x['name']:10s} {x['ready']}/{x['markets']} calibrated · paper {x['paper']['n']} trades, "
                  f"{x['paper']['pnl']:+.3f} USDT, mean {x['paper']['mean_bps']} bp")
    elif a.cmd == "status":
        status()
    elif a.cmd == "kill":
        KILL_FILE.parent.mkdir(parents=True, exist_ok=True)
        KILL_FILE.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"))
        print(f"Kill switch ON ({KILL_FILE}). No real orders will be placed.")
    elif a.cmd == "resume":
        KILL_FILE.unlink(missing_ok=True)
        print("Kill switch off.")


if __name__ == "__main__":
    main()
