"""Load the vendored Day Trader toolkit (vendor/day-trader-skill, MIT, reviewed commit 3d0f4ef) as modules.

The toolkit's scripts are standalone files, not a package; this imports them by path so the stock
lab reuses their tested Yahoo parsing, NYSE calendar, universe filter and Time Machine.
"""

from __future__ import annotations

import importlib.util
import sys
from functools import lru_cache
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[2] / "vendor" / "day-trader-skill"


@lru_cache(maxsize=None)
def load(skill: str, script: str):
    path = VENDOR / skill / script
    name = f"daytrader_{skill.replace('-', '_')}_{Path(script).stem}"
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def scanner():
    return load("trader-scanner", "scan.py")


def timemachine():
    return load("trader-timemachine", "timemachine.py")
