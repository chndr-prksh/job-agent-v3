"""Orchestrator — the main pipeline.

Pipeline per job:
  1. Rank   → job_matches row (if missing)
  2. Tailor → resume_versions row (if missing, score >= threshold)
  3. Plan   → application_plans row (if missing, resume_versions exists)

Each step is idempotent: re-running is safe, the missing-row queries select
the next job that needs work.

Two ways jobs enter:
- Your scraper pipeline populates companies + jobs + scrape_logs.
- Telegram URL paste inserts directly into jobs (NULL company_id).
"""
from __future__ import annotations

import logging
import os
import signal
import threading
import time
from datetime import datetime, timezone

from daemon import config, planner, ranker, tailor
from daemon.supabase_client import (
    get_client,
    get_jobs_needing_plan,
    get_jobs_needing_rank,
    get_jobs_needing_tailor,
    heartbeat,
    load_profile,
    log_event,
    table,
)
from daemon.telegram_bot import (
    daily_summary,
    notify_blocked,
    notify_error,
    notify_planned,
    notify_ranked,
    notify_tailored,
    run_listener as run_telegram_listener,
)
from daemon.gmail_watcher import run_watcher as run_gmail_watcher


log = logging.getLogger(__name__)


def _is_quiet_hours() -> bool:
    h = datetime.now().hour
    return not (config.ACTIVE_HOURS_START <= h < config.ACTIVE_HOURS_END)


def _process_ranking_batch(profile: dict) -> None:
    jobs = get_jobs_needing_rank(limit=5)
    for job in jobs:
        try:
            ranking = ranker.rank_job(job, profile)
            if ranking.get("error"):
                continue
            notify_ranked(job, ranking)
        except Exception as e:
            log.exception("ranking failed for %s: %s", job["id"][:8], e)
            notify_error(job["id"], str(e))


def _process_tailoring_batch(profile: dict) -> None:
    jobs = get_jobs_needing_tailor(limit=3)
    for job in jobs:
        # Look up score + matched skills
        try:
            m = table("job_matches").select("matched_skills,relevance_score").eq("job_id", job["id"]).limit(1).execute()
            if not m.data:
                continue
            top_kw = (m.data[0].get("matched_skills") or [])[:10]
            score = m.data[0].get("relevance_score", 0) or 0
            if score < config.MIN_RELEVANCE_SCORE:
                continue
        except Exception:
            top_kw = []
        try:
            t = tailor.tailor_resume(job, profile, keywords=top_kw)
            if not t.get("ok"):
                continue
            notify_tailored(job, t)
        except Exception as e:
            log.exception("tailoring failed for %s: %s", job["id"][:8], e)
            notify_error(job["id"], str(e))


def _process_planning_batch(profile: dict, candidate_id: str | None) -> None:
    jobs = get_jobs_needing_plan(limit=3)
    for job in jobs:
        # Look up the latest resume_version file_url
        try:
            rv = table("resume_versions").select("file_url").eq("job_id", job["id"]).order("created_at", desc=True).limit(1).execute()
            resume_path = (rv.data or [{}])[0].get("file_url")
        except Exception:
            resume_path = None
        try:
            plan = planner.plan_job(job, profile, resume_path=resume_path)
            if not plan.get("ok"):
                continue
            notify_planned(job, plan)
        except Exception as e:
            log.exception("planning failed for %s: %s", job["id"][:8], e)
            notify_error(job["id"], str(e))


def _reap_stuck_jobs() -> None:
    """Reset jobs stuck in a transient pipeline state."""
    cutoff = (datetime.now(timezone.utc) - __import__("datetime").timedelta(minutes=10)).isoformat()
    for stuck in ("ranking", "tailoring", "planning"):
        try:
            r = table("job_status").select("job_id,pipeline_status,updated_at").eq("pipeline_status", stuck).lt("updated_at", cutoff).execute()
            for js in (r.data or []):
                log.warning("Reaping stuck job %s (in %s since %s)", js["job_id"][:8], js["pipeline_status"], js["updated_at"])
                table("job_status").update({"pipeline_status": "queued", "status_message": "reaped after 10min stuck"}).eq("job_id", js["job_id"]).execute()
        except Exception as e:
            log.warning("reap %s: %s", stuck, e)


def main() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("Daemon v3 starting (pid=%d)", os.getpid())

    stop_event = threading.Event()
    def _on_signal(signum, frame):
        log.info("Signal %s; stopping", signum)
        stop_event.set()
    signal.signal(signal.SIGINT, _on_signal)
    signal.signal(signal.SIGTERM, _on_signal)

    try:
        get_client()
        log.info("Supabase OK")
    except Exception as e:
        log.error("Supabase init failed: %s", e)
        return

    profile = load_profile()
    if not profile:
        log.error("No candidate_profile row found. Insert one in Supabase first.")
        return
    if profile.get("full_name") in (None, "", "Your Name"):
        log.warning("candidate_profile looks unfilled — fill it in before running for real")

    # Telegram + Gmail threads
    threading.Thread(target=run_telegram_listener, args=(stop_event,), daemon=True, name="telegram").start()
    threading.Thread(target=run_gmail_watcher, args=(stop_event,), daemon=True, name="gmail").start()

    last_heartbeat = last_daily_summary = last_reap = 0.0
    while not stop_event.is_set():
        now = time.time()

        if now - last_heartbeat >= config.HEARTBEAT_INTERVAL_SECONDS:
            heartbeat(os.getpid())
            last_heartbeat = now

        hr = datetime.now().hour
        if now - last_daily_summary >= 3600 and hr == 9:
            try:
                daily_summary()
            except Exception as e:
                log.warning("daily summary: %s", e)
            last_daily_summary = now
        elif last_daily_summary == 0.0:
            last_daily_summary = now

        if now - last_reap >= 300:
            _reap_stuck_jobs()
            last_reap = now

        quiet = _is_quiet_hours()
        if not quiet:
            try:
                _process_ranking_batch(profile)
                _process_tailoring_batch(profile)
                _process_planning_batch(profile, str(profile.get("id") or ""))
            except Exception as e:
                log.exception("pipeline batch failed: %s", e)

        for _ in range(config.POLL_INTERVAL_SECONDS):
            if stop_event.is_set():
                break
            time.sleep(1)

    log.info("Daemon v3 stopped")