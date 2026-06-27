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
    """Central user-facing progress model for chat and Repo Cockpit runs."""

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


_REPO_COCKPIT_PHASES: dict[str, tuple[str, str, str, int]] = {
    "queued_plan": ("analyse", "Analyse", "je prepare le contexte et les garde-fous.", 1),
    "running_quota": ("analyse", "Analyse", "je verifie les quotas avant de lancer une phase lourde.", 1),
    "running_triage": ("analyse", "Analyse", "je classe la demande et les risques.", 1),
    "running_plan": ("plan", "Plan", "je construis le plan d'execution.", 2),
    "plan_ready": ("plan", "Plan", "le plan est pret pour la suite.", 2),
    "waiting_plan_approval": ("waiting_approval", "Attente validation", "j'attends ta validation avant de continuer.", 2),
    "running_gpt55": ("implementation", "Implementation", "j'implemente le plan dans le projet.", 3),
    "running_review_remediation": ("implementation", "Implementation", "je corrige les points detectes par la review.", 3),
    "running_tests": ("tests", "Tests", "je lance les controles et je verifie le comportement.", 4),
    "blocked_tests": ("tests", "Tests", "les tests bloquent; je garde le diagnostic visible.", 4),
    "running_independent_review": ("audit", "Audit", "je fais une review independante du resultat.", 5),
    "running_pr": ("audit", "Audit", "je prepare la PR et les gates de validation.", 5),
    "running_deploy_preview": ("deploy", "Deploy", "je deploie une preview testable.", 6),
    "deployed_preview": ("deploy", "Deploy", "la preview est prete; je verifie le dernier etat.", 6),
    "blocked_deploy": ("deploy", "Deploy", "le deploiement bloque; je garde la cause visible.", 6),
    "blocked_smoke": ("deploy", "Deploy", "la preview existe mais le smoke test bloque.", 6),
    "blocked_quota": ("quota", "Pause quota", "quota trop bas; j'ecris la reprise avant d'arreter.", 0),
}

_PHASE_TOTAL = 6


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


def classify_repo_cockpit_status(status: Optional[str], current_phase: Optional[str] = None) -> tuple[str, str, str, Optional[int], str]:
    """Map Repo Cockpit task statuses to human phases."""

    candidates = [str(status or "").strip(), str(current_phase or "").strip()]
    for value in candidates:
        if not value:
            continue
        mapped = _REPO_COCKPIT_PHASES.get(value)
        if mapped:
            key, label, sentence, step = mapped
            severity = "blocked" if value.startswith("blocked") else "working"
            if value == "deployed_preview":
                severity = "success"
            if value == "blocked_quota":
                severity = "warning"
            return key, label, sentence, step or None, severity
        if value.startswith("blocked_"):
            return "action_required", "Action requise", "une action ou une configuration bloque la suite.", None, "blocked"
    return "analyse", "Analyse", "je lis l'etat de la tache.", 1, "working"


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


def progress_from_autonomy(data: Optional[Mapping[str, object]], *, elapsed_seconds: int = 0) -> AgentProgressView:
    payload: Mapping[str, object] = data or {}
    raw_task = payload.get("task") if isinstance(payload, Mapping) else {}
    task: Mapping[str, object] = raw_task if isinstance(raw_task, Mapping) else payload
    status = _safe_str(task.get("status"))
    current_phase = _safe_str(task.get("current_phase") or task.get("phase"))
    phase_key, phase_label, sentence, step_current, severity = classify_repo_cockpit_status(status, current_phase)
    preview = task.get("preview_url") or task.get("deployment_url")
    model = (
        task.get("model")
        or task.get("model_label")
        or task.get("active_model")
        or task.get("plan_model")
        or payload.get("model")
        or payload.get("active_model")
    )
    return AgentProgressView(
        elapsed_seconds=elapsed_seconds,
        phase_key=phase_key,
        phase_label=phase_label,
        state_sentence=sentence,
        mode=_safe_str(task.get("mode"), 40),
        model_label=clean_model_label(_safe_str(model, 80)),
        step_current=step_current,
        step_total=_PHASE_TOTAL if step_current else None,
        task_id=_safe_str(task.get("id") or payload.get("task_id"), 80),
        repo=_safe_str(task.get("repo"), 120),
        preview_url=_safe_str(preview, 240),
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
