from __future__ import annotations

import ast
from pathlib import Path


TOOLS_DIR = Path(__file__).resolve().parents[1] / "worker" / "agent" / "tools"
TOOL_FILES = [
    path for path in TOOLS_DIR.glob("*.py")
    if path.name != "__init__.py" and not path.name.startswith("_")
]


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return ""


def _is_tool_decorator(decorator: ast.AST) -> bool:
    if isinstance(decorator, ast.Call):
        decorator = decorator.func
    return _call_name(decorator) in {"repair_tool", "ops_tool"}


def _decorated_tool_functions(tree: ast.Module) -> list[ast.AsyncFunctionDef]:
    return [
        node for node in tree.body
        if isinstance(node, ast.AsyncFunctionDef)
        and any(_is_tool_decorator(decorator) for decorator in node.decorator_list)
    ]


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def test_agent_tool_modules_do_not_import_random() -> None:
    for path in TOOL_FILES:
        tree = _parse(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = {alias.name.partition(".")[0] for alias in node.names}
                assert "random" not in imported, f"{path} imports random"
            elif isinstance(node, ast.ImportFrom):
                assert node.module != "random", f"{path} imports from random"


def test_workflow_executed_tool_bodies_avoid_nondeterministic_calls() -> None:
    banned_calls = {
        "asyncio.sleep",
        "date.today",
        "datetime.now",
        "datetime.utcnow",
        "random.choice",
        "random.randint",
        "random.random",
        "random.uniform",
        "secrets.choice",
        "time.time",
        "uuid.uuid1",
        "uuid.uuid4",
    }
    for path in TOOL_FILES:
        tree = _parse(path)
        for tool_fn in _decorated_tool_functions(tree):
            for node in ast.walk(tool_fn):
                if isinstance(node, ast.Call):
                    call_name = _call_name(node.func)
                    assert call_name not in banned_calls, (
                        f"{path}:{tool_fn.name} uses nondeterministic {call_name}"
                    )
