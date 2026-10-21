#!/usr/bin/env bash
# bump-version.sh — bumps VERSION file
# Usage: bump-version.sh [patch|minor|major]
set -euo pipefail

LEVEL="${1:-patch}"
VERSION_FILE="$(dirname "$0")/../VERSION"

if [ ! -f "$VERSION_FILE" ]; then
	echo "0.0.0" >"$VERSION_FILE"
fi

IFS='.' read -r major minor patch <"$VERSION_FILE"

case "$LEVEL" in
major)
	major=$((major + 1))
	minor=0
	patch=0
	;;
minor)
	minor=$((minor + 1))
	patch=0
	;;
patch) patch=$((patch + 1)) ;;
*)
	echo "Usage: $0 [patch|minor|major]" >&2
	exit 1
	;;
esac

NEW_VERSION="${major}.${minor}.${patch}"
echo "$NEW_VERSION" >"$VERSION_FILE"

# Also update hardcoded VERSION in the main script
SCRIPT_DIR="$(dirname "$0")/.."
sed -i "s/^VERSION = .*/VERSION = \"${NEW_VERSION}\"/" "$SCRIPT_DIR/postfix-messageanalyzer.py"

echo "Bumped to v${NEW_VERSION}"
