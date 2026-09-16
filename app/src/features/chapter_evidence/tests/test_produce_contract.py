from __future__ import annotations

import re

import pytest

from src.features.chapter_evidence.produce import produce_chapter_evidence_candidates
from src.features.chapter_evidence.tests.fixtures import (
    build_listed_fixture,
    make_attempt,
    make_document,
    make_fragment,
)
from src.shared.report_evidence.constants import CollectionState, EvidenceReadiness
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult


_REASON_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,100}$")


def test_아홉_장을_정책_순서로_돌려준다() -> None:
    fixture = build_listed_fixture()

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-listed",
        company_type="listed",
        **fixture,
    )

    assert tuple(candidate.section_id for candidate in candidates) == REQUIRED_EVIDENCE_SECTION_IDS
    assert len(candidates) == 9


def test_회사_식별자가_비어있으면_거부한다() -> None:
    fixture = build_listed_fixture()

    with pytest.raises(ValueError, match="회사 식별자"):
        produce_chapter_evidence_candidates(
            company_id="  ",
            company_type="listed",
            **fixture,
        )


def test_알수없는_회사유형은_거부한다() -> None:
    fixture = build_listed_fixture()

    with pytest.raises(ValueError, match="회사 유형"):
        produce_chapter_evidence_candidates(
            company_id="corp-listed",
            company_type="platypus",
            **fixture,
        )


def test_다른_회사_문서는_한_건도_섞이지_않는다() -> None:
    fixture = build_listed_fixture(company_id="corp-listed")
    foreign_document = make_document(
        company_id="corp-other",
        document_id="foreign-doc",
        source_kind="dart_business_report",
        exact_evidence_hashes=None,
    )
    foreign_fragment = make_fragment(
        company_id="corp-other",
        fragment_id="frag-foreign",
        document_id="foreign-doc",
        section_id="business_model",
        slot_id="business_model:revenue_model",
        text="다른 회사의 매출 구조 서술.",
        score_millis=999,
    )
    documents = [*fixture["documents"], foreign_document]
    fragments = [*fixture["fragments"], foreign_fragment]

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-listed",
        company_type="listed",
        documents=documents,
        fragments=fragments,
        attempts=fixture["attempts"],
    )

    for candidate in candidates:
        assert all(document.company_id == "corp-listed" for document in candidate.documents)
        assert "frag-foreign" not in {
            fragment.fragment_id for fragment in candidate.fragments
        }
        assert "document_company_mismatch:1" in candidate.reason_codes


def test_다른_회사_조각은_회사식별자_결속확인으로_걸러진다() -> None:
    # generation=8 — 조각 자신의 company_id가 대상 회사와 다르면
    # document_id·해시가 우연히 맞아떨어져도(가정) select.py가 먼저
    # 걸러내야 한다(1층 방어). 여기서는 대상 회사 자신의 실제 문서를
    # 가리키게 해 "document_id는 맞다"는 조건까지 갖췄는데도 company_id
    # 하나만으로 제외되는지 본다.
    fixture = build_listed_fixture(company_id="corp-listed")
    own_document_id = fixture["documents"][0]["document_id"]
    foreign_fragment = make_fragment(
        company_id="corp-other",
        fragment_id="frag-foreign-company",
        document_id=own_document_id,
        section_id="business_model",
        slot_id="business_model:revenue_model",
        text="다른 회사가 우리 문서 위에 심으려는 조각.",
        score_millis=999,
    )
    fragments = [*fixture["fragments"], foreign_fragment]

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-listed",
        company_type="listed",
        documents=fixture["documents"],
        fragments=fragments,
        attempts=fixture["attempts"],
    )

    business_model = next(
        candidate for candidate in candidates if candidate.section_id == "business_model"
    )
    assert "frag-foreign-company" not in {
        fragment.fragment_id for fragment in business_model.fragments
    }
    assert any(
        code.startswith("fragment_company_mismatch:")
        for code in business_model.reason_codes
    )


