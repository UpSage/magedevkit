# Setup wizard, part 9 of 11 — Step 10: partial-install check, setup:install or the existing install, hooks.
# Sourced in order by bin/lib/setup-engine.sh (top-level script, shared
# variables); never run on its own.

# ------------------------------------------------------------------
# Magento Setup: Magento installation/check
# ------------------------------------------------------------------

# If env.php exists, don't just trust it — an interrupted setup:install
# can leave a full schema (hundreds of tables) but no seed data (e.g.
# eav_entity_type empty), which app/etc/env.php's mere existence wouldn't
# catch. Only checkable here (not back when $FRESH_INSTALL was first
# computed) since it needs the Docker stack up to query the database.
ui_step setup 10

SKIP_INSTALL_PROMPT=0
if [ "$FRESH_INSTALL" = 0 ]; then
    # db_client / db_recreate (bin/lib/db-backup.sh), as the env file's DB_* user.
    # shellcheck disable=SC1091
    source "$PLATFORM_DIR/bin/lib/db-backup.sh"
    db_credentials_from_env
    UI_TMP_ERR="$(mktemp)"
    # Only "no such database/table" (1049/1146) or a count of 0 means an
    # interrupted install. Any other error (DB down, wrong password …) stops
    # here — it must never be read as "incomplete" and lead to a DROP.
    EAV_ERR=""
    if ! EAV_COUNT="$(db_client app -N -e "SELECT COUNT(*) FROM eav_entity_type;" 2>"$UI_TMP_ERR")"; then
        EAV_ERR="$(cat "$UI_TMP_ERR" 2>/dev/null)"
        EAV_COUNT=0
        case "$EAV_ERR" in
            *"ERROR 1049"*|*"ERROR 1146"*) ;;
            *) fail "Couldn't check the existing database (${EAV_ERR:-no error text}). Fix the database connection and run setup again." ;;
        esac
    fi
    rm -f "$UI_TMP_ERR"
    if ! [ "${EAV_COUNT:-0}" -gt 0 ] 2>/dev/null; then
        log "⚠ app/etc/env.php exists, but the database looks incomplete (no EAV entity types found) — a previous install attempt was likely interrupted partway through."
        # Yes by default only when someone is there to see the question.
        DROP_DEFAULT=N
        ui_interactive && DROP_DEFAULT=Y
        prompt_yes_no 'Clean up this partial install and start fresh?' "$DROP_DEFAULT" \
            || fail "Can't continue with a partially-installed Magento — clean it up by hand, or re-run and accept the cleanup."
        log "Dropping the partially-installed database ..."
        db_recreate || fail "Couldn't recreate database $DB_NAME."
        "$PLATFORM_DIR/bin/platform" exec --user "$EXEC_USER" php rm -f app/etc/env.php
        FRESH_INSTALL=1
        SKIP_INSTALL_PROMPT=1

        # The earlier Data Refresh planning (right after Magento source was
        # confirmed ready) may have decided DO_DB_REFRESH=0, believing this
        # was already a fine existing install — now that it's known to be
        # broken, force a refresh so the reinstalled site isn't left empty.
        if [ "$DO_DB_REFRESH" != 1 ]; then
            DO_DB_REFRESH=1
            if [ -z "${PRESET_DB_SOURCE:-}" ]; then
                while true; do
                    PRESET_DB_SOURCE_INPUT="$(prompt_db_source "Where is the database dump? (file path or URL)")"
                    [ -n "$PRESET_DB_SOURCE_INPUT" ] && break
                    ui_interactive || fail "Database source can't be blank — set PRESET_DB_SOURCE, or run setup in a terminal (or the setup wizard)."
                    log "Database source can't be blank — enter a local file path or URL."
                done
                export PRESET_DB_SOURCE="$PRESET_DB_SOURCE_INPUT"
            fi
            export PRESET_PROD_CONFIG_CHOICE="Y"
            export PRESET_BACKUP_CHOICE="N"
        fi
    fi
fi

# Lets setup:install / setup:upgrade accept the MariaDB version this stack
# runs, even when the project's committed app/etc/di.xml predates it.
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/db-version-override.sh"
write_db_version_override

