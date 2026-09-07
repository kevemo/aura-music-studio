from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from .aura_chat_store import AuraChatStore
from .aura_ui_extension import UI_SCRIPT
from .aura_work_modes import (
    WORK_MODE_UI_SCRIPT,
    activate_work_mode_scope,
    install_aura_work_modes,
    router as work_mode_router,
    work_mode_instruction,
)

AuraReasoningMode = Literal["fast", "auto", "deep", "creative"]
router = APIRouter(tags=["Aura Reasoning Modes"])
router.include_router(work_mode_router)
store = AuraChatStore()


@dataclass(frozen=True)
class ModeConfig:
    name: AuraReasoningMode
    temperature: float
    history_messages: int
    instruction: str


MODE_CONFIGS: dict[str, ModeConfig] = {
    "fast": ModeConfig(
        name="fast",
        temperature=0.22,
        history_messages=40,
        instruction=(
            "Aura Fast mode is active. Prioritize latency and concise useful answers. Use direct/deterministic tools when clearly needed; "
            "do not perform unnecessary planning calls. Preserve all safety, rights, source and tool-truthfulness rules."
        ),
    ),
    "auto": ModeConfig(
        name="auto",
        temperature=0.42,
        history_messages=70,
        instruction=(
            "Aura Auto mode is active. Balance speed, reasoning depth, creativity and tool use according to the request."
        ),
    ),
    "deep": ModeConfig(
        name="deep",
        temperature=0.20,
        history_messages=110,
        instruction=(
            "Aura Deep mode is active. Be more deliberate: verify assumptions against available project/tool/source evidence, consider edge cases, "
            "and give a well-supported conclusion. Do not expose hidden chain-of-thought; provide conclusions, checks, evidence and concise reasoning summaries instead."
        ),
    ),
    "creative": ModeConfig(
        name="creative",
        temperature=0.78,
        history_messages=80,
        instruction=(
            "Aura Creative mode is active. Explore stronger alternatives, imaginative concepts and novel combinations while respecting the member's constraints. "
            "Creative freedom never overrides rights, safety, project preservation instructions or truthful tool/render status."
        ),
    ),
}


class ModeRequest(BaseModel):
    mode: AuraReasoningMode


def _member(request: Request):
    member = getattr(request.state, "member", None)
    if member is None:
        raise HTTPException(401, "Sign in required")
    return member


def ensure_mode_schema(chat_store: AuraChatStore) -> None:
    with chat_store._connect() as con:
        con.execute(
            """CREATE TABLE IF NOT EXISTS aura_chat_reasoning_modes (
                   thread_id TEXT PRIMARY KEY,
                   user_id TEXT NOT NULL,
                   mode TEXT NOT NULL DEFAULT 'auto',
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   FOREIGN KEY(thread_id) REFERENCES aura_chat_threads(id) ON DELETE CASCADE,
                   FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
               )"""
        )


def get_reasoning_mode(chat_store: AuraChatStore, user_id: str, thread_id: str) -> AuraReasoningMode:
    ensure_mode_schema(chat_store)
    if not chat_store.thread(user_id, thread_id):
        raise KeyError(thread_id)
    with chat_store._connect() as con:
        row = con.execute(
            "SELECT mode FROM aura_chat_reasoning_modes WHERE thread_id=? AND user_id=?",
            (thread_id, user_id),
        ).fetchone()
    value = str(row["mode"] if row else "auto").lower()
    # Streaming already resolves reasoning mode for every turn, so this also activates the
    # separate work-mode ContextVar before any streaming tool registry is constructed.
    activate_work_mode_scope(chat_store, user_id, thread_id)
    return value if value in MODE_CONFIGS else "auto"  # type: ignore[return-value]


def set_reasoning_mode(chat_store: AuraChatStore, user_id: str, thread_id: str, mode: str) -> AuraReasoningMode:
    ensure_mode_schema(chat_store)
    value = (mode or "").strip().lower()
    if value not in MODE_CONFIGS:
        raise ValueError("Aura reasoning mode must be fast, auto, deep or creative")
    if not chat_store.thread(user_id, thread_id):
        raise KeyError(thread_id)
    with chat_store._connect() as con:
        con.execute(
            """INSERT INTO aura_chat_reasoning_modes(thread_id,user_id,mode,updated_at)
               VALUES (?,?,?,CURRENT_TIMESTAMP)
               ON CONFLICT(thread_id) DO UPDATE SET mode=excluded.mode,user_id=excluded.user_id,updated_at=CURRENT_TIMESTAMP""",
            (thread_id, user_id, value),
        )
    activate_work_mode_scope(chat_store, user_id, thread_id)
    return value  # type: ignore[return-value]


def mode_config(mode: str) -> ModeConfig:
    base = MODE_CONFIGS.get((mode or "").lower(), MODE_CONFIGS["auto"])
    return ModeConfig(
        name=base.name,
        temperature=base.temperature,
        history_messages=base.history_messages,
        instruction=base.instruction + "\n" + work_mode_instruction(),
    )


def detect_mode_command(text: str) -> AuraReasoningMode | None:
    clean = " ".join((text or "").strip().lower().split())
    patterns = [
        r"^(?:aura[,:]?\s*)?(?:switch|set|change|use|go)?\s*(?:to\s+)?(fast|auto|deep|creative)\s+mode(?:\s+please)?[.!]?$",
        r"^(?:aura[,:]?\s*)?(fast|auto|deep|creative)\s+mode[.!]?$",
    ]
    for pattern in patterns:
        match = re.match(pattern, clean)
        if match:
            return match.group(1)  # type: ignore[return-value]
    return None


@router.get("/aura-intelligence/api/threads/{thread_id}/reasoning-mode")
def get_mode(thread_id: str, request: Request):
    member = _member(request)
    try:
        mode = get_reasoning_mode(store, member.user_id, thread_id)
    except KeyError as exc:
        raise HTTPException(404, "Aura conversation not found") from exc
    config = mode_config(mode)
    return {"mode": mode, "temperature": config.temperature, "history_messages": config.history_messages}


@router.put("/aura-intelligence/api/threads/{thread_id}/reasoning-mode")
def put_mode(thread_id: str, body: ModeRequest, request: Request):
    member = _member(request)
    try:
        mode = set_reasoning_mode(store, member.user_id, thread_id, body.mode)
    except KeyError as exc:
        raise HTTPException(404, "Aura conversation not found") from exc
    return {"mode": mode, "detail": f"Aura {mode.title()} mode is now active for this conversation."}


# This router is mounted before aura_ui_extension_router in app.py. Serving the combined script
# here keeps the existing Aura extension intact while adding work-mode controls without another
# app-level route or page fork.
@router.get("/aura-intelligence/ui-extension.js", include_in_schema=False)
def combined_aura_ui_extension():
    return Response(
        content=UI_SCRIPT + "\n" + WORK_MODE_UI_SCRIPT,
        media_type="application/javascript",
        headers={"Cache-Control": "no-store"},
    )


install_aura_work_modes()


__all__ = [
    "router",
    "AuraReasoningMode",
    "ModeConfig",
    "MODE_CONFIGS",
    "detect_mode_command",
    "get_reasoning_mode",
    "set_reasoning_mode",
    "mode_config",
]
