# Full Magento rebuild engine — sourced by bin/local/rebuild once it has
# resolved ENV_FILE/EXEC_USER. Never invoke this file directly.
#
# Composer alone doesn't finish a change: new or updated modules need
# setup:upgrade, a fresh DI compile and static content before the store
# works again. So composer install/update from the dashboard run here, and
# the rebuild afterwards is the same magento_rebuild the refresh and version
# wizards use (bin/lib/magento-rebuild.sh): permissions, composer install,
# compiled-code reset, platform config, mode, setup:upgrade, di:compile
# (with retries), static content, reindex, cache flush, maintenance off and
# the production lock.
#
# REBUILD_COMPOSER: empty | install | update. REBUILD_PACKAGES: optional
# space-separated vendor/package names for update.
#
# Expects the caller to have already defined: log(), fail(), ui_* helpers,
# PLATFORM_DIR, MAGENTO, ENV_FILE, and EXEC_USER.

# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/magento-rebuild.sh"
load_env
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/stack.sh"

MAGENTO_DIR="$(cd "$MAGENTO_DIR" 2>/dev/null && pwd)" \
    || fail "$MAGENTO_DIR not found — run the setup wizard first."
[ -f "$MAGENTO_DIR/composer.json" ] || fail "No Magento project in $MAGENTO_DIR (composer.json missing)."

COMPOSER_MODE="${REBUILD_COMPOSER:-}"
[ "$COMPOSER_MODE" = none ] && COMPOSER_MODE=""
read -r -a PACKAGES <<< "${REBUILD_PACKAGES:-}"
composer_check "$COMPOSER_MODE" "${PACKAGES[@]}"

LOCK_BEFORE="$(file_sha256 "$MAGENTO_DIR/composer.lock")"

# ------------------------------------------------------------------
# 1. Stack check
# ------------------------------------------------------------------

ui_step rebuild 1
ensure_stack_running

# ------------------------------------------------------------------
# 2. Composer
# ------------------------------------------------------------------

ui_step rebuild 2
if [ -z "$COMPOSER_MODE" ]; then
    log "No composer run asked for — the rebuild still runs composer install, which only installs what composer.lock lists."
else
    # A production-mode store is locked read-only; open it for composer.
    ui_task_start "Preparing file permissions"
    "$PLATFORM_DIR/bin/permissions/unlock"
    "$PLATFORM_DIR/bin/permissions/apply"
    ui_task_done
    if [ "$COMPOSER_MODE" = install ]; then
        ui_task "composer install" "$PLATFORM_DIR/bin/composer" install --no-interaction \
            || fail "composer install failed — see the output above."
    else
        ui_task "composer update${PACKAGES[*]:+ ${PACKAGES[*]}}" "$PLATFORM_DIR/bin/composer" update --no-interaction "${PACKAGES[@]}" \
            || fail "composer update failed — composer.lock is unchanged if it stopped before writing it. See the output above."
    fi
fi

# ------------------------------------------------------------------
# 3. Rebuild
# ------------------------------------------------------------------

ui_step rebuild 3
magento_rebuild

# ------------------------------------------------------------------
# 4. Done
# ------------------------------------------------------------------

ui_step rebuild 4
log "Rebuild complete."
ui_result link "Storefront" "https://${APP_DOMAIN}/"
ui_result link "Admin panel" "https://${APP_DOMAIN}/${MAGENTO_ADMIN_URI:-admin}/"
ui_result text "Composer" "$(case "$COMPOSER_MODE" in install) echo "composer install, then a full rebuild" ;; update) echo "composer update${PACKAGES[*]:+ ${PACKAGES[*]}}, then a full rebuild" ;; *) echo "full rebuild (composer.lock as it was)" ;; esac)"
if [ "$(file_sha256 "$MAGENTO_DIR/composer.lock")" != "$LOCK_BEFORE" ]; then
    ui_result text "composer.lock" "Changed — test the store, then commit composer.lock (dashboard → Git)."
fi
ui_result domain "Domain" "$APP_DOMAIN"
ui_result command "Rebuild again" "$(cmd_hint rebuild-wizard)"
