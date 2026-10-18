#!/bin/sh
set -eu
cd "$(dirname "$0")"
ROOT=$(pwd -P)
PREBUILT="$ROOT/firmware/prebuilt"
BACKUPS="firmware-backup"
NEW_BACKUP="$BACKUPS/factory-full8MB.bin"
FLASH_BYTES=8388608

fail() {
    echo "flash.sh: $1" >&2
    exit 1
}

if [ $# -ne 1 ]; then
    echo "用法：sh flash.sh <端口>，端口是卡片用 USB 线连上 Mac 后出现的串口，用 ls /dev/cu.usbmodem* 能看到。" >&2
    exit 64
fi
PORT=$1

ESPTOOL=
for candidate in esptool esptool.py "$ROOT/.venv/bin/esptool"; do
    if command -v "$candidate" >/dev/null 2>&1; then
        ESPTOOL=$candidate
        break
    fi
done
[ -n "$ESPTOOL" ] || fail "没找到 esptool。用 brew install esptool 装上，或者在仓库目录运行 python3 -m venv .venv && .venv/bin/pip install esptool，再运行这个脚本。"

(cd "$PREBUILT" && shasum -a 256 -c SHA256SUMS >/dev/null 2>&1) \
    || fail "firmware/prebuilt 里编译好的固件文件不全或被改过，没有动卡片。用 git checkout -- firmware/prebuilt 恢复后再运行。"

size_of() {
    wc -c < "$1" | tr -d ' '
}

kept=0
for image in "$BACKUPS"/*.bin; do
    [ -e "$image" ] || continue
    kept=1
    [ -f "$image.sha256" ] || fail "备份 $image 旁边没有 $image.sha256，核对不了，没有写卡片。"
    [ "$(size_of "$image")" = "$FLASH_BYTES" ] || fail "备份 $image 不是整块 8 MB，没有写卡片。"
    expected=$(awk 'NR == 1 { print $1 }' "$image.sha256")
    actual=$(shasum -a 256 "$image" | awk '{ print $1 }')
    [ "$expected" = "$actual" ] || fail "备份 $image 和 $image.sha256 里记的 SHA-256 对不上，备份可能坏了，没有写卡片。"
    echo "flash.sh: 备份 $image 核对无误。"
done

if [ "$kept" = 0 ]; then
    mkdir -p "$BACKUPS"
    first="$NEW_BACKUP.read1"
    second="$NEW_BACKUP.read2"
    rm -f "$first" "$second"
    for copy in "$first" "$second"; do
        echo "flash.sh: 把卡片整块 8 MB Flash 读进 ${copy}，要一两分钟。"
        if ! "$ESPTOOL" --chip esp32c3 -p "$PORT" read_flash 0 0x800000 "$copy"; then
            rm -f "$first" "$second"
            fail "读卡片失败，没有写卡片。卡片用原厂固件时闲置 5 分钟会睡着，按一下右侧任意一个键唤醒，再运行这个脚本。"
        fi
    done
    if [ "$(size_of "$first")" != "$FLASH_BYTES" ] || [ "$(size_of "$second")" != "$FLASH_BYTES" ] \
        || ! cmp -s "$first" "$second"; then
        rm -f "$first" "$second"
        fail "两次读出的内容不一样或不完整，没有留下备份，也没有写卡片。检查 USB 线后再运行这个脚本。"
    fi
    mv "$first" "$NEW_BACKUP"
    rm -f "$second"
    shasum -a 256 "$NEW_BACKUP" > "$NEW_BACKUP.sha256"
    echo "flash.sh: 备份存在 ${NEW_BACKUP}，两次读出的内容一致。里面有这张卡片独有的身份和配对数据，不要上传到任何地方。"
fi

echo "flash.sh: 写入 firmware/prebuilt 里编译好的固件。"
(cd "$PREBUILT" && "$ESPTOOL" --chip esp32c3 -p "$PORT" write_flash $(cat flash_args)) \
    || fail "写入失败。卡片上的程序可能不完整，再运行一次这个脚本；还不行就照 README“恢复原厂固件”写回备份。"
echo "flash.sh: 写好了，卡片会自己重启，屏幕上出现像素小人。"
