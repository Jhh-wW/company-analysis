from __future__ import annotations

from dataclasses import dataclass, replace

from src.shared.report_quality.comparison_claims import (
    COMPARISON_TARGET_SLOT,
    comparison_target_claim,
    comparison_target_source_problems,
    expected_comparison_context_claim,
    stated_differentiator_program_problems,
)
from src.shared.report_quality.constants import (
    STATED_DIFFERENTIATOR_CLAIM_TYPE,
    STRICT_FACTUAL_CLAIM_TYPES,
)


@dataclass(frozen=True)
class _ContextFact:
    claim_slot: str
    state_evidence: str
    comparison_target: str
    comparison_metric: str = "영업이익률"
    comparator_source_id: str = "comparison-source"


@dataclass(frozen=True)
class _ComparatorSource:
    source_id: str
    publisher: str


@dataclass(frozen=True)
class _StatedDifferentiatorFact:
    legal_entity: str = "주식회사 알파"
    claim: str = "주식회사 알파는 세계 최초 센서를 독자 개발했다고 밝혔다."
    claim_slot: str = "competitive_position:stated_differentiator"
    claim_type: str = "stated_differentiator"
    source_id: str = "official-source"
    source_publisher: str = "알파 주식회사"
    source_url: str = "https://alpha.example/news/1"
    source_document_id: str = "news-1"
    source_date: str = "2026-09-05"
    comparison_judgment: str = ""


def test_회사차별점_타입은_FULL_출고_사실타입으로_등록된다() -> None:
    assert STATED_DIFFERENTIATOR_CLAIM_TYPE in STRICT_FACTUAL_CLAIM_TYPES
    assert _StatedDifferentiatorFact().claim_type == STATED_DIFFERENTIATOR_CLAIM_TYPE


def test_비교대상_정본문장은_원문전체를_복사하지않는다() -> None:
    evidence = (
        "주식회사 알파는 주식회사 베타와 경쟁한다. "
        "이 뒤 문장은 보고서에 복사하면 안 되는 긴 공식 설명이다."
    )

    claim = comparison_target_claim(
        evidence_text=evidence,
        comparison_target="주식회사 베타",
    )

    assert claim == (
        "공식 원문에서 동종 비교 단서(대상 표기: 주식회사 베타, "
        "관계 표현: 와 경쟁한다)를 확인했으며, DART에서 확인한 비교 법인은 "
        "주식회사 베타이다."
    )
    assert evidence not in claim
    assert "이 뒤 문장" not in claim


def test_비교대상_claim은_구조필드에서_같은값으로_재생성된다() -> None:
    fact = _ContextFact(
        claim_slot=COMPARISON_TARGET_SLOT,
        state_evidence="Beta competes directly with us.",
        comparison_target="Beta",
    )

    assert expected_comparison_context_claim(fact) == (
        "공식 원문에서 동종 비교 단서(대상 표기: beta, 관계 표현: "
        "competes directly with)를 확인했으며, DART에서 확인한 비교 법인은 Beta이다."
    )


def test_공식원문에_없는_비교대상은_정본문장을_만들지않는다() -> None:
    assert comparison_target_claim(
        evidence_text="Alpha competes directly with Beta.",
        comparison_target="Gamma",
    ) == ""


def test_비교대상은_법인표지만_다른_같은_비교사_Source와_결속된다() -> None:
    fact = _ContextFact(
        claim_slot=COMPARISON_TARGET_SLOT,
        state_evidence="알파는 베타와 경쟁한다.",
        comparison_target="(주)베타",
    )

    assert comparison_target_source_problems(
        fact,
        _ComparatorSource("comparison-source", "베타 주식회사"),
    ) == ()


def test_원문에_우연히_나온_단어는_비교법인명으로_바꿀수없다() -> None:
    fact = _ContextFact(
        claim_slot=COMPARISON_TARGET_SLOT,
        state_evidence="알파는 베타와 경쟁한다.",
        comparison_target="경쟁",
    )

    assert comparison_target_source_problems(
        fact,
        _ComparatorSource("comparison-source", "주식회사 베타"),
    ) == ("비교 대상 법인명이 비교사 Source 발행 법인과 다릅니다",)


def test_비교사_Source_ID도_구조필드와_정확히_같아야한다() -> None:
    fact = _ContextFact(
        claim_slot=COMPARISON_TARGET_SLOT,
        state_evidence="알파는 베타와 경쟁한다.",
        comparison_target="베타",
    )

    assert comparison_target_source_problems(
        fact,
        _ComparatorSource("other-source", "베타"),
    ) == ("비교사 Source ID가 구조 필드와 다릅니다",)


def test_회사차별점은_회사주어_날짜_공식출처가_있으면_통과한다() -> None:
    assert stated_differentiator_program_problems(
        (_StatedDifferentiatorFact(),)
    ) == ()


def test_회사차별점의_별도_한계문장도_같은_공식출처로_통과한다() -> None:
    fact = replace(
        _StatedDifferentiatorFact(),
        claim="주식회사 알파가 밝힌 표현만 옮겼으며 타사 비교 판정은 포함하지 않습니다.",
        claim_slot="competitive_position:limitation",
    )

    assert stated_differentiator_program_problems((fact,)) == ()


def test_회사차별점의_판정어휘와_출처누락은_각각_거절한다() -> None:
    problems = stated_differentiator_program_problems(
        (
            _StatedDifferentiatorFact(
                claim="주식회사 알파는 경쟁력이 더 낫다고 밝혔다.",
                source_publisher="",
                source_url="",
                source_document_id="",
                source_date="",
            ),
        )
    )

    assert "회사 차별점에 작성자의 우열 판정이 섞였습니다" in problems
    assert "회사 차별점에 발표일이 없습니다" in problems
    assert "회사 차별점에 회사 공식 출처가 없습니다" in problems


def test_회사차별점의_구조화_우열판정도_거절한다() -> None:
    problems = stated_differentiator_program_problems(
        (_StatedDifferentiatorFact(comparison_judgment="competitive_advantage"),)
    )

    assert problems == ("회사 차별점에 작성자의 우열 판정이 섞였습니다",)