# Reuses the $FRESH_INSTALL flag computed earlier (right after Magento
# source was confirmed ready) instead of re-checking app/etc/env.php.
if [ "$FRESH_INSTALL" = 1 ]; then
    if [ "$SKIP_INSTALL_PROMPT" != 1 ]; then
        if ! prompt_yes_no 'No Magento installation detected. Run Magento setup:install now?' Y; then
            log "Skipping Magento install. Re-run this wizard when you're ready to install."
            exit 0
        fi
    fi

    # Some modules' plugins/helpers get eagerly constructed during
    # Magento's own install process (e.g. during config-cache-warming),
    # before the database is fully ready — crashing setup:install outright.
    # Disabling the culprit(s) just for this one command sidesteps that;
    # they're re-enabled right after so nothing stays permanently off.
    # A fresh vanilla install has no custom modules to trip over, so it
    # skips the question (and ignores any value left over in .env) — only
    # a cloned/existing source being installed for a DB refresh asks.
    if [ "$FRESH_SOURCE" = 1 ]; then
        MAGENTO_DISABLE_MODULES=""
    else
        # Interactively there's no default: Enter always means "disable
        # nothing", even if .env still holds a value from an earlier run.
        # Without a TTY, keep the .env value like every other prompt.
        if ui_interactive; then
            MAGENTO_DISABLE_MODULES="$(prompt_value "Should any modules be turned off during the first install? (comma-separated, e.g. Vendor_Module; Enter for none)" 0)"
        fi
        set_env_var "$ENV_FILE" MAGENTO_DISABLE_MODULES "$MAGENTO_DISABLE_MODULES"
    fi

    log "Bootstrapping pub/media (installer checks for it but won't create it) ..."
    "$PLATFORM_DIR/bin/platform" exec --user "$EXEC_USER" php mkdir -p /var/www/html/pub/media

    # bin/magento's CLI bootstrap loads every registered command's DI
    # dependencies before running anything — if generated/code or
    # generated/metadata already have stale compiled interceptors (e.g.
    # from an earlier interrupted install attempt) while app/etc/env.php
    # doesn't exist yet, that bootstrap crashes with "Connection 'default'
    # is not defined" before setup:install even starts. Clear them first
    # so a fresh install always starts from a clean slate — same fix
    # reset_compile_state() (bin/lib/magento-rebuild.sh) applies before recompiling.
    if [ -d "$MAGENTO_DIR/generated/code" ] || [ -d "$MAGENTO_DIR/generated/metadata" ]; then
        log "Clearing stale generated code from a previous attempt ..."
        clear_generated
    fi

    # With no DB import afterwards (a fresh vanilla install), the admin
    # created here is the real one — ask for it. With an import, the
    # imported database replaces it, so placeholders are fine.
    ADMIN_FIRSTNAME="${ADMIN_FIRSTNAME:-Local}"
    ADMIN_LASTNAME="${ADMIN_LASTNAME:-Admin}"
    ADMIN_EMAIL="${ADMIN_EMAIL:-admin@example.com}"
    ADMIN_USER="${ADMIN_USER:-admin}"
    ADMIN_PASSWORD='Admin123!'
    INSTALL_SAMPLE_DATA=0
    if [ "$DO_DB_REFRESH" != 1 ]; then
        log "Magento admin account"
        ADMIN_FIRSTNAME="$(prompt_matching "What's the admin's first name?" "$ADMIN_FIRSTNAME" '^[A-Za-z][A-Za-z .-]*$' "use letters, spaces, . and - only.")"
        ADMIN_LASTNAME="$(prompt_matching "What's the admin's last name?" "$ADMIN_LASTNAME" '^[A-Za-z][A-Za-z .-]*$' "use letters, spaces, . and - only.")"
        ADMIN_EMAIL="$(prompt_matching "What's the admin's email address?" "$ADMIN_EMAIL" '^[^@[:space:]|]+@[^@[:space:]|]+\.[^@[:space:]|]+$' "please enter a valid email address.")"
        ADMIN_USER="$(prompt_matching "Which username should the admin log in with?" "$ADMIN_USER" '^[A-Za-z0-9._-]+$' "use letters, digits, . _ and - only.")"
        ADMIN_PASSWORD="$(prompt_admin_password "$ADMIN_PASSWORD")"
        # Name/email/username only — the password is never written to disk.
        for k in ADMIN_FIRSTNAME ADMIN_LASTNAME ADMIN_EMAIL ADMIN_USER; do
            set_env_var "$ENV_FILE" "$k" "${!k}"
        done

        prompt_yes_no 'Install Luma sample data (demo catalog/CMS)?' N && INSTALL_SAMPLE_DATA=1
        log "Running Magento's first-time install ..."
    else
        log "Running Magento's first-time install (admin credentials here are placeholders — the refresh below replaces them with the real imported database) ..."
    fi
    DISABLE_MODULES_FLAG=()
    [ -n "$MAGENTO_DISABLE_MODULES" ] && DISABLE_MODULES_FLAG=(--disable-modules="$MAGENTO_DISABLE_MODULES")
    ui_task_start "Installing Magento (setup:install)"
    "$PLATFORM_DIR/bin/magento" setup:install \
        --base-url="https://${APP_DOMAIN}/" \
        --db-host=mariadb --db-name="${DB_NAME}" --db-user="${DB_USER}" --db-password="${DB_PASSWORD}" \
        --backend-frontname="${MAGENTO_ADMIN_URI:-admin}" \
        --admin-firstname="$ADMIN_FIRSTNAME" --admin-lastname="$ADMIN_LASTNAME" --admin-email="$ADMIN_EMAIL" \
        --admin-user="$ADMIN_USER" --admin-password="$ADMIN_PASSWORD" \
        --language=en_US --currency=USD --timezone="${APP_TIMEZONE:-America/New_York}" \
        --use-rewrites=1 --use-secure=0 --use-secure-admin=0 \
        --search-engine=opensearch --opensearch-host=opensearch --opensearch-port=9200 \
        --opensearch-index-prefix="${OPENSEARCH_INDEX_PREFIX:-magento}" --opensearch-enable-auth=0 \
        --amqp-host=rabbitmq --amqp-port=5672 --amqp-user="${RABBITMQ_DEFAULT_USER}" --amqp-password="${RABBITMQ_DEFAULT_PASS}" --amqp-virtualhost=/ \
        --session-save=redis --session-save-redis-host=valkey --session-save-redis-port=6379 \
        --cache-backend=redis --cache-backend-redis-server=valkey --cache-backend-redis-port=6379 --cache-backend-redis-db=0 \
        --page-cache=redis --page-cache-redis-server=valkey --page-cache-redis-port=6379 --page-cache-redis-db=1 \
        "${DISABLE_MODULES_FLAG[@]}"
    ui_task_done

    if [ -n "$MAGENTO_DISABLE_MODULES" ]; then
        log "Re-enabling ${MAGENTO_DISABLE_MODULES} (only disabled for the install above) ..."
        # shellcheck disable=SC2086
        "$PLATFORM_DIR/bin/magento" module:enable ${MAGENTO_DISABLE_MODULES//,/ }
        "$PLATFORM_DIR/bin/magento" setup:upgrade --no-interaction
    fi

    if [ "$INSTALL_SAMPLE_DATA" = 1 ]; then
        ui_task_start "Installing Luma sample data"
        log "Installing Luma sample data ..."
        "$PLATFORM_DIR/bin/magento" sampledata:deploy
        "$PLATFORM_DIR/bin/magento" setup:upgrade --no-interaction
        ui_task_done
    fi

    # Version-specific commands from the m2 catalog (base, then patch).
    if [ "$FRESH_SOURCE" = 1 ]; then
        while IFS= read -r hook; do
            log "Running ${hook#"$PLATFORM_DIR/"} ..."
            PLATFORM_DIR="$PLATFORM_DIR" MAGENTO_VERSION="$MAGENTO_VERSION" "$hook"
        done < <(m2_hooks post-install)
    fi
else
    log "✓ Existing Magento installation detected"
fi
