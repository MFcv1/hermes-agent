---
name: cloudflare-atomic-deploy
description: "Build and deploy a Next.js/OpenNext Cloudflare Worker from one immutable, SHA-attested artifact with contractual smoke tests and N-1 rollback."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [cloudflare, nextjs, opennext, deployment, rollback, smoke-test]
    related_skills: [test-driven-development, systematic-debugging]
---

# Cloudflare Atomic Deploy

Use this runbook for Next.js sites deployed through OpenNext to Cloudflare
Workers. It deliberately uses the existing terminal through `hermes deploy`;
it does not add a permanent model tool.

## Safety contract

- Run `validate` and `prepare` without provider mutation first.
- Never reuse the project's existing `.next` or `.open-next`. `prepare` moves
  them aside, builds both stages fresh, and restores them afterward.
- Never deploy a dirty tree by default. `--allow-dirty` is diagnostic only and
  must not be used for a release.
- Never put secret values in the contract, Wrangler `vars`, manifest, receipt,
  logs, or prompt. `runtime.required_secrets` contains names only.
- A public upload requires `--confirm-upload`; rollback requires
  `--confirm-rollback`. Production, DNS, paid-plan changes, and resource
  creation still require explicit operator approval.
- Before an approved upload, announce the exact smoke routes and the rollback
  target policy. Do not report success unless the post-upload smoke passes.

Add `.hermes-deploy/` to the target project's `.gitignore`. The directory holds
immutable local artifacts, manifests, provider receipts, and smoke reports.

## Project contract

Create `cloudflare.deploy.yaml` in the target project. Non-secret values shown
below are examples and must be replaced with the verified target values:

```yaml
provider:
  worker_name: portfolio
  account_id: "0123456789abcdef0123456789abcdef"
  plan: free
  max_compressed_bytes: 3145728

build:
  required_env:
    - NEXT_PUBLIC_SITE_URL
  public_origin_env: NEXT_PUBLIC_SITE_URL

runtime:
  required_vars:
    - NEXT_PUBLIC_SITE_URL
  required_secrets: []

cache:
  mode: static_assets

commands:
  next_build: npm run build
  open_next_build: npx opennextjs-cloudflare build --skipNextBuild
  wrangler: npx wrangler

smoke:
  base_url: https://portfolio.example
  build_info_path: /__hermes/build-info.json
  timeout_seconds: 20
  checks:
    - path: /
      status: 200
      contains: [Portfolio]
      content_type: text/html
      canonical: /
      cache_control_contains: public
    - path: /projects/example
      status: 200
      contains: [Example]
      content_type: text/html
      canonical: /projects/example
    - path: /_next/static/chunks/app.js
      status: 200
      content_type: javascript
      cache_control_contains: immutable
    - path: /robots.txt
      status: 200
      not_contains: [localhost, 127.0.0.1]
    - path: /sitemap.xml
      status: 200
      contains: [<urlset]
```

Wrangler must name the same Worker and account, use
`.open-next/worker.js`, bind `.open-next/assets`, and enable
`nodejs_compat`. A runtime copy of the public origin must equal the build-time
origin. This prevents a valid build from advertising localhost or a different
domain after deployment.

OpenNext cache interception and dummy cache implementations are rejected by
this workflow. Static assets remain the only supported cache mode until a real
remote cache is configured and validated end to end.

## Read-only preflight and immutable build

```bash
hermes deploy cloudflare validate --project /absolute/path/to/project
hermes deploy cloudflare prepare --project /absolute/path/to/project --json
```

Keep the returned `manifest.json` path. Inspect its source commit, account,
Worker, plan, compressed size/limit, public origin, build/runtime variable
names, cache configuration, Wrangler config digest, and artifact digest. The
generated static `/__hermes/build-info.json` binds the live site to that SHA
and digest.

If preflight fails, fix the project or provider configuration and create a new
artifact. Do not edit an artifact: digest verification will reject it.

## Approved upload and post-deploy proof

Announce the routes listed in `smoke.checks`, including a representative SSG
dynamic route, a hashed asset, canonical, robots, sitemap and cache headers.
Then, only when a free preview upload is already authorized or the operator has
approved the exact external change:

```bash
hermes deploy cloudflare deploy \
  --project /absolute/path/to/project \
  --confirm-upload \
  --json
```

The command rebuilds fresh, verifies Cloudflare identity and secret names,
uploads exactly the artifact-local Wrangler config, records the provider's
previous and deployed version IDs, and immediately runs cache-busted smoke
checks. The deployment is complete only when `smoke-report.json` has
`"ok": true` and its SHA/digest match the manifest.

Re-run smoke without uploading:

```bash
hermes deploy cloudflare smoke \
  --project /absolute/path/to/project \
  --manifest /absolute/path/to/manifest.json \
  --json
```

## Rollback N-1

If the upload or smoke is unhealthy, inspect `deployment-receipt.json`. It
contains the exact prior provider version. With explicit approval:

```bash
hermes deploy cloudflare rollback \
  --project /absolute/path/to/project \
  --manifest /absolute/path/to/manifest.json \
  --confirm-rollback \
  --json
```

Preserve `rollback-receipt.json`, then verify the known-good site's routes
manually or with the manifest belonging to that prior version. Never guess a
rollback version from timestamps or Telegram history.
