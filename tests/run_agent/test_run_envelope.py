"""Acceptance tests for the strict per-run model-call contract."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from agent.run_envelope import (
    ModelCallBudget,
    ModelCallBudgetExceeded,
    RunEnvelope,
    begin_model_call,
)


def _tool_defs():
    return [
        {
            "type": "function",
            "function": {
                "name": "web_search",
                "description": "search",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]


def _response(*, content="", tool=False, call_id="call_1"):
    tool_calls = []
    finish_reason = "stop"
    if tool:
        finish_reason = "tool_calls"
        tool_calls = [
            SimpleNamespace(
                id=call_id,
                type="function",
                function=SimpleNamespace(name="web_search", arguments="{}"),
            )
        ]
    message = SimpleNamespace(
        content=content,
        tool_calls=tool_calls,
        reasoning=None,
        reasoning_content=None,
        reasoning_details=None,
    )
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason=finish_reason)],
        model="test/model",
        usage=None,
    )


def _agent(*, max_iterations=8, model="test/model", run_envelope=None):
    from run_agent import AIAgent

    with (
        patch("run_agent.get_tool_definitions", return_value=_tool_defs()),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            provider="openrouter",
            model=model,
            max_iterations=max_iterations,
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            run_envelope=run_envelope,
        )
    agent.client = MagicMock()
    return agent


def test_budget_eight_includes_reserved_final_call():
    agent = _agent(max_iterations=8)
    work = [_response(tool=True, call_id=f"call_{idx}") for idx in range(7)]
    agent.client.chat.completions.create.side_effect = [
        *work,
        _response(content="final synthesis"),
    ]

    with (
        patch("run_agent.handle_function_call", return_value="ok"),
        patch.object(agent, "_persist_session"),
        patch.object(agent, "_save_trajectory"),
        patch.object(agent, "_cleanup_task_resources"),
    ):
        result = agent.run_conversation("finish within eight calls")

    assert agent.client.chat.completions.create.call_count == 8
    assert result["model_calls"] == 8
    assert result["run"]["budget"]["used"] == 8
    assert result["run"]["budget"]["limit"] == 8
    assert result["run"]["budget"]["reserved_used"] == 1
    assert agent.session_api_calls == 8


def test_wrong_model_is_blocked_before_first_provider_call():
    envelope = RunEnvelope.create(
        session_id="session_1",
        task_id="task_1",
        model="expected/model",
        provider="openrouter",
        effort="default",
        budget_limit=8,
    )
    agent = _agent(model="actual/model", run_envelope=envelope)

    with patch.object(agent, "_persist_session"):
        result = agent.run_conversation("do not call the provider")

    assert result["failure_reason"] == "run_contract_mismatch"
    assert result["api_calls"] == 0
    agent.client.chat.completions.create.assert_not_called()
    assert envelope.budget.used == 0


def test_child_envelope_shares_parent_budget():
    parent = RunEnvelope.create(
        session_id="session_1",
        task_id="task_1",
        model="parent/model",
        provider="openrouter",
        effort="high",
        budget_limit=4,
    )
    child = parent.derive_child(
        model="child/model",
        provider="anthropic",
        effort="medium",
    )

    parent.budget.acquire("execution")
    child.budget.acquire("delegation")

    assert child.run_id == parent.run_id
    assert child.budget is parent.budget
    assert parent.budget.snapshot()["used"] == 2


def test_subagent_policy_deny_refuses_before_child_creation():
    from tools.delegate_tool import delegate_task

    parent = SimpleNamespace(
        run_envelope=RunEnvelope.create(
            session_id="session_1",
            task_id="task_1",
            model="test/model",
            provider="openrouter",
            effort="default",
            budget_limit=8,
            subagent_policy="deny",
        )
    )

    result = json.loads(delegate_task(goal="must not spawn", parent_agent=parent))

    assert "forbidden by the active run envelope" in result["error"]


def test_call_start_emits_and_persists_exactly_once():
    events = []
    db = MagicMock()
    agent = SimpleNamespace(
        run_envelope=RunEnvelope.create(
            session_id="session_1",
            task_id="task_1",
            model="test/model",
            provider="openrouter",
            effort="high",
            budget_limit=3,
        ),
        model="test/model",
        provider="openrouter",
        reasoning_config={"effort": "high"},
        session_api_calls=0,
        _session_db=db,
        session_id="session_1",
        _session_db_created=True,
        base_url="https://openrouter.ai/api/v1",
        event_callback=lambda event, payload: events.append((event, payload)),
    )

    begin_model_call(agent, api_request_id="request_1")

    assert agent.session_api_calls == 1
    assert events[0][0] == "llm:call"
    assert events[0][1]["used"] == 1
    assert events[0][1]["limit"] == 3
    db.update_token_counts.assert_called_once_with(
        "session_1",
        model="test/model",
        billing_provider="openrouter",
        billing_base_url="https://openrouter.ai/api/v1",
        api_call_count=1,
    )


def test_phase_budget_and_final_reservation_are_hard_limits():
    budget = ModelCallBudget(
        4,
        reserved=1,
        phase_limits={"planning": 1},
    )

    budget.acquire("planning")
    with pytest.raises(ModelCallBudgetExceeded, match="planning"):
        budget.acquire("planning")
    budget.acquire("execution")
    budget.acquire("execution")
    with pytest.raises(ModelCallBudgetExceeded, match="reserved final"):
        budget.acquire("execution")
    budget.acquire("final", final=True)
    with pytest.raises(ModelCallBudgetExceeded, match="reserved final"):
        budget.acquire("final", final=True)
