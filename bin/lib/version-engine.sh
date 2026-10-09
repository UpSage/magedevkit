# Magento version switcher engine — sourced by bin/local/version once it has
# resolved ENV_FILE/EXEC_USER. Never invoke this file directly.
#
# Upgrade: to any newer version/patch in the m2/ catalog, applying its stack
# pins (PHP, MariaDB, OpenSearch, ...), then composer + the same rebuild a
# refresh runs.
# Roll back (downgrade): Magento can't revert schema changes or data patches
# in place, so going back means restoring a snapshot. One is taken
# automatically before every switch — database dump, composer.json/lock,
# app/etc/config.php and the stack pins — in db/snapshots/<APP_NAME>/.
#
# Non-interactive use: PRESET_VERSION_ACTION=upgrade|rollback, plus
# PRESET_TARGET_VERSION=2.4.9 (upgrade) or PRESET_SNAPSHOT=<name or path>
# (rollback).
#
# Expects the caller to have already defined: log(), fail(), ui_* helpers,
# set_env_var, PLATFORM_DIR, MAGENTO, ENV_FILE, and EXEC_USER.

# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/magento-rebuild.sh"
load_env
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/stack.sh"
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/m2-catalog.sh"
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/db-backup.sh"

MAGENTO_DIR="$(cd "$MAGENTO_DIR" 2>/dev/null && pwd)" \
    || fail "$MAGENTO_DIR not found — run bin/local/setup first."
[ -f "$MAGENTO_DIR/composer.json" ] || fail "No Magento project in ${MAGENTO_DIR} (composer.json missing) — run bin/local/setup first."

db_credentials_from_env

SNAPSHOT_ROOT="$DB_DIR/snapshots/${APP_NAME:-app}"
: "${VERSION_SNAPSHOTS_KEEP:=5}"

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

# 0 if version $1 sorts strictly before $2 (2.4.8 < 2.4.8-p1 < 2.4.9).


# community | enterprise, from the product metapackage composer.json
# requires (blank if it requires neither).
magento_edition() {
    if grep -q '"magento/product-enterprise-edition"' "$MAGENTO_DIR/composer.json"; then
        printf 'enterprise'
    elif grep -q '"magento/product-community-edition"' "$MAGENTO_DIR/composer.json"; then
        printf 'community'
    fi
}

