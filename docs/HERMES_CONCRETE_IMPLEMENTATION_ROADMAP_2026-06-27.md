# Hermes Concrete Implementation Roadmap - 2026-06-27

Audience: next Codex/Hermes implementation chat.

Goal: implement concrete roadmap features step by step. For each step: code,
test, validate, update this file, then move to the next step.

Companion learnings/feature handoff:
`docs/HERMES_FEATURES_AND_LEARNINGS_2026-06-27.md`.

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
# 5 passed
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

Status: done locally.

Implemented:

- Added Telegram `/audit` and `/auditer` Repo Cockpit commands.
- The command reuses the existing Repo Cockpit task/worker API and does not add
  a new core model tool.
- It creates a bounded read-only audit task from the active `/conv` thread,
  immediately returns job id, task id, repo, mode, phase, and `/status` /
  `/runs` follow-up commands.
- The background worker is invoked with `execute: false`; the message contract
  explicitly says dry-run and forbids repo mutation, deployment, service
  restart, or destructive actions.

Evidence:

```bash
venv/bin/python -m pytest tests/gateway/test_telegram_conv_ux.py -q
# 5 passed
```

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

Status: done locally.

Implemented:

- Hardened the existing prune dry-run contract: old unpinned skills appear in
  the preview, pinned skills and recent skills are excluded, and `archive_skill`
  is never called.
- No production curator mutation was run on VPS.

Evidence:

```bash
venv/bin/python -m pytest tests/hermes_cli/test_curator_archive_prune.py -q
# 13 passed
```

### 9. Weekly Updatecheck Activation Plan

Status: done locally; real activation still pending user-provided Telegram
target/cadence.

Implemented:

- `weekly-updatecheck` remains a no-agent blueprint backed by
  `cron/scripts/weekly_updatecheck.py`.
- Script-backed blueprints now install their bundled script into
  `HERMES_HOME/scripts/` at job creation time, so the future weekly job will be
  runnable instead of pointing at a missing script.
- The install path is used by `/blueprint`, dashboard blueprint instantiation,
  and accepted cron suggestions.
- No production cron job was created, resumed, or armed.

Evidence:

```bash
venv/bin/python -m pytest tests/cron/test_blueprint_catalog.py tests/cron/test_cron_no_agent.py -q
# 47 passed
```

### 10. GitHub Release Watcher Blueprint

Status: implemented locally; production activation still pending explicit
Telegram target/cadence/repo choice.

Implemented:

- Added `github-release-watch`, a no-agent Automation Blueprint for monitoring
  GitHub releases without relying on X/Twitter or a permanent agent.
- Added `cron/scripts/github_release_watch.py`, which polls GitHub releases,
  stores a local watermark in `HERMES_HOME/watcher-state`, stays silent on the
  first baseline and unchanged runs, and emits a concise Markdown alert only
  when new releases appear.
- Added generic `script_args` support in cron jobs, scheduler execution,
  blueprint fills, and the `cronjob` tool so script-backed blueprints can be
  parameterized safely without shell interpolation.
- Did not create or arm a live cron job.

Evidence:

```bash
venv/bin/python -m pytest -q \
  tests/cron/test_cron_script.py \
  tests/cron/test_cron_no_agent.py \
  tests/cron/test_blueprint_catalog.py \
  tests/cron/test_github_release_watch_script.py \
  tests/tools/test_cronjob_tools.py
# 156 passed
```

```bash
venv/bin/python -m pytest \
  tests/hermes_cli/test_web_server.py::TestNewEndpoints::test_cron_blueprints_list \
  tests/hermes_cli/test_web_server.py::TestNewEndpoints::test_blueprint_instantiate_creates_job \
  tests/hermes_cli/test_web_server.py::TestNewEndpoints::test_blueprint_instantiate_unknown_404 \
  tests/hermes_cli/test_web_server.py::TestNewEndpoints::test_blueprint_instantiate_bad_value_422 -q
# 4 passed
```

### 11. Simple Telegram Ops Commands

Status: implemented locally; production watchers still require the user to run
the command from the intended Telegram chat/topic.

Implemented:

- `/runs` was already present and remains the simple Repo Cockpit task/gates
  view for the active task or an explicit `op_xxx`.
- Added `/watch` Telegram shortcuts:
  - `/watch releases owner/repo` creates a `github-release-watch` no-agent job.
  - `/watch vps` creates a `vps-healthcheck` no-agent job.
  - `/watch list` shows active release/VPS watcher jobs.
  - `/watch remove job_id` removes one watcher.
