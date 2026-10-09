# Database dump / recreate / restore helpers — shared by the DB refresh
# (bin/lib/db-refresh-engine.sh) and the version switcher's snapshots
# (bin/lib/version-engine.sh). Sourced, never run.
#
# Expects PLATFORM_DIR, MARIADB_DB/MARIADB_USER/MARIADB_PASSWORD/
# MARIADB_ROOT_PASSWORD (db_credentials_from_env sets them) and
# bin/lib/ui.sh's helpers.
#
# Every database call goes through db_client / db_dump_raw: the password is
# handed to the container in its environment (MYSQL_PWD), never on a command
# line, where `ps` or `docker inspect` would show it.

DB_SERVICE="mariadb"
DB_ROOT_USER="root"

# MARIADB_* from the env file's DB_* — the same values docker-compose.yml
# hands the mariadb container and setup passes to setup:install.
db_credentials_from_env() {
  MARIADB_DB="${DB_NAME:?DB_NAME is not set in $ENV_NAME}"
  MARIADB_USER="${DB_USER:?DB_USER is not set in $ENV_NAME}"
  MARIADB_PASSWORD="${DB_PASSWORD:?DB_PASSWORD is not set in $ENV_NAME}"
  MARIADB_ROOT_PASSWORD="${DB_ROOT_PASSWORD:?DB_ROOT_PASSWORD is not set in $ENV_NAME}"
}

# db_client root|app [MARIADB ARGS...] — the mariadb client in the DB
# container, as root or as the app user (then in its database). stdin and
# stdout pass through (imports, queries with -N -e).
db_client() {
  local as="$1" user pw
  shift
  case "$as" in
    root) user="$DB_ROOT_USER"; pw="$MARIADB_ROOT_PASSWORD" ;;
    app) user="$MARIADB_USER"; pw="$MARIADB_PASSWORD"; set -- "$@" "$MARIADB_DB" ;;
    *) fail "db_client: expected root or app, got '$as'." ;;
  esac
  MYSQL_PWD="$pw" "$PLATFORM_DIR/bin/platform" exec -T -e MYSQL_PWD "$DB_SERVICE" mariadb -h 127.0.0.1 -u "$user" "$@"
}

# 0 if the database has at least one table, 1 if it has none, 2 if the
# check itself failed (DB down, wrong password …) — callers must not treat
# 2 as "empty".
db_has_tables() {
  local out
  out="$(db_client root -N -e "SELECT 1 FROM information_schema.tables WHERE table_schema='$MARIADB_DB' LIMIT 1;")" || return 2
  [ "$out" = 1 ]
}

# Gzipped dump of the whole database to $1 (written as $1.part, then moved,
# so a half-written file never looks complete). Returns 1 on failure.
db_dump_to() {
  local file="$1"
  mkdir -p "$(dirname "$file")"
  ( umask 077; : > "$file.part" )  # a dump is the whole database: owner-only
  MYSQL_PWD="$MARIADB_ROOT_PASSWORD" "$PLATFORM_DIR/bin/platform" exec -T -e MYSQL_PWD "$DB_SERVICE" \
      mariadb-dump -h 127.0.0.1 -u "$DB_ROOT_USER" --single-transaction --quick --routines --triggers --events "$MARIADB_DB" \
    | gzip > "$file.part" \
    || { rm -f "$file.part"; return 1; }
  mv "$file.part" "$file"
}

# Drops and recreates the (empty) database and its app user's grants.
db_recreate() {
  db_client root -e "
DROP DATABASE IF EXISTS \`$MARIADB_DB\`;
CREATE DATABASE \`$MARIADB_DB\` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS '$MARIADB_USER'@'%' IDENTIFIED BY '$MARIADB_PASSWORD';
GRANT ALL PRIVILEGES ON \`$MARIADB_DB\`.* TO '$MARIADB_USER'@'%';
FLUSH PRIVILEGES;
"
}

# Replaces the database with the gzipped dump $1 (one made by db_dump_to —
# no URL rewriting or definer cleanup, unlike a refresh).
db_restore_from() {
  local file="$1"
  gunzip -t "$file" || fail "$file isn't a valid .gz file."
  db_recreate || fail "Failed to recreate database $MARIADB_DB."
  gunzip -c "$file" | db_client app \
    || fail "Failed to import $file into $MARIADB_DB."
}

# Keeps the newest $3 files named $2*.sql.gz in $1 and deletes the rest.
# Handles names with spaces (no ls | xargs).
prune_backups() {
  local dir="$1" prefix="$2" keep="$3" f
  local -a files=()
  while IFS= read -r -d '' f; do files+=("${f#* }"); done < <(
    find "$dir" -maxdepth 1 -type f -name "${prefix}*.sql.gz" -printf '%T@ %p\0' 2>/dev/null | sort -z -rn)
  [ "${#files[@]}" -gt "$keep" ] && rm -f -- "${files[@]:$keep}"
  return 0
}
