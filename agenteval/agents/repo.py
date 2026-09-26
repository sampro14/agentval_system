"""A compact, model-readable view of a workspace, shared by the agents that need to understand the code."""

from __future__ import annotations

import ast
import re
from pathlib import Path

from agenteval.execution.workspace import MAX_FILE_BYTES, list_files


def python_symbols(source: str) -> list[str]:
    """Top-level functions (with argument names), classes and UPPER_CASE constants."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    symbols = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            symbols.append(f"{node.name}({', '.join(a.arg for a in node.args.args)})")
        elif isinstance(node, ast.ClassDef):
            bases = ", ".join(ast.unparse(b) for b in node.bases)
            symbols.append(f"class {node.name}({bases})" if bases else f"class {node.name}")
        elif isinstance(node, ast.Assign):
            symbols.extend(t.id for t in node.targets if isinstance(t, ast.Name) and t.id.isupper())
    return symbols


def describe_file(relative: str, text: str) -> str:
    if relative.endswith(".py"):
        if Path(relative).name.startswith("test_"):
            count = len(re.findall(r"^\s*(?:async )?def test_", text, flags=re.MULTILINE))
            return f"{relative}: {count} tests"
        symbols = python_symbols(text)
        return f"{relative}: {'; '.join(symbols)}" if symbols else relative
    if relative.endswith(".sql"):
        facts = [f"creates table {t}" for t in re.findall(r"CREATE TABLE (\w+)", text)]
        facts += [f"adds column {c} to {t}" for t, c in re.findall(r"ALTER TABLE (\w+) ADD COLUMN (\w+)", text)]
        return f"{relative}: {', '.join(facts)}" if facts else relative
    if relative.endswith(".html"):
        ids = re.findall(r'\bid="([^"]+)"', text)
        return f"{relative}: element ids {', '.join('#' + i for i in ids)}" if ids else relative
    return relative


def empty_files(workspace: Path) -> set[str]:
    return {relative for relative in list_files(workspace) if (workspace / relative).stat().st_size == 0}


def summarize_repo(workspace: Path) -> str:
    """Every file with what it contains. Empty and oversized files are labelled as such."""
    lines = []
    for relative in list_files(workspace):
        size = (workspace / relative).stat().st_size
        if size == 0:
            lines.append(f"- {relative}: (empty file)")
        elif size > MAX_FILE_BYTES:
            lines.append(f"- {relative}: (large file, {size:,} bytes)")
        else:
            text = (workspace / relative).read_text(errors="replace")
            lines.append(f"- {describe_file(relative, text)}")
    return "\n".join(lines) if lines else "(empty repository)"
