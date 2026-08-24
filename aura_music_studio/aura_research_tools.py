from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

from . import aura_agent_core as core
from . import aura_agent_tools as tools

_INSTALLED = False


RESEARCH_SPEC = tools.ToolSpec(
    name="web_research",
    description=(
        "Run a bounded multi-source public-web research pass through Aura's protected gateway: search, diversify domains, "
        "fetch several sources in parallel, and return source-labelled excerpts. Use for research/compare/investigate requests that need more than search snippets."
    ),
    arguments={
        "query": "Research question/search query.",
        "max_sources": "Optional 2-8 source pages; default 5.",
    },
    write=False,
    web=True,
)


def _select_diverse(results: list[dict], limit: int) -> list[dict]:
    selected: list[dict] = []
    domains: set[str] = set()
    deferred: list[dict] = []
    for row in results:
        url = str(row.get("url") or "")
        host = (urlparse(url).hostname or "").lower()
        if host and host not in domains:
            selected.append(row)
            domains.add(host)
        else:
            deferred.append(row)
        if len(selected) >= limit:
            return selected
    for row in deferred:
        selected.append(row)
        if len(selected) >= limit:
            break
    return selected


def _fetch_one(row: dict) -> dict:
    url = str(row.get("url") or "")
    try:
        fetched = tools._web_text(url)
        text = str(fetched.get("text") or "")
        return {
            "title": row.get("title") or url,
            "url": fetched.get("url") or url,
            "content": text[:12000] or str(row.get("content") or "")[:2500],
            "search_snippet": str(row.get("content") or "")[:2500],
            "content_type": fetched.get("content_type"),
            "cached": bool(fetched.get("cached")),
            "fetched": True,
            "truncated": bool(fetched.get("truncated")) or len(text) > 12000,
        }
    except Exception as exc:
        return {
            "title": row.get("title") or url,
            "url": url,
            "content": str(row.get("content") or "")[:2500],
            "search_snippet": str(row.get("content") or "")[:2500],
            "fetched": False,
            "fetch_error": f"{type(exc).__name__}: {exc}",
        }


def _research(registry, query: str, max_sources: int) -> list[dict]:
    clean = " ".join((query or "").split())[:2000]
    if not clean:
        raise ValueError("Research query is empty")
    limit = max(2, min(int(max_sources or 5), 8))
    gateway = tools.AuraWebGateway()
    search_rows = gateway.search(clean, limit=min(20, max(10, limit * 2)))
    selected = _select_diverse(search_rows, limit)
    if not selected:
        return []

    fetched_by_url: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(4, len(selected)), thread_name_prefix="aura-research") as pool:
        futures = {pool.submit(_fetch_one, row): str(row.get("url") or "") for row in selected}
        for future in as_completed(futures):
            url = futures[future]
            try:
                fetched_by_url[url] = future.result()
            except Exception as exc:
                fetched_by_url[url] = {"title": url, "url": url, "content": "", "fetched": False, "fetch_error": str(exc)}

    counter = int(getattr(registry, "_aura_source_counter", 0))
    output: list[dict] = []
    for row in selected:
        counter += 1
        item = fetched_by_url.get(str(row.get("url") or ""), _fetch_one(row))
        item["source_id"] = f"S{counter}"
        output.append(item)
    registry._aura_source_counter = counter
    return output


def _looks_research(text: str) -> bool:
    lower = (text or "").lower()
    return any(
        phrase in lower
        for phrase in (
            "deep research", "research this", "research the", "research and compare", "investigate", "compare sources",
            "look into", "do a detailed search", "do a detailed research", "research online", "research on the web",
        )
    )


def install_aura_research_tools() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    if RESEARCH_SPEC.name not in {item.name for item in tools.TOOL_SPECS}:
        tools.TOOL_SPECS.append(RESEARCH_SPEC)
        tools._SPEC_BY_NAME[RESEARCH_SPEC.name] = RESEARCH_SPEC

    original_execute = tools.AuraToolRegistry.execute
    original_direct = core._direct_tool_plan
    original_needs = core._needs_model_tool_router

    def execute(self, call: tools.ToolCall, *, latest_user_message: str):
        if call.name != "web_research":
            return original_execute(self, call, latest_user_message=latest_user_message)
        if not self.tools_enabled:
            raise PermissionError("Aura tools are disabled for this conversation")
        if not self.web_enabled:
            raise PermissionError("Web research is disabled for this conversation")
        args = dict(call.arguments or {})
        return _research(self, str(args.get("query") or latest_user_message), int(args.get("max_sources") or 5))

    def direct_tool_plan(text: str, pinned_project: str | None, web_enabled: bool):
        prior = original_direct(text, pinned_project, web_enabled)
        if prior is not None and not (_looks_research(text) and web_enabled):
            return prior
        if web_enabled and _looks_research(text):
            return tools.ToolPlan(calls=[tools.ToolCall(name="web_research", arguments={"query": text, "max_sources": 6})])
        return prior

    def needs_model_tool_router(text: str, pinned_project: str | None, tools_enabled: bool, web_enabled: bool) -> bool:
        if tools_enabled and web_enabled and _looks_research(text):
            return True
        return original_needs(text, pinned_project, tools_enabled, web_enabled)

    tools.AuraToolRegistry.execute = execute
    core._direct_tool_plan = direct_tool_plan
    core._needs_model_tool_router = needs_model_tool_router
    _INSTALLED = True


__all__ = ["install_aura_research_tools"]
