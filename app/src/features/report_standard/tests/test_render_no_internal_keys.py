"""실경로 폴백 조건의 FactRecord가 화면에 내부 키를 노출하지 않는지 검증한다.

조립기(pipeline/canonical_report.py의 ``_fact_from_claim``)는
``relationship_or_action``을 채울 값이 없으면 claim_type 내부 키를 그대로
남긴다(예: ``official_self_definition``, ``revenue_model``). 기존 시험
(test_publish.py의 claim_type 치환, test_section_content_contract.py:190)은
데모의 한국어 값을 유지한 채 claim_type만 바꾸는 방식이라 이 결함 —
배포본 JYP PDF에 내부 키가 인쇄된 사고 — 을 잡지 못했다. 여기서는 데모
값을 쓰지 않고 실경로 폴백 조건 그대로 FactRecord를 직접 만들어
``section_content_blocks()`` 렌더 결과를 검사한다.
"""

from __future__ import annotations

import re
from dataclasses import replace

from src.features.pipeline.port import FactRecord, Grade, Report, ReportSection
from src.features.report_standard.constants import (
    CANONICAL_CLAIM_TYPES_BY_SECTION,
    CANONICAL_SCHEMA_VERSION,
    RELATIONSHIP_KEY_FALLBACK_LABEL,
    RELATIONSHIP_KEY_LABELS,
)
from src.features.report_standard.publish import _section_projection_problems
from src.features.report_standard.section_content import section_content_blocks

#: 조립기 폴백이 남기는 영문 내부 키 모양. 렌더 결과에 이 모양이 남으면 결함이다.
_INTERNAL_KEY_SHAPE = re.compile(r"^[a-z][a-z0-9_]*$")


def _fact(
    fact_id: str,
    section_owner: str,
    claim_type: str,
    claim: str,
    subject_scope: str,
) -> FactRecord:
    """실경로 폴백 조건 그대로: relationship_or_action에 claim_type 내부 키."""

    return FactRecord(
        fact_id=fact_id,
        legal_entity="주식회사 검증",
        subject_scope=subject_scope,
        # canonical_report._fact_from_claim의 `claim.claim_type or claim.section_id`
        # 폴백과 동일한 조건. 데모의 한국어 값을 쓰지 않는다.
        relationship_or_action=claim_type,
        claim=claim,
        claim_type=claim_type,
        section_owner=section_owner,
        status="verified",
        verification_status="verified",
        source_id="src-01",
    )


def _report_with_section(
    section_id: str, facts: list[FactRecord]
) -> tuple[Report, ReportSection]:
    """사실과 공개 prose가 결속된 최소 보고서를 만든다."""

    section = ReportSection(
        cell=section_id,
        title=section_id,
        prose_lines=[(fact.claim, "[1]") for fact in facts],
        fact_ids=[fact.fact_id for fact in facts],
    )
    report = Report(
        company="주식회사 검증",
        job="",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=[section],
        fact_records=list(facts),
        schema_version=CANONICAL_SCHEMA_VERSION,
    )
    return report, section


def _field_values(report: Report, section: ReportSection) -> list[str]:
    return [
        field.value
        for block in section_content_blocks(report, section)
        for field in block.fields
    ]


def test_닫힌_claim_type_키는_모두_한국어_라벨을_갖는다() -> None:
    """CANONICAL_CLAIM_TYPES_BY_SECTION의 모든 키가 라벨맵에 있어야 한다."""

    all_keys = set().union(*CANONICAL_CLAIM_TYPES_BY_SECTION.values())
    missing = all_keys - set(RELATIONSHIP_KEY_LABELS)
    assert not missing, f"라벨 누락 키: {sorted(missing)}"
    for label in (*RELATIONSHIP_KEY_LABELS.values(), RELATIONSHIP_KEY_FALLBACK_LABEL):
        assert label.strip(), "빈 라벨은 출고 게이트 전체 차단을 일으킨다"
        assert _INTERNAL_KEY_SHAPE.fullmatch(label) is None
        assert re.search(r"[가-힣]", label), f"한국어가 아닌 라벨: {label}"


