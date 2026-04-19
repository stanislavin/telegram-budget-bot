#!/usr/bin/env bash
# Build the Expense Tracker debug APK and copy it to a stable path the bot serves.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
ANDROID_DIR="$REPO_ROOT/android"
OUT_APK="$ANDROID_DIR/expense-tracker.apk"

if [[ -z "${JAVA_HOME:-}" ]]; then
    if [[ -x "/opt/homebrew/opt/openjdk@17/bin/java" ]]; then
        export JAVA_HOME="/opt/homebrew/opt/openjdk@17"
    elif [[ -x "/usr/libexec/java_home" ]]; then
        export JAVA_HOME="$(/usr/libexec/java_home -v 17 2>/dev/null || /usr/libexec/java_home)"
    fi
fi

if [[ -n "${JAVA_HOME:-}" ]]; then
    export PATH="$JAVA_HOME/bin:$PATH"
fi

if [[ -z "${ANDROID_SDK_ROOT:-}" && -d "$HOME/Library/Android/sdk" ]]; then
    export ANDROID_SDK_ROOT="$HOME/Library/Android/sdk"
fi

cd "$ANDROID_DIR"
./gradlew --no-daemon assembleDebug

BUILT_APK="$ANDROID_DIR/app/build/outputs/apk/debug/app-debug.apk"
if [[ ! -f "$BUILT_APK" ]]; then
    echo "Build succeeded but APK not found at $BUILT_APK" >&2
    exit 1
fi

cp "$BUILT_APK" "$OUT_APK"
echo "APK ready: $OUT_APK"
