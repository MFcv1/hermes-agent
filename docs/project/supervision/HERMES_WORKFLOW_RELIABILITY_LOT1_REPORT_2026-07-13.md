---
status: implemented
verified_at: 2026-07-13
scope: HMR-001, Codex Supervisor Mode evidence verdicts
branch: codex/fix/hermes-supervisor-reliability
base_commit: 6f2a2669235fad4bffdd2737339408afede9df8f
safe_to_execute: local_tests_only
governed_by: ../../../AGENTS.md
---

# Hermes workflow reliability — Lot 1 HMR-001

## Defect reproduced

The previous Supervisor implementation returned `True` from every check helper
when its evidence contained `{"skipped": true}`. `summarize_status()` then used
`all(checks.values())`, so omitted Telegram, GitHub, deploy and task evidence
could still produce `ready_for_human_review`. The original seven-test suite
passed while explicitly asserting that false-green contract.

## Runtime contract implemented

- Report schema 2 exposes `pass`, `fail`, `skipped` and `unknown` for Telegram,
  Cockpit, GitHub, deploy and task watch.
- Each check records whether it was required and why it received its outcome.
- Requirements derive from run arguments: Telegram/Cockpit skip flags,
  `task_id`/`watch_task`, GitHub repo/branch and deploy URL.
- A required `skipped` or `unknown` check produces `incomplete_evidence`.
- A known failure produces `attention_required`.
- Task collection success is not task success: only `completed`, `done` and
  `deployed_preview` pass; blocked, failed, cancelled and human-action states
  require attention.
- Both non-ready statuses return exit code 2; only
  `ready_for_human_review` returns 0.
- `legacy_checks` is a temporary fail-closed boolean projection where only an
  explicit `pass` is true. Consumers must migrate to the typed `checks` field.
- Returned data, JSON and Markdown reports are centrally redacted and bounded
  to 4,000 characters per string, 50 collection entries and 10 nesting levels.

## Verification

- Canonical targeted suite: 41 tests passed through `scripts/run_tests.sh`.
- Matrix coverage: all five checks across pass, fail, required skip, optional
  skip and unknown outcomes.
- Direct CLI proofs: requested GitHub branch and task evidence omitted in
  separate runs both returned `incomplete_evidence` and exit code 2.
- Direct module compilation and `git diff --check` passed.

No Telegram message, Cockpit mutation, GitHub write, deployment, DNS change,
paid resource or production action was performed for this lot.
