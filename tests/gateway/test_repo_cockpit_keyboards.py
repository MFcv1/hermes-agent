from gateway.repo_cockpit_keyboards import (
    autonomy_keyboard,
    new_chat_keyboard,
    pending_prs_keyboard,
    pilot_existing_intent_keyboard,
    repo_button_label,
    repo_new_chat_keyboard,
    repo_selected_keyboard,
)


class FakeButton:
    def __init__(self, text, **kwargs):
        self.text = text
        self.kwargs = kwargs


class FakeMarkup:
    def __init__(self, rows):
        self.rows = rows


class FakeWebAppInfo:
    def __init__(self, url):
        self.url = url


def _labels(markup):
    return [[button.text for button in row] for row in markup.rows]


def test_new_chat_keyboard_keeps_mode_callbacks_and_selected_label():
    markup = new_chat_keyboard("pilote", button=FakeButton, markup=FakeMarkup)

    assert _labels(markup)[0] == ["Ask review", "✓ Pilote"]
    assert markup.rows[0][1].kwargs == {"callback_data": "rcn:mode:pilote"}
    assert markup.rows[2][0].kwargs == {"callback_data": "rcn:existing:pilote"}
    assert markup.rows[3][0].kwargs == {"callback_data": "rcn:scratch:pilote"}


def test_pilot_existing_intent_keyboard_keeps_routes():
    markup = pilot_existing_intent_keyboard("autopilot", button=FakeButton, markup=FakeMarkup)

    callbacks = [row[0].kwargs.get("callback_data") for row in markup.rows[:6]]
    assert callbacks == [
        "rcn:intent:audit_repo:autopilot",
        "rcn:intent:feature_work:autopilot",
        "rcn:intent:debug_fix:autopilot",
        "rcn:intent:deploy:autopilot",
        "rcn:intent:review_harden:autopilot",
        "rcn:intent:pilot_discovery:autopilot",
    ]


def test_repo_new_chat_keyboard_keeps_repo_rows_webapp_and_actions():
    repos = [
        {"nameWithOwner": "MFcv1/demo", "isPrivate": True},
        {"nameWithOwner": "MFcv1/public-demo", "isPrivate": False},
    ]

    markup = repo_new_chat_keyboard(
        "123",
        "pilote",
        repos,
        "https://cockpit.example/app",
        button=FakeButton,
        markup=FakeMarkup,
        web_app_info=FakeWebAppInfo,
    )

    assert repo_button_label(repos[0]) == "demo · privé"
    assert _labels(markup)[0] == ["demo · privé"]
    assert markup.rows[0][0].kwargs == {"callback_data": "rcnr:pilote:0"}
    assert markup.rows[2][0].kwargs["web_app"].url == "https://cockpit.example/app"
    assert _labels(markup)[-1] == ["Actualiser", "Annuler"]


def test_repo_selected_keyboard_keeps_mode_switches():
    markup = repo_selected_keyboard("pilote", button=FakeButton, markup=FakeMarkup)

    assert _labels(markup) == [
        ["Changer repo", "Ask review"],
        ["Pilote", "Autopilot"],
        ["Annuler"],
    ]
    assert markup.rows[0][0].kwargs == {"callback_data": "rcn:existing:pilote"}


def test_pending_prs_keyboard_keeps_links_and_task_actions():
    markup = pending_prs_keyboard(
        {
            "prs": [
                {
                    "repo": "MFcv1/tennis-coach-platform",
                    "task_id": "op_gate_pr_required_4939a587",
                    "title": "Fix tennis deploy",
                    "pr_url": "https://github.com/MFcv1/demo/pull/1",
                    "preview_url": "https://preview.example",
                }
            ]
        },
        button=FakeButton,
        markup=FakeMarkup,
    )

    assert _labels(markup)[:4] == [
        ["PR tennis · 39a587"],
        ["Preview tennis · 39a587"],
        ["Status tennis · 39a587", "Runs tennis · 39a587"],
        ["Résumé tennis · 39a587"],
    ]
    assert markup.rows[0][0].kwargs == {"url": "https://github.com/MFcv1/demo/pull/1"}
    assert markup.rows[2][0].kwargs == {"callback_data": "rca:status:op_gate_pr_required_4939a587"}


def test_autonomy_keyboard_hides_blocked_preview_and_switches_view():
    blocked = autonomy_keyboard(
        {
            "task": {
                "id": "op_abc",
                "status": "blocked_tests",
                "preview_url": "https://preview.example",
            }
        },
        "status",
        button=FakeButton,
        markup=FakeMarkup,
    )
    runs = autonomy_keyboard(
        {"task": {"id": "op_abc", "status": "running", "preview_url": "https://preview.example"}},
        "runs",
        button=FakeButton,
        markup=FakeMarkup,
    )

    assert _labels(blocked) == [["Runs", "Rafraîchir"], ["Threads"]]
    assert _labels(runs) == [["Ouvrir preview"], ["Status", "Rafraîchir"], ["Threads"]]
    assert runs.rows[1][0].kwargs == {"callback_data": "rca:status:op_abc"}
