---
name: hermes-vps-repo-cockpit-ops
description: "Use when operating Hermes on the VPS with Telegram and Repo Cockpit: update preflight, CUA smoke tests, /conv, /audit, weekly updatecheck, and safety gates."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [hermes, vps, telegram, repo-cockpit, updatecheck, cua, operations]
    related_skills: [hermes-agent-skill-authoring, systematic-debugging, test-driven-development]
---

# Hermes VPS / Repo Cockpit Operations

## Overview

Use this runbook when working on the Hermes VPS, Telegram gateway, or Repo
Cockpit flows. The goal is to move fast without breaking the live bot:
preflight first, keep changes at the edge, prove behavior with targeted tests,
and only perform live operations after explicit operator approval.

This skill is the durable version of the 2026-06-27 Hermes/Repo Cockpit
roadmap implementation pass.

## When to Use

- The user asks to update, audit, or stabilize Hermes on the VPS.
- The task touches Telegram gateway behavior, Repo Cockpit `/conv`, `/audit`,
  approvals, heartbeat, notification noise, or rich Telegram rendering.
- The user wants weekly updatecheck, release monitoring, watchers, or
  automation blueprints for Hermes operations.
- The user asks to turn a working VPS/Telegram procedure into a repeatable
  runbook or skill.

Do not use this skill for unrelated app work, generic Python debugging, or
non-Hermes infrastructure unless the procedure will affect the Hermes bot.

## Safety Invariants

1. Do not run `hermes update`, reboot, restore files, arm timers, or restart
   live services unless the user explicitly approves that exact action.
2. Keep prompt caching safe: do not add core model tools for Telegram or Repo
   Cockpit convenience. Prefer existing code, CLI command plus skill, service
   gate, plugin, or MCP.
3. Treat `.env` as secrets-only. Behavioral settings belong in `config.yaml`.
4. For Telegram Desktop validation, use CUA Driver. Do not fall back to legacy
   macOS UI scripting.
5. Never send a live Telegram smoke message unless the user confirms the target
   chat/thread is open and sending is intended.
6. Before VPS maintenance, prove write roots and backup coverage:

```bash
venv/bin/python -m pytest tests/scripts/test_vps_write_roots_audit.py -q
python3 scripts/vps_smoke_matrix.py --json
```

## Durable run lineage and completion

Every supervised mission must keep one immutable `run_id` from the first
Supervisor invocation through the final report. Pass known task/session/model,
GitHub and deployment identifiers explicitly; the supervisor writes a
hash-chained append-only ledger:

```bash
python3 scripts/codex_supervisor_mode.py \
  --message "<brief>" \
  --skip-telegram \
  --task-id "<task-id>" --watch-task \
  --session-id "<gateway-session>" \
  --github-repo "<owner/repo>" --github-branch "<branch>" \
  --provider "<observed-provider>" --model "<observed-model>" \
  --budget-calls "<limit>" --used-calls "<observed>" \
  --ledger-path "$HOME/.hermes/supervisor-runs/ledger.jsonl"
```

Do not accept `completed`, `done` or `deployed_preview` without a validated
`PROJECT_STATUS.md`. Start from
`docs/project/supervision/PROJECT_STATUS_TEMPLATE.md`; it must contain the full
source SHA, gates, URLs, resources/limits, rollback and next action. Use
`--project-root` when the project is locally visible. If Cockpit is remote, its
task payload must expose equivalent `project_status` evidence.

## Self-Ops effectiveness and retention

An exit code zero proves that a cleanup command executed, not that it achieved
its objective. Measure filesystem bytes before and after and record removed
paths with `scripts/selfops_effectiveness_guard.py`. Two consecutive successful
commands below the configured delta become `ineffective`, trigger one
escalation with top consumers and suspend that action for 24 hours.

Inventory Kanban retention candidates before deletion:

```bash
hermes kanban gc --event-retention-days 30 --log-retention-days 30 --dry-run
```

Follow `docs/project/supervision/HERMES_RETENTION_POLICY.md`. Never count a
zero-delta run as remediation and never automatically remove active-task,
Supervisor-ledger or N/N-1 rollback evidence.

## VPS changes remain plans until approval

`python3 scripts/vps_maintenance_plan.py --json` generates locally validated
systemd hardening, a durable loopback-only dashboard unit, release-from-SHA,
offsite Restic backup and restore-drill plans. Generation is read-only. Do not
install units, reload/restart services, resize the VPS, create snapshots, alter
GitHub rulesets, or modify Tailscale ACL/device state without explicit approval
for that exact operation.

## Repo Cockpit Telegram Flows

### `/conv`

Expected operator experience:

1. User opens `/conv`.
2. User chooses an existing repo or new repo flow.
3. Telegram confirms the selected repo and selected mode.
4. Telegram tells the user to send the task in this chat.
5. Compact follow-up buttons remain available: change repo, change mode,
   cancel.

Verification:

```bash
venv/bin/python -m pytest tests/gateway/test_telegram_conv_ux.py -q
```

### `/audit` / `/auditer`

Use `/audit` for long Repo Cockpit checks that should not block the Telegram
chat. The command should:

