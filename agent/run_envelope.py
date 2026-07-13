"""Runtime contract for one bounded Hermes execution.

The envelope is deliberately independent from the conversation transcript and
tool schema so attaching it to a run cannot invalidate prompt caching.  Its
``ModelCallBudget`` is shared by parent and child agents and counts provider
attempts, including retries and the reserved final synthesis call.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Mapping

logger = logging.getLogger(__name__)


class RunContractViolation(RuntimeError):
    """The effective runtime does not match the declared run contract."""


class ModelCallBudgetExceeded(RuntimeError):
    """No model-call slot remains for the requested phase."""


class ModelCallBudget:
    """Thread-safe total/phase budget with final-call reservation."""

    def __init__(
        self,
        limit: int,
        *,
        reserved: int = 1,
        phase_limits: Mapping[str, int] | None = None,
    ) -> None:
        if int(limit) < 1:
            raise ValueError("model-call budget must be at least 1")
        self.limit = int(limit)
        self.reserved = min(max(0, int(reserved)), max(0, self.limit - 1))
        self.phase_limits = {
            str(name): max(0, int(value))
            for name, value in dict(phase_limits or {}).items()
        }
        self._used = 0
        self._final_used = 0
        self._phase_used: dict[str, int] = {}
        self._lock = threading.Lock()

    def acquire(self, phase: str, *, final: bool = False) -> dict[str, Any]:
        clean_phase = str(phase or "execution")
        with self._lock:
            phase_limit = self.phase_limits.get(clean_phase)
            phase_used = self._phase_used.get(clean_phase, 0)
            if phase_limit is not None and phase_used >= phase_limit:
                raise ModelCallBudgetExceeded(
                    f"phase '{clean_phase}' exhausted ({phase_used}/{phase_limit})"
                )
            if final:
                if self.reserved <= 0 or self._final_used >= self.reserved:
                    raise ModelCallBudgetExceeded("reserved final model call is unavailable")
                if self._used >= self.limit:
                    raise ModelCallBudgetExceeded(
                        f"model-call budget exhausted ({self._used}/{self.limit})"
                    )
                self._final_used += 1
            elif self._used >= self.limit - self.reserved:
                raise ModelCallBudgetExceeded(
                    "work-call budget exhausted; reserved final call cannot be consumed"
                )
            self._used += 1
            self._phase_used[clean_phase] = phase_used + 1
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> dict[str, Any]:
        return {
            "used": self._used,
            "limit": self.limit,
            "reserved": self.reserved,
            "reserved_used": self._final_used,
            "remaining": max(0, self.limit - self._used),
            "work_remaining": max(0, self.limit - self.reserved - self._used),
            "phase_used": dict(self._phase_used),
            "phase_limits": dict(self.phase_limits),
        }

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot_unlocked()

    @property
    def used(self) -> int:
        return int(self.snapshot()["used"])

    @property
    def work_remaining(self) -> int:
        return int(self.snapshot()["work_remaining"])


def reasoning_effort(reasoning_config: Any) -> str:
    if not isinstance(reasoning_config, dict):
        return "default"
    if reasoning_config.get("enabled") is False:
        return "none"
    return str(reasoning_config.get("effort") or "default")


@dataclass
class RunEnvelope:
    run_id: str
    session_id: str
    task_id: str | None
    model: str
    provider: str
    effort: str
    budget: ModelCallBudget
    phase: str = "execution"
    permissions: dict[str, bool] = field(default_factory=dict)
    subagent_policy: str = "allow"
    _runtime_verified: bool = field(default=False, init=False, repr=False)

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        task_id: str | None,
        model: str,
        provider: str,
        effort: str,
        budget_limit: int,
        reserved_final_calls: int = 1,
        phase_budgets: Mapping[str, int] | None = None,
        permissions: Mapping[str, bool] | None = None,
        subagent_policy: str = "allow",
        run_id: str | None = None,
    ) -> "RunEnvelope":
        policy = str(subagent_policy or "allow").lower()
        if policy not in {"allow", "deny"}:
            raise ValueError("subagent_policy must be 'allow' or 'deny'")
        return cls(
            run_id=run_id or f"run_{uuid.uuid4().hex}",
            session_id=str(session_id or ""),
            task_id=str(task_id) if task_id else None,
            model=str(model or ""),
            provider=str(provider or ""),
            effort=str(effort or "default"),
            budget=ModelCallBudget(
                budget_limit,
                reserved=reserved_final_calls,
                phase_limits=phase_budgets,
            ),
            permissions={str(k): bool(v) for k, v in dict(permissions or {}).items()},
            subagent_policy=policy,
        )

    def bind(self, *, session_id: str, task_id: str | None) -> None:
        if not self.session_id:
            self.session_id = str(session_id or "")
        if not self.task_id and task_id:
            self.task_id = str(task_id)

    def verify_runtime(self, *, model: str, provider: str, effort: str) -> None:
        if self._runtime_verified:
            return
        actual = {
            "model": str(model or ""),
            "provider": str(provider or ""),
            "effort": str(effort or "default"),
        }
        expected = {
            "model": self.model,
            "provider": self.provider,
            "effort": self.effort,
        }
        mismatches = {
            key: (expected[key], actual[key])
            for key in expected
            if expected[key] and expected[key] != actual[key]
        }
        if mismatches:
            details = ", ".join(
                f"{key} expected={wanted!r} actual={got!r}"
                for key, (wanted, got) in mismatches.items()
            )
            raise RunContractViolation(f"run envelope mismatch before first call: {details}")
        self._runtime_verified = True

    def derive_child(self, *, model: str, provider: str, effort: str) -> "RunEnvelope":
        child = RunEnvelope(
            run_id=self.run_id,
            session_id=self.session_id,
            task_id=self.task_id,
            model=str(model or ""),
            provider=str(provider or ""),
            effort=str(effort or "default"),
            budget=self.budget,
            phase="delegation",
            permissions=dict(self.permissions),
            subagent_policy=self.subagent_policy,
        )
        return child

    def receipt(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "model": self.model,
            "provider": self.provider,
            "effort": self.effort,
            "phase": self.phase,
            "permissions": dict(self.permissions),
            "subagent_policy": self.subagent_policy,
            "budget": self.budget.snapshot(),
        }


def begin_model_call(
    agent,
    *,
    phase: str | None = None,
    final: bool = False,
    api_request_id: str = "",
) -> dict[str, Any]:
    """Verify, reserve, persist and emit one real provider-call attempt."""

    envelope: RunEnvelope = agent.run_envelope
    envelope.verify_runtime(
        model=agent.model,
        provider=agent.provider,
        effort=reasoning_effort(agent.reasoning_config),
    )
    call_phase = phase or envelope.phase
    budget = envelope.budget.acquire(call_phase, final=final)
    agent.session_api_calls += 1

    if agent._session_db and agent.session_id:
        try:
            if not agent._session_db_created:
                agent._ensure_db_session()
            agent._session_db.update_token_counts(
                agent.session_id,
                model=agent.model,
                billing_provider=agent.provider,
                billing_base_url=agent.base_url,
                api_call_count=1,
            )
        except Exception as exc:
            logger.debug("model-call persistence failed: %s", exc)

    payload = {
        "run_id": envelope.run_id,
        "session_id": agent.session_id or envelope.session_id,
        "task_id": envelope.task_id,
        "api_request_id": api_request_id,
        "phase": call_phase,
        "final": bool(final),
        "model": agent.model,
        "provider": agent.provider,
        "effort": reasoning_effort(agent.reasoning_config),
        "used": budget["used"],
        "limit": budget["limit"],
        "reserved": budget["reserved"],
    }
    logger.info(
        "Model call started: run=%s phase=%s model=%s provider=%s budget=%d/%d reserved=%d",
        envelope.run_id,
        call_phase,
        agent.model,
        agent.provider or "unknown",
        budget["used"],
        budget["limit"],
        budget["reserved"],
    )
    if agent.event_callback:
        try:
            agent.event_callback("llm:call", payload)
        except Exception as exc:
            logger.debug("llm:call event callback failed: %s", exc)
    return payload


__all__ = [
    "ModelCallBudget",
    "ModelCallBudgetExceeded",
    "RunContractViolation",
    "RunEnvelope",
    "begin_model_call",
    "reasoning_effort",
]
