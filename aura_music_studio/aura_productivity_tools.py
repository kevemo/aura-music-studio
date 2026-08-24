from __future__ import annotations

import ast
import math
import operator
import re
import statistics
from typing import Any

from . import aura_agent_core as core
from . import aura_agent_tools as tools

_INSTALLED = False


# Deliberately tiny arithmetic language. Aura never evals member input as Python.
_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "sqrt": math.sqrt,
    "ceil": math.ceil,
    "floor": math.floor,
    "min": min,
    "max": max,
}
_CONSTANTS = {"pi": math.pi, "e": math.e, "tau": math.tau}
_MAX_ABS = 1e100
_MAX_POWER = 1000


def _finite_number(value: Any) -> float | int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Calculator accepts numeric values only")
    if not math.isfinite(float(value)) or abs(float(value)) > _MAX_ABS:
        raise ValueError("Calculator result is outside the allowed finite range")
    return value


def _evaluate_node(node: ast.AST, *, depth: int = 0) -> float | int:
    if depth > 30:
        raise ValueError("Expression is too deeply nested")
    if isinstance(node, ast.Expression):
        return _evaluate_node(node.body, depth=depth + 1)
    if isinstance(node, ast.Constant):
        return _finite_number(node.value)
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _finite_number(_UNARY[type(node.op)](_evaluate_node(node.operand, depth=depth + 1)))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left = _evaluate_node(node.left, depth=depth + 1)
        right = _evaluate_node(node.right, depth=depth + 1)
        if isinstance(node.op, ast.Pow) and abs(float(right)) > _MAX_POWER:
            raise ValueError("Exponent exceeds the calculator safety limit")
        return _finite_number(_BINARY[type(node.op)](left, right))
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCTIONS:
        if node.keywords or len(node.args) > 50:
            raise ValueError("Unsupported calculator function arguments")
        values = [_evaluate_node(arg, depth=depth + 1) for arg in node.args]
        return _finite_number(_FUNCTIONS[node.func.id](*values))
    raise ValueError("Expression contains unsupported syntax")


def safe_calculate(expression: str) -> dict:
    clean = (expression or "").strip()
    if not clean or len(clean) > 500:
        raise ValueError("Calculator expression must contain 1-500 characters")
    try:
        tree = ast.parse(clean, mode="eval")
    except SyntaxError as exc:
        raise ValueError("Invalid calculator expression") from exc
    result = _evaluate_node(tree)
    return {"expression": clean, "result": result, "engine": "Aura safe arithmetic", "code_execution": False}


def _numeric_values(raw: Any) -> list[float]:
    if isinstance(raw, str):
        parts = [item for item in re.split(r"[\s,;|]+", raw.strip()) if item]
    elif isinstance(raw, (list, tuple)):
        parts = list(raw)
    else:
        raise ValueError("Statistics values must be a list or a comma/space separated string")
    if not parts or len(parts) > 10000:
        raise ValueError("Statistics requires between 1 and 10,000 values")
    values: list[float] = []
    for item in parts:
        try:
            value = float(item)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Statistics contains a non-numeric value: {item!r}") from exc
        if not math.isfinite(value) or abs(value) > _MAX_ABS:
            raise ValueError("Statistics values must be finite numbers")
        values.append(value)
    return values


def statistics_summary(raw: Any) -> dict:
    values = _numeric_values(raw)
    ordered = sorted(values)
    count = len(values)
    q1 = ordered[max(0, round((count - 1) * 0.25))]
    q3 = ordered[max(0, round((count - 1) * 0.75))]
    result = {
        "count": count,
        "sum": math.fsum(values),
        "mean": statistics.fmean(values),
        "median": statistics.median(values),
        "minimum": ordered[0],
        "maximum": ordered[-1],
        "q1_approx": q1,
        "q3_approx": q3,
        "range": ordered[-1] - ordered[0],
    }
    if count >= 2:
        result["sample_stdev"] = statistics.stdev(values)
        result["population_stdev"] = statistics.pstdev(values)
    return result


def _spec(name: str, description: str, arguments: dict[str, str]):
    return tools.ToolSpec(name=name, description=description, arguments=arguments, write=False, web=False)


PRODUCTIVITY_SPECS = [
    _spec(
        "calculator",
        "Evaluate arithmetic safely without executing Python or shell code. Supports + - * / // % **, parentheses, pi/e/tau, abs, round, sqrt, ceil, floor, min and max.",
        {"expression": "Arithmetic expression only, e.g. (1250*0.2)+47.50."},
    ),
    _spec(
        "statistics",
        "Calculate descriptive statistics for up to 10,000 numeric values.",
        {"values": "Numeric list or comma/space separated numeric string."},
    ),
]


def _extract_calculation(text: str) -> str | None:
    clean = (text or "").strip()
    patterns = [
        r"^(?:calculate|compute|work out)\s+(.+?)[?.!]*$",
        r"^what(?:'s| is)\s+([0-9piertau+\-*/%()., _]+?)[?.!]*$",
    ]
    for pattern in patterns:
        match = re.match(pattern, clean, flags=re.I | re.S)
        if match:
            expression = match.group(1).strip().replace("^", "**")
            expression = re.sub(r"(?<=\d),(?=\d{3}(?:\D|$))", "", expression)
            if expression:
                return expression[:500]
    if re.fullmatch(r"[0-9piertau+\-*/%()., _]+", clean, flags=re.I) and any(ch.isdigit() for ch in clean):
        return clean.replace("^", "**")[:500]
    return None


