from __future__ import annotations

from scripts.source_completeness_audit import audit_source


def _kinds(source: str) -> set[str]:
    return {item.kind for item in audit_source(source, relative_path="sample.py")}


def test_concrete_pass_and_ellipsis_are_rejected():
    assert "empty-implementation" in _kinds("def unfinished():\n    pass\n")
    assert "empty-implementation" in _kinds("async def unfinished():\n    ...\n")


def test_concrete_not_implemented_raise_is_rejected():
    assert "not-implemented" in _kinds(
        "def unfinished():\n    raise NotImplementedError('later')\n"
    )


def test_protocol_and_abstract_contracts_are_not_treated_as_placeholders():
    source = """
from abc import ABC, abstractmethod
from typing import Protocol

class Contract(ABC):
    @abstractmethod
    def run(self):
        raise NotImplementedError

class Shape(Protocol):
    def size(self):
        ...
"""
    assert audit_source(source, relative_path="contracts.py") == []


def test_unfinished_comments_are_rejected_but_explicit_audit_exception_is_allowed():
    assert "unfinished-comment" in _kinds("# TODO: implement this\nvalue = 1\n")
    assert audit_source(
        "# TODO: external protocol wording example  # source-audit: allow\nvalue = 1\n",
        relative_path="allowed.py",
    ) == []


def test_normal_concrete_implementation_passes():
    source = """
def total(values):
    return sum(values)

class Worker:
    def run(self):
        return True
"""
    assert audit_source(source, relative_path="complete.py") == []
