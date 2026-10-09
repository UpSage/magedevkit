# Magento rebuild pipeline — shared by the DB refresh (bin/lib/refresh-engine.sh)
# and the version switcher (bin/lib/version-engine.sh). Sourced, never run.
#
# Expects log(), fail(), ui_* (bin/lib/ui.sh), PLATFORM_DIR, MAGENTO,
# ENV_FILE and EXEC_USER.

# REMOTE_STORAGE_DRIVER/BUCKET/REGION/KEY/SECRET, BASE_MEDIA_URL/
# BASE_STATIC_URL/COOKIE_DOMAIN, MAGENTO_ADMIN_URI, and OPENSEARCH_INDEX_PREFIX
# all come from the active env file (gitignored) — a per-machine choice
# bin/local/setup makes once (or bin/local/sync-db's own local-only prompts
# can change on any later run) — never hardcoded here, since this file is
# committed to git. Callable again below, after bin/sync-db runs, since that
# script can rewrite any of these to the same file from its own process
# (which can't export values back into this one).
load_env() {
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a

    # CHANGE_ME means bin/local/setup's remote-storage prompt hasn't run on
    # this box yet (or the env file predates this var) — treat it as unset
    # (resolved below) instead of literally passing "CHANGE_ME" through to
    # setup:config:set.
    [ "${REMOTE_STORAGE_DRIVER:-}" = "CHANGE_ME" ] && REMOTE_STORAGE_DRIVER=""

    # Only fills in bucket/region if the env file above didn't already set
    # them (e.g. a per-machine override) — see bin/remote-storage.sh's own
    # comment.
    # shellcheck disable=SC1091
    source "$PLATFORM_DIR/bin/remote-storage.sh"
    # No driver chosen: S3 only if a bucket is configured, else local disk.
    if [ -z "${REMOTE_STORAGE_DRIVER:-}" ]; then
        if [ -n "$REMOTE_STORAGE_BUCKET" ]; then REMOTE_STORAGE_DRIVER=aws-s3; else REMOTE_STORAGE_DRIVER=file; fi
    fi

    : "${MAGENTO_MODE:=developer}"
    case "$MAGENTO_MODE" in
        default|developer|production) ;;
        *) fail "MAGENTO_MODE='$MAGENTO_MODE' in ${ENV_NAME:-env/.env.local} — expected default, developer, or production." ;;
    esac
}

# Every "Class ... does not exist" failure chased before this reset was in
# place traced back to it not having happened yet: long-lived php-fpm workers
# can carry stale OPcache/realpath-cache state from before this refresh (or
# an earlier interrupted compile). Every "$MAGENTO" call bootstraps the CLI's
# own command list against generated/'s interceptors — even a plain
# config:set, not just setup:di:compile — so this reset has to run before ANY
# of them, and (see the retry loop around setup:di:compile below) needs to be
# re-runnable on its own if a compile attempt still fails for some other reason.
reset_compile_state() {
    log "Stopping nginx/varnish to prevent live requests from racing the compile ..."
    "$PLATFORM_DIR/bin/platform" stop nginx varnish

    log "Restarting php to clear stale worker/opcache state ..."
    "$PLATFORM_DIR/bin/platform" restart php

    # Single-owner model: php-fpm and $EXEC_USER are the same identity now
    # (see docker-compose.local.yml's php `user:` override), so a
    # partial compile can never leave behind files this delete can't unlink —
    # no root escalation or chgrp/chmod dance needed, unlike before.
    log "Clearing generated code for a clean compile ..."
    "$PLATFORM_DIR/bin/platform" exec --user "$EXEC_USER" php rm -rf generated/code generated/metadata
    "$PLATFORM_DIR/bin/platform" exec --user "$EXEC_USER" php mkdir -p generated/code generated/metadata
}

