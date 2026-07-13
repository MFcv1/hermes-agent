# Project Status

> Copy this file to the project root as `PROJECT_STATUS.md`. Replace every
> placeholder with machine-verifiable evidence before a task is considered
> completed.

## Status

- Final state: `<ready_for_review | blocked | no_changes>`
- Scope delivered: `<summary>`
- Known limitations: `<none or explicit list>`

## Source

- Repository: `<owner/repo>`
- Branch: `<branch>`
- Commit: `<full 40-64 character SHA>`
- Working tree / remote divergence: `<clean and 0/0, or exact exception>`

## Gates

- Tests: `<command and result>`
- Build/typecheck/lint: `<command and result or not applicable>`
- Review/security checks: `<result or not applicable>`
- Evidence reuse key: `<SHA + command + environment digest when available>`

## URLs

- Preview/live URL: `<URL or not deployed>`
- Smoke manifest/report: `<path or not applicable>`
- Provider deployment/version ID: `<ID or not applicable>`

## Resources and limits

- Resources created or changed: `<none or list>`
- Runtime/build variables required: `<names only; never values>`
- Cost/plan implications: `<none or explicit amount/trigger>`
- Budgets consumed: `<model calls and token/cost evidence>`

## Rollback

- Previous known-good SHA/version: `<SHA or provider version>`
- Exact rollback procedure: `<commands or runbook link>`
- Data migration caveat: `<none or explicit>`

## Next action

- Human decision required: `<review/merge/deploy/DNS/none>`
- Recommended next task: `<one concrete action>`
