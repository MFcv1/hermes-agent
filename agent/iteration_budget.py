"""Per-agent iteration budget — thread-safe consume/refund counter.

Extracted from ``run_agent.py``.  Each ``AIAgent`` instance (parent or
subagent) holds an :class:`IterationBudget`; the parent's cap comes from
``max_iterations`` (default 90), each subagent's local loop cap comes from
``delegation.max_iterations`` (default 50). The separate model-call budget in
``agent.run_envelope`` is shared across the whole parent/child run tree.

``run_agent`` re-exports ``IterationBudget`` so existing
``from run_agent import IterationBudget`` imports keep working unchanged.
"""

from __future__ import annotations

import threading


class IterationBudget:
    """Thread-safe iteration counter for an agent.

    Each agent (parent or subagent) gets its own ``IterationBudget``.
    The parent's budget is capped at ``max_iterations`` (default 90).
    Each subagent gets a local loop budget capped at
    ``delegation.max_iterations``. Provider calls are additionally governed
    by the shared, strict ``RunEnvelope`` budget, so local iteration caps can
    no longer increase the run's total model-call allowance.

    ``execute_code`` (programmatic tool calling) iterations are refunded via
    :meth:`refund` so they don't eat into the budget.
    """

    def __init__(self, max_total: int):
        self.max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """Try to consume one iteration.  Returns True if allowed."""
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> None:
        """Give back one iteration (e.g. for execute_code turns)."""
        with self._lock:
            if self._used > 0:
                self._used -= 1

    @property
    def used(self) -> int:
        with self._lock:
            return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)


__all__ = ["IterationBudget"]
