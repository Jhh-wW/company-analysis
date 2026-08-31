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
