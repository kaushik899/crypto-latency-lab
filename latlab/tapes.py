"""Turn a live recording into a tape the backtester can replay.

    python -m latlab.tapes data/recordings/20261021-093000
    python -m latlab.research --tape data/recordings/20261021-093000/tape
"""

import sys
from pathlib import Path

from .tape import Tape


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        sys.exit(__doc__)
    rec = Path(argv[0])
    tape = Tape.from_recording(rec / "ticks.csv", rec / "books.csv", rec / "markets.json", step=0.05)
    out = tape.save(rec / "tape")
    hours = tape.t[-1] / 3600
    settled = sum(1 for m in tape.markets if m.settle_price == m.settle_price)
    print(f"{out}: {hours:.2f} h, {len(tape.spot)} assets, {len(tape.markets)} markets ({settled} settled)")


if __name__ == "__main__":
    main()
