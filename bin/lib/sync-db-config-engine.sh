# sync-db config-resolution engine — sourced by bin/local/sync-db once it has
# set up its credentials. Never invoke this file directly. Every `prompt()`
# call falls back to its own default without blocking when there's no TTY.
#
# Expects the caller to have already defined: log(), fail(), prompt(),
# set_env_var(), PLATFORM_DIR, ENV_FILE, APP_DOMAIN, and whatever
# REMOTE_STORAGE_*/BASE_MEDIA_URL/BASE_STATIC_URL/PROD_* values are already
# known (sourced from the env file). Optional:
# PRESET_DB_SOURCE/PRESET_PROD_CONFIG_CHOICE/PRESET_CONTINUE_CHOICE (skip
# those specific prompts entirely, used by bin/local/setup's own earlier
# Data Refresh planning), TABLES_TO_STRIP_DEFAULT (defaults to blank).

# Authenticates to S3 via a named CLI profile.
: "${AWS_PROFILE:=${AWS_PROFILE_DEFAULT:-default}}"
export AWS_PROFILE

# Single source prompt — one field, auto-detected, instead of asking
# "local or remote?" first. Required — no blank/auto-pull-from-S3 default
# exists at all, so a presigned S3 URL never has to be shown as a prompt
# default just to make "press Enter" convenient. Can be pre-answered via
# PRESET_DB_SOURCE (bin/local/setup's own earlier prompt).
ui_step refresh 2
if [ -n "${PRESET_DB_SOURCE:-}" ]; then
  DB_SOURCE_INPUT="$(ui_normalize_path "$PRESET_DB_SOURCE")"
else
  while true; do
    if ui_web; then
      DB_SOURCE_INPUT="$(prompt_db_source 'Which database dump should be imported? (.sql.gz)')"
    else
      DB_SOURCE_INPUT="$(ui_normalize_path "$(prompt 'Database source (local file path or URL)' '')")"
    fi
    case "$DB_SOURCE_INPUT" in
      '')
        # No terminal/page to ask again: fail instead of looping forever.
        ui_interactive || fail "Database source can't be blank — run this in a terminal (or $(cmd_hint refresh-wizard)), or pass PRESET_DB_SOURCE."
        log "Database source can't be blank — enter a local file path or URL."
        ;;
      http://*|https://*) break ;;
      *)
        [ -f "$DB_SOURCE_INPUT" ] && break
        # Re-ask instead of failing when someone can answer again.
        ui_interactive || break
        log "No file found at '$DB_SOURCE_INPUT' — check the path and try again."
        ;;
    esac
  done
fi
case "$DB_SOURCE_INPUT" in
  http://*|https://*)
    DB_URL="$DB_SOURCE_INPUT"
    ;;
  *)
    LOCAL_SOURCE_FILE="$DB_SOURCE_INPUT"
    [ -f "$LOCAL_SOURCE_FILE" ] || fail "No file found at '$LOCAL_SOURCE_FILE'."
    ;;
esac

# Production-config gate: whether to rewrite the dump's own URLs/cookie
# domain at all. Declining just skips the rewrite for this dump — it does
# NOT skip the Project Configuration / Remote storage prompts below, since
# those are about this environment's own settings, independent of what any
# particular dump happens to contain.
ui_step refresh 3
APPLY_URL_REWRITE=1
PROD_CONFIG_CHOICE="${PRESET_PROD_CONFIG_CHOICE:-$(prompt 'Does this database still contain production configuration?' 'Y')}"
case "$PROD_CONFIG_CHOICE" in
  n|N|no|No)
    CONTINUE_CHOICE="${PRESET_CONTINUE_CHOICE:-$(prompt '⚠ The imported database may retain production URLs and environment configuration. Continue without URL replacement?' 'N')}"
    case "$CONTINUE_CHOICE" in
      y|Y|yes|Yes)
        APPLY_URL_REWRITE=0
        ;;
      *)
        fail "Refresh cancelled — no changes made."
        ;;
    esac
    ;;
esac

# Project Configuration / Remote storage — asked every standalone run so
# you can change your mind here instead of only during bin/local/setup's
# one-time setup, which never re-asks any of this once
# config/nginx-local/default.conf (Project Configuration) or a real
# REMOTE_STORAGE_DRIVER (Remote storage) already exists on this machine.
# Skipped only when bin/local/setup itself is driving this run
# (PRESET_PROD_CONFIG_CHOICE set) — by that point in bin/local/setup's own
# flow, both of those steps have already resolved everything into
# the env file, so there's nothing left to ask here.
if [ -z "${PRESET_PROD_CONFIG_CHOICE:-}" ]; then
  APP_DOMAIN="$(prompt 'Local domain' "$APP_DOMAIN")"
  set_env_var "$ENV_FILE" APP_DOMAIN "$APP_DOMAIN"
  log "Note: changing the domain here does NOT regenerate the nginx config or TLS cert — re-run bin/local/setup to pick those up."

  BASE_MEDIA_URL="$(prompt 'Override base_media_url (optional, blank = derive from base_url)' "${BASE_MEDIA_URL:-}")"
  set_env_var "$ENV_FILE" BASE_MEDIA_URL "$BASE_MEDIA_URL"

  BASE_STATIC_URL="$(prompt 'Override base_static_url (optional, blank = derive from base_url)' "${BASE_STATIC_URL:-}")"
  set_env_var "$ENV_FILE" BASE_STATIC_URL "$BASE_STATIC_URL"

  # CHANGE_ME is the env file.example's placeholder, before this has ever
  # been set to a real driver/bucket — normalize it to a sane prompt default
  # instead of showing it verbatim.
  [ "${REMOTE_STORAGE_DRIVER:-}" = "CHANGE_ME" ] && REMOTE_STORAGE_DRIVER=""
  [ "${REMOTE_STORAGE_BUCKET:-}" = "CHANGE_ME" ] && REMOTE_STORAGE_BUCKET=""

  # Same numbered-list style as bin/local/setup's own storage-driver
  # prompt, just with the default reflecting whatever's already
  # configured instead of always defaulting to option 1.
  RS_DEFAULT_NUM=1
  [ "${REMOTE_STORAGE_DRIVER:-file}" = "aws-s3" ] && RS_DEFAULT_NUM=2
  if ui_web; then
    RS_DRIVER_INPUT="$(prompt_menu 'Where should Magento store media files (pub/media)?' "$RS_DEFAULT_NUM" \
      'Local filesystem — this machine only' \
      'Amazon S3 — a shared bucket other environments can also read')"
  else
    RS_DRIVER_INPUT="$(prompt 'Where should Magento store media files (pub/media)?
  1) Local filesystem — this machine only
  2) Amazon S3 — a shared bucket other environments can also read
