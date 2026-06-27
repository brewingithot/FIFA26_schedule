"""Sync the xlsx from football-data.org.

Does three things in one pass:
  1. Updates TBD team names in knockout rows when the matchup is known.
  2. Backfills the Score column (col G) for finished matches in ALL rounds.
  3. Inserts any missing Group Stage MD3 rows (idempotent once they exist).

Run after each match day or whenever the bracket advances:
    python3 update_xlsx.py
    ./update.sh          # regenerate the .ics with updated names + scores
"""

from __future__ import annotations

import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl
import requests

HERE = Path(__file__).resolve().parent
XLSX = HERE / "2026_FIFA_World_Cup_Schedule.xlsx"
PT = ZoneInfo("America/Los_Angeles")
YEAR = 2026
SCORE_COL = 7   # column G (1-indexed)

# football-data.org stage identifiers → xlsx stage names
FD_STAGE_MAP = {
    "GROUP_STAGE":    "Group Stage",
    "LAST_32":        "Round of 32",
    "LAST_16":        "Round of 16",
    "QUARTER_FINALS": "Quarter-final",
    "SEMI_FINALS":    "Semi-final",
    "THIRD_PLACE":    "Third Place",
    "FINAL":          "Final",
}

# Normalize venue names so xlsx labels can be compared to football-data.org venue strings
VENUE_KEYWORDS = {
    "sofi":         "SoFi Stadium",
    "gillette":     "Boston Stadium",
    "nrg":          "Houston Stadium",
    "metlife":      "MetLife Stadium",
    "at&t":         "Dallas Stadium",
    "mercedes":     "Atlanta Stadium",
    "lumen":        "Seattle Stadium",
    "hard rock":    "Miami Stadium",
    "arrowhead":    "Kansas City Stadium",
    "bmo":          "Toronto Stadium",
    "bc place":     "BC Place Vancouver",
    "lincoln":      "Philadelphia Stadium",
    "bbva":         "Monterrey Stadium",
    "banorte":      "Guadalajara Stadium",
    "akron":        "Guadalajara Stadium",
    "levi":         "San Jose Stadium",
    "rose bowl":    "Los Angeles Stadium",
    "new york":     "New York/New Jersey Stadium",
    "new jersey":   "New York/New Jersey Stadium",
}

def norm_venue(v: str) -> str:
    """Reduce a venue string to its xlsx label, or the lowercased original."""
    vl = v.lower()
    for kw, label in VENUE_KEYWORDS.items():
        if kw in vl:
            return label
    return vl

# football-data.org uses slightly different team names in some cases
NAME_MAP = {
    "Bosnia-Herzegovina": "Bosnia",
    "United States":      "USA",
    "Cape Verde Islands": "Cabo Verde",
    "Turkey":             "Türkiye",
}