- Added `vps-healthcheck`, a no-agent Automation Blueprint backed by
  `cron/scripts/vps_healthcheck.py`; it stays silent when the VPS is green and
  reports only warning/error states.
- Added `/vps`, a concise Telegram overview for disk, Hermes home disk, cron
  heartbeat, enabled cron jobs, user services, and load.
- Added a shorter Telegram `/updatecheck` path using
  `format_updatecheck_short()` for update readiness: git status, update
  availability, worktree dirtiness, disk, latest release, and blockers.
- Did not create or arm a live production watcher in this implementation turn.

Evidence:

```bash
venv/bin/python -m pytest -q \
  tests/gateway/test_telegram_conv_ux.py \
  tests/cron/test_blueprint_catalog.py \
  tests/cron/test_github_release_watch_script.py \
  tests/cron/test_vps_healthcheck_script.py \
  tests/hermes_cli/test_vps_status.py \
  tests/cron/test_cron_no_agent.py \
  tests/cron/test_cron_script.py \
  tests/tools/test_cronjob_tools.py \
  tests/cron/test_cronjob_schema.py
# 175 passed
```

```bash
venv/bin/python -m py_compile \
  gateway/platforms/telegram.py hermes_cli/commands.py hermes_cli/updatecheck.py \
  hermes_cli/vps_status.py cron/blueprint_catalog.py \
  cron/scripts/github_release_watch.py cron/scripts/vps_healthcheck.py \
  tools/cronjob_tools.py
# passed
```

### 12. Beginner Developer Cockpit / Noise Reduction

Status: implemented locally.

Implemented:

- Added `/dev` as the simple Telegram entry point for a learning developer.
  It groups the useful workflows instead of exposing every expert command:
  project start/resume, GitHub branch/PR flow, audit/runs/approvals,
  deploy/ops checks, and learning-oriented prompts.
- Added `/dev` inline buttons:
  - `Nouveau projet` -> existing `/new` repo/project flow.
  - `Conversations` -> existing `/conv` thread list.
  - `GitHub flow`, `Ops / deploy`, `Apprendre`, `Accueil` -> focused help
    sections without leaving the chat.
- Prioritized `/dev`, `/vps`, `/watch`, and `/updatecheck` in the Telegram
  command menu so the beginner-friendly surface survives Telegram's visible
  menu cap.
- Routed legacy `/serveurstatut` to the simpler `/vps` path to reduce noisy
  rich status output while preserving compatibility for old muscle memory.
- Added `docs/HERMES_BOT_ACCESSIBILITY_AUDIT_2026-06-29.md`, a short audit of
  the current beginner workflow, friction points, daily usage path, and
  follow-up gaps.

Evidence:

```bash
venv/bin/python -m pytest -q \
  tests/gateway/test_telegram_conv_ux.py \
  tests/hermes_cli/test_commands.py::TestTelegramMenuCommands::test_operational_builtins_survive_thirty_command_cap \
  tests/cron/test_blueprint_catalog.py \
  tests/cron/test_github_release_watch_script.py \
  tests/cron/test_vps_healthcheck_script.py \
  tests/hermes_cli/test_vps_status.py \
  tests/cron/test_cron_no_agent.py \
  tests/cron/test_cron_script.py \
  tests/tools/test_cronjob_tools.py \
  tests/cron/test_cronjob_schema.py
# 179 passed
```

```bash
venv/bin/python -m py_compile \
  gateway/platforms/telegram.py hermes_cli/commands.py hermes_cli/updatecheck.py \
  hermes_cli/vps_status.py cron/blueprint_catalog.py \
  cron/scripts/github_release_watch.py cron/scripts/vps_healthcheck.py \
  tools/cronjob_tools.py
# passed
```

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
  tests/hermes_cli/test_curator_archive_prune.py \
  tests/cron/test_blueprint_catalog.py \
  tests/cron/test_cron_no_agent.py \
  tests/hermes_cli/test_web_server.py::TestNewEndpoints::test_cron_blueprints_list \
  tests/hermes_cli/test_web_server.py::TestNewEndpoints::test_blueprint_instantiate_creates_job \
  tests/hermes_cli/test_web_server.py::TestNewEndpoints::test_blueprint_instantiate_unknown_404 \
  tests/hermes_cli/test_web_server.py::TestNewEndpoints::test_blueprint_instantiate_bad_value_422 \
  tests/scripts/test_telegram_desktop_cua_smoke.py \
  tests/scripts/test_vps_smoke_matrix.py \
  tests/scripts/test_vps_maintenance_plan.py \
  tests/scripts/test_vps_write_roots_audit.py -q