def test_다른_회사_시도는_슬롯을_확인된_것으로_만들지_못한다() -> None:
    # generation=8 — attempt도 company_id가 다르면 걸러진다. 대상 회사가
    # 이 슬롯을 실제로는 조회한 적이 없어야(UNKNOWN) 하는데, 다른 회사의
    # REQUIRED 정상 확인 기록이 «우리도 확인했다»로 위장하면 결함이다.
    fixture = build_listed_fixture(company_id="corp-listed")
    section_id = "future_strategy"
    fragments = [
        fragment for fragment in fixture["fragments"] if fragment["section_id"] != section_id
    ]
    attempts = [
        attempt
        for attempt in fixture["attempts"]
        if not all(slot_id.startswith(f"{section_id}:") for slot_id in attempt["slot_ids"])
    ]
    foreign_attempt = make_attempt(
        company_id="corp-other",
        attempt_id="attempt-foreign-future_strategy",
        source_kind="dart_business_report",
        slot_ids=collector_slots_for(section_id),
        state=CollectionState.MISSING.value,
        reason_code="dart_business_report_missing",
    )
    attempts.append(foreign_attempt)

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-listed",
        company_type="listed",
        documents=fixture["documents"],
        fragments=fragments,
        attempts=attempts,
    )

    future_strategy = next(
        candidate for candidate in candidates if candidate.section_id == section_id
    )
    assert future_strategy.candidate_readiness is EvidenceReadiness.UNKNOWN
    assert all(attempt.company_id == "corp-listed" for attempt in future_strategy.attempts)
    assert any(
        code.startswith("attempt_company_mismatch:")
        for code in future_strategy.reason_codes
    )


def test_장별_후보는_전부_같은_집합이_아니다() -> None:
    fixture = build_listed_fixture()

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-listed",
        company_type="listed",
        **fixture,
    )

    fragment_texts_by_section = {
        candidate.section_id: frozenset(fragment.text for fragment in candidate.fragments)
        for candidate in candidates
    }
    unique_sets = {frozenset(texts) for texts in fragment_texts_by_section.values()}
    # 아홉 장이 전부 같은 원문 뭉치를 받는다면 unique_sets 는 1개뿐일 것이다.
    assert len(unique_sets) > 1
    for candidate in candidates:
        assert all(fragment.section_id == candidate.section_id for fragment in candidate.fragments)


def test_생성된_사유코드는_기계코드_형식과_길이를_지킨다() -> None:
    fixture = build_listed_fixture()

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-listed",
        company_type="listed",
        **fixture,
    )

    for candidate in candidates:
        for code in candidate.reason_codes:
            assert _REASON_CODE.fullmatch(code), code
            assert len(code) <= 100


def test_완전한_수집결과는_모든_장이_ready다() -> None:
    fixture = build_listed_fixture()

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-listed",
        company_type="listed",
        **fixture,
    )

    assert all(
        candidate.candidate_readiness is EvidenceReadiness.READY for candidate in candidates
    )


def test_문서식별자가_충돌해도_타사_조각은_결속확인으로_걸러진다() -> None:
    # 겹마다 따로 확인한다 — company_id 결속(1층)이 맞아떨어져도(대상 회사
    # 값으로 위장했거나, 수집기가 실수로 엉뚱한 문서에 재사용한 조각이라도)
    # exact_evidence_hashes 결속(2층)이 혼자서 이 조각을 잡아야 한다. 여기서
    # company_id는 일부러 대상 회사와 같게 둬서, 1층이 아니라 2층이 실제로
    # 막는지 검증한다.
    fixture = build_listed_fixture(company_id="corp-listed")
    collided_document_id = fixture["documents"][0]["document_id"]
    foreign_fragment = make_fragment(
        company_id="corp-listed",
        fragment_id="frag-collided",
        document_id=collided_document_id,
        section_id="business_model",
        slot_id="business_model:revenue_model",
        text="원본 문서가 실제로는 내보내지 않은 조작된 원문.",
        score_millis=999,
    )
    fragments = [*fixture["fragments"], foreign_fragment]

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-listed",
        company_type="listed",
        documents=fixture["documents"],
        fragments=fragments,
        attempts=fixture["attempts"],
    )

    business_model = next(
        candidate for candidate in candidates if candidate.section_id == "business_model"
    )
    assert "frag-collided" not in {
        fragment.fragment_id for fragment in business_model.fragments
    }
    assert any(
        code.startswith("fragment_not_bound_to_document:")
        for code in business_model.reason_codes
    )


