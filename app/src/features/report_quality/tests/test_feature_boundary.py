from __future__ import annotations

import ast
from pathlib import Path


def test_report_quality는_다른_feature를_직접_import하지_않는다() -> None:
    feature_dir = Path(__file__).resolve().parents[1]
    violations: list[str] = []
    for path in feature_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules.append(node.module)
            for module in modules:
                if module.startswith("src.features.") and not module.startswith(
                    "src.features.report_quality"
                ):
                    violations.append(f"{path.name}:{node.lineno}:{module}")

    assert violations == []