def _extract_statistics(text: str) -> str | None:
    clean = (text or "").strip()
    match = re.search(
        r"(?:mean|average|median|statistics|standard deviation|stdev)\s+(?:of|for)\s+([0-9eE+\-.,;\s]+)$",
        clean,
        flags=re.I,
    )
    return match.group(1).strip() if match else None


def _source_wrap(result: Any, *, start: int = 0) -> tuple[Any, int]:
    if not isinstance(result, list):
        return result, start
    rows = []
    counter = start
    for item in result:
        if not isinstance(item, dict):
            continue
        counter += 1
        row = dict(item)
        row["source_id"] = f"S{counter}"
        rows.append(row)
    return rows, counter


def source_records(tool_results: list[dict]) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for run in tool_results or []:
        if not run.get("ok"):
            continue
        name = run.get("tool")
        result = run.get("result")
        items = result if name == "web_search" and isinstance(result, list) else [result] if name == "web_fetch" and isinstance(result, dict) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            rows.append(
                {
                    "source_id": str(item.get("source_id") or f"S{len(rows) + 1}"),
                    "title": " ".join(str(item.get("title") or item.get("url") or "Source").split())[:300],
                    "url": url,
                    "snippet": " ".join(str(item.get("content") or "").split())[:600],
                }
            )
    return rows


def source_markdown(tool_results: list[dict]) -> str:
    records = source_records(tool_results)
    if not records:
        return ""
    lines = ["Sources:"]
    for item in records[:12]:
        lines.append(f"- [{item['source_id']}] {item['title']} — {item['url']}")
    return "\n".join(lines)


def _append_sources(text: str, tool_results: list[dict]) -> str:
    block = source_markdown(tool_results)
    if not block:
        return text
    # Always append the verified retrieval list. Inline citations are still useful, but the
    # source trail must not disappear just because a small local model forgot to cite them.
    return text.rstrip() + "\n\n" + block


def install_aura_productivity_tools() -> None:
    global _INSTALLED
    if _INSTALLED:
        return

    known = {item.name for item in tools.TOOL_SPECS}
    for spec in PRODUCTIVITY_SPECS:
        if spec.name not in known:
            tools.TOOL_SPECS.append(spec)
            tools._SPEC_BY_NAME[spec.name] = spec

    original_execute = tools.AuraToolRegistry.execute
    original_direct = core._direct_tool_plan
    original_needs = core._needs_model_tool_router
    original_respond = core.AuraAgent.respond

    def execute(self, call: tools.ToolCall, *, latest_user_message: str):
        if call.name == "calculator":
            if not self.tools_enabled:
                raise PermissionError("Aura tools are disabled for this conversation")
            return safe_calculate(str((call.arguments or {}).get("expression") or ""))
        if call.name == "statistics":
            if not self.tools_enabled:
                raise PermissionError("Aura tools are disabled for this conversation")
            return statistics_summary((call.arguments or {}).get("values"))
        result = original_execute(self, call, latest_user_message=latest_user_message)
        if call.name == "web_search":
            wrapped, counter = _source_wrap(result, start=int(getattr(self, "_aura_source_counter", 0)))
            self._aura_source_counter = counter
            return wrapped
        if call.name == "web_fetch" and isinstance(result, dict):
            counter = int(getattr(self, "_aura_source_counter", 0)) + 1
            self._aura_source_counter = counter
            value = dict(result)
            value["source_id"] = f"S{counter}"
            return value
        return result

    def direct_tool_plan(text: str, pinned_project: str | None, web_enabled: bool):
        prior = original_direct(text, pinned_project, web_enabled)
        if prior is not None:
            return prior
        expression = _extract_calculation(text)
        if expression:
            return tools.ToolPlan(calls=[tools.ToolCall(name="calculator", arguments={"expression": expression})])
        values = _extract_statistics(text)
        if values:
            return tools.ToolPlan(calls=[tools.ToolCall(name="statistics", arguments={"values": values})])
        return None

    def needs_model_tool_router(text: str, pinned_project: str | None, tools_enabled: bool, web_enabled: bool) -> bool:
        if tools_enabled and (_extract_calculation(text) or _extract_statistics(text)):
            return True
        return original_needs(text, pinned_project, tools_enabled, web_enabled)

    def respond(self, *args, **kwargs):
        result = original_respond(self, *args, **kwargs)
        tool_results = result.get("tool_runs") or []
        message = result.get("message") or {}
        content = str(message.get("content") or "")
        enriched = _append_sources(content, tool_results)
        if enriched != content and message.get("id"):
            with self.store._connect() as con:
                con.execute("UPDATE aura_chat_messages SET content=? WHERE id=?", (enriched, message["id"]))
            message = {**message, "content": enriched}
            result["message"] = message
            result["sources"] = source_records(tool_results)
        return result

    tools.AuraToolRegistry.execute = execute
    core._direct_tool_plan = direct_tool_plan
    core._needs_model_tool_router = needs_model_tool_router
    core.AuraAgent.respond = respond
    core.AURA_CORE_SYSTEM += """

Research/source contract:
- Web-search results may include stable source_id values such as S1, S2 and S3. When a factual claim materially relies on a web result, cite its source id inline in square brackets, e.g. [S1].
- Never invent a source id, URL, title, quotation or publication detail. If retrieved evidence is weak or conflicting, say so.
- Calculator/statistics tool results are authoritative for the arithmetic they actually returned; do not replace them with mental arithmetic when a tool result is available.
"""
    _INSTALLED = True


__all__ = [
    "install_aura_productivity_tools",
    "safe_calculate",
    "statistics_summary",
    "source_markdown",
    "source_records",
]
