#!/bin/sh
set -eu
cd "$(dirname "$0")"
APP=build/FunMedalBridge.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp Info.plist "$APP/Contents/Info.plist"
swiftc -swift-version 5 -O Sources/*.swift -o "$APP/Contents/MacOS/funmedal-bridge"
codesign --force --sign - "$APP"
