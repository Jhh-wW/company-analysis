"""FULL 1차 품질 중단 진단에는 닫힌 코드와 장 ID만 남긴다."""

from __future__ import annotations

from copy import deepcopy

import pytest

from src.shared.report_quality.composition_diagnostics import observed_composition_steps


def _record() -> dict[str, object]:
    return {
        "step": "8_FULL1차_품질중단",
        "회복사유": "too_many_underfilled_sections",
        "품질코드": ["one_claim_sections", "low_semantic_coverage"],
        "안내문장": [],
        "한주장장": ["business_model"],
        "공개문장부족장": ["business_model"],
        "의미부족장": ["future_strategy", "operations_partners"],
        "안전문제수": 0,
        "안전유형": {},
    }


def test_닫힌_품질코드와_장만_남기고_원문과_식별자는_버린다() -> None:
    record = _record()
    record.update({"회사": "비공개 대상", "원문": "비공개 인용", "fact_id": "비공개 식별자"})
    assert observed_composition_steps([record]) == (_record(),)


@pytest.mark.parametrize(("field", "value"), (
    ("회복사유", "비공개 자유 문장"),
    ("품질코드", ["one_claim_sections", "비공개 코드"]),
    ("품질코드", ["one_claim_sections", "one_claim_sections"]),
    ("품질코드", []),
    ("안내문장", ["비공개 장"]),
    ("한주장장", ["summary"]),
    ("공개문장부족장", ["business_model", "business_model"]),
    ("의미부족장", "future_strategy"),
    ("안전문제수", True),
    ("안전유형", {"비공개 문제": 1}),
    ("안전유형", {"numeric_binding_missing": 1}),
))
def test_열린_값이나_서로_다른_집계는_기록_전체를_버린다(field: str, value: object) -> None:
    record = deepcopy(_record())
    record[field] = value
    assert observed_composition_steps([record]) == ()
