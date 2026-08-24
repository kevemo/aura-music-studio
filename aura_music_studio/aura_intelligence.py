from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from html import escape
from uuid import uuid4

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .accounts import AccountStore
from .branding import ENDORSEMENT, PRODUCT_FULL_NAME, PRODUCT_NAME

router = APIRouter()
accounts = AccountStore()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Active membership required")
    return member


SYSTEM_PROMPT = f"""You are Aura Intelligence inside {PRODUCT_FULL_NAME}, {ENDORSEMENT}.
You are a capable general conversational assistant for research, reasoning, writing, planning, learning and creative work.
Be accurate, clear and practical. Distinguish facts from assumptions. Do not claim to have performed actions, searches,
file reads or external operations that you have not actually performed. Never reveal deployment secrets, other members'
data, ESP private Creator/Agent material or owner-only information. ESP Creator Network operations live in a separate
permission-gated hub and are not available merely because a user asks for them here. For creative requests, keep outputs
professional and do not assist with targeted harassment, hateful/dehumanising campaigns, doxxing or violent propaganda.
When the user asks to modify an existing project, explain what context or project reference is needed rather than pretending
it is already loaded. Keep answers useful and conversational."""


class ThreadCreateRequest(BaseModel):
    title: str = Field(default="New conversation", min_length=1, max_length=180)


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=30000)


