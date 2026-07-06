from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from pathlib import Path
from typing import Any


class HandoffStore:
    """SQLite cache for Libre active context, handoffs, and learned policies.

    Repo Cockpit remains the source of truth for task handoffs. This store keeps
    only the chat/conversation pointer and a local cache so Telegram can resume
    without depending on the old append-only JSON file.
    """

    def __init__(self, path: str | Path, *, legacy_json_path: str | Path | None = None):
        self.path = Path(path)
        self.legacy_json_path = Path(legacy_json_path) if legacy_json_path else None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            self._ensure_schema(con)
        if self.legacy_json_path and self.legacy_json_path.exists():
            self.migrate_json(self.legacy_json_path)

    def get_active(self, key: str) -> dict[str, Any]:
        with self._connect() as con:
            row = con.execute("select * from active_contexts where key=?", (str(key),)).fetchone()
        if not row:
            return {"mode": "libre"}
        return self._context_dict(row)

    def set_active(self, key: str, **updates: Any) -> dict[str, Any]:
        current = self.get_active(key)
        current.update({k: v for k, v in updates.items() if v not in (None, "")})
        current.setdefault("mode", "libre")
        now = int(time.time())
        with self._connect() as con:
            con.execute(
                """insert into active_contexts(
                       key,repo,mode,task,task_id,parent_task_id,thread_id,last_handoff_id,updated_at,payload_json
                   ) values(?,?,?,?,?,?,?,?,?,?)
                   on conflict(key) do update set
                       repo=excluded.repo,
                       mode=excluded.mode,
                       task=excluded.task,
                       task_id=excluded.task_id,
                       parent_task_id=excluded.parent_task_id,
                       thread_id=excluded.thread_id,
                       last_handoff_id=excluded.last_handoff_id,
                       updated_at=excluded.updated_at,
                       payload_json=excluded.payload_json""",
                (
                    str(key),
                    current.get("repo", ""),
                    current.get("mode", "libre"),
                    current.get("task", ""),
                    current.get("task_id", ""),
                    current.get("parent_task_id", ""),
                    current.get("thread_id", ""),
                    current.get("last_handoff_id", ""),
                    now,
                    json.dumps({k: v for k, v in current.items() if k not in {"repo", "mode", "task", "task_id", "parent_task_id", "thread_id", "last_handoff_id", "updated_at"}}, ensure_ascii=False, sort_keys=True),
                ),
            )
            con.commit()
        return self.get_active(key)

    def soft_close(self, key: str, *, reason: str = "/libre") -> dict[str, Any]:
        current = self.get_active(key)
        now = int(time.time())
        task_id = str(current.get("task_id") or "")
        handoff_id = "handoff_" + hashlib.sha1(f"{key}:{task_id}:{now}:{reason}".encode()).hexdigest()[:14]
        handoff = {
            "id": handoff_id,
            "created_at": now,
            "reason": reason,
            "repo": str(current.get("repo") or ""),
            "mode": str(current.get("mode") or ""),
            "task": str(current.get("task") or ""),
            "task_id": task_id,
            "parent_task_id": str(current.get("parent_task_id") or ""),
            "thread_id": str(current.get("thread_id") or ""),
            "conversation_key": str(key),
        }
        repo = handoff["repo"] or "aucun repo actif"
        mode = handoff["mode"] or "mode inconnu"
        task = handoff["task"] or "aucune tâche résumée"
        handoff["summary"] = f"Soft-close {repo} ({mode}) — reprise: {task}"
        handoff["resume_hints"] = {
            "repo": handoff["repo"],
            "mode": handoff["mode"],
            "task": handoff["task"],
            "task_id": handoff["task_id"],
            "parent_task_id": handoff["parent_task_id"],
            "thread_id": handoff["thread_id"],
        }
        self.cache_handoff(key, handoff)
        self.set_active(key, mode="libre", last_handoff_id=handoff_id)
        return handoff

    def cache_handoff(self, key: str, handoff: dict[str, Any]) -> dict[str, Any]:
        clean = dict(handoff or {})
        clean.setdefault("id", "handoff_" + hashlib.sha1(f"{key}:{time.time_ns()}".encode()).hexdigest()[:14])
        clean.setdefault("conversation_key", str(key))
        clean.setdefault("created_at", int(time.time()))
        resume_hints = clean.get("resume_hints") if isinstance(clean.get("resume_hints"), dict) else {}
        payload = clean.get("payload") if isinstance(clean.get("payload"), dict) else {}
        with self._connect() as con:
            con.execute(
                """insert or replace into handoff_cache(
                       id,key,task_id,parent_task_id,repo,mode,task,thread_id,reason,summary,
                       resume_hints_json,created_at,consumed_at,source,payload_json
                   ) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    clean["id"],
                    str(key),
                    str(clean.get("task_id") or ""),
                    str(clean.get("parent_task_id") or ""),
                    str(clean.get("repo") or ""),
                    str(clean.get("mode") or ""),
                    str(clean.get("task") or clean.get("summary") or ""),
                    str(clean.get("thread_id") or ""),
                    str(clean.get("reason") or "handoff"),
                    str(clean.get("summary") or ""),
                    json.dumps(resume_hints, ensure_ascii=False, sort_keys=True),
                    int(clean.get("created_at") or time.time()),
                    clean.get("consumed_at"),
                    str(clean.get("source") or ""),
                    json.dumps(payload, ensure_ascii=False, sort_keys=True),
                ),
            )
            con.commit()
        return self.latest_handoff(key, include_consumed=True) or clean

    def latest_handoff(self, key: str, *, include_consumed: bool = False) -> dict[str, Any] | None:
        where = "key=?"
        params: list[Any] = [str(key)]
        if not include_consumed:
            where += " and consumed_at is null"
        with self._connect() as con:
            row = con.execute(f"select * from handoff_cache where {where} order by created_at desc limit 1", params).fetchone()
        return self._handoff_dict(row) if row else None

    def mark_consumed(self, key: str, handoff_id: str | None = None) -> dict[str, Any] | None:
        handoff = self.latest_handoff(key)
        if not handoff:
            return None
        target_id = handoff_id or str(handoff["id"])
        with self._connect() as con:
            con.execute("update handoff_cache set consumed_at=? where id=? and key=?", (int(time.time()), target_id, str(key)))
            con.commit()
        return self.latest_handoff(key, include_consumed=True)

    def remember_policy(self, key: str, policy: dict[str, str], *, source: str = "telegram") -> dict[str, str]:
        now = int(time.time())
        stored = dict(policy)
        stored.update({"key": str(key), "source": source, "created_at": str(now)})
        with self._connect() as con:
            con.execute(
                "insert into policies(key,scope,model,reasoning_effort,source,created_at,payload_json) values(?,?,?,?,?,?,?)",
                (
                    str(key),
                    stored.get("scope", ""),
                    stored.get("model", ""),
                    stored.get("reasoning_effort", ""),
                    source,
                    now,
                    json.dumps(stored, ensure_ascii=False, sort_keys=True),
                ),
            )
            con.commit()
        return stored

    def migrate_json(self, path: str | Path) -> None:
        legacy = Path(path)
        if not legacy.exists():
            return
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        for key, ctx in (data.get("contexts") or {}).items():
            if isinstance(ctx, dict):
                self.set_active(str(key), **ctx)
        for handoff in data.get("handoffs") or []:
            if not isinstance(handoff, dict):
                continue
            key = str(handoff.get("conversation_key") or handoff.get("key") or "legacy")
            self.cache_handoff(key, handoff)
        for policy in data.get("policies") or []:
            if isinstance(policy, dict):
                self.remember_policy(str(policy.get("key") or "legacy"), {k: str(v) for k, v in policy.items()}, source=str(policy.get("source") or "legacy_json"))

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        return con

    def _ensure_schema(self, con: sqlite3.Connection) -> None:
        con.executescript(
            """
            create table if not exists active_contexts (
                key text primary key,
                repo text,
                mode text,
                task text,
                task_id text,
                parent_task_id text,
                thread_id text,
                last_handoff_id text,
                updated_at integer not null,
                payload_json text
            );
            create table if not exists handoff_cache (
                id text primary key,
                key text not null,
                task_id text,
                parent_task_id text,
                repo text,
                mode text,
                task text,
                thread_id text,
                reason text,
                summary text,
                resume_hints_json text,
                created_at integer not null,
                consumed_at integer,
                source text,
                payload_json text
            );
            create index if not exists idx_handoff_cache_key_created on handoff_cache(key, created_at desc);
            create table if not exists policies (
                id integer primary key autoincrement,
                key text not null,
                scope text,
                model text,
                reasoning_effort text,
                source text,
                created_at integer not null,
                payload_json text
            );
            """
        )
        con.commit()

    def _context_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        try:
            payload = json.loads(data.pop("payload_json") or "{}")
        except Exception:
            payload = {}
        return {**payload, **{k: v for k, v in data.items() if v not in (None, "")}}

    def _handoff_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        try:
            data["resume_hints"] = json.loads(data.get("resume_hints_json") or "{}")
        except Exception:
            data["resume_hints"] = {}
        try:
            data["payload"] = json.loads(data.get("payload_json") or "{}")
        except Exception:
            data["payload"] = {}
        data["conversation_key"] = data.get("key")
        return data
