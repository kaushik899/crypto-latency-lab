"""Stock routes for the local trade desk: preview a ticket, then place it after one explicit approval.

Flow (the Day Trader pack's three gates, adapted to a website button):
  1. POST /stocks/preview  -> the desk re-checks the plan against live account numbers and returns a
     ticket with a one-time token and a plain-English summary. Nothing is sent to the broker.
  2. The person reads the ticket and presses "Place this order" (the site never auto-confirms).
  3. POST /stocks/order {token, confirm: true} -> the one order on that ticket goes to Alpaca.
Tokens expire after 90 seconds and work once. No route places an order without a fresh ticket.
"""

from __future__ import annotations

import asyncio
import csv
import datetime as dt
import secrets
import time
from pathlib import Path

from aiohttp import web

from .alpaca import SYMBOL, Alpaca, bracket_body, fractional_bodies

TICKET_TTL = 90.0


def _now_et() -> dt.datetime:
    from .toolkit import timemachine
    return timemachine().to_eastern(dt.datetime.utcnow())


def check_ticket(symbol: str, qty: float, entry: float, stop: float, target: float, equity: float,
                 risk_pct: float, max_order: float, now_et: dt.datetime, pdt: dict | None = None) -> list[dict]:
    from .toolkit import scanner
    session = scanner().session(now_et.date())
    mins = now_et.hour * 60 + now_et.minute
    risk = (entry - stop) * qty
    rr = (target - entry) / (entry - stop) if entry > stop else 0
    checks = [
        {"check": "symbol looks like a US ticker", "ok": bool(SYMBOL.match(symbol)), "detail": symbol},
        {"check": "stop below entry below target", "ok": 0 < stop < entry < target,
         "detail": f"{stop:.2f} < {entry:.2f} < {target:.2f}"},
        {"check": "reward at least 1.5x risk", "ok": rr >= 1.5 - 1e-9, "detail": f"{rr:.2f} : 1"},
        {"check": f"order size within the desk cap (${max_order:,.0f})", "ok": 0 < qty * entry <= max_order,
         "detail": f"${qty * entry:,.2f}"},
        {"check": f"max loss within {risk_pct:g}% of equity", "ok": 0 < risk <= equity * risk_pct / 100 + 1e-6,
         "detail": f"${risk:,.2f} of ${equity * risk_pct / 100:,.2f} allowed"},
        {"check": "market open, 9:45-15:30 ET", "ok": bool(session) and 9 * 60 + 45 <= mins <= 15 * 60 + 30,
         "detail": now_et.strftime("%a %H:%M ET") if session else "market closed today"},
    ]
    if pdt is not None:
        n = int(pdt.get("daytrade_count") or 0)
        small = float(pdt.get("equity") or 0) < 25000
        checks.append({"check": "pattern-day-trader room (3 day trades / 5 days under $25k)",
                       "ok": not (pdt.get("live") and small and n >= 3),
                       "detail": f"{n} day trades used" + (" (paper account: not enforced)" if not pdt.get("live") else "")})
    return checks