class AuraIntelligenceStore:
    def __init__(self, account_store: AccountStore | None = None):
        self.accounts = account_store or accounts
        self.db_path = self.accounts.db_path
        self._init_schema()

    def _connect(self):
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def _init_schema(self):
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
                """
            )

    def create_thread(self, user_id: str, title: str) -> dict:
        thread_id = uuid4().hex
        now = _now()
        clean = (title or "New conversation").strip()[:180] or "New conversation"
        with self._connect() as con:
            con.execute(
                "INSERT INTO aura_chat_threads(id,user_id,title,created_at,updated_at) VALUES (?,?,?,?,?)",
                (thread_id, user_id, clean, now, now),
            )
        return self.thread(user_id, thread_id) or {}

    def list_threads(self, user_id: str, limit: int = 100) -> list[dict]:
        with self._connect() as con:
            rows = con.execute(
                """SELECT t.*,COUNT(m.id) AS message_count
                   FROM aura_chat_threads t LEFT JOIN aura_chat_messages m ON m.thread_id=t.id
                   WHERE t.user_id=? GROUP BY t.id ORDER BY t.updated_at DESC LIMIT ?""",
                (user_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def thread(self, user_id: str, thread_id: str) -> dict | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT * FROM aura_chat_threads WHERE id=? AND user_id=?",
                (thread_id, user_id),
            ).fetchone()
        return dict(row) if row else None

    def messages(self, user_id: str, thread_id: str, limit: int = 80) -> list[dict]:
        if not self.thread(user_id, thread_id):
            raise KeyError(thread_id)
        with self._connect() as con:
            rows = con.execute(
                """SELECT id,role,content,created_at FROM aura_chat_messages
                   WHERE thread_id=? ORDER BY created_at ASC LIMIT ?""",
                (thread_id, max(1, min(limit, 200))),
            ).fetchall()
        return [dict(row) for row in rows]

    def add_message(self, user_id: str, thread_id: str, role: str, content: str) -> dict:
        if role not in {"user", "assistant"}:
            raise ValueError("Unsupported message role")
        if not self.thread(user_id, thread_id):
            raise KeyError(thread_id)
        message_id = uuid4().hex
        now = _now()
        with self._connect() as con:
            con.execute(
                "INSERT INTO aura_chat_messages(id,thread_id,role,content,created_at) VALUES (?,?,?,?,?)",
                (message_id, thread_id, role, content, now),
            )
            con.execute("UPDATE aura_chat_threads SET updated_at=? WHERE id=?", (now, thread_id))
            if role == "user":
                count = con.execute(
                    "SELECT COUNT(*) AS n FROM aura_chat_messages WHERE thread_id=?",
                    (thread_id,),
                ).fetchone()["n"]
                if count == 1:
                    title = " ".join(content.strip().split())[:90] or "New conversation"
                    con.execute("UPDATE aura_chat_threads SET title=? WHERE id=?", (title, thread_id))
        return {"id": message_id, "role": role, "content": content, "created_at": now}

    def delete_thread(self, user_id: str, thread_id: str) -> None:
        with self._connect() as con:
            row = con.execute(
                "SELECT id FROM aura_chat_threads WHERE id=? AND user_id=?",
                (thread_id, user_id),
            ).fetchone()
            if not row:
                raise KeyError(thread_id)
            con.execute("DELETE FROM aura_chat_threads WHERE id=?", (thread_id,))


store = AuraIntelligenceStore(accounts)


def _ollama_chat(messages: list[dict]) -> str:
    base = (os.getenv("OLLAMA_BASE_URL") or "http://127.0.0.1:11434").rstrip("/")
    model = os.getenv("AURA_INTELLIGENCE_MODEL") or os.getenv("AURA_OLLAMA_MODEL") or "qwen3:4b"
    response = requests.post(
        f"{base}/api/chat",
        json={
            "model": model,
            "stream": False,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "options": {"temperature": 0.55},
        },
        timeout=max(30, int(os.getenv("AURA_INTELLIGENCE_TIMEOUT", "180"))),
    )
    response.raise_for_status()
    value = response.json()
    content = ((value.get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("Aura Intelligence returned an empty response")
    return content


def _openai_compatible_chat(messages: list[dict]) -> str:
    base = (os.getenv("AURA_LLM_BASE_URL") or "").strip().rstrip("/")
    if not base:
        raise RuntimeError("No OpenAI-compatible local endpoint configured")
    model = os.getenv("AURA_INTELLIGENCE_MODEL") or os.getenv("AURA_LLM_MODEL") or "local-model"
    headers = {"Content-Type": "application/json"}
    key = (os.getenv("AURA_LLM_API_KEY") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    response = requests.post(
        f"{base}/chat/completions",
        headers=headers,
        json={
            "model": model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "temperature": 0.55,
        },
        timeout=max(30, int(os.getenv("AURA_INTELLIGENCE_TIMEOUT", "180"))),
    )
    response.raise_for_status()
    value = response.json()
    choices = value.get("choices") or []
    content = (((choices[0] if choices else {}).get("message") or {}).get("content") or "").strip()
    if not content:
        raise RuntimeError("Aura Intelligence returned an empty response")
    return content


def generate_reply(messages: list[dict]) -> str:
    provider = (os.getenv("AURA_INTELLIGENCE_PROVIDER") or "auto").strip().lower()
    errors: list[str] = []
    if provider in {"auto", "ollama"}:
        try:
            return _ollama_chat(messages)
        except Exception as exc:
            errors.append(f"Ollama: {type(exc).__name__}")
            if provider == "ollama":
                raise RuntimeError(errors[-1]) from exc
    if provider in {"auto", "openai_compatible", "local"} and os.getenv("AURA_LLM_BASE_URL"):
        try:
            return _openai_compatible_chat(messages)
        except Exception as exc:
            errors.append(f"OpenAI-compatible endpoint: {type(exc).__name__}")
            if provider != "auto":
                raise RuntimeError(errors[-1]) from exc
    raise RuntimeError(
        "Aura Intelligence has no reachable language-model engine on this deployment. "
        + ("; ".join(errors) if errors else "Configure Ollama or AURA_LLM_BASE_URL.")
    )


@router.get("/aura-intelligence/api/threads")
def list_threads(request: Request):
    member = _member(request)
    return {"threads": store.list_threads(member.user_id)}


@router.post("/aura-intelligence/api/threads")
def create_thread(body: ThreadCreateRequest, request: Request):
    member = _member(request)
    return {"thread": store.create_thread(member.user_id, body.title)}


@router.get("/aura-intelligence/api/threads/{thread_id}")
def get_thread(thread_id: str, request: Request):
    member = _member(request)
    thread = store.thread(member.user_id, thread_id)
    if not thread:
        raise HTTPException(404, "Conversation not found")
    return {"thread": thread, "messages": store.messages(member.user_id, thread_id)}


@router.delete("/aura-intelligence/api/threads/{thread_id}")
def delete_thread(thread_id: str, request: Request):
    member = _member(request)
    try:
        store.delete_thread(member.user_id, thread_id)
    except KeyError as exc:
        raise HTTPException(404, "Conversation not found") from exc
    return {"deleted": True}


@router.post("/aura-intelligence/api/threads/{thread_id}/messages")
def send_message(thread_id: str, body: MessageRequest, request: Request):
    member = _member(request)
    try:
        user_message = store.add_message(member.user_id, thread_id, "user", body.message.strip())
        history = store.messages(member.user_id, thread_id, limit=60)
    except KeyError as exc:
        raise HTTPException(404, "Conversation not found") from exc
    llm_messages = [
        {"role": row["role"], "content": row["content"]}
        for row in history
        if row["role"] in {"user", "assistant"}
    ]
    try:
        reply = generate_reply(llm_messages)
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc
    assistant_message = store.add_message(member.user_id, thread_id, "assistant", reply)
    try:
        accounts.record_usage(
            member.user_id,
            "aura_intelligence_message",
            metadata_json=json.dumps({"thread_id": thread_id}, ensure_ascii=False),
        )
    except Exception:
        pass
    return {"user": user_message, "assistant": assistant_message}


@router.get("/aura-intelligence", response_class=HTMLResponse, include_in_schema=False)
def aura_intelligence_page(request: Request):
    member = _member(request)
    html = f"""<!doctype html><html lang='en'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'><meta name='robots' content='noindex,nofollow'><title>Aura Intelligence — {escape(PRODUCT_NAME)}</title><style>
