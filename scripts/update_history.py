"""Fold one scheduled research session into the website's history (web/src/data/live-history.json)."""
import json
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HIST = ROOT / "web/src/data/live-history.json"
KEEP = 180


def main(folder: str, where: str):
    f = Path(folder)
    ll = json.loads((f / "leadlag.json").read_text())
    bot = json.loads((f / "bot.json").read_text())
    markets = json.loads((f / "markets.json").read_text()) if (f / "markets.json").exists() else {}
    q = pd.read_csv(f / "quotes.csv", usecols=["recv_ts", "venue", "asset"])
    catch = {}
    for a, d in ll["assets"].items():
        for v, x in d["catchup_ms"].items():
            if x["median"] is not None and x["n"]:
                c = catch.setdefault(v, {"w": 0.0, "n": 0})
                c["w"] += x["median"] * x["n"]
                c["n"] += x["n"]
    session = {
        "id": time.strftime("%Y%m%dT%H%M", time.gmtime(q.recv_ts.min())),
        "recordedAt": pd.Timestamp(q.recv_ts.min(), unit="s", tz="UTC").isoformat(),
        "minutes": round(ll["minutes"], 1),
        "where": where,
        "quotes": int(len(q)),
        "markets": {v: int(n) for v, n in q.groupby("venue").asset.nunique().items()},
        "binanceMarkets": markets.get("binance", []),
        "feedDelayMs": {k: round(v, 1) for k, v in ll["feed_delay_ms"].items() if v == v},
        "catchupMs": {v: {"median": round(c["w"] / c["n"]), "n": c["n"]} for v, c in catch.items()},
        "gaps": ll["gaps_total"],
        "bot": [{"name": s["name"], "markets": s["markets"], "ready": s["ready"], "observations": s["observations"],
                 "paper": s["paper"], "best": s["best"][:3]} for s in bot["strategies"]],
        "botCounts": {k: bot["counts"].get(k, 0) for k in ("observations", "labels", "signals", "below_cost")},
    }
    hist = json.loads(HIST.read_text()) if HIST.exists() else {"sessions": []}
    hist["sessions"] = [s for s in hist["sessions"] if s["id"] != session["id"]] + [session]
    hist["sessions"] = hist["sessions"][-KEEP:]
    hist["updated"] = session["recordedAt"]
    HIST.write_text(json.dumps(hist, indent=1, default=float))
    print(f"history: {len(hist['sessions'])} sessions; this one {session['minutes']} min, {session['quotes']:,} quotes")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "GitHub Actions (US East)")
