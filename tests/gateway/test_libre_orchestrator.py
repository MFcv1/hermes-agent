"""Tests for Libre V2 orchestration primitives."""

from __future__ import annotations

from pathlib import Path

from gateway.libre_orchestrator import (
    ActiveWorkStore,
    classify_libre_message,
    extract_learning_policy,
    scan_watch_logs,
)
from gateway.memory.handoff_store import HandoffStore


def test_classify_libre_message_keeps_normal_chat_free():
    decision = classify_libre_message("explique moi la différence entre Pilote et Autopilot")

    assert decision.action == "chat"
    assert decision.mode == "libre"
    assert decision.intent == "general"


def test_classify_libre_message_routes_repo_bug_to_pilote_debug():
    decision = classify_libre_message("corrige le bug du menu /new sur le repo Hermes")

    assert decision.action == "repo_task"
    assert decision.mode == "pilote"
    assert decision.intent == "debug_fix"
    assert decision.requires_active_repo is True


def test_classify_libre_message_routes_confident_autopilot_request():
    decision = classify_libre_message("fais le fix simple en autopilot si les tests passent")

    assert decision.action == "repo_task"
    assert decision.mode == "autopilot"
    assert decision.intent == "debug_fix"


def test_classify_libre_message_detects_repo_switch_request():
    decision = classify_libre_message("passe sur le repo Starter Pack Studio")

    assert decision.action == "switch_repo"
    assert decision.mode == "pilote"
    assert decision.intent == "switch_repo"


def test_classify_libre_message_detects_resume_intent():
    decision = classify_libre_message("reprends le chantier d'hier")

    assert decision.action == "resume"
    assert decision.intent == "resume"


def test_extract_learning_policy_detects_plan_model_reasoning():
    policy = extract_learning_policy("Pour les plans importants mets toi en GPT-5.5 xhigh")

    assert policy is not None
    assert policy["scope"] == "planning"
    assert policy["model"] == "gpt-5.5"
    assert policy["reasoning_effort"] == "xhigh"


def test_active_work_store_soft_close_keeps_handoff_note(tmp_path: Path):
    store = ActiveWorkStore(tmp_path / "libre_state.json")
    key = "telegram:chat:thread:user"
    store.set_active(
        key,
        repo="MFcv1/hermes-agent",
        mode="pilote",
        task="Améliorer /new",
        task_id="op_123",
        thread_id="thread_123",
    )

    note = store.soft_close(key, reason="/libre")

    assert "MFcv1/hermes-agent" in note["summary"]
    assert note["reason"] == "/libre"
    assert store.get_active(key)["mode"] == "libre"
    assert note["task_id"] == "op_123"
    assert store.latest_handoff(key)["thread_id"] == "thread_123"


def test_handoff_store_migrates_legacy_json_and_consumes(tmp_path: Path):
    legacy = tmp_path / "state.json"
    legacy.write_text(
        """
        {
          "contexts": {"telegram:100::42": {"repo": "MFcv1/demo", "mode": "pilote", "task_id": "op_old"}},
          "handoffs": [{"id": "handoff_old", "conversation_key": "telegram:100::42", "task_id": "op_old", "summary": "old handoff", "created_at": 1}],
          "policies": []
        }
        """,
        encoding="utf-8",
    )
    store = HandoffStore(tmp_path / "handoffs.sqlite", legacy_json_path=legacy)

    active = store.get_active("telegram:100::42")
    latest = store.latest_handoff("telegram:100::42")
    consumed = store.mark_consumed("telegram:100::42")

    assert active["repo"] == "MFcv1/demo"
    assert latest["task_id"] == "op_old"
    assert consumed["consumed_at"] is not None
    assert store.latest_handoff("telegram:100::42") is None


def test_scan_watch_logs_reports_repeated_errors(tmp_path: Path):
    log = tmp_path / "gateway.log"
    log.write_text(
        "INFO ok\nERROR callback rcn:intent failed\nWARNING minor\nTraceback boom\nERROR callback rcn:intent failed again\n",
        encoding="utf-8",
    )

    report = scan_watch_logs([log], limit=10)

    assert report["status"] == "attention"
    assert report["error_count"] == 3
    assert any("callback" in item["line"] for item in report["items"])
