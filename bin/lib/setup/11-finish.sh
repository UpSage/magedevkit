# Setup wizard, part 11 of 11 — Step 12: Magento mode, data refresh, optional S3 sync, verification and the summary.
# Sourced in order by bin/lib/setup-engine.sh (top-level script, shared
# variables); never run on its own.

# ------------------------------------------------------------------
# Magento mode — applied here only when no data refresh follows;
# bin/refresh applies MAGENTO_MODE (and the production lock) itself.
# ------------------------------------------------------------------

if [ "$DO_DB_REFRESH" != 1 ]; then
    if [ "$FRESH_SOURCE" = 1 ]; then
        # What bin/refresh would otherwise apply (OpenSearch, Varnish FPC,
        # media/remote storage, URL overrides, admin frontName) — same code.
        ui_task_start "Applying platform configuration"
        log "Applying platform configuration to the fresh install ..."
        MAGENTO="$PLATFORM_DIR/bin/magento"
        # shellcheck disable=SC1091
        source "$PLATFORM_DIR/bin/lib/magento-config-engine.sh"
        apply_magento_config
        # One-time env.php settings (see README's "Varnish full-page cache").
        "$MAGENTO" setup:config:set --http-cache-hosts="varnish:80" -n
        "$MAGENTO" cache:enable full_page
        ui_task_done
    fi
    # Same command you'd run by hand later to switch modes.
    ui_task "Applying $MAGENTO_MODE mode" "$PLATFORM_DIR/bin/mode/$MAGENTO_MODE"
fi

# ------------------------------------------------------------------
# Data Refresh
# ------------------------------------------------------------------

# Reuses the $DO_DB_REFRESH decision (and PRESET_* answers) already
# collected right after Magento source was confirmed ready — no re-asking.
if [ "$DO_DB_REFRESH" = 1 ]; then
    log "Running the full data refresh (this can take 20-40 minutes) ..."
    "$PLATFORM_DIR/bin/refresh"
else
    log "✓ Existing database preserved"
fi

# ------------------------------------------------------------------
# Optional S3 Sync (independent of Data Refresh; only relevant when
# storage driver is aws-s3 — no prompt at all otherwise).
# ------------------------------------------------------------------

if [ "$REMOTE_STORAGE_DRIVER_NOW" = "aws-s3" ]; then
    if prompt_yes_no 'Copy local media files (pub/media) up to the S3 bucket now?' N; then
        log "Synchronizing local media to S3 ..."
        ui_task "Copying local media to S3" "$PLATFORM_DIR/bin/sync-media-to-s3"
    else
        log "✓ S3 synchronization skipped"
    fi
fi

# ------------------------------------------------------------------
# Verification
# ------------------------------------------------------------------

ui_step setup 12

ui_task_start "Verifying services"
log "Verifying services ..."
"$PLATFORM_DIR/bin/platform" ps
"$PLATFORM_DIR/bin/magento" --version >/dev/null 2>&1 && log "✓ Magento CLI reachable" || log "⚠ Could not reach Magento CLI"

if [ "$REMOTE_STORAGE_DRIVER_NOW" = "aws-s3" ]; then
    aws sts get-caller-identity --profile "$AWS_PROFILE" >/dev/null 2>&1 \
        && log "✓ AWS credentials" || log "⚠ AWS credentials could not be re-verified"
    aws s3api head-bucket --bucket "$REMOTE_STORAGE_BUCKET" --profile "$AWS_PROFILE" >/dev/null 2>&1 \
        && log "✓ S3 bucket accessible" || log "⚠ S3 bucket could not be re-verified"
fi
ui_task_done

# ------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------

echo
echo "════════════════════════════════════════"
echo "       Magento Setup Complete"
echo "════════════════════════════════════════"
echo
echo "Project       : ${APP_NAME:-$DB_NAME}"
echo "Domain        : $APP_DOMAIN"
echo "Admin         : /${MAGENTO_ADMIN_URI:-admin}"
[ "$FRESH_SOURCE" = 1 ] && echo "Magento       : $MAGENTO_PACKAGE $MAGENTO_VERSION"
echo "Mode          : $MAGENTO_MODE (ownership: $FILE_OWNERSHIP)"
echo "Storage       : $REMOTE_STORAGE_DRIVER_NOW"
if [ "$REMOTE_STORAGE_DRIVER_NOW" = "aws-s3" ]; then
    echo "S3 Bucket     : $REMOTE_STORAGE_BUCKET"
    echo "AWS Profile   : $AWS_PROFILE"
