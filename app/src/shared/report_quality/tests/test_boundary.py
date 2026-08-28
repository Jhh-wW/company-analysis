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


def test_shared_품질정본은_core나_feature를_역으로_import하지_않는다() -> None:
    package = Path(__file__).resolve().parents[1]
    violations = [
        f"{path.name}:{module}"
        for path in package.glob("*.py")
        for module in _imports(path)
        if module.startswith(("src.core.", "src.features."))
    ]

    assert violations == []


def test_소비feature는_옛_core우회경로를_쓰지_않는다() -> None:
    app_src = Path(__file__).resolve().parents[3]
    targets = (
        app_src / "features" / "composer" / "pipeline.py",
        app_src / "features" / "report_standard" / "publish.py",
    )
    forbidden = {
        "src.core.generation_quality",
        "src.core.numeric_validation",
    }

    assert [
        f"{path.name}:{module}"
        for path in targets
        for module in _imports(path)
        if module in forbidden
    ] == []


def test_옛_공개경로_facade도_shared_정본만_import한다() -> None:
    app_src = Path(__file__).resolve().parents[3]
    facades = (
        app_src / "core" / "generation_quality.py",
        app_src / "core" / "numeric_validation.py",
        *(app_src / "features" / "report_quality").glob("*.py"),
    )
    violations = [
        f"{path.as_posix()}:{module}"
        for path in facades
        for module in _imports(path)
        if module.startswith("src.")
        and not module.startswith("src.shared.report_quality.")
    ]

    assert violations == []
