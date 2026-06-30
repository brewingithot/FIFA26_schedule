"""Live score updater for 2026 FIFA World Cup calendar feed.

Source priority per match:
  1. ESPN scoreboard — has live minute data, covers featured matches
  2. football-data.org — fallback for matches ESPN doesn't cover

Token resolution: FOOTBALL_DATA_TOKEN env var (GitHub Actions) or macOS Keychain (local/daemon).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
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
FOOTBALL_DATA_URL = "https://api.football-data.org/v4/competitions/WC/matches"


# ── Token ─────────────────────────────────────────────────────────────────────

def get_token() -> str:
    token = os.environ.get("FOOTBALL_DATA_TOKEN", "")
    if token:
        return token
    r = subprocess.run(
        ["security", "find-generic-password", "-a", "fifa2026", "-s", "football-data-api", "-w"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() if r.returncode == 0 else ""


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


# ── ESPN (primary) ────────────────────────────────────────────────────────────

def fetch_espn() -> list[dict]:
    r = requests.get(ESPN_URL, timeout=10, headers={"User-Agent": "Mozilla/5.0"})
    r.raise_for_status()
    return r.json().get("events", [])


def _norm(s: str) -> str:
    return s.lower().strip()


def _team_match_espn(our: str, espn_team: dict) -> bool:
    our_n = _norm(our)
    for key in ("displayName", "name", "shortDisplayName", "abbreviation"):
        val = _norm(espn_team.get(key, ""))
        if val == our_n:
            return True
        if our_n and (our_n in val or val in our_n):
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
        if (_team_match_espn(t1, home) and _team_match_espn(t2, away)) or \
           (_team_match_espn(t1, away) and _team_match_espn(t2, home)):
            return ev
    return None


def extract_score_espn(ev: dict, match_str: str) -> dict | None:
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

    t1 = match_str.split(" vs ", 1)[0].strip()
    if _team_match_espn(t1, home_c.get("team", {})):
        score = f"{home_c.get('score', 0)}:{away_c.get('score', 0)}"
    else:
        score = f"{away_c.get('score', 0)}:{home_c.get('score', 0)}"

    status_name = stype.get("name", "")

    if state == "post":
        if status_name == "STATUS_FINAL_PEN":
            if _team_match_espn(t1, home_c.get("team", {})):
                h_pen = home_c.get("shootoutScore")
                a_pen = away_c.get("shootoutScore")
            else:
                h_pen = away_c.get("shootoutScore")
                a_pen = home_c.get("shootoutScore")
            score = f"{score}, {h_pen}:{a_pen}p" if h_pen is not None else score
            minute = "FT-Pens"
        else:
            minute = "FT"
    elif status_name == "STATUS_HALFTIME":
        minute = "HT"
    else:
        detail = stype.get("detail", "")
        minute = detail if detail else f"{int(status.get('clock', 0) // 60)}'"

    return {"score": score, "minute": minute}


# ── football-data.org (fallback) ──────────────────────────────────────────────

def fetch_live() -> list[dict]:
    token = get_token()
    if not token:
        raise ValueError("No football-data.org token found.")
    r = requests.get(
        FOOTBALL_DATA_URL,
        params={"status": "LIVE"},
        headers={"X-Auth-Token": token},
        timeout=10,
    )
    r.raise_for_status()
    return r.json().get("matches", [])


def _team_match_fd(our: str, fd_team: dict) -> bool:
    our_n = _norm(our)
    for key in ("name", "shortName", "tla"):
        val = _norm(fd_team.get(key, ""))
        if val == our_n:
            return True
        # Handle partial matches e.g. "Bosnia" matching "Bosnia and Herzegovina"
        if our_n and (our_n in val or val in our_n):
            return True
    return False


def find_fd_match(match_str: str, fd_matches: list) -> dict | None:
    if "TBD" in match_str:
        return None
    parts = match_str.split(" vs ", 1)
    if len(parts) != 2:
        return None
    t1, t2 = parts[0].strip(), parts[1].strip()
    for m in fd_matches:
        home = m.get("homeTeam", {})
        away = m.get("awayTeam", {})
        if (_team_match_fd(t1, home) and _team_match_fd(t2, away)) or \
           (_team_match_fd(t1, away) and _team_match_fd(t2, home)):
            return m
    return None


def extract_score_fd(fd_match: dict, match_str: str, kickoff: datetime) -> dict | None:
    status = fd_match.get("status", "")
    if status not in ("IN_PLAY", "PAUSED", "FINISHED"):
        return None

    ft = (fd_match.get("score") or {}).get("fullTime") or {}
    h = ft.get("home") or 0
    a = ft.get("away") or 0

    t1 = match_str.split(" vs ", 1)[0].strip()
    if _team_match_fd(t1, fd_match.get("homeTeam", {})):
        score_str = f"{h}:{a}"
    else:
        score_str = f"{a}:{h}"

    if status == "FINISHED":
        minute = "FT"
    elif status == "PAUSED":
        minute = "HT"
    else:
        elapsed = int((datetime.now(timezone.utc) - kickoff.astimezone(timezone.utc)).total_seconds() / 60)
        if elapsed > 60:
            elapsed = max(elapsed - 15, 46)
        minute = f"{min(elapsed, 90)}'"

    return {"score": score_str, "minute": minute}


# ── Winner trophy ─────────────────────────────────────────────────────────────

def _trophy_match(match: str, score: str) -> str:
    """Prefix the winning team with 🏆. No-op for draws or unparseable scores."""
    parts = match.split(" vs ", 1)
    if len(parts) != 2:
        return match
    home, away = parts[0].strip(), parts[1].strip()
    try:
        if "p" in score:
            pen = score.split(", ", 1)[1].rstrip("p")
            h, a = int(pen.split(":")[0]), int(pen.split(":")[1])
        else:
            h, a = int(score.split(":")[0]), int(score.split(":")[1])
        if h > a:
            return f"🏆 {home} vs {away}"
        elif a > h:
            return f"{home} vs 🏆 {away}"
    except Exception:
        pass
    return match


# ── Final score persistence ───────────────────────────────────────────────────

def save_final_score(uid: str, score: str) -> None:
    scores = json.loads(SCORES_FILE.read_text()) if SCORES_FILE.exists() else {}
    if scores.get(uid) == score:
        return
    scores[uid] = score
    SCORES_FILE.write_text(json.dumps(scores, indent=2, sort_keys=True))


# ── .ics patching ─────────────────────────────────────────────────────────────

def patch_ics(uid: str, new_summary: str, state: dict) -> bool:
    raw = OUTPUT_ICS.read_bytes().decode("utf-8")

    new_esc = escape_ical(new_summary)
    folded_summary = fold_line(f"SUMMARY:{new_esc}")

    changed = False
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    def replace_block(m: re.Match) -> str:
        nonlocal changed
        block = m.group(0)

        if f"UID:{uid}\r\n" not in block:
            return block

        existing = re.search(r"SUMMARY:[^\r\n]*(?:\r\n[ \t][^\r\n]*)*", block)
        if existing and existing.group(0) == f"SUMMARY:{new_esc}":
            return block

        new_seq = state.get(uid, {}).get("sequence", 0) + 1

        block = re.sub(r"SUMMARY:[^\r\n]*(?:\r\n[ \t][^\r\n]*)*", folded_summary, block, count=1)
        block = re.sub(r"DTSTAMP:[^\r\n]+", f"DTSTAMP:{now_stamp}", block)
        block = re.sub(r"LAST-MODIFIED:[^\r\n]+", f"LAST-MODIFIED:{now_stamp}", block)
        block = re.sub(r"SEQUENCE:\d+", f"SEQUENCE:{new_seq}", block)

        changed = True
        prev = state.get(uid, {})
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

    # Fetch ESPN once — covers featured matches with live minute data
    try:
        espn_events = fetch_espn()
    except Exception as e:
        print(f"ESPN fetch failed: {e}", file=sys.stderr)
        espn_events = []

    # Fetch football-data.org lazily — only if needed as fallback
    fd_matches: list[dict] | None = None

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}

    for m in live:
        score_info = None
        source = None

        # Try ESPN first
        ev = find_espn_event(m["match"], espn_events)
        if ev:
            score_info = extract_score_espn(ev, m["match"])
            source = "ESPN"

        # Fall back to football-data.org
        if not score_info:
            if fd_matches is None:
                try:
                    fd_matches = fetch_live()
                except Exception as e:
                    print(f"  football-data.org fetch failed: {e}", file=sys.stderr)
                    fd_matches = []
            fd = find_fd_match(m["match"], fd_matches)
            if fd:
                score_info = extract_score_fd(fd, m["match"], m["kickoff"])
                source = "football-data.org"

        if not score_info:
            print(f"  Not found on any source: {m['match']}")
            continue

        if score_info["minute"] in ("FT", "FT-Pens"):
            save_final_score(m["uid"], score_info["score"])

        match_display = _trophy_match(m["match"], score_info["score"]) \
            if score_info["minute"] in ("FT", "FT-Pens") else m["match"]
        summary = (
            f"{match_display} {score_info['minute']} "
            f"({score_info['score']}) ({m['stage']})"
        )
        updated = patch_ics(m["uid"], summary, state)
        print(f"  [{source}] {'Updated' if updated else 'Unchanged'}: {summary}")


if __name__ == "__main__":
    main()
