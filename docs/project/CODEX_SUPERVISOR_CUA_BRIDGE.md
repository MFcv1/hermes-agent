# Codex Supervisor CUA bridge

`codex_supervisor_mode.py --send` first uses the existing Python MCP `CuaDriverBackend`. A standalone Python process cannot import or call Codex's in-process `node_repl` CUA tool. When that backend is unavailable, the supervisor therefore fails closed unless an external bridge is selected explicitly.

## Invocation

```bash
python3 scripts/codex_supervisor_mode.py \
  --message 'instruction to type' \
  --send \
  --cua-bridge-command '/absolute/path/to/codex-cua-bridge --stdio'
```

Replace `/absolute/path/to/codex-cua-bridge --stdio` with an operator-provided executable that actually has access to the in-process CUA runtime. This repository does **not** claim that such an executable exists and does not auto-select a command. Without the option, the result is `cua_bridge_required` and is non-success. A missing, failing, timed-out, or malformed bridge returns `cua_bridge_failed`; it is never reported as a send.

## JSON-over-stdio contract

The command receives one JSON object on stdin:

```json
{"schema":1,"operation":"telegram_desktop_cua_smoke","intent":"...","app":"Telegram","mode":"som","send":true,"no_enter":false,"evidence_dir":"..."}
```

It must write exactly one JSON object to stdout with a non-empty `status`. Only a response backed by the bridge's real UI execution may use `sent_review_required`. The supervisor preserves the bridge status and adds:

- `backend.selected = "external_bridge"`
- `backend.fallback_used = true`
- MCP failure diagnostics
- `fallback.ok = true|false`

A bridge process exit code other than zero, invalid JSON, empty status, or a 120-second timeout is explicit failure. No secret or `HERMES_*` environment variable is introduced.
