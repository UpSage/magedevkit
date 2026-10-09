# Setup wizard, part 8 of 11 — Steps 8-9: TLS certificate, build and start the stack, composer install.
# Sourced in order by bin/lib/setup-engine.sh (top-level script, shared
# variables); never run on its own.

# ------------------------------------------------------------------
# TLS certificate (mkcert, locally trusted)
# ------------------------------------------------------------------

ui_step setup 8

if [ ! -f "$CERTS_DIR/$APP_DOMAIN.pem" ]; then
    ui_task_start "Creating the HTTPS certificate for $APP_DOMAIN"
    log "Generating a locally-trusted HTTPS certificate for $APP_DOMAIN ..."
    mkcert -install
    mkdir -p "$CERTS_DIR"
    (cd "$CERTS_DIR" && mkcert "$APP_DOMAIN")
    ui_task_done
else
    log "TLS certificate for $APP_DOMAIN already exists."
fi

# ------------------------------------------------------------------
# Build & start the stack, then wait for every service to report healthy
# ------------------------------------------------------------------

ui_task "Building images" "$PLATFORM_DIR/bin/platform" build
# A brand-new data volume can make a service fail its very first start
# (RabbitMQ's image, for one, sometimes can't read its .erlang.cookie on the
# first boot; the restart policy brings it up seconds later). `up -d` then
# reports "dependency failed to start" — so give it one more try before
# calling it a failure.
if ! ui_task "Starting the stack" "$PLATFORM_DIR/bin/platform" up -d; then
    log "Starting the stack didn't finish — a service may still be on its first start. Trying once more in 20 s ..."
    sleep 20
    ui_task "Starting the stack (second try)" "$PLATFORM_DIR/bin/platform" up -d \
        || fail "The stack didn't start — see the output above, and: $(cmd_hint platform logs)."
fi
ui_task "Waiting for services to report healthy" wait_for_healthy 180 \
    || fail "Services still unhealthy after 180s — see the status above and check: $(cmd_hint platform logs) <service>"
"$PLATFORM_DIR/bin/platform" ps

# ------------------------------------------------------------------
# Composer install
# ------------------------------------------------------------------

ui_step setup 9

if [ "$FRESH_SOURCE" = 1 ]; then
    ui_task_start "Downloading Magento $MAGENTO_VERSION (several minutes)"
    log "Downloading $MAGENTO_PACKAGE $MAGENTO_VERSION (composer create-project — several minutes) ..."
    [ -z "$(ls -A "$MAGENTO_DIR" 2>/dev/null)" ] || fail "magento/ is no longer empty — create-project needs an empty directory."
    COMPOSER_AUTH="$(cat "$FRESH_AUTH")" "$PLATFORM_DIR/bin/composer" create-project \
        --repository-url=https://repo.magento.com/ \
        "$MAGENTO_PACKAGE=$MAGENTO_VERSION" .
    install -m 600 "$FRESH_AUTH" "$AUTH_JSON"
    magento_source_valid "$MAGENTO_DIR" \
        || fail "create-project finished but $MAGENTO_DIR doesn't look like a Magento tree."
    log "✓ Magento $MAGENTO_VERSION downloaded (credentials saved to magento/auth.json)"
    ui_task_done
fi

# Unlock first (a previous production run may have locked the tree), then
# match ownership to FILE_OWNERSHIP before anything writes into magento/.
ui_task_start "Preparing file permissions"
"$PLATFORM_DIR/bin/permissions/unlock"
"$PLATFORM_DIR/bin/permissions/apply"
ui_task_done

# A cloned/existing project's composer.lock was resolved against some
# other PHP — check the container's PHP version and extensions against it
# now, instead of letting composer install (or Magento at runtime) fail
# with something far less clear.
if [ "$FRESH_SOURCE" != 1 ]; then
    if [ -f "$MAGENTO_DIR/composer.lock" ]; then
        log "Checking PHP ${PHP_VERSION:-} and extensions against composer.lock ..."
        if "$PLATFORM_DIR/bin/composer" check-platform-reqs --lock --no-dev; then
            log "✓ PHP ${PHP_VERSION:-} and extensions satisfy composer.lock"
        else
            log "⚠ The PHP container doesn't meet this project's requirements (see the table above)."
            log "  Set PHP_VERSION in $ENV_NAME to a version the project supports — or pin it in magento/composer.json's config.platform.php, which setup follows — and re-run setup."
            prompt_yes_no 'Continue anyway?' N || fail "Stopped — fix the PHP version/extensions above and re-run."
            log "Continuing despite unmet platform requirements."
        fi
    else
        log "No composer.lock — composer install will resolve dependencies against PHP ${PHP_VERSION:-} in the container."
    fi
fi

ui_task "Installing Composer dependencies" "$PLATFORM_DIR/bin/composer" install
