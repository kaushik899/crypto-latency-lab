"""Today's setups, small-account sizing, and an honest after-the-close track record.

``scan_today`` looks at today's bars for every stock the research covered. A stock is a SETUP when
its rule's breakout fired in the last couple of bars (so the entry is still fresh). Each setup gets:

* a plan: entry ~ last price, stop from the rule, target at the rule's R multiple;
* the Time Machine's odds (10,000 Monte Carlo replays of this stock's own past bars);
* a size for a small account: whole shares, never more than the budget, max loss <= risk_pct;
* a status: VALIDATED (the rule passed the walk-forward test and so did this stock) or
  WATCH (a breakout under a rule that did NOT pass: shown for learning, never for money).

``score_day`` replays every published setup against the real bars after the close and appends the
result to the track record. Nothing here places an order.
"""

from __future__ import annotations

import datetime as dt
import math

from .edge import FLAT_SLOT, LAST_ENTRY_SLOT, Costs, Rule, run_rule, summarize
from .toolkit import timemachine

FRESH_BARS = 2


def parse_rule(key: str) -> Rule:
    orb, stop, tgt = key.split("-")
    return Rule(int(orb[3:]), "or_" + stop, 0.0 if tgt == "close" else float(tgt[:-1]))


def size(entry: float, stop: float, account: float, risk_pct: float, budget: float | None = None,
         fractional: bool = True) -> dict:
    """Whole shares when they fit; otherwise fractional shares (Alpaca allows them on market and stop
    day orders, not on brackets) so a $100 account can still trade a $400 stock at 1% risk."""
    budget = min(budget or account, account)
    risk_ps = entry - stop
    max_loss = account * risk_pct / 100
    if risk_ps <= 0:
        return {"shares": 0, "cost": 0.0, "max_loss": 0.0, "risk_per_share": 0.0, "fractional": False,
                "note": "stop is not below the entry"}
    whole = min(math.floor(max_loss / risk_ps), math.floor(budget / entry))
    frac = False
    if whole >= 1:
        shares = whole
    elif fractional:
        shares = math.floor(min(max_loss / risk_ps, budget / entry) * 1e4) / 1e4
        frac = shares > 0
    else:
        shares = 0
    note = None
    if frac:
        note = "fractional shares: sent as a market buy plus a separate stop (no bracket for fractions)"
    elif shares == 0:
        note = "does not fit the budget and the risk limit"
    return {"shares": shares, "cost": round(shares * entry, 2), "max_loss": round(shares * risk_ps, 2),
            "risk_per_share": round(risk_ps, 2), "fractional": frac, "note": note}


def plan_for(sym: str, today: list[dict], history: list[tuple[str, list[dict]]], rule: Rule,
             account: float, risk_pct: float, budget: float | None, replays: int = 10000) -> dict | None:
    """The live plan if the rule's breakout fired within FRESH_BARS bars, else None."""
    tm = timemachine()
    n = rule.or_minutes // 5
    if len(today) <= n:
        return None
    slots = [b["slot"] for b in today]
    if slots != list(range(len(today))):
        return None
    oh, ol = tm.opening_range(today, n)
    k = next((i for i in range(n, min(len(today), LAST_ENTRY_SLOT)) if today[i]["c"] > oh), None)
    if k is None or len(today) - 1 - k > FRESH_BARS:
        return None
    last = today[-1]["c"]
    entry = round(last, 2)
    stop = tm.rule_stop({"stop_rule": rule.stop}, oh, ol, entry, True)
    if stop >= entry:
        return None              # price already fell back through the stop level
    risk = entry - stop
    target = round(entry + (rule.target_r or 2.0) * risk, 2)
    mc = None
    if len(history) >= tm.MIN_SESSIONS:
        pool = tm.build_pool(history)
        res = tm.simulate(pool, entry, stop, target, len(today) - 1, FLAT_SLOT, replays=replays)
        mc = {k_: res[k_] for k_ in ("p_target", "p_stop", "p_neither", "p_green", "avg_R", "r_at_target",
                                     "median_close", "p5_close", "p95_close", "bands")}
        mc["verdict"] = tm.verdict(res)
        lv = tm.lean(res)
        mc["lean"] = round(lv[0], 3) if lv else None
    return {"symbol": sym, "rule": rule.key, "breakout_slot": k, "breakout_time": today[k]["start"],
            "or_high": round(oh, 2), "or_low": round(ol, 2), "entry": entry, "stop": stop, "target": target,
            "target_is_proxy": not rule.target_r, "time_machine": mc,
            "size": size(entry, stop, account, risk_pct, budget),
            "bars": [[b["start"], round(b["o"], 2), round(b["h"], 2), round(b["l"], 2), round(b["c"], 2)]
                     for b in today]}


def risk_checks(plan: dict, account: float, risk_pct: float, now_et: dt.datetime) -> list[dict]:
    mins = now_et.hour * 60 + now_et.minute
    rr = (plan["target"] - plan["entry"]) / max(1e-9, plan["entry"] - plan["stop"])
    checks = [
        {"check": "max loss within budget", "ok": plan["size"]["shares"] > 0
         and plan["size"]["max_loss"] <= account * risk_pct / 100 + 1e-9,
         "detail": f"${plan['size']['max_loss']:.2f} at risk of ${account * risk_pct / 100:.2f} allowed"},
        {"check": "reward at least 1.5x risk", "ok": rr >= 1.5 - 1e-9,
         "detail": f"{rr:.2f} : 1"},
        {"check": "entry window 9:45-15:30 ET", "ok": 9 * 60 + 45 <= mins <= 15 * 60 + 30,
         "detail": now_et.strftime("%H:%M ET")},
        {"check": "Time Machine leans to the target", "ok": bool(plan.get("time_machine"))
         and (plan["time_machine"].get("lean") or 0) >= 0.05,
         "detail": (plan.get("time_machine") or {}).get("verdict", "not enough history")},
    ]
    return checks


def score_day(picks: list[dict], sessions: dict[str, list[dict]], costs: Costs | None = None) -> list[dict]:
    """Replay each published pick on the full day's bars (entry at the bar after the publish time)."""
    costs = costs or Costs()
    out = []
    for p in picks:
        D = sessions.get(p["symbol"])
        if not D or len(D) < FLAT_SLOT + 1:
            continue
        rule = parse_rule(p["rule"])
        # replay the rule but only keep the trade that matches the published breakout
        trades = [t for t in run_rule(p["symbol"], [(p["date"], D)], rule, costs)
                  if t.entry_slot == p["breakout_slot"] + 1]
        if trades:
            t = trades[0]
            out.append({**{k: p[k] for k in ("date", "symbol", "rule", "status")},
                        "entry": t.entry, "exit": t.exit, "outcome": t.outcome, "R": t.R, "net_bps": t.net_bps,
                        "pnl_per_share": round(t.exit - t.entry, 4)})
    return out


def track_summary(rows: list[dict]) -> dict:
    from .edge import Trade
    groups = {}
    for status in ("VALIDATED", "WATCH"):
        tr = [Trade(r["symbol"], r["date"], r["rule"], 0, r["entry"], 0, None, r["exit"], r["outcome"],
                    r["R"], r["net_bps"]) for r in rows if r["status"] == status]
        groups[status] = summarize(tr)
    return groups
