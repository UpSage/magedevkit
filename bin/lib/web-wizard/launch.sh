#!/bin/bash
# Starts the browser wizard for one flow — `setup` (bin/local/setup),
# `refresh` (bin/refresh), `version` (bin/version), `pipeline`
# (bin/local/pipeline), `rebuild [install|update [packages…]]`
# (bin/local/rebuild) or `ownership single|two-owner`
# (bin/ownership/<model>; started by the dashboard, since it may need the sudo
# password, which the wizard asks for on its page), or `account add|import|login
# NAME` (bin/local/aws-account: MageDevKit's AWS connections — no instance).
# Called by
# bin/local/{setup,refresh,version,pipeline,rebuild}-wizard and the dashboard;
# see bin/lib/web-wizard/server.py.
#
#   --no-browser   print the link only, don't open a browser
#   --port N       listen on a fixed port (default: any free port)

set -e

FLOW="${1:?usage: launch.sh setup|refresh|version|pipeline|rebuild [install|update [packages…]]|ownership MODEL|account add|import|login NAME [--no-browser] [--port N]}"
shift
MODEL=""
if [ "$FLOW" = ownership ]; then
    MODEL="${1:-}"
    shift || true
fi
# rebuild [install|update [vendor/package …]] — a composer run first.
REBUILD_COMPOSER=""
REBUILD_PACKAGES=""
if [ "$FLOW" = rebuild ] && [[ "${1:-}" =~ ^(install|update)$ ]]; then
    REBUILD_COMPOSER="$1"
    shift
    while [ $# -gt 0 ] && [[ "$1" != --* ]]; do
        REBUILD_PACKAGES="${REBUILD_PACKAGES:+$REBUILD_PACKAGES }$1"
        shift
    done
fi
export REBUILD_COMPOSER REBUILD_PACKAGES
# account add | import | login NAME — MageDevKit's own AWS connections. They
# belong to no instance, so the wizard runs in this repo (logs/ here).
ACCOUNT_ACTION=""
ACCOUNT_NAME=""
if [ "$FLOW" = account ]; then
    ACCOUNT_ACTION="${1:-}"
    shift || true
    if [ "$ACCOUNT_ACTION" = login ]; then
        ACCOUNT_NAME="${1:-}"
        shift || true
    fi
    unset PLATFORM_INSTANCE_DIR
fi
export ACCOUNT_ACTION ACCOUNT_NAME
PLATFORM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/ui.sh"
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/paths.sh"

OPEN_BROWSER=1
export WIZARD_PORT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --no-browser) OPEN_BROWSER=0 ;;
        --port) shift; WIZARD_PORT="${1:?--port needs a number}" ;;
        *) fail "Unknown option '$1' (expected --no-browser or --port N)." ;;
    esac
    shift
done

case "$FLOW" in
    setup) SCRIPT="$PLATFORM_DIR/bin/local/setup"; TERMINAL_CMD="bin/local/setup" ;;
    refresh)
        SCRIPT="$PLATFORM_DIR/bin/refresh"; TERMINAL_CMD="bin/refresh"
        require_env_file
        ;;
    version)
        SCRIPT="$PLATFORM_DIR/bin/version"; TERMINAL_CMD="bin/version"
        require_env_file
        ;;
    pipeline)
        SCRIPT="$PLATFORM_DIR/bin/local/pipeline"; TERMINAL_CMD="$(cmd_hint pipeline)"
        require_env_file
        ;;
    rebuild)
        SCRIPT="$PLATFORM_DIR/bin/local/rebuild"; TERMINAL_CMD="$(cmd_hint rebuild)"
        require_env_file
        ;;
    ownership)
        case "$MODEL" in
            single|two-owner) ;;
            *) fail "Usage: launch.sh ownership single|two-owner" ;;
        esac
        SCRIPT="$PLATFORM_DIR/bin/ownership/$MODEL"; TERMINAL_CMD="$(cmd_hint ownership "$MODEL")"
        require_env_file
        ;;
    account)
        case "$ACCOUNT_ACTION" in
            add|import) ;;
            login) [[ "$ACCOUNT_NAME" =~ ^[A-Za-z0-9._-]{1,64}$ ]] || fail "Usage: launch.sh account login NAME" ;;
            *) fail "Usage: launch.sh account add|import|login NAME" ;;
        esac
        SCRIPT="$PLATFORM_DIR/bin/local/aws-account"; TERMINAL_CMD="mdk aws $ACCOUNT_ACTION${ACCOUNT_NAME:+ $ACCOUNT_NAME}"
        ;;
    *) fail "Unknown flow '$FLOW' (expected setup, refresh, version, pipeline, rebuild, ownership or account)." ;;
esac

command -v python3 >/dev/null 2>&1 \
    || fail "python3 isn't installed, so the browser wizard can't start — use $TERMINAL_CMD in the terminal instead (or: sudo apt install python3)."

# Refuse early (before opening a browser) if a setup/refresh already runs
# here; the child script takes the real lock itself.
if command -v flock >/dev/null 2>&1; then
    exec 8<>"$LOCK_FILE"
    flock -n 8 || fail "Another wizard (setup, refresh, version switch, pipeline …) is already running here ($(cat "$LOCK_FILE" 2>/dev/null || echo 'unknown')). Wait for it to finish, or stop it first."
    flock -u 8
    exec 8>&-
fi

WIZARD_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
export WIZARD_TOKEN
WIZARD_URL_FILE="$(mktemp)"
export WIZARD_URL_FILE
trap 'rm -f "$WIZARD_URL_FILE"' EXIT

python3 "$PLATFORM_DIR/bin/lib/web-wizard/server.py" "$FLOW" "$INSTANCE_DIR" "$SCRIPT" &
SERVER_PID=$!

# Wait for the server to publish its URL (it picks a free port).
for _ in $(seq 1 50); do
    [ -s "$WIZARD_URL_FILE" ] && break
    kill -0 "$SERVER_PID" 2>/dev/null || { wait "$SERVER_PID"; exit $?; }
    sleep 0.1
done
URL="$(cat "$WIZARD_URL_FILE")"
[ -n "$URL" ] || { kill "$SERVER_PID" 2>/dev/null; fail "The wizard server didn't start."; }

case "$FLOW" in
    refresh) TITLE="Database refresh wizard" ;;
    version) TITLE="Magento version wizard" ;;
    pipeline) TITLE="Pipeline wizard" ;;
    rebuild) TITLE="Magento rebuild" ;;
    ownership) TITLE="File ownership switch" ;;
    account) TITLE="AWS connection wizard" ;;
    *) TITLE="Magento setup wizard" ;;
esac
STOPS="${FLOW/version/version switch}"
[ "$FLOW" = account ] && STOPS="wizard"
cat <<MSG

  $TITLE is running.

  Open:  $URL

  Keep this terminal open until it finishes — closing it stops the $STOPS.
  Refreshing or closing the browser tab is safe; reopen the link to continue.
  Ctrl+C twice stops it.

MSG

if [ "$OPEN_BROWSER" = 1 ]; then
    # shellcheck disable=SC1091
    source "$PLATFORM_DIR/bin/lib/web-wizard/open-browser.sh"
    open_browser "$URL"
fi

# Ctrl+C belongs to the server (it asks for a second press); the launcher
# just waits for it.
trap '' INT
set +e
wait "$SERVER_PID"
exit $?
