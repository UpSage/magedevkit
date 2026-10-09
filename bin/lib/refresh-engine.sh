# Refresh engine — sourced by bin/local/refresh once it has resolved
# ENV_FILE/EXEC_USER. Never invoke this file directly.
#
# Expects the caller to have already defined: log(), fail(), PLATFORM_DIR,
# MAGENTO, ENV_FILE, and EXEC_USER.

# load_env, reset_compile_state, magento_rebuild — see bin/lib/magento-rebuild.sh.
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/magento-rebuild.sh"
load_env

# The usual next-day state is a stopped stack (reboot, Docker Desktop
# restart) — offer to start it before anything else.
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/stack.sh"
ui_step refresh 1
ensure_stack_running

# Questions (dump, URLs, storage, backup, confirmation) and the import come
# first, so nothing slow runs before the user has answered everything.
log "Syncing database ..."
"$PLATFORM_DIR/bin/sync-db"

magento_rebuild

log "Refresh complete."

# Finish screen (standalone refresh only — inside bin/local/setup, setup's
# own finish screen covers this).
ui_step refresh 7
if [ "${UI_FLOW:-}" = refresh ]; then
    ui_result link "Storefront" "https://${APP_DOMAIN}/"
    ui_result link "Admin panel" "https://${APP_DOMAIN}/${MAGENTO_ADMIN_URI:-admin}/"
    ui_result text "Admin login" "Comes from the imported database — use that site's admin account"
    ui_result domain "Domain" "$APP_DOMAIN"
    ui_result command "Replace the database again later" "$(cmd_hint refresh-wizard)"
fi
