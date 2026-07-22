"""Human-readable progress rendering for chat gateway heartbeats."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Optional


_TECHNICAL_PATTERNS = (
    "iteration",
    "non-streaming api response",
    "api error recovery",
    "max_iterations",
    "api_call_count",
    "process",
    "pid",
)


@dataclass(frozen=True)
class HumanHeartbeat:
    elapsed_seconds: int
    phase: str = "Analyse"
    state: str = "je traite ta demande."
    model: Optional[str] = None
    mode: Optional[str] = None
    step_current: Optional[int] = None
    step_total: Optional[int] = None
    severity: str = "working"


@dataclass(frozen=True)
class AgentProgressView:
    """Central user-facing progress model for gateway runs."""

    elapsed_seconds: int
    phase_key: str = "analyse"
    phase_label: str = "Analyse"
    state_sentence: str = "je traite ta demande."
    mode: Optional[str] = None
    model_label: Optional[str] = None
    step_current: Optional[int] = None
    step_total: Optional[int] = None
    task_id: Optional[str] = None
    repo: Optional[str] = None
    preview_url: Optional[str] = None
    severity: str = "working"


def format_elapsed(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    if seconds < 60:
        return f"{seconds}s"
    minutes, rest = divmod(seconds, 60)
    if rest == 0:
        return f"{minutes} min"
    return f"{minutes} min {rest}s"


def clean_model_label(model: Optional[str]) -> Optional[str]:
    if model is not None and not isinstance(model, str):
        return None
    value = str(model or "").strip()
    if not value:
        return None
    low = value.lower()
    if "gpt-5.5" in low or "gpt5.5" in low:
        return "GPT-5.5"
    if "spark" in low:
        return "GPT Spark"
    if "gpt-5" in low or "gpt5" in low:
        return "GPT-5"
    if "codex" in low:
        return "Codex"
    return value[:40]


def classify_agent_status(activity: Optional[Mapping[str, object]]) -> tuple[str, str]:
    """Map raw agent activity to a user-facing phase and short sentence."""

    if not activity:
        return "Analyse", "je lis ta demande et je prepare la suite."

    raw_action = " ".join(
        str(activity.get(key) or "")
        for key in ("current_tool", "last_activity_desc", "phase", "status")
    ).strip()
    action = raw_action.lower()

    if "api error recovery" in action or "retry" in action or "recover" in action:
        return "Correction", "je relance apres une erreur temporaire."
    if "rate-limit" in action or "rate limit" in action or "429" in action:
        return "Pause", "le modele est temporairement limite; je garde le contexte pret."
    if "non-streaming api response" in action or "api response" in action:
        return "Analyse", "j'attends la reponse du modele."
    if "terminal" in action or "exec" in action or "command" in action:
        return "Execution", "j'execute une action puis je verifierai le resultat."
    if "browser" in action or "screenshot" in action or "playwright" in action or "cua" in action:
        return "Verification visuelle", "je verifie l'interface et le rendu."
    if "test" in action or "pytest" in action or "build" in action:
        return "Tests", "je verifie que le projet passe les controles."
    if "deploy" in action or "vercel" in action or "hosting" in action:
        return "Deploiement", "je prepare le lien de test."
    if "audit" in action or "review" in action:
        return "Audit", "je controle le resultat avant de conclure."
    if "plan" in action:
        return "Plan", "je structure les etapes avant d'agir."

    return "Analyse", "je traite ta demande."


def _safe_str(value: object, limit: int = 120) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    return text[:limit]


def progress_from_activity(
    *,
    elapsed_seconds: int,
    activity: Optional[Mapping[str, object]] = None,
    model: Optional[str] = None,
    mode: Optional[str] = None,
) -> AgentProgressView:
    phase, state = classify_agent_status(activity)
    key = phase.lower().replace(" ", "_")
    severity = "warning" if phase in {"Pause"} else "working"
    return AgentProgressView(
        elapsed_seconds=elapsed_seconds,
        phase_key=key,
        phase_label=phase,
        state_sentence=state,
        model_label=clean_model_label(model),
        mode=_safe_str(mode, 40),
        severity=severity,
    )


def has_technical_leak(text: str) -> bool:
    low = str(text or "").lower()
    return any(pattern in low for pattern in _TECHNICAL_PATTERNS)


def render_human_heartbeat(progress: HumanHeartbeat) -> str:
    """Render a compact Telegram-friendly heartbeat without internal jargon."""

    title = "Hermes en pause" if progress.severity in {"warning", "blocked"} else "Hermes travaille"
    lines = [f"{title} - {format_elapsed(progress.elapsed_seconds)}"]
    if progress.phase:
        lines.append(f"Phase : {progress.phase}")
    model = clean_model_label(progress.model)
    if model:
        lines.append(f"Modele : {model}")
    if progress.mode:
        lines.append(f"Mode : {progress.mode}")
    if progress.step_current and progress.step_total:
        lines.append(f"Progression : {progress.step_current}/{progress.step_total}")
    state = str(progress.state or "").strip()
    if state:
        lines.append(f"Etat : {state}")
    rendered = "\n".join(lines)
    if has_technical_leak(rendered):
        rendered = re.sub(r"(?i)iteration\s+\d+/\d+", "progression en cours", rendered)
        rendered = re.sub(r"(?i)api error recovery(?:\s*\([^)]*\))?", "correction temporaire", rendered)
        rendered = re.sub(r"(?i)waiting for non-streaming api response", "attente du modele", rendered)
    return rendered


def render_progress_view(progress: AgentProgressView) -> str:
    """Render a compact Telegram-friendly progress view without internals."""

    if progress.severity == "success":
        title = "Hermes a termine"
    elif progress.severity in {"warning", "blocked"}:
        title = "Hermes en pause"
    else:
        title = "Hermes travaille"
    lines = [f"{title} - {format_elapsed(progress.elapsed_seconds)}"]
    if progress.phase_label:
        lines.append(f"Phase : {progress.phase_label}")
    if progress.step_current and progress.step_total:
        lines.append(f"Progression : {progress.step_current}/{progress.step_total}")
    model = clean_model_label(progress.model_label)
    if model:
        lines.append(f"Modele : {model}")
    if progress.mode:
        lines.append(f"Mode : {progress.mode}")
    if progress.task_id:
        lines.append(f"Task : {progress.task_id}")
    if progress.repo:
        lines.append(f"Repo : {progress.repo}")
    state = str(progress.state_sentence or "").strip()
    if state:
        lines.append(f"Etat : {state}")
    if progress.preview_url:
        lines.append(f"Preview : {progress.preview_url}")
    rendered = "\n".join(lines)
    if has_technical_leak(rendered):
        rendered = re.sub(r"(?i)iteration\s+\d+/\d+", "progression en cours", rendered)
        rendered = re.sub(r"(?i)api error recovery(?:\s*\([^)]*\))?", "correction temporaire", rendered)
        rendered = re.sub(r"(?i)waiting for non-streaming api response", "attente du modele", rendered)
    return rendered


def render_from_activity(
    *,
    elapsed_seconds: int,
    activity: Optional[Mapping[str, object]] = None,
    model: Optional[str] = None,
    mode: Optional[str] = None,
) -> str:
    return render_progress_view(
        progress_from_activity(
            elapsed_seconds=elapsed_seconds,
            activity=activity,
            model=model,
            mode=mode,
        )
    )
