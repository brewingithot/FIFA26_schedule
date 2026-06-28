# 2026 FIFA World Cup — Calendar Feed

**Live updates on your calendar event so you don't miss a thing!**

A subscribable iCalendar (`.ics`) feed for all 76 matches of the 2026 FIFA World Cup, hosted as a static file on GitHub Pages. Each match is a 15-minute event at kickoff with an alert that fires at the start of the match.

**Live scores:** During each match, the event title updates automatically with the live score and match minute — no action needed from subscribers. If your calendar app is set to refresh every 15 minutes, you'll see the score update within 5 minutes of it changing.

Example title progression during a match:
```
Argentina vs France (Quarter-final)          ← before kickoff
Argentina vs France 23' (1:0) (Quarter-final) ← in progress
Argentina vs France HT (1:0) (Quarter-final)  ← half time
Argentina vs France 67' (1:2) (Quarter-final) ← second half
Argentina vs France FT (3:3) (Quarter-final)  ← full time
```

**Subscribe URLs:**

With live scores (updates during matches):
```
https://brewingithot.github.io/FIFA26_schedule/2026_FIFA_World_Cup.ics
```

No scores — clean match titles only (spoiler-free):
```
https://brewingithot.github.io/FIFA26_schedule/2026_FIFA_World_Cup_no_live_scores.ics
```

Times are in `America/Los_Angeles` and your calendar app will auto-convert to your local timezone.

As bracket TBDs are resolved during the tournament (e.g., `TBD vs TBD` → `USA vs Brazil`), the existing event's title updates in place. You don't need to re-subscribe — your calendar app pulls the latest version on its own schedule.

<img width="2554" height="1386" alt="image" src="https://github.com/user-attachments/assets/3bcaf888-1406-429a-bf46-ba09745e3487" />

---

## For subscribers — how to add this to your calendar

You only need to do this once. Your calendar app polls the URL automatically and applies updates as the bracket fills in.

> **Important:** use the **Subscribe** flow (sometimes called "New Calendar Subscription" or "From URL"). Don't use **File → Import** — importing takes a one-shot snapshot and won't pick up updates.

### Apple Calendar (macOS)

Click this link from any browser, Mail, or Messages:

With live scores:
```
webcal://brewingithot.github.io/FIFA26_schedule/2026_FIFA_World_Cup.ics
```
No scores (spoiler-free):
```
webcal://brewingithot.github.io/FIFA26_schedule/2026_FIFA_World_Cup_no_live_scores.ics
```

…or in Calendar.app: **File → New Calendar Subscription…** and paste the HTTPS URL above. Set **Auto-refresh** to "Every 5 minutes" to get live score updates during matches.

### iPhone / iPad

Tap the `webcal://` link in Mail, Messages, or Safari — iOS will offer a **Subscribe** button.

With live scores:
```
webcal://brewingithot.github.io/FIFA26_schedule/2026_FIFA_World_Cup.ics
```
No scores (spoiler-free):
```
webcal://brewingithot.github.io/FIFA26_schedule/2026_FIFA_World_Cup_no_live_scores.ics
```

Manual path: **Settings → Calendar → Accounts → Add Account → Other → Add Subscribed Calendar** → paste the HTTPS URL.

> **Note:** iOS does not allow setting a custom refresh interval for subscribed calendars. However, if you use the same iCloud account on a Mac with Apple Calendar set to refresh every 5 minutes, updates sync to your iPhone via iCloud — so you still get near real-time score updates without needing to configure anything on iOS.

### Google Calendar

Left sidebar → next to **Other calendars** → **+** → **From URL** → paste one of the HTTPS URLs below → **Add calendar**.

With live scores: `https://brewingithot.github.io/FIFA26_schedule/2026_FIFA_World_Cup.ics`
No scores: `https://brewingithot.github.io/FIFA26_schedule/2026_FIFA_World_Cup_no_live_scores.ics`

Google polls every 12–24 hours, so updates can take up to a day to appear there.

### Outlook (web / Microsoft 365)

**Add calendar → Subscribe from web** → paste one of the HTTPS URLs below → give it a name → **Import**.

With live scores: `https://brewingithot.github.io/FIFA26_schedule/2026_FIFA_World_Cup.ics`
No scores: `https://brewingithot.github.io/FIFA26_schedule/2026_FIFA_World_Cup_no_live_scores.ics`

### Removing the calendar later

Right-click the calendar in your sidebar and choose **Unsubscribe** (Apple Calendar) or **Remove** (Google / Outlook). All 76 events disappear cleanly.

---

## For maintainers — running the update script locally

This section is for whoever maintains this feed (or anyone forking the repo for a different tournament). Subscribers don't need any of this.

### Prerequisites

- Python 3.10 or newer
- `pip3 install openpyxl requests`
- `gh` CLI authenticated (`gh auth login`)
- football-data.org API token stored in Keychain (see Setup below)

### Fresh machine setup

```bash
git clone https://github.com/brewingithot/FIFA26_schedule.git
cd FIFA26_schedule
./setup.sh   # installs deps, configures git, stores API token, installs launchd daemon
```

### Two write paths to the .ics

