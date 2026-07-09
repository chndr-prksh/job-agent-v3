"""Ranker: scores a job against candidate_profile, writes to job_matches.

Single Claude (Haiku) call per job. ~$0.005/job.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from daemon import config, llm
from daemon.supabase_client import load_profile, log_event, upsert_job_status, upsert_match


log = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are an expert recruiter. Score how well a candidate matches a job description.

Given:
1. The candidate's full profile (work history, skills, summary, preferences)
2. A job description (title, company, full text, listed skills/keywords)

Return STRICT JSON only, matching this schema:
{
  "relevance_score": 0.0-1.0,
  "seniority_level": "junior" | "mid" | "senior" | "staff" | "principal" | "unknown",
  "matched_skills": ["skill1", "skill2", ...],
  "missing_skills": ["skill3", ...],
  "reasoning": "1-2 sentences",
  "should_apply": true | false,
  "top_keywords": ["top 8 keywords/themes for tailoring"]
}

Scoring:
- 0.0-0.3: clear mismatch
- 0.3-0.5: weak match
- 0.5-0.7: solid match
- 0.7-0.9: strong match
- 0.9-1.0: exceptional match

should_apply = false if score < 0.4 OR seniority clearly above candidate's years.

top_keywords: 8 most distinctive keywords/themes from the JD that would be useful for tailoring.
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
        log.warning("ranker JSON parse failed")
        return {
            "relevance_score": 0.5,
            "seniority_level": "unknown",
            "matched_skills": [],
            "missing_skills": [],
            "reasoning": "Failed to parse ranker output",
            "should_apply": True,
            "top_keywords": [],
        }


def rank_job(job: dict, profile: dict) -> dict[str, Any]:
    """Score a job. Writes to job_matches + sets job_status."""
    job_id = job["id"]
    desc_text = job.get("description") or ""
    if not desc_text:
        log.warning("Job %s has no description; cannot rank", job_id[:8])
        return {"error": "no description"}

    upsert_job_status(job_id, "ranking")
    log_event(job_id, "ranking_started")

    user_prompt = (
        f"Candidate profile:\n{json.dumps(profile, indent=2, default=str)}\n\n"
        f"Job description:\n{desc_text[:6000]}\n\n"
        f"Return strict JSON only."
    )

    try:
        resp = llm.call(
            model=config.CLAUDE_MODEL_RANK,
            system=SYSTEM_PROMPT,
            user=user_prompt,
            max_tokens=1024,
        )
    except llm.BudgetExceeded as e:
        log.warning("Skipping rank for %s: %s", job_id[:8], e)
        return {"error": str(e)}
    except Exception as e:
        log.exception("ranker call failed: %s", e)
        return {"error": str(e)}

    parsed = _safe_json_parse(resp["text"])

    upsert_match(
        job_id=job_id,
        score=float(parsed.get("relevance_score", 0.5)),
        seniority=parsed.get("seniority_level", "unknown"),
        matched=parsed.get("matched_skills", []),
        missing=parsed.get("missing_skills", []),
        reasoning=parsed.get("reasoning", ""),
    )
    upsert_job_status(
        job_id,
        "ranked",
        message=parsed.get("reasoning", "")[:300],
    )
    log_event(job_id, "ranked", {
        "score": parsed.get("relevance_score"),
        "should_apply": parsed.get("should_apply"),
        "llm_cost_usd": resp["cost_usd"],
    })

    parsed["_llm_cost_usd"] = resp["cost_usd"]
    parsed["_input_tokens"] = resp["input_tokens"]
    parsed["_output_tokens"] = resp["output_tokens"]
    return parsed