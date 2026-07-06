from gateway.repo_cockpit_formatting import (
    format_autonomy_status,
    format_pending_prs,
    format_pr_summary,
    format_runs_status,
    pending_pr_label,
    status_badge,
)


def test_pending_pr_label_matches_existing_short_tennis_label():
    item = {
        "repo": "MFcv1/tennis-coach-platform",
        "task_id": "op_gate_pr_required_4939a587",
        "title": "Fix tennis deploy",
    }

    assert pending_pr_label(item) == "tennis · 39a587"


def test_format_pending_prs_keeps_existing_html_shape():
    text = format_pending_prs(
        {
            "prs": [
                {
                    "task_id": "op_123456",
                    "repo": "MFcv1/demo",
                    "status": "blocked_review_required",
                    "title": "Add <safe> panel",
                    "branch": "codex/demo",
                    "smoke_status": "passed",
                    "updated_at": 1720000000,
                }
            ]
        }
    )

    assert "<b>🔀 PRs en attente</b>" in text
    assert "<b>1. MFcv1/demo</b>" in text
    assert "Add &lt;safe&gt; panel" in text
    assert "Status : <code>blocked_review_required</code>" in text
    assert "Maj : <code>2024-07-03 09:46 UTC</code>" in text


def test_format_pr_summary_extracts_pr_branch_checks_and_runs():
    text = format_pr_summary(
        {
            "task": {
                "id": "op_abc",
                "repo": "MFcv1/demo",
                "status": "done",
                "mode": "pilote",
                "result_json": {
                    "pr": {"pr_url": "https://github.com/MFcv1/demo/pull/1", "branch": "codex/demo"},
                    "preview_url": "https://preview.example",
                },
            },
            "smoke_tests": [{"status": "passed"}],
            "provider_checks": [{"status": "passed"}, {"status": "failed"}],
            "task_runs": [{"phase": "test", "status": "passed"}],
        }
    )

    assert "<b>🧾 Résumé PR</b>" in text
    assert "Task : <code>op_abc</code>" in text
    assert "Branche : <code>codex/demo</code>" in text
    assert "PR : https://github.com/MFcv1/demo/pull/1" in text
    assert "Provider checks : <code>1/2 OK</code>" in text
    assert "✅ <code>test</code> · passed" in text


def test_format_autonomy_and_runs_status_keep_badges_and_preview_blocking():
    data = {
        "task_id": "op_abc",
        "task": {
            "id": "op_abc",
            "repo": "MFcv1/demo",
            "status": "blocked_tests",
            "current_phase": "test",
            "preview_url": "https://preview.example",
        },
        "latest_error": {"category": "test_regression", "runbook": "syntax_error", "human_action_required": True},
        "provider_checks": [{"provider": "vercel", "check_name": "deploy", "status": "failed"}],
        "smoke_tests": [{"status": "queued", "url": "https://preview.example"}],
        "task_runs": [{"phase": "pytest", "status": "failed"}],
    }

    status_text = format_autonomy_status(data)
    runs_text = format_runs_status(data)

    assert status_badge("failed") == "🚨"
    assert "Preview non validée : https://preview.example" in status_text
    assert "Catégorie : <code>test_regression</code>" in status_text
    assert "🚨 vercel/deploy : <code>failed</code>" in status_text
    assert "<b>🧪 Runs / gates</b>" in runs_text
    assert "🚨 <code>pytest</code> · <b>failed</b>" in runs_text
