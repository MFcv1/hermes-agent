# Hermes Features And Learnings - 2026-06-27

Purpose: durable handoff for the next Hermes implementation agent. This file is
the compact "what changed / what we learned / what remains" companion to the
concrete roadmap.

## Implemented Features

### Telegram Busy Ack / Heartbeat

- `gateway/run.py` now has `build_busy_ack_message()`.
- Busy-session replies use user-facing wording for interrupt, queue, steer, and
  subagent demotion.
- Learning: keep provider/compression/iteration internals out of Telegram user
  messages. The useful signal is model, elapsed time, queue/interrupt state, and
  the next action.

### Telegram Notification Contracts

- Progress/intermediate Telegram sends remain silent.
- Final/user-triggered sends can notify when `metadata={"notify": True}`.
- Learning: both legacy `send_message` and rich-message paths need notification
  coverage, because regressions can hide in one path while the other is green.

### Repo Cockpit `/conv` UX

- Native repo selection now confirms selected repo, selected mode, optional
  conversation id, and tells the user to send the task in the chat.
- The follow-up keyboard keeps compact actions: change repo, change mode,
  cancel.
- Learning: after a native Telegram button flow, the next step must be explicit;
  otherwise users think the bot is still waiting for another button.

### Repo Cockpit `/audit` / `/auditer`

- Telegram now exposes `/audit` and `/auditer` for the active Repo Cockpit
  thread.
- The command creates a bounded read-only audit task through existing Repo
  Cockpit APIs and launches the worker in background with `execute: false`.
- No new core model tool was added.
- Learning: this belongs at the edge, in the Telegram/Repo Cockpit integration,
  not in the core tool schema.

### Approval Multi-Session Safety

- Added a regression proof that resolving approval for one session does not
  resolve or remove approvals for another session.
- Learning: gateway approval state must always stay session-scoped; global
  resolution is unsafe under concurrent chats.

### Backup Coverage

- Quick snapshots now include Hermes kanban state:
  `kanban.db`, `kanban/current`, `kanban/boards/*/board.json`, and
  `kanban/boards/*/kanban.db`.
- Board workspaces remain intentionally excluded.
- Learning: Hermes kanban state belongs in `hermes backup`; Repo Cockpit
  external DBs are covered by VPS audit scripts instead.

### Skill Curator Dry-Run Safety

- Dry-run prune now has an explicit proof: old unpinned skills are previewed,
  pinned/recent skills are excluded, and `archive_skill` is not called.
- Learning: dry-run tests should prove both "what would happen" and "what did
  not happen".

### Hermes VPS / Repo Cockpit Ops Skill

- Added `skills/software-development/hermes-vps-repo-cockpit-ops/SKILL.md`.
- The skill captures durable runbooks for Telegram reliability, `/conv`,
  `/audit`, CUA smoke, weekly updatecheck, backup/update preflight, and
  procedure-to-skill conversion.
- Learning: `/learn` is the eventual product feature, but a committed in-repo
  skill is the safest immediate way to preserve procedures for future agents.

### Weekly Updatecheck No-Agent Blueprint

- `weekly-updatecheck` remains no-agent and script-backed by
  `cron/scripts/weekly_updatecheck.py`.
- Script-backed blueprints now install bundled scripts into
  `HERMES_HOME/scripts/` before job creation.
- Covered creation paths: `/blueprint`, dashboard blueprint instantiate, and
  accepted cron suggestions.
- Learning: a blueprint job can be syntactically valid but operationally dead if
  the script is not copied into the scheduler's allowed scripts directory.

### Telegram Desktop CUA Smoke Harness

- Added `scripts/telegram_desktop_cua_smoke.py`.
- Default mode is safe: target Telegram Desktop via CUA and capture evidence,
  but do not type or send.
- `--send` explicitly types the chosen command/message and presses Return.
- Learning: live Telegram validation needs a CUA harness in the repo, not only
  command strings in VPS scripts.

## Important Non-Actions

- No live Telegram message was sent.

### Beginner Developer Cockpit

- Telegram now exposes `/dev` as the simple developer entry point. It groups
  project creation/resume, GitHub branch/PR flow, audit/runs/approvals,
  ops/deploy checks, and learning prompts.
- `/dev` has inline sections instead of asking a beginner to remember the full
  command catalog.
- Legacy `/serveurstatut` now routes to the shorter `/vps` path, reducing daily
  status noise while keeping compatibility.
- Learning: the bot needed one obvious "what do I do now?" command more than it
  needed more low-level commands.
- No production cron job was created, resumed, or armed.
- No VPS service was restarted.
- No `hermes update`, reboot, rollback, or restore command was run.
- No new core model tool was added.

## Prioritized Feature Map

This section maps the broader feature ideas to what is already in this
implementation pass, what is only partially covered, and what should remain a
careful follow-up.

Source note: the broader VPS update/scale audit file referenced during the
handoff (`docs/HERMES_VPS_UPDATE_SCALE_AUDIT_2026-06-27.md`) was not present in
this checkout. The inventory below preserves the feature list captured in the
implementation conversation so it is not lost.

### Priority 1

- Gateway reliability / Telegram fixes: covered locally. Busy ack, heartbeat
  wording, queue/interrupt/steer/subagent paths, Telegram notification
  contracts, and approval multi-session safety all have tests.
- Backup/update hardening: partially covered locally. Backup coverage for
  Hermes kanban state is implemented; VPS maintenance/update remains gated by
  read-only audit/plan scripts and must not run without approval.
