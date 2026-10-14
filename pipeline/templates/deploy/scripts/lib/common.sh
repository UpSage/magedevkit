# @@HEADER@@
#
# Shared by every script in deploy/scripts/ on the EC2 box — sourced, never
# run. The box's identity (APP, ENV_NAME, AWS_REGION, APP_HOME) comes from
# /etc/m2deploy.env, written once by the instance's UserData
# (deploy/infra/stack.json, resource Box).
#
# Layout on the box (APP_HOME = /opt/<app>):
#   magento/    the live Magento code (synced from each release)
#   deploy/     this release's deploy/ folder (compose, config, scripts)
#   shared/     .env + env.php (rendered from SSM), release.env, deploy.lock,
#               deploys.jsonl, release.json, backups/, awaiting-database (a
#               release is installed but the database is still empty)
#   incoming/   where CodeDeploy unpacks a revision before after-install.sh

set -euo pipefail
set +x  # never trace: these scripts handle secrets from SSM

# M2DEPLOY_ENV only for tests; on the box it is always /etc/m2deploy.env.
M2DEPLOY_ENV="${M2DEPLOY_ENV:-/etc/m2deploy.env}"
[ -f "$M2DEPLOY_ENV" ] || { echo "ERROR: $M2DEPLOY_ENV missing — this isn't a box created by deploy/infra/stack.json." >&2; exit 1; }
set -a
# shellcheck disable=SC1090
source "$M2DEPLOY_ENV"
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

# --- Database dumps ------------------------------------------------------------

BACKUPS_KEEP="${BACKUPS_KEEP:-3}"

prune_backups() {  # $1 = file name prefix
    find "$BACKUP_DIR" -maxdepth 1 -name "$1*.sql.gz" -printf '%T@ %p\n' \
        | sort -rn | tail -n +$((BACKUPS_KEEP + 1)) | cut -d' ' -f2- | xargs -r rm -f
}

# dump_db PREFIX — a full dump into backups/ (the newest BACKUPS_KEEP kept).
dump_db() {
    local dump
    mkdir -p "$BACKUP_DIR"
    dump="$BACKUP_DIR/$1-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
    log "Dumping the database → $dump"
    # shellcheck disable=SC2016
    box_compose exec -T mariadb sh -c '
        dump=$(command -v mariadb-dump || command -v mysqldump)
        exec "$dump" -uroot -p"$MARIADB_ROOT_PASSWORD" --all-databases \
            --single-transaction --quick --routines --events --triggers' \
        | gzip > "$dump"
    gzip -t "$dump" || fail "the dump $dump is corrupt — stopping before anything changes."
    log "✓ Dump written ($(du -h "$dump" | cut -f1))"
    prune_backups "$1"
}

# db_tables — how many tables the Magento database has (0 when it's empty).
db_tables() {
    # shellcheck disable=SC2016
    box_compose exec -T mariadb sh -c \
        'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD" -N -e "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema=\"$MARIADB_DATABASE\""' \
        2>/dev/null | tr -d '[:space:]'
}

# --- What runs here, for MageDevKit -----------------------------------------------

# target_setting KEY — a value from this environment's deploy/targets/<env>.env.
target_setting() {
    sed -n "s/^$1=//p" "$DEPLOY_DIR/targets/$ENV_NAME.env" 2>/dev/null | head -1 | tr -d '"'
}

# record_event RESULT [COMMAND] — one line in shared/deploys.jsonl: a deploy
# ("live", "awaiting-database") or a repair made on the box ("repair"), which
# the next deploy replaces.
record_event() {
    local commit="" tag=""
    [ -f "$SHARED/release.env" ] && commit="$(sed -n 's/^COMMIT=//p' "$SHARED/release.env")" \
        && tag="$(sed -n 's/^IMAGE_TAG=//p' "$SHARED/release.env")"
    printf '{"commit": "%s", "imageTag": "%s", "at": "%s", "deploymentId": "%s", "result": "%s", "command": "%s"}\n' \
        "$commit" "$tag" "$(date -u +%FT%TZ)" "${DEPLOYMENT_ID:-}" "$1" "${2:-}" >> "$SHARED/deploys.jsonl"
}

# publish_status STATE — the box's state (ok, awaiting-database, failed) in
# SSM /<app>/<env>/status, where MageDevKit reads it without reaching into the
# box. Nothing secret: the release, the kit version, the mode, the time. The
# box role may write this one parameter and no other.
publish_status() {
    local commit="" kit="" value
    [ -f "$SHARED/release.env" ] && commit="$(sed -n 's/^COMMIT=//p' "$SHARED/release.env")"
    value="$(python3 -c 'import json, sys
try:
    kit = str(json.load(open(sys.argv[2])).get("templateVersion", ""))
except (OSError, ValueError):
    kit = ""
print(json.dumps(dict(zip(("state", "commit", "mode", "production", "at"), sys.argv[3:]), kit=kit), separators=(",", ":")))' \
        x "$DEPLOY_DIR/pipeline.json" "$1" "$commit" "$(target_setting MAGENTO_MODE)" "$(target_setting PRODUCTION)" "$(date -u +%FT%TZ)")"
    aws ssm put-parameter --region "$AWS_REGION" --name "/$APP/$ENV_NAME/status" --type String --overwrite \
        --value "$value" >/dev/null 2>&1 || log "⚠ couldn't publish the status to SSM /$APP/$ENV_NAME/status"
}
