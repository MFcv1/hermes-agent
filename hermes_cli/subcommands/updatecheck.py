"""``hermes updatecheck`` subcommand parser."""

from __future__ import annotations

from typing import Callable


def build_updatecheck_parser(subparsers, *, cmd_updatecheck: Callable) -> None:
    parser = subparsers.add_parser(
        "updatecheck",
        aliases=("update-check",),
        help="Read-only Hermes update readiness report",
        description=(
            "Check whether Hermes can be updated safely without pulling, "
            "installing, restarting, or changing skills/plugins."
        ),
    )
    parser.add_argument(
        "--cached",
        action="store_true",
        help="Do not fetch origin/main; report using currently available refs/cache.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20,
        help="Timeout in seconds for the scoped git fetch (default: 20).",
    )
    parser.add_argument(
        "--stateful",
        action="store_true",
        help="Record the latest updatecheck signature under HERMES_HOME.",
    )
    parser.add_argument(
        "--silent-unchanged",
        action="store_true",
        help="Print [SILENT] when the state is unchanged and healthy.",
    )
    parser.add_argument(
        "--state-path",
        help="Override the updatecheck state file path.",
    )
    parser.set_defaults(func=cmd_updatecheck)
