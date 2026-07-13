# Hermes workflow reliability — Lot 3 report

Date: 2026-07-13
Scope: HMR-004, HMR-005 and HMR-007
Branch: `codex/fix/hermes-supervisor-reliability`

## Reproduced defects

- reset invalidated only the active turn token; detached process and delegation
  completions had no durable conversation generation and could be injected into
  the next conversation;
- stop interrupted the running agent before all pending dispatch rails were
  closed;
- gateway auto-vision injected the complete provider description directly into
  the user turn and did not recheck cancellation between multiple images;
- local process-group cleanup could be escaped by `setsid` plus double-fork,
  and the reusable shell snapshot accidentally persisted ephemeral process
  ownership state;
- background process ownership did not survive the crash checkpoint;
- heavy Node builds had no host-wide serialization rail, while batch workers
  could start an implicit LSP service each.

The sequential executor already checked the interrupt flag before each tool.
The new regression test proves that contract specifically when interrupt is
raised by tool N: tool N+1 receives a cancellation result and is never invoked.

## Implemented contract

The gateway now owns a durable, bounded `session_generation` ledger. Explicit
reset, stop, suspension and automatic reset advance it before new work can be
accepted. Session generation is carried through the run envelope, context-local
tool environment, async delegation records, background process sessions,
watchers, checkpoint recovery and completion events. Consumers discard stale
events before routing or context injection.

Stop/reset cleanup now invalidates both generations, clears adapter and runner
pending queues, clears queued events, closes and interrupts session delegations,
kills session processes, then signals the active agent. This establishes the
required close-dispatch-before-cancel ordering.

Auto-vision rechecks the active run before every image and after every provider
result. Descriptions above 20,000 characters are persisted under the active
Hermes home and only a 2,000-character preview plus artifact reference enters
the transcript. The existing per-tool/per-turn result budget remains the common
rail for normal tool calls, with a pinned lower threshold for `vision_analyze`.

Local commands receive a random inherited ownership marker in addition to the
existing process group. Cleanup scans that marker to find reparented descendants,
records PIDs, working directories and listening ports, sends TERM then KILL, and
verifies that no owned PID or port remains. The marker is excluded from reusable
shell snapshots. Background checkpoints persist it so startup can clean escaped
children even when the recorded wrapper PID is gone or recycled.

Finally, heavy Node build/test/lint/typecheck commands acquire one cross-process
file lock. Foreground calls release it on return; background sessions hold it
until verified completion. Batch workers set an internal execution marker and
LSP startup defaults off there, with an explicit `lsp.batch_enabled: true`
configuration opt-in.

## Acceptance evidence

- reset race: an event from generation 0 is rejected after reset advances and
  persists generation 1;
- stop ordering: queues, delegation dispatch and processes are closed before
  the agent interrupt callback runs;
- stop between tools: only tool N is invoked; tool N+1 is returned as not
  started;
- double-fork success path: a daemon that calls `setsid`, forks again and opens
  a TCP listener is reaped after its wrapper exits successfully;
- timeout path: a server process and its listening port are removed after the
  command returns timeout 124;
- crash recovery: a checkpoint with a dead wrapper PID uses the durable owner
  marker to reap the surviving server and close its port;
- vision quota: a 50,000-character analysis is stored as an artifact while the
  enriched turn stays below 5,000 characters; cancellation prevents analysis of
  the second image;
- two concurrent Node builds never overlap their protected section;
- batch mode disables LSP by default and permits an explicit config opt-in.

Validation used `scripts/run_tests.sh`. A final focused pass completed 155/155
tests across run/session envelopes, process registry and ownership, async
delegation, terminal, vision, LSP and heavy-job serialization. The full
386-test `run_agent` file passed. An extended 652-test pass produced 651 passes
and one unrelated macOS path-alias assertion in
`test_background_command.py` (`/tmp` versus `/private/tmp`); the background
command behavior itself was not modified by this lot. Python byte-compilation
and `git diff --check` also passed.

## Compatibility and limitations

- `session_generation` defaults to zero for API and legacy callers, preserving
  existing constructors and event formats while making gateway-managed sessions
  strict.
- process ownership is a portable inherited-marker envelope layered over POSIX
  process groups, not a Linux-only cgroup. It covers forks, `setsid`, successful
  wrapper exit, timeout, stop and checkpoint recovery without adding a daemon or
  privileged service dependency.
- the marker scan relies on same-user process environment visibility. Existing
  process-group and backend-specific cancellation remain the primary fallback
  where a platform denies that visibility.
- heavy-job classification is intentionally limited to known Node build, test,
  lint and typecheck entry points; ordinary terminal commands are unaffected.
