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

**Subscribe URL:**

```
https://brewingithot.github.io/FIFA26_schedule/2026_FIFA_World_Cup.ics
```

Times are in `America/Los_Angeles` and your calendar app will auto-convert to your local timezone.

As bracket TBDs are resolved during the tournament (e.g., `TBD vs TBD` → `USA vs Brazil`), the existing event's title updates in place. You don't need to re-subscribe — your calendar app pulls the latest version on its own schedule.

<img width="3016" height="1740" alt="image" src="https://github.com/user-attachments/assets/25c25aca-5fd0-4dbb-96f8-3c9cc5f10dfb" />

---

## For subscribers — how to add this to your calendar

You only need to do this once. Your calendar app polls the URL automatically and applies updates as the bracket fills in.

> **Important:** use the **Subscribe** flow (sometimes called "New Calendar Subscription" or "From URL"). Don't use **File → Import** — importing takes a one-shot snapshot and won't pick up updates.

### Apple Calendar (macOS)

Click this link from any browser, Mail, or Messages:

```
webcal://brewingithot.github.io/FIFA26_schedule/2026_FIFA_World_Cup.ics
```

…or in Calendar.app: **File → New Calendar Subscription…** and paste the HTTPS URL above. Set **Auto-refresh** to "Every 15 minutes" to get live score updates during matches.

### iPhone / iPad

Tap the `webcal://` link in Mail, Messages, or Safari — iOS will offer a **Subscribe** button.

Manual path: **Settings → Calendar → Accounts → Add Account → Other → Add Subscribed Calendar** → paste the HTTPS URL.

### Google Calendar

Left sidebar → next to **Other calendars** → **+** → **From URL** → paste the HTTPS URL → **Add calendar**.

Google polls every 12–24 hours, so updates can take up to a day to appear there.

### Outlook (web / Microsoft 365)

**Add calendar → Subscribe from web** → paste the HTTPS URL → give it a name → **Import**.

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
# 1. Open the spreadsheet and make your changes
open 2026_FIFA_World_Cup_Schedule.xlsx

# Fill col G ("Score") with final scores, e.g. 2:1
# Format: H:A (home goals : away goals)

# 2. Regenerate the .ics
./update.sh

# 3. Review the diff
git diff 2026_FIFA_World_Cup.ics

# 4. Commit and push
git add 2026_FIFA_World_Cup.ics state.json scores.json 2026_FIFA_World_Cup_Schedule.xlsx
git commit -m "Describe what changed"
git push
```

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
| `2026_FIFA_World_Cup.ics` | The artifact subscribers pull |
| `state.json` | Per-event hash + SEQUENCE tracking |
| `scores.json` | Final scores keyed by event UID — single source of truth |
| `live_score_updater.py` | Fetches live scores (ESPN primary, football-data.org fallback), patches .ics |
| `daemon.py` | Long-running local daemon — polls every 2/5 min, handles git push |
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
