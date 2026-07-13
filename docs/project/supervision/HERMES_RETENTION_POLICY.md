# Hermes retention and cleanup policy

This policy applies to Kanban scratch workspaces, task events, worker logs,
Supervisor reports/ledger and deployment receipts. Cleanup must preserve the
evidence required to reconstruct a run.

## Default retention

| Data | Minimum | Deletion eligibility |
|---|---:|---|
| Active/blocked task events | Indefinite | Never through automatic GC |
| Done/archived task events | 30 days | Only after dry-run review |
| Worker logs | 30 days | Only files older than the threshold |
| Archived scratch workspaces | Until archive review | Only inside the board scratch root |
| Supervisor ledger | Indefinite | Append-only; never automatic GC |
| Supervisor JSON/Markdown reports | 90 days | Only after ledger and handoff references are durable |
| Deploy manifests/receipts | Current + N-1 minimum | Never delete the active rollback pair |
| Offsite backup snapshots | Provider policy | At least one quarterly restore point |

## Required procedure

Always inventory before mutation:

```bash
hermes kanban gc \
  --event-retention-days 30 \
  --log-retention-days 30 \
  --dry-run
```

The dry-run counts eligible rows, files and workspaces but must not delete or
rewrite them. Review the project handoff, active task list, Supervisor ledger
and N/N-1 deploy receipts. Only then run the same command without `--dry-run`
during an approved maintenance window.

A cleanup exit code of zero means only that the command ran. Feed measured
before/after bytes and removed paths into `scripts/selfops_effectiveness_guard.py`.
The objective is achieved only when the configured minimum delta is met. Two
consecutive zero-delta cleanups mark the action `ineffective`, suspend it for 24
hours and require one escalation containing the real top disk consumers.

## Safety invariants

- Never follow a workspace path outside the board's resolved scratch root.
- Never automatically delete active, ready or blocked task evidence.
- Never count `0 bytes / 0 paths` as successful remediation.
- Never delete the Supervisor ledger or the only rollback receipt.
- Offsite means a failure domain outside the VPS; a second directory on the VPS
  is not a backup.
- Test a restore into a fresh temporary target at least quarterly before
  claiming backup readiness.
