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
    ("Ultra", "max"),
]

_REASONING_LABELS = dict((effort, label) for label, effort in TELEGRAM_REASONING_LEVELS)
_REASONING_LABELS["xhigh"] = "Extra High"


def reasoning_levels_for_model(provider: str, model_id: str) -> List[Tuple[str, str]]:
    """Return only the effort controls accepted by a provider/model pair.

    Telegram intentionally omits ``none`` and ``minimal`` from this guided
    flow: it is a compact quality selector, while the full ``/reasoning``
    command remains available for advanced overrides.  Unknown/non-adjustable
    models return an empty list so the picker can skip straight to review.
    """
    provider = str(provider or "").strip().lower()
    model = str(model_id or "").strip().lower().rsplit("/", 1)[-1]

    efforts: list[str]
    if provider in {"openai-codex", "openai-api", "openai"}:
        if model.startswith("gpt-5.6"):
            # OpenAI calls the API value ``max``.  Telegram labels it
            # "Ultra" to match the product vocabulary users recognize.
            efforts = ["low", "medium", "high", "xhigh", "max"]
        elif model.startswith(("gpt-5", "o1", "o3", "o4")):
            efforts = ["low", "medium", "high", "xhigh"]
        else:
            efforts = []
    elif provider in {"copilot", "github-copilot", "copilot-acp"}:
        try:
            from hermes_cli.models import (
                _resolve_copilot_catalog_api_key,
                github_model_reasoning_efforts,
            )

            efforts = github_model_reasoning_efforts(
                model_id,
                api_key=_resolve_copilot_catalog_api_key() or None,
            )
        except Exception:
            efforts = []
    elif provider in {"xai", "xai-oauth", "grok-oauth"}:
        if model.startswith("grok-4.20-multi-agent"):
            efforts = ["low", "medium", "high", "xhigh"]
        elif model.startswith(("grok-4.5", "grok-4.3", "grok-3-mini")):
            efforts = ["low", "medium", "high"]
        else:
            efforts = []
    else:
        efforts = []

    allowed = {"low", "medium", "high", "xhigh", "max"}
    return [
        (_REASONING_LABELS.get(effort, effort.title()), effort)
        for effort in dict.fromkeys(efforts)
        if effort in allowed
    ]


def reasoning_config_for_effort(effort: str) -> dict | None:
    if effort == "none":
        return {"enabled": False}
    if effort in {"minimal", "low", "medium", "high", "xhigh", "max"}:
        return {"enabled": True, "effort": effort}
    return None
