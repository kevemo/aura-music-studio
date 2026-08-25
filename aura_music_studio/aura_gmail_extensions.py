from __future__ import annotations

import base64
from html.parser import HTMLParser
from urllib.parse import quote

from . import aura_agent_core as core
from . import aura_agent_tools as tools
from .aura_connectors import google

_INSTALLED = False
_MAX_BODY = 60_000
_MAX_PARTS = 100

READ_SPEC = tools.ToolSpec(
    "gmail_read_message",
    "Read one message body from the member's explicitly connected Gmail account using a message id returned by Gmail search. Read-only; returns bounded text and attachment metadata, never OAuth tokens.",
    {"message_id": "Gmail message id returned by gmail_search."},
)


class _HTMLText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        lower = tag.lower()
        if lower in {"script", "style", "noscript"}:
            self._skip += 1
        elif lower in {"br", "p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.rows.append("\n")

    def handle_endtag(self, tag):
        lower = tag.lower()
        if lower in {"script", "style", "noscript"}:
            self._skip = max(0, self._skip - 1)
        elif lower in {"p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"}:
            self.rows.append("\n")

    def handle_data(self, data):
        if not self._skip and data:
            self.rows.append(data)

    def text(self) -> str:
        raw = "".join(self.rows)
        return "\n".join(line.strip() for line in raw.splitlines() if line.strip())


def _decode(data: str | None) -> str:
    if not data:
        return ""
    value = str(data).encode("ascii", errors="ignore")
    value += b"=" * (-len(value) % 4)
    try:
        return base64.urlsafe_b64decode(value).decode("utf-8", errors="replace")
    except Exception:
        return ""


def _html_to_text(value: str) -> str:
    parser = _HTMLText()
    try:
        parser.feed(value)
        parser.close()
    except Exception:
        return ""
    return parser.text()


def _headers(payload: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in payload.get("headers") or []:
        name = str(row.get("name") or "").strip().lower()
        if name and name not in result:
            result[name] = str(row.get("value") or "")[:4000]
    return result


def _extract_payload(payload: dict) -> tuple[str, str, list[dict]]:
    plain: list[str] = []
    html: list[str] = []
    attachments: list[dict] = []
    stack: list[tuple[dict, int]] = [(payload or {}, 0)]
    seen = 0
    while stack and seen < _MAX_PARTS:
        part, depth = stack.pop()
        seen += 1
        if depth > 12:
            continue
        mime = str(part.get("mimeType") or "").lower()
        filename = str(part.get("filename") or "").strip()
        body = part.get("body") or {}
        attachment_id = body.get("attachmentId")
        if filename or attachment_id:
            attachments.append({
                "filename": filename or "attachment",
                "mime_type": mime or "application/octet-stream",
                "size": int(body.get("size") or 0),
                "external_body": bool(attachment_id),
            })
        encoded = body.get("data")
        if encoded and not filename:
            decoded = _decode(str(encoded))
            if mime == "text/plain":
                plain.append(decoded)
            elif mime == "text/html":
                html.append(decoded)
        for child in reversed(part.get("parts") or []):
            if isinstance(child, dict):
                stack.append((child, depth + 1))
    if plain:
        text = "\n\n".join(item.strip() for item in plain if item.strip()).strip()
        return text, "text/plain", attachments[:50]
    if html:
        text = "\n\n".join(_html_to_text(item) for item in html if item.strip()).strip()
        return text, "text/html→text", attachments[:50]
    return "", "none", attachments[:50]


def read_gmail_message(user_id: str, message_id: str) -> dict:
    clean = (message_id or "").strip()
    if not clean or len(clean) > 300:
        raise ValueError("A valid Gmail message id is required")
    data = google._get(
        user_id,
        "gmail",
        f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{quote(clean, safe='')}",
        params={"format": "full"},
    )
    payload = data.get("payload") or {}
    headers = _headers(payload)
    text, source_format, attachments = _extract_payload(payload)
    if not text:
        text = str(data.get("snippet") or "").strip()
        source_format = "snippet" if text else "none"
    return {
        "message": {
            "id": data.get("id") or clean,
            "thread_id": data.get("threadId"),
            "subject": headers.get("subject") or "(no subject)",
            "from": headers.get("from"),
            "to": headers.get("to"),
            "cc": headers.get("cc"),
            "date": headers.get("date"),
            "label_ids": (data.get("labelIds") or [])[:30],
        },
        "body": text[:_MAX_BODY],
        "characters": len(text),
        "truncated": len(text) > _MAX_BODY,
        "body_source": source_format,
        "attachments": attachments,
        "attachments_downloaded": False,
        "read_only": True,
        "tokens_exposed": False,
    }


def _wants_gmail_read(text: str) -> bool:
    lower = (text or "").lower()
    return any(term in lower for term in (
        "read it", "read the email", "read the message", "open it", "open the email",
        "summarize it", "summarise it", "summarize the email", "summarise the email",
        "what does it say", "show the email", "show the message",
    ))


def _gmail_search_query(text: str) -> str | None:
    lower = (text or "").lower()
    for phrase in ("search my email for", "search gmail for", "find in my email", "find in gmail"):
        index = lower.find(phrase)
        if index < 0:
            continue
        value = text[index + len(phrase):].strip()
        for tail in (" and read", " and open", " and summarize", " and summarise", " then read", " then open", " then summarize", " then summarise"):
            pos = value.lower().find(tail)
            if pos >= 0:
                value = value[:pos]
                break
        return value.strip(" :,-.?") or None
    return None


def install_aura_gmail_extensions() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    if READ_SPEC.name not in {item.name for item in tools.TOOL_SPECS}:
        tools.TOOL_SPECS.append(READ_SPEC)
        tools._SPEC_BY_NAME[READ_SPEC.name] = READ_SPEC

    original_execute = tools.AuraToolRegistry.execute
    original_direct = core._direct_tool_plan
    original_needs = core._needs_model_tool_router

    def execute(self, call: tools.ToolCall, *, latest_user_message: str):
        if call.name != "gmail_read_message":
            return original_execute(self, call, latest_user_message=latest_user_message)
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        return read_gmail_message(self.member.user_id, str((call.arguments or {}).get("message_id") or ""))

    def direct_tool_plan(text: str, pinned_project: str | None, web_enabled: bool):
        query = _gmail_search_query(text)
        if query and _wants_gmail_read(text):
            return tools.ToolPlan(calls=[
                tools.ToolCall(name="gmail_search", arguments={"query": query, "limit": 10}),
                tools.ToolCall(name="gmail_read_message", arguments={"message_id": "$step0.messages.0.id"}),
            ])
        return original_direct(text, pinned_project, web_enabled)

    def needs_model_tool_router(text: str, pinned_project: str | None, tools_enabled: bool, web_enabled: bool) -> bool:
        if tools_enabled and any(term in (text or "").lower() for term in ("gmail", "my email", "inbox")) and _wants_gmail_read(text):
            return True
        return original_needs(text, pinned_project, tools_enabled, web_enabled)

    tools.AuraToolRegistry.execute = execute
    core._direct_tool_plan = direct_tool_plan
    core._needs_model_tool_router = needs_model_tool_router
    _INSTALLED = True


__all__ = [
    "install_aura_gmail_extensions",
    "read_gmail_message",
    "_gmail_search_query",
    "_extract_payload",
    "_html_to_text",
]
