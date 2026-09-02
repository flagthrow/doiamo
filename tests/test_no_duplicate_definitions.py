"""No module should define the same thing twice.

Python silently keeps the last definition, so a duplicated block passes every
test while leaving dead code behind. ors.py once carried 154 duplicated lines
this way — an edit that sliced text assuming one method came before another
when it came after, which appended the region instead of removing it.
"""
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MODULES = sorted(
    p for p in list((ROOT / "backend").rglob("*.py")) + list((ROOT / "tools").rglob("*.py"))
)


@pytest.mark.parametrize("path", MODULES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_duplicate_definitions(path):
    tree = ast.parse(path.read_text())

    def names(body):
        seen = []
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                seen.append(node.name)
        return seen

    scopes = [("module", tree.body)]
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            scopes.append((node.name, node.body))

    for scope, body in scopes:
        defined = names(body)
        duplicates = {n for n in defined if defined.count(n) > 1}
        assert not duplicates, "{} defines {} twice in {}".format(
            path.name, sorted(duplicates), scope
        )
