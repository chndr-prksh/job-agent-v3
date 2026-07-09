"""scripts/run_one.py — fetch a URL, run the full pipeline, dump results.

Usage:
    python scripts/run_one.py "https://jobs.ashbyhq.com/Jerry.ai/2f9c37f6-2a97-4033-92d1-6ecf4b494c48"

What it does:
1. Fetches the JD via Playwright
2. Inserts into your `jobs` table (skipped if already there)
3. Runs ranker → writes job_matches row + Telegram ping
4. Runs tailor → writes resume_versions row, drops tailored .docx in ~/Downloads/
5. Runs planner → writes application_plans row + Telegram ping

Tailored resume lands at:
    ~/Downloads/{Company}_{Role}_{YYYY-MM-DD}.docx

Real Supabase writes require SUPABASE_URL + SUPABASE_SERVICE_KEY in your .env.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Allow running from project root or scripts/
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from daemon import ranker, tailor, planner
from daemon.supabase_client import load_profile, log_event, table, upsert_job_status
from daemon.url_ingest import fetch_job, detect_ats


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    url = sys.argv[1].strip()
    print(f"Fetching: {url}")

    fetched = fetch_job(url)
    if not fetched["ok"]:
        print(f"❌ Fetch failed: {fetched.get('error')}")
        return 2

    print(f"  title:    {fetched.get('title')}")
    print(f"  company:  {fetched.get('company')}")
    print(f"  location: {fetched.get('location')}")
    print(f"  ats:      {fetched.get('ats')}")
    print(f"  jd:       {len(fetched.get('jd_text', ''))} chars")

    # Try to insert (de-dup on apply_url)
    job_row = None
    try:
        existing = table("jobs").select("id,job_title,company_id").eq("apply_url", url).limit(1).execute()
        if existing.data:
            job_row = existing.data[0]
            print(f"  (existing job row: {job_row['id'][:8]})")
        else:
            import time
            ins = table("jobs").insert({
                "external_job_id": f"manual-{int(time.time()*1000)}",
                "job_title": fetched["title"] or "(untitled)",
                "apply_url": url,
                "description": fetched["jd_text"],
                "is_active": True,
                "is_us_job": True,
                "is_test_job": False,
                "city": fetched.get("location"),
            }).execute()
            inserted = (ins.data or [])
            if inserted:
                job_row = inserted[0]
                print(f"  (inserted new job row: {job_row['id'][:8]})")
            else:
                print("  (insert returned no data; using local fallback)")
                job_row = None
    except Exception as e:
        print(f"  (Supabase unavailable — running in local-only mode): {e}")
        job_row = {
            "id": "local-test-job-id",
            "external_job_id": "manual-test",
            "job_title": fetched["title"],
            "company_id": None,
            "apply_url": url,
            "description": fetched["jd_text"],
            "is_active": True,
            "city": fetched.get("location"),
        }

    if job_row is None:
        print("  (Supabase unavailable — running in local-only mode)")
        job_row = {
            "id": "local-test-job-id",
            "external_job_id": "manual-test",
            "job_title": fetched["title"],
            "company_id": None,
            "apply_url": url,
            "description": fetched["jd_text"],
            "is_active": True,
            "city": fetched.get("location"),
        }

    # Load profile
    try:
        profile = load_profile()
    except Exception as e:
        print(f"  (profile load failed: {e})")
        profile = None

    if not profile:
        print("❌ No candidate_profile row found. Fill it in Supabase first.")
        return 3

    job_row["job_title"] = job_row.get("job_title") or fetched.get("title")
    job_row["description"] = job_row.get("description") or fetched.get("jd_text")

    # Run ranker
    print("\n--- ranker ---")
    r = ranker.rank_job(job_row, profile)
    print(f"  score:        {r.get('relevance_score')}")
    print(f"  should_apply: {r.get('should_apply')}")
    print(f"  matched:      {r.get('matched_skills')}")
    print(f"  reasoning:    {r.get('reasoning')}")
    print(f"  cost:         ${r.get('_llm_cost_usd')}")

    if not r.get("should_apply"):
        print("\nSkipped: low score.")
        return 0

    # Run tailor
    print("\n--- tailor ---")
    t = tailor.tailor_resume(job_row, profile, keywords=r.get("top_keywords"))
    print(f"  ok:           {t.get('ok')}")
    print(f"  file:         {t.get('file_path')}")
    print(f"  notes:        {t.get('tailoring_notes')}")
    print(f"  cost:         ${t.get('llm_cost_usd')}")

    # Run planner
    print("\n--- planner ---")
    p = planner.plan_job(job_row, profile, resume_path=t.get("file_path"))
    print(f"  ok:        {p.get('ok')}")
    print(f"  ats:       {p.get('ats')}")
    print(f"  fields:    {len(p.get('fields') or [])}")
    for f in (p.get("fields") or []):
        needs = " ⚠️" if f.get("needs_escalation") else ""
        print(f"    • {f.get('field_name')[:30]:30} ({f.get('field_type')}) → {str(f.get('value'))[:50]!r}{needs}")
    print(f"  cost:      ${p.get('llm_cost_usd')}")

    # Final summary
    total = (r.get("_llm_cost_usd", 0) + t.get("llm_cost_usd", 0) + p.get("llm_cost_usd", 0))
    print(f"\n✅ Done. Total cost: ${total:.4f}")
    if t.get("file_path"):
        print(f"\n📄 Tailored resume: {t['file_path']}")
        print(f"\n👉 Next: open the apply URL in Chrome with the extension loaded.")
        print(f"   {url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())