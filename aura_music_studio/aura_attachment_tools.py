from __future__ import annotations

from pathlib import Path

from . import aura_agent_core as core
from . import aura_agent_tools as tools
from .aura_chat_store import AuraChatStore
from .aura_project_bridge import PromoteAttachmentRequest, promote_attachment_for_member
from .aura_runtime_context import current_turn, latest_attachments

_INSTALLED = False


ATTACHMENT_SPECS = [
    tools.ToolSpec(
        name="inspect_current_attachments",
        description="List the files attached to the member's current Aura message without exposing private storage paths.",
        arguments={},
        write=False,
        web=False,
    ),
    tools.ToolSpec(
        name="promote_current_attachment",
        description=(
            "Add one attachment from the member's current message to the pinned project and Creative DNA with a rights record. "
            "Requires rights_confirmed=true AND explicit ownership/authorization wording in the member's own latest message."
        ),
        arguments={
            "attachment": "Optional attachment id or filename; omit only when exactly one file is attached to the current message.",
            "project_name": "Project name; omit when pinned.",
            "rights_confirmed": "Must be true only when the member explicitly confirmed ownership/authorization in this message.",
            "attestation": "Short rights attestation reflecting the member's own statement.",
            "usage": "Optional intended project use.",
        },
        write=True,
        web=False,
    ),
]


def _public(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "name": item.get("name"),
        "kind": item.get("kind"),
        "mime_type": item.get("mime_type"),
        "bytes": item.get("bytes"),
        "sha256": item.get("sha256"),
        "perceived": bool(item.get("extracted_text")),
        "metadata": item.get("metadata") or {},
    }


def _select(items: list[dict], selector: str | None) -> dict:
    if not items:
        raise ValueError("No files are attached to the current Aura message")
    clean = (selector or "").strip().lower()
    if not clean:
        if len(items) == 1:
            return items[0]
        raise ValueError("Multiple files are attached. Specify the attachment filename or id.")
    exact = [item for item in items if clean in {str(item.get("id") or "").lower(), str(item.get("name") or "").lower()}]
    if len(exact) == 1:
        return exact[0]
    partial = [item for item in items if clean in str(item.get("name") or "").lower()]
    if len(partial) == 1:
        return partial[0]
    if not partial:
        raise KeyError(f"No current attachment matches {selector!r}")
    raise ValueError("Attachment selector is ambiguous: " + ", ".join(str(item.get("name")) for item in partial[:12]))


def _rights_in_member_text(text: str) -> bool:
    lower = " ".join((text or "").lower().split())
    direct = any(phrase in lower for phrase in (
        "i own this", "i own the", "i have the rights", "i have rights", "i have permission", "i have authorization", "i have authorisation",
        "i am authorized", "i am authorised", "i'm authorized", "i'm authorised", "licensed to me", "i created this", "i made this",
    ))
    possessive = any(phrase in lower for phrase in (
        "this is my file", "this is my image", "this is my photo", "this is my audio", "this is my song", "this is my video",
        "this is my spreadsheet", "this is my document", "this is my csv", "this is my xlsx",
    ))
    return direct or possessive


def _promotion_requested(text: str) -> bool:
    lower = (text or "").lower()
    return any(phrase in lower for phrase in (
        "add this to", "add it to", "add the file to", "add the attachment to", "put this in", "put it in", "use this in the project",
        "use it in the project", "promote this", "save this to the project", "save it to the project",
    ))


def _wants_table_analysis(text: str, attachment: dict | None) -> bool:
    lower = (text or "").lower()
    suffix = Path(str((attachment or {}).get("name") or "")).suffix.lower()
    table_file = suffix in {".csv", ".tsv", ".xlsx", ".xlsm"}
    analysis = any(word in lower for word in ("analyze", "analyse", "summarize", "summarise", "statistics", "profile", "inspect the data", "analyse the data", "analyze the data"))
    return table_file and analysis


