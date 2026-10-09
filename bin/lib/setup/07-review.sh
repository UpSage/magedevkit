# Setup wizard, part 7 of 11 — Step 7: the review checkpoint before anything heavy.
# Sourced in order by bin/lib/setup-engine.sh (top-level script, shared
# variables); never run on its own.

# ------------------------------------------------------------------
# Review — the checkpoint before anything heavy (certificate, image builds,
# downloads, install, import) runs.
# ------------------------------------------------------------------

ui_step setup 7

if [ "$FRESH_SOURCE" = 1 ]; then
    REVIEW_MAGENTO="Fresh download: $MAGENTO_PACKAGE $MAGENTO_VERSION"
    REVIEW_DATA="None — an empty store"
else
    REVIEW_MAGENTO="Existing code in magento/ (Magento ${DETECTED_VERSION:-version not detected})"
    if [ "$DO_DB_REFRESH" = 1 ]; then
        if [ "${PRESET_PROD_CONFIG_CHOICE:-Y}" = Y ]; then
            REVIEW_DATA="Import $(basename "$PRESET_DB_SOURCE") — production copy, URLs rewritten for this site"
        else
            REVIEW_DATA="Import $(basename "$PRESET_DB_SOURCE") — as-is"
        fi
    else
        REVIEW_DATA="None — keep the current database"
    fi
fi
if [ "$FRESH_INSTALL" = 1 ]; then REVIEW_INSTALL="Runs Magento setup:install"; else REVIEW_INSTALL="Already installed — kept"; fi
if ! ui_review "Review your settings before the installation starts" \
    "Project" "$APP_NAME" \
    "Storefront" "https://${APP_DOMAIN}/" \
    "Admin path" "/${MAGENTO_ADMIN_URI:-admin}" \
    "Magento code" "$REVIEW_MAGENTO" \
    "Magento install" "$REVIEW_INSTALL" \
    "Store data" "$REVIEW_DATA" \
    "Database" "$DB_NAME (user $DB_USER, password ••••)" \
    "Mode" "$MAGENTO_MODE" \
    "File ownership" "$FILE_OWNERSHIP" \
    "Stack versions" "$(stack_summary)"; then
    cancel "Stopped at the review — nothing heavy has run yet. Re-run setup when you're ready; your answers are kept as defaults."
fi
