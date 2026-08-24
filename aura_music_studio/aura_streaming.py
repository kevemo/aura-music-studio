from __future__ import annotations

import json
import threading
from collections.abc import Generator

import requests
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .aura_agent_core import (
    AURA_CORE_SYSTEM,
    AuraAgent,
    AuraModelClient,
    ModelReply,
    _attachment_context,
    _explicit_memory,
    _history_messages,
    _memory_context,
)
from .aura_agent_tools import AuraToolRegistry, project_snapshot
from .aura_chat_store import AuraChatStore

router = APIRouter(tags=["Aura Realtime"])
store = AuraChatStore()
base_agent = AuraAgent(store=store)
_thread_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class StreamMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=50000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=12)


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def _lock(thread_id: str) -> threading.Lock:
    with _locks_guard:
        return _thread_locks.setdefault(thread_id, threading.Lock())


def _event(kind: str, **payload) -> bytes:
    return (json.dumps({"type": kind, **payload}, ensure_ascii=False, default=str) + "\n").encode("utf-8")


def _stream_ollama(model: AuraModelClient, messages: list[dict]) -> Generator[str, None, None]:
    if not model.ollama_base:
        raise RuntimeError("OLLAMA_BASE_URL is not configured")
    response = requests.post(
        f"{model.ollama_base}/api/chat",
        json={
            "model": model.ollama_model,
            "stream": True,
            "messages": messages,
            "options": {"temperature": 0.42},
        },
        timeout=(15, model.timeout),
        stream=True,
    )
    response.raise_for_status()
    with response:
        for raw in response.iter_lines(decode_unicode=True):
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            token = str((data.get("message") or {}).get("content") or "")
            if token:
                yield token
            if data.get("done"):
                break


def _stream_openai_compatible(model: AuraModelClient, messages: list[dict]) -> Generator[str, None, None]:
    if not model.openai_base or not model.openai_model:
        raise RuntimeError("OpenAI-compatible local endpoint/model is not configured")
    headers = {"Content-Type": "application/json"}
    if model.openai_key:
        headers["Authorization"] = f"Bearer {model.openai_key}"
    response = requests.post(
        f"{model.openai_base}/v1/chat/completions",
        headers=headers,
        json={
            "model": model.openai_model,
            "messages": messages,
            "temperature": 0.42,
            "stream": True,
        },
        timeout=(15, model.timeout),
        stream=True,
    )
    response.raise_for_status()
    with response:
        for raw in response.iter_lines(decode_unicode=True):
            if not raw:
                continue
            line = raw.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                data = json.loads(line)
                choice = (data.get("choices") or [{}])[0]
                token = str((choice.get("delta") or {}).get("content") or "")
            except Exception:
                continue
            if token:
                yield token


def _provider_order(model: AuraModelClient) -> list[str]:
    result = []
    if model.provider in {"auto", "ollama", "local"}:
        result.append("ollama")
    if model.provider in {"auto", "openai_compatible", "local"}:
        result.append("openai_compatible")
    return result


def _build_generation(
    *,
    member,
    thread_id: str,
    text: str,
    attachment_ids: list[str],
) -> tuple[dict, list[dict], list[dict], dict | None]:
    user_id = member.user_id
    thread = store.thread(user_id, thread_id)
    if not thread:
        raise KeyError(thread_id)
    user_message = store.add_message(user_id, thread_id, "user", text)
    if attachment_ids:
        store.bind_attachments(user_id, thread_id, user_message["id"], attachment_ids)
    attachments = store.message_attachments(user_id, thread_id, user_message["id"])

    memory_saved = None
    explicit = _explicit_memory(text)
    if explicit:
        memory_saved = store.add_memory(user_id, explicit[0], explicit[1])

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
    plan = base_agent._tool_plan(text=text, registry=registry, project_context=project_context)
    tool_results: list[dict] = []
    for call in plan.calls[:6]:
        run_id = store.start_tool_run(user_id, thread_id, user_message["id"], call.name, call.arguments)
        try:
            result = registry.execute(call, latest_user_message=text)
            store.finish_tool_run(run_id, result=result)
            tool_results.append({"tool": call.name, "ok": True, "result": result})
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
            store.finish_tool_run(run_id, error=error)
            tool_results.append({"tool": call.name, "ok": False, "error": error})

    rows = store.messages(user_id, thread_id, limit=400)
    summary = base_agent._maybe_summarize(user_id, thread_id, rows)
    memories = store.memories(user_id, enabled_only=True, limit=50)
    system_parts = [AURA_CORE_SYSTEM]
    if summary:
        system_parts.append("Conversation summary from older turns:\n" + summary)
    memory_text = _memory_context(memories)
    if memory_text:
        system_parts.append(memory_text)
    if project_context:
        system_parts.append(
            "Pinned project snapshot (private member context):\n"
            + json.dumps(project_context, ensure_ascii=False, default=str)[:45000]
        )
    attach_text = _attachment_context(attachments)
    if attach_text:
        system_parts.append(attach_text)
    if tool_results:
        system_parts.append(
            "Aura tool results for the latest request. Treat these as the authoritative execution/retrieval record:\n"
            + json.dumps(tool_results, ensure_ascii=False, default=str)[:65000]
        )
    if memory_saved:
        system_parts.append("The member explicitly requested this memory and it was saved successfully.")
    messages = [{"role": "system", "content": "\n\n".join(system_parts)}]
    messages.extend(_history_messages(rows, maximum=70))
    return user_message, messages, tool_results, memory_saved


