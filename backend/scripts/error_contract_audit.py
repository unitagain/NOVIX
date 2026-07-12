"""Static W6 gate for unsafe exception propagation and silent broad catches."""

from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Iterable


SAFE_FUNCTIONS = {
    "benchmark_failure",
    "classify_exception",
    "error_envelope",
    "record_degradation",
    "safe_error_code",
    "tool_error_text",
}
SAFE_METHODS = {"_handle_error"}
EXTERNAL_SINK_METHODS = {"append", "extend", "send_json", "send_text", "write_text", "write_bytes"}
OBSERVABILITY_METHODS = {
    "debug",
    "error",
    "exception",
    "increment",
    "info",
    "record",
    "warning",
}


def _call_name(node: ast.Call) -> str:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return ""


def _contains_unsafe_reference(node: ast.AST, name: str) -> bool:
    if isinstance(node, ast.Call) and _call_name(node) in SAFE_FUNCTIONS | SAFE_METHODS:
        return False
    if isinstance(node, ast.Name):
        return node.id == name
    return any(_contains_unsafe_reference(child, name) for child in ast.iter_child_nodes(node))


def _external_sink(node: ast.AST) -> bool:
    if isinstance(node, ast.Return):
        return True
    if isinstance(node, (ast.Assign, ast.AnnAssign)):
        targets: Iterable[ast.AST] = getattr(node, "targets", ())
        return any(isinstance(target, (ast.Attribute, ast.Subscript)) for target in targets)
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in EXTERNAL_SINK_METHODS
    )


def _is_broad(handler: ast.ExceptHandler) -> bool:
    return isinstance(handler.type, ast.Name) and handler.type.id in {"Exception", "BaseException"}


def _is_observed(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Call) and _call_name(node) in OBSERVABILITY_METHODS | SAFE_FUNCTIONS | SAFE_METHODS:
            return True
    return False


def _is_silent(handler: ast.ExceptHandler) -> bool:
    if not _is_broad(handler) or _is_observed(handler):
        return False
    meaningful = [node for node in handler.body if not isinstance(node, ast.Pass)]
    if not meaningful:
        return True
    return all(isinstance(node, (ast.Pass, ast.Continue)) for node in handler.body)


def audit(root: Path) -> dict[str, list[str]]:
    unsafe_sinks: list[str] = []
    silent_broad: list[str] = []
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        relative = path.relative_to(root).as_posix()
        for handler in (node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)):
            if handler.name:
                for node in ast.walk(handler):
                    if _external_sink(node) and _contains_unsafe_reference(node, handler.name):
                        unsafe_sinks.append(f"{relative}:{getattr(node, 'lineno', 0)}")
            if _is_silent(handler):
                silent_broad.append(f"{relative}:{handler.lineno}")
    return {"unsafe_external_sinks": sorted(set(unsafe_sinks)), "silent_broad_catches": sorted(set(silent_broad))}


def main() -> int:
    root = Path(__file__).resolve().parents[1] / "app"
    result = audit(root)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if any(result.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
