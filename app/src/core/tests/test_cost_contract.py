"""AI 비용 환율·원화 환산·운영 한도의 단일 정본 계약."""

from __future__ import annotations

import ast
from pathlib import Path

from src.core import paths, pricing
from src.features.budget import provider_budget
from src.features.pipeline import real
from src.features.posting_image import logic as posting_image
from src.features.sharelink.constants import (
    ADMIN_DAILY_BUDGET_KRW,
    PER_LINK_DAILY_BUDGET_KRW,
    PER_USER_DAILY_BUDGET_KRW,
)
from src.features.spanselect.constants import USAGE_MODEL_KEY


_HAIKU = "claude-haiku-4-5"
_PRODUCTION_ROOTS = (
    paths.PROJECT_ROOT / "app" / "src",
    paths.PROJECT_ROOT / "analysis_engine",
)


def _production_python_files() -> list[Path]:
    return sorted(
        path
        for root in _PRODUCTION_ROOTS
        for path in root.rglob("*.py")
        if "tests" not in path.parts
    )


def _defined_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return names


def test_환율변경은_admission_장부_OCR을_같이_이동시킨다(monkeypatch):
    monkeypatch.setattr(pricing, "AI_COST_KRW_PER_USD", 2000.0)
    expected = 12_000.0  # Haiku 입력 $1 + 출력 $5, 각 100만 token.

    admission = provider_budget.ProviderBudget(expected)
    reservation = admission.reserve_call(
        model=_HAIKU,
        input_tokens_upper=1_000_000,
        max_tokens=1_000_000,
    )
    ledger = real._step_usage_spent_krw(  # noqa: SLF001 - 비용 장부 경계 계약
        [
            {
                "usage": {
                    "in": 1_000_000,
                    "out": 1_000_000,
                    USAGE_MODEL_KEY: _HAIKU,
                }
            }
        ],
        model="사용하지-않는-기본값",
    )
    ocr = posting_image.usage_cost_krw(_HAIKU, 1_000_000, 1_000_000)

    assert reservation.estimated_krw == expected
    assert ledger == expected
    assert ocr == expected


def test_비용_환율과_원화환산_정의는_한곳뿐이다():
    definitions: dict[str, list[Path]] = {
        "AI_COST_KRW_PER_USD": [],
        "KRW_PER_USD": [],
        "_USD_TO_KRW": [],
    }
    cost_functions: list[Path] = []
    for path in _production_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = _defined_names(tree)
        for name in definitions:
            if name in names:
                definitions[name].append(path)
        if any(
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "usage_cost_krw"
            for node in ast.walk(tree)
        ):
            cost_functions.append(path)

    canonical = paths.PROJECT_ROOT / "app" / "src" / "core" / "pricing.py"
    assert definitions["AI_COST_KRW_PER_USD"] == [canonical]
    assert definitions["KRW_PER_USD"] == []
    assert definitions["_USD_TO_KRW"] == []
    assert cost_functions == [canonical]


def test_네_비용경로가_공통_원화환산을_사용한다():
    expected_calls = {
        paths.PROJECT_ROOT / "app" / "src" / "features" / "budget" / "provider_budget.py": "usage_cost_krw",
        paths.PROJECT_ROOT / "app" / "src" / "features" / "pipeline" / "real.py": "usage_cost_krw",
        paths.PROJECT_ROOT / "app" / "src" / "features" / "posting_image" / "logic.py": "usage_cost_krw",
        paths.PROJECT_ROOT / "analysis_engine" / "tools" / "run_pilot.py": "ai_pricing.usage_cost_krw",
    }
    for path, call_text in expected_calls.items():
        source = path.read_text(encoding="utf-8")
        assert f"{call_text}(" in source, f"공통 환산 호출 누락: {path}"


def test_영속원장밖_수동파일럿은_secret과_provider보다_먼저_실행을_거부한다():
    pilot = paths.PROJECT_ROOT / "analysis_engine" / "tools" / "run_pilot.py"
    tree = ast.parse(pilot.read_text(encoding="utf-8"), filename=str(pilot))
    main = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "main"
    )
    first = main.body[0]
    assert isinstance(first, ast.Raise)
    assert isinstance(first.exc, ast.Call)
    assert isinstance(first.exc.func, ast.Name)
    assert first.exc.func.id == "SystemExit"

    source = pilot.read_text(encoding="utf-8")
    assert source.index("raise SystemExit(PAID_EXECUTION_DISABLED_MESSAGE)") < source.index(
        "loaded = load_env()"
    )


def test_운영한도와_paid_phase_호출계약은_정본에_명시한_상한만_쓴다():
    assert PER_LINK_DAILY_BUDGET_KRW == 3000.0
    assert PER_USER_DAILY_BUDGET_KRW == 3000.0
    assert ADMIN_DAILY_BUDGET_KRW == 5000.0

    definitions: dict[str, list[Path]] = {
        "DAILY_BUDGET_KRW": [],
        "PER_LINK_DAILY_BUDGET_KRW": [],
        "PER_USER_DAILY_BUDGET_KRW": [],
    }
    production_calls: list[tuple[Path, ast.Call]] = []
    paid_runtime_path = paths.PROJECT_ROOT / "app" / "src" / "web" / "paid_runtime.py"
    paid_runtime_tree: ast.AST | None = None

    for path in _production_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        names = _defined_names(tree)
        for name in definitions:
            if name in names:
                definitions[name].append(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            called = (
                node.func.id
                if isinstance(node.func, ast.Name)
                else node.func.attr
                if isinstance(node.func, ast.Attribute)
                else ""
            )
            if called == "_begin_paid_phase":
                production_calls.append((path, node))
        if path == paid_runtime_path:
            paid_runtime_tree = tree

    link_canonical = (
        paths.PROJECT_ROOT
        / "app"
        / "src"
        / "features"
        / "sharelink"
        / "constants.py"
    )
    assert definitions["DAILY_BUDGET_KRW"] == []
    assert definitions["PER_LINK_DAILY_BUDGET_KRW"] == [link_canonical]
    assert definitions["PER_USER_DAILY_BUDGET_KRW"] == [link_canonical]
    assert paid_runtime_tree is not None

    begin = next(
        node
        for node in ast.walk(paid_runtime_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_begin_paid_phase"
    )
    cap_index = [arg.arg for arg in begin.args.kwonlyargs].index("cap_krw")
    assert begin.args.kw_defaults[cap_index] is None
    # 호출 지점 수는 OCR·식별·지연 single-flight owner처럼 제품 경계가
    # 늘 때 달라질 수 있다. 안전 계약은 개수가 아니라 «새 호출까지 전부
    # 통장별 cap을 명시하는가»이므로 아래 전수검사를 정본으로 삼는다.
    assert production_calls
    for path, call in production_calls:
        assert any(keyword.arg == "cap_krw" for keyword in call.keywords), (
            f"cap_krw를 명시하지 않은 production 호출: {path}:{call.lineno}"
        )
