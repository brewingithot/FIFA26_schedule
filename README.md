# 2026 FIFA World Cup — Calendar Feed

A subscribable iCalendar (`.ics`) feed for all 76 matches of the 2026 FIFA World Cup, hosted as a static file on GitHub Pages. Each match is a 15-minute event at kickoff with an alert that fires at the start of the match.

**Live scores:** During each match, the event title updates automatically with the live score and match minute — no action needed from subscribers. If your calendar app is set to refresh every 15 minutes, you'll see the score update within 15 minutes of it changing.

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
- `openpyxl` — install with `pip3 install openpyxl`
- Git, configured to push to your fork

### Workflow

```bash
git clone https://github.com/brewingithot/FIFA26_schedule.git
cd FIFA26_schedule

# 1. Edit the source spreadsheet
open 2026_FIFA_World_Cup_Schedule.xlsx

# 2. Regenerate the .ics file
./update.sh

# 3. Inspect the diff to make sure only the events you intended changed
git diff 2026_FIFA_World_Cup.ics

# 4. Commit and push — subscribers' calendars pull on their next refresh
git add 2026_FIFA_World_Cup.ics state.json 2026_FIFA_World_Cup_Schedule.xlsx
git commit -m "Describe what changed"
git push
```

### What `update.sh` does under the hood

1. Runs `generate_ics.py`, which reads `2026_FIFA_World_Cup_Schedule.xlsx` and writes `2026_FIFA_World_Cup.ics`.
2. Maintains stable per-event UIDs derived from `(stage, kickoff time, stadium)` — *not* the team names. That way, when a `TBD vs TBD` knockout entry gets real teams, the UID stays the same and subscribers' calendars treat it as an **update** to the existing event, not a brand-new event.
3. Tracks each event's content hash in `state.json`. Only events whose summary/location/time actually changed get a bumped `SEQUENCE` number and a fresh `LAST-MODIFIED` timestamp. Unchanged events stay byte-identical across runs, so subscribers don't see spurious update notifications.

The script prints a summary on each run, e.g.:

```
Wrote 76 events to 2026_FIFA_World_Cup.ics (+0 added, 1 changed, 75 unchanged, 0 removed)
```

### About `state.json`

`state.json` is the source of truth for SEQUENCE numbers. **Always commit it alongside the `.ics`.** If it gets lost or deleted, the next regeneration treats every event as new and resets all SEQUENCE counts to `0`, which can cause some calendar clients to ignore future updates because they think they already have a "newer" copy.

### Generated file structure

| File | Purpose |
|---|---|
| `2026_FIFA_World_Cup_Schedule.xlsx` | Source data — the only file you edit |
| `generate_ics.py` | Reads the xlsx, writes the .ics, updates state.json |
| `update.sh` | Wrapper that runs the generator |
| `2026_FIFA_World_Cup.ics` | The artifact subscribers pull |
| `state.json` | Per-event hash + SEQUENCE tracking |
| `live_score_updater.py` | Patches the .ics with live scores during matches |
| `.github/workflows/live_scores.yml` | Runs the updater every 5 minutes via GitHub Actions |
| `scores.json` | Permanent record of final scores; used by generate_ics.py to keep FT scores in titles |

---

## Live score updates — how it works

A GitHub Actions workflow runs `live_score_updater.py` every 5 minutes automatically. No server or manual intervention is needed.

**What it does:**
1. Checks if any match is currently in progress (`kickoff ≤ now ≤ kickoff + 100 min`)
2. If yes, fetches the live score from ESPN's scoreboard API
3. Patches the event's SUMMARY in the `.ics` with the current score and match minute
4. Commits and pushes — subscribers' calendar apps pick up the update on their next poll

**Subscribers don't need to do anything.** The `.ics` URL stays the same. As long as Auto-refresh is enabled in their calendar app, updates arrive automatically:

| App | Refresh frequency |
|---|---|
| Apple Calendar | Every 15 minutes (set in subscription settings) |
| iPhone / iPad | Every 15 minutes (same setting synced via iCloud) |
| Google Calendar | Every 12–24 hours (Google's fixed polling interval) |
| Outlook | Every few hours (varies by client) |

**Score format:** `Argentina vs France 67' (1:2) (Quarter-final)`
Halftime shows `HT`, full time shows `FT`. After 100 minutes from kickoff, the workflow stops polling for that match.
