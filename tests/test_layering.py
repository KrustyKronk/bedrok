"""The package layering is a rule, so it is a test rather than a good intention.

    surfaces -> ops -> engines -> ingest -> core

Library code may import downward and sideways, never upward. Before this was
enforced, one ingest module held both a command and helpers the engines needed,
so ingest reached up into engines and engines reached back down into ingest — a
cycle kept alive only by function-local imports, which is exactly the kind of
thing nobody notices until a move breaks it.
"""
import ast
import pathlib

import pytest

LAYERS = ["core", "ingest", "engines", "ops", "surfaces"]
RANK = {name: i for i, name in enumerate(LAYERS)}
PACKAGE = pathlib.Path(__file__).resolve().parents[1] / "bedrok"

# A CLI entry point is allowed to compose across layers: a command's main() may
# dispatch into another layer, and __main__ imports from everywhere by definition.
# That is dispatch, not a library dependency — the distinction is the point, and
# it is narrow on purpose. Anything outside a `main()` is a real violation.
DISPATCH_FUNCTIONS = {"main"}


def _module_files():
    return [p for p in PACKAGE.rglob("*.py") if "__pycache__" not in str(p)]


def _layer_of(path: pathlib.Path) -> str | None:
    parts = path.relative_to(PACKAGE).parts
    return parts[0] if parts[0] in RANK else None


def _imports(tree: ast.AST):
    """(imported_module, enclosing_function_name_or_None) for every import."""
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def enclosing(node):
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur.name
            cur = parents.get(cur)
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            yield node.module, enclosing(node)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, enclosing(node)


@pytest.mark.parametrize("path", _module_files(), ids=lambda p: str(p.name))
def test_no_upward_imports(path):
    mine = _layer_of(path)
    if mine is None:                       # __init__.py / __main__.py at the root
        return
    for module, func in _imports(ast.parse(path.read_text())):
        if not module.startswith("bedrok."):
            continue
        theirs = module.split(".")[1]
        if theirs not in RANK or RANK[theirs] <= RANK[mine]:
            continue
        if func in DISPATCH_FUNCTIONS:
            continue
        pytest.fail(
            f"{path.relative_to(PACKAGE.parent)} is in `{mine}` and imports "
            f"`{module}` from `{theirs}`, which sits above it.\n"
            f"Allowed direction: {' -> '.join(LAYERS)}.\n"
            f"Either the import belongs in a lower layer, or this module is "
            f"filed in the wrong one.")


def test_core_depends_on_nothing_above_it():
    """Stated separately because core is the one that must stay clean: it is
    imported by every other layer, so an upward edge here is a guaranteed cycle."""
    offenders = []
    for path in _module_files():
        if _layer_of(path) != "core":
            continue
        for module, _func in _imports(ast.parse(path.read_text())):
            if module.startswith("bedrok.") and module.split(".")[1] in (
                    "ingest", "engines", "ops", "surfaces"):
                offenders.append(f"{path.name} -> {module}")
    assert not offenders, "core reaches upward: " + ", ".join(offenders)


def test_every_cli_command_resolves():
    """The dispatch table is a pile of strings; a move renames a module and
    nothing complains until someone runs that command."""
    import importlib

    from bedrok.__main__ import _COMMANDS
    for name, target in _COMMANDS.items():
        mod = importlib.import_module(target)
        assert hasattr(mod, "main"), f"`bedrok {name}` -> {target} has no main()"
