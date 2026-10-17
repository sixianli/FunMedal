#!/bin/sh
set -eu
PLUGIN=funmedal@funmedal
MARKETPLACE=funmedal
BRIDGE_APP="$HOME/Applications/FunMedalBridge.app"

if ! command -v claude >/dev/null 2>&1; then
    echo "uninstall.sh: 没找到 claude 命令，卸不了插件。先确认 Claude Code 装着、claude 命令能用，再运行这个脚本。" >&2
    exit 1
fi
PLUGINS=$(claude plugin list --json)
MARKETPLACES=$(claude plugin marketplace list --json)
if printf '%s\n' "$PLUGINS" | grep -q "\"id\": *\"$PLUGIN\""; then
    claude plugin uninstall "$PLUGIN"
else
    echo "uninstall.sh: 插件 $PLUGIN 没装，跳过。"
fi
if printf '%s\n' "$MARKETPLACES" | grep -q "\"name\": *\"$MARKETPLACE\""; then
    claude plugin marketplace remove "$MARKETPLACE"
else
    echo "uninstall.sh: 插件市场 $MARKETPLACE 不在，跳过。"
fi
cat <<EOF
uninstall.sh: FunMedal 的插件和插件市场都已经不在了。下面三步要你自己做，按这个顺序：
1. 已经开着的 Claude Code 会话还带着插件，要重开，或者在会话里运行 /reload-plugins。不然它们照旧把状态发给桥接程序，会话开始或压缩上下文时还会把桥接程序重新启动起来。
2. 退出桥接程序：
   "$BRIDGE_APP/Contents/MacOS/funmedal-bridge" quit
   之后可以删掉 ${BRIDGE_APP}。
3. 在 Mac 的“系统设置 → 蓝牙”里找到 FunMedal，选“忽略此设备”。以后再装要照 README 第 4 步重新配对。
EOF
