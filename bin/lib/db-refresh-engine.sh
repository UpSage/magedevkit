# DB-refresh engine — sourced by bin/local/sync-db (via
# bin/lib/sync-db-config-engine.sh) once it has resolved its inputs. Never
# invoke this file directly.
#
# Expects the caller to have already defined: log(), fail(), PLATFORM_DIR,
# MARIADB_DB/MARIADB_USER/MARIADB_PASSWORD/MARIADB_ROOT_PASSWORD,
# TARGET_BASE_URL, TABLES_TO_STRIP (array, may be empty), and either
# LOCAL_SOURCE_FILE or DB_URL (the required source). APPLY_URL_REWRITE,
# EFFECTIVE_MEDIA_URL, and COOKIE_DOMAIN are optional — sane defaults are
# applied below. The production URLs and cookie domain to rewrite *from*
# are read from the dump itself (bin/lib/prod-urls.sh); the saved
# PROD_BASE_URL/PROD_MEDIA_URL/PROD_COOKIE_DOMAIN are only the fallback for
# a dump that doesn't carry them.

LOCAL_DIR="$DB_DIR/db-refresh"
LOCAL_FILE="$LOCAL_DIR/dbbackup.sql.gz"
PROCESSED_FILE="$LOCAL_DIR/processed_dbbackup.sql"
IMPORT_ERROR_LOG="$LOCAL_DIR/import-error.log"

# db_has_tables / db_dump_to / db_recreate / prune_backups
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/db-backup.sh"
# sed_re_literal / sed_repl_literal
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/env.sh"
# detect_prod_urls / prod_urls_are_local / confirm_prod_urls — the dump's
# own store URLs
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/prod-urls.sh"

# Shared non-prod media CDN default — the rewrite target used whenever
# nothing overrides it. With no project-specific CDN configured, deriving
# from this environment's own domain (matches BASE_STATIC_URL's existing
# "blank = derive from base_url" convention) is the sane generic default;
# a project that has a real shared CDN sets DEV_MEDIA_URL directly in
# the env file to keep using it.
: "${DEV_MEDIA_URL:=${TARGET_BASE_URL}media/}"
: "${EFFECTIVE_MEDIA_URL:=$DEV_MEDIA_URL}"
: "${APPLY_URL_REWRITE:=1}"

mkdir -p "$LOCAL_DIR"

ui_step refresh 6

[ -n "${LOCAL_SOURCE_FILE:-}" ] || [ -n "${DB_URL:-}" ] \
  || fail "No database source given — set LOCAL_SOURCE_FILE or DB_URL."
command -v gunzip >/dev/null 2>&1 || fail "gunzip is not installed or not available in PATH."

# Fail fast on insufficient space rather than running out partway through
# download/import — decompressed SQL text typically runs ~8-10x the size of
# the gzipped backup, so require 8x the compressed size free up front. Two
# possible sources by this point: a local file (LOCAL_SOURCE_FILE, size read
# directly) or a URL (DB_URL, size from its Content-Length header if the
# server sends one).
ui_task_start "Checking free disk space"
log "Checking available disk space in $LOCAL_DIR ..."
if [ -n "${LOCAL_SOURCE_FILE:-}" ]; then
  BACKUP_SIZE=$(stat -c%s "$LOCAL_SOURCE_FILE") || fail "Failed to read '$LOCAL_SOURCE_FILE''s size."
else
  BACKUP_SIZE="$(curl -sIL "$DB_URL" | tr -d '\r' | grep -i '^content-length:' | tail -1 | cut -d: -f2 | tr -d ' ')"
fi
# A backup of the current database (gzipped, typically well under a quarter
# of its on-disk size) needs room too.
CURRENT_DB_BYTES=0
if [ "${BACKUP_BEFORE_REPLACE:-0}" = 1 ]; then
  CURRENT_DB_BYTES="$(db_client root -N \
    -e "SELECT COALESCE(SUM(data_length + index_length), 0) FROM information_schema.tables WHERE table_schema='$MARIADB_DB';" 2>/dev/null | tr -dc '0-9')"
  : "${CURRENT_DB_BYTES:=0}"
fi
if [ -n "${BACKUP_SIZE:-}" ] && [ "$BACKUP_SIZE" -gt 0 ] 2>/dev/null; then
  AVAILABLE=$(df --output=avail -B1 "$LOCAL_DIR" | tail -1)
  REQUIRED=$((BACKUP_SIZE * 8 + CURRENT_DB_BYTES / 4))
  if [ "$AVAILABLE" -lt "$REQUIRED" ]; then
    fail "Not enough disk space in $LOCAL_DIR: need ~$((REQUIRED / 1024 / 1024)) MB, have ~$((AVAILABLE / 1024 / 1024)) MB."
  fi
  log "Disk space check passed."
else
  log "Could not determine the source size (no Content-Length header) — skipping the disk-space check."