Select an option [1-2]' "$RS_DEFAULT_NUM")"
  fi
  case "$RS_DRIVER_INPUT" in
    1|file) REMOTE_STORAGE_DRIVER="file" ;;
    2|aws-s3) REMOTE_STORAGE_DRIVER="aws-s3" ;;
    *) fail "Unrecognized storage driver '$RS_DRIVER_INPUT' — expected 1 or 2 (or 'file'/'aws-s3')." ;;
  esac

  # s3_storage_choose / s3_storage_save
  # shellcheck disable=SC1091
  source "$PLATFORM_DIR/bin/lib/aws-storage.sh"
  if [ "$REMOTE_STORAGE_DRIVER" = "file" ]; then
    REMOTE_STORAGE_BUCKET=""
    REMOTE_STORAGE_REGION=""
    REMOTE_STORAGE_KEY=""
    REMOTE_STORAGE_SECRET=""
  else
    command -v aws >/dev/null 2>&1 || fail "S3 storage requires the AWS CLI — install it, or choose 'file' storage instead."

    # Profile, credentials, region, bucket and its keys.
    s3_storage_choose "$INSTANCE_DIR/.envrc" "${REMOTE_STORAGE_BUCKET:-}"

    REMOTE_STORAGE_BUCKET="$RS_BUCKET"
    REMOTE_STORAGE_REGION="$RS_REGION"
    REMOTE_STORAGE_KEY="$AWS_KEY_ID"
    REMOTE_STORAGE_SECRET="$AWS_SECRET"
  fi

  s3_storage_save "$REMOTE_STORAGE_DRIVER" "$REMOTE_STORAGE_BUCKET" "$REMOTE_STORAGE_REGION" "$REMOTE_STORAGE_KEY" "$REMOTE_STORAGE_SECRET"
fi

TARGET_BASE_URL="https://${APP_DOMAIN}/"
# Left unset otherwise — bin/lib/db-refresh-engine.sh defaults it to the
# shared non-prod media CDN itself, so the same literal doesn't need to be
# duplicated here too.
[ -n "${BASE_MEDIA_URL:-}" ] && EFFECTIVE_MEDIA_URL="$BASE_MEDIA_URL"

ui_step refresh 4

# Blank by default (nothing stripped) — requires an explicit, deliberate
# list rather than silently dropping data.
STRIP_TABLES_INPUT="$(prompt 'Tables to strip from the dump (comma-separated, optional)' "${TABLES_TO_STRIP_DEFAULT:-}")"
IFS=',' read -ra STRIP_TABLES_RAW <<< "$STRIP_TABLES_INPUT"
TABLES_TO_STRIP=()
for t in "${STRIP_TABLES_RAW[@]}"; do
  t="${t//[[:space:]]/}"
  [ -n "$t" ] || continue
  # The names go into a sed script and SQL — table-name characters only.
  [[ "$t" =~ ^[A-Za-z0-9_]{1,64}$ ]] || fail "'$t' isn't a table name (letters, digits and _ only)."
  TABLES_TO_STRIP+=("$t")
done

# Safety net for the DROP DATABASE below — a gzipped dump of the current
# database in platform/db/backups/ (newest 3 kept). Default yes; preset to N
# by bin/local/setup when the database is only a fresh install's placeholder.
BACKUP_CHOICE="${PRESET_BACKUP_CHOICE:-$(prompt 'Back up the current database before replacing it?' 'Y')}"
case "$BACKUP_CHOICE" in
  y|Y|yes|Yes) BACKUP_BEFORE_REPLACE=1 ;;
  *) BACKUP_BEFORE_REPLACE=0 ;;
esac

# Destructive-replacement confirmation. Defaults to Y, so a non-interactive
# invocation auto-confirms instead of hanging or failing. On the refresh
# wizard page the project name has to be typed to enable the button.
ui_step refresh 5
log "This will replace the current local Magento database ($MARIADB_DB)."
if ui_web && [ "${UI_FLOW:-}" = refresh ]; then
  ui_confirm_destructive "Replace the database '$MARIADB_DB' of project '${APP_NAME:-app}' with $(basename "${LOCAL_SOURCE_FILE:-$DB_URL}")?" "${APP_NAME:-app}" \
    || fail "Refresh cancelled — no changes made."
else
  REPLACE_CHOICE="$(prompt 'Continue?' 'Y')"
  case "$REPLACE_CHOICE" in
    y|Y|yes|Yes) : ;;
    *) fail "Refresh cancelled — no changes made." ;;
  esac
fi

# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/db-refresh-engine.sh"
