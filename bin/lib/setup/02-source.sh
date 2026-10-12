# Setup wizard, part 2 of 11 — Step 2: the Magento source code (GitHub clone, fresh download, or what is already there).
# Sourced in order by bin/lib/setup-engine.sh (top-level script, shared
# variables); never run on its own.

# ------------------------------------------------------------------
# Magento Setup: Magento source code
# ------------------------------------------------------------------

magento_source_valid() {  # $1=dir -> 0 if it looks like a real Magento tree
    [ -f "$1/composer.json" ] && [ -d "$1/app" ] && [ -f "$1/bin/magento" ] && [ -d "$1/pub" ]
}

# repo_remember_login / repo_ensure_identity — per-repo git identity and login.
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/git-repo.sh"

# Prompt helpers (prompt_value/menu/yes_no/db_ident/db_secret/matching/
# admin_password, with_default) live in bin/lib/ui.sh — shared with the
# web wizard.

# m2/ catalog helpers (choose_magento_version, m2_get, detect_magento_version,
# apply_m2_stack_pins, ...) — see bin/lib/m2-catalog.sh.
# shellcheck disable=SC1091
source "$PLATFORM_DIR/bin/lib/m2-catalog.sh"

# repo.magento.com credentials for create-project, written as auth.json JSON
# to $1 (a temp file outside magento/, which must stay empty until
# create-project runs).
collect_fresh_auth() {
    local out="$1" choice src pub priv
    choice="$(prompt_menu 'How will you provide your repo.magento.com access keys? (needed to download Magento)' 2 \
        'Use my existing auth.json file' \
        'Type my public and private keys')"
    case "$choice" in
        1)
            src="$(prompt_file_source 'Where is your auth.json? (file path or URL)' json)"
            case "$src" in
                http://*|https://*) curl -fsSL -o "$out" "$src" || fail "Failed to download auth.json from '$src'." ;;
                *) [ -f "$src" ] || fail "No file found at '$src'."; cp "$src" "$out" ;;
            esac
            grep -q '<public-key>\|<private-key>' "$out" && fail "That auth.json still has placeholder credentials."
            grep -q 'repo.magento.com' "$out" || fail "That auth.json has no repo.magento.com entry."
            ;;
        2)
            pub="$(prompt_value "What's your repo.magento.com public key? (Marketplace → My Profile → Access Keys)" 0)"
            priv="$(prompt_value 'And your private key? (typing is hidden)' 1)"
            [[ "$pub" =~ ^[A-Za-z0-9]+$ ]] && [[ "$priv" =~ ^[A-Za-z0-9]+$ ]] \
                || fail "Access keys should be letters/digits only — check them and re-run."
            printf '{"http-basic":{"repo.magento.com":{"username":"%s","password":"%s"}}}\n' "$pub" "$priv" > "$out"
            ;;
    esac
    return 0
}

ui_step setup 2

# 1 when option 3 below downloads a brand-new Magento via composer
# create-project (no DB import, no S3 data — vanilla).
FRESH_SOURCE=0

if magento_source_valid "$MAGENTO_DIR"; then
    log "✓ Existing Magento source detected"
else
    log "Magento source code"
    SOURCE_CHOICE="$(prompt_menu 'How should the Magento code in magento/ be set up?' 2 \
        'Clone an existing Magento project from GitHub' \
        'Download a new, empty Magento store')"

    case "$SOURCE_CHOICE" in
      1)
        # ---- GitHub clone: ephemeral credentials, never persisted ----
        GITHUB_URL="$(prompt_value 'Which GitHub repository should be cloned? (https:// or git@ URL)' 0)"
        GIT_BRANCH_INPUT="$(prompt_value 'Which branch? [master]' 0)"
        GIT_BRANCH="${GIT_BRANCH_INPUT:-master}"

        if [ -n "$(ls -A "$MAGENTO_DIR" 2>/dev/null)" ]; then
            log "The local Magento directory already contains source code."
            log "Replacing it may overwrite the current Magento application."
            prompt_yes_no 'Delete what is in magento/ now and continue?' N || cancel "Cancelled — no changes made."
            rm -rf "$MAGENTO_DIR" && mkdir -p "$MAGENTO_DIR"
        fi

        ui_task_start "Cloning the Magento repository"
        log "Cloning Magento source ..."
        case "$GITHUB_URL" in
          https://*)
            # A login git already has (e.g. a system credential manager) is
            # used as is; otherwise ask for one, use it for this clone only —
            # and save it for this repo only (magento/.git/credentials, 0600,
            # bin/lib/git-repo.sh) if wanted. Never into the global git config.
            CRED_HOST="$(printf '%s' "$GITHUB_URL" | sed -E 's#^https://([^/@]+@)?([^/]+)/.*#\2#')"
            if printf 'protocol=https\nhost=%s\n\n' "$CRED_HOST" \
                | GIT_TERMINAL_PROMPT=0 GIT_ASKPASS=/bin/false timeout 5 git credential fill >/dev/null 2>&1; then
                log "Using your saved login for $CRED_HOST"
                GIT_TERMINAL_PROMPT=0 git clone --branch "$GIT_BRANCH" "$GITHUB_URL" "$MAGENTO_DIR" \
                    || fail "git clone failed — check the repository URL and branch '$GIT_BRANCH'. If your saved token expired, remove it from your credential manager (printf 'protocol=https\\nhost=$CRED_HOST\\n\\n' | git credential reject) and run setup again."
            else
                GH_USER="$(prompt_value "What's your GitHub username?" 0)"
                GH_TOKEN="$(prompt_value "What's your GitHub personal access token? (typing is hidden)" 1)"
                REMEMBER_LOGIN=0
                prompt_yes_no "Remember these credentials for this repo (saved in magento/.git/credentials, readable only by you; other repos never see them)? The dashboard's Git tab and your terminal can then fetch and push here without asking." N \
                    && REMEMBER_LOGIN=1
                ASKPASS_SCRIPT="$(mktemp)"
                chmod 700 "$ASKPASS_SCRIPT"
                cat > "$ASKPASS_SCRIPT" <<'ASKPASS'
