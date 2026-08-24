from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .accounts import AccountStore


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class AuraChatStore:
    """Durable private state for Aura's ChatGPT-style workspace.

    This extends the original thread/message tables without breaking existing conversations.
    Memory is explicit/user-approved; attachments, tool calls and thread context are isolated
    by user_id and never shared between members.
    """

    def __init__(self, account_store: AccountStore | None = None):
        self.accounts = account_store or AccountStore()
        self.db_path = self.accounts.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path, timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                CREATE TABLE IF NOT EXISTS aura_chat_threads (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS aura_chat_messages (
                    id TEXT PRIMARY KEY,
                    thread_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(thread_id) REFERENCES aura_chat_threads(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_aura_threads_user_updated
                    ON aura_chat_threads(user_id, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_aura_messages_thread_created
                    ON aura_chat_messages(thread_id, created_at ASC);

                CREATE TABLE IF NOT EXISTS aura_chat_thread_context (
                    thread_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    project_name TEXT,
                    web_enabled INTEGER NOT NULL DEFAULT 1,
                    tools_enabled INTEGER NOT NULL DEFAULT 1,
                    voice_reply INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(thread_id) REFERENCES aura_chat_threads(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS aura_chat_summaries (
                    thread_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    through_message_id TEXT,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(thread_id) REFERENCES aura_chat_threads(id) ON DELETE CASCADE,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS aura_memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    label TEXT NOT NULL,
                    content TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_aura_memories_user ON aura_memories(user_id, enabled, updated_at DESC);

                CREATE TABLE IF NOT EXISTS aura_chat_attachments (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    message_id TEXT,
                    name TEXT NOT NULL,
                    stored_path TEXT NOT NULL,
                    mime_type TEXT,
                    kind TEXT NOT NULL,
                    bytes INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    extracted_text TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(thread_id) REFERENCES aura_chat_threads(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_aura_attach_thread ON aura_chat_attachments(user_id, thread_id, created_at);

                CREATE TABLE IF NOT EXISTS aura_chat_tool_runs (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    message_id TEXT,
                    tool_name TEXT NOT NULL,
                    arguments_json TEXT,
                    result_json TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
                    FOREIGN KEY(thread_id) REFERENCES aura_chat_threads(id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_aura_tool_runs_thread ON aura_chat_tool_runs(user_id, thread_id, created_at);
                """
            )

    def _thread_owned(self, con, user_id: str, thread_id: str) -> bool:
        return bool(con.execute("SELECT 1 FROM aura_chat_threads WHERE id=? AND user_id=?", (thread_id, user_id)).fetchone())

    def create_thread(self, user_id: str, title: str = "New conversation") -> dict:
        thread_id = uuid4().hex
        now = _now()
        clean = " ".join((title or "New conversation").split())[:180] or "New conversation"
        with self._connect() as con:
            con.execute(
                "INSERT INTO aura_chat_threads(id,user_id,title,created_at,updated_at) VALUES (?,?,?,?,?)",
                (thread_id, user_id, clean, now, now),
            )
            con.execute(
                "INSERT INTO aura_chat_thread_context(thread_id,user_id,updated_at) VALUES (?,?,?)",
                (thread_id, user_id, now),
            )
        return self.thread(user_id, thread_id) or {}

    def thread(self, user_id: str, thread_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT t.*,c.project_name,c.web_enabled,c.tools_enabled,c.voice_reply
                   FROM aura_chat_threads t LEFT JOIN aura_chat_thread_context c ON c.thread_id=t.id
                   WHERE t.id=? AND t.user_id=?""",
                (thread_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def list_threads(self, user_id: str, limit: int = 100, query: str = "") -> list[dict]:
        query = " ".join((query or "").split())[:200]
        params: list[object] = [user_id]
        where = "t.user_id=?"
        if query:
            where += " AND (t.title LIKE ? OR EXISTS (SELECT 1 FROM aura_chat_messages mx WHERE mx.thread_id=t.id AND mx.content LIKE ?))"
            needle = f"%{query}%"
            params += [needle, needle]
        params.append(max(1, min(int(limit), 200)))
        with self._connect() as con:
            rows = con.execute(
                f"""SELECT t.*,c.project_name,COUNT(m.id) AS message_count
                    FROM aura_chat_threads t
                    LEFT JOIN aura_chat_thread_context c ON c.thread_id=t.id
                    LEFT JOIN aura_chat_messages m ON m.thread_id=t.id
                    WHERE {where}
                    GROUP BY t.id ORDER BY t.updated_at DESC LIMIT ?""",
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def rename_thread(self, user_id: str, thread_id: str, title: str) -> dict:
        clean = " ".join((title or "").split())[:180]
        if not clean:
            raise ValueError("Conversation title cannot be empty")
        with self._connect() as con:
            cur = con.execute(
                "UPDATE aura_chat_threads SET title=?,updated_at=? WHERE id=? AND user_id=?",
                (clean, _now(), thread_id, user_id),
            )
            if cur.rowcount != 1:
                raise KeyError(thread_id)
        return self.thread(user_id, thread_id) or {}

    def delete_thread(self, user_id: str, thread_id: str) -> None:
        with self._connect() as con:
            cur = con.execute("DELETE FROM aura_chat_threads WHERE id=? AND user_id=?", (thread_id, user_id))
            if cur.rowcount != 1:
                raise KeyError(thread_id)

    def set_context(
        self,
        user_id: str,
        thread_id: str,
        *,
        project_name: str | None = None,
        web_enabled: bool | None = None,
        tools_enabled: bool | None = None,
        voice_reply: bool | None = None,
    ) -> dict:
        if not self.thread(user_id, thread_id):
            raise KeyError(thread_id)
        current = self.thread(user_id, thread_id) or {}
        values = {
            "project_name": project_name if project_name is not None else current.get("project_name"),
            "web_enabled": int(web_enabled if web_enabled is not None else bool(current.get("web_enabled", 1))),
            "tools_enabled": int(tools_enabled if tools_enabled is not None else bool(current.get("tools_enabled", 1))),
            "voice_reply": int(voice_reply if voice_reply is not None else bool(current.get("voice_reply", 0))),
        }
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_chat_thread_context(thread_id,user_id,project_name,web_enabled,tools_enabled,voice_reply,updated_at)
                   VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(thread_id) DO UPDATE SET project_name=excluded.project_name,
                   web_enabled=excluded.web_enabled,tools_enabled=excluded.tools_enabled,
                   voice_reply=excluded.voice_reply,updated_at=excluded.updated_at""",
                (thread_id, user_id, values["project_name"], values["web_enabled"], values["tools_enabled"], values["voice_reply"], _now()),
            )
        return self.thread(user_id, thread_id) or {}

    def messages(self, user_id: str, thread_id: str, limit: int = 120) -> list[dict]:
        with self._connect() as con:
            if not self._thread_owned(con, user_id, thread_id):
                raise KeyError(thread_id)
            rows = con.execute(
                """SELECT * FROM (
                       SELECT id,role,content,created_at FROM aura_chat_messages
                       WHERE thread_id=? ORDER BY created_at DESC LIMIT ?
                   ) q ORDER BY created_at ASC""",
                (thread_id, max(1, min(int(limit), 400))),
            ).fetchall()
        return [dict(row) for row in rows]

    def message(self, user_id: str, thread_id: str, message_id: str) -> dict | None:
        with self._connect() as con:
            if not self._thread_owned(con, user_id, thread_id):
                return None
            row = con.execute(
                "SELECT id,role,content,created_at FROM aura_chat_messages WHERE id=? AND thread_id=?",
                (message_id, thread_id),
            ).fetchone()
        return dict(row) if row else None

    def add_message(self, user_id: str, thread_id: str, role: str, content: str) -> dict:
        if role not in {"user", "assistant"}:
            raise ValueError("Unsupported message role")
        clean = (content or "").strip()
        if not clean:
            raise ValueError("Message cannot be empty")
        message_id = uuid4().hex
        now = _now()
        with self._connect() as con:
            if not self._thread_owned(con, user_id, thread_id):
                raise KeyError(thread_id)
            con.execute(
                "INSERT INTO aura_chat_messages(id,thread_id,role,content,created_at) VALUES (?,?,?,?,?)",
                (message_id, thread_id, role, clean, now),
            )
            con.execute("UPDATE aura_chat_threads SET updated_at=? WHERE id=?", (now, thread_id))
            if role == "user":
                count = int(con.execute("SELECT COUNT(*) n FROM aura_chat_messages WHERE thread_id=?", (thread_id,)).fetchone()["n"])
                if count == 1:
                    title = " ".join(clean.split())[:90] or "New conversation"
                    con.execute("UPDATE aura_chat_threads SET title=? WHERE id=?", (title, thread_id))
        return {"id": message_id, "role": role, "content": clean, "created_at": now}

    def edit_user_message(self, user_id: str, thread_id: str, message_id: str, content: str) -> dict:
        clean = (content or "").strip()
        if not clean:
            raise ValueError("Message cannot be empty")
        with self._connect() as con:
            if not self._thread_owned(con, user_id, thread_id):
                raise KeyError(thread_id)
            row = con.execute(
                "SELECT created_at,role FROM aura_chat_messages WHERE id=? AND thread_id=?",
                (message_id, thread_id),
            ).fetchone()
            if not row or row["role"] != "user":
                raise KeyError(message_id)
            con.execute("UPDATE aura_chat_messages SET content=? WHERE id=?", (clean, message_id))
            con.execute(
                "DELETE FROM aura_chat_messages WHERE thread_id=? AND created_at>?",
                (thread_id, row["created_at"]),
            )
            con.execute("UPDATE aura_chat_threads SET updated_at=? WHERE id=?", (_now(), thread_id))
        return self.message(user_id, thread_id, message_id) or {}

    def delete_last_assistant(self, user_id: str, thread_id: str) -> bool:
        with self._connect() as con:
            if not self._thread_owned(con, user_id, thread_id):
                raise KeyError(thread_id)
            row = con.execute(
                "SELECT id FROM aura_chat_messages WHERE thread_id=? AND role='assistant' ORDER BY created_at DESC LIMIT 1",
                (thread_id,),
            ).fetchone()
            if not row:
                return False
            con.execute("DELETE FROM aura_chat_messages WHERE id=?", (row["id"],))
            con.execute("UPDATE aura_chat_threads SET updated_at=? WHERE id=?", (_now(), thread_id))
        return True

    def fork_thread(self, user_id: str, thread_id: str, through_message_id: str) -> dict:
        rows = self.messages(user_id, thread_id, limit=400)
        selected: list[dict] = []
        found = False
        for row in rows:
            selected.append(row)
            if row["id"] == through_message_id:
                found = True
                break
        if not found:
            raise KeyError(through_message_id)
        source = self.thread(user_id, thread_id) or {}
        new_thread = self.create_thread(user_id, f"{source.get('title') or 'Conversation'} — branch")
        self.set_context(
            user_id,
            new_thread["id"],
            project_name=source.get("project_name"),
            web_enabled=bool(source.get("web_enabled", 1)),
            tools_enabled=bool(source.get("tools_enabled", 1)),
            voice_reply=bool(source.get("voice_reply", 0)),
        )
        for row in selected:
            self.add_message(user_id, new_thread["id"], row["role"], row["content"])
        return self.thread(user_id, new_thread["id"]) or new_thread

    def set_summary(self, user_id: str, thread_id: str, summary: str, through_message_id: str | None = None) -> None:
        if not self.thread(user_id, thread_id):
            raise KeyError(thread_id)
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_chat_summaries(thread_id,user_id,summary,through_message_id,updated_at)
                   VALUES (?,?,?,?,?) ON CONFLICT(thread_id) DO UPDATE SET summary=excluded.summary,
                   through_message_id=excluded.through_message_id,updated_at=excluded.updated_at""",
                (thread_id, user_id, summary[:20000], through_message_id, _now()),
            )

    def summary(self, user_id: str, thread_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT summary,through_message_id,updated_at FROM aura_chat_summaries WHERE thread_id=? AND user_id=?",
                (thread_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def add_memory(self, user_id: str, label: str, content: str) -> dict:
        clean = (content or "").strip()
        if not clean:
            raise ValueError("Memory cannot be empty")
        memory_id = uuid4().hex
        now = _now()
        with self._connect() as con:
            con.execute(
                "INSERT INTO aura_memories(id,user_id,label,content,enabled,created_at,updated_at) VALUES (?,?,?,?,1,?,?)",
                (memory_id, user_id, (label or "Memory").strip()[:120], clean[:5000], now, now),
            )
        return {"id": memory_id, "label": (label or "Memory").strip()[:120], "content": clean[:5000], "enabled": True, "created_at": now, "updated_at": now}

    def memories(self, user_id: str, enabled_only: bool = True, limit: int = 80) -> list[dict]:
        where = "user_id=?" + (" AND enabled=1" if enabled_only else "")
        with self._connect() as con:
            rows = con.execute(
                f"SELECT id,label,content,enabled,created_at,updated_at FROM aura_memories WHERE {where} ORDER BY updated_at DESC LIMIT ?",
                (user_id, max(1, min(int(limit), 200))),
            ).fetchall()
        return [{**dict(row), "enabled": bool(row["enabled"])} for row in rows]

    def delete_memory(self, user_id: str, memory_id: str) -> bool:
        with self._connect() as con:
            cur = con.execute("DELETE FROM aura_memories WHERE id=? AND user_id=?", (memory_id, user_id))
        return cur.rowcount == 1

    def add_attachment(
        self,
        user_id: str,
        thread_id: str,
        *,
        name: str,
        stored_path: str,
        mime_type: str | None,
        kind: str,
        bytes_count: int,
        sha256: str,
        extracted_text: str | None = None,
        metadata: dict | None = None,
    ) -> dict:
        if not self.thread(user_id, thread_id):
            raise KeyError(thread_id)
        attachment_id = uuid4().hex
        now = _now()
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_chat_attachments
                   (id,user_id,thread_id,name,stored_path,mime_type,kind,bytes,sha256,extracted_text,metadata_json,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    attachment_id, user_id, thread_id, name[:240], stored_path, mime_type, kind,
                    int(bytes_count), sha256, extracted_text, json.dumps(metadata or {}, ensure_ascii=False), now,
                ),
            )
        return self.attachment(user_id, thread_id, attachment_id) or {}

    def attachment(self, user_id: str, thread_id: str, attachment_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                """SELECT id,name,stored_path,mime_type,kind,bytes,sha256,extracted_text,metadata_json,created_at,message_id
                   FROM aura_chat_attachments WHERE id=? AND user_id=? AND thread_id=?""",
                (attachment_id, user_id, thread_id),
            ).fetchone()
        if not row:
            return None
        value = dict(row)
        try:
            value["metadata"] = json.loads(value.pop("metadata_json") or "{}")
        except Exception:
            value["metadata"] = {}
            value.pop("metadata_json", None)
        return value

    def attachments(self, user_id: str, thread_id: str, attachment_ids: list[str] | None = None) -> list[dict]:
        if not self.thread(user_id, thread_id):
            raise KeyError(thread_id)
        with self._connect() as con:
            if attachment_ids:
                clean = [x for x in attachment_ids if x]
                placeholders = ",".join("?" for _ in clean)
                rows = con.execute(
                    f"""SELECT id FROM aura_chat_attachments WHERE user_id=? AND thread_id=? AND id IN ({placeholders}) ORDER BY created_at""",
                    (user_id, thread_id, *clean),
                ).fetchall()
            else:
                rows = con.execute(
                    "SELECT id FROM aura_chat_attachments WHERE user_id=? AND thread_id=? ORDER BY created_at",
                    (user_id, thread_id),
                ).fetchall()
        return [self.attachment(user_id, thread_id, row["id"]) for row in rows if self.attachment(user_id, thread_id, row["id"])]

    def bind_attachments(self, user_id: str, thread_id: str, message_id: str, attachment_ids: list[str]) -> None:
        if not attachment_ids:
            return
        clean = [x for x in attachment_ids if x]
        placeholders = ",".join("?" for _ in clean)
        with self._connect() as con:
            if not self._thread_owned(con, user_id, thread_id):
                raise KeyError(thread_id)
            con.execute(
                f"UPDATE aura_chat_attachments SET message_id=? WHERE user_id=? AND thread_id=? AND id IN ({placeholders})",
                (message_id, user_id, thread_id, *clean),
            )

    def message_attachments(self, user_id: str, thread_id: str, message_id: str) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                "SELECT id FROM aura_chat_attachments WHERE user_id=? AND thread_id=? AND message_id=? ORDER BY created_at",
                (user_id, thread_id, message_id),
            ).fetchall()
        return [self.attachment(user_id, thread_id, row["id"]) for row in rows if self.attachment(user_id, thread_id, row["id"])]

    def start_tool_run(self, user_id: str, thread_id: str, message_id: str | None, tool_name: str, arguments: dict) -> str:
        run_id = uuid4().hex
        with self._connect() as con:
            con.execute(
                """INSERT INTO aura_chat_tool_runs(id,user_id,thread_id,message_id,tool_name,arguments_json,status,created_at)
                   VALUES (?,?,?,?,?,?, 'running', ?)""",
                (run_id, user_id, thread_id, message_id, tool_name, json.dumps(arguments, ensure_ascii=False, default=str), _now()),
            )
        return run_id

    def finish_tool_run(self, run_id: str, *, result: object | None = None, error: str | None = None) -> None:
        status = "failed" if error else "completed"
        payload = json.dumps(result, ensure_ascii=False, default=str) if result is not None else None
        with self._connect() as con:
            con.execute(
                "UPDATE aura_chat_tool_runs SET status=?,result_json=?,completed_at=? WHERE id=?",
                (status, json.dumps({"error": error}) if error else payload, _now(), run_id),
            )

    def tool_runs(self, user_id: str, thread_id: str, limit: int = 50) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT id,message_id,tool_name,arguments_json,result_json,status,created_at,completed_at
                   FROM aura_chat_tool_runs WHERE user_id=? AND thread_id=? ORDER BY created_at DESC LIMIT ?""",
                (user_id, thread_id, max(1, min(int(limit), 200))),
            ).fetchall()
        result = []
        for row in rows:
            value = dict(row)
            for key in ("arguments_json", "result_json"):
                try:
                    value[key[:-5]] = json.loads(value.pop(key) or "null")
                except Exception:
                    value[key[:-5]] = None
                    value.pop(key, None)
            result.append(value)
        return result


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
