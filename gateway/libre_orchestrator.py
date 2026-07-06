"""Libre V2 orchestration helpers.

Libre is a soft orchestration mode: it does not erase durable memory, but it can
close the currently-active repo/thread context, keep a resumable handoff, route
obvious repo work into Repo Cockpit, and keep lightweight learning policies.
The functions here are intentionally pure/small so Telegram and gateway code can
use them without growing the model tool schema or rebuilding prompts mid-session.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from gateway.memory.handoff_store import HandoffStore


@dataclass(frozen=True)
class LibreDecision:
    """Routing decision for a natural Libre-mode message."""

    action: str = "chat"  # chat | repo_task | resume | learn_policy
    mode: str = "libre"   # libre | ask_review | pilote | autopilot
    intent: str = "general"
    requires_active_repo: bool = False
    confidence: float = 0.0
    reason: str = ""


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def classify_libre_message(text: str) -> LibreDecision:
    """Classify whether a Libre message should remain chat or become repo work.

    The router is deliberately conservative. It only captures messages with
    action verbs strongly associated with repo work. Everything else flows to
    the normal Hermes chat loop.
    """

    clean = _normalize(text)
    if not clean:
        return LibreDecision(reason="empty")

    if extract_learning_policy(text):
        return LibreDecision(
            action="learn_policy",
            mode="libre",
            intent="learning_policy",
            confidence=0.86,
            reason="explicit model/reasoning learning preference",
        )

    resume_markers = (
        "reprends",
        "reprend",
        "continue",
        "continuons",
        "où on en était",
        "ou on en etait",
        "chantier d'hier",
        "reprise chantier",
    )
    deploy_markers = ("déploi", "deploi", "deploy", "prod", "preview", "vps")
    switch_markers = ("passe sur", "switch", "change de repo", "changer de repo", "reprends le repo", "reprend le repo")
    bug_markers = ("bug", "corrige", "fix", "erreur", "crash", "cass", "répare", "repare")
    feature_markers = ("ajoute", "implémente", "implemente", "feature", "modifie", "améliore", "ameliore")
    repo_markers = ("repo", "github", "branche", "pr", "commit", "tests", "gateway", "cockpit", "vps")
    autopilot_markers = ("autopilot", "tout seul", "sans friction", "si les tests passent", "go direct")
    ask_review_markers = ("review", "relis", "analyse", "audit", "vérifie", "verifie")

    has_repo_context = any(marker in clean for marker in repo_markers)
    wants_switch = any(marker in clean for marker in switch_markers)
    wants_deploy = any(marker in clean for marker in deploy_markers)
    wants_bugfix = any(marker in clean for marker in bug_markers)
    wants_feature = any(marker in clean for marker in feature_markers)
    wants_review = any(marker in clean for marker in ask_review_markers)
    wants_autopilot = any(marker in clean for marker in autopilot_markers)

    if any(marker in clean for marker in resume_markers):
        return LibreDecision(
            action="resume",
            mode="pilote",
            intent="resume",
            requires_active_repo=False,
            confidence=0.84,
            reason="resume intent detected",
        )

    if wants_switch:
        return LibreDecision(
            action="switch_repo",
            mode="pilote",
            intent="switch_repo",
            requires_active_repo=False,
            confidence=0.76,
            reason="repo switch request detected",
        )

    if not (wants_deploy or wants_bugfix or wants_feature or wants_review):
        return LibreDecision(reason="no strong repo-work verb")

    intent = "general"
    if wants_deploy:
        intent = "deploy"
    elif wants_bugfix:
        intent = "debug_fix"
    elif wants_feature:
        intent = "feature_work"
    elif wants_review:
        intent = "audit_repo"

    mode = "pilote"
    if wants_autopilot and not wants_deploy:
        mode = "autopilot"
    elif wants_review and not (wants_bugfix or wants_feature or wants_deploy):
        mode = "ask_review"

    return LibreDecision(
        action="repo_task",
        mode=mode,
        intent=intent,
        requires_active_repo=True,
        confidence=0.82 if has_repo_context else 0.68,
        reason="repo-work markers detected" if has_repo_context else "repo-work verb detected",
    )


def extract_learning_policy(text: str) -> dict[str, str] | None:
    """Extract a durable model/reasoning preference from natural French text."""

    clean = _normalize(text)
    learning_markers = ("pour les", "mets toi", "met toi", "utilise", "toujours", "mémorise", "memorise")
    if not any(marker in clean for marker in learning_markers):
        return None

    scope = "general"
    if any(word in clean for word in ("plan", "plans", "architecture", "architect")):
        scope = "planning"
    elif any(word in clean for word in ("deploy", "déploi", "deploi", "prod")):
        scope = "deployment"
    elif any(word in clean for word in ("bug", "fix", "corrige")):
        scope = "debugging"

    model_match = re.search(r"\b(gpt[-\s]?5(?:\.5|\.3)?(?:[-\s]codex[-\s]spark)?|haiku|sonnet|opus|grok)\b", clean)
    reasoning_match = re.search(r"\b(xhigh|extra[-\s]?high|high|medium|low|minimal|none)\b", clean)
    if not model_match and not reasoning_match:
        return None

    model = (model_match.group(1).replace(" ", "-") if model_match else "").lower()
    if model == "gpt-5":
        model = "gpt-5.5"
    reasoning = reasoning_match.group(1).replace("extra-high", "xhigh").replace("extra high", "xhigh") if reasoning_match else ""

    policy = {"scope": scope}
    if model:
        policy["model"] = model
    if reasoning:
        policy["reasoning_effort"] = reasoning
    return policy


class ActiveWorkStore:
    """Compatibility facade over the Phase 5 SQLite handoff store."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        sqlite_path = self.path.with_suffix(".sqlite") if self.path.suffix == ".json" else self.path
        self._store = HandoffStore(sqlite_path, legacy_json_path=self.path if self.path.suffix == ".json" else None)

    def get_active(self, key: str) -> dict[str, Any]:
        return self._store.get_active(key)

    def set_active(self, key: str, **updates: Any) -> dict[str, Any]:
        return self._store.set_active(key, **updates)

    def soft_close(self, key: str, *, reason: str = "/libre") -> dict[str, Any]:
        return self._store.soft_close(key, reason=reason)

    def latest_handoff(self, key: str, *, include_consumed: bool = False) -> dict[str, Any] | None:
        return self._store.latest_handoff(key, include_consumed=include_consumed)

    def cache_handoff(self, key: str, handoff: dict[str, Any]) -> dict[str, Any]:
        return self._store.cache_handoff(key, handoff)

    def remember_policy(self, key: str, policy: dict[str, str], *, source: str = "telegram") -> dict[str, str]:
        return self._store.remember_policy(key, policy, source=source)


def scan_watch_logs(paths: Iterable[str | Path], *, limit: int = 30) -> dict[str, Any]:
    """Return a small Watch V1 report over recent log lines."""

    items: list[dict[str, str]] = []
    error_re = re.compile(r"\b(error|exception|traceback|failed|critical)\b", re.I)
    for raw_path in paths:
        path = Path(raw_path)
        if not path.exists() or not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        except Exception:
            continue
        for line in lines:
            if error_re.search(line):
                items.append({"file": str(path), "line": line[-500:]})
    status = "attention" if items else "green"
    return {
        "status": status,
        "error_count": len(items),
        "items": items[:limit],
    }
