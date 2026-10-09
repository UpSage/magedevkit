# Host-port allocation for the debug ports docker-compose.local.yml publishes
# — sourced by bin/platform, which calls allocate_ports before every `up`.
# Keeps each port in the env file as long as nothing else claims it, and
# otherwise moves it to the next free port and saves that, so ports stay
# stable across restarts (DB GUI connections keep working) yet never collide
# with another stack.
#
# "Claimed" = bound by any other compose project's container — running or
# stopped, since Docker keeps a stopped container's port bindings and it will
# want them back on its next start — or by a live listener on this host.

PORT_KEYS=(DB_PORT VALKEY_PORT RABBITMQ_PORT RABBITMQ_MANAGEMENT_PORT OPENSEARCH_PORT MAILPIT_HTTP_PORT MAILPIT_SMTP_PORT)

# set_env_var
# shellcheck disable=SC1091
source "$(dirname "${BASH_SOURCE[0]}")/env.sh"

allocate_ports() {  # $1=env file, $2=this stack's compose project name
    local env_file="$1" project="$2"
    local -A claimed=() own=() taken=()
    local proj ports p key current candidate

    # project|port port ... for every container on this Docker host.
    while IFS='|' read -r proj ports; do
        for p in $ports; do
            if [ "$proj" = "$project" ]; then own[$p]=1; else claimed[$p]="$proj"; fi
        done
    done < <(docker ps -aq | xargs -r docker inspect -f \
        '{{index .Config.Labels "com.docker.compose.project"}}|{{range $p,$b := .HostConfig.PortBindings}}{{range $b}}{{.HostPort}} {{end}}{{end}}' 2>/dev/null)

    # Live listeners, minus this stack's own (it may already be running).
    if command -v ss >/dev/null 2>&1; then
        while read -r p; do
            [ -n "${own[$p]:-}" ] || [ -n "${claimed[$p]:-}" ] || claimed[$p]="a process on this host"
        done < <(ss -ltnH 2>/dev/null | awk '{n=split($4,a,":"); print a[n]}' | sort -u)
    fi

    for key in "${PORT_KEYS[@]}"; do
        current="$(env_get "$env_file" "$key")"
        [[ "$current" =~ ^[0-9]+$ ]] || continue
        candidate="$current"
        while [ -n "${claimed[$candidate]:-}" ] || [ -n "${taken[$candidate]:-}" ]; do
            candidate=$((candidate + 1))
        done
        if [ "$candidate" != "$current" ]; then
            echo "Port $key $current → $candidate ($current is in use by ${claimed[$current]:-another port in this stack})." >&2
            set_env_var "$env_file" "$key" "$candidate"
        fi
        taken[$candidate]=1
    done
}
