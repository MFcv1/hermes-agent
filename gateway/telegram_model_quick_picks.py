"""Curated Telegram quick-picks for model + reasoning configuration."""

from __future__ import annotations

from typing import List, Tuple

# label, model_id, provider_slug
TELEGRAM_QUICK_MODELS: List[Tuple[str, str, str]] = [
    ("Composer 2.5", "grok-composer-2.5-fast", "xai-oauth"),
    ("Grok Build", "grok-build-0.1", "xai-oauth"),
    ("Grok 4.3", "grok-4.3", "xai-oauth"),
    ("GPT-5.5", "gpt-5.5", "openai-codex"),
]

# label, effort token for agent.reasoning / session override
TELEGRAM_REASONING_LEVELS: List[Tuple[str, str]] = [
    ("Aucun", "none"),
    ("Low", "low"),
    ("Medium", "medium"),
    ("High", "high"),
    ("XHigh", "xhigh"),
]


def reasoning_config_for_effort(effort: str) -> dict | None:
    if effort == "none":
        return {"enabled": False}
    if effort in {"minimal", "low", "medium", "high", "xhigh"}:
        return {"enabled": True, "effort": effort}
    return None