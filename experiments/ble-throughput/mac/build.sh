#!/bin/sh
set -eu
cd "$(dirname "$0")"
APP=build/FunMedalBench.app
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp Info.plist "$APP/Contents/Info.plist"
swiftc -swift-version 5 -O BleBench.swift -o "$APP/Contents/MacOS/blebench"
codesign --force --sign - "$APP"
