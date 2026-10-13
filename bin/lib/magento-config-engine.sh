# Magento configuration that a DB import wipes (core_config_data) or that a
# fresh install doesn't set — sourced by bin/lib/refresh-engine.sh (every
# refresh) and bin/lib/setup-engine.sh (fresh vanilla install). Never invoke
# directly.
#
# Expects the caller to have already defined: log(), MAGENTO, and the env
# values from the env file (OPENSEARCH_INDEX_PREFIX, REMOTE_STORAGE_*,
# BASE_MEDIA_URL/BASE_STATIC_URL/COOKIE_DOMAIN, MAGENTO_ADMIN_URI).

apply_magento_config() {
    # sync-db's DB reimport overwrites core_config_data, wiping the search
    # engine selection/connection settings along with everything else covered
    # below — this must be reapplied every refresh, not just set once.
    log "Configuring OpenSearch connection ..."
    "$MAGENTO" config:set catalog/search/engine opensearch -n
    "$MAGENTO" config:set catalog/search/opensearch_server_hostname opensearch -n
    "$MAGENTO" config:set catalog/search/opensearch_server_port 9200 -n
    "$MAGENTO" config:set catalog/search/opensearch_index_prefix "${OPENSEARCH_INDEX_PREFIX:-magento}" -n
    "$MAGENTO" config:set catalog/search/opensearch_enable_auth 0 -n
    "$MAGENTO" config:set catalog/search/opensearch_server_timeout 15 -n

    # sync-db's DB reimport overwrites core_config_data, wiping whatever
    # caching_application setting the prod dump had, so this needs reapplying
    # every refresh (unlike http_cache_hosts, which lives in env.php and only
    # needs setting once — see README).
    log "Configuring Varnish full-page cache ..."
    "$MAGENTO" config:set system/full_page_cache/caching_application 2 -n
    # Only feeds the admin's "Export VCL" button — Varnish itself runs
    # config/varnish/default.vcl, whose purge ACL is fixed. Magento 2.4.9+
    # accepts CIDR ranges here; older releases reject "/" outright, so fall
    # back to the php container (the one sending PURGEs) plus localhost.
    "$MAGENTO" config:set system/full_page_cache/varnish/access_list "10.0.0.0/8,172.16.0.0/12,192.168.0.0/16" -n >/dev/null 2>&1 \
        || "$MAGENTO" config:set system/full_page_cache/varnish/access_list "localhost,php" -n
    "$MAGENTO" config:set system/full_page_cache/varnish/backend_host nginx -n
    "$MAGENTO" config:set system/full_page_cache/varnish/backend_port 8080 -n
    "$MAGENTO" config:set system/full_page_cache/varnish/grace_period 300 -n

    log "Disabling database media storage ..."
    "$MAGENTO" config:set system/media_storage_configuration/media_database 0 -n

    if [ "$REMOTE_STORAGE_DRIVER" = "file" ]; then
        log "Configuring local filesystem media storage (REMOTE_STORAGE_DRIVER=file) ..."
        "$MAGENTO" setup:config:set --remote-storage-driver=file -n
    else
        [ -n "${REMOTE_STORAGE_BUCKET:-}" ] && [ "$REMOTE_STORAGE_BUCKET" != CHANGE_ME ] \
            || fail "REMOTE_STORAGE_DRIVER=aws-s3 but no S3 bucket is set — run bin/sync-db (or bin/local/setup) to choose one, set REMOTE_STORAGE_BUCKET in ${ENV_NAME:-env/.env.local}, or switch to REMOTE_STORAGE_DRIVER=file."
        log "Configuring S3 remote storage (bucket=$REMOTE_STORAGE_BUCKET, region=$REMOTE_STORAGE_REGION) ..."
        # remote_storage_apply: the keys never go on a command line.
        # shellcheck disable=SC1091
        declare -F remote_storage_apply >/dev/null || source "$PLATFORM_DIR/bin/lib/aws-storage.sh"
        remote_storage_apply "$REMOTE_STORAGE_BUCKET" "$REMOTE_STORAGE_REGION" "${REMOTE_STORAGE_KEY:-}" "${REMOTE_STORAGE_SECRET:-}"
    fi

    # sync-db's DB reimport rewrites the dump's own base_media_url to the shared
    # dev CDN unconditionally (see its PROD_MEDIA_URL/DEV_MEDIA_URL rewrite) and
    # leaves base_static_url/cookie_domain at whatever the dump had — these
    # overrides (blank by default, meaning "keep that behavior") come from
    # the env file, set once by bin/local/setup, and must be reapplied every
    # refresh for the same reason as everything else in this block.
    if [ -n "${BASE_MEDIA_URL:-}" ]; then
        log "Overriding base_media_url ..."
        "$MAGENTO" config:set web/unsecure/base_media_url "$BASE_MEDIA_URL" -n
        "$MAGENTO" config:set web/secure/base_media_url "$BASE_MEDIA_URL" -n
    fi
    if [ -n "${BASE_STATIC_URL:-}" ]; then
        log "Overriding base_static_url ..."
        "$MAGENTO" config:set web/unsecure/base_static_url "$BASE_STATIC_URL" -n
        "$MAGENTO" config:set web/secure/base_static_url "$BASE_STATIC_URL" -n
    fi
    if [ -n "${COOKIE_DOMAIN:-}" ]; then
        log "Overriding cookie domain ..."
        "$MAGENTO" config:set web/cookie/cookie_domain "$COOKIE_DOMAIN" -n
    fi

    # Unlike the config:set calls above, this doesn't live in core_config_data
    # (sync-db's DB reimport doesn't wipe it) — it's in env.php, normally set
    # once by hand per README's setup steps. Reapplying it here too makes it
    # self-healing instead of depending on that manual step never having been
    # skipped or undone by some later full reset. Falls back to Magento's own
    # default ("admin") when the sourced env file doesn't define
    # MAGENTO_ADMIN_URI.
    log "Setting admin panel frontName ..."
    "$MAGENTO" setup:config:set --backend-frontname="${MAGENTO_ADMIN_URI:-admin}" -n
}
