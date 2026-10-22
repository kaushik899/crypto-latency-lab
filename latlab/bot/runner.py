"""Runs the engine on live feeds. Used by ``python -m latlab.bot run`` and by the trade desk."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from ..feeds.exchanges import ASSETS, TAKER_BPS, stream
from ..universe import top_assets
from .engine import Engine
from .ledger import Ledger
from .risk import KILL_FILE, RiskLimits

ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("latlab.bot")
FEES_FILE = Path.home() / ".latlab" / "fees.json"


def load_fees() -> dict:
    fees = dict(TAKER_BPS)
    try:
        fees.update({k: float(v) for k, v in json.loads(FEES_FILE.read_text()).items()})
    except (OSError, ValueError):
        pass
    return fees


class BotRunner:
    """mode: 'paper' (no keys), 'testnet' (exchange sandboxes, fake money) or 'live' (real money,
    only through strategies whose paper record has opened the gate)."""

    def __init__(self, mode: str = "paper", top: int = 30, venues=("binance", "coinbase", "kraken", "okx", "bybit"),
                 trade_venues: list[str] | None = None, limits: RiskLimits | None = None,
                 ledger_path: Path | None = None, state_path: Path | None = None):
        self.mode, self.top, self.venues = mode, top, list(venues)
        self.trade_venues = trade_venues
        self.limits = limits or RiskLimits()
        self.ledger = Ledger(ledger_path or ROOT / "data/bot/ledger.sqlite")
        self.state_path = state_path or ROOT / "data/bot/state.json"
        self.models_local = self.state_path.parent / "models-local.json"
        self.engine: Engine | None = None
        self.tasks: list[asyncio.Task] = []
        self.assets: list[str] = []
        self.error = ""

    @property
    def running(self) -> bool:
        return any(not t.done() for t in self.tasks)

    async def start(self, brokers: dict | None = None):
        if self.running:
            return
        self.assets = await top_assets(self.top)
        e = Engine(fees_bps=load_fees(), limits=self.limits, ledger=self.ledger)
        # start from what the bot has already learned: this machine's models, else the published ones
        for src in (self.models_local, ROOT / "data/bot/models.json"):
            try:
                log.info("loaded %d calibrated models from %s", e.import_models(json.loads(src.read_text())), src.name)
                break
            except (OSError, ValueError):
                continue
        e.trade_venues = set(self.trade_venues) if self.trade_venues else None
        if self.mode in ("testnet", "live"):
            e.real_brokers = dict(brokers or {})
            e.allow_real = bool(e.real_brokers)
            if self.mode == "testnet":
                e.gate.min_trades = 0          # fake money: no need to earn it first
                e.gate.status = lambda s, t=None: {"open": True, "why": "testnet: practice funds", "n": 0}
        self.engine = e
        loop = asyncio.get_running_loop()
        for v in self.venues:
            assets = self.assets if v == "binance" else ASSETS
            self.tasks.append(loop.create_task(self._pump(v, assets)))
        self.tasks.append(loop.create_task(self._clock()))
        self.tasks.append(loop.create_task(self._writer()))
        log.info("bot started: %s, %d Binance markets, venues %s", self.mode, len(self.assets), ",".join(self.venues))

    async def stop(self):
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.tasks = []
        self._write()
        self._save_models()

    def kill(self):
        KILL_FILE.parent.mkdir(parents=True, exist_ok=True)
        KILL_FILE.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"))
        if self.engine:
            self.engine.risk.kill("Stopped by kill switch.")

    async def _pump(self, venue, assets):
        async for q in stream(venue, assets):
            self.engine.on_quote(q)

    async def _clock(self):
        while True:
            await asyncio.sleep(0.02)
            self.engine.tick(time.time())

    async def _writer(self):
        k = 0
        while True:
            await asyncio.sleep(1.0)
            self._write()
            k += 1
            if k % 60 == 0:
                self._save_models()

    def _save_models(self):
        if self.engine:
            try:
                self.models_local.write_text(json.dumps(self.engine.export_models()))
            except OSError:
                pass

    def snapshot(self) -> dict:
        if not self.engine:
            return {"running": False, "mode": self.mode}
        s = self.engine.snapshot(time.time())
        s.update(running=self.running, mode=self.mode, watching=len(self.assets), error=self.error)
        return s

    def _write(self):
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(self.snapshot(), default=float))
        except OSError:
            pass