fi
ui_task_done

if [ -n "${LOCAL_SOURCE_FILE:-}" ]; then
  ui_task_start "Copying the dump"
  log "Using local database file $LOCAL_SOURCE_FILE ..."
  [ "$LOCAL_SOURCE_FILE" = "$LOCAL_FILE" ] || cp "$LOCAL_SOURCE_FILE" "$LOCAL_FILE"
else
  ui_task_start "Downloading the dump"
  log "Downloading $DB_URL to $LOCAL_FILE ..."
  curl -fL -o "$LOCAL_FILE" "$DB_URL" || fail "Failed to download the database backup from $DB_URL."
  log "Database backup downloaded successfully."
fi
ui_task_done

ui_task_start "Verifying the dump"
log "Verifying .gz file ..."
gunzip -t "$LOCAL_FILE" || fail "The downloaded file is not a valid .gz file."
log "Verified: .gz file is valid."
ui_task_done

# Which site the dump came from: read from the dump itself (its
# core_config_data), not typed in — see bin/lib/prod-urls.sh. Then the
# "from → to" check, before anything is backed up, dropped or saved.
if [ "$APPLY_URL_REWRITE" = 1 ]; then
  ui_task_start "Reading the dump's store URLs"
  detect_prod_urls "$LOCAL_FILE"
  ui_task_done
  if prod_urls_are_local; then
    log "✓ The dump already uses this site's domain ($APP_DOMAIN) — nothing to rewrite."
    APPLY_URL_REWRITE=0
  else
    confirm_prod_urls
  fi
fi

STRIP_SED_SCRIPT=""
for t in "${TABLES_TO_STRIP[@]}"; do
  STRIP_SED_SCRIPT+="/^INSERT INTO \`$t\` VALUES/,/;\$/d;"
done

# Extract, clean, and rewrite the dump in a single streamed pass instead of
# writing out a separate copy of the full decompressed SQL at each step —
# with a multi-GB dump, materializing every intermediate copy on disk was
# ballooning peak disk usage to 4-5x the dump size and intermittently
# exhausting the volume mid-run.
ui_task_start "Preparing the SQL (rewriting URLs, cleaning definers)"
log "Extracting and processing SQL file ..."
if [ "$APPLY_URL_REWRITE" = 1 ]; then
  # Every production URL → this site's: media URLs first (a CDN, or a
  # media/ path under the base URL), then the base URLs (unsecure, secure
  # and link URLs can all differ), then the cookie domain if one is set.
  REWRITE=()
  for url in "${PROD_MEDIA_URLS[@]}"; do
    REWRITE+=(-e "s|$(sed_re_literal "$url")|$(sed_repl_literal "$EFFECTIVE_MEDIA_URL")|g")
  done
  for url in "${PROD_BASE_URLS[@]}"; do
    REWRITE+=(-e "s|$(sed_re_literal "$url")|$(sed_repl_literal "$TARGET_BASE_URL")|g")
  done
  if [ -n "${PROD_COOKIE_DOMAIN:-}" ]; then
    REWRITE+=(-e "s|$(sed_re_literal "'web/cookie/cookie_domain','$PROD_COOKIE_DOMAIN'")|$(sed_repl_literal "'web/cookie/cookie_domain','${COOKIE_DOMAIN:-}'")|g")
  fi

  # The cookie-domain rewrite blanks the production cookie domain by
  # default (COOKIE_DOMAIN empty in the env file) rather than rewriting
  # it to another literal value — left as-is, a production cookie domain
  # breaks session/private-content cookies on any environment whose domain
  # isn't a subdomain of it, since the browser rejects a cookie whose
  # Domain attribute isn't the current host or a parent of it. Blank makes
  # Magento default to the current request's host, correct on every
  # environment — preserved here, just driven by the configured
  # COOKIE_DOMAIN value instead of an unconditional hardcoded blank.
  gunzip -c "$LOCAL_FILE" \
    | grep -v '\/\*M!999999\\-' \
    | sed -e 's/DEFINER=`[^`]*`@`[^`]*`//g' \
          -e 's/SQL SECURITY DEFINER//g' \
          "${REWRITE[@]}" \
    | sed "$STRIP_SED_SCRIPT" \
    > "$PROCESSED_FILE" || fail "Failed to extract and process $LOCAL_FILE."
else
  # Production-config gate said this dump doesn't need URL/cookie
  # replacement — DEFINER-strip and search-log cleanup still always apply,
  # since those are SQL-portability/hygiene concerns, not production-config
  # ones.
  gunzip -c "$LOCAL_FILE" \
    | grep -v '\/\*M!999999\\-' \
    | sed -e 's/DEFINER=`[^`]*`@`[^`]*`//g' \
          -e 's/SQL SECURITY DEFINER//g' \
    | sed "$STRIP_SED_SCRIPT" \
    > "$PROCESSED_FILE" || fail "Failed to extract and process $LOCAL_FILE."