def install_aura_attachment_tools() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    for spec in ATTACHMENT_SPECS:
        if spec.name not in {item.name for item in tools.TOOL_SPECS}:
            tools.TOOL_SPECS.append(spec)
            tools._SPEC_BY_NAME[spec.name] = spec

    original_execute = tools.AuraToolRegistry.execute
    original_direct = core._direct_tool_plan
    original_needs = core._needs_model_tool_router

    def execute(self, call: tools.ToolCall, *, latest_user_message: str):
        if call.name not in {"inspect_current_attachments", "promote_current_attachment"}:
            return original_execute(self, call, latest_user_message=latest_user_message)
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        context = current_turn()
        if context is None or context.user_id != self.member.user_id:
            raise RuntimeError("Current Aura attachment context is unavailable")
        chat_store = AuraChatStore()
        items = latest_attachments(chat_store)
        if call.name == "inspect_current_attachments":
            return {"attachments": [_public(item) for item in items], "raw_storage_paths_exposed": False}

        args = dict(call.arguments or {})
        if not bool(args.get("rights_confirmed")):
            raise PermissionError("Aura cannot promote a chat attachment without rights_confirmed=true")
        if not _rights_in_member_text(latest_user_message):
            raise PermissionError("The member's latest message does not explicitly confirm ownership or authorization for this attachment")
        if not _promotion_requested(latest_user_message):
            raise PermissionError("The member's latest message does not explicitly ask Aura to add the attachment to the project")
        item = _select(items, str(args.get("attachment") or "") or None)
        project_name = str(args.get("project_name") or self.pinned_project or "").strip() or None
        body = PromoteAttachmentRequest(
            project_name=project_name,
            rights_confirmed=True,
            attestation=str(args.get("attestation") or "The member explicitly confirmed ownership or authorization in this Aura message."),
            usage=str(args.get("usage") or "creative reference and project source material"),
        )
        return promote_attachment_for_member(self.member, context.thread_id, item["id"], body, chat_store=chat_store)

    def direct_tool_plan(text: str, pinned_project: str | None, web_enabled: bool):
        # Promotion has to run before any analysis of the newly-added file, so it takes
        # precedence over the table-analysis router when the same member turn asks for both.
        if pinned_project and _promotion_requested(text) and _rights_in_member_text(text):
            items = latest_attachments()
            selected = items[0] if len(items) == 1 else None
            args = {
                "project_name": pinned_project,
                "rights_confirmed": True,
                "attestation": "The member explicitly confirmed ownership or authorization in this Aura message.",
            }
            if selected:
                args["attachment"] = selected.get("id")
            calls = [tools.ToolCall(name="promote_current_attachment", arguments=args)]
            if selected and _wants_table_analysis(text, selected):
                calls.append(tools.ToolCall(name="analyze_project_table", arguments={"project_name": pinned_project, "table": selected.get("name")}))
            return tools.ToolPlan(calls=calls)
        prior = original_direct(text, pinned_project, web_enabled)
        if prior is not None:
            return prior
        lower = (text or "").lower()
        if any(phrase in lower for phrase in ("what files did i attach", "what did i attach", "list these attachments", "inspect these attachments")):
            return tools.ToolPlan(calls=[tools.ToolCall(name="inspect_current_attachments", arguments={})])
        return None

    def needs_model_tool_router(text: str, pinned_project: str | None, tools_enabled: bool, web_enabled: bool) -> bool:
        if tools_enabled and (_promotion_requested(text) or "attachment" in (text or "").lower()):
            return True
        return original_needs(text, pinned_project, tools_enabled, web_enabled)

    tools.AuraToolRegistry.execute = execute
    core._direct_tool_plan = direct_tool_plan
    core._needs_model_tool_router = needs_model_tool_router
    _INSTALLED = True


__all__ = ["install_aura_attachment_tools", "_rights_in_member_text", "_promotion_requested"]
