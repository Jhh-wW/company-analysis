from __future__ import annotations

from dataclasses import dataclass

from src.shared.report_quality.comparison_claims import (
    COMPARISON_TARGET_SLOT,
    comparison_target_claim,
    comparison_target_source_problems,
    expected_comparison_context_claim,
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