def test_실경로_정체성_폴백은_내부_키_대신_한국어_라벨을_렌더한다() -> None:
    facts = [
        _fact(
            "identity-fact-01",
            "identity",
            "identity_summary",
            "회사는 음반 제작과 매니지먼트를 함께 운영하는 엔터테인먼트 기업이다.",
            "전사",
        ),
        _fact(
            "identity-fact-02",
            "identity",
            "official_self_definition",
            "회사는 스스로를 종합 엔터테인먼트 기업으로 정의한다.",
            "전사",
        ),
        _fact(
            "identity-fact-03",
            "identity",
            "operating_scope",
            "회사는 음반 제작과 공연 기획 사업을 운영한다.",
            "음반·공연",
        ),
    ]
    report, section = _report_with_section("identity", facts)

    blocks = section_content_blocks(report, section)
    values = _field_values(report, section)

    assert len(blocks) == 3
    leaked = [value for value in values if _INTERNAL_KEY_SHAPE.fullmatch(value)]
    assert leaked == [], f"내부 키가 렌더에 노출됨: {leaked}"
    assert RELATIONSHIP_KEY_LABELS["identity_summary"] in values
    assert RELATIONSHIP_KEY_LABELS["official_self_definition"] in values
    assert RELATIONSHIP_KEY_LABELS["operating_scope"] in values


def test_실경로_수익모델_폴백은_내부_키를_렌더하지_않는다() -> None:
    fact = _fact(
        "biz-fact-01",
        "business_model",
        "revenue_model",
        "회사는 음반 판매와 공연 티켓에서 매출을 얻는다.",
        "음반·공연",
    )
    report, section = _report_with_section("business_model", [fact])

    blocks = section_content_blocks(report, section)
    values = _field_values(report, section)

    assert len(blocks) == 1
    leaked = [value for value in values if _INTERNAL_KEY_SHAPE.fullmatch(value)]
    assert leaked == [], f"내부 키가 렌더에 노출됨: {leaked}"
    assert RELATIONSHIP_KEY_LABELS["revenue_model"] in values


def test_라벨맵에_없는_내부_키도_빈값이_아닌_기본_라벨로_렌더한다() -> None:
    """빈 문자열은 publish의 빈 항목 검사에 걸려 전체 차단되므로 금지다."""

    fact = replace(
        _fact(
            "identity-fact-01",
            "identity",
            "identity_summary",
            "회사는 음반 제작과 매니지먼트를 함께 운영하는 엔터테인먼트 기업이다.",
            "전사",
        ),
        relationship_or_action="unknown_future_key",
    )
    report, section = _report_with_section("identity", [fact])

    blocks = section_content_blocks(report, section)
    fields = {field.label: field.value for block in blocks for field in block.fields}

    assert fields["산업 내 역할"] == RELATIONSHIP_KEY_FALLBACK_LABEL


def test_정상_한국어_역할_문구는_변환_없이_그대로_렌더한다() -> None:
    fact = replace(
        _fact(
            "identity-fact-01",
            "identity",
            "identity_summary",
            "회사는 음반 제작과 매니지먼트를 함께 운영하는 엔터테인먼트 기업이다.",
            "전사",
        ),
        relationship_or_action="음반·매니지먼트 산업의 종합 사업자",
    )
    report, section = _report_with_section("identity", [fact])

    blocks = section_content_blocks(report, section)
    fields = {field.label: field.value for block in blocks for field in block.fields}

    assert fields["산업 내 역할"] == "음반·매니지먼트 산업의 종합 사업자"


def test_출고_게이트는_구조블록의_내부_키_노출을_안전망으로_잡는다() -> None:
    """라벨맵이 못 덮는 다른 경로로 내부 키가 새도 게이트가 problem을 쌓는다."""

    fact = replace(
        _fact(
            "culture-fact-01",
            "culture",
            "official_value",
            "회사는 도전과 존중을 전사 공식 가치로 제시한다.",
            "전사",
        ),
        # subject_scope는 라벨맵 변환 대상이 아니므로 내부 키가 그대로 렌더된다.
        subject_scope="official_value",
    )
    report, section = _report_with_section("culture", [fact])

    problems = _section_projection_problems(
        report,
        section,
        [fact.fact_id],
        {fact.fact_id: fact},
        {},
    )

    assert any("내부 키 'official_value'" in problem for problem in problems)
