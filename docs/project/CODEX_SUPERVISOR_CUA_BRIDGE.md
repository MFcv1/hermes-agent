# Codex Supervisor local CUA fallback

`codex_supervisor_mode.py --send` uses this strict backend order:

1. the existing Python MCP `CuaDriverBackend` (priority);
2. the locally installed native CLI, invoked only as `cua-driver call <tool> <JSON>`;
3. an explicitly configured `--cua-bridge-command`, as a last resort only.

The second path requires no Python `mcp` package and no configured command. The
binary is resolved with `shutil.which("cua-driver")`, then the standard
`~/.local/bin/cua-driver` path. It reuses the existing CUA facade for Telegram
app/window selection, `list_windows`, `get_window_state` capture/AX,
`type_text`, and `press_key`.

The native CLI returns direct top-level tool JSON. In particular,
`list_windows` returns `{ "current_space_id": ..., "windows": [...] }`, not
an MCP `structuredContent` envelope. The adapter normalizes this real shape for
the existing facade; `list_apps`, `get_window_state`, images/text, and action
results are normalized similarly.

## Failure behavior

Every subprocess call uses a fixed argv (no shell), JSON serialization, captured
stdout/stderr, and a timeout. Missing executable, timeout, non-zero exit,
invalid/non-object JSON, absent Telegram app/window, empty capture, typing
failure, and key failure remain explicit non-success statuses. The external
bridge is attempted only when configured and only after the local CLI is absent
or its smoke fails.

No send occurs unless `--send` is present. For a safe local check, use the
read-only command:

```bash
~/.local/bin/cua-driver call list_apps '{}'
```

## Optional last-resort bridge

```bash
python3 scripts/codex_supervisor_mode.py \
  --message 'instruction to type' \
  --send \
  --cua-bridge-command '/absolute/path/to/codex-cua-bridge --stdio'
```

The bridge receives one JSON object on stdin for operation
`telegram_desktop_cua_smoke` and must emit one JSON object with a non-empty
`status`. A missing, failing, timed-out, or malformed bridge returns
`cua_bridge_failed`; it is never reported as a successful send. No secret or
new `HERMES_*` setting is introduced.
