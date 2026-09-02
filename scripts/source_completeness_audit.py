#!/usr/bin/env python3
"""Fail closed when production Python contains unfinished implementation stubs.

This audit intentionally targets executable production Python rather than docs, tests,
examples or provider configuration. External-provider locks and fail-closed feature
availability are legitimate runtime states; concrete Python placeholders are not.
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

_MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX)\b", re.IGNORECASE)
_ALLOW_COMMENT = "source-audit: allow"
_PROTOCOL_BASES = {"Protocol"}
_ENTRYPOINTS = ("app.py", "worker.py", "vercel_bootstrap.py")


@dataclass(frozen=True, order=True)
class Finding:
    path: str
    line: int
    kind: str
    detail: str


def _name_tail(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _name_tail(node.func)
    return ""


def _without_docstring(body: list[ast.stmt]) -> list[ast.stmt]:
    if body and isinstance(body[0], ast.Expr):
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return body[1:]
    return body


def _is_empty_stub(body: list[ast.stmt]) -> bool:
    body = _without_docstring(body)
    if len(body) != 1:
        return False
    stmt = body[0]
    if isinstance(stmt, ast.Pass):
        return True
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis


def _is_not_implemented_raise(node: ast.Raise) -> bool:
    exc = node.exc
    if exc is None:
        return False
    if isinstance(exc, ast.Call):
        exc = exc.func
    return _name_tail(exc) == "NotImplementedError"


class _ImplementationVisitor(ast.NodeVisitor):
    def __init__(self, relative_path: str) -> None:
        self.relative_path = relative_path
        self.findings: list[Finding] = []
        self._protocol_depth = 0
        self._abstract_function_depth = 0

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        is_protocol = any(_name_tail(base) in _PROTOCOL_BASES for base in node.bases)
        self._protocol_depth += int(is_protocol)
        self.generic_visit(node)
        self._protocol_depth -= int(is_protocol)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        decorators = {_name_tail(item) for item in node.decorator_list}
        is_abstract = "abstractmethod" in decorators or self._protocol_depth > 0
        if not is_abstract and _is_empty_stub(node.body):
            self.findings.append(
                Finding(
                    self.relative_path,
                    node.lineno,
                    "empty-implementation",
                    f"{node.name} has only pass/ellipsis",
                )
            )
        self._abstract_function_depth += int(is_abstract)
        self.generic_visit(node)
        self._abstract_function_depth -= int(is_abstract)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        if (
            self._protocol_depth == 0
            and self._abstract_function_depth == 0
            and _is_not_implemented_raise(node)
        ):
            self.findings.append(
                Finding(
                    self.relative_path,
                    node.lineno,
                    "not-implemented",
                    "concrete production path raises NotImplementedError",
                )
            )
        self.generic_visit(node)


def audit_source(source: str, *, relative_path: str = "<memory>") -> list[Finding]:
    findings: list[Finding] = []
    try:
        tree = ast.parse(source, filename=relative_path)
    except SyntaxError as exc:
        findings.append(
            Finding(
                relative_path,
                int(exc.lineno or 1),
                "syntax-error",
                exc.msg,
            )
        )
        return findings

    visitor = _ImplementationVisitor(relative_path)
    visitor.visit(tree)
    findings.extend(visitor.findings)

    try:
        tokens = tokenize.generate_tokens(io.StringIO(source).readline)
        for token in tokens:
            if token.type != tokenize.COMMENT:
                continue
            text = token.string
            if _ALLOW_COMMENT in text.lower():
                continue
            match = _MARKER_RE.search(text)
            if match:
                findings.append(
                    Finding(
                        relative_path,
                        token.start[0],
                        "unfinished-comment",
                        f"production comment contains {match.group(1).upper()}",
                    )
                )
    except tokenize.TokenError as exc:
        findings.append(Finding(relative_path, 1, "tokenize-error", str(exc)))

    return sorted(set(findings))


def production_python_files(root: Path) -> Iterable[Path]:
    package = root / "aura_music_studio"
    if package.is_dir():
        yield from sorted(path for path in package.rglob("*.py") if path.is_file())
    for name in _ENTRYPOINTS:
        path = root / name
        if path.is_file():
            yield path


def audit_repository(root: Path) -> list[Finding]:
    root = root.resolve()
    findings: list[Finding] = []
    for path in production_python_files(root):
        relative = path.resolve().relative_to(root).as_posix()
        source = path.read_text(encoding="utf-8")
        findings.extend(audit_source(source, relative_path=relative))
    return sorted(set(findings))


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject unfinished production Python stubs")
    parser.add_argument("--root", type=Path, default=Path.cwd(), help="repository root")
    args = parser.parse_args()

    findings = audit_repository(args.root)
    if findings:
        print("FAIL: unfinished production-code markers were found:")
        for item in findings:
            print(f"- {item.path}:{item.line}: {item.kind}: {item.detail}")
        return 1

    print("PASS: production Python contains no concrete placeholder implementations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
