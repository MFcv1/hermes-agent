# Hermes Concrete Implementation Roadmap - 2026-06-27

Audience: next Codex/Hermes implementation chat.

Goal: implement concrete roadmap features step by step. For each step: code,
test, validate, update this file, then move to the next step.

## Operating Rules

- Keep this file updated as the source of truth.
- One implementation slice should be small enough to finish in one focused turn.
- Telegram Desktop validation must use CUA Driver, not legacy macOS UI scripting.
- Do not run `hermes update`, reboot, restore files, arm timers, or restart live
  services unless the user explicitly approves that action.
- Preserve prompt caching; do not add broad core tools for feature work.

## Current Implementation Status

### 1. Telegram Busy Ack / Heartbeat E2E Contract

Status: done locally, CUA live smoke pending.

Implemented:

- Added `build_busy_ack_message()` in `gateway/run.py`.
- `_handle_active_session_busy_message()` now delegates user-facing busy text
  to that helper while preserving interrupt, queue, steer, and subagent-demotion
  behavior.
- Added sync contracts for interrupt/queue/steer/subagent wording and no raw
  provider/compression/iteration jargon.

Evidence:

```bash
venv/bin/python -m pytest \
  tests/gateway/test_gateway_noise_suppression.py \
  tests/gateway/test_human_heartbeat.py \
  tests/gateway/test_busy_session_ack.py -q
# 37 passed
```

### 2. Telegram Notification Mode For Progress Messages

Status: done locally, CUA live smoke pending.

Implemented:

- Locked the legacy `send_message` path contract:
  progress/intermediate sends stay silent with `disable_notification=True`;
  final/user-triggered sends with `metadata={"notify": True}` can notify.
- Rich-message notification contracts were already present and remain covered.

Evidence:

```bash
venv/bin/python -m pytest \
  tests/gateway/test_telegram_format.py \
  tests/gateway/test_telegram_rich_messages.py \
  tests/gateway/test_telegram_status_update.py \
  tests/gateway/test_telegram_progress_edit_transient.py -q
# 195 passed
```

### 3. Repo Cockpit `/conv` Follow-Up UX Polish

Status: done locally, CUA live smoke pending.

Implemented:

- After native repo selection, Telegram now shows selected repo, selected mode,
  optional conversation id, and the next action: send the task in this chat.
- The confirmation keeps compact buttons:
  `Changer repo`, `Changer mode`, `Annuler`.
- Added focused tests for the confirmation text and keyboard callback shape.

Evidence:

```bash
venv/bin/python -m pytest tests/gateway/test_telegram_conv_ux.py -q
# 2 passed
```

### 4. Telegram Rich Messages/Table Renderer Gated Rollout

Status: local contracts done, live rich-table CUA proof pending.

Notes:

- Rich rendering remains gated by existing adapter capability/config behavior.
- Current implementation work in this pass only hardened notification contracts;
  no broad production enablement was performed.

Evidence:

```bash
venv/bin/python -m pytest tests/gateway/test_telegram_rich_messages.py -q
# included in the 195-test Telegram run above
```

### 5. Background/Subagent Audit Feature For Repo Cockpit

Status: todo.

Next slice:

- Reuse existing `/background` or `delegate_task(background=true)` mechanisms.
- Do not add a new core model tool.
- Return task id, repo, phase, heartbeat/progress, and status/resume
  instructions.

### 6. Concurrent Approval Multi-Session Stress Proof

Status: done locally.

Implemented:

- Added a regression test proving approval resolution for session A does not
  resolve or remove pending approvals for session B.

Evidence:

```bash
venv/bin/python -m pytest \
  tests/gateway/test_approve_deny_commands.py::TestBlockingGatewayApproval -q
# included in the 27-test safety run below
```

### 7. Backup Coverage For Project/Kanban DBs

Status: done locally for Hermes kanban state; Repo Cockpit external DB audit
already covered by VPS audit script.

Implemented:

- Quick snapshots now include:
  `kanban.db`, `kanban/current`, `kanban/boards/*/board.json`, and
  `kanban/boards/*/kanban.db`.
- Board workspaces are intentionally not captured in quick snapshots.
- Repo Cockpit DBs live outside `HERMES_HOME` under repo-cockpit data paths and
  stay covered by `scripts/vps_write_roots_audit.py`, not `hermes backup`.

Evidence:

```bash
venv/bin/python -m pytest \
  tests/hermes_cli/test_backup.py::TestQuickSnapshot \
  tests/gateway/test_approve_deny_commands.py::TestBlockingGatewayApproval -q
# 27 passed
```

### 8. Skill Curator Dry-Run Safety

Status: todo.

Next slice:

- Run only against a temp `HERMES_HOME`.
- Prove pinned/user skills are not deleted.
- Do not run production curator mutation on VPS.

### 9. Weekly Updatecheck Activation Plan

Status: todo.

Next slice:

- Keep `weekly-updatecheck` no-agent and unarmed until the user gives target
  Telegram thread and cadence.

## Combined Local Verification

```bash
venv/bin/python -m pytest \
  tests/gateway/test_gateway_noise_suppression.py \
  tests/gateway/test_human_heartbeat.py \
  tests/gateway/test_busy_session_ack.py \
  tests/gateway/test_telegram_format.py \
  tests/gateway/test_telegram_rich_messages.py \
  tests/gateway/test_telegram_status_update.py \
  tests/gateway/test_telegram_progress_edit_transient.py \
  tests/gateway/test_telegram_conv_ux.py \
  tests/hermes_cli/test_backup.py::TestQuickSnapshot \
  tests/gateway/test_approve_deny_commands.py::TestBlockingGatewayApproval \
  tests/scripts/test_vps_write_roots_audit.py -q
# 264 passed
```

```bash
venv/bin/python -m py_compile \
  gateway/run.py gateway/platforms/telegram.py hermes_cli/backup.py tools/approval.py
# passed
```

## CUA Validation Status

Pending. `scripts/telegram_desktop_cua_smoke.py` was not present in this
checkout during this implementation pass, so no Telegram Desktop CUA live smoke
was run.

## Update Log

- 2026-06-27: Implemented local phases 1, 2, 3, 6, and 7. Added roadmap
  evidence after discovering the referenced roadmap file was absent on disk.
