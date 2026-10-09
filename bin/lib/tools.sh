# Command-line tools the platform installs for you into ~/.local/bin, only
# when a flow needs them: mkcert (setup's TLS step), the AWS CLI and direnv
# (S3 media storage). Sourced, never run. The installers use log/fail from
# bin/lib/ui.sh; machine_arch and local_bin_on_path need nothing.

# amd64 or arm64 for this machine (blank if it's neither).
machine_arch() {
    case "$(uname -m)" in
        x86_64|amd64) echo amd64 ;;
        aarch64|arm64) echo arm64 ;;
    esac
}

# Makes ~/.local/bin exist and be on PATH — now, and in new shells (~/.bashrc).
local_bin_on_path() {
    mkdir -p "$HOME/.local/bin"
    # shellcheck disable=SC2016  # written to .bashrc unexpanded on purpose
    grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null \
        || echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
    case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) export PATH="$HOME/.local/bin:$PATH" ;; esac
}

# require_installed CMD — fails when an install finished but CMD still isn't found.
require_installed() {
    command -v "$1" >/dev/null 2>&1 \
        || fail "$1 install finished but it's still not on PATH — check ~/.local/bin/$1 exists and ~/.local/bin is in PATH."
}

install_mkcert() {
    local arch tmp
    arch="$(machine_arch)"
    [ -n "$arch" ] || fail "No mkcert build for $(uname -m) — install mkcert yourself (https://github.com/FiloSottile/mkcert)."
    log "'mkcert' not found — installing it into ~/.local/bin ..."
    local_bin_on_path
    tmp="$HOME/.local/bin/mkcert.tmp"
    curl -fsSL "https://github.com/FiloSottile/mkcert/releases/latest/download/mkcert-v1.4.4-linux-$arch" -o "$tmp" \
        || { rm -f "$tmp"; fail "Couldn't download mkcert."; }
    install -m 755 "$tmp" "$HOME/.local/bin/mkcert"
    rm -f "$tmp"
    require_installed mkcert
    log "Installed mkcert ($(mkcert -version 2>&1))."
}

# Only ever called from the aws-s3 storage branch: S3 is an optional Magento
# storage capability, not a prerequisite for setup in general.
install_aws_cli() {
    local machine tmp
    case "$(machine_arch)" in
        amd64) machine=x86_64 ;;
        arm64) machine=aarch64 ;;
        *) fail "No AWS CLI build for $(uname -m) — install it yourself (https://aws.amazon.com/cli/)." ;;
    esac
    log "'aws' not found — installing AWS CLI v2 into ~/.local ..."
    tmp="$(mktemp -d)"
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-$machine.zip" -o "$tmp/awscliv2.zip" \
        || { rm -rf "$tmp"; fail "Couldn't download the AWS CLI."; }
    python3 -m zipfile -e "$tmp/awscliv2.zip" "$tmp"
    chmod +x "$tmp/aws/install" "$tmp/aws/dist/aws" "$tmp/aws/dist/aws_completer"
    "$tmp/aws/install" --install-dir "$HOME/.local/aws-cli" --bin-dir "$HOME/.local/bin" --update
    rm -rf "$tmp"
    local_bin_on_path
    require_installed aws
    log "Installed $(aws --version 2>&1)."
}

# Only called when the aws-s3 branch creates .envrc for the first time
# (direnv/.envrc exist purely to export AWS_PROFILE for later manual runs).
install_direnv() {
    local arch kernel url
    arch="$(machine_arch)"
    [ -n "$arch" ] || fail "Unsupported architecture for direnv auto-install ($(uname -m)) — install it manually, see https://direnv.net/docs/installation.html."
    kernel="$(uname -s | tr '[:upper:]' '[:lower:]')"
    log "'direnv' not found — installing it into ~/.local/bin ..."
    local_bin_on_path
    url="$(curl -fsSL "https://api.github.com/repos/direnv/direnv/releases/latest" \
        | grep browser_download_url | cut -d '"' -f 4 | grep "direnv.${kernel}.${arch}\$")"
    [ -n "$url" ] || fail "Couldn't find a direnv download for ${kernel}/${arch}."
    curl -fsSL -o "$HOME/.local/bin/direnv" "$url" || fail "Couldn't download direnv."
    chmod +x "$HOME/.local/bin/direnv"
    # shellcheck disable=SC2016
    grep -qxF 'eval "$(direnv hook bash)"' "$HOME/.bashrc" 2>/dev/null \
        || echo 'eval "$(direnv hook bash)"' >> "$HOME/.bashrc"
    require_installed direnv
    log "Installed direnv ($(direnv version 2>&1))."
}
