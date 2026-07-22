from gateway.dashboard_links import build_url, hermes_dashboard_url, hermes_mini_app_url


def test_build_url_preserves_existing_query_and_adds_cache_bust(monkeypatch):
    monkeypatch.setattr("gateway.dashboard_links.time.time", lambda: 1234)

    url = build_url("https://hermes.example/base?token=abc", "/work-sessions", repo="agent")

    assert url == "https://hermes.example/base/work-sessions?token=abc&repo=agent&v=1234"


def test_hermes_mini_app_uses_dashboard_public_url(monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_PUBLIC_URL", "https://hermes.tailnet.ts.net/")
    monkeypatch.setattr("gateway.dashboard_links.time.time", lambda: 1234)

    url = hermes_mini_app_url("/work-sessions")

    assert url == "https://hermes.tailnet.ts.net/work-sessions?v=1234"


def test_hermes_mini_app_requires_dashboard_public_url(monkeypatch):
    monkeypatch.delenv("HERMES_DASHBOARD_PUBLIC_URL", raising=False)

    url = hermes_mini_app_url("/work-sessions")

    assert url == ""


def test_hermes_dashboard_uses_public_url(monkeypatch):
    monkeypatch.setenv("HERMES_DASHBOARD_PUBLIC_URL", "https://hermes.example/")
    monkeypatch.setattr("gateway.dashboard_links.time.time", lambda: 1234)

    assert hermes_dashboard_url() == "https://hermes.example/sessions?v=1234"


def test_hermes_dashboard_has_no_mini_app_fallback(monkeypatch):
    monkeypatch.delenv("HERMES_DASHBOARD_PUBLIC_URL", raising=False)

    assert hermes_dashboard_url() == ""