- read the active `/conv` thread;
- create a bounded read-only task;
- return job id, task id, repo, mode, phase, `/status`, and `/runs`;
- invoke the worker with `execute: false`;
- forbid repo mutation, deploy, service restart, and destructive actions.

Verification:

```bash
venv/bin/python -m pytest tests/gateway/test_telegram_conv_ux.py -q
```

## Telegram Reliability Checks

Run these after changes to gateway noise, heartbeat, Telegram formatting,
approval resolution, or status/progress messages:

```bash
venv/bin/python -m pytest \
  tests/gateway/test_gateway_noise_suppression.py \
  tests/gateway/test_human_heartbeat.py \
  tests/gateway/test_busy_session_ack.py \
  tests/gateway/test_telegram_format.py \
  tests/gateway/test_telegram_rich_messages.py \
  tests/gateway/test_telegram_status_update.py \
  tests/gateway/test_telegram_progress_edit_transient.py \
  tests/gateway/test_approve_deny_commands.py::TestBlockingGatewayApproval -q
```

Interpretation:

- Progress/intermediate messages should remain silent.
- Final/user-triggered messages may notify.
- Busy acks should use user-facing language, not provider/compression jargon.
- Approval state must remain session-scoped.

## Telegram Desktop CUA Smoke

Default dry-run captures evidence only:

```bash
python3 scripts/telegram_desktop_cua_smoke.py \
  --message 'smoke dry-run: reponds juste OK smoke'
```

Live send requires the operator to open Telegram Desktop on the intended chat:

```bash
python3 scripts/telegram_desktop_cua_smoke.py \
  --message 'smoke normal chat: reponds juste OK smoke' \
  --send
```

Common statuses:

| Status | Meaning | Next action |
|---|---|---|
| `screenshot_review_required` | CUA captured Telegram but did not send | Inspect evidence, then rerun with `--send` if correct |
| `sent_review_required` | CUA typed/sent | Inspect Telegram response |
| `telegram_window_not_on_current_space` | Telegram runs but is not visible to CUA | Bring Telegram Desktop to the current visible Space/chat |
| `missing_cua_driver` | CUA Driver unavailable | Install/repair CUA Driver before live smoke |

Evidence is written to `~/.hermes/telegram-gui-smoke/`.

## Weekly Updatecheck

The `weekly-updatecheck` automation must remain no-agent and script-backed:

- blueprint key: `weekly-updatecheck`
- script: `cron/scripts/weekly_updatecheck.py`
- installed runtime script: `HERMES_HOME/scripts/weekly_updatecheck.py`
- default behavior: silent when unchanged; notify only on RED/YELLOW, new
  update, or changed status.

Do not create or arm the production job until the user provides:

- Telegram delivery target/thread;
- cadence/day/time;
- whether to create paused first or active immediately.

Verification:

```bash
venv/bin/python -m pytest tests/cron/test_blueprint_catalog.py tests/cron/test_cron_no_agent.py -q
```

## Backup And Update Preflight

Before any VPS update:

1. Confirm quick backup covers Hermes kanban state.
2. Confirm Repo Cockpit external DBs are covered by the VPS write-roots audit,
   not by `hermes backup`.
3. Confirm the live worktree status before touching services.
4. Generate a read-only maintenance plan before applying anything.

Commands:

```bash
venv/bin/python -m pytest \
  tests/hermes_cli/test_backup.py::TestQuickSnapshot \
  tests/scripts/test_vps_write_roots_audit.py \
  tests/scripts/test_vps_maintenance_plan.py \
  tests/scripts/test_vps_smoke_matrix.py -q
```

## Turning Procedures Into Skills

When a procedure works twice, turn it into a skill or runbook section. Good
candidates from this work:

- `/conv` repo selection and follow-up UX;
- `/audit` bounded background Repo Cockpit review;
- VPS update preflight and rollback drill;
- Telegram Desktop CUA smoke;
- weekly updatecheck activation;
- Repo Cockpit debugging and smoke evidence collection.

Completion criterion: the next agent should be able to run the procedure from
the skill without reading the original implementation chat.

## Common Pitfalls

1. **Treating a dry-run as live validation.** A dry-run proves tooling and
   evidence capture, not chat delivery.
2. **Arming cron too early.** Blueprint creation is safe in temp homes; prod
   activation needs target thread and cadence.
3. **Duplicating Repo Cockpit workspace logic.** Projects/worktrees are useful
   later, but Repo Cockpit already owns repo/workspace behavior.
4. **Adding core tools for convenience.** Telegram/Repo Cockpit ops should stay
   in commands, skills, service-gated tools, plugins, or MCP.
5. **Ignoring dirty worktrees.** Preserve unrelated user changes; do not reset
   or checkout files unless explicitly asked.

## Verification Checklist

- [ ] Targeted tests passed for the changed surface.
- [ ] Combined roadmap tests updated if the roadmap changed.
- [ ] Roadmap and feature/learning handoff updated.
- [ ] No live VPS service action was taken without explicit approval.
- [ ] No live Telegram send was performed without explicit approval.
- [ ] Any new procedure that should survive future sessions is captured here or
      in a more focused skill.