# 342 passed before ops skill addition; see Feature / Learning Handoff for current skill-inclusive run
```

```bash
venv/bin/python -m py_compile \
  gateway/run.py gateway/platforms/telegram.py hermes_cli/backup.py hermes_cli/curator.py \
  cron/blueprint_catalog.py hermes_cli/blueprint_cmd.py hermes_cli/web_server.py \
  cron/suggestions.py scripts/telegram_desktop_cua_smoke.py scripts/vps_smoke_matrix.py \
  tools/approval.py
# passed
```

## CUA Validation Status

Harness ready; live operator smoke still pending.

Implemented:

- Added `scripts/telegram_desktop_cua_smoke.py`, the script already referenced
  by `scripts/vps_smoke_matrix.py` and `scripts/vps_maintenance_plan.py`.
- Default mode is safe dry-run: target Telegram Desktop via CUA, capture
  evidence under `~/.hermes/telegram-gui-smoke/`, and do not type/send.
- `--send` explicitly types the requested `--message` or `--command` and presses
  Return; this assumes the operator already opened the intended Telegram chat.

Evidence:

```bash
venv/bin/python -m pytest \
  tests/scripts/test_telegram_desktop_cua_smoke.py \
  tests/scripts/test_vps_smoke_matrix.py \
  tests/scripts/test_vps_maintenance_plan.py -q
# 11 passed
```

```bash
venv/bin/python scripts/telegram_desktop_cua_smoke.py --help
# passed
```

Not run live: no operator-approved Telegram Desktop send/capture smoke was
executed in this turn.

Latest dry-run attempt:

```bash
venv/bin/python scripts/telegram_desktop_cua_smoke.py \
  --message 'smoke roadmap dry-run: reponds juste OK smoke' --json
# status: telegram_window_not_on_current_space
```

CUA sees Telegram running and a `Herme_core` window, but marks the Telegram
window as `is_on_screen=false`. The operator must bring Telegram Desktop onto
the current visible space/chat before the live CUA smoke can proceed.

## Feature / Learning Handoff

Status: done locally.

Created `docs/HERMES_FEATURES_AND_LEARNINGS_2026-06-27.md` and
`skills/software-development/hermes-vps-repo-cockpit-ops/SKILL.md` with:

- implemented feature inventory;
- implementation learnings and design decisions;
- explicit non-actions;
- remaining live CUA and weekly-updatecheck activation steps;
- verification command snapshot.
- a durable in-repo skill for Hermes VPS / Repo Cockpit runbooks.

Evidence:

```bash
venv/bin/python -m pytest \
  tests/scripts/test_build_skills_index_health.py \
  tests/website/test_extract_skills.py \
  tests/website/test_generate_skill_docs.py -q
# 22 passed
```

Current skill-inclusive roadmap run:

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
  tests/hermes_cli/test_curator_archive_prune.py \
  tests/cron/test_blueprint_catalog.py \
  tests/cron/test_cron_no_agent.py \
  tests/hermes_cli/test_web_server.py::TestNewEndpoints::test_cron_blueprints_list \
  tests/hermes_cli/test_web_server.py::TestNewEndpoints::test_blueprint_instantiate_creates_job \
  tests/hermes_cli/test_web_server.py::TestNewEndpoints::test_blueprint_instantiate_unknown_404 \
  tests/hermes_cli/test_web_server.py::TestNewEndpoints::test_blueprint_instantiate_bad_value_422 \
  tests/scripts/test_telegram_desktop_cua_smoke.py \
  tests/scripts/test_vps_smoke_matrix.py \
  tests/scripts/test_vps_maintenance_plan.py \
  tests/scripts/test_vps_write_roots_audit.py \
  tests/scripts/test_build_skills_index_health.py \
  tests/website/test_extract_skills.py \
  tests/website/test_generate_skill_docs.py -q
# 364 passed
```

## Update Log

- 2026-06-27: Implemented local phases 1, 2, 3, 5, 6, 7, 8, and 9. Added roadmap
  evidence after discovering the referenced roadmap file was absent on disk.
- 2026-06-27: Added explicit feature/learning handoff document.