# Args joined with ", " (blank for none).
join_list() {
    local out
    out="$(printf '%s, ' "$@")"
    [ $# -gt 0 ] && printf '%s' "${out%, }"
    return 0
}

# "PHP 8.4 → 8.5, ..." for the selected catalog entry (blank if
# nothing changes). Reads M2_BASE_DIR/M2_PATCH_DIR.
catalog_pin_changes() {
    local k new out=()
    for k in "${M2_STACK_KEYS[@]}"; do
        new="$(m2_get "$k")"
        [ -n "$new" ] && [ "$new" != "${!k:-}" ] && out+=("${k%_VERSION} ${!k:-unset} → $new")
    done
    join_list "${out[@]}"
}

# Same, for a snapshot's stack.env ($1).
snapshot_pin_changes() {
    local k new out=()
    for k in "${M2_STACK_KEYS[@]}"; do
        new="$(snapshot_value "$1" "$k")"
        [ -n "$new" ] && [ "$new" != "${!k:-}" ] && out+=("${k%_VERSION} ${!k:-unset} → $new")
    done
    join_list "${out[@]}"
}

# $1=stack.env $2=KEY -> value (blank if absent).
snapshot_value() {
    env_get "$1" "$2"
}

# Snapshot folders, newest first (one path per line).
list_snapshots() {
    [ -d "$SNAPSHOT_ROOT" ] || return 0
    find "$SNAPSHOT_ROOT" -mindepth 1 -maxdepth 1 -type d -name '*_*' -printf '%f\n' \
        | sort -r | while IFS= read -r d; do
            [ -f "$SNAPSHOT_ROOT/$d/stack.env" ] && [ -f "$SNAPSHOT_ROOT/$d/db.sql.gz" ] && printf '%s\n' "$SNAPSHOT_ROOT/$d"
        done
    return 0
}

# "2.4.8 · taken 2026-10-07 14:03" for snapshot folder $1.
snapshot_label() {
    local name ts
    name="$(basename "$1")"
    ts="${name%%_*}"
    printf '%s · taken %s-%s-%s %s:%s' "$(snapshot_value "$1/stack.env" MAGENTO_VERSION)" \
        "${ts:0:4}" "${ts:4:2}" "${ts:6:2}" "${ts:9:2}" "${ts:11:2}"
}

# Saves the current state as a snapshot of version $1. Sets SNAPSHOT_DIR.
take_snapshot() {
    local version="$1" k
    SNAPSHOT_DIR="$SNAPSHOT_ROOT/$(date '+%Y%m%d-%H%M%S')_$version"
    mkdir -p "$SNAPSHOT_DIR"
    ui_task_start "Saving a snapshot of Magento $version (database + composer files)"
    log "Snapshot → ${SNAPSHOT_DIR#"$INSTANCE_DIR/"}"
    db_dump_to "$SNAPSHOT_DIR/db.sql.gz" \
        || { rm -rf "$SNAPSHOT_DIR"; fail "Saving the database snapshot failed — nothing was changed."; }
    cp "$MAGENTO_DIR/composer.json" "$SNAPSHOT_DIR/"
    [ -f "$MAGENTO_DIR/composer.lock" ] && cp "$MAGENTO_DIR/composer.lock" "$SNAPSHOT_DIR/"
    [ -f "$MAGENTO_DIR/app/etc/config.php" ] && cp "$MAGENTO_DIR/app/etc/config.php" "$SNAPSHOT_DIR/"
    {
        printf 'MAGENTO_VERSION=%s\n' "$version"
        for k in "${M2_STACK_KEYS[@]}"; do printf '%s=%s\n' "$k" "${!k:-}"; done
    } > "$SNAPSHOT_DIR/stack.env"
    log "✓ Snapshot saved ($(du -sh "$SNAPSHOT_DIR" | cut -f1))."
    ui_task_done
}

# Keeps the newest VERSION_SNAPSHOTS_KEEP snapshots. Only called once a
# switch has finished, so a rollback's own source snapshot is never pruned
# mid-run.
prune_snapshots() {
    local -a old=()
    local d
    mapfile -t old < <(list_snapshots | tail -n +$((VERSION_SNAPSHOTS_KEEP + 1)))
    [ "${#old[@]}" -gt 0 ] || return 0
    log "Keeping the newest $VERSION_SNAPSHOTS_KEEP snapshots — removing ${#old[@]} older one(s)."
    for d in "${old[@]}"; do
        # list_snapshots only prints direct children of SNAPSHOT_ROOT; check anyway.
        [ "$(dirname "$d")" = "$SNAPSHOT_ROOT" ] && rm -rf -- "$d"
    done
    return 0
}

# Puts composer.json/lock (and config.php when $2=1) back from snapshot $1.
restore_code_files() {
    local snap="$1" with_config="${2:-0}"
    cp "$snap/composer.json" "$MAGENTO_DIR/composer.json"
    if [ -f "$snap/composer.lock" ]; then
        cp "$snap/composer.lock" "$MAGENTO_DIR/composer.lock"
    else
        rm -f "$MAGENTO_DIR/composer.lock"
    fi
    if [ "$with_config" = 1 ] && [ -f "$snap/config.php" ]; then
        cp "$snap/config.php" "$MAGENTO_DIR/app/etc/config.php"
    fi
}

stack_fingerprint() {
    local k
    for k in "${M2_STACK_KEYS[@]}"; do printf '%s=%s;' "$k" "${!k:-}"; done
}

rebuild_stack() {
    ui_task "Building images" "$PLATFORM_DIR/bin/platform" build
    ui_task "Starting the stack" "$PLATFORM_DIR/bin/platform" up -d
    ui_task "Waiting for services to report healthy" wait_for_healthy 180 \
        || fail "Services didn't become healthy — check 'bin/platform logs <service>'."
}

# Turns maintenance on; a broken install can't always run bin/magento, which
# mustn't stop the switch that's meant to fix it.
maintenance_on() {
    ui_task "Turning maintenance mode on" "$MAGENTO" maintenance:enable \
        || log "⚠ Couldn't enable maintenance mode — continuing."
}

open_permissions() {
    ui_task_start "Preparing file permissions"
    "$PLATFORM_DIR/bin/permissions/unlock"
    "$PLATFORM_DIR/bin/permissions/apply"
    ui_task_done
}

# Once anything has been changed, a failure leaves the store mid-switch —
# point at the snapshot that undoes it.
SWITCH_STARTED=0
version_exit_hint() {
    local rc=$?
    if [ "$rc" -ne 0 ] && [ "$SWITCH_STARTED" = 1 ] && [ -n "${SNAPSHOT_DIR:-}" ]; then
        log "The switch stopped partway. To go back to where you started, run:"
        log "  PRESET_VERSION_ACTION=rollback PRESET_SNAPSHOT=$(basename "$SNAPSHOT_DIR") bin/local/version"
        log "(or $(cmd_hint version-wizard) → Roll back → $(snapshot_label "$SNAPSHOT_DIR"))."
    fi
}
trap version_exit_hint EXIT

# ------------------------------------------------------------------
# 1. Stack check · 2. Current version
# ------------------------------------------------------------------

ui_step version 1
ensure_stack_running

ui_step version 2
IFS=$'\t' read -r CURRENT_VERSION CURRENT_FROM <<< "$(detect_magento_version)"
[ -n "$CURRENT_VERSION" ] \
    || fail "Couldn't read the installed Magento version from magento/composer.lock or composer.json."
EDITION="$(magento_edition)"
log "Installed: Magento $CURRENT_VERSION (${EDITION:-unknown} edition, from $CURRENT_FROM)."
# Imported stores never had these recorded — keep the env file current.
[ "${MAGENTO_VERSION:-}" = "$CURRENT_VERSION" ] || set_env_var "$ENV_FILE" MAGENTO_VERSION "$CURRENT_VERSION"

mapfile -t SNAPSHOTS < <(list_snapshots)

# ------------------------------------------------------------------
# 3. Choose action
# ------------------------------------------------------------------

ui_step version 3
ACTION="${PRESET_VERSION_ACTION:-}"
if [ -z "$ACTION" ]; then
    ui_interactive || fail "Nothing to do — set PRESET_VERSION_ACTION=upgrade|rollback when running without a terminal."
    case "$(prompt_menu "Magento $CURRENT_VERSION is installed. What would you like to do?" 1 \
            'Upgrade to a newer Magento version' \
            "Roll back to an earlier snapshot (downgrade) — ${#SNAPSHOTS[@]} available")" in
        1) ACTION=upgrade ;;
        2) ACTION=rollback ;;
    esac
