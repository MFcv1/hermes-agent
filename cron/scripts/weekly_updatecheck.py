#!/usr/bin/env python3
"""No-agent cron watchdog for Hermes update readiness.

Install/copy this file into ``HERMES_HOME/scripts/weekly_updatecheck.py`` and
create a cron job with ``no_agent=True``. Empty stdout means no notification.
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--cached", action="store_true")
    args = parser.parse_args()

    project = _project_root()
    sys.path.insert(0, str(project))

    from hermes_cli.updatecheck import (  # pylint: disable=import-error,import-outside-toplevel
        _default_state_path,
        _load_state,
        _state_from_report,
        _write_state,
        collect_updatecheck,
        evaluate_notification,
        format_updatecheck,
    )
    from hermes_constants import get_hermes_home  # pylint: disable=import-error,import-outside-toplevel

    hermes_home = get_hermes_home()
    report = collect_updatecheck(
        project_root=project,
        hermes_home=hermes_home,
        fresh=not args.cached,
        fetch_timeout=args.timeout,
    )
    state_path = _default_state_path(hermes_home)
    decision = evaluate_notification(report, _load_state(state_path))
    report["notification"] = {**decision, "state_path": str(state_path)}
    _write_state(state_path, _state_from_report(report))

    if not decision["should_notify"]:
        return 0

    print(format_updatecheck(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