| Path | When to use |
|---|---|
| **Manual** — edit xlsx → `./update.sh` → push | Schedule changes, TBD→real teams, backfilling past scores |
| **Live daemon** — `daemon.py` running locally | Automatic live scores during active matches |
| **GitHub Actions** — `workflow_dispatch` | Live scores when away from your laptop |

### Manual update workflow

```bash
# 1. Sync schedule from APIs (fixes times, fills scores, rebuilds knockout rows)
python3 update_xlsx.py

# 2. Run the timing gate — must PASS before committing
python3 test_schedule.py

# 3. Regenerate the .ics
./update.sh

# 4. Review the diff
git diff 2026_FIFA_World_Cup.ics

# 5. Commit and push
git add 2026_FIFA_World_Cup.ics state.json scores.json 2026_FIFA_World_Cup_Schedule.xlsx
git commit -m "Describe what changed"
git push
```

### `test_schedule.py` — schedule timing gate

Verifies that every game's date and time in the xlsx matches the authoritative API schedules:
- **Knockout rounds** — cross-checked against ESPN
- **Group stage** — cross-checked against football-data.org

Run it after `update_xlsx.py` and before pushing. Exits `0` on success, `1` if any mismatch is found.

```bash
python3 test_schedule.py
# PASS — all xlsx timings match API schedules.
# or
# FAIL — 2 timing mismatch(es): ...
```

If it fails, re-run `python3 update_xlsx.py` to auto-correct, then test again.

### Live daemon (local macOS)

`daemon.py` runs continuously, polling every 2 min during live matches and 5 min otherwise. Managed by launchd — starts on login, restarts on crash.

```bash
# Start
launchctl start com.fifa2026.livescores

# Stop
launchctl stop com.fifa2026.livescores

# Watch logs
tail -f daemon.log
```

### GitHub Actions (manual trigger)

The cron is disabled by default. Trigger manually when away from your laptop:

```bash
gh workflow run live_scores.yml --repo brewingithot/FIFA26_schedule
```

Or via GitHub.com → Actions → Live Score Updater → Run workflow.

Requires `FOOTBALL_DATA_TOKEN` secret set in repo Settings → Secrets → Actions.

### What `update.sh` does under the hood

1. Scrubs author/org metadata from the xlsx (Excel writes your name into the file on save)
2. Runs `generate_ics.py`, which reads `2026_FIFA_World_Cup_Schedule.xlsx` and writes `2026_FIFA_World_Cup.ics`
3. Reads col G ("Score") from the xlsx — if a score is present, writes it to `scores.json` and bakes `FT (2:1)` into the event title
4. Tracks each event's content hash in `state.json`. Only changed events get a bumped `SEQUENCE` number — unchanged events stay byte-identical across runs

### About `state.json`

`state.json` is the source of truth for SEQUENCE numbers. **Always commit it alongside the `.ics`.** If it gets lost, the next regeneration resets all SEQUENCE counts to `0`, which can cause calendar clients to ignore future updates.

### File structure

| File | Purpose |
|---|---|
| `2026_FIFA_World_Cup_Schedule.xlsx` | Source data. Col G ("Score") = final score input, e.g. `2:1` |
| `generate_ics.py` | Reads xlsx, writes .ics, syncs col G scores → scores.json |
| `update.sh` | Scrubs xlsx metadata then runs generate_ics.py |
| `2026_FIFA_World_Cup.ics` | The artifact subscribers pull — includes live scores |
| `2026_FIFA_World_Cup_no_live_scores.ics` | Spoiler-free version — clean match titles only, no scores ever |
| `state.json` | Per-event hash + SEQUENCE tracking (scores version) |
| `state_no_scores.json` | Per-event hash + SEQUENCE tracking (no-scores version) |
| `scores.json` | Final scores keyed by event UID — single source of truth |
| `live_score_updater.py` | Fetches live scores (ESPN primary, football-data.org fallback), patches .ics |
| `daemon.py` | Long-running local daemon — polls every 2/5 min, handles git push |
| `update_xlsx.py` | Syncs schedule + scores from ESPN and football-data.org into the xlsx |
| `test_schedule.py` | Timing gate — verifies xlsx dates/times match APIs; run before pushing |
| `setup.sh` | One-shot setup for a fresh machine |
| `.github/workflows/live_scores.yml` | GitHub Actions — manual trigger only (cron disabled) |

---

## Live score updates — how it works

`live_score_updater.py` is the core scoring engine, used by both the local daemon and GitHub Actions.

**Source priority:**
1. **ESPN** — used first; has live match minute data; covers featured matches
2. **football-data.org** — fallback for matches ESPN doesn't cover (requires `FOOTBALL_DATA_TOKEN`)

**Live window:** `kickoff ≤ now ≤ kickoff + 130 min` (covers 90 min + 30 min ET + penalties buffer)

**Score format:** `Argentina vs France 67' (1:2) (Quarter-final)`
Halftime shows `HT`, full time shows `FT`.

**Subscribers don't need to do anything.** The `.ics` URL stays the same. As long as Auto-refresh is enabled in their calendar app, updates arrive automatically:

| App | Refresh frequency |
|---|---|
| Apple Calendar | Every 15 minutes (set in subscription settings) |
| iPhone / iPad | Every 15 minutes (same setting synced via iCloud) |
| Google Calendar | Every 12–24 hours (Google's fixed polling interval) |
| Outlook | Every few hours (varies by client) |
