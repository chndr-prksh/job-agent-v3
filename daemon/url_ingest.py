"""url_ingest — one-off URL fetcher for jobs your scraper hasn't seen.

Used when a user pastes a URL via Telegram. Opens the URL in headless Chromium,
extracts title/company/location/JD, detects ATS, inserts into your `jobs` table.

This is a v3-specific convenience; your main scraper pipeline still does the
bulk of ingestion. URL ingest handles the "I found one on my own" case.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from playwright.sync_api import sync_playwright

from daemon.supabase_client import log_event, table, upsert_job_status


log = logging.getLogger(__name__)


# ATS detection — same patterns as the planner + career_page scraper
_ATS_PATTERNS = [
    ("greenhouse",      [r"boards\.greenhouse\.io",            r"boards-api\.greenhouse\.io",  r"gh_jid="]),
    ("lever",           [r"jobs\.lever\.co",                   r"api\.lever\.co"]),
    ("ashby",           [r"jobs\.ashbyhq\.com",                r"api\.ashbyhq\.com"]),
    ("workday",         [r"\.myworkdayjobs\.com",              r"wday/cxs/"]),
    ("icims",           [r"\.icims\.com",                      r"icims\.com/jobs/"]),
    ("eightfold",       [r"\.eightfold\.ai"]),
    ("rippling",        [r"ats\.rippling\.com"]),
    ("smartrecruiters", [r"jobs\.smartrecruiters\.com",        r"api\.smartrecruiters\.com"]),
]


def detect_ats(url: str, body: str = "") -> str:
    haystack = (url + " " + body[:5000]).lower()
    for name, patterns in _ATS_PATTERNS:
        for pat in patterns:
            if re.search(pat, haystack):
                return name
    return "unknown"


def fetch_job(url: str, *, timeout_ms: int = 30_000) -> dict[str, Any]:
    """Open a job URL and extract details. Returns dict with ok/error/fields/jd_text."""
    result: dict[str, Any] = {
        "ok": False,
        "error": None,
        "title": None,
        "company": None,
        "location": None,
        "ats": "unknown",
        "jd_text": "",
    }
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 900},
            )
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                page.wait_for_timeout(2500)

                # Title
                title = None
                for sel in ["h1", "[class*='job-title' i]", "[class*='JobTitle' i]"]:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0 and loc.is_visible():
                            t = loc.inner_text().strip()
                            if t:
                                title = t
                                break
                    except Exception:
                        continue
                result["title"] = title

                # Company — Ashby puts it in a "CompanyHeader" or h2 after title
                company = None
                for sel in [
                    "[class*='company' i]",
                    "h2",
                    "[data-test='company-name']",
                    "meta[property='og:site_name']",
                ]:
                    try:
                        if sel.startswith("meta"):
                            company = page.locator(sel).first.get_attribute("content")
                        else:
                            loc = page.locator(sel).first
                            if loc.count() > 0:
                                txt = loc.inner_text().strip()
                                if txt and len(txt) < 100 and txt != title:
                                    company = txt
                                    break
                    except Exception:
                        continue
                result["company"] = company

                # Location
                location = None
                for sel in ["[class*='location' i]", "[data-test='location']"]:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0:
                            txt = loc.inner_text().strip()
                            if txt and len(txt) < 200:
                                location = txt
                                break
                    except Exception:
                        continue
                result["location"] = location

                # JD body
                jd_text = ""
                for sel in [
                    "[class*='job-description' i]",
                    "[class*='description' i]",
                    "main",
                    "article",
                ]:
                    try:
                        loc = page.locator(sel).first
                        if loc.count() > 0:
                            jd_text = loc.inner_text().strip()
                            if len(jd_text) > 200:
                                break
                    except Exception:
                        continue
                if len(jd_text) < 200:
                    jd_text = page.evaluate("document.body.innerText")
                result["jd_text"] = (jd_text or "").strip()

                result["ats"] = detect_ats(url, result["jd_text"][:3000])
                result["ok"] = True
            except Exception as e:
                result["error"] = f"{type(e).__name__}: {e}"
            finally:
                try:
                    context.close()
                    browser.close()
                except Exception:
                    pass
    except Exception as e:
        result["error"] = f"playwright launch: {e}"
    return result


def ingest_url(url: str) -> dict[str, Any]:
    """Fetch a URL and insert into the jobs table. Returns {ok, job_id, error}."""
    # Dedup on apply_url
    try:
        existing = table("jobs").select("id").eq("apply_url", url).limit(1).execute()
        if existing.data:
            return {"ok": True, "job_id": existing.data[0]["id"], "deduped": True}
    except Exception as e:
        return {"ok": False, "error": f"dedup check failed: {e}"}

    fetched = fetch_job(url)
    if not fetched["ok"]:
        return {"ok": False, "error": fetched.get("error", "fetch failed")}

    # Derive a unique external_job_id
    import time
    ext_id = f"manual-{int(time.time() * 1000)}"

    row = {
        "external_job_id": ext_id,
        "job_title": fetched["title"] or "(untitled)",
        "apply_url": url,
        "description": fetched["jd_text"],
        "is_active": True,
        "is_us_job": True,
        "is_test_job": False,
        # location / company tracked in separate fields where possible
        "city": fetched["location"],
    }

    try:
        ins = table("jobs").insert(row).execute()
        if not ins.data:
            return {"ok": False, "error": "insert returned no data"}
        job_id = ins.data[0]["id"]
        upsert_job_status(job_id, "queued", message=f"ingested via url_ingest; ats={fetched['ats']}")
        log_event(job_id, "ingested_via_url", {"ats": fetched["ats"], "company": fetched.get("company"), "location": fetched.get("location")})
        return {
            "ok": True,
            "job_id": job_id,
            "title": fetched["title"],
            "company": fetched.get("company"),
            "location": fetched.get("location"),
            "ats": fetched["ats"],
            "jd_length": len(fetched["jd_text"]),
        }
    except Exception as e:
        return {"ok": False, "error": f"insert failed: {e}"}