def get_token() -> str:
    r = subprocess.run(
        ["security", "find-generic-password", "-a", "fifa2026", "-s", "football-data-api", "-w"],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def norm(name: str | None) -> str:
    if not name:
        return "TBD"
    return NAME_MAP.get(name, name)


def fmt_date(dt: datetime) -> str:
    return dt.strftime("%B ") + str(dt.day)   # "June 4", not "June 04"


def scrub_metadata(wb: openpyxl.Workbook) -> None:
    p = wb.properties
    for field in ("creator", "lastModifiedBy", "title", "subject",
                  "description", "keywords", "category", "contentStatus"):
        setattr(p, field, "")


def main() -> None:
    token = get_token()
    if not token:
        raise SystemExit("No football-data.org token in Keychain. "
                         "Store it with: security add-generic-password "
                         "-a fifa2026 -s football-data-api -w YOUR_TOKEN")

    print("Fetching schedule from football-data.org...")
    resp = requests.get(
        "https://api.football-data.org/v4/competitions/WC/matches",
        headers={"X-Auth-Token": token},
        timeout=15,
    )
    resp.raise_for_status()
    raw = resp.json()["matches"]
    print(f"  {len(raw)} matches fetched\n")

    # ── Build lookup structures ───────────────────────────────────────────────

    # by_teams: frozenset({home_lower, away_lower}) → match dict  (for known matchups)
    # by_slot:  (xlsx_stage, date_str, approx_time) → match dict  (for TBD rows)
    by_teams: dict = {}
    by_slot:  dict = {}

    for m in raw:
        home = norm(m["homeTeam"].get("name"))
        away = norm(m["awayTeam"].get("name"))
        fd_stage = m.get("stage", "")
        xlsx_stage = FD_STAGE_MAP.get(fd_stage, "")
        dt_pt = datetime.fromisoformat(
            m["utcDate"].replace("Z", "+00:00")
        ).astimezone(PT)
        ft = (m.get("score") or {}).get("fullTime") or {}
        score = (f"{ft['home']}:{ft['away']}"
                 if ft.get("home") is not None else "")
        status = m.get("status", "")

        entry = {
            "home": home, "away": away,
            "date": fmt_date(dt_pt), "time": dt_pt.strftime("%H:%M"),
            "matchday": m.get("matchday"),
            "status": status, "score": score,
            "xlsx_stage": xlsx_stage, "dt_pt": dt_pt,
            "venue": m.get("venue") or "",
        }

        if home != "TBD" and away != "TBD":
            by_teams[frozenset({home.lower(), away.lower()})] = entry

        if xlsx_stage:
            # Key on (stage, date, normalized_venue) so venue disambiguates same-day games
            fd_venue_norm = norm_venue(entry.get("venue", ""))
            slot_key = (xlsx_stage, fmt_date(dt_pt), fd_venue_norm)
            by_slot.setdefault(slot_key, []).append(entry)

    # ── Load xlsx ─────────────────────────────────────────────────────────────

    wb = openpyxl.load_workbook(XLSX)
    ws = wb["Schedule"]

    existing_teams: set = set()
    first_knockout_row: int | None = None

    for row in ws.iter_rows(min_row=2):
        stage = str(row[0].value or "")
        match_str = str(row[3].value or "")
        if " vs " in match_str and "TBD" not in match_str:
            h, a = match_str.strip().split(" vs ", 1)
            existing_teams.add(frozenset({h.strip().lower(), a.strip().lower()}))
        if stage != "Group Stage" and stage.strip() and first_knockout_row is None:
            first_knockout_row = row[0].row

    print(f"Existing named fixtures in xlsx: {len(existing_teams)}")
    print(f"First knockout row: {first_knockout_row}\n")

    # ── 1. Update existing rows: team names + scores ──────────────────────────

    names_updated = scores_updated = 0
    used_fd_entries: set[int] = set()   # prevent same fd match assigned to two rows

    for row in ws.iter_rows(min_row=2):
        stage     = str(row[0].value or "").strip()
        date_val  = str(row[1].value or "").strip()
        time_val  = str(row[2].value or "").strip()
        match_str = str(row[3].value or "").strip()
        score_cell = row[SCORE_COL - 1]   # 0-indexed

        if not match_str:
            continue

        is_tbd = "TBD" in match_str

        if is_tbd:
            # Match by stage + date + normalized venue (venue prevents cross-stadium confusion)
            xlsx_venue_norm = norm_venue(str(row[4].value or ""))
            slot_key = (stage, date_val, xlsx_venue_norm)
            candidates = [
                c for c in by_slot.get(slot_key, [])
                if id(c) not in used_fd_entries
                and c["home"] != "TBD" and c["away"] != "TBD"
            ]
            if not candidates:
                continue

            best = (candidates[0] if len(candidates) == 1 else
                    min(candidates, key=lambda c: abs(
                        (c["dt_pt"] - datetime.strptime(
                            f"{date_val} {YEAR} {time_val}", "%B %d %Y %H:%M"
                        ).replace(tzinfo=PT)).total_seconds()
                    )))

            used_fd_entries.add(id(best))

            new_match = f"{best['home']} vs {best['away']}"
            row[3].value = new_match
            names_updated += 1
            print(f"  [name]  Row {row[0].row}: {match_str} → {new_match}")

            # Also fill score if the match is already finished
            if best["status"] == "FINISHED" and best["score"] and not score_cell.value:
                score_cell.value = best["score"]
                scores_updated += 1
                print(f"  [score] Row {row[0].row}: {new_match} → {best['score']}")

        else:
            # Known teams — look up by frozenset to get score
            if score_cell.value:
                continue   # already has a score
            parts = match_str.split(" vs ", 1)
            if len(parts) != 2:
                continue
            h, a = parts[0].strip(), parts[1].strip()
            entry = by_teams.get(frozenset({h.lower(), a.lower()}))
            if entry and entry["status"] == "FINISHED" and entry["score"]:
                score_cell.value = entry["score"]
                scores_updated += 1
                print(f"  [score] Row {row[0].row}: {match_str} → {entry['score']}")

    print(f"\n  {names_updated} team names updated, {scores_updated} scores filled\n")

    # ── 2. Insert missing MD3 rows ────────────────────────────────────────────

    md3_to_add = sorted(
        [m for k, m in by_teams.items()
         if m["matchday"] == 3
         and k not in existing_teams],
        key=lambda x: datetime.strptime(
            f"{x['date']} {YEAR} {x['time']}", "%B %d %Y %H:%M"
        ),
    )

    if md3_to_add and first_knockout_row:
        print(f"Inserting {len(md3_to_add)} missing MD3 rows before row {first_knockout_row}...")
        ws.insert_rows(first_knockout_row, len(md3_to_add))
        for i, m in enumerate(md3_to_add):
            r = first_knockout_row + i
            ws.cell(row=r, column=1).value = "Group Stage"
            ws.cell(row=r, column=2).value = m["date"]
            ws.cell(row=r, column=3).value = m["time"]
            ws.cell(row=r, column=4).value = f"{m['home']} vs {m['away']}"
            ws.cell(row=r, column=5).value = m["venue"]
            ws.cell(row=r, column=6).value = ""
            if m["score"]:
                ws.cell(row=r, column=SCORE_COL).value = m["score"]
            print(f"  Row {r}: {m['date']} {m['time']}  {m['home']} vs {m['away']}"
                  + (f"  [{m['score']}]" if m["score"] else ""))
    else:
        print("No missing MD3 rows to insert.")

    # ── Save ──────────────────────────────────────────────────────────────────

    scrub_metadata(wb)
    wb.save(XLSX)
    print(f"\nSaved: {XLSX.name}")
    print("Next: ./update.sh to regenerate the .ics")


if __name__ == "__main__":
    main()
