#!/usr/bin/env bash
# Likely Profits — install (first time) or update, then start the trade desk on this computer.
#   curl -fsSL https://raw.githubusercontent.com/kaushik899/crypto-latency-lab/main/scripts/install-desk.sh | bash
#   ... | bash -s -- --live          # real money (keys with withdrawals off; you confirm every order)
# Needs git and Python 3.10+. Everything stays in ~/likely-profits; keys go in your system keychain.
set -euo pipefail
DIR="${LIKELY_PROFITS_DIR:-$HOME/likely-profits}"
REPO="https://github.com/kaushik899/crypto-latency-lab.git"
command -v git >/dev/null || { echo "Please install git first (macOS: xcode-select --install)."; exit 1; }
PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "Please install Python 3.10 or newer from https://www.python.org/downloads/"; exit 1; }
"$PY" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "Python 3.10+ needed (found $("$PY" -V))."; exit 1; }
if [ -d "$DIR/.git" ]; then
  echo "Updating $DIR ..."; git -C "$DIR" pull -q --ff-only
else
  echo "Installing into $DIR ..."; git clone -q --depth 1 "$REPO" "$DIR"
fi
cd "$DIR"
[ -d .venv ] || "$PY" -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt keyring
echo "Starting the desk. Your browser opens Likely Profits already paired."
exec .venv/bin/python -m latlab.desk "$@"
