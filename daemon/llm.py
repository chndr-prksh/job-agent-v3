"""LLM client — Claude (Anthropic SDK).

Tracks daily spend in a local log; refuses to call if budget exceeded.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from pathlib import Path

import anthropic

from daemon import config


log = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None
_COST_LOG = Path(__file__).resolve().parent.parent / "data" / "llm_cost.log"
_COST_LOG.parent.mkdir(parents=True, exist_ok=True)


_PRICING = {
    "claude-sonnet-4-5-20250929": {"input": 3.00, "output": 15.00},
    "claude-haiku-4-5-20251001":  {"input": 0.80, "output": 4.00},
    "claude-opus-4-1-20250805":   {"input": 15.00, "output": 75.00},
}


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    return _client


def _today_spend() -> float:
    today = date.today().isoformat()
    total = 0.0
    if not _COST_LOG.exists():
        return 0.0
    for line in _COST_LOG.read_text(encoding="utf-8").splitlines():
        try:
            ts, cost_str = line.split("\t")
            if ts.startswith(today):
                total += float(cost_str)
        except Exception:
            continue
    return total


def _record_spend(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _PRICING.get(model, _PRICING["claude-haiku-4-5-20251001"])
    cost = (input_tokens / 1_000_000) * pricing["input"] + (output_tokens / 1_000_000) * pricing["output"]
    with _COST_LOG.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.now(timezone.utc).isoformat()}\t{cost:.6f}\t{model}\n")
    return cost


class BudgetExceeded(Exception):
    pass


def call(*, model: str, system: str, user: str, max_tokens: int = 2048, temperature: float = 0.3) -> dict:
    """Call Claude. Returns dict with text, input_tokens, output_tokens, cost_usd."""
    if _today_spend() >= config.DAILY_LLM_BUDGET_USD:
        raise BudgetExceeded(
            f"Daily LLM budget ${config.DAILY_LLM_BUDGET_USD:.2f} exceeded "
            f"(today: ${_today_spend():.2f}). Raise DAILY_LLM_BUDGET_USD in .env or wait until tomorrow."
        )

    msg = _get_client().messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
    cost = _record_spend(model, msg.usage.input_tokens, msg.usage.output_tokens)
    log.info("Claude call: model=%s in=%d out=%d cost=$%.4f",
             model, msg.usage.input_tokens, msg.usage.output_tokens, cost)
    return {
        "text": text,
        "input_tokens": msg.usage.input_tokens,
        "output_tokens": msg.usage.output_tokens,
        "cost_usd": cost,
    }