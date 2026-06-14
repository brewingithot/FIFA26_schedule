#!/usr/bin/env bash
# One-command refresh: regenerate the .ics, then (if a remote is configured)
# push it to your hosting. Edit the PUSH_CMD line below to match your setup.
set -euo pipefail
cd "$(dirname "$0")"

python3 generate_ics.py

# === Hosting hook (uncomment and edit ONE of these for your setup) ===
# GitHub Gist (via gh CLI):
#   gh gist edit <YOUR_GIST_ID> 2026_FIFA_World_Cup.ics
# GitHub Pages / repo push:
#   git add 2026_FIFA_World_Cup.ics state.json && git commit -m "update schedule" && git push
# scp to your server:
#   scp 2026_FIFA_World_Cup.ics user@host:/var/www/calendars/
# rsync:
#   rsync -av 2026_FIFA_World_Cup.ics user@host:/var/www/calendars/

echo
echo "Done. Output: 2026_FIFA_World_Cup.ics"
echo "Upload this file to your host. The URL stays the same; subscribers will pull updates on their next poll."
