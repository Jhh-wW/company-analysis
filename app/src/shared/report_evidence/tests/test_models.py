"""``EvidenceFragment.covered_slot_ids`` 계약을 단위로 잠근다.

한 원문 범위가 같은 장의 여러 의미 칸에 답할 수 있게 됐다
(collector 재생 조각1, root d00e538 + 조각1 델타). 이 계약이 깨지면
``build_section_bundle``(logic.py)이 조용히 커버리지를 놓친다.
"""

from __future__ import annotations

import hashlib

import pytest

from src.shared.report_evidence.models import EvidenceFragment


_FRAGMENT_TEXT = "회사는 공식 제품을 고객에게 직접 판매해 수익을 얻습니다."
_FRAGMENT_SHA256 = hashlib.sha256(_FRAGMENT_TEXT.encode("utf-8")).hexdigest()


def _fragment(**overrides: object) -> EvidenceFragment:
    fields: dict[str, object] = {
        "company_id": "corp-1",
        "fragment_id": "fragment-1",
        "document_id": "doc-1",
        "location": "본문 1문단",
        "text_sha256": _FRAGMENT_SHA256,
        "text": _FRAGMENT_TEXT,
        "section_id": "business_model",
        "slot_id": "business_model",
        "score_millis": 900,
        "reason_codes": ("official_direct_statement",),
    }
    fields.update(overrides)
    return EvidenceFragment(**fields)  # type: ignore[arg-type]


def test_covered_slot_ids를_생략하면_대표_의미칸_하나로_채워진다() -> None:
    fragment = _fragment(slot_id="business_model")

    assert fragment.covered_slot_ids == ("business_model",)


def test_covered_slot_ids로_한_조각이_여러_의미칸을_동시에_채운다() -> None:
    fragment = _fragment(
        slot_id="business_model",
        covered_slot_ids=("business_model", "revenue_source"),
    )

    assert fragment.covered_slot_ids == ("business_model", "revenue_source")


def test_covered_slot_ids는_대표_의미칸을_반드시_포함해야_한다() -> None:
    with pytest.raises(ValueError, match="대표 의미 칸"):
        _fragment(
            slot_id="business_model",
            covered_slot_ids=("revenue_source",),
        )


def test_covered_slot_ids는_중복될_수_없다() -> None:
    with pytest.raises(ValueError, match="근거 조각이 채우는 의미 칸"):
        _fragment(
            slot_id="business_model",
            covered_slot_ids=("business_model", "business_model"),
        )
