from fastapi.testclient import TestClient


def test_work_sessions_api_create_resume_delete(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    from hermes_cli import web_server

    previous_auth_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.auth_required = False
    client = TestClient(web_server.app)
    client.headers[web_server._SESSION_HEADER_NAME] = web_server._SESSION_TOKEN
    try:
        created = client.post(
            "/api/work-sessions",
            json={
                "title": "Fix deploy preview",
                "workflow": "supervisor",
                "origin_channel": "telegram",
                "repo": "hermes-agent",
                "objective": "Fix preview 502",
            },
        )
        assert created.status_code == 200
        session = created.json()["work_session"]
        assert session["id"].startswith("ws_")
        assert session["repo"] == "hermes-agent"

        listed = client.get("/api/work-sessions", params={"repo": "hermes-agent"})
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()["work_sessions"]] == [session["id"]]

        patched = client.patch(
            f"/api/work-sessions/{session['id']}",
            json={
                "status": "blocked",
                "summary": "Preview returns 502.",
                "next_actions": ["check env", "redeploy"],
            },
        )
        assert patched.status_code == 200
        assert patched.json()["work_session"]["status"] == "blocked"

        packet = client.get(f"/api/work-sessions/{session['id']}/resume-packet")
        assert packet.status_code == 200
        assert packet.json()["resume_packet"]["next_actions"] == ["check env", "redeploy"]

        deleted = client.delete(f"/api/work-sessions/{session['id']}")
        assert deleted.status_code == 200
        assert deleted.json() == {"ok": True, "deleted": True}
    finally:
        web_server.app.state.auth_required = previous_auth_required

