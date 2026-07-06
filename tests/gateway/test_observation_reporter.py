from gateway.observation_reporter import (
    RAW_EXCERPT_LIMIT,
    build_legacy_runtime_observation_payload,
    build_runtime_observation_v2,
    mask_observation_secrets,
    post_runtime_observations,
    runtime_observations_from_watch_report,
)


def test_build_runtime_observation_v2_truncates_masks_and_omits_fingerprint():
    payload = build_runtime_observation_v2(
        task_id="op_123",
        raw_excerpt="API_KEY=sk-test-secret " + ("x" * 5000),
        source="telegram_autonomous_worker",
        phase="test",
        command="pytest",
        detected_at="2026-07-06T15:00:00Z",
    )

    assert payload["schema_version"] == 2
    assert payload["task_id"] == "op_123"
    assert payload["run_id"] is None
    assert payload["source"] == "telegram_autonomous_worker"
    assert payload["phase"] == "test"
    assert payload["command"] == "pytest"
    assert payload["detected_at"] == "2026-07-06T15:00:00Z"
    assert payload["raw_excerpt"].startswith("API_KEY=<secret-hidden>")
    assert len(payload["raw_excerpt"]) == RAW_EXCERPT_LIMIT
    assert "fingerprint" not in payload


def test_build_runtime_observation_requires_task_id():
    try:
        build_runtime_observation_v2(task_id="", raw_excerpt="boom")
    except ValueError as exc:
        assert "task_id" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_watch_report_maps_items_to_v2_observations():
    observations = runtime_observations_from_watch_report(
        task_id="op_123",
        report={
            "severity": "high",
            "items": [
                {"line": "ModuleNotFoundError: No module named foo", "phase": "test", "command": "pytest"},
                "failed service on :3000",
            ],
        },
        detected_at="2026-07-06T15:00:00Z",
    )

    assert len(observations) == 2
    assert observations[0]["phase"] == "test"
    assert observations[0]["command"] == "pytest"
    assert observations[0]["severity"] == "high"
    assert observations[1]["raw_excerpt"] == "failed service on :3000"


def test_legacy_payload_preserves_current_wire_shape_and_masks_report():
    payload = build_legacy_runtime_observation_payload(
        task_id="op_123",
        report={"status": "attention", "items": [{"line": "token: abcdefghijk"}]},
        captured_at=1720000000,
    )

    assert payload == {
        "source": "telegram_runtime_observer",
        "task_id": "op_123",
        "report": {
            "status": "attention",
            "items": [{"line": "token=<secret-hidden>"}],
        },
        "captured_at": 1720000000,
    }


def test_post_runtime_observations_defaults_to_legacy_payload():
    calls = []

    def api_sync(method, path, payload, timeout):
        calls.append((method, path, payload, timeout))
        return {"ok": True}

    result = post_runtime_observations(
        api_sync,
        task_id="op_123",
        report={"items": [{"line": "failed"}]},
        timeout=9,
    )

    assert result == {"ok": True}
    assert calls[0][0] == "POST"
    assert calls[0][1] == "/api/internal/tasks/op_123/runtime-observations"
    assert "schema_version" not in calls[0][2]
    assert calls[0][2]["report"]["items"][0]["line"] == "failed"
    assert calls[0][3] == 9


def test_post_runtime_observations_requires_task_id_before_posting():
    def api_sync(method, path, payload, timeout):
        raise AssertionError("api_sync should not be called")

    try:
        post_runtime_observations(api_sync, task_id="", report={"items": []}, prefer_v2=True)
    except ValueError as exc:
        assert "task_id" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_post_runtime_observations_can_emit_v2_items():
    calls = []

    def api_sync(method, path, payload, timeout):
        calls.append((method, path, payload, timeout))
        return {"ok": True}

    result = post_runtime_observations(
        api_sync,
        task_id="op_123",
        report={"items": [{"line": "failed one"}, {"line": "failed two"}]},
        prefer_v2=True,
    )

    assert result["ok"] is True
    assert len(calls) == 2
    assert calls[0][2]["schema_version"] == 2
    assert calls[1][2]["raw_excerpt"] == "failed two"


def test_mask_observation_secrets_uses_secret_env_values():
    masked = mask_observation_secrets(
        "value=super-secret-token",
        env={"SERVICE_TOKEN": "super-secret-token", "OTHER": "visible"},
    )

    assert masked == "value=<secret-hidden>"