#!/bin/bash
case "$1" in
    Username*) printf '%s' "$GIT_CLONE_USERNAME" ;;
    Password*) printf '%s' "$GIT_CLONE_TOKEN" ;;
esac
ASKPASS
                CLONE_STATUS=0
                GIT_ASKPASS="$ASKPASS_SCRIPT" GIT_CLONE_USERNAME="$GH_USER" GIT_CLONE_TOKEN="$GH_TOKEN" GIT_TERMINAL_PROMPT=0 \
                    git clone --branch "$GIT_BRANCH" "$GITHUB_URL" "$MAGENTO_DIR" || CLONE_STATUS=$?
                rm -f "$ASKPASS_SCRIPT"
                # Commit name/email for this repo, from the GitHub account the token
                # belongs to — so it isn't asked for separately.
                if [ "$CLONE_STATUS" -eq 0 ] && [ "$CRED_HOST" = github.com ]; then
                    IFS=$'\t' read -r GH_ID_NAME GH_ID_EMAIL < <(github_identity "$GH_TOKEN") || true
                fi
                if [ "$CLONE_STATUS" -eq 0 ] && [ "$REMEMBER_LOGIN" = 1 ]; then
                    repo_remember_login "$MAGENTO_DIR" "$CRED_HOST" "$GH_USER" "$GH_TOKEN" \
                        || fail "The clone worked, but the login couldn't be saved for magento/."
                    log "✓ GitHub login saved for this repo only (magento/.git/credentials)"
                fi
                unset GH_TOKEN GIT_CLONE_TOKEN
                [ "$CLONE_STATUS" -eq 0 ] || fail "git clone failed — check the repository URL, branch '$GIT_BRANCH', and credentials."
            fi
            ;;
          *)
            # SSH-style URL (or an already-authenticated https remote) —
            # clone directly, no credential prompt needed.
            git clone --branch "$GIT_BRANCH" "$GITHUB_URL" "$MAGENTO_DIR" \
                || fail "git clone failed — check the repository URL, branch '$GIT_BRANCH', and your SSH access."
            ;;
        esac

        [ -d "$MAGENTO_DIR/.git" ] || fail "Clone finished but $MAGENTO_DIR/.git is missing — something went wrong."
        ui_task_done
        ;;
      2)
        # ---- fresh vanilla Magento via composer create-project ----
        FRESH_SOURCE=1
        if [ -n "$(ls -A "$MAGENTO_DIR" 2>/dev/null)" ]; then
            log "The local Magento directory isn't empty — a fresh install needs it empty."
            log "Replacing it may overwrite the current Magento application."
            prompt_yes_no 'Delete what is in magento/ now and continue?' N || cancel "Cancelled — no changes made."
            rm -rf "$MAGENTO_DIR" && mkdir -p "$MAGENTO_DIR"
        fi

        releases_refresh
        choose_magento_version
        MAGENTO_VERSION="$(m2_get MAGENTO_VERSION)"
        MAGENTO_PACKAGE="$(m2_get MAGENTO_PACKAGE)"
        : "${MAGENTO_PACKAGE:=magento/project-community-edition}"
        [ -n "$MAGENTO_VERSION" ] || fail "No MAGENTO_VERSION in ${M2_PATCH_DIR:-$M2_BASE_DIR}/version.env."
        set_env_var "$ENV_FILE" MAGENTO_VERSION "$MAGENTO_VERSION"
        set_env_var "$ENV_FILE" MAGENTO_PACKAGE "$MAGENTO_PACKAGE"
        log "✓ Will install $MAGENTO_PACKAGE $MAGENTO_VERSION"

        # Stack versions for it are applied in "Stack versions" below,
        # after APP_NAME (which names the data volumes) is settled.

        FRESH_AUTH="$(mktemp)"
        chmod 600 "$FRESH_AUTH"
        trap 'rm -f "$FRESH_AUTH"' EXIT
        collect_fresh_auth "$FRESH_AUTH"
        log "✓ Composer credentials ready (copied into magento/auth.json after download)"
        ;;
    esac

    [ "$FRESH_SOURCE" = 1 ] || magento_source_valid "$MAGENTO_DIR" \
        || fail "No valid Magento source found in $MAGENTO_DIR (missing composer.json, app/, bin/magento, or pub/) — clone it first (see LOCAL_SETUP.md Step 5), or re-run and choose to clone it from GitHub."
    if [ "$FRESH_SOURCE" = 1 ]; then
        log "Magento will be downloaded once the Docker stack is up."
    else
        log "Magento source is ready."
    fi
fi

# magento/'s own commit identity, saved in this repo only: taken from the
# GitHub account used for the clone when there was one, otherwise asked once
# (a fresh download isn't a git repo, so it's skipped there).
repo_ensure_identity "$MAGENTO_DIR" "${GH_ID_NAME:-}" "${GH_ID_EMAIL:-}"
