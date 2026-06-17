"""Live score updater for 2026 FIFA World Cup calendar feed.

Finds any match currently in progress (kickoff <= now <= kickoff + 100 min),
fetches the live score from ESPN's scoreboard API, and patches the .ics
SUMMARY to: "ABC vs DEF 45' (1:2) (Group Stage)"

Designed to run every 5 minutes via GitHub Actions. Exits silently when no
match is live so non-match days cost nothing.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import openpyxl
import requests

HERE = Path(__file__).resolve().parent
SOURCE_XLSX = HERE / "2026_FIFA_World_Cup_Schedule.xlsx"
OUTPUT_ICS = HERE / "2026_FIFA_World_Cup.ics"
STATE_FILE = HERE / "state.json"
SCORES_FILE = HERE / "scores.json"

YEAR = 2026
TZ = ZoneInfo("America/Los_Angeles")
LIVE_WINDOW = timedelta(minutes=130)

ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/fifa.world/scoreboard"


# ── iCal helpers ──────────────────────────────────────────────────────────────

def escape_ical(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold_line(line: str) -> str:
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    pieces, idx = [], 0
    while idx < len(encoded):
        chunk = encoded[idx: idx + 75]
        while True:
            try:
                chunk.decode("utf-8")
                break
            except UnicodeDecodeError:
                chunk = chunk[:-1]
        pieces.append(chunk.decode("utf-8"))
        idx += len(chunk)
    return "\r\n ".join(pieces)


# ── Schedule ──────────────────────────────────────────────────────────────────

def read_schedule() -> list[dict]:
    wb = openpyxl.load_workbook(SOURCE_XLSX, data_only=True)
    ws = wb["Schedule"]
    rows = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        stage, date_str, time_str, match, stadium, country = row[:6]
        if not date_str or not time_str or not match:
            continue
        dt_naive = datetime.strptime(f"{date_str} {YEAR} {time_str}", "%B %d %Y %H:%M")
        # UID must be computed from naive datetime to match generate_ics.py
        uid_key = f"{(stage or '').strip()}|{dt_naive.isoformat()}|{(stadium or '').strip()}"
        uid = hashlib.sha1(uid_key.encode()).hexdigest()[:16] + "@fifa-world-cup-2026"
        rows.append({
            "uid": uid,
            "stage": (stage or "").strip(),
            "match": match.strip(),
            "kickoff": dt_naive.replace(tzinfo=TZ),
            "stadium": (stadium or "").strip(),
        })
    return rows


def find_live(matches: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc)
    return [
        m for m in matches
        if m["kickoff"].astimezone(timezone.utc) <= now
        <= m["kickoff"].astimezone(timezone.utc) + LIVE_WINDOW
    ]


# ── ESPN API ──────────────────────────────────────────────────────────────────

def fetch_espn() -> list[dict]:
    r = requests.get(ESPN_URL, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json().get("events", [])


def _norm(s: str) -> str:
    return s.lower().strip()


def _team_match(our: str, espn_team: dict) -> bool:
    our_n = _norm(our)
    for key in ("displayName", "name", "shortDisplayName", "abbreviation"):
        if _norm(espn_team.get(key, "")) == our_n:
            return True
    return False


def find_espn_event(match_str: str, espn_events: list) -> dict | None:
    if "TBD" in match_str:
        return None
    parts = match_str.split(" vs ", 1)
    if len(parts) != 2:
        return None
    t1, t2 = parts[0].strip(), parts[1].strip()
    for ev in espn_events:
        comps = ev.get("competitions", [{}])[0].get("competitors", [])
        if len(comps) < 2:
            continue
        by_side = {c["homeAway"]: c["team"] for c in comps}
        home, away = by_side.get("home", {}), by_side.get("away", {})
        if (_team_match(t1, home) and _team_match(t2, away)) or \
           (_team_match(t1, away) and _team_match(t2, home)):
            return ev
    return None


def extract_score(ev: dict, match_str: str) -> dict | None:
    comp = ev["competitions"][0]
    status = comp["status"]
    stype = status["type"]
    state = stype.get("state", "")

    if state not in ("in", "post"):
        return None

    comps = comp["competitors"]
    by_side = {c["homeAway"]: c for c in comps}
    home_c = by_side.get("home", {})
    away_c = by_side.get("away", {})

    # Preserve team order from our match string
    t1 = match_str.split(" vs ", 1)[0].strip()
    if _team_match(t1, home_c.get("team", {})):
        score = f"{home_c.get('score', 0)}:{away_c.get('score', 0)}"
    else:
        score = f"{away_c.get('score', 0)}:{home_c.get('score', 0)}"

    if state == "post":
        minute = "FT"
    elif stype.get("name") == "STATUS_HALFTIME":
        minute = "HT"
    else:
        detail = stype.get("detail", "")
        minute = detail if detail else f"{int(status.get('clock', 0) // 60)}'"

    return {"score": score, "minute": minute}


# ── Final score persistence ───────────────────────────────────────────────────

def save_final_score(uid: str, score: str) -> None:
    """Write FT score to scores.json so generate_ics.py can include it permanently."""
    scores = json.loads(SCORES_FILE.read_text()) if SCORES_FILE.exists() else {}
    if scores.get(uid) == score:
        return
    scores[uid] = score
    SCORES_FILE.write_text(json.dumps(scores, indent=2, sort_keys=True))


# ── .ics patching ─────────────────────────────────────────────────────────────

def patch_ics(uid: str, new_summary: str, state: dict) -> bool:
    """Find the VEVENT with matching UID and update its SUMMARY in place.

    Uses regex on raw .ics bytes so folded multi-line property values are
    handled correctly without a full parse/rewrite cycle.
    Returns True if the file was modified.
    """
    raw = OUTPUT_ICS.read_bytes().decode("utf-8")

    new_esc = escape_ical(new_summary)
    folded_summary = fold_line(f"SUMMARY:{new_esc}")

    changed = False
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def replace_block(m: re.Match) -> str:
        nonlocal changed
        block = m.group(0)

        # Only touch the block that contains this UID
        if f"UID:{uid}\r\n" not in block:
            return block

        # Check if SUMMARY is already up to date
        existing = re.search(r"SUMMARY:[^\r\n]*(?:\r\n[ \t][^\r\n]*)*", block)
        if existing and existing.group(0) == f"SUMMARY:{new_esc}":
            return block  # nothing to do

        new_seq = state.get(uid, {}).get("sequence", 0) + 1

        block = re.sub(r"SUMMARY:[^\r\n]*(?:\r\n[ \t][^\r\n]*)*", folded_summary, block, count=1)
        block = re.sub(r"DTSTAMP:[^\r\n]+", f"DTSTAMP:{now_stamp}", block)
        block = re.sub(r"LAST-MODIFIED:[^\r\n]+", f"LAST-MODIFIED:{now_stamp}", block)
        block = re.sub(r"SEQUENCE:\d+", f"SEQUENCE:{new_seq}", block)

        changed = True
        prev = state.get(uid, {})
        # Keep original hash so generate_ics.py can detect when to revert after the match
        state[uid] = {"hash": prev.get("hash", ""), "sequence": new_seq, "stamp": now_stamp}
        return block

    new_raw = re.sub(r"BEGIN:VEVENT\r\n.*?END:VEVENT", replace_block, raw, flags=re.DOTALL)

    if changed:
        OUTPUT_ICS.write_bytes(new_raw.encode("utf-8"))
        STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))

    return changed


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    matches = read_schedule()
    live = find_live(matches)

    if not live:
        print("No live matches.")
        return

    print(f"Live: {[m['match'] for m in live]}")

    try:
        espn_events = fetch_espn()
    except Exception as e:
        print(f"ESPN fetch failed: {e}", file=sys.stderr)
        sys.exit(1)

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    for m in live:
        ev = find_espn_event(m["match"], espn_events)
        if not ev:
            print(f"  Not found on ESPN: {m['match']}")
            continue

        score_info = extract_score(ev, m["match"])
        if not score_info:
            print(f"  Not started yet on ESPN: {m['match']}")
            continue

        if score_info["minute"] == "FT":
            save_final_score(m["uid"], score_info["score"])

        summary = (
            f"{m['match']} {score_info['minute']} "
            f"({score_info['score']}) ({m['stage']})"
        )
        updated = patch_ics(m["uid"], summary, state)
        print(f"  {'Updated' if updated else 'Unchanged'}: {summary}")


if __name__ == "__main__":
    main()
