"""Backfill final scores from ESPN for all past match days.

Queries the ESPN scoreboard API for each date from tournament start through
yesterday, finds completed matches, and writes their FT scores to scores.json.
Run once to catch up on any matches the live updater missed.

    python3 backfill_scores.py
    python3 generate_ics.py   # bake the scores into the .ics
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl
import requests

HERE = Path(__file__).resolve().parent
SOURCE_XLSX = HERE / "2026_FIFA_World_Cup_Schedule.xlsx"
SCORES_FILE = HERE / "scores.json"

YEAR = 2026
TZ = ZoneInfo("America/Los_Angeles")
TOURNAMENT_START = date(2026, 6, 11)
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"


def make_uid(stage: str, dt_naive: datetime, stadium: str) -> str:
    key = f"{stage}|{dt_naive.isoformat()}|{stadium}"
    return hashlib.sha1(key.encode()).hexdigest()[:16] + "@fifa-world-cup-2026"


def load_schedule() -> list[dict]:
    wb = openpyxl.load_workbook(SOURCE_XLSX, data_only=True)
    ws = wb["Schedule"]
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        stage, date_str, time_str, match, stadium = (row[c] or "" for c in range(5))
        if not date_str or not time_str or not match:
            continue
        try:
            dt = datetime.strptime(f"{date_str} {YEAR} {time_str}", "%B %d %Y %H:%M")
        except ValueError:
            continue
        rows.append({
            "uid": make_uid(stage.strip(), dt, stadium.strip()),
            "match": match.strip(),
            "kickoff_utc": dt.replace(tzinfo=TZ).astimezone(timezone.utc),
        })
    return rows


def _norm(s: str) -> str:
    return s.lower().strip()


def _team_match(our: str, espn_team: dict) -> bool:
    our_n = _norm(our)
    for key in ("displayName", "name", "shortDisplayName", "abbreviation"):
        if _norm(espn_team.get(key, "")) == our_n:
            return True
    return False


def fetch_day(d: date) -> list[dict]:
    r = requests.get(
        ESPN_URL,
        params={"dates": d.strftime("%Y%m%d")},
        timeout=10,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    r.raise_for_status()
    return r.json().get("events", [])


def find_score(match_str: str, espn_events: list) -> str | None:
    if "TBD" in match_str:
        return None
    parts = match_str.split(" vs ", 1)
    if len(parts) != 2:
        return None
    t1, t2 = parts[0].strip(), parts[1].strip()

    for ev in espn_events:
        comp = ev.get("competitions", [{}])[0]
        state = comp.get("status", {}).get("type", {}).get("state", "")
        if state != "post":
            continue
        comps = comp.get("competitors", [])
        if len(comps) < 2:
            continue
        by_side = {c["homeAway"]: c for c in comps}
        home_c = by_side.get("home", {})
        away_c = by_side.get("away", {})
        home_t = home_c.get("team", {})
        away_t = away_c.get("team", {})

        if (_team_match(t1, home_t) and _team_match(t2, away_t)):
            return f"{home_c.get('score', 0)}:{away_c.get('score', 0)}"
        if (_team_match(t1, away_t) and _team_match(t2, home_t)):
            return f"{away_c.get('score', 0)}:{home_c.get('score', 0)}"
    return None


def main() -> None:
    schedule = load_schedule()
    by_uid = {m["uid"]: m for m in schedule}

    scores = json.loads(SCORES_FILE.read_text()) if SCORES_FILE.exists() else {}
    already = set(scores)

    today = datetime.now(timezone.utc).date()
    days = []
    d = TOURNAMENT_START
    while d < today:
        days.append(d)
        d += timedelta(days=1)

    if not days:
        print("No past days to backfill.")
        return

    print(f"Fetching {len(days)} days: {TOURNAMENT_START} → {today - timedelta(days=1)}")

    new_scores = 0
    for day in days:
        try:
            events = fetch_day(day)
        except Exception as e:
            print(f"  {day}: fetch failed — {e}", file=sys.stderr)
            continue

        day_new = 0
        for m in schedule:
            if m["uid"] in already:
                continue
            if m["kickoff_utc"].date() != day:
                continue
            score = find_score(m["match"], events)
            if score:
                scores[m["uid"]] = score
                already.add(m["uid"])
                day_new += 1
                new_scores += 1
                print(f"  {m['match']}: {score}")

        if day_new == 0:
            completed = sum(
                1 for ev in events
                if ev.get("competitions", [{}])[0].get("status", {}).get("type", {}).get("state") == "post"
            )
            print(f"  {day}: {len(events)} events, {completed} completed, 0 new matched")

    if new_scores:
        SCORES_FILE.write_text(json.dumps(scores, indent=2, sort_keys=True))
        print(f"\nWrote {new_scores} new score(s) to {SCORES_FILE.name}")
        print("Run: python3 generate_ics.py")
    else:
        print("\nNo new scores found.")


if __name__ == "__main__":
    main()
