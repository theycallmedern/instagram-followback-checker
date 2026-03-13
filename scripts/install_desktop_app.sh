#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="Instagram Followback.app"
SOURCE_APP="$ROOT_DIR/src-tauri/target/release/bundle/macos/$APP_NAME"
TARGET_APP="/Applications/$APP_NAME"

if [[ ! -d "$SOURCE_APP" ]]; then
  echo "Release app bundle not found: $SOURCE_APP" >&2
  echo "Run npm run desktop:build-app first." >&2
  exit 1
fi

rm -rf "$TARGET_APP"
cp -R "$SOURCE_APP" "/Applications/"
echo "Installed to $TARGET_APP"
