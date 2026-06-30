"""Generate a subscribable iCalendar feed for the 2026 FIFA World Cup schedule.

Reads 2026_FIFA_World_Cup_Schedule.xlsx and writes 2026_FIFA_World_Cup.ics.

Update semantics:
  * UID is derived from (stage, kickoff, stadium) — NOT match name. So when a
    "TBD vs TBD" knockout entry is later filled in with real teams, the UID
    stays the same and subscribers' calendar clients treat it as an UPDATE to
    the existing event, not a new event.
  * state.json tracks per-UID content hashes and SEQUENCE numbers. SEQUENCE
    bumps only when an event's content actually changes; unchanged events
    re-emit byte-identical VEVENT blocks across runs (no churn for clients).
  * LAST-MODIFIED / DTSTAMP advance only on actual change.
  * Identity-free output: no ORGANIZER, no ATTENDEE, no email anywhere.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, time, timedelta, timezone
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
SOURCE_XLSX = HERE / "2026_FIFA_World_Cup_Schedule.xlsx"
OUTPUT_ICS = HERE / "2026_FIFA_World_Cup.ics"
OUTPUT_ICS_NO_SCORES = HERE / "2026_FIFA_World_Cup_no_live_scores.ics"
STATE_FILE = HERE / "state.json"
STATE_FILE_NO_SCORES = HERE / "state_no_scores.json"
SCORES_FILE = HERE / "scores.json"

YEAR = 2026
TZID = "America/Los_Angeles"
EVENT_DURATION = timedelta(minutes=15)
PRODID = "-//Public//FIFA 2026//EN"
CAL_NAME = "2026 FIFA World Cup"
CAL_DESC = "All 76 matches of the 2026 FIFA World Cup. Times in Pacific."

VTIMEZONE = """BEGIN:VTIMEZONE
TZID:America/Los_Angeles
X-LIC-LOCATION:America/Los_Angeles
BEGIN:DAYLIGHT
TZOFFSETFROM:-0800
TZOFFSETTO:-0700
TZNAME:PDT
DTSTART:19700308T020000
RRULE:FREQ=YEARLY;BYMONTH=3;BYDAY=2SU
END:DAYLIGHT
BEGIN:STANDARD
TZOFFSETFROM:-0700
TZOFFSETTO:-0800
TZNAME:PST
DTSTART:19701101T020000
RRULE:FREQ=YEARLY;BYMONTH=11;BYDAY=1SU
END:STANDARD
END:VTIMEZONE"""


def escape_text(value: str) -> str:
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
        chunk = encoded[idx : idx + 75]
        while True:
            try:
                decoded = chunk.decode("utf-8")
                break
            except UnicodeDecodeError:
                chunk = chunk[:-1]
        pieces.append(decoded)
        idx += len(chunk)
    return "\r\n ".join(pieces)


def parse_row(row: tuple) -> dict | None:
    stage, date_str, time_str, match, stadium, country = row[:6]
    force_score = row[6] if len(row) > 6 else None
    if not date_str or not time_str or not match:
        return None
    dt = datetime.strptime(f"{date_str} {YEAR} {time_str}", "%B %d %Y %H:%M")
    return {
        "stage": (stage or "").strip(),
        "match": match.strip(),
        "stadium": (stadium or "").strip(),
        "country": (country or "").strip(),
        "start": dt,
        "end": dt + EVENT_DURATION,
        "force_score": f"{force_score.hour}:{force_score.minute}" if isinstance(force_score, time) else (str(force_score).strip() if force_score else None),
    }


def make_uid(event: dict) -> str:
    # Identity = stage + kickoff + stadium. Match name excluded on purpose.
    key = f"{event['stage']}|{event['start'].isoformat()}|{event['stadium']}"
    digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]
    return f"{digest}@fifa-world-cup-2026"


def fmt_local(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%M%S")


def trophy_match(match: str, score: str) -> str:
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


def build_event_payload(event: dict, final_scores: dict | None = None) -> dict:
    uid = make_uid(event)
    score = (final_scores or {}).get(uid)
    if score:
        ft_label = "FT-Pens" if "p" in score else "FT"
        summary = f"{trophy_match(event['match'], score)} {ft_label} ({score}) ({event['stage']})"
    else:
        summary = f"{event['match']} ({event['stage']})"
    location = ", ".join(p for p in (event["stadium"], event["country"]) if p)
    description = (
        f"Stage: {event['stage']}\n"
        f"Match: {event['match']}\n"
        f"Venue: {location}\n"
        f"Kickoff: {event['start'].strftime('%a %b %d, %Y at %H:%M')} {TZID}"
    )
    return {
        "uid": uid,
        "summary": summary,
        "location": location,
        "description": description,
        "dtstart": fmt_local(event["start"]),
        "dtend": fmt_local(event["end"]),
        "stage": event["stage"],
    }


def content_hash(payload: dict) -> str:
    parts = [
        payload["summary"],
        payload["location"],
        payload["description"],
        payload["dtstart"],
        payload["dtend"],
    ]
    return hashlib.sha1("\x00".join(parts).encode("utf-8")).hexdigest()


def load_state(path: Path = STATE_FILE) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {}


def save_state(state: dict, path: Path = STATE_FILE) -> None:
    path.write_text(json.dumps(state, indent=2, sort_keys=True))


def build_vevent(payload: dict, sequence: int, stamp: str) -> list[str]:
    return [
        "BEGIN:VEVENT",
        f"UID:{payload['uid']}",
        f"DTSTAMP:{stamp}",
        f"LAST-MODIFIED:{stamp}",
        f"SEQUENCE:{sequence}",
        f"DTSTART;TZID={TZID}:{payload['dtstart']}",
        f"DTEND;TZID={TZID}:{payload['dtend']}",
        f"SUMMARY:{escape_text(payload['summary'])}",
        f"LOCATION:{escape_text(payload['location'])}",
        f"DESCRIPTION:{escape_text(payload['description'])}",
        f"CATEGORIES:{escape_text(payload['stage'])}",
        "TRANSP:TRANSPARENT",
        "STATUS:CONFIRMED",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        f"DESCRIPTION:{escape_text(payload['summary'])}",
        "TRIGGER:PT0M",
        "END:VALARM",
        "END:VEVENT",
    ]


def generate_ics(
    events: list,
    final_scores: dict,
    output_path: Path,
    state_path: Path,
    now_stamp: str,
    cal_name: str,
    cal_desc: str,
) -> None:
    state = load_state(state_path)
    new_state: dict = {}

    out_lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:{PRODID}",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{escape_text(cal_name)}",
        f"X-WR-CALDESC:{escape_text(cal_desc)}",
        f"X-WR-TIMEZONE:{TZID}",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
        VTIMEZONE,
    ]

    changed = added = unchanged = 0
    for event in events:
        payload = build_event_payload(event, final_scores)
        h = content_hash(payload)
        prev = state.get(payload["uid"])
        if prev is None:
            sequence = 0
            stamp = now_stamp
            added += 1
        elif prev["hash"] == h:
            sequence = prev["sequence"]
            stamp = prev["stamp"]
            unchanged += 1
        else:
            sequence = prev["sequence"] + 1
            stamp = now_stamp
            changed += 1
        new_state[payload["uid"]] = {"hash": h, "sequence": sequence, "stamp": stamp}
        out_lines.extend(build_vevent(payload, sequence, stamp))

    out_lines.append("END:VCALENDAR")

    folded = [fold_line(line) for raw in out_lines for line in raw.splitlines()]
    output_path.write_bytes(("\r\n".join(folded) + "\r\n").encode("utf-8"))

    removed = len(set(state) - set(new_state))
    save_state(new_state, state_path)

    print(
        f"Wrote {len(events)} events to {output_path.name} "
        f"(+{added} added, {changed} changed, {unchanged} unchanged, {removed} removed)"
    )


def main() -> None:
    wb = openpyxl.load_workbook(SOURCE_XLSX, data_only=True)
    ws = wb["Schedule"]

    events = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        parsed = parse_row(row)
        if parsed:
            events.append(parsed)
    events.sort(key=lambda e: e["start"])

    final_scores = json.loads(SCORES_FILE.read_text()) if SCORES_FILE.exists() else {}

    # Write force scores through to scores.json so all consumers see them
    force_applied = 0
    for event in events:
        if event.get("force_score"):
            uid = make_uid(event)
            if final_scores.get(uid) != event["force_score"]:
                final_scores[uid] = event["force_score"]
                force_applied += 1
    if force_applied:
        SCORES_FILE.write_text(json.dumps(final_scores, indent=2, sort_keys=True))
        print(f"Applied {force_applied} force score(s) to {SCORES_FILE.name}")

    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    # Version 1: with live scores
    generate_ics(
        events, final_scores, OUTPUT_ICS, STATE_FILE, now_stamp,
        CAL_NAME,
        "All 76 matches of the 2026 FIFA World Cup. Times in Pacific. Includes live scores.",
    )

    # Version 2: no scores — clean titles only
    generate_ics(
        events, {}, OUTPUT_ICS_NO_SCORES, STATE_FILE_NO_SCORES, now_stamp,
        f"{CAL_NAME} (No Scores)",
        "All 76 matches of the 2026 FIFA World Cup. Times in Pacific. No live scores.",
    )


if __name__ == "__main__":
    main()
