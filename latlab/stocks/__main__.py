"""python -m latlab.stocks research | today | score

research  rank the most liquid US stocks, pull 60 days of 5-minute bars, walk-forward test the
          ORB rule grid with costs, publish web/src/data/stocks-edge.json
today     during the session: today's fresh breakouts on the researched stocks, with plans,
          Time Machine odds and small-account sizes -> web/src/data/stocks-today.json
score     after the close: replay every published pick on the real bars -> stocks-track.json
scan      every US stock: today's chart setups (Day Trader rules), two-year base rates per setup,
          each match enriched from its own OHLCV chart -> stocks-scan.json

None of these places an order. Orders go through the local desk, one explicit approval each.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import time
from pathlib import Path

from . import daily, data, edge
from .toolkit import scanner, timemachine

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web" / "src" / "data"
PICKS = ROOT / "data" / "stocks" / "picks"


def _write(path: Path, doc) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, indent=1, default=str))
    tmp.replace(path)


def _load(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return default


def _bars(symbols, now, offline_dir=None, log=print):
    out, meta = {}, {}
    for i, sym in enumerate(symbols):
        try:
            off = offline_dir / f"{sym}.json.gz" if offline_dir else None
            m, days = data.bars_60d(sym, now_utc=now, offline=off if off and off.exists() else None,
                                    cache_minutes=0 if not offline_dir else 1e9)
            out[sym], meta[sym] = days, m
        except Exception as exc:  # one bad symbol never stops the run
            log(f"  {sym}: {exc}")
        if (i + 1) % 25 == 0:
            log(f"  bars {i + 1}/{len(symbols)}")
    return out, meta


def cmd_research(a) -> int:
    now = dt.datetime.utcnow()
    today = timemachine().to_eastern(now).date().isoformat()
    if a.symbols:
        ranked = [{"symbol": s, "price": None, "dollar_volume": None} for s in a.symbols.split(",")]
        universe_n = len(ranked)
    else:
        uni = data.universe()
        universe_n = len(uni)
        print(f"universe: {universe_n} US stocks and ADRs; ranking liquidity...")
        ranked = data.liquid(sorted(set(uni) | set(data.ETFS)), top=a.top, min_price=a.min_price)
    syms = [r["symbol"] for r in ranked]
    print(f"top {len(syms)} by dollar volume: {', '.join(syms[:12])} ...")
    bars, _ = _bars(syms, now)
    sessions = {s: data.full_sessions(d, today) for s, d in bars.items()}
    sessions = {s: v for s, v in sessions.items() if len(v) >= 20}
    costs = edge.Costs(slippage_bps=a.slippage, commission=a.commission)
    res = edge.walk_forward(sessions, costs)
    rules = res["rules"]
    best_watch = rules[0]["rule"] if rules else None
    doc = {
        "generated": now.isoformat(timespec="seconds") + "Z", "universe": universe_n,
        "researched": len(sessions), "sessions": max((len(v) for v in sessions.values()), default=0),
        "split": res["split"], "costs": res["costs"], "select_t": res["select_t"],
        "rules": rules, "chosen": res["chosen"], "watch_rule": best_watch,
        "symbols": [{"symbol": r["symbol"], "price": r["price"], "dollar_volume": r["dollar_volume"]}
                    for r in ranked if r["symbol"] in sessions],
        "eligible": res["symbols"],
    }
    _write(WEB / "stocks-edge.json", doc)
    print(f"split at {res['split']}: {len(res['chosen'])} rule(s) validated, {len(res['symbols'])} stocks eligible")
    for r in rules[:6]:
        i, o = r["in_sample"], r["out_of_sample"]
        print(f"  {r['rule']:<18} IS n={i['n']:>5} mean={i['mean_R']} t={i['t']}  OOS n={o['n']} mean={o['mean_R']} t={o['t']}")
    return 0


def cmd_today(a) -> int:
    tm = timemachine()
    sc = scanner()
    now = dt.datetime.utcnow() if not a.now else dt.datetime.fromisoformat(a.now)
    et = tm.to_eastern(now)
    d = et.date().isoformat()
    edge_doc = _load(WEB / "stocks-edge.json", None)
    if not edge_doc or edge_doc.get("pending") or not edge_doc.get("symbols"):
        print("run research first", file=sys.stderr)
        return 1
    session = sc.session(et.date())
    out = {"generated": now.isoformat(timespec="seconds") + "Z", "date": d, "now_et": et.strftime("%H:%M"),
           "market": "open" if session and session[0] <= et.time() < session[1] else "closed",
           "account": a.account, "risk_pct": a.risk_pct, "setups": []}
    if not session:
        out["market"] = "holiday or weekend"
        _write(WEB / "stocks-today.json", out)
        print("market closed today")
        return 0
    eligible = edge_doc.get("eligible", {})
    watch_rule = edge_doc.get("watch_rule")
    syms = [s["symbol"] for s in edge_doc["symbols"]][: a.top]
    bars, _ = _bars(syms, now)
    picks_path = PICKS / f"{d}.json"
    picks = _load(picks_path, [])
    seen = {(p["symbol"], p["rule"]) for p in picks}
    for sym, days in bars.items():
        today = days.get(d, [])
        hist = data.full_sessions(days, d)
        if sym in eligible:
            key, status = eligible[sym]["rule"], "VALIDATED"
        elif watch_rule:
            key, status = watch_rule, "WATCH"
        else:
            continue
        plan = daily.plan_for(sym, today, hist, daily.parse_rule(key), a.account, a.risk_pct, a.budget,
                              replays=a.replays)
        if not plan:
            continue
        plan["status"] = status
        plan["checks"] = daily.risk_checks(plan, a.account, a.risk_pct, et)
        plan["all_checks_pass"] = all(c["ok"] for c in plan["checks"]) and status == "VALIDATED"
        out["setups"].append(plan)
        if (sym, key) not in seen:
            picks.append({"date": d, "symbol": sym, "rule": key, "status": status,
                          "breakout_slot": plan["breakout_slot"], "published": out["now_et"],
                          "entry": plan["entry"], "stop": plan["stop"], "target": plan["target"]})
            seen.add((sym, key))
    out["setups"].sort(key=lambda p: (p["status"] != "VALIDATED", -((p.get("time_machine") or {}).get("lean") or -1)))
    _write(WEB / "stocks-today.json", out)
    _write(picks_path, picks)
    print(f"{d} {out['now_et']} ET: {len(out['setups'])} setups "
          f"({sum(p['status'] == 'VALIDATED' for p in out['setups'])} validated)")
    return 0


def cmd_score(a) -> int:
    now = dt.datetime.utcnow()
    track_path = WEB / "stocks-track.json"
    track = _load(track_path, {})
    track.pop("pending", None)
    track.setdefault("rows", [])
    done = {(r["date"], r["symbol"], r["rule"]) for r in track["rows"]}
    files = sorted(PICKS.glob("*.json"))
    todo = []
    for f in files:
        for p in _load(f, []):
            if (p["date"], p["symbol"], p["rule"]) not in done:
                todo.append(p)
    if todo:
        bars, _ = _bars(sorted({p["symbol"] for p in todo}), now)
        for p in todo:
            day = bars.get(p["symbol"], {}).get(p["date"])
            if not day:
                continue
            track["rows"].extend(daily.score_day([p], {p["symbol"]: day}))
    track["rows"].sort(key=lambda r: (r["date"], r["symbol"]))
    track["summary"] = daily.track_summary(track["rows"])
    track["generated"] = now.isoformat(timespec="seconds") + "Z"
    _write(track_path, track)
    s = track["summary"]
    print(f"track record: validated {s['VALIDATED']}, watch {s['WATCH']}")
    return 0


def cmd_scan(a) -> int:
    from . import scan
    doc = scan.run(min_price=a.min_price, min_volume=a.min_volume, rng=a.range, enrich_max=a.enrich)
    # served as a static file (fetched by the page), not bundled into the JavaScript
    _write(ROOT / "web" / "public" / "data" / "stocks-scan.json", doc)
    br = doc.get("base_rates", {}).get("setups", {})
    print(f"{len(doc['hits'])} matches on {doc['as_of']} ({doc['counts']})")
    for k, row in br.items():
        c = row["horizons"]["5"]["all"]
        print(f"  {k:<16} 5-day: n={c.get('n')} excess={c.get('excess_bps')} bp t={c.get('t')} "
              f"held={row['horizons']['5']['held']}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m latlab.stocks", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("research")
    r.add_argument("--top", type=int, default=150)
    r.add_argument("--min-price", type=float, default=5.0)
    r.add_argument("--symbols", help="comma list instead of the liquidity ranking")
    r.add_argument("--slippage", type=float, default=2.0, help="bp per side")
    r.add_argument("--commission", type=float, default=0.0, help="$ per order")
    t = sub.add_parser("today")
    t.add_argument("--top", type=int, default=150)
    t.add_argument("--account", type=float, default=500.0)
    t.add_argument("--risk-pct", type=float, default=1.0)
    t.add_argument("--budget", type=float, default=None, help="max $ in one position")
    t.add_argument("--replays", type=int, default=10000)
    t.add_argument("--now", help="UTC ISO time, for tests")
    sub.add_parser("score")
    s_ = sub.add_parser("scan")
    s_.add_argument("--min-price", type=float, default=5.0)
    s_.add_argument("--min-volume", type=float, default=1_000_000)
    s_.add_argument("--range", default="2y")
    s_.add_argument("--enrich", type=int, default=600, help="matches to enrich from their own OHLCV chart")
    a = p.parse_args(argv)
    t0 = time.time()
    rc = {"research": cmd_research, "today": cmd_today, "score": cmd_score, "scan": cmd_scan}[a.cmd](a)
    print(f"done in {time.time() - t0:.0f}s")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