fi
echo
echo "Storefront: https://${APP_DOMAIN}/"
if [ "$FRESH_SOURCE" = 1 ]; then
    echo "Admin:      https://${APP_DOMAIN}/${MAGENTO_ADMIN_URI:-admin}/  (user: $ADMIN_USER)"
elif [ "$DO_DB_REFRESH" = 1 ]; then
    echo "Admin:      https://${APP_DOMAIN}/${MAGENTO_ADMIN_URI:-admin}/  (credentials come from the imported database)"
else
    echo "Admin:      https://${APP_DOMAIN}/${MAGENTO_ADMIN_URI:-admin}/"
fi
# Re-read: bin/platform up may have moved ports that another stack holds.
for k in DB_PORT VALKEY_PORT RABBITMQ_PORT RABBITMQ_MANAGEMENT_PORT OPENSEARCH_PORT MAILPIT_HTTP_PORT MAILPIT_SMTP_PORT; do
    printf -v "$k" '%s' "$(env_get "$ENV_FILE" "$k")"
done
echo "Mailpit:    http://localhost:${MAILPIT_HTTP_PORT:-8025}/"
echo "Host ports: MariaDB ${DB_PORT}, Valkey ${VALKEY_PORT}, RabbitMQ ${RABBITMQ_PORT} (UI ${RABBITMQ_MANAGEMENT_PORT}), OpenSearch ${OPENSEARCH_PORT}, SMTP ${MAILPIT_SMTP_PORT}"
echo "════════════════════════════════════════"

echo
echo "One manual step left — this is never done automatically (Windows-side, admin-elevated, outside WSL's reach):"
echo "  Add this line to C:\\Windows\\System32\\drivers\\etc\\hosts (Notepad, Run as Administrator):"
echo "    127.0.0.1 ${APP_DOMAIN}"

# Finish screen items for the web wizard (no-ops in the terminal, which
# printed the summary above).
ui_result link "Storefront" "https://${APP_DOMAIN}/"
ui_result link "Admin panel" "https://${APP_DOMAIN}/${MAGENTO_ADMIN_URI:-admin}/"
if [ "$FRESH_SOURCE" = 1 ]; then
    ui_result text "Admin login" "$ADMIN_USER (with the password you chose)"
elif [ "$DO_DB_REFRESH" = 1 ]; then
    ui_result text "Admin login" "Your usual admin account — it comes from the imported database"
fi
ui_result domain "Domain" "$APP_DOMAIN"
ui_result hosts "Windows hosts file line" "127.0.0.1 ${APP_DOMAIN}"
if [ -n "${WSL_DISTRO_NAME:-}" ] && command -v mkcert >/dev/null 2>&1; then
    CA_PATH="$(mkcert -CAROOT 2>/dev/null)/rootCA.pem"
    ui_result path "Local certificate authority (for Windows)" "\\\\wsl.localhost\\${WSL_DISTRO_NAME}${CA_PATH//\//\\}"
fi
[ "$FRESH_SOURCE" = 1 ] && ui_result text "Magento" "$MAGENTO_PACKAGE $MAGENTO_VERSION"
ui_result text "Mode" "$MAGENTO_MODE (file ownership: $FILE_OWNERSHIP)"
ui_result text "Media storage" "$REMOTE_STORAGE_DRIVER_NOW"
ui_result link "Mailpit (catches all outgoing email)" "http://localhost:${MAILPIT_HTTP_PORT:-8025}/"
ui_result copy "MariaDB" "127.0.0.1:${DB_PORT} — database ${DB_NAME}, user ${DB_USER}"
ui_result copy "OpenSearch" "http://localhost:${OPENSEARCH_PORT}/"
ui_result copy "RabbitMQ admin" "http://localhost:${RABBITMQ_MANAGEMENT_PORT}/"
ui_result command "Start / stop this stack" "$(cmd_hint platform up -d)   # or: … platform stop"
ui_result command "Replace the database later (webpage)" "$(cmd_hint refresh-wizard)"
ui_result command "Run Magento commands" "$(cmd_hint magento cache:flush)"
