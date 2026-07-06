from gateway.repo_cockpit_text import (
    audit_task_text,
    format_audit_blocked,
    format_audit_completed,
    format_audit_started,
    mode_note,
    mode_title,
    new_chat_text,
    pilot_intent_title,
    pilot_waiting_prompt_text,
    project_created_text,
    repo_selected_text,
    tasks_list_text,
)


def test_mode_title_and_note_cover_modes():
    assert mode_title("pilote") == "Pilote"
    assert mode_title("autopilot") == "Autopilot"
    assert mode_title("unknown") == "Ask review"
    assert "Architect/Deploy" in mode_note("pilote")
    assert "validation avant merge" in mode_note("ask_review")


def test_pilot_intent_title_defaults_and_known_routes():
    assert pilot_intent_title("deploy") == "Déployer / vérifier prod"
    assert pilot_intent_title("feature_work") == "Modifier / ajouter une feature"
    assert pilot_intent_title("missing") == "Architect / cadrage"


def test_pilot_waiting_prompt_text_keeps_existing_html_shape():
    text = pilot_waiting_prompt_text(
        origin="github_existing",
        intent="debug_fix",
        reasoning="high",
        repo="MFcv1/demo<unsafe>",
    )

    assert "<b>🧭 Pilote prêt</b>" in text
    assert "Source : <b>Projet GitHub existant</b>" in text
    assert "Route : <b>Corriger un bug</b>" in text
    assert "Plan : <b>high</b>" in text
    assert "Repo : <code>MFcv1/demo&lt;unsafe&gt;</code>" in text
    assert "Pas besoin de <code>/task</code>" in text


def test_repo_selected_text_keeps_actionable_copy_and_escapes_values():
    text = repo_selected_text("MFcv1/demo<unsafe>", "pilote", "thread_<123>")

    assert "<b>✅ Repo sélectionné</b>" in text
    assert "Repo : <code>MFcv1/demo&lt;unsafe&gt;</code>" in text
    assert "Mode : <b>Pilote</b>" in text
    assert "Conversation : <code>thread_&lt;123&gt;</code>" in text
    assert "Prochaine étape : envoie ta tâche directement dans ce chat." in text


def test_new_chat_text_keeps_mode_effect_and_repo_line():
    text = new_chat_text("autopilot", "MFcv1/demo<unsafe>")

    assert "<b>🧭 Nouveau chat Hermes</b>" in text
    assert "Mode : <b>Autopilot</b>" in text
    assert "secret scan" in text
    assert "Repo actuel : <code>MFcv1/demo&lt;unsafe&gt;</code>" in text
    assert "repo GitHub existant ou d'un nouveau projet" in text


def test_project_created_and_tasks_list_text_keep_existing_shape():
    created = project_created_text(
        {
            "title": "Demo <x>",
            "repo": "MFcv1/demo",
            "mode": "pilote",
            "thread_id": "thread_123",
        }
    )
    tasks = tasks_list_text([
        {"id": "op_1", "status": "queued", "repo": "MFcv1/demo"},
        {"id": "op_2", "status": "done", "repo": "MFcv1/demo2"},
    ])

    assert "Projet : <code>Demo &lt;x&gt;</code>" in created
    assert "Mode : <b>Pilote</b>" in created
    assert "Thread : <code>thread_123</code>" in created
    assert "<b>📋 Tâches Repo Cockpit</b>" in tasks
    assert "<code>op_1</code> · <b>queued</b> · MFcv1/demo" in tasks
    assert "Détail : <code>/task ID</code>" in tasks
    assert tasks_list_text([]) == "<b>📋 Tâches</b>\n\nAucune tâche."


def test_audit_text_helpers_keep_dry_run_contract_and_escape_output():
    active = {"repo": "MFcv1/demo", "thread_id": "thread_123", "thread_mode": "pilote"}
    task = {"id": "op_1", "repo": "MFcv1/demo", "status": "queued_plan", "mode": "pilote"}

    prompt = audit_task_text(active, "focus auth")
    started = format_audit_started(job_id="audit_1", task=task, active=active)
    completed = format_audit_completed(job_id="audit_1", task_id="op_1", status="done<ok>")
    blocked = format_audit_blocked(job_id="audit_1", task_id="op_1", error="boom <secret>")

    assert "sans modifier le repo" in prompt
    assert "Focus utilisateur : focus auth" in prompt
    assert "<b>🔎 Audit Repo Cockpit lancé</b>" in started
    assert "Mode : <b>Pilote</b>" in started
    assert "Suivi : <code>/status op_1</code> · <code>/runs op_1</code>" in started
    assert "Worker : <code>done&lt;ok&gt;</code>" in completed
    assert "<b>🔎 Audit Repo Cockpit bloqué</b>" in blocked
    assert "boom &lt;secret&gt;" in blocked
