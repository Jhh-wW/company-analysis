"""장별 적합도 결정론 채점 시험 — 서로 다른 장에 서로 다른 조각을 주는지."""

from __future__ import annotations

from features.evidence_collection import constants as c
from features.evidence_collection.relevance import score_fragment_text
from features.evidence_collection.segment import segment_document
from features.evidence_collection.tests.fixtures.synthetic_documents import (
    LISTED_BUSINESS_REPORT_TEXT,
)


def test_신호가_없으면_None을_돌려준다() -> None:
    assert score_fragment_text("아무 신호도 없는 평범한 문장입니다.") is None


def test_매출_키워드가_있으면_business_model로_채점된다() -> None:
    score = score_fragment_text("당사의 매출은 주요 고객사에 대한 판매에서 발생한다.")
    assert score is not None
    assert score.section_id == "business_model"
    assert score.slot_id in c.CLAIM_SLOTS_BY_SECTION["business_model"]


def test_score_millis는_0_1000_범위다() -> None:
    score = score_fragment_text("매출 수익 판매 과금 수수료가 모두 걸리는 문장이다.")
    assert score is not None
    assert 0 <= score.score_millis <= 1000


def test_reason_codes는_형식_규정을_지킨다() -> None:
    score = score_fragment_text("경영진은 리더십을 발휘한다.", section_heading="I. 임원 및 직원")
    assert score is not None
    for code in score.reason_codes:
        assert c.REASON_CODE_PATTERN.fullmatch(code)


def test_제목_힌트가_맞으면_가산점을_받는다() -> None:
    without_hint = score_fragment_text("당사의 매출은 판매에서 발생한다.")
    with_hint = score_fragment_text(
        "당사의 매출은 판매에서 발생한다.", section_heading="II. 사업의 내용",
    )
    assert without_hint is not None and with_hint is not None
    assert with_hint.score_millis >= without_hint.score_millis
    assert any(code.startswith("heading_match:") for code in with_hint.reason_codes)


def test_실제_문서를_분할해서_채점하면_서로_다른_장에_서로_다른_조각이_배정된다() -> None:
    """전체 원문을 통째로 복사하는 게 아니라 장마다 다른 문단이 배정되는지."""
    candidates = segment_document(LISTED_BUSINESS_REPORT_TEXT)
    scored = [
        (candidate.text, score_fragment_text(candidate.text, candidate.section_heading))
        for candidate in candidates
    ]
    assigned = [(text, score.section_id) for text, score in scored if score is not None]

    section_ids = {section_id for _text, section_id in assigned}
    # 적어도 서로 다른 장 3개 이상에 조각이 갈렸는지 — «전체 원문 복사» 금지 요구사항.
    assert len(section_ids) >= 3

    texts_by_section: dict[str, set[str]] = {}
    for text, section_id in assigned:
        texts_by_section.setdefault(section_id, set()).add(text)
    # 어떤 두 장도 완전히 같은 조각 집합을 받지 않는다(원문 전체 복사가 아니라는 증거).
    seen: list[set[str]] = []
    for texts in texts_by_section.values():
        assert texts not in seen
        seen.append(texts)
