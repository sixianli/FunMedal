#!/bin/sh
set -eu
cd "$(dirname "$0")"
ROOT=$(pwd -P)
NEEDED=2.1.292
BUILT="$ROOT/mac/bridge/build/FunMedalBridge.app"
TARGET="$HOME/Applications/FunMedalBridge.app"

if ! command -v claude >/dev/null 2>&1; then
    echo "install.sh: 没找到 claude 命令。先装好 Claude Code $NEEDED 或更新的版本，再运行这个脚本。" >&2
    exit 1
fi
FOUND=$(claude --version 2>/dev/null | awk 'NR == 1 { print $1 }')
if ! printf '%s\n' "$FOUND" | awk -F. -v needed="$NEEDED" '
    NF != 3 || $1 !~ /^[0-9]+$/ || $2 !~ /^[0-9]+$/ || $3 !~ /^[0-9]+$/ { exit 1 }
    {
        split(needed, want, ".")
        for (i = 1; i <= 3; i++) {
            if ($i + 0 > want[i] + 0) exit 0
            if ($i + 0 < want[i] + 0) exit 1
        }
        exit 0
    }'; then
    echo "install.sh: Claude Code 的版本是 ${FOUND:-（读不出来）}，FunMedal 要 $NEEDED 或更新。先把 Claude Code 升级到最新版（例如运行 claude update），再运行这个脚本。" >&2
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
claude plugin marketplace add "$ROOT"
claude plugin install funmedal@funmedal
echo "install.sh: 装好了。已经开着的 Claude Code 会话要重开，才会用上插件。"