:root{{--bg:#04050b;--panel:#101322;--panel2:#080a12;--line:#ffffff1d;--gold:#f4c873;--violet:#a66bff;--cyan:#5de7ff;--text:#fff;--muted:#bcc1d4;--bad:#ff92a4}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 10% 0,#33134d,transparent 30%),radial-gradient(circle at 95% 4%,#10384e,transparent 28%),#04050b;color:#fff;font-family:Inter,ui-sans-serif,system-ui,sans-serif;height:100vh;overflow:hidden}}button,input,textarea{{font:inherit}}.app{{display:grid;grid-template-columns:290px 1fr;height:100vh}}.side{{border-right:1px solid var(--line);background:#080912d9;padding:16px;overflow:auto}}.brand{{font-weight:950;margin:5px 0 18px}}.brand small{{display:block;color:var(--gold);font-size:.66rem;letter-spacing:.08em;text-transform:uppercase}}.btn{{width:100%;border:1px solid var(--line);border-radius:11px;padding:10px 12px;background:#ffffff08;color:#fff;font-weight:850;cursor:pointer;text-align:left}}.btn.primary{{border:0;background:linear-gradient(110deg,#fff0b0,var(--gold),#c7a1ff);color:#160b1d;text-align:center}}.threads{{display:grid;gap:7px;margin-top:13px}}.thread{{border:1px solid transparent;border-radius:10px;padding:9px;background:#ffffff05;cursor:pointer;color:#dcdcea}}.thread.active{{border-color:#a66bff66;background:#a66bff12}}.thread small{{display:block;color:var(--muted);margin-top:3px}}.main{{display:grid;grid-template-rows:auto 1fr auto;min-width:0}}.top{{border-bottom:1px solid var(--line);padding:14px 18px;display:flex;justify-content:space-between;gap:12px;align-items:center}}.top a{{color:#fff;text-decoration:none}}.messages{{overflow:auto;padding:24px max(18px,calc((100% - 900px)/2));scroll-behavior:smooth}}.empty{{max-width:760px;margin:11vh auto;text-align:center}}.empty h1{{font-size:clamp(2.7rem,7vw,5rem);letter-spacing:-.06em;margin:.15em 0;background:linear-gradient(100deg,#fff,var(--gold),var(--cyan));background-clip:text;color:transparent}}.empty p{{color:var(--muted);line-height:1.6}}.msg{{max-width:860px;margin:11px auto;padding:14px 16px;border-radius:17px;line-height:1.58;white-space:pre-wrap;word-wrap:break-word}}.msg.user{{background:#27203a;margin-left:auto}}.msg.assistant{{background:linear-gradient(135deg,#121628,#0d1820);border:1px solid var(--line)}}.msg b{{display:block;color:var(--gold);font-size:.72rem;letter-spacing:.08em;margin-bottom:5px}}.composer{{border-top:1px solid var(--line);padding:14px max(18px,calc((100% - 900px)/2));background:#070810e8}}.compose{{display:grid;grid-template-columns:1fr auto;gap:9px;align-items:end;border:1px solid var(--line);border-radius:17px;padding:8px;background:#0c0e18}}textarea{{width:100%;min-height:48px;max-height:180px;resize:none;border:0;outline:none;background:transparent;color:#fff;padding:10px}}.send{{border:0;border-radius:12px;padding:11px 16px;background:linear-gradient(110deg,var(--gold),#b993ff);color:#170c1d;font-weight:900;cursor:pointer}}.status{{font-size:.78rem;color:var(--muted);margin-top:7px}}@media(max-width:760px){{.app{{grid-template-columns:1fr}}.side{{display:none}}.messages{{padding-left:14px;padding-right:14px}}.composer{{padding-left:10px;padding-right:10px}}}}
</style></head><body><div class='app'><aside class='side'><div class='brand'>✨ Aura Intelligence<small>{escape(PRODUCT_FULL_NAME)}</small></div><button class='btn primary' onclick='newThread()'>+ New conversation</button><div id='threads' class='threads'></div></aside><section class='main'><header class='top'><div><b>Aura Intelligence</b><div style='font-size:.75rem;color:var(--muted)'>Private conversation · {escape(member.plan.name)}</div></div><a href='/dashboard'>← Dashboard</a></header><main id='messages' class='messages'><div class='empty'><div style='font-size:2rem'>✨</div><h1>How can Aura help?</h1><p>Research, reason, write, plan, learn and think through ideas in a persistent private conversation. Creative Studio and ESP agency permissions remain separate.</p></div></main><footer class='composer'><div class='compose'><textarea id='input' placeholder='Message Aura…' onkeydown='keySend(event)'></textarea><button id='send' class='send' onclick='sendMessage()'>Send</button></div><div id='status' class='status'>Aura uses the language-model engine configured on this Pulsar-Frequency House deployment.</div></footer></section></div><script>
let active=null;const q=id=>document.getElementById(id);function esc(v){{return String(v??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]))}}async function api(path,opts={{}}){{const r=await fetch(path,{{credentials:'same-origin',headers:{{'Content-Type':'application/json'}},...opts}});let b={{}};try{{b=await r.json()}}catch(e){{}}if(!r.ok)throw new Error(b.detail||`Request failed (${{r.status}})`);return b}}async function loadThreads(){{const d=await api('/aura-intelligence/api/threads');q('threads').innerHTML=(d.threads||[]).map(t=>`<div class="thread ${{t.id===active?'active':''}}" onclick="openThread('${{t.id}}')"><strong>${{esc(t.title)}}</strong><small>${{t.message_count}} messages</small></div>`).join('')}}async function newThread(){{const d=await api('/aura-intelligence/api/threads',{{method:'POST',body:JSON.stringify({{title:'New conversation'}})}});active=d.thread.id;await loadThreads();await openThread(active)}}async function openThread(id){{active=id;const d=await api(`/aura-intelligence/api/threads/${{id}}`);render(d.messages||[]);await loadThreads()}}function render(rows){{if(!rows.length){{q('messages').innerHTML='<div class="empty"><div style="font-size:2rem">✨</div><h1>Start a conversation.</h1><p>Aura Intelligence keeps this thread private to your member account.</p></div>';return}}q('messages').innerHTML=rows.map(m=>`<div class="msg ${{m.role}}"><b>${{m.role==='assistant'?'AURA':'YOU'}}</b>${{esc(m.content)}}</div>`).join('');q('messages').scrollTop=q('messages').scrollHeight}}async function sendMessage(){{const text=q('input').value.trim();if(!text)return;if(!active)await newThread();q('send').disabled=true;q('status').textContent='Aura is thinking…';try{{q('input').value='';const d=await api(`/aura-intelligence/api/threads/${{active}}/messages`,{{method:'POST',body:JSON.stringify({{message:text}})}});const t=await api(`/aura-intelligence/api/threads/${{active}}`);render(t.messages||[]);await loadThreads();q('status').textContent='Ready.'}}catch(e){{q('status').textContent=e.message;q('input').value=text}}finally{{q('send').disabled=false;q('input').focus()}}}}function keySend(e){{if(e.key==='Enter'&&!e.shiftKey){{e.preventDefault();sendMessage()}}}}loadThreads().then(async()=>{{const d=await api('/aura-intelligence/api/threads');if(d.threads?.length)openThread(d.threads[0].id)}}).catch(e=>q('status').textContent=e.message);
</script></body></html>"""
    return HTMLResponse(html)
