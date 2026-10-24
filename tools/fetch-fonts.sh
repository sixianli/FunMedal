#!/bin/sh
set -eu
cd "$(dirname "$0")/.."

fail() {
    echo "fetch-fonts.sh: $1" >&2
    exit 1
}

command -v curl >/dev/null 2>&1 || fail "没找到 curl。"

matches() {
    [ -f "$1" ] && [ "$(shasum -a 256 "$1" | awk '{ print $1 }')" = "$2" ]
}

fetch() {
    dest=$1
    sha=$2
    url=$3
    member=${4:-}
    if matches "$dest" "$sha"; then
        echo "fetch-fonts.sh: $dest 已经有了，SHA-256 对得上。"
        return
    fi
    work=$(mktemp -d)
    echo "fetch-fonts.sh: 下载 $url"
    if ! curl -sSfL -o "$work/download" "$url"; then
        rm -rf "$work"
        fail "下载 $url 失败，检查网络后再运行。"
    fi
    got="$work/download"
    if [ -n "$member" ]; then
        if ! unzip -q -o "$got" "$member" -d "$work/unzipped"; then
            rm -rf "$work"
            fail "解不开 $url，里面没有 $member。"
        fi
        got="$work/unzipped/$member"
    fi
    if ! matches "$got" "$sha"; then
        rm -rf "$work"
        fail "$url 下载下来的 $(basename "$dest") 和记下的 SHA-256 $sha 对不上，没有放进 $(dirname "$dest")。"
    fi
    mv "$got" "$dest"
    rm -rf "$work"
    echo "fetch-fonts.sh: 放好了 $dest。"
}

fetch third_party/noto-sans-sc/NotoSansSC-Regular.otf \
    faa6c9df652116dde789d351359f3d7e5d2285a2b2a1f04a2d7244df706d5ea9 \
    https://github.com/notofonts/noto-cjk/raw/main/Sans/SubsetOTF/SC/NotoSansSC-Regular.otf
fetch third_party/fusion-pixel/fusion-pixel-12px-proportional-zh_hans.bdf \
    9d69fe7a51e928dbb0689a70bed8eec28666713d8c57410fba0f0aa69c0005c6 \
    https://github.com/TakWolf/fusion-pixel-font/releases/download/2026.09.25/fusion-pixel-font-12px-proportional-bdf-v2026.09.25.zip \
    fusion-pixel-12px-proportional-zh_hans.bdf
fetch third_party/zcool-kuaile/ZCOOLKuaiLe-Regular.ttf \
    812a6fc1fe54b6d73a419245c32dfeba8aa33104d5be90d1cf6af082007cb71d \
    https://raw.githubusercontent.com/google/fonts/a606d6f3181c62b792c8c1463d309c6a8abb8a12/ofl/zcoolkuaile/ZCOOLKuaiLe-Regular.ttf
