"""Supabase client wrapper — schema-aware (matches your real tables).

Tables used (from your schema):
- companies              (your scraper writes here)
- jobs                   (your scraper writes here; daemon reads)
- candidate_profile      (1 row, daemon reads)
- resumes                (base + tailored; is_base flag)
- resume_versions        (per-job tailored resume)
- question_bank          (deterministic Q→A brain)
- job_matches            (ranker writes here)
- application_plans      (planner writes here)
- applications           (extension writes here on submit)
- ats_accounts           (Workday/iCIMS logins; vault_id refers to Anthropic Vault)
- ats_field_templates    (self-healing selector cache)
- captcha_incidents       (we write on detection)
- application_stats      (we update on submit/interview/rejection)
- telegram_poll_state    (use existing row, just update last_update_id)
- application_feedback   (write on 👍/🚩)
- scrape_logs            (read for context, never write)

Tables we ADD (not in your schema):
- daemon_heartbeat       (liveness)
- apply_log              (audit trail)
- job_status             (pipeline progress, separate from jobs.is_active)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from supabase import Client, create_client

from daemon import config


log = logging.getLogger(__name__)


_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)
        log.info("Supabase client initialized for %s", config.SUPABASE_URL[:40])
    return _client


def table(name: str):
    return get_client().table(name)


def storage():
    return get_client().storage


# ---------- logging ----------

def log_event(job_id: str, event: str, detail: dict[str, Any] | None = None) -> None:
    """Write a row to apply_log."""
    try:
        table("apply_log").insert({
            "job_id": job_id,
            "event": event,
            "detail": detail or {},
        }).execute()
    except Exception as e:
        log.warning("apply_log insert failed: %s", e)


def heartbeat(pid: int) -> None:
    """Update daemon_heartbeat row."""
    try:
        table("daemon_heartbeat").upsert({
            "id": 1,
            "last_seen": datetime.now(timezone.utc).isoformat(),
            "pid": pid,
        }).execute()
    except Exception as e:
        log.warning("heartbeat failed: %s", e)


# ---------- reads ----------

def load_profile() -> dict | None:
    try:
        r = table("candidate_profile").select("*").limit(1).execute()
        return (r.data or [None])[0]
    except Exception as e:
        log.warning("load_profile failed: %s", e)
        return None


def load_question_bank() -> dict[str, str]:
    """Return question_key → answer_value dict."""
    try:
        r = table("question_bank").select("question_key,answer_value").execute()
        out = {}
        for row in (r.data or []):
            if row.get("question_key") and row.get("answer_value"):
                out[row["question_key"]] = row["answer_value"]
        return out
    except Exception as e:
        log.warning("load_question_bank failed: %s", e)
        return {}


def get_jobs_needing_rank(limit: int = 5) -> list[dict]:
    """Jobs that don't have a row in job_matches yet."""
    try:
        # Subquery via join — easier in raw SQL but we'll fake it via two queries
        all_jobs = table("jobs").select("id,external_job_id,job_title,description,apply_url,company_id,skills,keywords,posted_date,first_seen").eq("is_active", True).order("first_seen", desc=False).limit(limit * 3).execute()
        scored = table("job_matches").select("job_id").execute()
        scored_ids = {r["job_id"] for r in (scored.data or [])}
        return [j for j in (all_jobs.data or []) if j["id"] not in scored_ids][:limit]
    except Exception as e:
        log.warning("get_jobs_needing_rank failed: %s", e)
        return []


def get_jobs_needing_tailor(limit: int = 5) -> list[dict]:
    """Jobs that have job_matches.score >= threshold but no resume_versions row yet."""
    try:
        eligible = table("job_matches").select("job_id,relevance_score").gte("relevance_score", config.MIN_RELEVANCE_SCORE).execute()
        eligible_ids = [r["job_id"] for r in (eligible.data or [])]
        if not eligible_ids:
            return []
        tailored = table("resume_versions").select("job_id").in_("job_id", eligible_ids).execute()
        tailored_ids = {r["job_id"] for r in (tailored.data or [])}
        pending_ids = [jid for jid in eligible_ids if jid not in tailored_ids]
        if not pending_ids:
            return []
        rows = table("jobs").select("id,job_title,description,company_id,apply_url,skills,keywords,posted_date").in_("id", pending_ids).limit(limit).execute()
        return rows.data or []
    except Exception as e:
        log.warning("get_jobs_needing_tailor failed: %s", e)
        return []


def get_jobs_needing_plan(limit: int = 3) -> list[dict]:
    """Jobs that have resume_versions but no application_plans row yet."""
    try:
        tailored = table("resume_versions").select("job_id").execute()
        tailored_ids = [r["job_id"] for r in (tailored.data or [])]
        if not tailored_ids:
            return []
        plans = table("application_plans").select("job_id").in_("job_id", tailored_ids).execute()
        planned_ids = {r["job_id"] for r in (plans.data or [])}
        pending_ids = [jid for jid in tailored_ids if jid not in planned_ids]
        if not pending_ids:
            return []
        rows = table("jobs").select("id,job_title,description,apply_url,company_id").in_("id", pending_ids).limit(limit).execute()
        return rows.data or []
    except Exception as e:
        log.warning("get_jobs_needing_plan failed: %s", e)
        return []


