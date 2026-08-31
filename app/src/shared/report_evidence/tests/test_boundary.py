from __future__ import annotations

import ast
from pathlib import Path


def _imports(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.append(node.module)
    return tuple(modules)


def test_공용_근거계약은_core나_feature를_역으로_import하지_않는다() -> None:
    package = Path(__file__).resolve().parents[1]
    violations = [
        f"{path.name}:{module}"
        for path in package.glob("*.py")
        for module in _imports(path)
        if module.startswith(("src.core.", "src.features."))
    ]

    assert violations == []