- File safety / multi write roots: not changed in this pass, but kept in the
  validation surface through `scripts/vps_write_roots_audit.py`. Treat this as
  required preflight before VPS maintenance, especially for Hermes, Repo
  Cockpit, and workspace separation.
- CUA Driver: covered by harness, not by live execution. The
  `telegram_desktop_cua_smoke.py` script now exists and is tested, but operator
  Telegram Desktop smoke is still pending. Latest dry-run found Telegram running
  with a `Herme_core` window, but CUA marked it `is_on_screen=false`; bring
  Telegram Desktop onto the current visible space before live smoke.
- Skills Hub / curator / write approval: partially covered. Curator dry-run
  safety was hardened, but Skills Hub browsing, `/learn` production safety, and
  explicit write approval flows for memory/skills remain follow-up work.
- Security: not implemented in this pass. Dashboard auth, browser typed-text
  redaction, email spoofing fixes, and invisible-unicode scanning are important
  before exposing gateway/dashboard more broadly.

### Priority 2

- `/learn`: not implemented in this pass. The handoff document is the manual
  learning artifact for now, and `hermes-vps-repo-cockpit-ops` is the committed
  in-repo skill version of the stable runbooks. Follow-up should still evaluate
  product `/learn` for user-authored skills.
- Watchers + Automation Blueprints: covered for the first concrete release and
  VPS monitoring use cases. `weekly-updatecheck` remains no-agent and
  script-backed, `github-release-watch` polls GitHub releases with local
  watermark state, and `vps-healthcheck` reports only warning/error VPS states.
  Telegram now has `/watch releases owner/repo`, `/watch vps`, `/watch list`,
  and `/watch remove job_id` shortcuts so users do not need long blueprint
  commands.
- Background subagents: covered at the edge for Repo Cockpit audit. `/audit`
  creates a background dry-run task without adding a core tool. Longer general
  background review/subagent workflows are still follow-up work.
- Telegram rich messages + reliability: partially covered. Notification and
  formatting contracts are tested locally; rich messages should stay gated
  until Telegram Desktop CUA live smokes pass.
- Watchers skill: partially covered through the GitHub release watcher and VPS
  healthcheck blueprint/scripts. Useful next step: generalize the same pattern
  for RSS and HTTP JSON feeds without depending on X/Twitter.
- Dashboard admin + profile builder: not implemented in this pass. Useful for
  prod/staging VPS profiles, channels, MCP, credentials, and memory config, but
  only after access/security boundaries are explicit.

### Priority 3

- Projects/worktrees: not implemented in this pass. Promising for Repo Cockpit,
  but integrate carefully because Repo Cockpit already owns repo/workspace
  logic.
- New channels: not implemented in this pass. iMessage via Photon, Raft, and
  WhatsApp Cloud are interesting but not critical for the current Telegram bot.
- Image editing / video generation / model features: not implemented in this
  pass. Image-to-image, video providers, xAI/Grok Composer, and MoA presets are
  useful later, but not priority for stabilizing Hermes/Repo Cockpit on VPS.

## Full Feature Inventory From VPS Update Discussion

- `/learn` / self-learning: transform docs, URLs, folders, notes, or procedures
  into reusable skills. High value for Repo Cockpit/VPS runbooks.
- Background / async subagents: `delegate_task(background=true)` style long
  tasks while the chat remains usable. High value for Telegram audits/reviews.
- Automation Blueprints + Cron Recipes: create automations without writing raw
  cron. High value for updatecheck, release monitoring, and VPS healthchecks.
- Watchers skill: monitor RSS, HTTP JSON, and GitHub through cron without a
  permanent agent. Likely better than depending on X/Twitter for releases.
- Telegram rich messages + Telegram reliability: tables, rich rendering,
  persistent heartbeat, Bot API queue preservation, and topic routing. Enable
  rich rendering only after CUA tests.
- Gateway reliability: chat noise reduction, better errors, approval
  multi-session fixes, and dedupe user turns. Very high priority.
- Dashboard admin + profile builder: browser setup for channels, MCP,
  credentials, memory, and profiles. Useful for clean prod/staging operation.
- Projects / worktrees: project store, workspace tools, project tree, review
  pane. Interesting for Repo Cockpit but must not duplicate the cockpit.
- CUA Driver cross-platform: important because Telegram Desktop validation must
  use CUA Driver under workspace rules.
- Skills Hub / curator / write approval: safer skill browsing, curator safety,
  opt-in consolidation, and write approvals for memory/skills. Important before
  using `/learn` in production.
- Backup/update hardening: snapshots, project/kanban DB backup coverage,
  recovery install, and Git cleanup. Priority before VPS update.
- Security: browser typed-text redaction, dashboard auth, email spoofing fix,
  invisible unicode scanner. Important before exposing gateway/dashboard.
- New channels: iMessage via Photon, Raft, WhatsApp Cloud. Interesting but not
  critical for the current Telegram bot.
- Image editing / video generation / model features: image-to-image, video
  providers, xAI/Grok Composer, MoA presets. Later, not stabilisation priority.

## Remaining Work

### Live Telegram CUA Validation

Run from the operator Mac with Telegram Desktop open on the intended chat:

```bash
python3 scripts/telegram_desktop_cua_smoke.py \
  --message 'smoke normal chat: reponds juste OK smoke' \
  --send
```

Recommended live smoke set:

- normal chat response
- `/version`
- `/updatecheck`
- `/conv` repo selection flow
- `/audit` on an active Repo Cockpit thread
- one rich-table/rendering case

### Weekly Updatecheck Activation

Still needs explicit user input before activation:

- Telegram delivery target/thread
- cadence/day/time
- whether to create paused first or immediately active

## Verification Snapshot

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
