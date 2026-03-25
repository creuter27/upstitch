#!/bin/bash
# daily_backup.sh — commit and push any uncommitted changes.
# Run by launchd daily; also callable manually.

REPO="/Users/cr/Tresorit/Upstitch/code"
GIT="/usr/bin/git"
LOG_TAG="[upstitch-daily-backup]"

cd "$REPO" || { echo "$LOG_TAG ERROR: cannot cd to $REPO"; exit 1; }

# Stage everything (new files, modifications, deletions)
$GIT add -A

# Check if there is anything to commit
if $GIT diff --cached --quiet; then
    echo "$LOG_TAG $(date '+%Y-%m-%d %H:%M') — no changes, skipping."
    exit 0
fi

DATE=$(date '+%Y-%m-%d')
$GIT commit -m "Daily backup $DATE"

if $GIT push; then
    echo "$LOG_TAG $(date '+%Y-%m-%d %H:%M') — committed and pushed (backup $DATE)."
else
    echo "$LOG_TAG $(date '+%Y-%m-%d %H:%M') — commit OK but push failed."
    exit 1
fi
