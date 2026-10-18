#!/bin/sh
set -eu
cd "$(dirname "$0")"
ROOT=$(pwd -P)
BUILT="$ROOT/mac/bridge/build/FunMedalBridge.app"
TARGET="$HOME/Applications/FunMedalBridge.app"
. "$ROOT/tools/find-claude.sh"

find_claude
if [ -z "$CLAUDE" ]; then
    echo "install.sh: 没找到能读出版本号的 Claude Code。FunMedal 要 Claude Code $CLAUDE_NEEDED 或更新的版本，下面两种有一种就行：只用 Claude 桌面 App 时，打开它，在 Code 页开一个会话（桌面 App 会先准备好它自带的 Claude Code），再运行这个脚本；用终端版时，先装好终端版 Claude Code，确认终端里能运行 claude。" >&2
    exit 1
fi
if ! version_at_least "$CLAUDE_VERSION" "$CLAUDE_NEEDED"; then
    HINTS=
    [ -z "$TERMINAL_VERSION" ] || HINTS="$(claude_upgrade_hint terminal)"
    [ -z "$DESKTOP_VERSION" ] || HINTS="${HINTS:+${HINTS}；}$(claude_upgrade_hint desktop)"
    echo "install.sh: 找到的 Claude Code 里最新的是 $(claude_side_name "$CLAUDE_SIDE")的 ${CLAUDE_VERSION}，FunMedal 要 $CLAUDE_NEEDED 或更新。先升级（${HINTS}），再运行这个脚本。" >&2
    exit 1
fi
if [ ! -x "$BUILT/Contents/MacOS/funmedal-bridge" ]; then
    echo "install.sh: 桥接程序还没编译。先运行 sh mac/bridge/build.sh，再运行这个脚本。" >&2
    exit 1
fi
mkdir -p "$HOME/Applications"
rm -rf "$TARGET"
ditto "$BUILT" "$TARGET"
echo "install.sh: 桥接程序已复制到 $TARGET"
"$CLAUDE" plugin marketplace add "$ROOT"
"$CLAUDE" plugin install funmedal@funmedal
echo "install.sh: 装好了。已经开着的 Claude Code 会话要重开，才会用上插件。"
if [ -n "$OTHER_VERSION" ] && ! version_at_least "$OTHER_VERSION" "$CLAUDE_NEEDED"; then
    echo "install.sh: 注意：$(claude_side_name "$OTHER_SIDE")的 Claude Code 是 ${OTHER_VERSION}，低于 ${CLAUDE_NEEDED}，用它开的会话里 FunMedal 不能正常工作。要用它的话先升级：$(claude_upgrade_hint "$OTHER_SIDE")。"
fi
