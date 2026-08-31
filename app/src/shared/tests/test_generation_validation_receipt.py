from __future__ import annotations

import ast
from pathlib import Path

from src.features.report_recovery import (
    GenerationValidationReceipt as RecoveryReceiptFacade,
)
from src.shared.generation_validation_receipt import GenerationValidationReceipt


def test_생성영수증은_복구기능이_아닌_shared가_정본이다() -> None:
    assert RecoveryReceiptFacade is GenerationValidationReceipt


def test_shared_생성영수증은_feature를_import하지않는다() -> None:
    module_path = (
        Path(__file__).resolve().parents[1]
        / "generation_validation_receipt.py"
    )
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    forbidden: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith(
            "src.features"
        ):
            forbidden.append(node.module or "")
        if isinstance(node, ast.Import):
            forbidden.extend(
                alias.name
                for alias in node.names
                if alias.name.startswith("src.features")
            )

    assert forbidden == []
