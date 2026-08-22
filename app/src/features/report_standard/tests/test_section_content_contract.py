"""장별 질문을 공개 사실 원장에서 투영하는 공통 내용 계약을 검증한다."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
import re

from src.features.pipeline.canonical_demo import build_demo_report
from src.features.report_standard import SECTION_BY_ID
from src.features.report_standard.section_content import (
    section_content_blocks,
    source_verification_label,
    summary_topic,
)
from src.shared.comparison_candidate_basis import (
    COMPARISON_SOURCE_BASIS_VERSION,
    comparison_evidence_exact_sha256,
    comparison_evidence_sha256,
    encode_comparison_source_basis_v2,
)


def _section_blocks():
    report = build_demo_report()
    return report, {
        section.cell: section_content_blocks(report, section)
        for section in report.sections
    }


def _field_map(block) -> dict[str, str]:
    return {field.label: field.value for field in block.fields}


def _past_section(report):
    return next(
        section for section in report.sections if section.cell == "past_changes"
    )


def test_복수_실행_근거_해석은_prose와_출처를_정확히_한_번만_투영한다() -> None:
    report = build_demo_report()
    first = next(
        fact for fact in report.fact_records if fact.fact_id == "past-execution-01"
    )
    second = replace(
        first,
        fact_id="past-execution-02",
        subject_scope="열분해유·지정폐기물 처리 연결 범위",
        relationship_or_action="두 자원순환 사업 연결 포함",
        claim="2025년 말 열분해유 생산과 지정폐기물 처리 사업이 연결 범위에 포함됐다.",
        source_id="JY-S1",
    )
    facts = []
    for fact in report.fact_records:
        facts.append(fact)
        if fact.fact_id == first.fact_id:
            facts.append(second)
        elif fact.fact_id == "past-change-01":
            facts[-1] = replace(
                fact,
                basis_fact_ids=[first.fact_id, second.fact_id],
            )

    past = _past_section(report)
    first_line = past.prose_lines[0]
    second_line = (second.claim, "[1]")
    changed_past = replace(
        past,
        fact_ids=[first.fact_id, second.fact_id, *past.fact_ids[1:]],
        prose_lines=[first_line, second_line, *past.prose_lines[1:]],
        lines=[first_line, second_line, *past.lines[1:]],
    )
    report = replace(
        report,
        fact_records=facts,
        sections=[
            changed_past if section.cell == "past_changes" else section
            for section in report.sections
        ],
    )

    blocks = section_content_blocks(report, changed_past)
    displayed_ids = Counter(fact_id for block in blocks for fact_id in block.fact_ids)
    assert displayed_ids[first.fact_id] == 1
    assert displayed_ids[second.fact_id] == 1
    assert displayed_ids["past-change-01"] == 1

    field_values = [field.value for block in blocks for field in block.fields]
    for prose, _cite in changed_past.prose_lines:
        assert sum(value.count(prose) for value in field_values) == 1

    interpretation = next(
        block for block in blocks if "past-change-01" in block.fact_ids
    )
    basis_text = _field_map(interpretation)["근거 사실"]
    assert interpretation.source_numbers == (2, 1)
    assert first.subject_scope in basis_text
    assert second.subject_scope in basis_text
    assert all(
        internal_id not in "\n".join(field_values)
        for internal_id in (first.fact_id, second.fact_id, "past-change-01")
    )


def test_과거실적만_근거인_해석은_표_참조와_근거_출처만_공개한다() -> None:
    report = build_demo_report()
    basis_ids = ["past-fin-2025", "past-fin-2024"]
    facts = [
        replace(fact, basis_fact_ids=basis_ids)
        if fact.fact_id == "past-change-01"
        else fact
        for fact in report.fact_records
    ]
    report = replace(report, fact_records=facts)
    past = _past_section(report)

    blocks = section_content_blocks(report, past)
    displayed_ids = Counter(fact_id for block in blocks for fact_id in block.fact_ids)
    interpretation = next(
        block for block in blocks if "past-change-01" in block.fact_ids
    )
    field_values = [field.value for block in blocks for field in block.fields]
    basis_text = _field_map(interpretation)["근거 사실"]

    assert displayed_ids["past-execution-01"] == 1
    assert displayed_ids["past-change-01"] == 1
    assert all(displayed_ids[fact_id] == 0 for fact_id in basis_ids)
    assert basis_text == "2025 완료 사업연도 실적표 · 2024 완료 사업연도 실적표"
    assert interpretation.source_numbers == (2,)
    assert all(fact_id not in "\n".join(field_values) for fact_id in basis_ids)
    assert all(
        next(fact.claim for fact in facts if fact.fact_id == fact_id) not in basis_text
        for fact_id in basis_ids
    )


def test_진영_정본은_서로_다른_근거를_갖춘_두_제품만_우선제품으로_낸다() -> None:
    report = build_demo_report()
    portfolio = next(
        section for section in report.sections if section.cell == "portfolio"
    )
    products = [
        fact
        for fact in report.fact_records
        if fact.section_owner == "portfolio"
        and fact.claim_type == "priority_product"
        and fact.fact_id in portfolio.fact_ids
    ]

    assert len(products) == 2
    assert len({fact.subject_scope for fact in products}) == 2
    assert {fact.claim for fact in products} == {
        text for text, _cite in portfolio.prose_lines
    }
    source_ids = {source.source_id for source in report.citations}
    for fact in products:
        assert fact.product_role.strip()
        assert len(set(fact.priority_signals)) >= 2
        assert all(signal.strip() for signal in fact.priority_signals)
        assert fact.state_evidence.strip()
        assert len(set(fact.evidence_support_terms)) >= 2
        assert fact.evidence_binding.strip()
        assert fact.source_id in source_ids


def test_작성자_정체성_요약을_공식_자기정의처럼_표시하지_않는다() -> None:
    report, blocks_by_section = _section_blocks()
    identity = next(
        fact for fact in report.fact_records if fact.fact_id == "identity-01"
    )
    fields = {
        field.label: field.value
        for block in blocks_by_section["identity"]
        for field in block.fields
    }

    assert identity.claim_type == "identity_summary"
    assert fields["정체성 요약"] == identity.claim
    assert "공식 확인 내용" not in fields
    assert "공식 자기정의" not in fields


def test_공식_자기정의만_공식_라벨로_표시한다() -> None:
    report = build_demo_report()
    identity = next(
        fact for fact in report.fact_records if fact.fact_id == "identity-01"
    )
    changed = replace(identity, claim_type="official_self_definition")
    report = replace(
        report,
        fact_records=[
            changed if fact.fact_id == identity.fact_id else fact
            for fact in report.fact_records
        ],
    )
    section = next(item for item in report.sections if item.cell == "identity")
    fields = {
        field.label: field.value
        for block in section_content_blocks(report, section)
        for field in block.fields
    }

    assert fields["공식 자기정의"] == changed.claim
    assert "정체성 요약" not in fields


def test_단순_매출_지역은_시장_관찰만_표시하고_단계를_붙이지_않는다() -> None:
    report, blocks_by_section = _section_blocks()
    market = next(
        fact for fact in report.fact_records if fact.fact_id == "biz-customer-01"
    )
    market_block = next(
        block
        for block in blocks_by_section["business_model"]
        if market.fact_id in block.fact_ids
    )
    fields = _field_map(market_block)

    assert fields["시장 관찰"] == market.market_observation
    assert "시장 단계" not in fields
    assert "확인된 우선순위" not in fields


def test_1장부터_9장은_각_질문에_답하는_구조화_블록을_낸다() -> None:
    _report, blocks_by_section = _section_blocks()
    assert tuple(blocks_by_section) == tuple(SECTION_BY_ID)
    assert all(blocks_by_section.values())

    required_labels = {
        "identity": {"정체성 요약", "근거 사업 범위", "산업 내 역할"},
        "business_model": {
            "수익 경로",
            "가치·거래 방식",
            "가격·계약·반복 조건",
            "고객·시장",
            "시장 관찰",
            "구매자·사용자·수혜자",
        },
        "portfolio": {
            "제품·서비스 범위",
            "사업적 역할",
            "2장 수익 분류 참조",
            "중점 추진 근거·현재 확인·한계",
        },
        "past_changes": {"실행", "확인된 결과·의미", "범위·한계"},
        "current_challenges": {
            "현재 과제·증거",
            "진행 중 대응",
            "초기 신호·남은 문제",
            "다음 확인 지표",
        },
        "future_strategy": {
            "공식 계획",
            "시점·조건·현재 상태",
            "회사 제시 효과·한계",
            "실행 확인 신호",
        },
        "operations_partners": {
            "가치사슬 단계",
            "관계 유형",
            "확인된 역할",
            "운영 범위·한계",
        },
        "culture": {"적용 범위", "확인 내용", "범위·한계"},
        "competitive_position": {
            "비교군 선정 이유·동일 조건",
            "비교축",
            "확인된 차이",
            "판정·비교 한계",
        },
    }
    for section_id, required in required_labels.items():
        actual = {
            field.label
            for block in blocks_by_section[section_id]
            for field in block.fields
        }
        assert required <= actual, f"{section_id} 누락: {sorted(required - actual)}"


def test_모든_구조카드는_소제목_4개_이하이며_라벨과_값이_비지_않는다() -> None:
    _report, blocks_by_section = _section_blocks()

    for section_id, blocks in blocks_by_section.items():
        for block in blocks:
            assert block.title.strip(), f"{section_id} 카드 제목 누락"
            assert 1 <= len(block.fields) <= 4, (
                f"{section_id}/{block.title} 필드 수: {len(block.fields)}"
            )
            labels = [field.label for field in block.fields]
            assert len(labels) == len(set(labels)), f"{section_id} 카드 라벨 중복"
            assert all(field.label.strip() for field in block.fields), (
                f"{section_id} 카드의 빈 라벨"
            )
            assert all(field.value.strip() for field in block.fields), (
                f"{section_id} 카드의 빈 값"
            )


def test_회사_사실은_프로그램_안내를_제외한_공개블록_하나에서만_표시된다() -> None:
    report, blocks_by_section = _section_blocks()
    fact_by_id = {fact.fact_id: fact for fact in report.fact_records}
    displayed = [
        (section_id, fact_id)
        for section_id, blocks in blocks_by_section.items()
        for block in blocks
        for fact_id in block.fact_ids
    ]

    counts = Counter(fact_id for _section_id, fact_id in displayed)
    assert counts
    assert all(count == 1 for count in counts.values())
    for section_id, fact_id in displayed:
        assert fact_by_id[fact_id].section_owner == section_id
    assert all(block.fact_ids for block in blocks_by_section["culture"])
    assert all(
        block.title != "공식 사례 공개 수준"
        for block in blocks_by_section["culture"]
    )


def test_제품_현재과제_미래계획의_필수_판단정보가_빈칸없이_보인다() -> None:
    _report, blocks_by_section = _section_blocks()

    portfolio = blocks_by_section["portfolio"]
    assert len(portfolio) == 2
    for block in portfolio:
        fields = _field_map(block)
        assert fields["제품·서비스 범위"] == block.title
        assert fields["사업적 역할"]
        assert fields["2장 수익 분류 참조"]
        evidence = fields["중점 추진 근거·현재 확인·한계"]
        assert evidence.startswith("신호: ")
        assert " · 확인: " in evidence
        assert " · 한계: " in evidence

    current = blocks_by_section["current_challenges"]
    assert len(current) == 1
    current_fields = _field_map(current[0])
    assert "본계약" in current_fields["현재 과제·증거"]
    responses = current_fields["진행 중 대응"]
    assert "MOU" in responses
    assert "품질시험" in responses
    assert "초기 신호:" not in responses
    signal_and_remaining = current_fields["초기 신호·남은 문제"]
    assert "초기 신호: 대응 진행 중·효과 미확인" in signal_and_remaining
    assert "남은 문제:" in signal_and_remaining
    assert current_fields["다음 확인 지표"] == "본계약"

    future = blocks_by_section["future_strategy"]
    assert len(future) == 3
    expected_timing = {
        "열분해 설비": "2026년 하반기",
        "해외 납품과 특수 필름": "2026~2028년",
        "업무 자동화와 AX 솔루션": "2026~2028년",
    }
    for block in future:
        fields = _field_map(block)
        timing_and_status = fields["시점·조건·현재 상태"]
        assert timing_and_status == (
            f"시점: {expected_timing[block.title]} · "
            "조건: 공식 조건 미공개 · 상태: 발표·미실행"
        )
        assert fields["실행 확인 신호"]
        assert "공식 효과 미공개" in fields["회사 제시 효과·한계"]

    operations = blocks_by_section["operations_partners"]
    operation_fields = {block.title: _field_map(block) for block in operations}
    assert operation_fields["한국에코에너지"]["가치사슬 단계"] == "생산·운영"
    assert operation_fields["한국에코에너지"]["관계 유형"] == "종속회사"
    assert set(operation_fields) == {"한국에코에너지"}
    assert "operating_core" not in operation_fields["한국에코에너지"][
        "운영 범위·한계"
    ]

    competitive = blocks_by_section["competitive_position"]
    assert len(competitive) == 1
    for block in competitive:
        fields = _field_map(block)
        reason_and_conditions = fields["비교군 선정 이유·동일 조건"]
        assert reason_and_conditions.startswith("선정 이유: ")
        assert " · 동일 조건: " in reason_and_conditions
        assert "2025 회계연도" in reason_and_conditions
        judgment_and_limit = fields["판정·비교 한계"]
        assert judgment_and_limit.startswith("운영 특성 · ")
        assert len(judgment_and_limit.split(" · ", maxsplit=1)) == 2


def test_9장은_명시적으로_결속된_판정을_그대로_구분해_표시한다() -> None:
    report = build_demo_report()
    target = next(
        fact for fact in report.fact_records if fact.fact_id == "competition-01"
    )
    changed = replace(target, comparison_judgment="competitive_advantage")
    report = replace(
        report,
        fact_records=[
            changed if fact.fact_id == target.fact_id else fact
            for fact in report.fact_records
        ],
    )
    section = next(
        item for item in report.sections if item.cell == "competitive_position"
    )

    blocks = section_content_blocks(report, section)

    assert len(blocks) == 1
    assert _field_map(blocks[0])["판정·비교 한계"].startswith("경쟁우위 · ")


def test_9장은_자사와_비교사_공식출처를_함께_표시한다() -> None:
    report, blocks_by_section = _section_blocks()
    source_numbers = {
        source.source_id: source.number for source in report.citations
    }
    competitive = blocks_by_section["competitive_position"]

    assert competitive
    assert all(block.source_numbers == (2, 6) for block in competitive)
    for block in competitive:
        for fact_id in block.fact_ids:
            fact = next(fact for fact in report.fact_records if fact.fact_id == fact_id)
            assert source_numbers[fact.source_id] == 2
            assert source_numbers[fact.comparator_source_id] == 6


def test_요약_짧은제목과_부록_사실검증_표시는_정해진_형식을_지킨다() -> None:
    report = build_demo_report()
    topics = [summary_topic(item.section_id) for item in report.summary_items]

    assert len(topics) == len(report.summary_items)
    assert all(re.fullmatch(r"[가-힣]{2,6}", topic) for topic in topics)
    assert all(
        source_verification_label(report, source.source_id) == "사실 검증 완료"
        for source in report.citations
    )


def test_v2_비교후보_출처는_본문사실없음이_아닌_후보근거로_표시한다() -> None:
    report = build_demo_report()
    evidence = "베타전자는 우리의 현재 경쟁사다."
    candidate_source = replace(
        report.citations[0],
        number=max(source.number for source in report.citations) + 1,
        source_id="source-candidate-only",
    )
    basis = encode_comparison_source_basis_v2(
        {
            "version": COMPARISON_SOURCE_BASIS_VERSION,
            "candidate_source_id": candidate_source.source_id,
            "self_corp_code": "00000001",
            "self_attestation_source_id": report.citations[0].source_id,
            "self_attestation_evidence": "DART 기업개황 exact host 결속",
            "candidate_corp_code": "00000002",
            "candidate_name": "베타전자",
            "source_kind": "기타",
            "source_type": "회사 공식 웹",
            "source_publisher": report.company,
            "source_host": "company.example",
            "source_url": "https://company.example/ir/competition",
            "source_document_id": "/ir/competition",
            "source_location": "/ir/competition",
            "source_date": "2026-08-22",
            "evidence_text": evidence,
            "evidence_sha256": comparison_evidence_sha256(evidence),
            "evidence_exact_sha256": comparison_evidence_exact_sha256(evidence),
            "overlap_dimension": "경쟁 관계 명시",
        }
    )
    assert basis
    facts = [
        replace(report.fact_records[0], comparison_basis=basis),
        *report.fact_records[1:],
    ]
    candidate_report = replace(
        report,
        citations=[*report.citations, candidate_source],
        fact_records=facts,
    )

    assert (
        source_verification_label(candidate_report, candidate_source.source_id)
        == "후보 선정 근거"
    )
