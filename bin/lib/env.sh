# env-file helpers shared by setup, sync-db, mode/ownership and the version
# switcher — sourced, never run.

# Escapes $1 for safe use on the right-hand side of a sed s/// substitution.
esc_sed() {
    printf '%s' "$1" | sed -e 's/[\/&]/\\&/g'
}

# env_get FILE KEY [DEFAULT] — KEY's value in a KEY=value file, read the way
# `source` and docker compose see it: the last assignment wins, the value is
# everything after the first `=`, and one pair of surrounding quotes is
# dropped. DEFAULT (or nothing) when the key is missing or blank, or the
# file can't be read. Never runs the file.
env_get() {
    local file="$1" key="$2" default="${3-}" v
    v="$(grep "^${key}=" "$file" 2>/dev/null | tail -n 1)" || true
    v="${v#*=}"
    if [ "${#v}" -ge 2 ]; then
        case "$v" in
            \"*\") v="${v:1:${#v}-2}" ;;
            \'*\') v="${v:1:${#v}-2}" ;;
        esac
    fi
    printf '%s' "${v:-$default}"
}

# Escapes $1 to match literally as a sed (basic) regex with `|` as the
# delimiter — e.g. a URL whose dots must not match any character.
sed_re_literal() {
    printf '%s' "$1" | sed -e 's/[]\/$*.^[|]/\\&/g'
}

# Escapes $1 for the replacement side of a sed s||| (`\`, `&` and `|`).
sed_repl_literal() {
    printf '%s' "$1" | sed -e 's/[\/&|]/\\&/g'
}

# A hostname like shop.test or my-store.local: dot-separated labels of
# letters, digits and hyphens. Used before a domain reaches sed, mkcert,
# compose labels or a URL.
valid_hostname() {
    [[ "$1" =~ ^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)+$ ]] && [ "${#1}" -le 253 ]
}

# Updates KEY=value in $1, in place, adding the line if it isn't already there.
# The value is copied literally (awk via the environment — no sed script, so
# `|`, `&`, `\` or `/` in it can't change what runs). Refuses multi-line
# values and odd keys: one KEY=value per line is what every reader expects.
# Rewrites the file in place (same inode), so its owner and mode stay.
set_env_var() {
    local file="$1" key="$2" value="$3" tmp
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || { echo "set_env_var: invalid key '$key'" >&2; return 1; }
    case "$value" in
        *$'\n'*|*$'\r'*) echo "set_env_var: $key must be a single line" >&2; return 1 ;;
    esac
    if grep -q "^${key}=" "$file"; then
        tmp="$(mktemp)" || return 1
        if KEY="$key" VALUE="$value" awk 'BEGIN { p = ENVIRON["KEY"] "=" }
                index($0, p) == 1 { print p ENVIRON["VALUE"]; next } { print }' "$file" > "$tmp"; then
            cat "$tmp" > "$file"
        else
            rm -f "$tmp"
            return 1
        fi
        rm -f "$tmp"
    else
        printf '%s=%s\n' "$key" "$value" >> "$file"
    fi
}
