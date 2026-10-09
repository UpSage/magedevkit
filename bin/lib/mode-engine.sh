# Magento mode / file-ownership switching engine — sourced by bin/mode/* and
# bin/ownership/*. Never invoke directly. Expects PLATFORM_DIR.
#
# Every switch is reversible and safe to repeat: it always unlocks first
# (a production tree is locked read-only), re-applies ownership for the
# configured model, makes the change, and re-locks only if the resulting
# mode is production. So switching back later (production → developer,
# two-owner → single, ...) is just running the other command.

# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/permissions-engine.sh"

require_env_file
MAGENTO="$PLATFORM_DIR/bin/magento"

# esc_sed / set_env_var
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/env.sh"

env_value() {  # $1=KEY $2=default -> value from the env file
    env_get "$ENV_FILE" "$1" "$2"
}

# Magento's own idea of the current mode (blank if it can't be read).
current_magento_mode() {
    "$MAGENTO" deploy:mode:show 2>/dev/null | grep -oE 'default|developer|production' | head -1 || true
}

# $1 = default | developer | production
set_mode() {
    local mode="$1" current
    case "$mode" in
        default|developer|production) ;;
        *) fail "Unknown mode '$mode' — expected default, developer, or production." ;;
    esac
    # One change at a time per instance (no-op when a caller already holds it).
    wizard_lock
    set_env_var "$ENV_FILE" MAGENTO_MODE "$mode"
    log "MAGENTO_MODE=$mode written to $ENV_NAME"

    perm_unlock
    perm_apply

    current="$(current_magento_mode)"
    if [ "$current" = "$mode" ]; then
        log "✓ Magento already in $mode mode"
    else
        # production compiles DI and deploys static content itself; leaving
        # production clears those generated files again.
        log "Switching Magento mode: ${current:-unknown} → $mode ..."
        "$MAGENTO" deploy:mode:set "$mode"
        log "Restarting php to drop stale opcache from the previous mode ..."
        "$PLATFORM_DIR/bin/platform" restart php
        "$MAGENTO" cache:flush
    fi

    if [ "$mode" = "production" ]; then
        perm_lock
    fi
    log "✓ Mode: $mode (ownership: $(env_value FILE_OWNERSHIP single))"
}

# $1 = single | two-owner
set_ownership() {
    local model="$1"
    case "$model" in
        single|two-owner) ;;
        *) fail "Unknown ownership model '$model' — expected single or two-owner." ;;
    esac

    # One change at a time per instance (no-op when a caller already holds it).
    wizard_lock
    # Steps for the browser wizard (the dashboard runs this there, because the
    # chown/usermod below may need the sudo password, asked on the page).
    ui_flow_begin ownership

    # Unlock under the *current* model first — lock bits differ per model
    # (u-w vs g-w), so unlocking after switching would miss them.
    ui_step ownership 1
    ui_task "Unlocking files under the current model ($(env_value FILE_OWNERSHIP single))" perm_unlock

    ui_step ownership 2
    set_env_var "$ENV_FILE" FILE_OWNERSHIP "$model"
    log "FILE_OWNERSHIP=$model written to $ENV_NAME"
    # shellcheck disable=SC1091
    source "$PLATFORM_DIR/bin/lib/identity.sh"
    ui_task "Applying $model ownership to the writable folders" perm_apply

    # php's compose `user:` follows FILE_OWNERSHIP — recreate it to pick up
    # the new identity (a no-op if it already matches).
    ui_step ownership 3
    log "Recreating php as $PHP_UID:$PHP_GID ..."
    ui_task "Recreating php as $PHP_UID:$PHP_GID" "$PLATFORM_DIR/bin/platform" up -d php

    if [ "$(env_value MAGENTO_MODE developer)" = "production" ]; then
        ui_task "Locking code again (production mode)" perm_lock
    fi
    ui_step ownership 4
    log "✓ Ownership: $model (mode: $(env_value MAGENTO_MODE developer))"
    ui_result text "File ownership" "$model (php-fpm runs as $PHP_UID:$PHP_GID)"
    if [ "$model" = two-owner ]; then
        ui_result text "Next" "You were added to the www-data group — open a new terminal (or run 'wsl --shutdown' in Windows) so your shell and editor pick it up."
    fi
    ui_result command "Switch back later" "$(cmd_hint ownership "$([ "$model" = single ] && echo two-owner || echo single)")"
}

show_mode() {
    # shellcheck disable=SC1091
    source "$PLATFORM_DIR/bin/lib/identity.sh"
    echo "Configured mode : $(env_value MAGENTO_MODE developer)   ($ENV_NAME MAGENTO_MODE)"
    echo "Magento mode    : $(current_magento_mode || true)   (bin/magento deploy:mode:show)"
    echo "Ownership       : $FILE_OWNERSHIP   (php-fpm $PHP_UID:$PHP_GID, CLI $EXEC_USER)"
}
