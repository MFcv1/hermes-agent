# Hermes Bot Accessibility Audit - 2026-06-29

Goal: make Hermes feel like a simple developer app for a learning developer,
not a pile of expert commands.

## Current Useful Surface

- `/dev`: simple entry point for the main workflows.
- `/new`: create or select a project/repo.
- `/conv`: resume a project conversation.
- `/task ...`: create a tracked Repo Cockpit task.
- `/audit`: bounded read-only review of the active project/task.
- `/runs`: see worker runs, tests, smokes, gates, and failures.
- `/prs`: see pending PRs.
- `/approve op_xxx`: approve a gated action.
- `/vps`: concise VPS/storage/cron/services overview.
- `/updatecheck`: short read-only update readiness check.
- `/watch`: simple release/VPS watchers.
- `/clean`: remove Telegram progress/status noise while preserving real replies.

## Friction Found

- Too many commands are exposed for a beginner. The bot is powerful, but the
  first question should be "what do I want to do?" not "which command exists?".
- Project/GitHub/deploy operations were split across `/new`, `/task`, `/audit`,
  `/runs`, `/prs`, `/approve`, `/vps`, `/watch`, and `/updatecheck` without one
  obvious home.
- The old `/serveurstatut` path was richer and noisier than needed for daily
  use. The simpler `/vps` overview is a better default.
- Some workflows should stay natural-language first. For branch/PR/merge/debug,
  the user should select a repo, then ask in plain French instead of learning a
  command grammar.

## Changes Applied

- Added `/dev` as the beginner cockpit:
  - Home: create/resume, code with GitHub, ops commands.
  - GitHub flow: branch, tests, PR, audit, approval guidance.
  - Ops/deploy: `/vps`, `/updatecheck`, `/watch`.
  - Learn: how to ask for explanations without treating Hermes as a black box.
- Added Telegram buttons for the `/dev` sections and linked existing actions
  where useful.
- Kept `/new`, `/conv`, `/task`, `/audit`, `/runs`, `/prs`, `/approve`, `/vps`,
  `/updatecheck`, and `/watch` as the small recommended command set.
- Routed legacy `/serveurstatut` to the simpler `/vps` implementation to reduce
  output noise while keeping compatibility.
- Kept advanced commands available but not central to the beginner workflow.

## Recommended Daily Workflow

1. Start with `/dev`.
2. Create or choose a repo with `/new`.
3. Ask in natural language:
   `crée une branche, corrige le bug, lance les tests, ouvre une PR, explique-moi le diff simplement`.
4. Use `/runs` when something is slow or failed.
5. Use `/audit` before merge/deploy.
6. Use `/vps` and `/updatecheck` before Hermes/VPS maintenance.
7. Use `/clean` if Telegram gets noisy.

## Not Done Yet

- A full visual dashboard/profile builder for beginners.
- A dedicated dependency-vulnerability command across every project.
- A one-click safe merge/deploy flow with a clear checklist and rollback.
- RSS/HTTP JSON watcher templates beyond GitHub releases and VPS health.

These are good follow-ups, but `/dev` plus the existing Repo Cockpit commands is
the simplest useful shell today.
