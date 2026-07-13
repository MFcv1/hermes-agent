# Hermes workflow reliability — Lot 5 report

Date: 2026-07-13
Scope: HMR-002, HMR-010, HMR-011, HMR-012, HMR-016, HMR-017 and HMR-018
Branch: `codex/fix/hermes-supervisor-reliability`

## Scope boundary

The live Repo Cockpit and Self-Ops sources described by the audit are not part
of this repository. This lot therefore implements the enforceable Hermes and
Supervisor contracts locally and produces reversible VPS plans, but does not
claim to have patched or restarted the remote Cockpit service. No Telegram
message, VPS mutation, provider operation, GitHub ruleset, Tailscale change,
production deploy, DNS change or paid resize was performed.

## Reproduced defects

- Supervisor reports had no immutable global `run_id` and no append-only lineage
  linking task/session, model budget, GitHub evidence, artifact and deployment.
- A successful Cockpit task status could pass Supervisor without a standard
  `PROJECT_STATUS.md` handoff.
- Kanban GC deleted eligible evidence immediately; there was no inventory-only
  mode for a retention review.
- Cleanup success was represented only by a command exit status. There was no
  shared policy converting measured byte/path delta into objective success,
  cooldown and escalation.
- The existing VPS maintenance helper proposed only a write-root override. It
  did not produce hardening, a durable dashboard unit, immutable SHA release,
  offsite backup or restore-drill plans.

## Implemented contracts

### Run lineage ledger (HMR-002)

Supervisor Mode creates a `sup_<timestamp>_<random>` run ID before collecting
evidence. It appends a `supervisor_run_started` event immediately and a final
`supervisor_run_evaluated` event after the gates. JSONL records have monotonic
sequence numbers, previous-record hashes and their own canonical SHA-256 hash;
verification detects editing or chain breaks.

The final lineage records task and Gateway session IDs, provider/model/effort,
call budget and observed calls/tokens, repository, branch and full GitHub or
handoff SHA, typed gates, artifact digest, provider deployment ID/URL and final
Supervisor status. Values are bounded and redacted before append.

### Completion handoff (HMR-018)

`PROJECT_STATUS_TEMPLATE.md` standardizes state, full source SHA, branch,
machine gates, URLs/provider ID, resources and limits, budgets, rollback and
next action. For local projects, Supervisor validates the real file and compares
its SHA with GitHub branch evidence. For remote tasks, Cockpit must expose an
equivalent `project_status` object. `completed`, `done` and `deployed_preview`
without that evidence now produce `incomplete_evidence`, never a green review.

This blocks Supervisor acceptance; changing the remote Cockpit task transition
itself requires a corresponding change in the separate Cockpit repository.

### Self-Ops effectiveness and retention (HMR-010)

`selfops_effectiveness_guard.py` persists separate `command_executed` and
`objective_achieved` facts. Effect is measured by freed bytes or removed paths.
Two successful commands below the configured minimum become `ineffective`,
open an escalation carrying the real top consumers and suspend the action for
24 hours. State writes are atomic and every evaluation is appended to an event
file. Dry-run evaluation does not mutate either file.

`hermes kanban gc --dry-run` now walks the real board database, scratch root and
worker-log directory but only counts eligible objects. Active/blocked evidence
remains ineligible. The committed retention policy requires reviewing this
inventory before an approved destructive pass and preserves the Supervisor
ledger plus active and N/N-1 deployment evidence.

### Reversible VPS plans (HMR-011, HMR-012, HMR-016, HMR-017)

The read-only VPS maintenance planner now generates and statically validates:

- systemd hardening directives including write-path isolation, no-new-
  privileges, strict system protection, memory/task caps and control-group kill;
- a persistent loopback-only dashboard unit with restart policy;
- a detached Git worktree release prepared from an exact SHA and atomically
  switched release symlink;
- Restic offsite backup/check steps and a fresh-target restore drill;
- an explicit list of operations that still require approval.

The plan does not resize the current VPS. Lot 3 already disabled implicit LSP
in batch and serialized heavy Node work; reaching the audit's 2/4 GiB target is
a paid provider mutation and remains gated. Generated systemd content passes the
local static contract, but `systemd-analyze security` and service smoke must run
on Linux immediately before any approved install. Likewise, backup readiness is
not claimed until a real off-VPS repository and fresh-host restore drill pass.

GitHub App/ruleset creation and Tailscale ACL/device revocation remain explicit
external changes. The planner records their approval boundary; it does not
simulate provider evidence.

## Acceptance evidence

- two ledger events from one real local Supervisor invocation were appended and
  hash-linked under the same immutable run ID;
- ledger verification passes intact data and rejects a modified prior record;
- a completed task without project status becomes `incomplete_evidence`;
- a complete local template passes and a GitHub SHA mismatch fails;
- two zero-delta cleanup evaluations enter 24-hour cooldown/escalation;
- a real delta clears the no-op counter; a failed command cannot be effective;
- Kanban GC dry-run counts old events/logs/workspace while proving all still
  exist afterward;
- generated hardening/dashboard plans pass the local directive validator and a
  removed security directive fails it.

Validation used `scripts/run_tests.sh`: the final focused pass completed
537/537 tests across Supervisor lineage/handoff, Self-Ops effectiveness, VPS
planning, real Kanban DB/GC behavior and the existing skill loader/tool suite.
Python byte-compilation, a real local Supervisor ledger smoke, generated-plan
validation and `git diff --check` also passed.
