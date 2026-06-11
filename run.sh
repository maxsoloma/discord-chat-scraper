#!/usr/bin/env bash
#
# discord-chat-scraper launcher.
#
# On first run it bootstraps a local virtualenv (installs the package and the
# Chromium browser used for the login step). On every run it forwards all
# arguments to the CLI, so you never have to activate the venv yourself.
#
#   ./run.sh auth                       # log in via browser, store the token
#   ./run.sh list                       # list your DM / group channels
#   ./run.sh sync                       # interactive: pick a chat, backfill/update
#   ./run.sh sync --channel 123456789   # skip the menu
#   ./run.sh --db my.db sync            # custom database path
#
# Override the interpreter with PYTHON=python3.12 ./run.sh ...
# Skip the one-time Chromium download with SKIP_BROWSER_INSTALL=1 (only do this
# if you authenticate via the DISCORD_TOKEN env var and never use `auth`).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
PYTHON="${PYTHON:-python3}"

if [ ! -x "$VENV/bin/python" ]; then
    echo "[run.sh] First run: creating virtualenv and installing the package..." >&2
    "$PYTHON" -m venv "$VENV"
    "$VENV/bin/pip" install --upgrade pip >/dev/null
    "$VENV/bin/pip" install -e "$HERE"
fi

if [ -z "${SKIP_BROWSER_INSTALL:-}" ] && [ ! -f "$VENV/.chromium-installed" ]; then
    echo "[run.sh] Ensuring Chromium is installed for the browser login step..." >&2
    "$VENV/bin/playwright" install chromium
    touch "$VENV/.chromium-installed"
fi

exec "$VENV/bin/python" -m discord_scraper "$@"
