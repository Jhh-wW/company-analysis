"""검수 안내의 수치 증명 예시가 실제 결정론적 계약과 일치하는지 확인한다."""

from __future__ import annotations

from src.features.composer.grounding import grounding_problem
from src.features.composer.grounding_constants import (
    GROUNDING_GUIDE,
    GROUNDING_INVALID,
    NUMERIC_KEY,
)


def _entry(*, quote: str, metric: str = "매출액") -> dict[str, str]:
    return {
        "표현": "매출액 120억원",
        "항목": metric,
        "근거": "공시",
        "원문": quote,
        "원문항목": "매출액",
        "원문값": "120억원",
        "후보값": "120억원",
    }


def test_literal_example_passes_numeric_binding() -> None:
    assert "항목·후보값은 표현에 각각 그대로" in GROUNDING_GUIDE
    assert "원문항목·원문값은 같은 원문 인용" in GROUNDING_GUIDE
    assert "항목 헤더가 없는 숫자 행" in GROUNDING_GUIDE
    text = "매출액 120억원"
    proof = {"검증근거": {NUMERIC_KEY: [_entry(quote=text)]}}
    assert grounding_problem(text, {"공시": text}, proof) == ""


def test_synthesized_candidate_metric_is_rejected() -> None:
    text = "매출액 120억원"
    detail: dict[str, object] = {}
    proof = {"검증근거": {NUMERIC_KEY: [_entry(quote=text, metric="가상 매출액")]}}
    assert grounding_problem(text, {"공시": text}, proof, detail=detail) == GROUNDING_INVALID
    assert detail["stage"] == "expression_not_in_candidate"


def test_missing_source_header_is_rejected() -> None:
    text = "매출액 120억원"
    quote = "120억원"
    detail: dict[str, object] = {}
    proof = {"검증근거": {NUMERIC_KEY: [_entry(quote=quote)]}}
    assert grounding_problem(text, {"공시": quote}, proof, detail=detail) == GROUNDING_INVALID
    assert detail["stage"] == "source_value_scope"
