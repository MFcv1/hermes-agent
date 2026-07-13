# Hermes workflow reliability — Lot 4 report

Date: 2026-07-13
Scope: HMR-006 and HMR-015
Branch: `codex/fix/hermes-supervisor-reliability`

## Reproduced defects

The repository had no shared Cloudflare application deployment workflow. A
Hermes task could call a project's build and Wrangler commands directly, but
there was no invariant relating the source SHA, the OpenNext output, the
provider account, the uploaded bundle and the URL subsequently smoked.

Consequently, a task had no common fail-closed protection against:

- reusing an old `.open-next` directory after an incomplete build;
- compiling a public origin as localhost or using different build/runtime
  origins;
- silently using a dummy OpenNext cache;
- deploying to the wrong authenticated Cloudflare account;
- accepting a live URL whose robots, canonical, SSG route or assets belonged
  to a prior release;
- reporting a deploy without a provider version receipt or deterministic
  rollback target.

This is a capability at the edge, so the implementation follows the Footprint
Ladder: one CLI command plus one skill, with no new model tool or mutable
conversation prompt surface.

## Implemented contract

`hermes deploy cloudflare` provides five explicit phases: `validate`,
`prepare`, `deploy`, `smoke` and `rollback`.

Validation binds the declared Worker and account to Wrangler, requires
`nodejs_compat`, the canonical OpenNext entry point and asset directory,
separates build variables from Wrangler runtime variables and remote secret
names, rejects local public origins, and requires a static-assets cache policy.
Dummy cache references and response-cache interception fail closed.

Preparation resolves a full Git SHA and refuses dirty sources by default. It
moves any local `.next` and `.open-next` aside, runs a fresh Next build followed
by OpenNext with `--skipNextBuild`, copies only the new OpenNext output into a
run-specific artifact, restores the developer's previous outputs, then runs a
Wrangler dry-run from an artifact-local config. Compressed size is checked
against the declared Free or Paid limit before any upload.

The immutable manifest records source SHA and dirty state, account, Worker,
plan, origin, build/runtime variable names, secret names only, cache config,
Wrangler config digest, artifact digest and compressed size/limit. A generated
static `/__hermes/build-info.json` binds the served site to the same SHA and
digest. Any later Worker, asset, config or attestation modification is rejected.

Deployment requires `--confirm-upload`, rechecks authenticated account and
remote secret inventory, records the prior Cloudflare deployment version,
uploads exactly the artifact-local config with runtime variables preserved,
and requires a deployed provider version ID. It then runs the contractual smoke
suite immediately. Rollback requires a separate `--confirm-rollback` and uses
only the recorded N-1 version; it emits its own receipt.

The bundled `cloudflare-atomic-deploy` skill supplies a non-secret contract
template, mandatory approval boundary, preview procedure, smoke announcement
and rollback runbook for future Portfolio work.

## Acceptance evidence

The focused tests reproduce and reject every required incident:

- missing build-time variable;
- dummy cache configuration;
- stale or modified OpenNext artifact;
- robots content still pointing at localhost;
- authenticated account different from the contract;
- live build-info SHA/digest different from the manifest.

Additional integration coverage proves that Next runs before OpenNext, local
build outputs are restored, the immutable artifact contains only the fresh
output, environment/cache evidence reaches the manifest, deploy runs from the
artifact directory and exact config, provider N and N-1 version IDs reach the
receipt, rollback uses N-1, and both external mutations require explicit flags.

Validation used the canonical `scripts/run_tests.sh` runner. The final focused
pass completed 155/155 tests across the new workflow, CLI parser and routing
regressions, argument propagation, and the existing skill loader/tool suite.
The real `hermes deploy cloudflare --help` entry point and Python byte-compilation
also passed.
No Cloudflare upload, DNS mutation, production change or paid resource was
performed.

## Compatibility and limitations

- Existing projects are unaffected until they create `cloudflare.deploy.yaml`
  and invoke the new command.
- The declared `provider.plan` selects and records the applicable configured
  compressed-size limit. Cloudflare identity and account are checked online;
  Wrangler does not expose a stable account-plan query, so the workflow does
  not claim that the billing plan itself was discovered remotely.
- Static assets are the only supported cache mode. A real remote incremental
  cache can be added later only with a concrete consumer and E2E validation;
  dummy cache is intentionally rejected.
- The generated build-info endpoint proves which artifact is live without
  adding an application API route. It is non-secret and lives with the static
  assets.
- The workflow does not create Workers, domains, DNS records, plans or secrets.
  Those remain explicit provider actions outside this lot.
