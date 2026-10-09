# Setup wizard, part 6 of 11 — Step 6: Magento mode, file ownership, stack versions from the m2/ catalog, Composer credentials.
# Sourced in order by bin/lib/setup-engine.sh (top-level script, shared
# variables); never run on its own.

# ------------------------------------------------------------------
# Magento mode + file ownership — asked every run (current values as
# defaults) so either can be changed by re-running setup. Applied by
# bin/permissions/* + bin/mode/* further down (or by bin/refresh).
# ------------------------------------------------------------------

ui_step setup 6

case "${MAGENTO_MODE:-developer}" in
    default) MODE_DEFAULT_NUM=1 ;;
    production) MODE_DEFAULT_NUM=3 ;;
    *) MODE_DEFAULT_NUM=2 ;;
esac
MODE_LABELS=(
    'Default — Magento out of the box; errors are logged, not shown'
    'Developer — shows errors, no static-file caching; best for coding'
    'Production — compiled and fastest; code is locked read-only'
)
MODE_LABELS[MODE_DEFAULT_NUM - 1]+=" (current, default)"
MODE_INPUT="$(prompt_menu "Which Magento mode?" "$MODE_DEFAULT_NUM" "${MODE_LABELS[@]}")"
case "$MODE_INPUT" in
    1) MAGENTO_MODE=default ;;
    2) MAGENTO_MODE=developer ;;
    3) MAGENTO_MODE=production ;;
esac
set_env_var "$ENV_FILE" MAGENTO_MODE "$MAGENTO_MODE"

OWNERSHIP_DEFAULT_NUM=1
[ "$FILE_OWNERSHIP" = "two-owner" ] && OWNERSHIP_DEFAULT_NUM=2
[ "$MAGENTO_MODE" = "production" ] && log "Note: Adobe recommends two owners for production mode."
OWNERSHIP_LABELS=(
    "Single owner — php-fpm runs as you (simplest; Adobe's default/developer model)"
    "Two owners — php-fpm runs as www-data, you own the code (Adobe's production model; needs sudo)"
)
OWNERSHIP_LABELS[OWNERSHIP_DEFAULT_NUM - 1]+=" (current, default)"
OWNERSHIP_INPUT="$(prompt_menu "Who should own the Magento files?" "$OWNERSHIP_DEFAULT_NUM" "${OWNERSHIP_LABELS[@]}")"
case "$OWNERSHIP_INPUT" in
    1) FILE_OWNERSHIP=single ;;
    2) FILE_OWNERSHIP=two-owner ;;
esac
set_env_var "$ENV_FILE" FILE_OWNERSHIP "$FILE_OWNERSHIP"
# Re-resolve EXEC_USER/PHP_UID/PHP_GID for the rest of this run.
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/identity.sh"
log "✓ Mode: $MAGENTO_MODE, ownership: $FILE_OWNERSHIP"
[ "$MAGENTO_MODE" = "production" ] && log "Production: code will be locked read-only at the end (bin/permissions/unlock to edit)."

# ------------------------------------------------------------------
# Stack versions — Docker image versions follow the Magento version, per
# Adobe's system requirements as recorded in the m2/ catalog. Before the
# build below, so it picks them up.
# ------------------------------------------------------------------

if [ "$FRESH_SOURCE" = 1 ]; then
    apply_m2_stack_pins 0
else
    PROJECT_PHP="$(project_php_version)"
    [ -n "$PROJECT_PHP" ] && log "magento/composer.json pins PHP $PROJECT_PHP (config.platform.php) — its composer.lock is resolved for it, so PHP follows the project, not the m2 catalog."
    IFS=$'\t' read -r DETECTED_VERSION DETECTED_FROM <<< "$(detect_magento_version)"
    if [ -z "$DETECTED_VERSION" ]; then
        log "Couldn't read the Magento version from magento/composer.lock or composer.json — keeping the current stack versions."
    elif m2_select_detected "$DETECTED_VERSION"; then
        if [ "$DETECTED_FROM" = composer.json ]; then
            log "Detected Magento $DETECTED_VERSION (no usable composer.lock — version taken from composer.json's requirement)"
        else
            log "Detected Magento $DETECTED_VERSION (from $DETECTED_FROM)"
        fi
        apply_m2_stack_pins 1
    else
        log "Detected Magento $DETECTED_VERSION — no m2/ catalog entry for it (see m2/README.md); keeping the current stack versions."
    fi
    # Applies even when the catalog wasn't consulted (or its changes were
    # declined) — a PHP the lock file wasn't resolved for fails the
    # check-platform-reqs gate below anyway.
    if [ -n "$PROJECT_PHP" ] && [ "$PROJECT_PHP" != "${PHP_VERSION:-}" ]; then
        log "PHP_VERSION ${PHP_VERSION:-unset} → $PROJECT_PHP (the project's config.platform.php)"
        set_env_var "$ENV_FILE" PHP_VERSION "$PROJECT_PHP"
        PHP_VERSION="$PROJECT_PHP"
    fi
fi

# Always re-rendered (cheap) so the generated vhost ($NGINX_CONF) stays in
# sync even after a hand-edit to APP_DOMAIN in the env file without
# deleting it. The template lives in the platform repo.
mkdir -p "$NGINX_CONF_DIR"
sed "s/local\.magento\.test/$APP_DOMAIN/g" \
    "$PLATFORM_DIR/config/nginx-local/default.conf.template" \
    > "$NGINX_CONF"

# ------------------------------------------------------------------
# Magento Setup: Composer credentials
# ------------------------------------------------------------------

# Not auto-created/prompted-for — on local the developer places real
# repo.magento.com credentials in magento/auth.json themselves (or supply
# one via the auth.json step above). Just verify it's actually ready.
AUTH_JSON="$MAGENTO_DIR/auth.json"
if [ "$FRESH_SOURCE" = 1 ]; then
    [ -s "$FRESH_AUTH" ] || fail "Composer credentials for the fresh download are missing."
    log "✓ Magento Composer credentials collected for the fresh download"
else
    [ -f "$AUTH_JSON" ] \
        || fail "magento/auth.json not found — copy magento/auth.json.sample to magento/auth.json and fill in your repo.magento.com credentials, then re-run this script."

    grep -q '<public-key>\|<private-key>' "$AUTH_JSON" \
        && fail "magento/auth.json still has placeholder credentials — edit it with your real repo.magento.com public/private keys, then re-run this script."

    log "✓ Magento Composer credentials already configured"
fi
