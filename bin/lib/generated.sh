# Magento's compiled code (generated/code + generated/metadata) going out of
# date — sourced by bin/magento and bin/lib/bootstrap.sh. Sourced, never run.
# Expects PLATFORM_DIR, MAGENTO_DIR; clear_generated also EXEC_USER.
#
# Once setup:di:compile has run (every Build Magento does, in any mode),
# bin/magento builds its command list from generated/metadata before it runs
# anything. A module removed or renamed since then — by hand, a git pull or
# branch switch, a composer change, module:disable — leaves interceptors and
# DI config pointing at classes that are gone, and EVERY command dies with
# 'Class "…" not found' in generated/code/…/Interceptor.php: cache:flush,
# maintenance:enable, even the setup:upgrade / setup:di:compile that would
# fix it.

# generated_stale [MAGENTO_DIR] — 0 (and the reason on stdout) when there is
# compiled DI and something it was compiled from changed after it; 1 when
# it's up to date (or there is none); 2 when it couldn't tell — a failing
# find must not look like "up to date". Host-side timestamps only (no PHP,
# no container), so it's cheap enough for every bin/magento call. Deleting
# a module folder updates its parent folder's time, so app/code/<Vendor>/
# catches removed modules even when config.php wasn't touched.
generated_stale() {
    local dir="${1:-$MAGENTO_DIR}" meta f
    meta="$dir/generated/metadata/global.php"
    [ -f "$meta" ] || return 1
    for f in app/etc/config.php composer.lock; do
        if [ "$dir/$f" -nt "$meta" ]; then
            echo "$f changed since the last compile"
            return 0
        fi
    done
    [ -d "$dir/app/code" ] || return 1
    f="$(find "$dir/app/code" -maxdepth 1 -type d -newer "$meta" -print -quit)" || return 2
    if [ -n "$f" ]; then
        echo "a module was added or removed in ${f#"$dir"/} since the last compile"
        return 0
    fi
    f="$(find "$dir/app/code" -mindepth 3 -maxdepth 5 \( -name registration.php -o -name di.xml \) \
        -newer "$meta" -print -quit)" || return 2
    if [ -n "$f" ]; then
        echo "${f#"$dir"/} changed since the last compile"
        return 0
    fi
    return 1
}

# clear_generated — empties generated/code and generated/metadata inside the
# php container, as the CLI user (who owns them in both ownership models).
clear_generated() {
    "$PLATFORM_DIR/bin/platform" exec -T --user "$EXEC_USER" php rm -rf generated/code generated/metadata
    "$PLATFORM_DIR/bin/platform" exec -T --user "$EXEC_USER" php mkdir -p generated/code generated/metadata
}