fi
case "$ACTION" in
    upgrade|rollback) ;;
    *) fail "PRESET_VERSION_ACTION='$ACTION' — expected upgrade or rollback." ;;
esac

# ------------------------------------------------------------------
# 4. Choose target · 5. Review & confirm
# ------------------------------------------------------------------

ui_step version 4
if [ "$ACTION" = upgrade ]; then
    [ -n "$EDITION" ] || fail "magento/composer.json doesn't require magento/product-community-edition or magento/product-enterprise-edition — this store's version can't be switched with the m2 catalog."
    TARGETS=()
    while IFS= read -r v; do
        version_lt "$CURRENT_VERSION" "$v" && TARGETS+=("$v")
    done < <(m2_list_versions)
    [ "${#TARGETS[@]}" -gt 0 ] \
        || fail "Magento $CURRENT_VERSION is already the newest version in the m2/ catalog (add newer ones there — see m2/README.md)."

    if [ -n "${PRESET_TARGET_VERSION:-}" ]; then
        TARGET_VERSION="$PRESET_TARGET_VERSION"
        printf '%s\n' "${TARGETS[@]}" | grep -qxF "$TARGET_VERSION" \
            || fail "PRESET_TARGET_VERSION=$TARGET_VERSION isn't a newer version in the m2/ catalog (newer ones: ${TARGETS[*]})."
    else
        LABELS=("${TARGETS[@]}")
        LABELS[-1]+=" (latest, default)"
        CHOICE="$(prompt_menu "Upgrade Magento $CURRENT_VERSION to which version?" "${#TARGETS[@]}" "${LABELS[@]}")"
        TARGET_VERSION="${TARGETS[$((CHOICE - 1))]}"
    fi
    m2_select_detected "$TARGET_VERSION" || fail "No m2/ catalog entry for $TARGET_VERSION."
    PIN_CHANGES="$(catalog_pin_changes)"

    ui_step version 5
    ui_review "Upgrade Magento $CURRENT_VERSION → $TARGET_VERSION" \
        "Project" "${APP_NAME:-app}" \
        "Current version" "$CURRENT_VERSION" \
        "New version" "$TARGET_VERSION (magento/product-$EDITION-edition)" \
        "Stack changes" "${PIN_CHANGES:-none}" \
        "Safety snapshot" "Saved first in db/snapshots/${APP_NAME:-app}/ — roll back to it any time" \
        "While it runs" "The store is in maintenance mode" \
        || cancel "Cancelled — no changes made."
    if ui_web; then
        ui_confirm_destructive "Upgrade project '${APP_NAME:-app}' from Magento $CURRENT_VERSION to $TARGET_VERSION?" "${APP_NAME:-app}" \
            || cancel "Cancelled — no changes made."
    fi
else
    [ "${#SNAPSHOTS[@]}" -gt 0 ] \
        || fail "No snapshots yet for ${APP_NAME:-app} — one is saved automatically before every upgrade, so there's nothing to roll back to."
    if [ -n "${PRESET_SNAPSHOT:-}" ]; then
        if [ -d "$PRESET_SNAPSHOT" ]; then RESTORE_DIR="$(cd "$PRESET_SNAPSHOT" && pwd)"; else RESTORE_DIR="$SNAPSHOT_ROOT/$PRESET_SNAPSHOT"; fi
        printf '%s\n' "${SNAPSHOTS[@]}" | grep -qxF "$RESTORE_DIR" \
            || fail "PRESET_SNAPSHOT=$PRESET_SNAPSHOT isn't a snapshot in ${SNAPSHOT_ROOT#"$INSTANCE_DIR/"}/."
    else
        LABELS=()
        for s in "${SNAPSHOTS[@]}"; do LABELS+=("$(snapshot_label "$s")"); done
        CHOICE="$(prompt_menu "Roll back to which snapshot? (newest first)" 1 "${LABELS[@]}")"
        RESTORE_DIR="${SNAPSHOTS[$((CHOICE - 1))]}"
    fi
    TARGET_VERSION="$(snapshot_value "$RESTORE_DIR/stack.env" MAGENTO_VERSION)"
    PIN_CHANGES="$(snapshot_pin_changes "$RESTORE_DIR/stack.env")"

    ui_step version 5
    ui_review "Roll back Magento $CURRENT_VERSION → $TARGET_VERSION" \
        "Project" "${APP_NAME:-app}" \
        "Current version" "$CURRENT_VERSION" \
        "Restore" "$(snapshot_label "$RESTORE_DIR")" \
        "Database" "Replaced with the snapshot's — changes since then are lost" \
        "Stack changes" "${PIN_CHANGES:-none}" \
        "Safety snapshot" "The current state is saved first, so this can be undone too" \
        || cancel "Cancelled — no changes made."
    if ui_web; then
        ui_confirm_destructive "Roll back project '${APP_NAME:-app}' to Magento $TARGET_VERSION and replace its database with the snapshot's?" "${APP_NAME:-app}" \
            || cancel "Cancelled — no changes made."
    fi
fi

# ------------------------------------------------------------------
# 6. Snapshot
# ------------------------------------------------------------------

ui_step version 6
take_snapshot "$CURRENT_VERSION"

# ------------------------------------------------------------------
# 7. Switching
# ------------------------------------------------------------------

ui_step version 7
SWITCH_STARTED=1
maintenance_on
open_permissions

if [ "$ACTION" = upgrade ]; then
    # The catalog's PHP applies even when composer.json pins
    # config.platform.php — that pin is moved along below.
    PROJECT_PHP=""
    BEFORE="$(stack_fingerprint)"
    apply_m2_stack_pins 0
    [ "$(stack_fingerprint)" = "$BEFORE" ] || rebuild_stack

    PINNED_PHP="$(project_php_version)"
    if [ -n "$PINNED_PHP" ] && [ "$PINNED_PHP" != "$PHP_VERSION" ]; then
        log "composer.json pins PHP $PINNED_PHP (config.platform.php) — moving it to $PHP_VERSION."
        "$PLATFORM_DIR/bin/composer" config platform.php "$PHP_VERSION.0"
    fi

    # A composer failure (usually a third-party extension that doesn't
    # support the new version yet) leaves the database untouched — put the
    # composer files back and stop.
    composer_failed() {
        log "Composer couldn't update to $TARGET_VERSION — the output above names the package that blocks it."
        restore_code_files "$SNAPSHOT_DIR"
        ui_task "Restoring the previous dependencies" "$PLATFORM_DIR/bin/composer" install || true
        "$MAGENTO" maintenance:disable >/dev/null 2>&1 || true
        SWITCH_STARTED=0
        fail "Upgrade to $TARGET_VERSION failed at composer — still on $CURRENT_VERSION, database unchanged. (If the stack versions changed, roll back with bin/local/version to restore them too.)"
    }
    PRODUCT="magento/product-$EDITION-edition"
    if grep -q '"magento/composer-root-update-plugin"' "$MAGENTO_DIR/composer.json"; then
        # Also updates the root composer.json's other Magento-owned entries.
        ui_task "Pointing composer.json at Magento $TARGET_VERSION" \
            "$PLATFORM_DIR/bin/composer" require-commerce "$PRODUCT" "$TARGET_VERSION" --no-update \
            || composer_failed
    else
        ui_task "Pointing composer.json at Magento $TARGET_VERSION" \
            "$PLATFORM_DIR/bin/composer" require "$PRODUCT:$TARGET_VERSION" --no-update \
            || composer_failed
    fi
    ui_task "Downloading Magento $TARGET_VERSION (composer update)" "$PLATFORM_DIR/bin/composer" update \
        || composer_failed
else
    # Stack pins back to the snapshot's. Data volumes a pin change can't
    # reuse are dropped — safe, the database comes back from the dump and
    # search is rebuilt by the reindex.
    DB_RESET=0
    OS_RESET=0
    BEFORE="$(stack_fingerprint)"
    for k in "${M2_STACK_KEYS[@]}"; do
        new="$(snapshot_value "$RESTORE_DIR/stack.env" "$k")"
        cur="${!k:-}"
        [ -n "$new" ] && [ "$new" != "$cur" ] || continue
        [ "$k" = MARIADB_VERSION ] && [ -n "$cur" ] && version_lt "$new" "$cur" && DB_RESET=1
        [ "$k" = OPENSEARCH_VERSION ] && [ -n "$cur" ] && [ "${cur%%.*}" != "${new%%.*}" ] && OS_RESET=1
        set_env_var "$ENV_FILE" "$k" "$new"
        printf -v "$k" '%s' "$new"
        log "✓ $k=$new"
    done
    if [ "$DB_RESET" = 1 ]; then
        log "MariaDB is going back to an older version, which can't open the current data volume — recreating it (the database is restored from the snapshot)."
        "$PLATFORM_DIR/bin/platform" rm -sf mariadb >/dev/null 2>&1 || true
        docker volume rm "${APP_NAME:-app}-db-data" >/dev/null
    fi
    if [ "$OS_RESET" = 1 ]; then
        log "OpenSearch changes major version — recreating its index volume (rebuilt by the reindex)."
        "$PLATFORM_DIR/bin/platform" rm -sf opensearch >/dev/null 2>&1 || true
        docker volume rm "${APP_NAME:-app}-opensearch-data" >/dev/null 2>&1 || true
    fi
    [ "$(stack_fingerprint)" = "$BEFORE" ] || rebuild_stack

    restore_code_files "$RESTORE_DIR" 1
    ui_task "Installing Magento $TARGET_VERSION's dependencies" "$PLATFORM_DIR/bin/composer" install
    ui_task_start "Restoring the database from the snapshot (don't interrupt)"
    db_restore_from "$RESTORE_DIR/db.sql.gz"
    ui_task_done
fi

magento_rebuild

# Version-specific commands from the m2 catalog (base, then patch) —
# upgrades only; a rollback restores a state that already had them.
if [ "$ACTION" = upgrade ]; then
    for hook in "$M2_BASE_DIR/post-upgrade" "${M2_PATCH_DIR:+$M2_PATCH_DIR/post-upgrade}"; do
        if [ -n "$hook" ] && [ -x "$hook" ]; then
            ui_task "Running ${hook#"$PLATFORM_DIR/"}" \
                env PLATFORM_DIR="$PLATFORM_DIR" MAGENTO_VERSION="$TARGET_VERSION" PREVIOUS_VERSION="$CURRENT_VERSION" "$hook"
        fi
    done
fi

set_env_var "$ENV_FILE" MAGENTO_VERSION "$TARGET_VERSION"
SWITCH_STARTED=0
prune_snapshots

# ------------------------------------------------------------------
# 8. Done
# ------------------------------------------------------------------

ui_step version 8
INSTALLED="$("$MAGENTO" --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+(-p[0-9]+)?' | head -1 || true)"
log "Magento is now $TARGET_VERSION${INSTALLED:+ (bin/magento --version: $INSTALLED)}. Snapshot of $CURRENT_VERSION: ${SNAPSHOT_DIR#"$INSTANCE_DIR/"}"
ui_result link "Storefront" "https://${APP_DOMAIN}/"
ui_result link "Admin panel" "https://${APP_DOMAIN}/${MAGENTO_ADMIN_URI:-admin}/"
ui_result text "Magento version" "$CURRENT_VERSION → $TARGET_VERSION"
ui_result path "Snapshot of $CURRENT_VERSION" "$SNAPSHOT_DIR"
ui_result domain "Domain" "$APP_DOMAIN"
ui_result command "Switch version again (or roll back)" "$(cmd_hint version-wizard)"
