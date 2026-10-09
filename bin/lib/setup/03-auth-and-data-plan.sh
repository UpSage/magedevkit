# Setup wizard, part 3 of 11 — Step 3: an existing auth.json, and the data-refresh decision (executed later).
# Sourced in order by bin/lib/setup-engine.sh (top-level script, shared
# variables); never run on its own.

# ------------------------------------------------------------------
# Magento Setup: Existing auth.json (optional convenience — supply one
# now instead of waiting for the later Composer-credentials check to fail
# with manual instructions).
# ------------------------------------------------------------------

AUTH_JSON="$MAGENTO_DIR/auth.json"
# Skipped for a fresh download — magento/ must stay empty until
# create-project; collect_fresh_auth already gathered the credentials.
if [ "$FRESH_SOURCE" != 1 ]; then
    # Always asked — never silently skipped just because a file happens to
    # already be sitting at magento/auth.json. That file can come from
    # anywhere (e.g. committed into the Magento repo you cloned), which isn't
    # the same as you having deliberately configured it through this wizard.
    # Either way, option 2 copies in an auth.json from a path or URL.
    if [ -f "$AUTH_JSON" ] && ! grep -q '<public-key>\|<private-key>' "$AUTH_JSON"; then
        HAVE_AUTH_CHOICE="$(prompt_menu 'magento/auth.json already has repo.magento.com credentials.' 1 \
            'Keep it' \
            'Replace it with another auth.json file')"
    else
        HAVE_AUTH_CHOICE="$(prompt_menu 'composer install needs repo.magento.com credentials (magento/auth.json).' 1 \
            "Skip for now — I'll add magento/auth.json myself (setup stops at the Composer step if it's still missing)" \
            'Use an existing auth.json file')"
    fi
    case "$HAVE_AUTH_CHOICE" in
        2)
            AUTH_SOURCE_INPUT="$(prompt_file_source 'Where is your auth.json? (file path or URL)' json)"
            case "$AUTH_SOURCE_INPUT" in
                http://*|https://*)
                    curl -fsSL -o "$AUTH_JSON" "$AUTH_SOURCE_INPUT" \
                        || fail "Failed to download auth.json from '$AUTH_SOURCE_INPUT'."
                    ;;
                *)
                    [ -f "$AUTH_SOURCE_INPUT" ] || fail "No file found at '$AUTH_SOURCE_INPUT'."
                    cp "$AUTH_SOURCE_INPUT" "$AUTH_JSON"
                    ;;
            esac
            grep -q '<public-key>\|<private-key>' "$AUTH_JSON" 2>/dev/null \
                && fail "The supplied auth.json still has placeholder credentials — check the source file."
            log "auth.json copied to $AUTH_JSON."
            ;;
        *)
            if [ -f "$AUTH_JSON" ] && ! grep -q '<public-key>\|<private-key>' "$AUTH_JSON"; then
                log "✓ Keeping existing magento/auth.json"
            else
                log "Skipping — the Composer credentials step later will check for magento/auth.json."
            fi
            ;;
    esac
fi

# Determined once, here — just a file check, no Docker/env dependency —
# and reused both by the Data Refresh planning right below and by
# "Magento Setup: Magento installation/check" much later. Nothing changes
# app/etc/env.php in between, so one check is enough.
if [ -f "$MAGENTO_DIR/app/etc/env.php" ]; then
    FRESH_INSTALL=0
else
    FRESH_INSTALL=1
fi

# ------------------------------------------------------------------
# Data Refresh: decide now, execute later (once Docker/Magento are up).
# Mirrors bin/sync-db's own prompts — the choices made here are exported
# so bin/sync-db uses them directly instead of asking again.
# ------------------------------------------------------------------

if [ "$FRESH_SOURCE" = 1 ]; then
    ui_step_skip setup 3
else
    ui_step setup 3
fi

# Set below if the "different base URL?" sub-flow confirms a domain
# change — Project Configuration then skips re-asking for it.
APP_DOMAIN_JUST_SET=0

if [ "$FRESH_SOURCE" = 1 ]; then
    DO_DB_REFRESH=0
    log "Fresh vanilla install — no database import (an empty Magento is installed)."
elif [ "$FRESH_INSTALL" = 1 ]; then
    DO_DB_REFRESH=1
    log "Fresh install — the database will be refreshed once Magento is installed."
else
    if prompt_yes_no 'Import a database dump now? This replaces the current local database.' N; then
        DO_DB_REFRESH=1
    else
        DO_DB_REFRESH=0
    fi
fi

if [ "$DO_DB_REFRESH" = 1 ]; then
    # Required — no default location/URL shown or silently assumed
    # (matching bin/sync-db's own required prompt; no presigned-URL default
    # either, to avoid handing out a working credential).
    while true; do
        PRESET_DB_SOURCE="$(prompt_db_source "Where is the database dump? (file path or URL)")"
        [ -n "$PRESET_DB_SOURCE" ] && break
        ui_interactive || fail "Database source can't be blank — set PRESET_DB_SOURCE, or run setup in a terminal (or the setup wizard)."
        log "Database source can't be blank — enter a local file path or URL."
    done

    # Exported as the Y/N answers bin/sync-db's own two questions expect.
    DUMP_KIND="$(prompt_menu 'What kind of database dump is this?' 1 \
        'A copy of production — rewrite its URLs and cookie domain for this local site' \
        'Already prepared for local use — import it as-is')"
    case "$DUMP_KIND" in
        2)
            PRESET_PROD_CONFIG_CHOICE=N
            PRESET_CONTINUE_CHOICE=Y
            log "⚠ The dump's URLs and cookie domain will be kept as they are."
            ;;
        *)
            PRESET_PROD_CONFIG_CHOICE=Y
            log "Production configuration will be replaced."
            while true; do
                LOCAL_DOMAIN_INPUT="$(prompt_value "$(with_default "Which local domain should the imported site use?" "$APP_DOMAIN")" 0)"
                valid_hostname "${LOCAL_DOMAIN_INPUT:-$APP_DOMAIN}" && break
                log "'${LOCAL_DOMAIN_INPUT:-$APP_DOMAIN}' isn't a valid domain — use something like shop.test (letters, digits, hyphens and dots)."
                ui_interactive || fail "No TTY to ask for a different domain — set APP_DOMAIN in $ENV_NAME and re-run."
            done
            APP_DOMAIN="${LOCAL_DOMAIN_INPUT:-$APP_DOMAIN}"
            set_env_var "$ENV_FILE" APP_DOMAIN "$APP_DOMAIN"
            APP_DOMAIN_JUST_SET=1
            log "The imported database will use https://${APP_DOMAIN}/ as its base URL."
            ;;
    esac
    # A first install's database is only setup:install's placeholder —
    # nothing worth backing up before the import replaces it.
    [ "$FRESH_INSTALL" = 1 ] && PRESET_BACKUP_CHOICE=N
    export PRESET_DB_SOURCE PRESET_PROD_CONFIG_CHOICE PRESET_CONTINUE_CHOICE PRESET_BACKUP_CHOICE
fi
