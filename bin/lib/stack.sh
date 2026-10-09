# Stack lifecycle helpers shared by setup and refresh — sourced, never run.
# Expects PLATFORM_DIR and bin/lib/ui.sh's helpers.

# Waits until every service with a healthcheck reports healthy. $1=timeout
# in seconds (default 180). Returns 1 (after printing the status) on timeout.
wait_for_healthy() {
    local timeout="${1:-180}" deadline health unhealthy
    deadline=$((SECONDS + timeout))
    while true; do
        # A failing `ps` must not look like "nothing unhealthy".
        if health="$("$PLATFORM_DIR/bin/platform" ps --format '{{.Health}}')"; then
            unhealthy="$(printf '%s\n' "$health" | grep -vE '^(healthy|)$' || true)"
            [ -z "$unhealthy" ] && return 0
        fi
        if [ "$SECONDS" -ge "$deadline" ]; then
            "$PLATFORM_DIR/bin/platform" ps
            log "Services still unhealthy after ${timeout}s — see the status above and check 'bin/platform logs <service>'."
            return 1
        fi
        sleep 5
    done
}

# Makes sure Docker is reachable and this project's whole stack is up and
# healthy — the usual state after a reboot or a Docker Desktop restart is
# "stopped". Offers to start it (default yes), then waits for healthy.
ensure_stack_running() {
    local all running missing
    until docker ps >/dev/null 2>&1; do
        log "Docker isn't reachable — start Docker Desktop (with WSL integration enabled for this distro)."
        ui_interactive || fail "Docker isn't reachable — start Docker Desktop and re-run."
        if ui_web; then
            [ "$(ui_ask menu "Docker isn't running. Start Docker Desktop, wait until it says it's running, then choose Retry." 1 'Retry' 'Cancel')" = 1 ] \
                || cancel "Cancelled — Docker isn't running."
        else
            prompt_yes_no 'Retry now?' Y || cancel "Cancelled — Docker isn't running."
        fi
    done

    all="$("$PLATFORM_DIR/bin/platform" config --services | sort)"
    [ -n "$all" ] || fail "Couldn't read this stack's services (docker compose config failed — see above)."
    running="$("$PLATFORM_DIR/bin/platform" ps --status running --services 2>/dev/null | sort)"
    missing="$(comm -23 <(printf '%s\n' "$all") <(printf '%s\n' "$running") | grep -v '^$' || true)"
    if [ -n "$missing" ]; then
        log "Not running: $(echo "$missing" | tr '\n' ' ')"
        prompt_yes_no 'The stack is (partly) stopped. Start it now?' Y \
            || cancel "Cancelled — the stack has to be running for this."
        ui_task "Starting the stack" "$PLATFORM_DIR/bin/platform" up -d
    else
        log "✓ Stack is running"
    fi
    ui_task "Waiting for services to report healthy" wait_for_healthy 180 \
        || fail "Services didn't become healthy — check 'bin/platform logs <service>'."
}
