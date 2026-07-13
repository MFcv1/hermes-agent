# Hermes workflow reliability — Lot 2 report

Date: 2026-07-13
Scope: HMR-003 and the related HMR-008/HMR-009 runtime accounting gaps
Branch: `codex/fix/hermes-supervisor-reliability`

## Reproduced defects

- The main loop limited successful tool iterations, not real provider attempts:
  retries inside `agent/conversation_loop.py` did not consume the outer limit.
- `agent/turn_finalizer.py` issued a synthesis request after the iteration limit,
  so `max_iterations=8` could produce a ninth provider call.
- `tools/delegate_tool.py` explicitly gave every child a fresh budget, allowing
  fan-out to bypass the parent cap.
- session API-call telemetry was incremented only after a successful response
  with usage, leaving active or failed calls invisible.
- Cockpit evaluation formatting omitted an empty suite, which allowed an empty
  evidence set to look healthy instead of explicitly reporting `not_run`.

## Implemented contract

`agent/run_envelope.py` now owns a per-run contract containing the run, session
and task identifiers; expected model, provider and effort; a thread-safe total
and per-phase model-call budget; a reserved final-call allowance; permissions;
and the subagent policy.

The runtime now:

- validates model/provider/effort before the first provider request;
- reserves and records every real provider attempt before transport dispatch,
  including retries and Codex app-server calls;
- keeps the final synthesis inside the declared total limit;
- shares one atomic budget between parent and child envelopes;
- refuses delegation before child construction when policy is `deny`;
- exposes `used/limit/reserved` in logs, activity state and the final receipt;
- persists the call counter and emits `llm.call.started` into `/v1/runs` SSE;
- reports empty evaluations as `not_run`.

The envelope is metadata beside the transcript. It does not alter the system
prompt, tool schema or prior messages, preserving prompt-cache stability.

## Acceptance evidence

- A real `AIAgent` mock-provider path with budget 8 performs exactly seven work
  calls and one reserved synthesis call: provider count 8, runtime count 8,
  telemetry count 8.
- A mismatched model is rejected with zero provider calls and zero budget use.
- Parent and child envelopes share the same locked budget object.
- `subagent_policy=deny` returns before `_build_child_agent` can run.
- the call-start path persists and emits exactly once per provider attempt.
- phase limits and the final reservation fail closed when exhausted.
- `/v1/runs` receives a structured `llm.call.started` event with the canonical
  API run identifier and current `used/limit/reserved` values.
- an empty evaluation map and a zero-total suite both render `not_run`.

Validation used `scripts/run_tests.sh`: 603 tests passed across the main agent,
Codex runtime, delegation, API-run, Cockpit and finalizer regression files; the
five iteration-budget race tests also passed. Focused post-change checks added
39 passing envelope/API/Cockpit/finalizer tests plus two retry/exhaustion tests.
`git diff --check` and Python byte-compilation passed.

## Compatibility notes

- `max_iterations` is now a strict provider-call ceiling including synthesis.
  Existing callers therefore receive at most `N-1` work calls when one final
  call is reserved. This is intentional and closes HMR-003.
- `result["api_calls"]` retains its historical successful-loop meaning for
  compatibility. Exact provider attempts are exposed as `model_calls`, in the
  run receipt, and through `session_api_calls`.
- agents created by older unit-test stubs without a run envelope retain the
  legacy finalizer path; all real `AIAgent` instances create an envelope.
- the legacy empty-summary retry remains callable, but a one-call reservation
  prevents it from making a second provider request beyond the declared cap.
