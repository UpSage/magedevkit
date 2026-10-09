# Resolves which uid:gid php-fpm and CLI commands run as, from
# FILE_OWNERSHIP in the env file — sourced by bin/platform, bin/magento,
# bin/composer, bin/local/setup, bin/local/refresh, and the
# permissions/mode engines.
# Expects PLATFORM_DIR. Sets/exports:
#   PHP_UID/PHP_GID — docker-compose.local.yml's php `user:` (php-fpm)
#   EXEC_USER       — `--user` for every CLI exec (composer, bin/magento)
#
#   single    (default) php-fpm and CLI both run as you — Adobe's one-owner
#             model, nothing to chown.
#   two-owner php-fpm runs as www-data (33); CLI runs as you with group
#             www-data, so files either side creates are group-shared —
#             Adobe's two-owner model (see bin/lib/permissions-engine.sh).

# env_get
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"
FILE_OWNERSHIP="$(env_get "${ENV_FILE:-$PLATFORM_DIR/env/.env.local}" FILE_OWNERSHIP single)"
WWW_DATA_UID=33
WWW_DATA_GID=33

case "$FILE_OWNERSHIP" in
    single)
        PHP_UID="$(id -u)"
        PHP_GID="$(id -g)"
        EXEC_USER="$(id -u):$(id -g)"
        ;;
    two-owner)
        PHP_UID="$WWW_DATA_UID"
        PHP_GID="$WWW_DATA_GID"
        EXEC_USER="$(id -u):$WWW_DATA_GID"
        ;;
    *)
        echo "Error: FILE_OWNERSHIP='$FILE_OWNERSHIP' in ${ENV_NAME:-env/.env.local} — expected 'single' or 'two-owner'." >&2
        exit 1
        ;;
esac
export PHP_UID PHP_GID FILE_OWNERSHIP
