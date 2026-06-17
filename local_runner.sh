#!/usr/bin/env bash
# Local live score runner — pull, update, push to GitHub.
# Designed to be called by launchd every 5 minutes.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
LOG="$REPO/local_runner.log"
PYTHON="$(command -v python3)"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

cd "$REPO"

# Pull latest so we don't push on top of GitHub Actions commits
git pull --rebase origin main >> "$LOG" 2>&1 || {
    log "git pull failed — skipping this run"
    exit 0
}

# Run the live updater
output=$("$PYTHON" live_score_updater.py 2>&1)
echo "$output" >> "$LOG"

# If no live matches, nothing to do
if echo "$output" | grep -q "No live matches"; then
    exit 0
fi

# Commit and push if .ics changed
git add 2026_FIFA_World_Cup.ics state.json
[ -f scores.json ] && git add scores.json || true

if ! git diff --cached --quiet; then
    git config user.name "brewingithot"
    git config user.email "brewingithot@users.noreply.github.com"
    git commit -m "live: score update" >> "$LOG" 2>&1
    for i in 1 2 3; do
        git pull --rebase origin main >> "$LOG" 2>&1 && \
        git push >> "$LOG" 2>&1 && break
        log "Push attempt $i failed, retrying..."
        sleep $((i * 2))
    done
    log "Pushed score update"
fi