# Everything after the database is in place: permissions, composer install,
# compiled-code reset, platform config, mode, setup:upgrade, DI compile,
# static content, reindex, cache flush, maintenance off (and the production
# lock). Exits via fail() on any error.
magento_rebuild() {
    # A previous production refresh leaves the tree locked read-only — open it
    # for composer/compile, and match ownership to FILE_OWNERSHIP (in case it
    # was just switched). Re-locked at the very end if MAGENTO_MODE=production.
    ui_task_start "Preparing file permissions"
    "$PLATFORM_DIR/bin/permissions/unlock"
    "$PLATFORM_DIR/bin/permissions/apply"
    ui_task_done

    # --user pins this to the CLI identity for FILE_OWNERSHIP (see
    # bin/lib/identity.sh and README's file-permissions section).
    ui_task "Installing Composer dependencies" "$PLATFORM_DIR/bin/composer" install

    # bin/sync-db's own local-only prompts (base/media/static URL, remote
    # storage driver/bucket/region/key/secret, AWS profile) may have just
    # rewritten any of these into $ENV_FILE — re-read it so the config:set calls
    # below apply what was just chosen, in this same run, instead of whatever
    # was loaded before bin/sync-db ran.
    load_env

    ui_task_start "Resetting compiled code"
    reset_compile_state
    ui_task_done

    # shellcheck disable=SC1091
    source "$PLATFORM_DIR/bin/lib/magento-config-engine.sh"
    ui_task_start "Applying platform configuration"
    apply_magento_config
    ui_task_done

    # Production is switched to only after compile + static deploy below
    # (--skip-compilation), so the compile retry loop still applies to it.
    if [ "$MAGENTO_MODE" != "production" ]; then
        log "Setting $MAGENTO_MODE mode ..."
        ui_task "Setting $MAGENTO_MODE mode" "$MAGENTO" deploy:mode:set "$MAGENTO_MODE"
    fi

    # shellcheck disable=SC1091
    source "$PLATFORM_DIR/bin/lib/db-version-override.sh"
    write_db_version_override

    log "Running setup:upgrade ..."
    ui_task "Upgrading the database schema (setup:upgrade)" "$MAGENTO" setup:upgrade --no-interaction

    # Retried rather than a single bare call: this is the one step every
    # real failure chased today happened during, and the reset above — while
    # now believed to cover every known cause — is cheap enough to redo that
    # there's no reason to let one unforeseen flake kill the whole refresh
    # outright. 3 attempts total; each retry re-runs the full reset (fresh php
    # workers, freshly wiped generated/) before trying again.
    ui_task_start "Compiling code (setup:di:compile)"
    log "Compiling DI ..."
    COMPILE_ATTEMPT=1
    until "$MAGENTO" setup:di:compile; do
        if [ "$COMPILE_ATTEMPT" -ge 3 ]; then
            fail "setup:di:compile failed after $COMPILE_ATTEMPT attempts"
        fi
        COMPILE_ATTEMPT=$((COMPILE_ATTEMPT + 1))
        log "setup:di:compile failed — resetting and retrying (attempt $COMPILE_ATTEMPT/3) ..."
        reset_compile_state
    done
    ui_task_done

    # Deliberately NOT wiped before regenerating (tried that here once — see
    # git history — and reverted it): setup:static-content:deploy -f already
    # forces a full regenerate every run, so wiping first didn't reduce the
    # work, it only removed the fallback. If a regen run ever comes up even
    # slightly short on one file, the previous run's copy staying in place is
    # what keeps the site serving instead of a hole with nothing behind it —
    # pub/static.php's on-demand fallback can't be relied on to cover
    # every gap. Leaves stale assets from a removed/renamed module or theme
    # behind (e.g. stale output from an abandoned custom module) — real, but
    # purely cosmetic disk clutter, not worth trading site reliability for. A
    # real fix for that needs a targeted
    # prune of specifically-disabled-module directories, designed and tested
    # on its own, not a blanket wipe in this routine path.
    log "Deploying static content ..."
    ui_task "Deploying static content" "$MAGENTO" setup:static-content:deploy -f

    if [ "$MAGENTO_MODE" = "production" ]; then
        log "Setting production mode (already compiled above) ..."
        ui_task "Setting production mode" "$MAGENTO" deploy:mode:set production --skip-compilation
    fi

    log "Restarting nginx/varnish ..."
    ui_task "Restarting the web servers" "$PLATFORM_DIR/bin/platform" start nginx varnish

    log "Reindexing ..."
    ui_task "Reindexing" "$MAGENTO" indexer:reindex

    log "Flushing cache ..."
    ui_task "Flushing caches" "$MAGENTO" cache:flush

    log "Ensuring maintenance mode is off ..."
    ui_task "Turning maintenance mode off" "$MAGENTO" maintenance:disable

    if [ "$MAGENTO_MODE" = "production" ]; then
        ui_task "Locking code (production mode)" "$PLATFORM_DIR/bin/permissions/lock"
    fi
}
