"""Gmail IMAP watcher — read-only.

Polls for ATS confirmation emails. Matches to recent jobs and:
- Sets applications.status = 'submitted' (or 'interviewing' / 'rejected')
- Bumps application_stats snapshot
- Sends Telegram ping
"""
from __future__ import annotations

import imaplib
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from email import message_from_bytes
from email.header import decode_header, make_header
from typing import Any

from daemon import config
from daemon.supabase_client import (
    bump_application_stats,
    log_event,
    table,
    upsert_job_status,
)


log = logging.getLogger(__name__)


_CONFIRMATION_PATTERNS = [
    r"thanks for applying",
    r"thank you for applying",
    r"we (?:have |'ve )received your application",
    r"your application (?:has been|was) (?:received|submitted)",
    r"application confirmation",
    r"successfully (?:applied|submitted)",
    r"we received your (?:resume|application)",
]

_INTERVIEW_PATTERNS = [
    r"(?:would like|like to) (?:schedule|set up) (?:a|an) (?:interview|chat|call)",
    r"next steps",
    r"phone screen",
    r"interview (?:invitation|request|scheduling)",
]

_REJECTION_PATTERNS = [
    r"we('ve| have) decided not to move forward",
    r"we will not be (?:moving forward|proceeding)",
    r"unfortunately.*not (?:moving forward|a fit)",
    r"position has been filled",
    r"we('ve| have) decided to pursue other candidates",
]


def _decode_subject(raw: str) -> str:
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw or ""


def _extract_body(msg) -> str:
    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if ctype == "text/plain":
                try:
                    body = part.get_payload(decode=True).decode(errors="ignore")
                    break
                except Exception:
                    continue
            elif ctype == "text/html" and not body:
                try:
                    body = part.get_payload(decode=True).decode(errors="ignore")
                except Exception:
                    continue
    else:
        try:
            body = msg.get_payload(decode=True).decode(errors="ignore") or ""
        except Exception:
            body = ""
    if "<" in body and ">" in body:
        body = re.sub(r"<[^>]+>", " ", body)
    return re.sub(r"\s+", " ", body).strip()[:4000]


def _classify(subject: str, body: str) -> str | None:
    text = f"{subject}\n{body}".lower()
    if any(re.search(p, text) for p in _CONFIRMATION_PATTERNS):
        return "submitted"
    if any(re.search(p, text) for p in _INTERVIEW_PATTERNS):
        return "interview"
    if any(re.search(p, text) for p in _REJECTION_PATTERNS):
        return "rejection"
    return None


def _connect():
    if not config.GMAIL_ADDRESS or not config.GMAIL_APP_PASSWORD:
        return None
    try:
        imap = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        imap.login(config.GMAIL_ADDRESS, config.GMAIL_APP_PASSWORD)
        imap.select("INBOX")
        return imap
    except Exception as e:
        log.warning("Gmail connect failed: %s", e)
        return None


def _find_job_for_email(subject: str, body: str) -> dict | None:
    """Match email to a recent application."""
    try:
        r = table("applications").select("job_id,status,applied_at").in_(
            "status", ["draft", "pending_review", "submitted"]
        ).order("applied_at", desc=True).limit(50).execute()
    except Exception:
        return None

    candidates = r.data or []
    text = f"{subject}\n{body}".lower()
    best = None
    for app in candidates:
        # Look up the job for this app
        j = table("jobs").select("id,job_title,company_id").eq("id", app["job_id"]).limit(1).execute()
        if not j.data:
            continue
        job = j.data[0]
        # Match by title tokens
        title_tokens = [t for t in re.findall(r"[a-z]{4,}", (job.get("job_title") or "").lower()) if t not in {"senior","junior","staff","principal","lead","head","director","manager"}]
        if any(t in text for t in title_tokens[:5]):
            if best is None or (app.get("applied_at") or "") > (best.get("applied_at") or ""):
                best = app
    return best


def _process_message(uid: bytes, raw: bytes) -> None:
    msg = message_from_bytes(raw)
    subject = _decode_subject(msg.get("Subject", ""))
    body = _extract_body(msg)
    classification = _classify(subject, body)
    if not classification:
        return

    app = _find_job_for_email(subject, body)
    if not app:
        log.info("No matching application for email '%s'", subject[:80])
        return

    job_id = app["job_id"]

    if classification == "submitted":
        # Update the most recent application row for this job
        r = table("applications").select("id").eq("job_id", job_id).order("created_at", desc=True).limit(1).execute()
        if r.data:
            table("applications").update({
                "status": "submitted",
                "submitted_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", r.data[0]["id"]).execute()
        bump_application_stats("applied")
        upsert_job_status(job_id, "submitted", message=f"email confirmation {datetime.now().isoformat()}")
        log_event(job_id, "confirmed_by_email", {"email_subject": subject[:120]})
        from daemon.telegram_bot import notify_submitted
        j = table("jobs").select("job_title").eq("id", job_id).limit(1).execute()
        notify_submitted({"title": (j.data or [{}])[0].get("job_title", "?")})

    elif classification == "interview":
        r = table("applications").select("id").eq("job_id", job_id).order("created_at", desc=True).limit(1).execute()
        if r.data:
            table("applications").update({"status": "interviewing"}).eq("id", r.data[0]["id"]).execute()
        bump_application_stats("interview_call")
        log_event(job_id, "interview_signal", {"email_subject": subject[:120]})

    elif classification == "rejection":
        r = table("applications").select("id").eq("job_id", job_id).order("created_at", desc=True).limit(1).execute()
        if r.data:
            table("applications").update({"status": "rejected"}).eq("id", r.data[0]["id"]).execute()
        bump_application_stats("rejection")
        log_event(job_id, "rejection_email", {"email_subject": subject[:120]})


def run_watcher(stop_event: threading.Event) -> None:
    log.info("Gmail watcher starting (every 60s)")
    last_uid: int | None = None
    while not stop_event.is_set():
        try:
            imap = _connect()
            if imap is None:
                time.sleep(60)
                continue
            try:
                since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%d-%b-%Y")
                typ, data = imap.search(None, f'(SINCE {since})')
                if typ == "OK" and data and data[0]:
                    uids = data[0].split()
                    for uid_bytes in reversed(uids):
                        try:
                            uid_int = int(uid_bytes)
                            if last_uid is not None and uid_int <= last_uid:
                                continue
                            typ2, msg_data = imap.fetch(uid_bytes, "(RFC822)")
                            if typ2 == "OK" and msg_data and msg_data[0]:
                                _process_message(uid_bytes, msg_data[0][1])
                        except Exception as e:
                            log.warning("msg process: %s", e)
                    if uids:
                        last_uid = max(int(u) for u in uids)
            finally:
                try:
                    imap.logout()
                except Exception:
                    pass
        except Exception as e:
            log.exception("gmail watcher: %s", e)
        for _ in range(60):
            if stop_event.is_set():
                break
            time.sleep(1)