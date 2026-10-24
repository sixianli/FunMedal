#!/bin/sh
set -eu
cd "$(dirname "$0")"
APP=build/FunMedalBridge.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp Info.plist "$APP/Contents/Info.plist"
swiftc -swift-version 5 -O Sources/*.swift -o "$APP/Contents/MacOS/funmedal-bridge"
IDENTITY=$(security find-identity -v -p codesigning | awk '/"Apple Development: / { print $2; exit }')
if [ -z "$IDENTITY" ]; then
    echo "build.sh: 钥匙串里没有 Apple Development 证书，改用临时签名。这样每次重新编译后，macOS 都会再问一次蓝牙授权；照 README“安装”的第 3 步拿一张证书后再编译，就不会了。" >&2
    IDENTITY=-
fi
codesign --force --sign "$IDENTITY" "$APP"
