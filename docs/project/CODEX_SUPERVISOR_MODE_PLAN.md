# Codex Supervisor Mode — implementation plan

Date: 2026-07-09

## Goal

Create a supervised bridge where Codex can drive Hermes through Telegram/CUA
while verifying the real machine state through Cockpit, GitHub, and hosting
providers.

The user-facing idea is:

```text
Codex
  -> pilots Telegram via CUA
  -> sends instructions to Hermes
  -> watches Telegram + Cockpit API + GitHub + provider deploy
  -> nudges/restarts if Hermes blocks
  -> verifies GitHub is current
  -> verifies the deploy URL responds
  -> writes a final report
```

Telegram is the human-visible proof surface. It is not the source of truth for
state. The supervisor must also query machine-readable surfaces.

## Existing building blocks

- `scripts/telegram_desktop_cua_smoke.py`
  - Captures Telegram Desktop through CUA.
  - Can type/send a command or message when `--send` is passed.
  - Writes screenshot + JSON evidence under `~/.hermes/telegram-gui-smoke/`.
- Repo Cockpit live API on the VPS.
  - Health, hosting capabilities, self-ops, weekly report, task status and
    telemetry endpoints.
- GitHub CLI/API on Mac and VPS.
  - Used to verify repo, branch, commit, and PR state.
- Provider APIs.
  - Vercel, Supabase, Cloudflare are ready on the VPS.
  - Cloudflare R2/D1/KV/Workers/Pages API checks pass.

## Phase 1 — read-only supervisor shell

Status: implemented as `scripts/codex_supervisor_mode.py`.

Deliver a local script that:

- accepts a natural-language instruction or slash command;
- optionally sends it to Telegram via the existing CUA helper;
- captures Telegram evidence before/after;
- queries Cockpit health/provider readiness;
- verifies an optional GitHub repo/branch;
- verifies an optional deploy URL with HTTP;
- writes a JSON + Markdown report.

This phase does not create repos, deploy, merge, or approve anything by itself.

## Phase 2 — task-aware supervision loop

Status: basic implementation done in `scripts/codex_supervisor_mode.py`.

Add a loop that can watch a `task_id` until it reaches one of:

- `completed`;
- `pilot_questions_required`;
- `needs_approval`;
- `blocked_*`;
- timeout.

For each poll, record:

- task status/phase;
- latest worker run;
- costs/telemetry if exposed;
- approvals pending;
- related GitHub branch/commit if available.

Current implementation:

- `--watch-task --task-id op_xxx` polls Cockpit through
  `/api/internal/tasks/{task_id}/autonomy`.
- It stops on terminal statuses such as `completed`,
  `pilot_questions_required`, `needs_approval`, `needs_merge_approval`,
  `needs_review`, `deployed_preview`, `failed`, `cancelled`, and `blocked_*`.
- It records every sampled status in the final JSON/Markdown report.
- It does not yet send automatic repair/nudge messages; that belongs to
  Phase 3.

## Phase 3 — repair/nudge policy

Allow Codex to send a follow-up Telegram message only when a safe rule matches:

- Hermes asks a clarification question;
- Hermes is blocked on missing approval;
- Hermes used the wrong repo;
- Hermes produced docs but did not push artifacts;
- deploy URL failed smoke;
- task timed out without status change.

All nudges must be appended to the report.

## Phase 4 — end-to-end deploy smoke

Run a controlled test:

- create a private smoke repo;
- ask Hermes to scaffold a tiny app;
- deploy to Cloudflare preview or Vercel preview;
- smoke the URL;
- verify GitHub contains all useful artifacts;
- write a final report with screenshots, task IDs, commits, and deploy URL.

## Guardrails

- No production deploy when preview is enough.
- No DNS/domain change without explicit approval.
- No paid plan change or budget increase without explicit approval.
- No destructive DB/storage cleanup without explicit approval.
- No merge to protected/default branch unless the user explicitly asks for it.
- A task is not considered done unless artifacts are:
  - pushed to GitHub;
  - explicitly marked `no changes`;
  - or blocked with a clear reason and next action.

## Acceptance criteria

- Codex can run one command that produces a supervisor report.
- Telegram evidence is stored for every CUA interaction.
- Cockpit status is read through API/SSH, not inferred from chat text.
- GitHub branch/commit state is verified with `gh` or Git.
- Deploy URLs are smoked with HTTP.
- The final report is durable in `docs/project/supervisor-runs/`.
