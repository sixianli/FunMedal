CLAUDE_NEEDED=2.1.292
DESKTOP_CLAUDES="$HOME/Library/Application Support/Claude/claude-code"

claude_version() {
    "$1" --version 2>/dev/null | awk 'NR == 1 && match($1, /^[0-9]+\.[0-9]+\.[0-9]+/) { print substr($1, RSTART, RLENGTH) }'
}

version_at_least() {
    awk -v have="$1" -v want="$2" 'BEGIN {
        split(have, h, "."); split(want, w, ".")
        for (i = 1; i <= 3; i++) {
            if (h[i] + 0 > w[i] + 0) exit 0
            if (h[i] + 0 < w[i] + 0) exit 1
        }
        exit 0
    }'
}

claude_side_name() {
    case $1 in
        terminal) printf '终端版' ;;
        desktop) printf 'Claude 桌面 App 自带' ;;
    esac
}

claude_upgrade_hint() {
    case $1 in
        terminal) printf '终端版运行 claude update' ;;
        desktop) printf '桌面 App 在菜单 Claude → Check for Updates 里升级，再开一个新的 Code 会话' ;;
    esac
}

find_claude() {
    TERMINAL_CLAUDE=$(command -v claude 2>/dev/null || true)
    TERMINAL_VERSION=
    if [ -n "$TERMINAL_CLAUDE" ]; then
        TERMINAL_VERSION=$(claude_version "$TERMINAL_CLAUDE")
    fi
    DESKTOP_CLAUDE=
    DESKTOP_VERSION=
    for candidate in "$DESKTOP_CLAUDES"/*/*/claude.app/Contents/MacOS/claude; do
        [ -x "$candidate" ] || continue
        found=$(claude_version "$candidate")
        [ -n "$found" ] || continue
        if [ -z "$DESKTOP_VERSION" ] || ! version_at_least "$DESKTOP_VERSION" "$found"; then
            DESKTOP_CLAUDE=$candidate
            DESKTOP_VERSION=$found
        fi
    done
    CLAUDE= CLAUDE_VERSION= CLAUDE_SIDE= OTHER_VERSION= OTHER_SIDE=
    if [ -n "$TERMINAL_VERSION" ]; then
        CLAUDE=$TERMINAL_CLAUDE CLAUDE_VERSION=$TERMINAL_VERSION CLAUDE_SIDE=terminal
    fi
    if [ -n "$DESKTOP_VERSION" ]; then
        if [ -z "$CLAUDE" ]; then
            CLAUDE=$DESKTOP_CLAUDE CLAUDE_VERSION=$DESKTOP_VERSION CLAUDE_SIDE=desktop
        elif version_at_least "$CLAUDE_VERSION" "$DESKTOP_VERSION"; then
            OTHER_VERSION=$DESKTOP_VERSION OTHER_SIDE=desktop
        else
            OTHER_VERSION=$CLAUDE_VERSION OTHER_SIDE=terminal
            CLAUDE=$DESKTOP_CLAUDE CLAUDE_VERSION=$DESKTOP_VERSION CLAUDE_SIDE=desktop
        fi
    fi
}
