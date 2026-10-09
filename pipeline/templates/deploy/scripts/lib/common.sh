# @@HEADER@@
#
# Shared by every script in deploy/scripts/ on the EC2 box — sourced, never
# run. The box's identity (APP, ENV_NAME, AWS_REGION, APP_HOME) comes from
# /etc/m2deploy.env, written once by the instance's user_data
# (deploy/infra/modules/compute/user-data.sh.tftpl).
#
# Layout on the box (APP_HOME = /opt/<app>):
#   magento/    the live Magento code (synced from each release)
#   deploy/     this release's deploy/ folder (compose, config, scripts)
#   shared/     .env + env.php (rendered from SSM), release.env, deploy.lock,
#               deploys.jsonl, release.json, backups/
#   incoming/   where CodeDeploy unpacks a revision before after-install.sh

set -euo pipefail

[ -f /etc/m2deploy.env ] || { echo "ERROR: /etc/m2deploy.env missing — this isn't a box created by deploy/infra." >&2; exit 1; }
set -a
# shellcheck disable=SC1091
source /etc/m2deploy.env
set +a

: "${APP:?APP missing in /etc/m2deploy.env}"
: "${ENV_NAME:?ENV_NAME missing in /etc/m2deploy.env}"
APP_HOME="${APP_HOME:-/opt/$APP}"
MAGENTO_ROOT="$APP_HOME/magento"
DEPLOY_DIR="$APP_HOME/deploy"
SHARED="$APP_HOME/shared"
BACKUP_DIR="$SHARED/backups"
export APP APP_HOME MAGENTO_ROOT DEPLOY_DIR SHARED BACKUP_DIR

LOG_TAG="${LOG_TAG:-deploy}"
log() { echo "[$LOG_TAG] $*"; }
fail() { echo "[$LOG_TAG] ERROR: $*" >&2; exit 1; }

# 0 if version $1 sorts strictly before $2.
version_lt() {
    [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$1" "$2" | sort -V | head -1)" = "$1" ]
}

# docker compose for this box's stack: the release's compose files, the pins
# from the deployed code, the box's own settings and the release's image tag.
box_compose() {
    local envs=(--env-file "$MAGENTO_ROOT/.platform/stack.env" --env-file "$SHARED/.env")
    [ -f "$SHARED/release.env" ] && envs+=(--env-file "$SHARED/release.env")
    docker compose -p "$APP" --project-directory "$DEPLOY_DIR/compose" \
        -f "$DEPLOY_DIR/compose/app.yml" -f "$DEPLOY_DIR/compose/edge.yml" "${envs[@]}" "$@"
}

# bin/magento inside the php container.
box_magento() {
    box_compose exec -T php php bin/magento "$@"
}

# Loads shared/.env (+ release.env) into this shell.
load_box_env() {
    [ -f "$SHARED/.env" ] || fail "$SHARED/.env is missing — run deploy/scripts/render-env first."
    set -a
    # shellcheck disable=SC1091
    source "$SHARED/.env"
    # shellcheck disable=SC1091
    [ -f "$SHARED/release.env" ] && source "$SHARED/release.env"
    # shellcheck disable=SC1091
    [ -f "$MAGENTO_ROOT/.platform/stack.env" ] && source "$MAGENTO_ROOT/.platform/stack.env"
    set +a
}

# Container id / image tag of a service as it runs right now (blank if not running).
running_container() { box_compose ps -q "$1" 2>/dev/null | head -1; }
running_tag() {
    local cid
    cid="$(running_container "$1")"
    [ -n "$cid" ] && docker inspect -f '{{.Config.Image}}' "$cid" | sed 's/.*://'
}
