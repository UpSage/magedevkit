# @@HEADER@@
#
# One change at a time on the box — sourced by the CodeDeploy hooks and by
# deploy/scripts/box (seed-db, maintenance work). Held from BeforeInstall
# (acquire) to ValidateService (release).
#
# A deployment that fails never reaches ValidateService, so its lock stays
# behind. It's taken over when:
#   - the same application deploys again (a retry, or CodeDeploy's automatic
#     rollback) — CodeDeploy already guarantees that one isn't concurrent;
#   - or it's older than DEPLOY_LOCK_STALE_SECONDS (default 3h).
#
# Uses CodeDeploy's hook environment: APPLICATION_NAME, DEPLOYMENT_ID
# (deploy/scripts/box sets its own). Expects SHARED (lib/common.sh).

DEPLOY_LOCK_DIR="${DEPLOY_LOCK_DIR:-$SHARED/deploy.lock}"
# 55 min — just under CodeDeploy's 1h hook limit (appspec BeforeInstall).
DEPLOY_LOCK_WAIT_SECONDS="${DEPLOY_LOCK_WAIT_SECONDS:-3300}"
DEPLOY_LOCK_STALE_SECONDS="${DEPLOY_LOCK_STALE_SECONDS:-10800}"

_deploy_lock_field() {
    grep -m1 "^$1=" "$DEPLOY_LOCK_DIR/owner" 2>/dev/null | cut -d= -f2-
}

_deploy_lock_write_owner() {
    printf 'application=%s\ndeployment=%s\nsince=%s\n' \
        "${APPLICATION_NAME:-unknown}" "${DEPLOYMENT_ID:-unknown}" "$(date +%s)" \
        > "$DEPLOY_LOCK_DIR/owner"
}

deploy_lock_acquire() {
    local deadline=$((SECONDS + DEPLOY_LOCK_WAIT_SECONDS)) app dep since age
    mkdir -p "$(dirname "$DEPLOY_LOCK_DIR")"
    while true; do
        if mkdir "$DEPLOY_LOCK_DIR" 2>/dev/null; then
            _deploy_lock_write_owner
            echo "[deploy-lock] acquired by ${APPLICATION_NAME:-unknown} (${DEPLOYMENT_ID:-unknown})"
            return 0
        fi

        app="$(_deploy_lock_field application)"
        dep="$(_deploy_lock_field deployment)"
        since="$(_deploy_lock_field since)"
        age=$(( $(date +%s) - ${since:-0} ))

        if [ "$dep" = "${DEPLOYMENT_ID:-}" ]; then
            return 0
        fi
        if [ "$app" = "${APPLICATION_NAME:-}" ]; then
            echo "[deploy-lock] taking over a lock left by this application's earlier deployment $dep (it didn't finish)"
            _deploy_lock_write_owner
            return 0
        fi
        if [ "$age" -ge "$DEPLOY_LOCK_STALE_SECONDS" ]; then
            echo "[deploy-lock] taking over a stale lock (${age}s old) left by $app ($dep)"
            _deploy_lock_write_owner
            return 0
        fi
        if [ "$SECONDS" -ge "$deadline" ]; then
            echo "[deploy-lock] ERROR: $app ($dep) has held the deploy lock for ${age}s; gave up waiting after ${DEPLOY_LOCK_WAIT_SECONDS}s." >&2
            echo "[deploy-lock]   If nothing is really running, remove $DEPLOY_LOCK_DIR on the box and retry." >&2
            return 1
        fi
        echo "[deploy-lock] waiting — $app ($dep) holds the lock (${age}s so far)"
        sleep 15
    done
}

deploy_lock_release() {
    if [ "$(_deploy_lock_field deployment)" = "${DEPLOYMENT_ID:-}" ]; then
        rm -rf "$DEPLOY_LOCK_DIR"
        echo "[deploy-lock] released"
    fi
}