def _stream_response(member, thread_id: str, text: str, attachment_ids: list[str]):
    lock = _lock(thread_id)
    if not lock.acquire(blocking=False):
        yield _event("error", error="Aura is already generating a response in this conversation")
        return
    collected: list[str] = []
    saved = False
    try:
        try:
            user_message, messages, tool_results, memory_saved = _build_generation(
                member=member,
                thread_id=thread_id,
                text=text,
                attachment_ids=attachment_ids,
            )
        except Exception as exc:
            yield _event("error", error=f"{type(exc).__name__}: {exc}")
            return

        yield _event(
            "start",
            user_message_id=user_message["id"],
            tools=[{"name": row["tool"], "ok": row["ok"]} for row in tool_results],
            memory_saved=bool(memory_saved),
        )
        for row in tool_results:
            yield _event("tool", name=row["tool"], ok=row["ok"], error=row.get("error"))

        model: AuraModelClient = base_agent.model
        provider_used = None
        model_used = None
        errors = []
        emitted = False
        for provider in _provider_order(model):
            if emitted:
                break
            try:
                if provider == "ollama":
                    stream = _stream_ollama(model, messages)
                    model_name = model.ollama_model
                else:
                    stream = _stream_openai_compatible(model, messages)
                    model_name = model.openai_model
                for token in stream:
                    emitted = True
                    provider_used = provider
                    model_used = model_name
                    collected.append(token)
                    yield _event("delta", text=token)
                if emitted:
                    break
            except Exception as exc:
                errors.append(f"{provider}: {type(exc).__name__}: {exc}")
                if emitted:
                    break

        if not collected:
            try:
                fallback: ModelReply = model.complete(messages, temperature=.42)
                provider_used = fallback.provider
                model_used = fallback.model
                collected.append(fallback.text)
                yield _event("delta", text=fallback.text)
            except Exception as exc:
                errors.append(f"fallback: {type(exc).__name__}: {exc}")
                yield _event("error", error="No Aura reasoning model is reachable", detail=" | ".join(errors[-3:]))
                return

        text_out = "".join(collected).strip()
        if not text_out:
            yield _event("error", error="Aura produced an empty response")
            return
        assistant = store.add_message(member.user_id, thread_id, "assistant", text_out)
        saved = True
        yield _event(
            "done",
            message=assistant,
            provider=provider_used,
            model=model_used,
            thread=store.thread(member.user_id, thread_id),
        )
    except GeneratorExit:
        raise
    except Exception as exc:
        if collected and not saved:
            partial = "".join(collected).strip()
            if partial:
                store.add_message(member.user_id, thread_id, "assistant", partial + "\n\n[Response interrupted before completion.]")
        yield _event("error", error=f"{type(exc).__name__}: {exc}")
    finally:
        lock.release()


@router.post("/aura-intelligence/api/threads/{thread_id}/messages-stream")
def stream_message(thread_id: str, body: StreamMessageRequest, request: Request):
    member = _member(request)
    if not store.thread(member.user_id, thread_id):
        raise HTTPException(404, "Aura conversation not found")
    return StreamingResponse(
        _stream_response(member, thread_id, body.message, body.attachment_ids),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
