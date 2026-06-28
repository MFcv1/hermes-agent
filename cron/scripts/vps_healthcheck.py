#!/usr/bin/env python3
"""No-agent VPS healthcheck for Hermes cron jobs.

Default behavior is quiet on green and prints a compact report on warning/error.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _project_root() -> Path:
    configured = os.environ.get("HERMES_UPDATECHECK_PROJECT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".hermes" / "hermes-agent").resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hermes VPS healthcheck")
    parser.add_argument("--always", action="store_true", help="print even when green")
    args = parser.parse_args(argv)

    project = _project_root()
    sys.path.insert(0, str(project))

    from hermes_cli.vps_status import (  # pylint: disable=import-error,import-outside-toplevel
        collect_vps_overview,
        format_vps_overview,
    )

    report = collect_vps_overview()
    if str(report.get("status")) == "green" and not args.always:
        return 0
    print(format_vps_overview(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
