# Setup wizard, part 5 of 11 — Step 5: database credentials (only while the MariaDB volume does not exist yet).
# Sourced in order by bin/lib/setup-engine.sh (top-level script, shared
# variables); never run on its own.

# ------------------------------------------------------------------
# Database credentials — only while the MariaDB volume doesn't exist yet.
# MariaDB applies DB_* only when it first initializes ${APP_NAME}-db-data;
# changing them afterwards would just break the connection (see README's
# "Environment files" section for how to change them later).
# ------------------------------------------------------------------

ui_step setup 5

if ! docker volume inspect "${APP_NAME:-app}-db-data" >/dev/null 2>&1; then
    log "Database login for the local MariaDB — set once, when the database is first created."
    DB_NAME="$(prompt_db_ident "What should the database be called?" "$DB_NAME" "database name")"
    DB_USER="$(prompt_db_ident "Which username should Magento connect with?" "$DB_USER" "database username")"
    DB_PASSWORD="$(prompt_db_secret "What password should that user have?" "$DB_PASSWORD" "database password")"
    DB_ROOT_PASSWORD="$(prompt_db_secret "What should the database root (admin) password be?" "$DB_ROOT_PASSWORD" "root password")"
    for k in DB_NAME DB_USER DB_PASSWORD DB_ROOT_PASSWORD; do
        set_env_var "$ENV_FILE" "$k" "${!k}"
    done
    log "✓ Database credentials saved to $ENV_NAME (DB_NAME=$DB_NAME, DB_USER=$DB_USER)."
else
    log "✓ Database already initialized (volume ${APP_NAME:-app}-db-data exists) — keeping DB_NAME=$DB_NAME, DB_USER=$DB_USER."
fi
