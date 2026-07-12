#!/usr/bin/env python3
"""Produce a reproducible call, dependency and public-contract architecture profile."""

from __future__ import annotations

import argparse
import ast
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


BACKEND_ROOT = Path(__file__).resolve().parents[1]
ORCHESTRATOR_PATH = BACKEND_ROOT / "app" / "orchestrator" / "orchestrator.py"
APP_ROOT = BACKEND_ROOT / "app"


def _orchestrator_methods() -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    tree = ast.parse(ORCHESTRATOR_PATH.read_text(encoding="utf-8-sig"), filename=str(ORCHESTRATOR_PATH))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "Orchestrator":
            return {
                child.name: child
                for child in node.body
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    raise RuntimeError("Orchestrator class not found")


def _delegation_target(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    if len(body) != 1 or not isinstance(body[0], ast.Return):
        return ""
    value = body[0].value
    if isinstance(value, ast.Await):
        value = value.value
    if not isinstance(value, ast.Call) or not isinstance(value.func, ast.Attribute):
        return ""
    parts = []
    cursor: ast.expr = value.func
    while isinstance(cursor, ast.Attribute):
        parts.append(cursor.attr)
        cursor = cursor.value
    if not isinstance(cursor, ast.Name) or cursor.id != "self":
        return ""
    return "self." + ".".join(reversed(parts))


def _module_name(path: Path) -> str:
    relative = path.relative_to(BACKEND_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imports(tree: ast.AST) -> set[str]:
    result: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            result.update(alias.name for alias in node.names if alias.name.startswith("app."))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("app."):
            result.add(node.module)
    return result


def _dependency_graph() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    graph: dict[str, set[str]] = defaultdict(set)
    importers: dict[str, set[str]] = defaultdict(set)
    modules = {_module_name(path) for path in APP_ROOT.rglob("*.py")}
    for path in APP_ROOT.rglob("*.py"):
        module = _module_name(path)
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for imported in _imports(tree):
            target = imported
            while target and target not in modules:
                target = target.rpartition(".")[0]
            if not target or target == module:
                continue
            graph[module].add(target)
            importers[target].add(module)
        graph.setdefault(module, set())
    return graph, importers


def _cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indexes: dict[str, int] = {}
    lowlinks: dict[str, int] = {}
    result: list[list[str]] = []

    def visit(node: str) -> None:
        nonlocal index
        indexes[node] = index
        lowlinks[node] = index
        index += 1
        stack.append(node)
        on_stack.add(node)
        for target in graph.get(node, set()):
            if target not in indexes:
                visit(target)
                lowlinks[node] = min(lowlinks[node], lowlinks[target])
            elif target in on_stack:
                lowlinks[node] = min(lowlinks[node], indexes[target])
        if lowlinks[node] != indexes[node]:
            return
        component = []
        while stack:
            member = stack.pop()
            on_stack.remove(member)
            component.append(member)
            if member == node:
                break
        if len(component) > 1:
            result.append(sorted(component))

    for node in sorted(graph):
        if node not in indexes:
            visit(node)
    return sorted(result)


def _external_private_accesses() -> list[str]:
    violations: list[str] = []
    roots = [BACKEND_ROOT / name for name in ("app/routers", "app/jobs", "scripts", "tests")]
    private_modules = ("app.orchestrator._", "app.llm_gateway._", "app.eval._")
    owner_names = {"orchestrator", "orch", "gateway", "harness"}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
            relative = path.relative_to(BACKEND_ROOT).as_posix()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith(private_modules):
                    violations.append(f"{relative}:{node.lineno}:private_module:{node.module}")
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr.startswith("_")
                    and isinstance(node.value, ast.Name)
                    and node.value.id in owner_names
                ):
                    violations.append(f"{relative}:{node.lineno}:private_attribute:{node.value.id}.{node.attr}")
    return sorted(set(violations))


def build_profile() -> dict[str, Any]:
    methods = _orchestrator_methods()
    references: Counter[str] = Counter()
    files: dict[str, set[str]] = {name: set() for name in methods}
    for path in BACKEND_ROOT.rglob("*.py"):
        if any(part in {".venv", "__pycache__"} for part in path.parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        except (OSError, SyntaxError, UnicodeError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Attribute):
                continue
            name = node.attr
            if name not in methods or name == "__init__":
                continue
            references[name] += 1
            files[name].add(path.relative_to(BACKEND_ROOT).as_posix())

    rows = []
    for name, node in sorted(methods.items(), key=lambda item: item[1].lineno):
        target = _delegation_target(node)
        rows.append(
            {
                "method": name,
                "line": node.lineno,
                "references": references[name],
                "reference_files": sorted(files[name]),
                "delegation_target": target,
                "facade_candidate": bool(target),
                "unreferenced_candidate": references[name] == 0 and name != "__init__",
            }
        )
    return {
        "schema_version": 2,
        "backend_root": str(BACKEND_ROOT),
        "orchestrator": str(ORCHESTRATOR_PATH.relative_to(BACKEND_ROOT)),
        "method_count": len(rows),
        "facade_candidates": sum(row["facade_candidate"] for row in rows),
        "unreferenced_candidates": sum(row["unreferenced_candidate"] for row in rows),
        "public_method_count": sum(not row["method"].startswith("_") for row in rows),
        "methods": rows,
    }


def build_architecture_profile() -> dict[str, Any]:
    profile = build_profile()
    graph, importers = _dependency_graph()
    cycles = _cycles(graph)
    profile.update(
        {
            "module_count": len(graph),
            "dependency_edges": sum(len(targets) for targets in graph.values()),
            "dependency_cycles": cycles,
            "dependency_cycle_count": len(cycles),
            "external_private_accesses": _external_private_accesses(),
            "change_fanout": [
                {"module": module, "importers": len(sources), "importer_modules": sorted(sources)}
                for module, sources in sorted(importers.items(), key=lambda item: (-len(item[1]), item[0]))[:20]
            ],
        }
    )
    return profile


def architecture_violations(profile: dict[str, Any]) -> list[str]:
    """Return release-blocking architecture contract violations."""
    violations: list[str] = []
    cycles = profile.get("dependency_cycles") or []
    private_accesses = profile.get("external_private_accesses") or []
    if cycles:
        violations.append(f"dependency_cycles:{len(cycles)}")
    if private_accesses:
        violations.append(f"external_private_accesses:{len(private_accesses)}")
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, help="Optional JSON output path")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero when dependency cycles or external private accesses are found",
    )
    args = parser.parse_args()
    profile = build_architecture_profile()
    profile["violations"] = architecture_violations(profile)
    payload = json.dumps(profile, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 1 if args.check and profile["violations"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
