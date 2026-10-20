"""The research programme: how much of the "latency edge" survives fees, latency and competition?

Run with ``python -m latlab.research`` (simulation) or ``--tape data/recordings/<run>``
(real recorded data). Writes ``data/research/results.json`` and a copy for the website.
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

from .fees import fee_per_share
from .metrics import calibration, equity_curve, summarize
from .sim import SimConfig, simulate
from .strategy import StrategyParams, gap_lifetimes, run, sigma_by_asset
from .tape import Tape

ROOT = Path(__file__).resolve().parents[1]
LATENCIES = [0, 50, 100, 150, 200, 300, 500, 750, 1000, 1500, 2000]  # multiples of the 50 ms grid

REGIMES = {
    "no_fees_slow": dict(
        title="Before fees, slow competition",
        blurb="No taker fee; makers re-quote every ~2 s and rival bots react in ~400 ms. The conditions the viral posts describe.",
        sim=dict(fee_rate=0.0, comp_latency_median_ms=400, comp_threshold=0.02, mm_refresh_mean_s=2.0),
        strat=dict(fee_rate=0.0)),
    "fees_slow": dict(
        title="Taker fee, slow competition",
        blurb="The identical market, but our taker fills now pay 0.07 · p · (1 − p) per share. Isolates the cost of the fee.",
        sim=dict(fee_rate=0.0, comp_latency_median_ms=400, comp_threshold=0.02, mm_refresh_mean_s=2.0),
        strat=dict(fee_rate=0.07)),
    "fees_crowded": dict(
        title="Taker fee, crowded (today)",
        blurb="Fees, makers re-quoting every ~0.3 s off a 50 ms feed, and co-located rivals sniping in ~20 ms.",
        sim=dict(fee_rate=0.07, comp_latency_median_ms=20, comp_threshold=0.008, mm_refresh_mean_s=0.3,
                 mm_data_lag_s=0.05),
        strat=dict(fee_rate=0.07)),
}


def _clean(o):
    if isinstance(o, dict):
        return {k: _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if (o != o or o in (float("inf"), float("-inf"))) else round(float(o), 6)
    if isinstance(o, np.integer):
        return int(o)
    return o


def latency_sweep(tape: Tape, base: StrategyParams, hours: float) -> list[dict]:
    sig = sigma_by_asset(tape, base)
    out = []
    for lat in LATENCIES:
        tr = run(tape, replace(base, latency_ms=lat), sig)
        out.append(dict(latency_ms=lat, **summarize(tr, base.bankroll, hours)))
    return out


def study_tape(tape: Tape, base: StrategyParams, hours: float, deep: bool) -> dict:
    sig = sigma_by_asset(tape, base)
    tr = run(tape, base, sig)
    res = dict(summary=summarize(tr, base.bankroll, hours), latency=latency_sweep(tape, base, hours))
    if not deep:
        return res
    f = tr[tr.filled & tr.pnl.notna()]
    res["equity"] = equity_curve(tr)
    res["calibration"] = calibration(tr)
    res["by_asset"] = [dict(asset=a, **summarize(d, base.bankroll, hours)) for a, d in tr.groupby("asset")]
    res["by_window"] = [dict(window=w, **summarize(d, base.bankroll, hours)) for w, d in tr.groupby("window")]
    res["by_tau"] = [dict(bucket=str(b), n=int(len(d)), pnl=float(d.pnl.sum()), hit=float((d.pnl > 0).mean()))
                     for b, d in f.groupby(pd.cut(f.tau_s, [0, 30, 60, 120, 300, 900, 3600]), observed=True)]
    res["by_price"] = [dict(bucket=str(b), n=int(len(d)), pnl=float(d.pnl.sum()),
                            fee_per_share=float(fee_per_share(d.px_arrival).mean()))
                       for b, d in f.groupby(pd.cut(f.px_arrival, [0, .1, .3, .5, .7, .9, 1]), observed=True)]
    res["thresholds"] = [dict(entry_edge=e, **summarize(run(tape, replace(base, entry_edge=e), sig),
                                                         base.bankroll, hours))
                         for e in [0.0, 0.005, 0.01, 0.015, 0.02, 0.03, 0.05, 0.08]]
    gaps = gap_lifetimes(tape, base, sig)
    if len(gaps):
        d = gaps.duration_s.values
        res["gaps"] = dict(count=int(len(d)), per_hour=float(len(d) / hours),
                           median_s=float(np.median(d)), p90_s=float(np.percentile(d, 90)),
                           mean_peak_edge=float(gaps.peak_edge.mean()),
                           survival=[dict(ms=ms, share=float((np.round(d * 1000) > ms).mean())) for ms in LATENCIES])
    # model risk: what if our probability model is wrong?
    res["model_risk"] = []
    for name, kw in [("Normal, 5-min vol memory", {}), ("Student-t tails (ν=4)", dict(dist="student")),
                     ("Fast vol (1 min)", dict(vol_halflife_s=60)), ("Slow vol (30 min)", dict(vol_halflife_s=1800)),
                     ("Shrink fair 20% to 0.5", dict(model_haircut=0.2))]:
        pp = replace(base, **kw)
        res["model_risk"].append(dict(model=name, **summarize(run(tape, pp, sigma_by_asset(tape, pp)), base.bankroll, hours)))
    # recent trades for the website's blotter
    res["sample_trades"] = f.sort_values("t_signal").tail(60)[
        ["label", "asset", "side", "t_signal", "tau_s", "fair", "px_signal", "px_arrival", "shares", "fee", "pnl"]
    ].to_dict("records")
    return res


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--hours", type=float, default=6.0)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--extra-seeds", type=int, nargs="*", default=[11, 23, 42],
                    help="additional simulated days for the latency robustness band")
    ap.add_argument("--tape", help="folder with a recorded tape (tape.npz/tape.json) to study instead")
    ap.add_argument("--out", default=str(ROOT / "data/research/results.json"))
    a = ap.parse_args(argv)
    t0 = time.time()
    base = StrategyParams()
    results = dict(generated=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), strategy=base.to_dict(),
                   latencies=LATENCIES, regimes={})
    if a.tape:
        tape = Tape.load(a.tape)
        hours = float(tape.t[-1] / 3600)
        results.update(source="recorded", hours=hours, n_markets=len(tape.markets))
        results["regimes"]["recorded"] = dict(title="Recorded live data", blurb=a.tape,
                                              **study_tape(tape, base, hours, deep=True))
    else:
        results.update(source="simulation", hours=a.hours)
        for key, R in REGIMES.items():
            cfg = SimConfig(hours=a.hours, seed=a.seed, **R["sim"])
            tape = simulate(cfg)
            sp = replace(base, **R["strat"])
            print(f"[{key}] {len(tape.markets)} markets simulated", flush=True)
            results["regimes"][key] = dict(title=R["title"], blurb=R["blurb"], sim_config=cfg.to_dict(),
                                           n_markets=len(tape.markets),
                                           **study_tape(tape, sp, a.hours, deep=True))
            results["n_markets"] = len(tape.markets)
            runs = [[r["pnl"] for r in results["regimes"][key]["latency"]]]
            for sd in a.extra_seeds:
                runs.append([r["pnl"] for r in latency_sweep(simulate(replace(cfg, seed=sd)), sp, a.hours)])
            arr = np.array(runs)
            results["regimes"][key]["latency_band"] = [
                dict(latency_ms=lat, mean=float(arr[:, i].mean()), lo=float(arr[:, i].min()), hi=float(arr[:, i].max()),
                     positive_share=float((arr[:, i] > 0).mean()))
                for i, lat in enumerate(LATENCIES)]
            results["seeds"] = [a.seed, *a.extra_seeds]
            print(f"  latency band over {len(runs)} seeds done", flush=True)
    results["runtime_s"] = round(time.time() - t0, 1)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(_clean(results), indent=1)
    out.write_text(payload)
    web = ROOT / "web/src/data/research.json"
    if web.parent.exists():
        web.write_text(payload)
    print(f"wrote {out} in {results['runtime_s']} s")


if __name__ == "__main__":
    main()
