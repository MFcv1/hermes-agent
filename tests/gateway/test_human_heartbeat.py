from gateway.human_heartbeat import (
    classify_agent_status,
    classify_repo_cockpit_status,
    format_elapsed,
    has_technical_leak,
    progress_from_autonomy,
    render_from_activity,
    render_human_heartbeat,
    render_progress_view,
    HumanHeartbeat,
)


def test_format_elapsed_seconds_and_minutes():
    assert format_elapsed(8) == "8s"
    assert format_elapsed(60) == "1 min"
    assert format_elapsed(74) == "1 min 14s"


def test_classifies_non_streaming_response_as_human_analysis():
    phase, state = classify_agent_status(
        {"last_activity_desc": "waiting for non-streaming API response"}
    )
    assert phase == "Analyse"
    assert "modele" in state


def test_classifies_api_error_recovery_without_leaking_raw_text():
    text = render_from_activity(
        elapsed_seconds=10,
        activity={"current_tool": "API error recovery (attempt 2/3)", "api_call_count": 1, "max_iterations": 60},
        model="gpt-5.5-high",
        mode="autopilot",
    )
    assert "Correction" in text
    assert "GPT-5.5" in text
    assert "autopilot" in text
    assert "iteration" not in text.lower()
    assert "API error recovery" not in text
    assert not has_technical_leak(text)


def test_render_never_outputs_internal_iteration_jargon():
    text = render_human_heartbeat(
        HumanHeartbeat(
            elapsed_seconds=12,
            phase="Implementation",
            state="iteration 1/60, waiting for non-streaming API response",
            model="codex",
        )
    )
    assert "iteration 1/60" not in text
    assert "non-streaming" not in text.lower()
    assert not has_technical_leak(text)


def test_repo_cockpit_major_statuses_map_to_human_phases():
    cases = {
        "queued_plan": "Analyse",
        "running_quota": "Analyse",
        "running_triage": "Analyse",
        "running_plan": "Plan",
        "plan_ready": "Plan",
        "waiting_plan_approval": "Attente validation",
        "running_gpt55": "Implementation",
        "running_review_remediation": "Implementation",
        "running_tests": "Tests",
        "blocked_tests": "Tests",
        "running_independent_review": "Audit",
        "running_pr": "Audit",
        "running_deploy_preview": "Deploy",
        "deployed_preview": "Deploy",
        "blocked_deploy": "Deploy",
        "blocked_smoke": "Deploy",
        "blocked_quota": "Pause quota",
        "blocked_auth_redirect": "Action requise",
    }
    for status, expected_phase in cases.items():
        _, phase, _, _, _ = classify_repo_cockpit_status(status)
        assert phase == expected_phase


def test_autonomy_progress_renders_task_repo_preview_without_internal_jargon():
    progress = progress_from_autonomy(
        {
            "task": {
                "id": "op_123",
                "repo": "MFcv1/hermes-tennis",
                "status": "running_deploy_preview",
                "mode": "autopilot",
                "preview_url": "https://example.vercel.app",
                "model": "gpt-5.5-xhigh",
            }
        },
        elapsed_seconds=135,
    )
    text = render_progress_view(progress)
    assert "Deploy" in text
    assert "2 min 15s" in text
    assert "op_123" in text
    assert "MFcv1/hermes-tennis" in text
    assert "https://example.vercel.app" in text
    assert "GPT-5.5" in text
    assert not has_technical_leak(text)
