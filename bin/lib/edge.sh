# Shared edge proxy (compose/edge/docker-compose.yml) — sourced by
# bin/platform, which calls ensure_edge before every `up`. Expects
# PLATFORM_DIR. Idempotent: a no-op when the edge is already running.

EDGE_NETWORK=platform-edge
EDGE_PROJECT=platform-edge

ensure_edge() {
    docker network inspect "$EDGE_NETWORK" >/dev/null 2>&1 \
        || docker network create "$EDGE_NETWORK" >/dev/null

    if [ -n "$(docker ps -q --filter "label=com.docker.compose.project=$EDGE_PROJECT" --filter status=running)" ]; then
        return 0
    fi

    if ! docker compose -p "$EDGE_PROJECT" -f "$PLATFORM_DIR/compose/edge/docker-compose.yml" up -d >&2; then
        local holders
        holders="$(docker ps --format '{{.Names}} {{.Ports}}' | grep -E '0\.0\.0\.0:(80|443)->|\[::\]:(80|443)->' | cut -d' ' -f1 | tr '\n' ' ')"
        echo "Error: the shared edge proxy couldn't bind host ports 80/443." >&2
        if [ -n "$holders" ]; then
            echo "  Held by: $holders— stop those containers (e.g. another local Magento stack) and retry." >&2
        else
            echo "  Something outside Docker (IIS, Apache, Skype, another proxy …) is holding them — free them and retry." >&2
        fi
        return 1
    fi
}
