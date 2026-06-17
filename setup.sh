#!/usr/bin/env bash
# Run once on a fresh machine after cloning the repo.
# Sets up Python dependencies, git config, GitHub auth, and the launchd agent.
set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$(command -v python3)"
PLIST_LABEL="com.fifa2026.livescores"
PLIST_DST="$HOME/Library/LaunchAgents/$PLIST_LABEL.plist"

echo "=== FIFA 2026 Live Score — Setup ==="
echo "Repo: $REPO"
echo "Python: $PYTHON ($($PYTHON --version))"
echo

# 1. Python dependencies
echo "--- Installing Python dependencies ---"
"$PYTHON" -m pip install --quiet openpyxl requests
echo "Done."
echo

# 2. Git identity (local to this repo)
echo "--- Configuring git identity ---"
git -C "$REPO" config user.name  "brewingithot"
git -C "$REPO" config user.email "brewingithot@users.noreply.github.com"
git -C "$REPO" config commit.gpgsign false
echo "Done."
echo

# 3. Clean remote URL (remove embedded token if present)
CURRENT_URL=$(git -C "$REPO" remote get-url origin)
CLEAN_URL="https://github.com/brewingithot/FIFA26_schedule.git"
if [ "$CURRENT_URL" != "$CLEAN_URL" ]; then
    echo "--- Fixing remote URL (removing embedded token) ---"
    git -C "$REPO" remote set-url origin "$CLEAN_URL"
    echo "Remote set to: $CLEAN_URL"
else
    echo "--- Remote URL already clean ---"
fi
echo

# 4. GitHub auth check
echo "--- Checking GitHub auth ---"
if command -v gh &>/dev/null; then
    if gh auth status &>/dev/null; then
        echo "gh CLI authenticated."
    else
        echo "gh CLI not authenticated. Running 'gh auth login'..."
        gh auth login
    fi
else
    echo "WARNING: gh CLI not found. Install it with: brew install gh"
    echo "Then run: gh auth login"
    echo "Push/pull will prompt for credentials without it."
fi
echo

# 5. football-data.org API token
echo "--- football-data.org API token ---"
EXISTING_TOKEN=$(security find-generic-password -a "fifa2026" -s "football-data-api" -w 2>/dev/null || true)
if [ -n "$EXISTING_TOKEN" ]; then
    echo "Token already in Keychain — skipping."
else
    echo "Enter your football-data.org API token:"
    read -rs FD_TOKEN
    echo
    if [ -n "$FD_TOKEN" ]; then
        security add-generic-password -a "fifa2026" -s "football-data-api" -w "$FD_TOKEN"
        echo "Token saved to Keychain."
    else
        echo "Skipped — set FOOTBALL_DATA_TOKEN env var or re-run setup."
    fi
fi
echo

# 6. Generate and install launchd plist with correct paths
echo "--- Installing launchd agent ---"
cat > "$PLIST_DST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$PLIST_LABEL</string>

    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON</string>
        <string>$REPO/daemon.py</string>
    </array>

    <key>KeepAlive</key>
    <true/>

    <key>StandardOutPath</key>
    <string>$REPO/daemon.log</string>

    <key>StandardErrorPath</key>
    <string>$REPO/daemon.log</string>

    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
PLIST

launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"
echo "launchd agent installed: $PLIST_LABEL"
echo

echo "=== Setup complete ==="
echo
echo "Useful commands:"
echo "  Stop daemon:   launchctl stop $PLIST_LABEL"
echo "  Start daemon:  launchctl start $PLIST_LABEL"
echo "  Uninstall:     launchctl unload $PLIST_DST && rm $PLIST_DST"
echo "  Watch logs:    tail -f $REPO/daemon.log"
