from __future__ import annotations

import re

import pytest

from src.features.chapter_evidence.produce import produce_chapter_evidence_candidates
from src.features.chapter_evidence.tests.fixtures import (
    build_listed_fixture,
    make_document,
    make_fragment,
)
from src.shared.report_evidence.constants import EvidenceReadiness
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS


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
    )
    foreign_fragment = make_fragment(
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
