"""Schedule timing gate — verifies xlsx dates/times match ESPN and football-data.org.

Exits 0 if everything matches.
Exits 1 if any mismatch is found (fail the pipeline).

Run before committing after update_xlsx.py:
    python3 test_schedule.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl
import requests

HERE = Path(__file__).resolve().parent
XLSX = HERE / "2026_FIFA_World_Cup_Schedule.xlsx"
PT   = ZoneInfo("America/Los_Angeles")
YEAR = 2026

KNOCKOUT_STAGES = {"Round of 32", "Round of 16", "Quarter-final",
                   "Semi-final", "Third Place", "Final"}

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"
KNOCKOUT_DATES = [
    "20260628","20260629","20260630","20260701","20260702","20260703",
    "20260704","20260705","20260706","20260707","20260709","20260710",
    "20260711","20260714","20260715","20260718","20260719",
]

FD_NAME_MAP = {
    "Bosnia-Herzegovina": "Bosnia",
    "United States":      "USA",
    "Cape Verde Islands": "Cabo Verde",
    "Turkey":             "Turkiye",
}
ESPN_NAME_MAP = {"United States": "USA"}
_TBD_KEYWORDS = ("Winner", "Loser", "Place", "Group", "Round",
                 "Quarterfinal", "Semifinal", "Playoff")

XLSX_TO_ESPN_VENUE: dict[str, list[str]] = {
    "SoFi Stadium":                ["sofi"],
    "Boston Stadium":              ["gillette"],
    "Houston Stadium":             ["nrg"],
    "MetLife Stadium":             ["metlife"],
    "Dallas Stadium":              ["at&t"],
    "Atlanta Stadium":             ["mercedes"],
    "Seattle Stadium":             ["lumen"],
    "Miami Stadium":               ["hard rock"],
    "Kansas City Stadium":         ["arrowhead"],
    "Toronto Stadium":             ["bmo"],
    "BC Place Vancouver":          ["bc place"],
    "Philadelphia Stadium":        ["lincoln"],
    "Monterrey Stadium":           ["bbva"],
    "Guadalajara Stadium":         ["banorte", "akron"],
    "San Jose Stadium":            ["levi"],
    "Los Angeles Stadium":         ["rose bowl"],
    "New York/New Jersey Stadium": ["metlife"],
}

def fd_norm(name):
    if not name: return "TBD"
    return FD_NAME_MAP.get(name, name)

def espn_norm(name):
    if any(kw in name for kw in _TBD_KEYWORDS): return "TBD"
    return ESPN_NAME_MAP.get(name, name)

def fmt_date(dt):
    return dt.strftime("%B ") + str(dt.day)

def get_fd_token():
    r = subprocess.run(
        ["security", "find-generic-password",
         "-a", "fifa2026", "-s", "football-data-api", "-w"],
        capture_output=True, text=True,
    )
    return r.stdout.strip()

def venue_matches(xlsx_stadium, espn_venue):
    keywords = XLSX_TO_ESPN_VENUE.get(xlsx_stadium, [])
    if not keywords:
        keywords = [w for w in xlsx_stadium.lower().split() if len(w) > 3]
    return any(kw in espn_venue.lower() for kw in keywords)

def teams_match(xlsx_match, home, away):
    if "TBD" in xlsx_match: return False
    parts = xlsx_match.split(" vs ", 1)
    if len(parts) != 2: return False
    t1, t2 = parts[0].strip().lower(), parts[1].strip().lower()
    return (t1 == home.lower() and t2 == away.lower()) or \
           (t1 == away.lower() and t2 == home.lower())

def fetch_espn_all():
    events = []
    for date in KNOCKOUT_DATES:
        req = urllib.request.Request(
            f"{ESPN_URL}?dates={date}",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        for ev in data.get("events", []):
            comp  = ev["competitions"][0]
            sides = {c["homeAway"]: c["team"]["displayName"] for c in comp["competitors"]}
            venue = comp.get("venue", {}).get("fullName", "")
            start = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(PT)
            events.append({
                "home": espn_norm(sides.get("home", "TBD")),
                "away": espn_norm(sides.get("away", "TBD")),
                "date": fmt_date(start), "time": start.strftime("%H:%M"),
                "venue": venue,
            })
    return events

def fetch_fd_group_stage(token):
    resp = requests.get(
        "https://api.football-data.org/v4/competitions/WC/matches",
        headers={"X-Auth-Token": token}, timeout=15,
    )
    resp.raise_for_status()
    events = []
    for m in resp.json()["matches"]:
        if m.get("stage") != "GROUP_STAGE": continue
        home = fd_norm(m["homeTeam"].get("name"))
        away = fd_norm(m["awayTeam"].get("name"))
        if home == "TBD" or away == "TBD": continue
        dt = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(PT)
        events.append({"home": home, "away": away,
                       "date": fmt_date(dt), "time": dt.strftime("%H:%M")})
    return events

def load_xlsx():
    wb = openpyxl.load_workbook(XLSX, data_only=True)
    ws = wb["Schedule"]
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0: continue
        stage, date_s, time_s, match, stadium = (list(row[:5]) + [None]*5)[:5]
        if not date_s or not time_s or not match: continue
        rows.append({
            "row": i+1, "stage": (stage or "").strip(),
            "date": str(date_s).strip(), "time": str(time_s).strip(),
            "match": str(match).strip(), "stadium": str(stadium or "").strip(),
        })
    return rows

def main():
    token = get_fd_token()
    if not token:
        print("SKIP: no football-data.org token — skipping group stage check")

    print("Fetching ESPN knockout schedule...")
    espn_events = fetch_espn_all()
    print(f"  {len(espn_events)} ESPN events\n")

    fd_events = []
    if token:
        print("Fetching football-data.org group stage schedule...")
        fd_events = fetch_fd_group_stage(token)
        print(f"  {len(fd_events)} fd.org group stage events\n")

    xlsx_rows = load_xlsx()
    print(f"Loaded {len(xlsx_rows)} rows from xlsx\n")

    failures = []
    checked = 0

    for row in xlsx_rows:
        stage, xlsx_d, xlsx_t = row["stage"], row["date"], row["time"]
        match, stadium = row["match"], row["stadium"]
        is_tbd = "TBD" in match

        if stage in KNOCKOUT_STAGES and not is_tbd:
            ref = next((e for e in espn_events
                        if teams_match(match, e["home"], e["away"])), None)
            if ref is None: continue
            checked += 1
            if xlsx_d != ref["date"] or xlsx_t != ref["time"]:
                failures.append(
                    f"Row {row['row']} [{stage}] {match}\n"
                    f"  xlsx : {xlsx_d} {xlsx_t}\n"
                    f"  ESPN : {ref['date']} {ref['time']}"
                )

        elif stage in KNOCKOUT_STAGES and is_tbd:
            slot = next((e for e in espn_events
                         if e["date"] == xlsx_d and e["time"] == xlsx_t
                         and venue_matches(stadium, e["venue"])), None)
            if slot is None:
                failures.append(
                    f"Row {row['row']} [{stage}] TBD at {stadium}\n"
                    f"  xlsx : {xlsx_d} {xlsx_t}\n"
                    f"  ESPN : no slot found for this date/time/venue"
                )
            else:
                checked += 1

        elif stage == "Group Stage" and not is_tbd and fd_events:
            ref = next((e for e in fd_events
                        if teams_match(match, e["home"], e["away"])), None)
            if ref is None: continue
            checked += 1
            if xlsx_d != ref["date"] or xlsx_t != ref["time"]:
                failures.append(
                    f"Row {row['row']} [Group Stage] {match}\n"
                    f"  xlsx   : {xlsx_d} {xlsx_t}\n"
                    f"  fd.org : {ref['date']} {ref['time']}"
                )

    print(f"Checked {checked} rows against API schedules.\n")
    if failures:
        print(f"FAIL — {len(failures)} timing mismatch(es):\n")
        for f in failures:
            print(f"  {f}\n")
        sys.exit(1)
    else:
        print("PASS — all xlsx timings match API schedules.")
        sys.exit(0)

if __name__ == "__main__":
    main()
