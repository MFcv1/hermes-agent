# VPS Disk Audit — 2026-07-15

## Scope and method

- Audit timestamp: `2026-07-14T22:55:54Z` / `2026-07-15T00:55:54+0200 CEST`.
- Read-only commands: `df -h /`, `df -B1 /`, and targeted `du -sh` on known consumers.
- No deletion, cache clean, prune, package installation, restart, reload, or deployment was performed.
- `/tmp` and system directories produced permission-denied entries for private service paths; totals are indicative, not deletion authorization.

## Capacity

| Filesystem | Size | Used | Available | Reported use |
|---|---:|---:|---:|---:|
| `/dev/vda1` mounted on `/` | 24G | 21G | 2.6G (2,703,499,264 bytes) | 90% at audit time |

The earlier established measure was 2.7G free / 89%; the live measure above is authoritative for this timestamp and indicates further pressure.

## Top consumers and retention decision

| Consumer | Measure | Classification | Conservation / risk |
|---|---:|---|---|
| `~/.npm/_cacache` | 2.4G | **Probably removable; validation required** | Rebuildable, but validate no active/offline build depends on it. |
| `~/repo-cockpit/workspaces` | 2.6G | **Probably removable in selected batches; validation required** | May contain active task worktrees/artifacts. Preserve Cockpit data and confirm task ownership/status. |
| `~/.hermes/hermes-agent` | 2.7G | **Keep until checkout ownership is audited** | Dirty checkout with modified/untracked work; no reset, clean, stash, or deletion authorized. |
| `~/MFcv1/portfolio-v2-hermes-test` | 1.3G | **Keep** | Portfolio source; explicitly out of scope. |
| `~/releases` | 1.4G total | Mixed | **Keep live** `hermes-agent-c0efe61655` (147M). Old immutable releases (208/208/210/221/390M; 1,237M total) are **probably removable only after validation** of all service/drop-in references and rollback policy. |
| `/tmp` | ~1.1G | **Probably removable in selected batches; validation required** | Includes two Repo Cockpit copies (~321M each) and a Hermes bundle (~55M). Validate owners, running processes, age, and recoverability. |
| `~/repo-cockpit/backups` | 316M | **Keep pending verification** | Backups are not proven restorable/redundant; verify contents and restore procedure first. |
| `/var/cache/apt` | 566M | **Probably removable; validation required** | Reconstructible, but cleaning is administrative; confirm rollback/offline needs. |
| journals/logs | ~317M (249M journal + 68M other logs) | **Probably reducible; validation required** | Retention/incident-evidence risk. Approve an age/size policy first. |

## Proposed cleanup batches — not executed

| Batch | Candidate | Estimated gross gain | Preconditions | Rollback / recovery |
|---|---|---:|---|---|
| A | npm cache | up to ~2.4G | Confirm no offline install/build requirement; record path/size. | Re-fetch packages; unsuitable if offline reproducibility is required. |
| B | obsolete Hermes releases only | ~1,237M (~1.21GiB) | Prove active release remains `c0efe61655`; inspect every service/drop-in/symlink; retain agreed rollback generations. | Restore retained archive or re-materialize the exact immutable SHA before changing references. |
| C | stale Cockpit workspaces | up to ~2.6G, likely less | Map each workspace to task/status/branch; preserve active/unpushed work; archive useful diffs. | Restore archive or recreate from pushed branch/commit. |
| D | validated `/tmp` artifacts | at least ~697M known; up to ~1.1G observed | Check age, owner, open descriptors, and process references. | Recreate copies/bundle from source; retain metadata for non-reproducible items. |
| E | apt cache | up to ~566M | Confirm package rollback and offline-install requirements. | Re-download exact versions if still available. |
| F | journal/log retention | up to ~317M depending approved floor | Define retention window and preserve incident evidence. | Restore only from external log backup; otherwise irreversible. |
| G | Cockpit backups | **0 now** | Keep until restore test, provenance, age, and redundancy are verified. | Existing backups are themselves rollback assets. |

Known non-workspace candidates total about 5,875M (~5.7GiB) gross. Including the full 2.6G workspace ceiling gives about 8,475M (~8.3GiB) gross. These are planning ceilings, not additive guarantees: `du` rounding, active files, retention floors, and overlap reduce realizable gain.

## Recommended supervised order

1. Inventory active processes, service references, workspace ownership, and backup restorability; remove nothing yet.
2. Approve rebuildable caches as separate batches (npm, then apt).
3. Approve stale `/tmp` artifacts only with owner/age/open-file evidence.
4. Keep live `c0ef`; choose an explicit old-release rollback floor.
5. Handle Cockpit workspaces per task/branch, with pushed commit or archive evidence.
6. Change log retention only under an incident-retention policy.

Every batch requires fresh `df`/`du` before and after, an explicit approval boundary, and rollback evidence. This document authorizes no cleanup.
