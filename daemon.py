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
    find_live,
    patch_ics,
    read_schedule,
    save_final_score,
    fetch_espn,
    fetch_live,
    find_espn_event,
    find_fd_match,
    extract_score_espn,
    extract_score_fd,
    _trophy_match,
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
    git("pull", "--rebase")

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
            pull = git("pull", "--rebase")
            if pull.returncode != 0:
                log(f"pull failed: {pull.stderr.strip()}")
            push = git("push")
            if push.returncode == 0:
                return True
            log(f"push attempt {attempt} failed: {push.stderr.strip()}")
            time.sleep(attempt * 2)

    log("all push attempts failed")
    return False


# ── One poll cycle ────────────────────────────────────────────────────────────

def poll() -> tuple[bool, set[str]]:
    """Run one check cycle. Returns (ics_updated, newly_ft_uids)."""
    matches = read_schedule()
    live = find_live(matches)

    if not live:
        return False, set()

    log(f"Live: {[m['match'] for m in live]}")

    # Fetch ESPN first — covers featured matches with live minute data
    try:
        espn_events = fetch_espn()
    except Exception as e:
        log(f"ESPN fetch failed: {e}")
        espn_events = []

    # football-data.org fetched lazily as fallback
    fd_matches: list | None = None

    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    any_updated = False
    newly_ft: set[str] = set()

    for m in live:
        score_info = None
        source = None

        ev = find_espn_event(m["match"], espn_events)
        if ev:
            score_info = extract_score_espn(ev, m["match"])
            source = "ESPN"

        if not score_info:
            if fd_matches is None:
                try:
                    fd_matches = fetch_live()
                except Exception as e:
                    log(f"  football-data.org fetch failed: {e}")
                    fd_matches = []
            fd = find_fd_match(m["match"], fd_matches)
            if fd:
                score_info = extract_score_fd(fd, m["match"], m["kickoff"])
                source = "football-data.org"

        if not score_info:
            log(f"  not found on any source: {m['match']}")
            continue

        if score_info["minute"] in ("FT", "FT-Pens"):
            save_final_score(m["uid"], score_info["score"])
            log(f"  FT saved: {m['match']} ({score_info['score']})")
            newly_ft.add(m["uid"])

        match_display = _trophy_match(m["match"], score_info["score"]) \
            if score_info["minute"] in ("FT", "FT-Pens") else m["match"]
        summary = (
            f"{match_display} {score_info['minute']} "
            f"({score_info['score']}) ({m['stage']})"
        )
        if patch_ics(m["uid"], summary, state):
            log(f"  [{source}] updated: {summary}")
            any_updated = True
        else:
            log(f"  [{source}] unchanged: {summary}")

    return any_updated, newly_ft


# ── Main loop ─────────────────────────────────────────────────────────────────

def run_post_match_update() -> None:
    """Run update_xlsx.py + generate_ics.py to pull in final scores and bracket."""
    log("Running post-match update_xlsx + generate_ics...")
    try:
        r = subprocess.run(
            [sys.executable, str(HERE / "update_xlsx.py")],
            capture_output=True, text=True, cwd=str(HERE),
        )
        if r.returncode != 0:
            log(f"  update_xlsx failed: {r.stderr.strip()}")
            return
        log("  update_xlsx OK")
        r = subprocess.run(
            [sys.executable, str(HERE / "generate_ics.py")],
            capture_output=True, text=True, cwd=str(HERE),
        )
        if r.returncode != 0:
            log(f"  generate_ics failed: {r.stderr.strip()}")
            return
        log("  generate_ics OK")
        git("add",
            "2026_FIFA_World_Cup_Schedule.xlsx",
            "scores.json",
            "2026_FIFA_World_Cup.ics", "state.json",
            "2026_FIFA_World_Cup_no_live_scores.ics", "state_no_scores.json",
        )
        if git("diff", "--cached", "--quiet").returncode != 0:
            git("config", "user.name", "brewingithot")
            git("config", "user.email", "brewingithot@users.noreply.github.com")
            git("commit", "-m", "auto: post-match schedule update")
            log("  committed post-match update")
    except Exception as e:
        log(f"  post-match update error: {e}")


def main() -> None:
    log("=== daemon starting ===")

    # If a previous run crashed mid-rebase, abort it so git is usable
    rebase_dir = HERE / ".git" / "rebase-merge"
    rebase_apply = HERE / ".git" / "rebase-apply"
    if rebase_dir.exists() or rebase_apply.exists():
        log("stale rebase detected — aborting")
        git("rebase", "--abort")
        log("rebase aborted, continuing")

    # Track UIDs whose post-match update has already been fired this session
    post_match_done: set[str] = set()

    while True:
        today = datetime.now(timezone.utc).date()
        if today > TOURNAMENT_END:
            log("tournament over — daemon exiting")
            sys.exit(0)

        try:
            updated, newly_ft = poll()
            if updated:
                pushed = commit_and_push()
                log(f"push: {'ok' if pushed else 'FAILED — check token/upstream'}")

            # Fire update_xlsx immediately when FT is detected for new matches
            new_ft = newly_ft - post_match_done
            if new_ft:
                log(f"FT detected for {len(new_ft)} match(es) — running post-match update")
                run_post_match_update()
                pushed = commit_and_push()
                log(f"post-match push: {'ok' if pushed else 'FAILED'}")
                post_match_done.update(new_ft)

            schedule = read_schedule()
            interval = POLL_INTERVAL_LIVE if find_live(schedule) else POLL_INTERVAL_IDLE
        except Exception as e:
            log(f"ERROR: {e}")
            interval = POLL_INTERVAL_IDLE

        time.sleep(interval)


if __name__ == "__main__":
    main()