def test_잘못된_형식의_문서_입력은_한국어_예외로_남는다() -> None:
    fixture = build_listed_fixture()
    broken_documents = [{**fixture["documents"][0], "source_tier": "TIER_9_UNKNOWN"}]

    with pytest.raises(ValueError, match="문서 출처 등급"):
        produce_chapter_evidence_candidates(
            company_id="corp-listed",
            company_type="listed",
            documents=broken_documents,
            fragments=fixture["fragments"],
            attempts=fixture["attempts"],
        )


def _previous_year_filing_fixture(
    *, company_id: str = "corp-listed", requirement: str
) -> dict[str, list]:
    """당기 공시 fixture에 직전 사업연도 공시 문서·조각을 한 벌 더 붙인다."""

    fixture = build_listed_fixture(company_id=company_id)
    previous_document_id = "dart-business-report-previous"
    previous_fragments = [
        make_fragment(
            company_id=company_id,
            fragment_id=f"frag-previous-{slot_id.split(':')[-1]}",
            document_id=previous_document_id,
            section_id="business_model",
            slot_id=slot_id,
            text=f"직전 사업연도 {slot_id} 관련 공식 원문 서술.",
        )
        for slot_id in collector_slots_for("business_model")
    ]
    previous_document = make_document(
        company_id=company_id,
        document_id=previous_document_id,
        source_kind="dart_business_report",
        title="사업보고서(직전 사업연도)",
        requirement=requirement,
        exact_evidence_hashes=tuple(
            str(fragment["text_sha256"]) for fragment in previous_fragments
        ),
    )
    return {
        "documents": [*fixture["documents"], previous_document],
        "fragments": [*fixture["fragments"], *previous_fragments],
        "attempts": fixture["attempts"],
    }


def test_직전사업연도_공시조각은_Writer입력에_남고_생산자계약도_통과한다() -> None:
    """2026-09-16 운영 실측 재현 — 한 문서의 조각 95개가 통째로 사라졌다.

    수집 엔진은 직전 사업연도 연차 공시를 필수 여부만 OPTIONAL로 낮춰 내보낸다.
    장 선택이 이 문서를 «낮은 신뢰 외부 페이지»로 세어 버리면 6장·8장이 빈 장이
    된다. 동시에 ``OfficialEvidenceCollectionResult``의 생산자 계약도 같은 조합을
    받아들여야 한다 — 한쪽만 고치면 회사 전체 생산이 예외로 죽는다.
    """

    fixture = _previous_year_filing_fixture(requirement="OPTIONAL")

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-listed",
        company_type="listed",
        **fixture,
    )

    business_model = next(
        candidate for candidate in candidates if candidate.section_id == "business_model"
    )
    fragment_ids = {fragment.fragment_id for fragment in business_model.fragments}
    assert {
        f"frag-previous-{slot_id.split(':')[-1]}"
        for slot_id in collector_slots_for("business_model")
    } <= fragment_ids
    assert "dart-business-report-previous" in {
        document.document_id for document in business_model.documents
    }
    assert not [
        code
        for candidate in candidates
        for code in candidate.reason_codes
        if code.startswith("low_trust_external_page_fragment_ignored:")
    ]

    # 생산자 계약(_validate_document_trust)까지 실제로 통과하는지 확인한다.
    OfficialEvidenceCollectionResult(company_id="corp-listed", candidates=candidates)


def test_필수여부를_올려말한_보조공시_문서는_사유이름과_함께_계속_버려진다() -> None:
    """음성 대조 — 완화는 낮춰 말한 연차 공시 한 방향뿐이다."""

    fixture = _previous_year_filing_fixture(requirement="REQUIRED")
    fixture["documents"][-1] = {
        **fixture["documents"][-1],
        "source_kind": "dart_semiannual_report",
    }

    candidates = produce_chapter_evidence_candidates(
        company_id="corp-listed",
        company_type="listed",
        **fixture,
    )

    business_model = next(
        candidate for candidate in candidates if candidate.section_id == "business_model"
    )
    assert not any(
        fragment.fragment_id.startswith("frag-previous-")
        for fragment in business_model.fragments
    )
    assert (
        "low_trust_external_page_fragment_ignored:3:formal_writer_trust_not_eligible"
        in business_model.reason_codes
    )
