# open_browser URL — opens URL in the user's browser; silent if none is
# found. Sourced by launch.sh (wizards) and bin/local/dashboard.
#
# WSL: Windows' own URL handler (rundll32 url.dll,FileProtocolHandler) opens
# the default browser. Not explorer.exe: on recent Windows builds it opens a
# File Explorer window instead when the URL has a query string — and every
# wizard/dashboard link carries ?token=…. PowerShell's Start-Process is the
# fallback; explorer.exe only the last resort. Windows tools run from / so
# they don't complain about a WSL (\\wsl$) working directory.
open_browser() {
    local url="$1"
    if grep -qi microsoft /proc/version 2>/dev/null; then
        if command -v wslview >/dev/null 2>&1; then
            wslview "$url" >/dev/null 2>&1 && return
        fi
        if command -v rundll32.exe >/dev/null 2>&1; then
            (cd / && rundll32.exe url.dll,FileProtocolHandler "$url") >/dev/null 2>&1 && return
        fi
        if command -v powershell.exe >/dev/null 2>&1; then
            (cd / && powershell.exe -NoProfile -NonInteractive -Command "Start-Process '${url//\'/\'\'}'") >/dev/null 2>&1 && return
        fi
        if command -v explorer.exe >/dev/null 2>&1; then
            (cd / && explorer.exe "$url") >/dev/null 2>&1 || true
        fi
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$url" >/dev/null 2>&1 || true
    elif command -v open >/dev/null 2>&1; then
        open "$url" >/dev/null 2>&1 || true
    fi
}
