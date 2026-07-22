#!/usr/bin/env python3
"""Inventory Python symbols in large files for Autonomie V2 refactors.

Reports top-level functions and class methods with line ranges, sizes, and
simple outgoing call names. This is intentionally static and dependency-free so
it can run on the VPS before service restarts.
"""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from typing import Any, Iterable


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return None


def _calls_in(node: ast.AST) -> list[str]:
    calls: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call):
            name = _call_name(child.func)
            if name:
                calls.add(name)
    return sorted(calls)


def _symbol_record(path: Path, node: ast.FunctionDef | ast.AsyncFunctionDef, *, class_name: str | None = None) -> dict[str, Any]:
    end_line = getattr(node, "end_lineno", node.lineno)
    qualified = f"{class_name}.{node.name}" if class_name else node.name
    return {
        "file": str(path),
        "name": node.name,
        "qualified_name": qualified,
        "kind": "method" if class_name else "function",
        "start_line": node.lineno,
        "end_line": end_line,
        "line_count": end_line - node.lineno + 1,
        "calls": _calls_in(node),
    }


def inventory_file(path: Path) -> dict[str, Any]:
    source = path.read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(source, filename=str(path))
    symbols: list[dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            symbols.append(_symbol_record(path, node))
        elif isinstance(node, ast.ClassDef):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    symbols.append(_symbol_record(path, child, class_name=node.name))
    symbols.sort(key=lambda item: (item["start_line"], item["qualified_name"]))
    return {
        "path": str(path),
        "line_count": len(source.splitlines()),
        "symbol_count": len(symbols),
        "symbols": symbols,
    }


def build_inventory(paths: Iterable[Path]) -> dict[str, Any]:
    files = [inventory_file(path) for path in paths]
    return {
        "schema_version": 1,
        "files": files,
        "total_symbols": sum(file["symbol_count"] for file in files),
    }


def _format_text(inventory: dict[str, Any]) -> str:
    lines = [f"schema_version={inventory['schema_version']} total_symbols={inventory['total_symbols']}"]
    for file in inventory["files"]:
        lines.append(f"\n{file['path']} lines={file['line_count']} symbols={file['symbol_count']}")
        for symbol in file["symbols"]:
            calls = ",".join(symbol["calls"][:8])
            if len(symbol["calls"]) > 8:
                calls += ",…"
            lines.append(
                f"  {symbol['start_line']:>5}-{symbol['end_line']:<5} "
                f"{symbol['line_count']:>4}L {symbol['kind']:<8} {symbol['qualified_name']}"
                + (f" calls=[{calls}]" if calls else "")
            )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Inventory Python functions/methods by line range and size.")
    parser.add_argument("paths", nargs="+", help="Python files to inventory")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    parser.add_argument("--output", help="Write output to this path instead of stdout")
    args = parser.parse_args()

    inventory = build_inventory([Path(path) for path in args.paths])
    text = json.dumps(inventory, indent=2, ensure_ascii=False) + "\n" if args.json else _format_text(inventory)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
