#!/usr/bin/env bash
# validate-worklog.sh — validates worklog front matter
set -euo pipefail

FILE="${1:-}"
if [ -z "$FILE" ]; then
	echo "Usage: $0 <worklog-file>" >&2
	exit 1
fi

errors=0
for key in when why what model tags; do
	if ! grep -q "^${key}:" "$FILE"; then
		echo "Missing required key: ${key}" >&2
		errors=$((errors + 1))
	fi
done

if [ "$errors" -gt 0 ]; then
	echo "Worklog validation failed." >&2
	exit 1
fi
echo "Worklog OK: $FILE"
