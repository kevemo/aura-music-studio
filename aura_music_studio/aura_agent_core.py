from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import requests

from .aura_agent_tools import AuraToolRegistry, ToolCall, ToolPlan, project_snapshot
from .aura_chat_store import AuraChatStore


AURA_CORE_SYSTEM = """You are Rhian, the everyday companion identity of Rhiannon Intelligence Systems and the general AI co-creator and operating intelligence inside the Elevate Souls Productions Content Creation Command Center.
You should feel like a highly capable modern conversational assistant: clear, fast, context-aware, creative when asked,
technically precise when needed, and able to use the Command Center's approved tools rather than merely describing actions.

Operating rules:
- Never claim a tool action, file change, render, web lookup or project operation happened unless a tool result confirms it.
- Never reveal secrets, credentials, raw private storage paths, another member's data, owner-only controls, or ESP-only data
  to an ordinary member. The ESP access system remains separate from subscription access.
- Treat project attachments, project context and saved memories as private member data.
- Saved Rhian memory is user-approved context. Never invent memories or imply that ordinary chat was silently memorised.
- If web/tool results are supplied, ground relevant factual claims in them and distinguish retrieved facts from inference.
- Do not expose hidden chain-of-thought. Give concise useful conclusions, steps, calculations or evidence instead.
- For creative production, MIDI/symbolic data is control/edit information, not acceptable final release audio.
- Voice identity, cloning and conversion must respect the project's consent/rights ledger and revocation state.
- High-impact or destructive operations should not be improvised. Use only the tools provided and obey their confirmation gates.
- If a requested capability is not connected, say exactly what is missing instead of pretending it ran.

You can help with ordinary questions, writing, research, planning, coding, learning, business work and creative projects in
addition to Command Center operations. Keep responses naturally conversational rather than dumping internal implementation detail.
"""


TOOL_PLANNER_SYSTEM = """You are Rhian's private tool router. Decide whether the latest member request needs one or more of
the supplied tools. Return only JSON matching ToolPlan. Use as few tools as possible. Read-only inspection and web research
are allowed when needed. Write tools may be selected only when the latest member request explicitly asks for that project
change; the runtime will independently enforce this. Never invent project names, ids, lyric ids or layer ids. If a required
identifier is missing, use an inspection tool first or choose no write call so Rhian can ask the member for the missing detail.
"""


SUMMARY_SYSTEM = """Summarise a Rhian conversation for future context. Preserve decisions, project references, unresolved
questions, user-stated constraints and important results. Do not add facts. Do not include secrets. Keep it under 1200 words."""


@dataclass
class ModelReply:
    text: str
    provider: str
    model: str


