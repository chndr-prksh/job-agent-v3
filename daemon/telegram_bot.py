"""Telegram bot — two-way.

- Outbound: notify on each pipeline step
- Inbound: URL paste adds a row to jobs (via the existing scraper is preferred,
  but in absence, the daemon can insert directly with manual company/job_title)
- Commands: /queue, /stats, /help, /skip, /reconsider
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any

import requests

from daemon import config
from daemon.supabase_client import table


log = logging.getLogger(__name__)


_API = "https://api.telegram.org/bot{token}/{method}"


def _send(text: str, *, reply_markup: dict | None = None) -> dict | None:
    # Resolve chat_id from existing telegram_poll_state row, or fall back
    chat_id = None
    try:
        r = table("telegram_poll_state").select("last_update_id").eq("id", 1).execute()
        # We don't store chat_id in your schema — recover from env or via update_id parsing
    except Exception:
        pass

    # Primary: read from .env TELEGRAM_CHAT_ID (recommended)
    chat_id = config.TELEGRAM_CHAT_ID or ""
    if not chat_id:
        log.warning("No TELEGRAM_CHAT_ID; can't send: %s", text[:80])
        return None

    payload: dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        r = requests.post(_API.format(token=config.TELEGRAM_BOT_TOKEN, method="sendMessage"),
                          json=payload, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        log.warning("Telegram send failed: %s", e)
        return None


def _keyboard(text: str, buttons: list[list[dict]]) -> None:
    _send(text, reply_markup={"inline_keyboard": buttons})


# ---------- outbound ----------

def notify_ranked(job: dict, ranking: dict) -> None:
    score = ranking.get("relevance_score", 0)
    pct = int(score * 100) if isinstance(score, (int, float)) else 0
    matched = ", ".join(ranking.get("matched_skills", [])[:6]) or "—"
    missing = ranking.get("missing_skills") or []
    missing_str = f"\n⚠️ Gap: {', '.join(missing[:3])}" if missing else ""
    reasoning = ranking.get("reasoning", "")
    title = job.get("job_title", "?")
    url = job.get("apply_url", "")

    if not ranking.get("should_apply", True):
        _send(f"⏭️ <b>Skipped</b> {pct}% — {title}\n<i>{reasoning}</i>")
        return

    text = (
        f"📊 <b>Ranked {pct}%</b>\n{title}\n"
        f"<i>{reasoning}</i>\n"
        f"✅ Matched: {matched}{missing_str}"
    )
    _send(text)


def notify_tailored(job: dict, tailored: dict) -> None:
    file_path = tailored.get("file_path", "")
    file_name = tailored.get("file_name", "")
    notes = tailored.get("tailoring_notes", "")
    keywords = tailored.get("keywords_surfaced", [])
    url = job.get("apply_url", "")
    text = (
        f"🎯 <b>Ready to apply</b>\n{job.get('job_title', '?')}\n"
        f"📄 <code>{file_name}</code>\n"
        f"📁 <code>{file_path}</code>\n"
        f"🔑 Surfaced: {', '.join(keywords[:6]) or '—'}\n"
        f"📝 {notes}\n\nApply: {url}"
    )
    buttons = [[{"text": "🔗 Open Apply Link", "url": url}]]
    _keyboard(text, buttons)


def notify_planned(job: dict, plan: dict) -> None:
    url = job.get("apply_url", "")
    n = len(plan.get("fields") or [])
    escalations = sum(1 for f in (plan.get("fields") or []) if f.get("needs_escalation"))
    text = (
        f"📋 <b>Plan ready</b>\n{job.get('job_title', '?')}\n"
        f"ATS: <code>{plan.get('ats', '?')}</code>\n"
        f"{n} fields, {escalations} need your input\n"
        f"Extension will pick this up on next visit to: {url}"
    )
    _send(text)


def notify_submitted(job: dict) -> None:
    _send(f"✅ <b>Application confirmed</b>\n{job.get('job_title', '?')}")


def notify_blocked(job: dict, reason: str) -> None:
    _send(f"⚠️ <b>Blocked</b>\n{job.get('job_title', '?')}\n{reason}\n{job.get('apply_url', '')}")


def notify_error(job_id: str, error: str) -> None:
    _send(f"❌ Error on <code>{job_id[:8]}</code>: {error[:300]}")


# ---------- daily summary ----------

def daily_summary() -> None:
    try:
        r = table("application_stats").select("*").order("snapshot_date", desc=True).limit(30).execute()
        # Today + last 7 days
        rows = r.data or []
        today_row = next((row for row in rows if row["snapshot_date"] == str(datetime.now(timezone.utc).date())), None)
        if not today_row:
            today_row = {"applied": 0, "interview_call": 0, "interviewing": 0, "rejection": 0}
        text = (
            f"📈 <b>Daily summary</b>\n"
            f"Applied today: {today_row.get('applied', 0)}\n"
            f"Interview calls: {today_row.get('interview_call', 0)}\n"
            f"Interviewing: {today_row.get('interviewing', 0)}\n"
            f"Rejections: {today_row.get('rejection', 0)}"
        )
        _send(text)
    except Exception as e:
        log.warning("daily_summary: %s", e)


# ---------- inbound ----------

_URL_RE = re.compile(r"https?://[^\s<>\"']+")


def handle_inbound(text: str, update_id: int) -> None:
    # Persist update_id so we don't re-process
    try:
        table("telegram_poll_state").upsert({"id": 1, "last_update_id": update_id}).execute()
    except Exception:
        pass

    text = (text or "").strip()
    if not text:
        return

    if text.startswith("/"):
        _handle_command(text)
        return

    urls = _URL_RE.findall(text)
    if urls:
        for url in urls:
            _enqueue_manual(url)
        return

    _send("Send me a job URL to add, or /help for commands.")


def _handle_command(text: str) -> None:
    parts = text.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ""

    if cmd == "/help":
        _send(
            "<b>Commands</b>\n"
            "/queue — list ready_to_apply jobs\n"
            "/stats — today's funnel\n"
            "/skipped — show skipped jobs\n"
            "/help — this list\n"
            "Or paste a URL."
        )
    elif cmd == "/queue":
        try:
            # Join job_status with jobs where pipeline_status='planned' or 'tailored'
            # (jobs.ready for the extension to pick up)
            js = table("job_status").select("job_id,pipeline_status").in_(
                "pipeline_status", ["planned", "tailored"]
            ).execute()
            ids = [r["job_id"] for r in (js.data or [])]
            if not ids:
                _send("Queue is empty.")
                return
            jobs = table("jobs").select("id,job_title,apply_url,is_pm_role").in_("id", ids).limit(20).execute()
            lines = ["<b>Ready to apply</b>"]
            for j in (jobs.data or []):
                lines.append(f"• <code>{j['id'][:8]}</code> {j.get('job_title','?')}")
            _send("\n".join(lines))
        except Exception as e:
            _send(f"Queue fetch failed: {e}")
    elif cmd == "/stats":
        daily_summary()
    else:
        _send(f"Unknown command: {cmd}")


def _enqueue_manual(url: str) -> None:
    """Insert a jobs row directly. Use only if scraper pipeline is bypassed."""
    try:
        # Find or create a placeholder company — manual-additions get company_id NULL
        # (your schema allows company_id to be null).
        r = table("jobs").select("id").eq("apply_url", url).limit(1).execute()
        if r.data:
            jid = r.data[0]["id"]
            _send(f"Already in queue: <code>{jid[:8]}</code>")
            return
        ins = table("jobs").insert({
            "external_job_id": f"manual-{int(time.time()*1000)}",
            "job_title": "(pending fetch)",
            "apply_url": url,
            "is_test_job": False,
            "is_active": True,
            "is_us_job": True,
        }).execute()
        if ins.data:
            jid = ins.data[0]["id"]
            _send(f"✅ Queued <code>{jid[:8]}</code>\nI'll fetch + rank + tailor + plan and ping you.")
    except Exception as e:
        _send(f"Enqueue failed: {e}")


def run_listener(stop_event: threading.Event) -> None:
    log.info("Telegram listener starting")
    offset: int | None = None
    while not stop_event.is_set():
        try:
            params = {"timeout": 25, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            r = requests.get(_API.format(token=config.TELEGRAM_BOT_TOKEN, method="getUpdates"),
                             params=params, timeout=35)
            r.raise_for_status()
            data = r.json()
            for upd in data.get("result", []):
                offset = max(offset or 0, upd["update_id"] + 1)
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue
                text = msg.get("text", "")
                if text:
                    handle_inbound(text, upd["update_id"])
        except requests.exceptions.Timeout:
            continue
        except requests.exceptions.RequestException as e:
            log.warning("getUpdates network error: %s", e)
            time.sleep(3)
        except Exception as e:
            log.exception("getUpdates: %s", e)
            time.sleep(3)