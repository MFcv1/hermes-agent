from work_sessions import WorkSessionStore


def test_work_session_create_filter_resume_and_delete(tmp_path):
    home = tmp_path / "hermes-home"
    with WorkSessionStore(hermes_home=home) as store:
        session = store.create_session(
            title="Fix deploy preview",
            workflow="supervisor",
            origin_channel="telegram",
            repo="hermes-agent",
            provider="cloudflare",
            cockpit_task_id="op_123",
            objective="Fix the 502 preview deploy",
            metadata={"telegram_user_id": "42"},
        )

        assert session["id"].startswith("ws_")
        assert session["repo"] == "hermes-agent"
        assert session["workflow"] == "supervisor"
        assert session["metadata"]["telegram_user_id"] == "42"

        store.update_session(
            session["id"],
            status="blocked",
            git_branch="work/deploy-preview",
            pr_url="https://github.com/acme/hermes-agent/pull/123",
            preview_url="https://preview.example",
            summary="Cloudflare preview returns 502.",
            current_state="blocked on smoke deploy",
            next_actions=["check Cloudflare env", "redeploy preview"],
        )
        store.add_event(
            session["id"],
            "smoke.failed",
            role="tool",
            content="Preview returned HTTP 502",
            payload={"status_code": 502},
        )
        artifact = home / "work-sessions" / session["id"] / "smoke.json"
        artifact.write_text('{"status":502}')
        store.add_artifact(
            session["id"],
            kind="smoke",
            path=str(artifact),
            label="Latest smoke result",
        )

        filtered = store.list_sessions(repo="hermes-agent", status="blocked")
        assert [item["id"] for item in filtered] == [session["id"]]

        packet = store.resume_packet(session["id"])
        assert packet["work_session_id"] == session["id"]
        assert packet["repo"] == "hermes-agent"
        assert packet["git_branch"] == "work/deploy-preview"
        assert packet["next_actions"] == ["check Cloudflare env", "redeploy preview"]
        assert packet["artifacts"][0]["label"] == "Latest smoke result"
        assert packet["recent_events"][-1]["event_type"] == "smoke.failed"

        prompt = store.resume_prompt(session["id"])
        assert "Paquet de reprise JSON" in prompt
        assert "Fix deploy preview" in prompt

        session_dir = home / "work-sessions" / session["id"]
        assert session_dir.exists()
        assert store.delete_session(session["id"]) is True
        assert store.get_session(session["id"]) is None
        assert not session_dir.exists()


def test_work_session_delete_many_counts_existing_only(tmp_path):
    with WorkSessionStore(hermes_home=tmp_path) as store:
        one = store.create_session(title="One")
        two = store.create_session(title="Two")

        deleted = store.delete_many([one["id"], "ws_missing", two["id"]])

        assert deleted == 2
        assert store.list_sessions() == []


def test_work_session_can_be_archived_and_reopened(tmp_path):
    with WorkSessionStore(hermes_home=tmp_path) as store:
        session = store.create_session(title="Project chat", repo="acme/project")

        archived = store.update_session(session["id"], status="archived")
        assert archived is not None
        assert archived["status"] == "archived"
        assert archived["closed_at"] is not None

        reopened = store.update_session(session["id"], status="open")
        assert reopened is not None
        assert reopened["status"] == "open"


def test_get_by_hermes_session_id_returns_linked_session(tmp_path):
    with WorkSessionStore(hermes_home=tmp_path) as store:
        session = store.create_session(
            title="Linked Telegram chat",
            hermes_session_id="session-123",
        )

        linked = store.get_by_hermes_session_id("session-123")

        assert linked is not None
        assert linked["id"] == session["id"]
        assert store.get_by_hermes_session_id("missing") is None