fi
log "Database backup extracted and processed to $PROCESSED_FILE."
ui_task_done

# No longer needed once the processed file exists — no reason to hold it
# through the import that follows.
rm -f "$LOCAL_FILE"

for t in "${TABLES_TO_STRIP[@]}"; do
  if grep -q "INSERT INTO \`$t\`" "$PROCESSED_FILE"; then
    fail "$t INSERT statements still exist in processed SQL."
  fi
done
if [ "${#TABLES_TO_STRIP[@]}" -gt 0 ]; then
  log "Stripped tables from SQL file: ${TABLES_TO_STRIP[*]}"
else
  log "No tables stripped from SQL file."
fi

# Backup of the current database, taken only now — after the new dump has
# been verified and processed, right before anything is dropped.
if [ "${BACKUP_BEFORE_REPLACE:-0}" = 1 ]; then
  ui_task_start "Backing up the current database"
  # 0 = has tables, 1 = empty, 2 = couldn't tell: never skip the backup then.
  db_has_tables && HAS_TABLES=0 || HAS_TABLES=$?
  [ "$HAS_TABLES" = 2 ] && fail "Couldn't check the current database, so it can't be backed up — nothing was replaced."
  if [ "$HAS_TABLES" = 0 ]; then
    BACKUP_DIR="$DB_DIR/backups"
    BACKUP_FILE="$BACKUP_DIR/${APP_NAME:-app}-$(date '+%Y%m%d-%H%M%S').sql.gz"
    log "Saving the current database to ${BACKUP_FILE#"$INSTANCE_DIR/"} ..."
    db_dump_to "$BACKUP_FILE" \
      || fail "Backing up the current database failed — nothing was replaced."
    log "✓ Current database saved ($(du -h "$BACKUP_FILE" | cut -f1)). Restore it any time by refreshing from this file and answering 'N' to 'still contains production configuration'."
    # Keep the newest 3 backups of this project.
    prune_backups "$BACKUP_DIR" "${APP_NAME:-app}-" 3
    ui_result path "Backup of the previous database" "$BACKUP_FILE"
    ui_result text "To restore that backup" "Run $(cmd_hint refresh-wizard), choose 'File on this computer' with the path above, and answer No to 'still contains production configuration'."
  else
    log "The current database is empty — nothing to back up."
  fi
  ui_task_done
fi

ui_task_start "Replacing the database (drop & import — don't interrupt)"
log "Dropping and recreating database $MARIADB_DB ..."
db_recreate || fail "Failed to recreate database."
log "Database recreated."

log "Importing processed SQL into $MARIADB_DB ..."
db_client app < "$PROCESSED_FILE" 2> "$IMPORT_ERROR_LOG"
if [ $? -ne 0 ]; then
  log "Error: Failed to import $PROCESSED_FILE into $MARIADB_DB."
  log "Check $IMPORT_ERROR_LOG for details."
  exit 1
fi
log "Data successfully imported into $MARIADB_DB."
ui_task_done

for t in "${TABLES_TO_STRIP[@]}"; do
  log "Verifying $t count ..."
  db_client app -e "SELECT COUNT(*) AS ${t}_count FROM \`$t\`;" || log "Warning: Could not verify $t."
done

ui_task_start "Checking the imported data"
log "Verifying Magento store tables ..."
db_client app -e "
SELECT COUNT(*) AS store_website_count FROM store_website;
SELECT COUNT(*) AS store_group_count FROM store_group;
SELECT COUNT(*) AS store_count FROM store;
" || log "Warning: Could not verify Magento store tables."

log "Removing temporary SQL files ..."
rm -f "$PROCESSED_FILE"
log "Temporary files removed. Import error log kept at $IMPORT_ERROR_LOG."

log "Repointing OpenSearch at the local container ..."
# Writes straight to core_config_data instead of using `bin/magento config:set`
# — the imported dump's module versions are routinely behind what's already
# deployed on this box, and Magento's CLI refuses to run config:set at all
# until setup:upgrade has run ("run app:config:import or setup:upgrade
# command before"). That previously made this step silently no-op (it only
# logged a warning), leaving OpenSearch pointed at the dump's own
# environment right before the setup:upgrade this script recommends next —
# which itself needs a working OpenSearch connection to complete, deadlocking.
db_client app -e "
UPDATE core_config_data SET value='opensearch' WHERE path='catalog/search/engine';
UPDATE core_config_data SET value='opensearch' WHERE path='catalog/search/opensearch_server_hostname';
UPDATE core_config_data SET value='9200' WHERE path='catalog/search/opensearch_server_port';
" || log "Warning: Could not repoint OpenSearch config."

ui_task_done
log "DB refresh complete."
log "Next suggested commands:"
log "$(cmd_hint magento setup:upgrade)"
log "$(cmd_hint magento cache:flush)"