# ---------- writes ----------

def upsert_match(job_id: str, score: float, seniority: str, matched: list, missing: list, reasoning: str) -> None:
    try:
        table("job_matches").upsert({
            "job_id": job_id,
            "relevance_score": score,
            "seniority_level": seniority,
            "matched_skills": matched,
            "missing_skills": missing,
            "reasoning": reasoning,
            "scored_at": "now()",
        }, on_conflict="job_id").execute()
    except Exception as e:
        log.warning("upsert_match failed: %s", e)


def upsert_base_resume(profile_id: str, file_url: str, version_label: str) -> str | None:
    """Ensure a base resume row exists. Returns resume id (uuid)."""
    try:
        r = table("resumes").select("id").eq("is_base", True).limit(1).execute()
        if r.data:
            return r.data[0]["id"]
        ins = table("resumes").insert({
            "version_label": version_label,
            "file_url": file_url,
            "file_name": Path(file_url).name if file_url.startswith("/") else file_url,
            "is_base": True,
        }).execute()
        if ins.data:
            return ins.data[0]["id"]
    except Exception as e:
        log.warning("upsert_base_resume failed: %s", e)
    return None


def create_tailored_version(
    job_id: str,
    base_resume_id: str | None,
    tailored_resume_path: str,
    tailored_resume_url: str | None,
    tailoring_notes: str,
) -> str | None:
    """Insert a resume_versions row. Returns the new version id."""
    try:
        ins = table("resume_versions").insert({
            "job_id": job_id,
            "base_resume_id": base_resume_id,
            "file_url": tailored_resume_url or tailored_resume_path,
            "tailoring_notes": tailoring_notes,
        }).execute()
        if ins.data:
            return ins.data[0]["id"]
    except Exception as e:
        log.warning("create_tailored_version failed: %s", e)
    return None


def upsert_job_status(job_id: str, status: str, message: str | None = None) -> None:
    """Pipeline progress (separate from the jobs table which is the listing truth)."""
    try:
        table("job_status").upsert({
            "job_id": job_id,
            "pipeline_status": status,
            "status_message": message,
        }, on_conflict="job_id").execute()
    except Exception as e:
        log.warning("upsert_job_status failed: %s", e)


def upsert_application_plan(
    job_id: str,
    ats: str,
    fields: list[dict],
    resume_path: str,
    status: str = "ready",
) -> None:
    try:
        table("application_plans").upsert({
            "job_id": job_id,
            "ats": ats,
            "fields": fields,
            "resume_path": resume_path,
            "status": status,
        }, on_conflict="job_id").execute()
    except Exception as e:
        log.warning("upsert_application_plan failed: %s", e)


def upsert_ats_field(ats: str, field_name: str, selector: str, field_type: str, notes: str | None = None) -> None:
    """Self-healing selector cache."""
    try:
        # Increment verified_count if exists, else insert
        existing = table("ats_field_templates").select("id,verified_count").eq("ats", ats).eq("field_name", field_name).limit(1).execute()
        if existing.data:
            row = existing.data[0]
            table("ats_field_templates").update({
                "selector": selector,
                "field_type": field_type,
                "verified_count": (row.get("verified_count") or 0) + 1,
                "last_verified_at": "now()",
                "notes": notes,
            }).eq("id", row["id"]).execute()
        else:
            table("ats_field_templates").insert({
                "ats": ats,
                "field_name": field_name,
                "selector": selector,
                "field_type": field_type,
                "notes": notes,
            }).execute()
    except Exception as e:
        log.warning("upsert_ats_field failed: %s", e)


def record_captcha(company_id: str | None, ats: str, job_id: str | None) -> None:
    if not company_id:
        return
    try:
        table("captcha_incidents").insert({
            "company_id": company_id,
            "ats": ats,
            "job_id": job_id,
        }).execute()
    except Exception as e:
        log.warning("record_captcha failed: %s", e)


def bump_application_stats(field: str, by: int = 1) -> None:
    """Increment today's snapshot row. field ∈ {applied,interview_call,interviewing,rejection}."""
    from datetime import date
    if field not in ("applied", "interview_call", "interviewing", "rejection"):
        return
    try:
        # Try upsert using the schema's PK on snapshot_date
        today = date.today().isoformat()
        # Postgres upsert via raw: get current value, increment, update. Simpler: rpc-less path
        # is a two-step read+write inside a loop, but we can use the PostgREST ON CONFLICT.
        # Use supabase-py rpc-less mechanism: try insert; on conflict update.
        get_client().rpc  # noqa
        # PostgREST supports upsert via Prefer: resolution=merge-duplicates
        # supabase-py passes that through headers
        r = table("application_stats").select(field).eq("snapshot_date", today).execute()
        if r.data:
            cur = (r.data[0].get(field) or 0)
            table("application_stats").update({field: cur + by}).eq("snapshot_date", today).execute()
        else:
            table("application_stats").insert({"snapshot_date": today, field: by}).execute()
    except Exception as e:
        log.warning("bump_application_stats failed: %s", e)