class StockDesk:
    def __init__(self, desk, client: Alpaca | None, max_order: float = 200.0, risk_pct: float = 1.0,
                 log_dir: Path | None = None):
        self.desk, self.client, self.max_order, self.risk_pct = desk, client, max_order, risk_pct
        self.log_dir = log_dir or desk.log_dir
        self.tickets: dict[str, dict] = {}
        self.last_order = 0.0
        self.lock = asyncio.Lock()

    def _j(self, req, doc, status=200):
        return self.desk.cors(req, web.json_response(doc, status=status))

    def _need(self, req):
        if (bad := self.desk.guard(req)):
            return self.desk.cors(req, bad)
        if self.client is None:
            return self._j(req, {"error": "No Alpaca account connected. Run: python -m latlab.connect "
                                          "and pick Alpaca (paper is free)."}, 400)
        return None

    async def status(self, req):
        if (bad := self.desk.guard(req)):
            return self.desk.cors(req, bad)
        if self.client is None:
            return self._j(req, {"connected": False, "maxOrder": self.max_order, "riskPct": self.risk_pct})
        try:
            acct, pos, orders = await asyncio.gather(self.client.account(), self.client.positions(),
                                                     self.client.open_orders())
        except Exception as e:  # noqa: BLE001
            return self._j(req, {"error": f"Alpaca: {e}"}, 502)
        return self._j(req, {"connected": True, "live": self.client.live, "account": acct, "positions": pos,
                             "orders": orders, "maxOrder": self.max_order, "riskPct": self.risk_pct})

    async def quote(self, req):
        if (bad := self._need(req)):
            return bad
        try:
            q = await self.client.quote(req.query.get("symbol", "").upper().strip())
        except Exception as e:  # noqa: BLE001
            return self._j(req, {"error": f"No quote: {e}"}, 400)
        return self._j(req, q)

    async def live(self, req):
        if (bad := self._need(req)):
            return bad
        syms = [x.strip().upper() for x in req.query.get("symbols", "").split(",") if x.strip()]
        try:
            return self._j(req, await self.client.snapshots(syms))
        except Exception as e:  # noqa: BLE001
            return self._j(req, {"error": f"No prices: {e}"}, 400)

    async def preview(self, req):
        if (bad := self._need(req)):
            return bad
        try:
            b = await req.json()
            symbol = str(b["symbol"]).upper().strip()
            qty, entry, stop, target = (float(b[k]) for k in ("qty", "entry", "stop", "target"))
        except (KeyError, ValueError, TypeError) as e:
            return self._j(req, {"error": f"Bad ticket: {e}"}, 400)
        acct = await self.client.account()
        equity = float(acct.get("equity") or 0)
        checks = check_ticket(symbol, qty, entry, stop, target, equity, self.risk_pct, self.max_order, _now_et(),
                              {**acct, "live": self.client.live})
        frac = qty != int(qty)
        kind = ("fractional market buy + separate stop (no automatic target: close it yourself or at 15:55)"
                if frac else "bracket: market buy + take-profit + stop-loss, day only")
        summary = (f"BUY {qty:g} {symbol} ≈ ${qty * entry:,.2f} · stop {stop:.2f} · target {target:.2f} · "
                   f"max loss ≈ ${(entry - stop) * qty:,.2f} · {'REAL MONEY' if self.client.live else 'paper account'}")
        ok = all(c["ok"] for c in checks)
        token = secrets.token_urlsafe(16) if ok else None
        now = time.time()
        self.tickets = {k: v for k, v in self.tickets.items() if v["expires"] > now}
        if token:
            self.tickets[token] = {"symbol": symbol, "qty": qty, "entry": entry, "stop": stop, "target": target,
                                   "fractional": frac, "expires": now + TICKET_TTL, "summary": summary}
        return self._j(req, {"ok": ok, "token": token, "summary": summary, "kind": kind, "checks": checks,
                             "live": self.client.live, "expiresIn": TICKET_TTL if token else 0})

    async def order(self, req):
        if (bad := self._need(req)):
            return bad
        try:
            b = await req.json()
        except Exception:  # noqa: BLE001
            b = {}
        if b.get("confirm") is not True:
            return self._j(req, {"error": "An order needs confirm: true from the approval button."}, 400)
        t = self.tickets.pop(str(b.get("token", "")), None)
        if not t or t["expires"] < time.time():
            return self._j(req, {"error": "This ticket expired or was already used. Preview it again."}, 400)
        async with self.lock:
            if time.time() - self.last_order < 2.0:
                return self._j(req, {"error": "One stock order every 2 seconds."}, 429)
            self.last_order = time.time()
            t0 = time.perf_counter()
            if t["fractional"]:
                buy, stp = fractional_bodies(t["symbol"], t["qty"], t["stop"])
                st, r = await self.client.submit(buy)
                st2, r2 = (await self.client.submit(stp)) if st == 200 else (None, None)
                result = {"ok": st == 200 and st2 == 200, "orders": [r, r2]}
                if st == 200 and st2 != 200:
                    result["error"] = ("Bought, but the stop order was rejected: "
                                       f"{(r2 or {}).get('message')}. Set a stop in Alpaca now or close the position.")
            else:
                st, r = await self.client.submit(bracket_body(t["symbol"], t["qty"], t["stop"], t["target"]))
                result = {"ok": st == 200, "orders": [r]}
            if not result["ok"] and "error" not in result:
                result["error"] = (r or {}).get("message", f"HTTP {st}")
            result["ms"] = round((time.perf_counter() - t0) * 1000, 1)
        result["summary"] = t["summary"]
        self._log(t, result)
        return self._j(req, result, 200 if result["ok"] else 400)

    async def close(self, req):
        if (bad := self._need(req)):
            return bad
        b = await req.json() if req.can_read_body else {}
        sym = (b.get("symbol") or "").upper()
        if sym:
            st, r = await self.client.close_symbol(sym)
        else:
            st, r = await self.client.close_all()
        return self._j(req, {"ok": st in (200, 207), "result": r}, 200 if st in (200, 207) else 400)

    def _log(self, t, r):
        self.log_dir.mkdir(parents=True, exist_ok=True)
        path = self.log_dir / f"stock-orders-{'live' if self.client.live else 'paper'}.csv"
        new = not path.exists()
        with open(path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "symbol", "qty", "entry_plan", "stop", "target", "ok", "ms", "error"])
            w.writerow([time.strftime("%Y-%m-%dT%H:%M:%S"), t["symbol"], t["qty"], t["entry"], t["stop"], t["target"],
                        r["ok"], r.get("ms"), r.get("error", "")])

    def routes(self, app: web.Application):
        app.router.add_get("/stocks/status", self.status)
        app.router.add_get("/stocks/quote", self.quote)
        app.router.add_get("/stocks/live", self.live)
        app.router.add_post("/stocks/preview", self.preview)
        app.router.add_post("/stocks/order", self.order)
        app.router.add_post("/stocks/close", self.close)
