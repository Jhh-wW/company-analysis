"""7차 조사 탐침의 뒤집힘(ADR 0005) — 정상 FULL 후보에 연도 토큰 하나를 넣는다.

2026-09-24 조사 탐침: 안전을 통과하는 45 claim·8 문서 FULL 후보의 확인 산문
한 건에 연도 토큰 하나만 넣어도 공개 차단이었다(구조화 수치 결속 없음 2문구).
이제는 그 토큰이 사실이 실은 «인용 조각 원문»에 그대로 있으면 완성으로 나가고,
없거나 원문을 싣지 않았으면 예전 두 문구로 막힌다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from src.features.report_quality.tests.test_assessment import (
    STRICT_QUALITY_CONTRACT_VERSION,
    _full_candidate,
    assess_generation,
)
from src.shared.report_quality.constants import (
    LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
    OFFICIAL_PROSE_EXACT_TEXT_KEY,
)


def _probe(
    exact_text: str,
    *,
    carry: bool = True,
    contract_version: str = STRICT_QUALITY_CONTRACT_VERSION,
):
    """첫 확인 산문에 연도 토큰을 넣고, 그 사실이 ``exact_text`` 를 인용한 후보."""

    base = _full_candidate()
    target = base.facts[0]
    source = next(item for item in base.sources if item.source_id == target.source_id)
    digest = hashlib.sha256(exact_text.encode("utf-8")).hexdigest()
    record = {
        "fragment_id": "1",
        "source_id": source.source_id,
        "document_identity": source.document_identity,
        "exact_sha256": digest,
    }
    if carry:
        record[OFFICIAL_PROSE_EXACT_TEXT_KEY] = exact_text
    fact = replace(
        target,
        claim=target.claim.replace("사실은", "사실은 2019년", 1),
        state_evidence=json.dumps([record], ensure_ascii=False),
        supporting_evidence_hashes=(digest,),
    )
    assert fact.claim != target.claim, "연도 토큰을 넣지 못했다"
    cited_source = replace(
        source, exact_evidence_hashes=(*source.exact_evidence_hashes, digest),
    )
    candidate = replace(
        base,
        facts=(fact, *base.facts[1:]),
        sources=tuple(
            cited_source if item.source_id == source.source_id else item
            for item in base.sources
        ),
    )
    result = assess_generation(candidate, contract_version=contract_version)
    return result, fact


def test_탐침_연도_토큰이_인용원문에_그대로_있으면_완성으로_나간다():
    result, _fact = _probe("가나다전자는 2019년 공식 원문을 공개했다.")

    assert result.safety.decision.value == "공개 가능"
    assert result.safety.problems == ()
    assert result.publication_grade.value == "완성"


@pytest.mark.parametrize(
    ("exact_text", "carry"),
    [
        ("가나다전자는 공식 원문을 공개했다.", True),
        ("가나다전자는 2019년 공식 원문을 공개했다.", False),
    ],
    ids=("원문에_토큰없음", "원문_미동봉"),
)
def test_탐침_연도_토큰이_원문에_없거나_원문을_싣지_않으면_예전처럼_막힌다(exact_text, carry):
    result, fact = _probe(exact_text, carry=carry)

    assert result.safety.decision.value == "공개 차단"
    assert result.safety.problems == (
        f"{fact.fact_id}의 구조화 수치 이름표가 비었습니다",
        f"{fact.fact_id}의 수치에 versioned NumericBinding이 없습니다",
    )
    assert result.publication_grade.value == "미완성"


def test_탐침_ENFORCE_NO_PARTIAL_계약은_원문에_있어도_예전처럼_막힌다():
    """예외는 FULL(v3) 계약에서만 연다(ADR 0005 결정 (가))."""

    result, fact = _probe(
        "가나다전자는 2019년 공식 원문을 공개했다.",
        contract_version=LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
    )

    assert result.safety.decision.value == "공개 차단"
    assert result.safety.problems == (
        f"{fact.fact_id}의 구조화 수치 이름표가 비었습니다",
        f"{fact.fact_id}의 수치에 versioned NumericBinding이 없습니다",
    )