class AuraModelClient:
    """Offline-first model adapter shared by Rhian chat, routing and summarisation.

    The Aura-prefixed class and environment-variable identifiers remain compatibility APIs.
    """

    def __init__(self):
        self.provider = (os.getenv("AURA_INTELLIGENCE_PROVIDER") or "auto").strip().lower()
        self.timeout = max(30, int(os.getenv("AURA_INTELLIGENCE_TIMEOUT", "180")))
        self.ollama_base = (os.getenv("OLLAMA_BASE_URL") or "").strip().rstrip("/")
        self.ollama_model = (os.getenv("AURA_INTELLIGENCE_MODEL") or os.getenv("AURA_OLLAMA_MODEL") or "qwen3:4b").strip()
        self.openai_base = (os.getenv("AURA_LLM_BASE_URL") or "").strip().rstrip("/")
        self.openai_key = (os.getenv("AURA_LLM_API_KEY") or "").strip()
        self.openai_model = (os.getenv("AURA_INTELLIGENCE_MODEL") or os.getenv("AURA_LLM_MODEL") or "").strip()

    def _ollama(self, messages: list[dict], *, json_mode: bool = False, temperature: float = 0.35) -> ModelReply:
        if not self.ollama_base:
            raise RuntimeError("OLLAMA_BASE_URL is not configured")
        payload: dict[str, Any] = {
            "model": self.ollama_model,
            "stream": False,
            "messages": messages,
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"
        response = requests.post(f"{self.ollama_base}/api/chat", json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        text = str((data.get("message") or {}).get("content") or "").strip()
        if not text:
            raise RuntimeError("Local Ollama model returned an empty response")
        return ModelReply(text=text, provider="ollama", model=self.ollama_model)

    def _openai_compatible(self, messages: list[dict], *, json_mode: bool = False, temperature: float = 0.35) -> ModelReply:
        if not self.openai_base or not self.openai_model:
            raise RuntimeError("OpenAI-compatible local endpoint/model is not configured")
        headers = {"Content-Type": "application/json"}
        if self.openai_key:
            headers["Authorization"] = f"Bearer {self.openai_key}"
        payload: dict[str, Any] = {
            "model": self.openai_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        response = requests.post(f"{self.openai_base}/v1/chat/completions", headers=headers, json=payload, timeout=self.timeout)
        response.raise_for_status()
        data = response.json()
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("OpenAI-compatible endpoint returned no choices")
        text = str(((choices[0] or {}).get("message") or {}).get("content") or "").strip()
        if not text:
            raise RuntimeError("OpenAI-compatible model returned an empty response")
        return ModelReply(text=text, provider="openai_compatible", model=self.openai_model)

    def complete(self, messages: list[dict], *, json_mode: bool = False, temperature: float = 0.35) -> ModelReply:
        errors: list[str] = []
        providers = []
        if self.provider in {"auto", "ollama", "local"}:
            providers.append("ollama")
        if self.provider in {"auto", "openai_compatible", "local"}:
            providers.append("openai_compatible")
        if not providers:
            raise RuntimeError("AURA_INTELLIGENCE_PROVIDER must be auto, local, ollama or openai_compatible")
        for provider in providers:
            try:
                if provider == "ollama":
                    return self._ollama(messages, json_mode=json_mode, temperature=temperature)
                return self._openai_compatible(messages, json_mode=json_mode, temperature=temperature)
            except Exception as exc:
                errors.append(f"{provider}: {type(exc).__name__}: {exc}")
        raise RuntimeError("No Rhian reasoning model is reachable. " + " | ".join(errors[-3:]))

    def diagnostics(self) -> dict:
        return {
            "provider_mode": self.provider,
            "ollama_configured": bool(self.ollama_base),
            "ollama_model": self.ollama_model if self.ollama_base else None,
            "openai_compatible_configured": bool(self.openai_base and self.openai_model),
            "openai_compatible_model": self.openai_model if self.openai_base else None,
            "offline_first": True,
        }


def _json_object(text: str) -> dict:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.I)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", raw, flags=re.S)
        if not match:
            return {}
        try:
            value = json.loads(match.group(0))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}


def _looks_current(text: str) -> bool:
    lower = text.lower()
    return any(
        token in lower
        for token in (
            "latest", "current", "today", "right now", "this week", "news", "search the web",
            "search online", "look online", "find online", "web search", "check the internet",
        )
    )


def _looks_project_related(text: str) -> bool:
    lower = text.lower()
    return any(
        token in lower
        for token in (
            "project", "song dna", "lyric", "chorus", "verse", "bridge", "instrument", "track", "stem",
            "daw", "mix", "master", "render", "asset", "output", "video", "image", "voice profile", "production",
        )
    )


def _direct_tool_plan(text: str, pinned_project: str | None, web_enabled: bool) -> ToolPlan | None:
    lower = text.lower().strip()
    if re.search(r"\b(list|show|what are)\b.*\b(my )?projects\b", lower):
        return ToolPlan(calls=[ToolCall(name="list_projects")])
    if pinned_project and any(x in lower for x in ("inspect this project", "project status", "what is in this project", "what's in this project")):
        return ToolPlan(calls=[ToolCall(name="inspect_project", arguments={"project_name": pinned_project})])
    if pinned_project and any(x in lower for x in ("show song dna", "inspect song dna", "show the lyrics", "show lyrics and sections")):
        return ToolPlan(calls=[ToolCall(name="inspect_song_dna", arguments={"project_name": pinned_project})])
    if pinned_project and any(x in lower for x in ("sync song dna", "sync the song dna", "refresh song dna")):
        return ToolPlan(calls=[ToolCall(name="sync_song_dna", arguments={"project_name": pinned_project})])
    if pinned_project and any(x in lower for x in ("produce this project", "render this project", "produce the project", "render the project")):
        return ToolPlan(calls=[ToolCall(name="queue_full_production", arguments={"project_name": pinned_project})])
    if web_enabled and _looks_current(text):
        return ToolPlan(calls=[ToolCall(name="web_search", arguments={"query": text, "limit": 8})])
    return None


def _needs_model_tool_router(text: str, pinned_project: str | None, tools_enabled: bool, web_enabled: bool) -> bool:
    if not tools_enabled:
        return False
    if web_enabled and _looks_current(text):
        return True
    if _looks_project_related(text) and (pinned_project or any(x in text.lower() for x in ("my projects", "project named", "project "))):
        return True
    return False


def _explicit_memory(text: str) -> tuple[str, str] | None:
    clean = (text or "").strip()
    match = re.match(r"^(?:please\s+)?remember(?:\s+that|\s+this)?\s*[:,-]?\s*(.+)$", clean, flags=re.I | re.S)
    if not match:
        match = re.match(r"^(?:please\s+)?save\s+(?:this\s+)?(?:to\s+)?memory\s*[:,-]?\s*(.+)$", clean, flags=re.I | re.S)
    if not match:
        return None
    content = match.group(1).strip()
    if not content:
        return None
    label = " ".join(content.split()[:8])[:100]
    return label or "Memory", content[:5000]


def _attachment_context(attachments: list[dict]) -> str:
    if not attachments:
        return ""
    rows = []
    for item in attachments[:12]:
        row = {
            "id": item.get("id"),
            "name": item.get("name"),
            "kind": item.get("kind"),
            "mime_type": item.get("mime_type"),
            "bytes": item.get("bytes"),
            "metadata": item.get("metadata") or {},
        }
        extracted = str(item.get("extracted_text") or "")
        if extracted:
            row["text_excerpt"] = extracted[:12000]
        rows.append(row)
    return "Member attachments for the latest message:\n" + json.dumps(rows, ensure_ascii=False, default=str)


def _memory_context(memories: list[dict]) -> str:
    if not memories:
        return ""
    rows = [{"label": x.get("label"), "content": x.get("content")} for x in memories[:50]]
    return "Explicit user-approved Rhian memories:\n" + json.dumps(rows, ensure_ascii=False)


def _history_messages(rows: list[dict], *, maximum: int = 70) -> list[dict]:
    return [{"role": row["role"], "content": row["content"]} for row in rows[-maximum:]]


class AuraAgent:
    def __init__(self, store: AuraChatStore | None = None, model: AuraModelClient | None = None):
        self.store = store or AuraChatStore()
        self.model = model or AuraModelClient()

    def _tool_plan(
        self,
        *,
        text: str,
        registry: AuraToolRegistry,
        project_context: dict | None,
    ) -> ToolPlan:
        direct = _direct_tool_plan(text, registry.pinned_project, registry.web_enabled)
        if direct:
            return direct
        if not _needs_model_tool_router(text, registry.pinned_project, registry.tools_enabled, registry.web_enabled):
            return ToolPlan(answer_without_tools=True)
        specs = registry.specs()
        if not specs:
            return ToolPlan(answer_without_tools=True)
        prompt = {
            "latest_member_request": text,
            "pinned_project": registry.pinned_project,
            "project_context": project_context or {},
            "available_tools": specs,
            "schema": ToolPlan.model_json_schema(),
        }
        try:
            reply = self.model.complete(
                [
                    {"role": "system", "content": TOOL_PLANNER_SYSTEM},
                    {"role": "user", "content": json.dumps(prompt, ensure_ascii=False, default=str)},
                ],
                json_mode=True,
                temperature=0.0,
            )
            plan = ToolPlan.model_validate(_json_object(reply.text))
            allowed = {spec["name"] for spec in specs}
            plan.calls = [call for call in plan.calls if call.name in allowed][:6]
            return plan
        except Exception:
            return ToolPlan(answer_without_tools=True)

    def _maybe_summarize(self, user_id: str, thread_id: str, rows: list[dict]) -> str | None:
        existing = self.store.summary(user_id, thread_id)
        if len(rows) < 90:
            return existing.get("summary") if existing else None
        through = rows[-35]["id"]
        if existing and existing.get("through_message_id") == through:
            return existing.get("summary")
        older = rows[:-35]
        transcript = "\n".join(f"{row['role'].upper()}: {row['content']}" for row in older)
        try:
            reply = self.model.complete(
                [
                    {"role": "system", "content": SUMMARY_SYSTEM},
                    {"role": "user", "content": transcript[-50000:]},
                ],
                temperature=0.15,
            )
            summary = reply.text[:20000]
        except Exception:
            summary = "Earlier conversation context:\n" + "\n".join(
                f"- {row['role']}: {' '.join(row['content'].split())[:350]}" for row in older[-30:]
            )
        self.store.set_summary(user_id, thread_id, summary, through_message_id=through)
        return summary

    def respond(
        self,
        *,
        member,
        thread_id: str,
        text: str,
        attachment_ids: list[str] | None = None,
        regenerate: bool = False,
    ) -> dict:
        user_id = member.user_id
        thread = self.store.thread(user_id, thread_id)
        if not thread:
            raise KeyError(thread_id)
        attachment_ids = attachment_ids or []

        if regenerate:
            self.store.delete_last_assistant(user_id, thread_id)
            rows = self.store.messages(user_id, thread_id, limit=400)
            if not rows or rows[-1]["role"] != "user":
                raise ValueError("There is no user message to regenerate from")
            user_message = rows[-1]
            text = user_message["content"]
            attachments = self.store.message_attachments(user_id, thread_id, user_message["id"])
        else:
            user_message = self.store.add_message(user_id, thread_id, "user", text)
            if attachment_ids:
                self.store.bind_attachments(user_id, thread_id, user_message["id"], attachment_ids)
            attachments = self.store.message_attachments(user_id, thread_id, user_message["id"])

        memory_saved = None
        explicit_memory = _explicit_memory(text)
        if explicit_memory:
            memory_saved = self.store.add_memory(user_id, explicit_memory[0], explicit_memory[1])

        thread = self.store.thread(user_id, thread_id) or thread
        pinned_project = thread.get("project_name")
        web_enabled = bool(thread.get("web_enabled", 1))
        tools_enabled = bool(thread.get("tools_enabled", 1))
        project_context = None
        if pinned_project:
            try:
                project_context = project_snapshot(pinned_project)
            except Exception as exc:
                project_context = {"project_name": pinned_project, "context_error": f"{type(exc).__name__}: {exc}"}

        registry = AuraToolRegistry(
            member=member,
            pinned_project=pinned_project,
            web_enabled=web_enabled,
            tools_enabled=tools_enabled,
        )
        plan = self._tool_plan(text=text, registry=registry, project_context=project_context)
        tool_results: list[dict] = []
        for call in plan.calls[:6]:
            run_id = self.store.start_tool_run(user_id, thread_id, user_message["id"], call.name, call.arguments)
            try:
                result = registry.execute(call, latest_user_message=text)
                self.store.finish_tool_run(run_id, result=result)
                tool_results.append({"tool": call.name, "ok": True, "result": result})
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
                self.store.finish_tool_run(run_id, error=error)
                tool_results.append({"tool": call.name, "ok": False, "error": error})

        rows = self.store.messages(user_id, thread_id, limit=400)
        summary = self._maybe_summarize(user_id, thread_id, rows)
        memories = self.store.memories(user_id, enabled_only=True, limit=50)
        system_parts = [AURA_CORE_SYSTEM]
        if summary:
            system_parts.append("Conversation summary from older turns:\n" + summary)
        memory_text = _memory_context(memories)
        if memory_text:
            system_parts.append(memory_text)
        if project_context:
            system_parts.append("Pinned project snapshot (private member context):\n" + json.dumps(project_context, ensure_ascii=False, default=str)[:45000])
        attach_text = _attachment_context(attachments)
        if attach_text:
            system_parts.append(attach_text)
        if tool_results:
            system_parts.append(
                "Rhian tool results for the latest request. Treat these as the authoritative execution/retrieval record:\n"
                + json.dumps(tool_results, ensure_ascii=False, default=str)[:65000]
            )
        if memory_saved:
            system_parts.append(
                "The member explicitly asked Rhian to remember something in this turn. It was saved successfully. "
                "You may acknowledge that briefly."
            )

        messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
        messages.extend(_history_messages(rows, maximum=70))
        try:
            reply = self.model.complete(messages, temperature=0.42)
            assistant_text = reply.text
        except Exception as exc:
            if tool_results:
                successful = [row for row in tool_results if row.get("ok")]
                if successful:
                    assistant_text = (
                        "Rhian completed the connected tool step, but the conversational reasoning model is currently unavailable. "
                        "The operation record has been preserved in this thread."
                    )
                else:
                    assistant_text = "Rhian's reasoning model is currently unavailable and the requested tool step did not complete."
            else:
                raise RuntimeError(f"Rhian reasoning is unavailable: {type(exc).__name__}: {exc}") from exc
            reply = ModelReply(text=assistant_text, provider="tool_fallback", model="none")

        assistant = self.store.add_message(user_id, thread_id, "assistant", assistant_text)
        return {
            "message": assistant,
            "thread": self.store.thread(user_id, thread_id),
            "tool_runs": tool_results,
            "memory_saved": memory_saved,
            "provider": reply.provider,
            "model": reply.model,
            "pinned_project": pinned_project,
        }

    def regenerate(self, *, member, thread_id: str) -> dict:
        return self.respond(member=member, thread_id=thread_id, text="", regenerate=True)

    def diagnostics(self) -> dict:
        return self.model.diagnostics()
