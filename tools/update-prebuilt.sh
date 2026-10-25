#!/bin/sh
set -eu
cd "$(dirname "$0")/../firmware"
PREBUILT=$(pwd -P)/prebuilt

command -v idf.py >/dev/null 2>&1 \
    || { echo "update-prebuilt.sh: 没找到 idf.py。先在这个终端运行 ESP-IDF 的 export.sh，见 README“安装”第 2 步。" >&2; exit 1; }
if [ -n "$(git status --porcelain -- . ':(exclude)prebuilt')" ]; then
    echo "update-prebuilt.sh: firmware/ 下有没提交的改动。先提交，编译好的文件才能对上一个确定的提交。" >&2
    exit 1
fi

idf.py -DFUNMEDAL_ASSET_PACK="$(pwd -P)/../assets/default" build
mkdir -p "$PREBUILT"
rm -f "$PREBUILT"/*.bin
{
    head -n 1 build/flash_args
    tail -n +2 build/flash_args | while read -r offset path; do
        cp "build/$path" "$PREBUILT/$(basename "$path")"
        echo "$offset $(basename "$path")"
    done
} > "$PREBUILT/flash_args"
(cd "$PREBUILT" && shasum -a 256 *.bin > SHA256SUMS)
git rev-parse HEAD > "$PREBUILT/SOURCE"
echo "update-prebuilt.sh: firmware/prebuilt 已更新，来自提交 $(cat "$PREBUILT/SOURCE")。"
