# Setup wizard, part 1 of 11 — Step 1: mkcert, Docker, folders; the env file.
# Sourced in order by bin/lib/setup-engine.sh (top-level script, shared
# variables); never run on its own.

ui_step setup 1
ui_task_start "Checking mkcert, Docker and folders"
log "Checking environment ..."

if ! command -v mkcert >/dev/null 2>&1; then
    install_mkcert  # bin/lib/tools.sh
else
    log "mkcert already installed."
fi

command -v docker >/dev/null 2>&1 || fail "'docker' not found on PATH — see LOCAL_SETUP.md's prerequisites checklist."
docker ps >/dev/null 2>&1 || fail "Docker isn't reachable (docker ps failed) — is Docker Desktop running with WSL2 integration enabled? See LOCAL_SETUP.md Step 2."
log "✓ Docker"

# No longer a hard requirement that this already exists — the "Magento
# source code" step below can populate it (GitHub clone or a fresh
# install). mkdir -p is a no-op if it's already there, so this is a
# zero-behavior-change for anyone who already cloned it manually per
# LOCAL_SETUP.md.
mkdir -p "$MAGENTO_DIR"
MAGENTO_DIR="$(cd "$MAGENTO_DIR" && pwd)"
log "✓ Required directories"

log "Preflight passed."
ui_task_done

# ------------------------------------------------------------------
# Environment file
# ------------------------------------------------------------------

if [ ! -f "$ENV_FILE" ]; then
    log "Creating $ENV_NAME from the example ..."
    cp "$PLATFORM_DIR/env/.env.local.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE"  # it will hold DB, admin and AWS secrets
else
    log "$ENV_NAME already exists."
fi

set -a
# shellcheck disable=SC1090
source "$ENV_FILE"
set +a
