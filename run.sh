#!/usr/bin/env bash
#
# discord-chat-scraper launcher.
#
# Run with NO arguments for an interactive menu:
#   ./run.sh
#
# Or pass a CLI command directly (scriptable, skips the menu):
#   ./run.sh auth                       # log in via browser, store the token
#   ./run.sh list                       # list your DM / group channels
#   ./run.sh sync                       # interactive: choose DMs or a server
#   ./run.sh sync --dms                 # straight to the DM / group list
#   ./run.sh sync --server              # straight to the server -> channel flow
#   ./run.sh sync --channel 123456789   # one channel (DM or server) by id
#   ./run.sh sync --guild 987654321     # every text channel of a server by id
#   ./run.sh --db my.db sync            # custom database path
#
# On first run it bootstraps a local virtualenv (installs the package and the
# Chromium browser used for the login step), so you never have to activate the
# venv yourself.
#
# Override the interpreter with PYTHON=python3.12 ./run.sh ...
# Skip the one-time Chromium download with SKIP_BROWSER_INSTALL=1 (only do this
# if you authenticate via the DISCORD_TOKEN env var and never use `auth`).

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$HERE/.venv"
PYTHON="${PYTHON:-python3}"

ensure_setup() {
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
}

cli() {
    "$VENV/bin/python" -m discord_scraper "$@"
}

menu() {
    while true; do
        printf '\n=== Discord Chat Scraper ===\n'
        printf '  1) Log in (capture token via browser)\n'
        printf '  2) List your DM / group chats\n'
        printf '  3) Sync a DM / group chat\n'
        printf '  4) Sync from a server\n'
        printf '  5) Sync a chat by channel ID\n'
        printf '  6) Quit\n'
        if ! read -rp 'Select [1-6]: ' choice; then
            printf '\n'           # Ctrl-D / EOF
            return 0
        fi
        case "$choice" in
            1) cli auth || echo "[run.sh] 'auth' failed (see error above)." >&2 ;;
            2) cli list || echo "[run.sh] 'list' failed (see error above)." >&2 ;;
            3) cli sync --dms || echo "[run.sh] 'sync' failed (see error above)." >&2 ;;
            4) cli sync --server || echo "[run.sh] 'sync' failed (see error above)." >&2 ;;
            5)
                if ! read -rp 'Channel ID: ' channel_id; then
                    printf '\n'
                    continue
                fi
                if [ -n "${channel_id// /}" ]; then
                    cli sync --channel "$channel_id" \
                        || echo "[run.sh] 'sync' failed (see error above)." >&2
                else
                    echo "[run.sh] No channel ID entered." >&2
                fi
                ;;
            6) return 0 ;;
            "") : ;;               # empty input -> just redraw the menu
            *) echo "[run.sh] Invalid choice: $choice" >&2 ;;
        esac
    done
}

ensure_setup

# Arguments given -> behave as a thin CLI wrapper (scriptable). No args -> menu.
if [ "$#" -gt 0 ]; then
    exec "$VENV/bin/python" -m discord_scraper "$@"
fi

menu
