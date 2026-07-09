"""job-agent v3 — config loader.

Reads from .env in the project root. All secrets stay in .env, never in code.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _require(key: str) -> str:
    val = os.getenv(key, "").strip()
    if not val:
        raise RuntimeError(
            f"Missing required env var: {key}. Set it in {_PROJECT_ROOT / '.env'}"
        )
    return val


def _opt(key: str, default: str = "") -> str:
    return os.getenv(key, default).strip()


# ---- Supabase ----
SUPABASE_URL = _require("SUPABASE_URL")
SUPABASE_SERVICE_KEY = _require("SUPABASE_SERVICE_KEY")

# ---- Anthropic ----
ANTHROPIC_API_KEY = _require("ANTHROPIC_API_KEY")
CLAUDE_MODEL_RANK = _opt("CLAUDE_MODEL_RANK", "claude-haiku-4-5-20251001")
CLAUDE_MODEL_TAILOR = _opt("CLAUDE_MODEL_TAILOR", "claude-sonnet-4-5-20250929")
CLAUDE_MODEL_PLAN = _opt("CLAUDE_MODEL_PLAN", "claude-sonnet-4-5-20250929")

# ---- Telegram ----
TELEGRAM_BOT_TOKEN = _require("TELEGRAM_BOT_TOKEN")
# chat_id is recovered on first inbound message; leave empty initially.

# ---- Gmail ----
GMAIL_ADDRESS = _opt("GMAIL_ADDRESS")
GMAIL_APP_PASSWORD = _opt("GMAIL_APP_PASSWORD")

# ---- Resume paths ----
DOWNLOADS_DIR = Path(_opt("DOWNLOADS_DIR", str(Path.home() / "Downloads")))
DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)

# ---- Supabase Storage bucket for tailored resumes ----
RESUMES_BUCKET = _opt("RESUMES_BUCKET", "resumes")

# ---- Polling ----
POLL_INTERVAL_SECONDS = int(_opt("POLL_INTERVAL_SECONDS", "30"))
HEARTBEAT_INTERVAL_SECONDS = int(_opt("HEARTBEAT_INTERVAL_SECONDS", "60"))

# ---- Cost guard ----
DAILY_LLM_BUDGET_USD = float(_opt("DAILY_LLM_BUDGET_USD", "5.00"))

# ---- Quiet hours ----
ACTIVE_HOURS_START = int(_opt("ACTIVE_HOURS_START", "7"))
ACTIVE_HOURS_END = int(_opt("ACTIVE_HOURS_END", "22"))

# ---- Ranking ----
MIN_RELEVANCE_SCORE = float(_opt("MIN_RELEVANCE_SCORE", "0.4"))

# ---- Logging ----
LOG_LEVEL = _opt("LOG_LEVEL", "INFO")