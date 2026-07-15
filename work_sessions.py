"""Persistent work-session store for Hermes/Codex workflows.

Work sessions are task-level records that sit above a chat transcript.  They
bind a repo, workflow, Cockpit task, branch/PR, artifacts, and a compact resume
packet so Telegram/Cockpit/Desktop clients can resume real work without relying
on a long raw chat history.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Iterable

from hermes_constants import get_hermes_home


_STATUSES = {"open", "active", "blocked", "done", "failed", "deleted"}
_WORKFLOWS = {
    "supervisor",
    "pilote",
    "autopilot",
    "ask_review",
    "libre",
    "debug",
    "deploy",
}
_CHANNELS = {"codex", "telegram", "cockpit", "cli", "dashboard", "api"}


def _now() -> float:
    return time.time()


def _clean(value: Any, limit: int = 400) -> str:
    text = str(value or "").strip()
    text = text.replace("\x00", "")
    return text[:limit]


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _json_loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def new_work_session_id() -> str:
    return f"ws_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"


class WorkSessionStore:
    """SQLite-backed work-session store scoped to one Hermes home."""

    def __init__(self, *, hermes_home: Path | None = None, db_path: Path | None = None) -> None:
        self.hermes_home = Path(hermes_home) if hermes_home is not None else get_hermes_home()
        self.db_path = Path(db_path) if db_path is not None else self.hermes_home / "work_sessions.db"
        self.artifacts_root = self.hermes_home / "work-sessions"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts_root.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "WorkSessionStore":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _migrate(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS work_sessions (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open',
                workflow TEXT NOT NULL DEFAULT 'libre',
                origin_channel TEXT NOT NULL DEFAULT 'telegram',
                repo TEXT,
                provider TEXT,
                cockpit_task_id TEXT,
                hermes_session_id TEXT,
                gateway_session_key TEXT,
                git_branch TEXT,
                pr_url TEXT,
                preview_url TEXT,
                live_url TEXT,
                brief_path TEXT,
                report_path TEXT,
                screenshots_dir TEXT,
                objective TEXT,
                summary TEXT,
                current_state TEXT,
                next_actions_json TEXT NOT NULL DEFAULT '[]',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                closed_at REAL
            );
            CREATE INDEX IF NOT EXISTS idx_work_sessions_updated
                ON work_sessions(updated_at DESC);
            CREATE INDEX IF NOT EXISTS idx_work_sessions_repo
                ON work_sessions(repo);
            CREATE INDEX IF NOT EXISTS idx_work_sessions_status
                ON work_sessions(status);
            CREATE INDEX IF NOT EXISTS idx_work_sessions_workflow
                ON work_sessions(workflow);
            CREATE INDEX IF NOT EXISTS idx_work_sessions_origin
                ON work_sessions(origin_channel);
            CREATE INDEX IF NOT EXISTS idx_work_sessions_task
                ON work_sessions(cockpit_task_id);

            CREATE TABLE IF NOT EXISTS work_session_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_session_id TEXT NOT NULL REFERENCES work_sessions(id) ON DELETE CASCADE,
                event_type TEXT NOT NULL,
                role TEXT,
                content TEXT,
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_work_session_events_session
                ON work_session_events(work_session_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS work_session_artifacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                work_session_id TEXT NOT NULL REFERENCES work_sessions(id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                path TEXT NOT NULL,
                label TEXT,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_work_session_artifacts_session
                ON work_session_artifacts(work_session_id, created_at DESC);
            """
        )
        self._conn.commit()

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        data = dict(row)
        data["next_actions"] = _json_loads(data.pop("next_actions_json", "[]"), [])
        data["metadata"] = _json_loads(data.pop("metadata_json", "{}"), {})
        return data

    def create_session(
        self,
        *,
        title: str,
        workflow: str = "libre",
        origin_channel: str = "telegram",
        repo: str | None = None,
        provider: str | None = None,
        cockpit_task_id: str | None = None,
        hermes_session_id: str | None = None,
        gateway_session_key: str | None = None,
        objective: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        work_session_id = new_work_session_id()
        workflow = workflow if workflow in _WORKFLOWS else "libre"
        origin_channel = origin_channel if origin_channel in _CHANNELS else "api"
        now = _now()
        clean_title = _clean(title, 180) or _clean(objective, 180) or "Nouvelle session"
        session_dir = self.artifacts_root / work_session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        self._conn.execute(
            """
            INSERT INTO work_sessions (
                id, title, status, workflow, origin_channel, repo, provider,
                cockpit_task_id, hermes_session_id, gateway_session_key,
                objective, metadata_json, created_at, updated_at
            ) VALUES (?, ?, 'open', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                work_session_id,
                clean_title,
                workflow,
                origin_channel,
                _clean(repo, 260) or None,
                _clean(provider, 120) or None,
                _clean(cockpit_task_id, 120) or None,
                _clean(hermes_session_id, 160) or None,
                _clean(gateway_session_key, 260) or None,
                _clean(objective, 4000) or None,
                _json_dumps(metadata or {}),
                now,
                now,
            ),
        )
        self.add_event(
            work_session_id,
            "session.created",
            role="system",
            content=f"Work session created: {clean_title}",
            payload={"repo": repo, "workflow": workflow, "origin_channel": origin_channel},
            commit=False,
        )
        self._conn.commit()
        return self.get_session(work_session_id) or {"id": work_session_id}

    def get_session(self, work_session_id: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT * FROM work_sessions WHERE id = ?",
            (_clean(work_session_id, 120),),
        ).fetchone()
        return self._row_to_dict(row)

    def get_by_hermes_session_id(self, hermes_session_id: str) -> dict[str, Any] | None:
        """Return the latest non-deleted work session linked to a chat."""
        clean_id = _clean(hermes_session_id, 120)
        if not clean_id:
            return None
        row = self._conn.execute(
            """
            SELECT * FROM work_sessions
            WHERE hermes_session_id = ? AND status != 'deleted'
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (clean_id,),
        ).fetchone()
        return self._row_to_dict(row)

    def list_sessions(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        repo: str | None = None,
        provider: str | None = None,
        status: str | None = None,
        workflow: str | None = None,
        origin_channel: str | None = None,
        since: float | None = None,
        until: float | None = None,
    ) -> list[dict[str, Any]]:
        clauses = ["status != 'deleted'"]
        params: list[Any] = []
        for column, value in (
            ("repo", repo),
            ("provider", provider),
            ("status", status),
            ("workflow", workflow),
            ("origin_channel", origin_channel),
        ):
            if value:
                clauses.append(f"{column} = ?")
                params.append(_clean(value, 260))
        if since is not None:
            clauses.append("updated_at >= ?")
            params.append(float(since))
        if until is not None:
            clauses.append("updated_at <= ?")
            params.append(float(until))
        sql = "SELECT * FROM work_sessions WHERE " + " AND ".join(clauses)
        sql += " ORDER BY updated_at DESC LIMIT ? OFFSET ?"
        params.extend([max(1, min(int(limit), 200)), max(0, int(offset))])
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_dict(row) for row in rows if row is not None]

    def update_session(self, work_session_id: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            "title", "status", "workflow", "origin_channel", "repo", "provider",
            "cockpit_task_id", "hermes_session_id", "gateway_session_key",
            "git_branch", "pr_url", "preview_url", "live_url", "brief_path",
            "report_path", "screenshots_dir", "objective", "summary",
            "current_state",
        }
        updates: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key == "next_actions":
                updates.append("next_actions_json = ?")
                params.append(_json_dumps(value if isinstance(value, list) else []))
            elif key == "metadata":
                updates.append("metadata_json = ?")
                params.append(_json_dumps(value if isinstance(value, dict) else {}))
            elif key in allowed:
                if key == "status" and value not in _STATUSES:
                    continue
                if key == "workflow" and value not in _WORKFLOWS:
                    continue
                if key == "origin_channel" and value not in _CHANNELS:
                    continue
                updates.append(f"{key} = ?")
                params.append(_clean(value, 4000) or None)
        if not updates:
            return self.get_session(work_session_id)
        updates.append("updated_at = ?")
        params.append(_now())
        if fields.get("status") in {"done", "failed"}:
            updates.append("closed_at = COALESCE(closed_at, ?)")
            params.append(_now())
        params.append(_clean(work_session_id, 120))
        self._conn.execute(
            f"UPDATE work_sessions SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        self._conn.commit()
        return self.get_session(work_session_id)

    def attach_cockpit_task(self, work_session_id: str, task_id: str) -> dict[str, Any] | None:
        session = self.update_session(work_session_id, cockpit_task_id=task_id)
        self.add_event(work_session_id, "cockpit.task.attached", content=_clean(task_id, 120))
        return session

    def add_event(
        self,
        work_session_id: str,
        event_type: str,
        *,
        role: str | None = None,
        content: str | None = None,
        payload: dict[str, Any] | None = None,
        commit: bool = True,
    ) -> int:
        now = _now()
        cur = self._conn.execute(
            """
            INSERT INTO work_session_events
                (work_session_id, event_type, role, content, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _clean(work_session_id, 120),
                _clean(event_type, 120) or "event",
                _clean(role, 80) or None,
                _clean(content, 8000) or None,
                _json_dumps(payload or {}),
                now,
            ),
        )
        self._conn.execute(
            "UPDATE work_sessions SET updated_at = ? WHERE id = ?",
            (now, _clean(work_session_id, 120)),
        )
        if commit:
            self._conn.commit()
        return int(cur.lastrowid)

    def list_events(self, work_session_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM work_session_events
            WHERE work_session_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (_clean(work_session_id, 120), max(1, min(int(limit), 500))),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["payload"] = _json_loads(item.pop("payload_json", "{}"), {})
            out.append(item)
        return out

    def add_artifact(
        self,
        work_session_id: str,
        *,
        kind: str,
        path: str,
        label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        cur = self._conn.execute(
            """
            INSERT INTO work_session_artifacts
                (work_session_id, kind, path, label, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _clean(work_session_id, 120),
                _clean(kind, 80) or "artifact",
                _clean(path, 1000),
                _clean(label, 240) or None,
                _json_dumps(metadata or {}),
                _now(),
            ),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def list_artifacts(self, work_session_id: str) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            """
            SELECT * FROM work_session_artifacts
            WHERE work_session_id = ?
            ORDER BY created_at DESC
            """,
            (_clean(work_session_id, 120),),
        ).fetchall()
        out = []
        for row in rows:
            item = dict(row)
            item["metadata"] = _json_loads(item.pop("metadata_json", "{}"), {})
            out.append(item)
        return out

    def resume_packet(self, work_session_id: str) -> dict[str, Any] | None:
        session = self.get_session(work_session_id)
        if not session:
            return None
        return {
            "work_session_id": session["id"],
            "title": session.get("title"),
            "status": session.get("status"),
            "workflow": session.get("workflow"),
            "origin_channel": session.get("origin_channel"),
            "repo": session.get("repo"),
            "provider": session.get("provider"),
            "cockpit_task_id": session.get("cockpit_task_id"),
            "hermes_session_id": session.get("hermes_session_id"),
            "gateway_session_key": session.get("gateway_session_key"),
            "git_branch": session.get("git_branch"),
            "pr_url": session.get("pr_url"),
            "preview_url": session.get("preview_url"),
            "live_url": session.get("live_url"),
            "objective": session.get("objective"),
            "summary": session.get("summary"),
            "current_state": session.get("current_state"),
            "next_actions": session.get("next_actions") or [],
            "artifacts": self.list_artifacts(work_session_id),
            "recent_events": list(reversed(self.list_events(work_session_id, limit=20))),
        }

    def resume_prompt(self, work_session_id: str) -> str | None:
        packet = self.resume_packet(work_session_id)
        if not packet:
            return None
        return (
            "Reprends cette session de travail Hermes.\n\n"
            "Paquet de reprise JSON:\n"
            f"{json.dumps(packet, ensure_ascii=False, indent=2)}\n\n"
            "Continue a partir de cet etat. Ne relis pas tout l'ancien chat si "
            "le paquet suffit; utilise les artefacts uniquement quand ils sont "
            "necessaires."
        )

    def delete_session(self, work_session_id: str) -> bool:
        work_session_id = _clean(work_session_id, 120)
        found = self.get_session(work_session_id) is not None
        if not found:
            return False
        self._conn.execute("DELETE FROM work_sessions WHERE id = ?", (work_session_id,))
        self._conn.commit()
        shutil.rmtree(self.artifacts_root / work_session_id, ignore_errors=True)
        return True

    def delete_many(self, ids: Iterable[str]) -> int:
        count = 0
        for work_session_id in ids:
            if self.delete_session(str(work_session_id)):
                count += 1
        return count

