# Current Autonomy Deployment Audit — 2026-07-09

## Purpose

This file is the current operational checkpoint for Matthis' Hermes autonomy
work. Read it before resuming Portfolio V2, deployment automation, or VPS
Repo Cockpit work.

## Local Mac Repository

- Path: `/Users/matthis/.hermes/hermes-agent`
- Current branch: `main`
- Remote:
  - `origin`: `https://github.com/MFcv1/hermes-agent.git`
  - `upstream`: `https://github.com/NousResearch/hermes-agent.git`
- Branch sync:
  - `HEAD` is synchronized with `origin/main`.
  - The previous `origin/main` was preserved on
    `backup/main-before-ops-update-readiness-20260709`.
  - `main` intentionally contains the current product/autonomy work and is far
    from `upstream/main`; do not click GitHub "Sync fork" unless the goal is to
    reconcile with the official Hermes upstream.
- GitHub auth on the Mac:
  - Logged in as `MFcv1`.
  - Token scopes include `repo` and `workflow`.

## GitHub Source Of Truth

For the core `hermes-agent` repository, GitHub is available and `main` is the
source-of-truth branch for Matthis' current autonomy work.

For the live VPS Repo Cockpit repository, `/home/hermes/repo-cockpit` currently
has no configured Git remote. Its live commits are clean locally on the VPS,
but they are not automatically stored in GitHub from that repository. Until a
remote is added or the code is mirrored into a tracked repository, the VPS Git
history is local-only.

## Live VPS Repo Cockpit

- Host: `root@134.122.73.242`
- Path: `/home/hermes/repo-cockpit`
- Current branch: `main`
- Git status: clean at audit time.
- Latest live commits:
  - `1cde32c fix(deploy): fallback cloudflare preview branch`
  - `be67791 feat(deploy): add cloudflare deployment rail`
  - `c06f905 fix(worker): broaden pilot foundation trigger keywords`
  - `7b92d69 fix(worker): require pilot questions for scalable stack decisions`
  - `9c087a5 feat(worker): persist pilot plan artifacts to github`
- Services:
  - `hermes-gateway.service`: active
  - `hermes-repo-cockpit.service`: active
  - `hermes-weekly-ops.timer`: active

## Provider Readiness

Cockpit `/api/hosting/capabilities` reports:

| Provider | Status | Notes |
|---|---|---|
| GitHub | ready | `gh` authenticated as `MFcv1`, repo creation available. |
| Vercel | ready | `VERCEL_TOKEN` present, CLI installed. |
| Supabase | ready | token valid, org/project listing works, dry-run provisioning works. |
| Cloudflare | ready | token + account id present; Pages, Workers, KV, R2, and D1 API checks pass. |
| Firebase | blocked_auth | CLI installed but credentials are missing. |

R2 is now active on Cloudflare. Earlier notes saying R2 still needed dashboard
activation are superseded by this audit.

## Deployment Profiles

The following deploy profiles are currently ready in Cockpit:

- `next-vercel-static`
- `next-vercel-supabase`
- `astro-cloudflare-static`
- `astro-cloudflare-fullstack`
- `next-cloudflare-opennext`

`firebase-apphosting` remains blocked until Firebase credentials are configured.

## What The Bot Can Do Autonomously Now

In supervised autonomy mode, Hermes can now reasonably be asked to:

- create a new GitHub repository under `MFcv1`;
- scaffold or modify a project on a dedicated branch;
- push artifacts and implementation commits;
- deploy a Next.js project to Vercel;
- deploy a Next.js + Supabase project to Vercel with Supabase backend support;
- deploy an Astro/static project to Cloudflare Pages;
- deploy a Next.js/OpenNext project to Cloudflare Workers when the project
  includes `@opennextjs/cloudflare`, `open-next.config.*`, or a
  `deploy:cloudflare` script;
- run HTTP smoke tests and return the preview/live URL through Cockpit/Telegram.

Actions that must remain approval-gated:

- production deploys when a preview would suffice;
- DNS/domain changes;
- destructive database migrations;
- paid plan changes or budget increases;
- cleanup or deletion of storage/buckets/databases;
- merges to protected branches.

## Verified During This Audit

- Mac branch is clean and synced to `origin/codex/ops-update-readiness`.
- VPS Repo Cockpit is clean locally.
- GitHub auth works on the VPS and can list repos.
- Vercel API token resolves the account.
- Supabase Management API works with a User-Agent and Supabase CLI project
  listing works from the correct `hermes` execution context.
- Cloudflare API checks pass for Pages, Workers, KV, R2, and D1.
- Worker deploy dry-runs pass for:
  - `next-vercel-static`
  - `next-vercel-supabase-postgis-drizzle`
  - `astro-cloudflare-static`
  - `next-cloudflare-opennext`
- A small Cloudflare preview branch fallback bug was fixed on the VPS in
  `1cde32c`.

## Remaining Risks

- No real end-to-end deployment was launched during this audit. The next proof
  step is a controlled test repo that creates a project, deploys it, smokes the
  URL, and reports the final link.
- `/home/hermes/repo-cockpit` is not backed by a remote. This is the main source
  of storage/source-of-truth drift for the VPS-specific implementation.
- Supabase CLI can behave incorrectly if invoked under the wrong home directory
  (`/root` instead of `/home/hermes`). Backend service calls and worker calls
  should keep an explicit service environment when using Supabase CLI.

## Recommended Next Step

Run a supervised real deployment smoke through Codex Supervisor Mode:

1. Create a private repo such as `MFcv1/hermes-deploy-smoke-cloudflare`.
2. Scaffold a minimal Next.js/OpenNext or Astro app.
3. Deploy to Cloudflare preview.
4. Smoke the returned URL.
5. Commit/push all artifacts and record the result in a new report.

After that, repeat once for Vercel + Supabase if the product path needs a
database-backed app.

The first read-only shell for this is `scripts/codex_supervisor_mode.py`. It
can already send/capture Telegram via CUA, query Cockpit locally or over SSH,
check GitHub state, smoke an optional deploy URL, and write Markdown/JSON
reports under `docs/project/supervisor-runs/`.
