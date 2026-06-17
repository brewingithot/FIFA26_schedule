"""Persistent live score daemon for the 2026 FIFA World Cup.

Runs continuously. Every 5 minutes:
  - Checks if any match is currently live
  - If yes: fetches ESPN score, patches .ics, commits and pushes
  - If no: sleeps and checks again

Keeps running until TOURNAMENT_END. launchd (KeepAlive=true) restarts
it automatically if it crashes or the machine reboots.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from live_score_updater import (
    extract_score,
    fetch_live,
    find_fd_match,
    find_live,
    patch_ics,
    read_schedule,
    save_final_score,
)

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / "state.json"
LOG_FILE = HERE / "daemon.log"

POLL_INTERVAL_IDLE = 300   # 5 minutes when no match is live
POLL_INTERVAL_LIVE = 120   # 2 minutes during a live match
TOURNAMENT_END = date(2026, 7, 26)  # ~45 days from June 11 final


# ── Logging ───────────────────────────────────────────────────────────────────

def log(msg: str) -> None:
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
    print(line, flush=True)
    with LOG_FILE.open("a") as f:
        f.write(line + "\n")


# ── Git ───────────────────────────────────────────────────────────────────────

def git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(HERE), *args],
        capture_output=True, text=True,
    )


def commit_and_push() -> bool:
    git("pull", "--rebase", "origin", "main")

    git("add", "2026_FIFA_World_Cup.ics", "state.json")
    if (HERE / "scores.json").exists():
        git("add", "scores.json")

    if git("diff", "--cached", "--quiet").returncode == 0:
        return False  # nothing staged

    git("config", "user.name", "brewingithot")
    git("config", "user.email", "brewingithot@users.noreply.github.com")
    result = git("commit", "-m", "live: score update")
    if result.returncode != 0:
        log(f"commit failed: {result.stderr.strip()}")
        return False

    for attempt in range(1, 4):
        pull = git("pull", "--rebase", "origin", "main")
        push = git("push")
        if push.returncode == 0:
            return True
        log(f"push attempt {attempt} failed — retrying in {attempt * 2}s")
        time.sleep(attempt * 2)

    log("all push attempts failed")
    return False


# ── One poll cycle ────────────────────────────────────────────────────────────

def poll() -> bool:
    """Run one check cycle. Returns True if .ics was updated."""
    matches = read_schedule()
    live = find_live(matches)

    if not live:
        return False

    log(f"Live: {[m['match'] for m in live]}")

    try:
        fd_matches = fetch_live()
    except Exception as e:
        log(f"football-data.org fetch failed: {e}")
        return False

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    any_updated = False

    for m in live:
        fd = find_fd_match(m["match"], fd_matches)
        if not fd:
            log(f"  not found: {m['match']}")
            continue

        score_info = extract_score(fd, m["match"], m["kickoff"])
        if not score_info:
            log(f"  not started yet: {m['match']}")
            continue

        if score_info["minute"] == "FT":
            save_final_score(m["uid"], score_info["score"])
            log(f"  FT saved: {m['match']} ({score_info['score']})")

        summary = (
            f"{m['match']} {score_info['minute']} "
            f"({score_info['score']}) ({m['stage']})"
        )
        if patch_ics(m["uid"], summary, state):
            log(f"  updated: {summary}")
            any_updated = True
        else:
            log(f"  unchanged: {summary}")

    return any_updated


# ── Main loop ─────────────────────────────────────────────────────────────────

def main() -> None:
    log("=== daemon starting ===")

    # If a previous run crashed mid-rebase, abort it so git is usable
    rebase_dir = HERE / ".git" / "rebase-merge"
    rebase_apply = HERE / ".git" / "rebase-apply"
    if rebase_dir.exists() or rebase_apply.exists():
        log("stale rebase detected — aborting")
        git("rebase", "--abort")
        log("rebase aborted, continuing")

    while True:
        today = datetime.now(timezone.utc).date()
        if today > TOURNAMENT_END:
            log("tournament over — daemon exiting")
            sys.exit(0)

        try:
            updated = poll()
            if updated:
                pushed = commit_and_push()
                log(f"push: {'ok' if pushed else 'nothing to push'}")
            interval = POLL_INTERVAL_LIVE if find_live(read_schedule()) else POLL_INTERVAL_IDLE
        except Exception as e:
            log(f"ERROR: {e}")
            interval = POLL_INTERVAL_IDLE

        time.sleep(interval)


if __name__ == "__main__":
    main()
