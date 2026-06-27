"""Tests for the /updatecheck command."""

from types import SimpleNamespace
from unittest.mock import patch

from cli import HermesCLI
from hermes_cli.commands import (
    ACTIVE_SESSION_BYPASS_COMMANDS,
    GATEWAY_KNOWN_COMMANDS,
    resolve_command,
)


def test_updatecheck_command_is_registered():
    cmd = resolve_command("updatecheck")
    assert cmd is not None
    assert cmd.name == "updatecheck"
    assert cmd.category == "Info"
    assert resolve_command("update-check") is cmd


def test_updatecheck_is_gateway_known_and_running_bypass():
    assert "updatecheck" in GATEWAY_KNOWN_COMMANDS
    assert "update-check" in GATEWAY_KNOWN_COMMANDS
    assert "updatecheck" in ACTIVE_SESSION_BYPASS_COMMANDS


def test_process_command_updatecheck_runs_readonly_report():
    cli_obj = HermesCLI.__new__(HermesCLI)

    with patch("hermes_cli.updatecheck.run_updatecheck", return_value=0) as mock_run:
        assert cli_obj.process_command("/updatecheck") is True

    args = mock_run.call_args.args[0]
    assert isinstance(args, SimpleNamespace)
    assert args.cached is False
    assert args.json is False
    assert args.timeout == 20
    assert args.stateful is False
    assert args.silent_unchanged is False
    assert args.state_path is None
