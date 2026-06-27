"""Fetch the full 2026 FIFA World Cup schedule from football-data.org and save to /tmp/wc2026_schedule.json."""

import json
import subprocess
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

PT = ZoneInfo("America/Los_Angeles")
OUT = Path(__file__).resolve().parent / "wc2026_schedule.json"


def get_token() -> str:
    r = subprocess.run(
        ["security", "find-generic-password", "-a", "fifa2026", "-s", "football-data-api", "-w"],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def main() -> None:
    token = get_token()
    if not token:
        raise SystemExit("No football-data.org token found in Keychain.")

    resp = requests.get(
        "https://api.football-data.org/v4/competitions/WC/matches",
        headers={"X-Auth-Token": token},
        timeout=15,
    )
    resp.raise_for_status()

    out = []
    for m in sorted(resp.json()["matches"], key=lambda x: x["utcDate"]):
        dt = datetime.fromisoformat(m["utcDate"].replace("Z", "+00:00")).astimezone(PT)
        score = m.get("score", {}).get("fullTime", {})
        sc = f"{score['home']}:{score['away']}" if score.get("home") is not None else ""
        out.append({
            "matchday": m.get("matchday"),
            "stage": m.get("stage"),
            "group": m.get("group", ""),
            "date": dt.strftime("%B %d"),
            "time": dt.strftime("%H:%M"),
            "home": m["homeTeam"].get("name", "TBD"),
            "away": m["awayTeam"].get("name", "TBD"),
            "status": m.get("status"),
            "score": sc,
        })

    OUT.write_text(json.dumps(out, indent=2))
    print(f"Saved {len(out)} matches to {OUT}")


if __name__ == "__main__":
    main()
