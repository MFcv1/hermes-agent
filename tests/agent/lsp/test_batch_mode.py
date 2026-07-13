from __future__ import annotations

from unittest.mock import patch

from agent.lsp.manager import LSPService


def test_batch_mode_disables_implicit_lsp(monkeypatch):
    monkeypatch.setenv("_HERMES_BATCH_MODE", "1")
    with patch("hermes_cli.config.load_config", return_value={"lsp": {"enabled": True}}):
        service = LSPService.create_from_config()

    assert service is not None
    assert not service.is_active()


def test_batch_mode_allows_explicit_lsp_opt_in(monkeypatch):
    monkeypatch.setenv("_HERMES_BATCH_MODE", "1")
    config = {"lsp": {"enabled": True, "batch_enabled": True}}
    with patch("hermes_cli.config.load_config", return_value=config):
        service = LSPService.create_from_config()

    assert service is not None
    assert service.is_active()
    service.shutdown()
