"""Planner: generates application_plans per job (blueprint's path B).

One Claude (Sonnet) call per job. Reads:
- Job's apply_url + description + listed skills/keywords
- candidate_profile (for filling values)
- question_bank (for deterministic Q→A mapping)
- ats_field_templates (self-healing selector cache — pre-fill selectors from
  previous successful runs to make this more grounded)

Writes:
- application_plans row with `fields` JSONB ([{field_name, field_type, label,
  selector, value, needs_escalation}, ...])
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from daemon import config, llm
from daemon.supabase_client import (
    load_question_bank,
    log_event,
    table,
    upsert_application_plan,
    upsert_job_status,
)


log = logging.getLogger(__name__)


PLANNER_SYSTEM = """You are an expert job-application planner agent.

You are given:
1. A job's metadata (URL, ATS detected, title, company, description, known skills)
2. The candidate's full profile
3. The candidate's question bank (deterministic Q→A answers)
4. Optionally: known selectors that worked in the past for this ATS (ats_field_templates)

Your job: produce a FIELD-BY-FIELD PLAN for the apply form. For each field,
specify what to type/select/click. Be CONSERVATIVE — only include fields you're
confident about. Anything that requires real human judgment (visa status, salary
expectation) should be marked needs_escalation=true.

CRITICAL: NEVER INVENT FACTS. Only use values from the candidate's profile
or question bank. If a value is missing, mark needs_escalation=true AND set
"value" to null (NOT the string "None", NOT an empty string). The human will fill
it manually. Empty string vs null: use null for needs_escalation=true; use empty
string only for optional fields you intend to leave blank.

Return STRICT JSON only:
{
  "ats": "greenhouse" | "lever" | "ashby" | ... | "unknown",
  "fields": [
    {
      "field_name": "first_name",
      "field_type": "text" | "email" | "tel" | "url" | "select" | "radio" | "checkbox" | "file" | "textarea",
      "label": "First Name",            // the visible label or question text
      "selector": "input[name='first_name']",
      "value": "Jane",                  // or null if needs_escalation
      "needs_escalation": false,
      "notes": "auto-resolved from profile"
    },
    ...
  ],
  "resume_path": "/Users/jane/Downloads/Toast_DPM.docx",  // or null
  "ats_notes": "any ATS-specific gotchas (e.g. multi-step, captcha, file picker quirks)"
}

Selector tips per ATS:
- Greenhouse: stable — input[name='first_name'], select[name='question_X']
- Lever: name-based 'name', 'email', 'phone', 'location', 'org', 'urls[LinkedIn]', 'resume', plus UUID-named custom questions (cards)
- Ashby: input[name='_systemfield_name'], '_systemfield_email', custom questions have UUID field names
- Workday: [data-automation-id='legalNameSection.firstName'] style — DO NOT include Workday in plan (it requires account-creation first)
- iCIMS: iCIMS-hosted pages have heavy custom markup; flag ats_notes if encountered
- Eightfold: input[name='Contact_Information_firstname'] style
- Rippling: field names are obfuscated hashes; resolve by placeholder
"""


def _safe_json_parse(raw: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", (raw or "").strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        log.error("planner JSON parse failed")
        return {"ats": "unknown", "fields": [], "ats_notes": "parse failed"}


def _detect_ats_from_url(url: str) -> str:
    if not url:
        return "unknown"
    h = url.lower()
    if "greenhouse.io" in h or "gh_jid=" in h: return "greenhouse"
    if "lever.co" in h: return "lever"
    if "ashbyhq.com" in h: return "ashby"
    if "myworkdayjobs.com" in h: return "workday"
    if "icims.com" in h: return "icims"
    if "eightfold.ai" in h: return "eightfold"
    if "rippling.com" in h: return "rippling"
    if "smartrecruiters.com" in h: return "smartrecruiters"
    return "unknown"


def _load_ats_templates(ats: str) -> list[dict]:
    """Known-good selectors from past runs."""
    try:
        r = table("ats_field_templates").select("*").eq("ats", ats).execute()
        return r.data or []
    except Exception:
        return []


def plan_job(job: dict, profile: dict, resume_path: str | None = None) -> dict[str, Any]:
    """Plan the apply flow for one job. Writes application_plans row."""
    job_id = job["id"]
    apply_url = job.get("apply_url") or ""

    ats = _detect_ats_from_url(apply_url)
    qb = load_question_bank()
    templates = _load_ats_templates(ats)

    upsert_job_status(job_id, "planning")
    log_event(job_id, "planning_started", {"ats": ats})

    user_prompt = (
        f"ATS detected: {ats}\n"
        f"Job title: {job.get('job_title', '?')}\n"
        f"Company: (see job)\n"
        f"Apply URL: {apply_url}\n"
        f"Job description: {(job.get('description') or '')[:6000]}\n"
        f"Listed skills from job: {json.dumps(job.get('skills') or [])}\n\n"
        f"Candidate profile:\n{json.dumps(profile, indent=2, default=str)}\n\n"
        f"Question bank (use these answers directly):\n{json.dumps(qb, indent=2)}\n\n"
        f"Known-good selectors for this ATS (optional reference):\n{json.dumps(templates[:20], default=str)}\n\n"
        f"Resume path:\n{resume_path or 'not generated yet'}\n\n"
        f"Return strict JSON only."
    )

    try:
        resp = llm.call(
            model=config.CLAUDE_MODEL_PLAN,
            system=PLANNER_SYSTEM,
            user=user_prompt,
            max_tokens=4096,
        )
    except llm.BudgetExceeded as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        log.exception("planner call failed: %s", e)
        return {"ok": False, "error": str(e)}

    parsed = _safe_json_parse(resp["text"])
    fields = parsed.get("fields") or []
    if not isinstance(fields, list):
        fields = []

    # Post-process: convert literal "None" / "N/A" values into null + escalation
    for f in fields:
        v = f.get("value")
        if isinstance(v, str) and v.strip().lower() in ("none", "n/a", "na", "null", "-"):
            f["value"] = None
            f["needs_escalation"] = True

    # Persist
    upsert_application_plan(
        job_id=job_id,
        ats=ats,
        fields=fields,
        resume_path=resume_path or "/tmp/base_resume.pdf",
        status="ready",
    )
    upsert_job_status(job_id, "planned", message=f"{len(fields)} fields planned")
    log_event(job_id, "planned", {
        "ats": ats,
        "fields_count": len(fields),
        "llm_cost_usd": resp["cost_usd"],
    })

    return {
        "ok": True,
        "ats": ats,
        "fields": fields,
        "llm_cost_usd": resp["cost_usd"],
    }