from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.export_pdf.automatic_release import report_sha256
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import FactRecord, Report, ReportSection
from src.features.provenance.sources import (
    Source,
    SourceKind,
    evidence_text_hash,
    seal_collected_source,
)
from src.features.report_standard.constants import (
    CANONICAL_CLAIM_TYPES_BY_SECTION,
    CANONICAL_SECTION_IDS,
    SECTION_BY_ID,
    SECTION_SPECS,
)
from src.features.spanselect.canonical import CLAIM_TYPES_BY_SECTION
from src.features.report_standard import publish as publish_module
from src.features.report_standard.publish import (
    PublishBlockedError,
    _forbidden_text_problem,
    build_published_report,
    fact_evidence_binding,
    validate_publishable,
)
from src.features.report_standard.section_content import (
    ContentField,
    section_content_blocks,
)


def _valid_report() -> Report:
    return build_demo_report()


def _replace_fact(report: Report, fact_id: str, **changes: object) -> Report:
    facts: list[FactRecord] = []
    for fact in report.fact_records:
        if fact.fact_id != fact_id:
            facts.append(fact)
            continue
        changed = replace(fact, **changes)
        facts.append(replace(changed, evidence_binding=fact_evidence_binding(changed)))
    return replace(report, fact_records=facts)


def _replace_visible_fact_evidence(
    report: Report, fact_id: str, *, evidence: str, **changes: object
) -> Report:
    """표적 계약 시험에서 공개 문장·수집 payload·seal을 함께 교체한다."""

    original = next(fact for fact in report.fact_records if fact.fact_id == fact_id)
    changed = replace(
        original,
        state_evidence=evidence,
        raw_value="",
        calculation="",
        display_value="",
        rounding_rule="",
        numeric_checks=[],
        **changes,
    )
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    citations = [
        seal_collected_source(
            replace(
                item,
                evidence_hashes=sorted(
                    {*item.evidence_hashes, evidence_text_hash(evidence)}
                ),
            )
        )
        if isinstance(item, Source) and item.source_id == original.source_id
        else item
        for item in report.citations
    ]
    sections = []
    for section in report.sections:
        if section.cell != original.section_owner:
            sections.append(section)
            continue
        prose_lines = [
            (changed.claim if text == original.claim else text, cite)
            for text, cite in section.prose_lines
        ]
        lines = [
            (changed.claim if text == original.claim else text, cite)
            for text, cite in section.lines
        ]
        sections.append(replace(section, prose_lines=prose_lines, lines=lines))
    return replace(
        report,
        citations=citations,
        sections=sections,
        fact_records=[
            changed if fact.fact_id == fact_id else fact
            for fact in report.fact_records
        ],
    )


def _past_section(report: Report) -> ReportSection:
    return next(
        section for section in report.sections if section.cell == "past_changes"
    )


def _report_with_second_completed_execution(report: Report) -> Report:
    first = next(
        fact for fact in report.fact_records if fact.fact_id == "past-execution-01"
    )
    interpretation = next(
        fact for fact in report.fact_records if fact.fact_id == "past-change-01"
    )
    second_evidence = (
        "2025년 사업보고서 연결 범위: 종속회사 편입, 열분해유 생산과 "
        "지정폐기물 처리 사업 포함 완료"
    )
    second = replace(
        first,
        fact_id="past-execution-02",
        subject_scope="열분해유·지정폐기물 처리 연결 범위",
        relationship_or_action="두 자원순환 사업 연결 포함",
        claim=(
            "2025년 말 열분해유 생산과 지정폐기물 처리 사업이 "
            "연결 범위에 포함됐다."
        ),
        state_evidence=second_evidence,
        evidence_support_terms=["열분해유", "지정폐기물"],
        evidence_binding="",
    )
    second = replace(second, evidence_binding=fact_evidence_binding(second))
    changed_interpretation = replace(
        interpretation,
        basis_fact_ids=[first.fact_id, second.fact_id],
        evidence_binding="",
    )
    changed_interpretation = replace(
        changed_interpretation,
        evidence_binding=fact_evidence_binding(changed_interpretation),
    )

    facts: list[FactRecord] = []
    for fact in report.fact_records:
        facts.append(
            changed_interpretation
            if fact.fact_id == interpretation.fact_id
            else fact
        )
        if fact.fact_id == first.fact_id:
            facts.append(second)

    past = _past_section(report)
    first_line = past.prose_lines[0]
    second_line = (second.claim, "[2]")
    changed_past = replace(
        past,
        fact_ids=[first.fact_id, second.fact_id, *past.fact_ids[1:]],
        prose_lines=[first_line, second_line, *past.prose_lines[1:]],
        lines=[first_line, second_line, *past.lines[1:]],
    )
    citations = []
    for item in report.citations:
        if isinstance(item, Source) and item.source_id == first.source_id:
            item = seal_collected_source(
                replace(
                    item,
                    evidence_hashes=sorted(
                        {*item.evidence_hashes, evidence_text_hash(second_evidence)}
                    ),
                )
            )
        citations.append(item)
    return replace(
        report,
        fact_records=facts,
        citations=citations,
        sections=[
            changed_past if section.cell == past.cell else section
            for section in report.sections
        ],
    )


def _move_past_interpretation_to_source_one(report: Report) -> Report:
    interpretation = next(
        fact for fact in report.fact_records if fact.fact_id == "past-change-01"
    )
    source = next(
        item
        for item in report.citations
        if isinstance(item, Source) and item.source_id == "JY-S1"
    )
    changed_source = seal_collected_source(
        replace(
            source,
            used_in=[*source.used_in, "past_changes"],
            evidence_hashes=sorted(
                {
                    *source.evidence_hashes,
                    evidence_text_hash(interpretation.state_evidence),
                }
            ),
        )
    )
    report = replace(
        report,
        citations=[
            changed_source
            if isinstance(item, Source) and item.source_id == source.source_id
            else item
            for item in report.citations
        ],
    )
    report = _replace_fact(
        report,
        interpretation.fact_id,
        basis_fact_ids=["past-fin-2025"],
        source_id=source.source_id,
        source_type=source.source_type,
        source_title=source.title,
        source_publisher=source.publisher,
        source_host=source.host,
        source_url=source.url,
        source_document_id=source.document_id,
        location=source.location,
        source_date=source.published_at or source.disclosed_at or source.collected_at,
    )
    past = _past_section(report)
    changed_prose = [
        (text, "[1]" if text == interpretation.claim else cite)
        for text, cite in past.prose_lines
    ]
    changed_past = replace(past, prose_lines=changed_prose, lines=list(changed_prose))
    return replace(
        report,
        sections=[
            changed_past if section.cell == past.cell else section
            for section in report.sections
        ],
    )


def test_complete_demo_passes_all_nine_sections() -> None:
    report = _valid_report()
    validation = validate_publishable(report)

    assert validation.publishable is True
    assert validation.included_section_ids == CANONICAL_SECTION_IDS
    assert report.filled_count == 9
    assert [section.cell for section in report.sections] == list(CANONICAL_SECTION_IDS)
    for section in report.sections:
        spec = SECTION_BY_ID[section.cell]
        assert (section.title, section.display_number, section.tag) == (
            spec.title,
            spec.display_number,
            spec.tag,
        )


def test_table_presentation_changes_report_hash_but_not_public_fact_or_evidence_bindings() -> None:
    hinted_report = _valid_report()
    target_section = next(
        section for section in hinted_report.sections if section.cell == "past_changes"
    )
    hinted_table = target_section.tables[0]
    assert hinted_table.presentation == "trend"

    plain_table = replace(hinted_table, presentation="table")
    plain_sections = [
        replace(section, tables=[plain_table, *section.tables[1:]])
        if section.cell == target_section.cell
        else section
        for section in hinted_report.sections
    ]
    plain_report = replace(hinted_report, sections=plain_sections)

    for field_name in (
        "caption",
        "headers",
        "rows",
        "raw_rows",
        "cite",
        "evidence_rows",
    ):
        assert getattr(plain_table, field_name) == getattr(hinted_table, field_name)

    hinted_published = build_published_report(hinted_report)
    plain_published = build_published_report(plain_report)
    hinted_expected_fact_ids = tuple(
        fact.fact_id for fact in hinted_published.fact_records
    )
    plain_expected_fact_ids = tuple(
        fact.fact_id for fact in plain_published.fact_records
    )

    assert plain_expected_fact_ids == hinted_expected_fact_ids
    assert plain_published.fact_records == hinted_published.fact_records
    assert [
        (
            fact.fact_id,
            fact.source_id,
            fact.state_evidence,
            fact.evidence_binding,
        )
        for fact in plain_published.fact_records
    ] == [
        (
            fact.fact_id,
            fact.source_id,
            fact.state_evidence,
            fact.evidence_binding,
        )
        for fact in hinted_published.fact_records
    ]
    assert plain_published.citations == hinted_published.citations
    assert report_sha256(plain_published) != report_sha256(hinted_published)


def test_section_contract_keeps_semantic_ids_and_display_labels_separate() -> None:
    assert CANONICAL_SECTION_IDS == tuple(spec.section_id for spec in SECTION_SPECS)
    assert SECTION_BY_ID["past_changes"].display_number == "4"
    assert SECTION_BY_ID["past_changes"].tag == "#과거"
    assert SECTION_BY_ID["current_challenges"].tag == "#현재"
    assert SECTION_BY_ID["future_strategy"].tag == "#미래"


def test_claim_type_is_a_closed_enum_per_section() -> None:
    report = _replace_fact(
        _valid_report(),
        "identity-01",
        claim_type="company_identity",
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("허용되지 않는 claim_type" in reason for reason in validation.reasons)


def test_identity_requires_an_author_summary_even_with_an_official_definition() -> None:
    report = _replace_fact(
        _valid_report(),
        "identity-01",
        claim_type="official_self_definition",
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("identity_summary" in reason for reason in validation.reasons)


def test_upstream_claim_type_enum_is_a_subset_of_the_publish_contract() -> None:
    for section_id, upstream_types in CLAIM_TYPES_BY_SECTION.items():
        assert upstream_types <= CANONICAL_CLAIM_TYPES_BY_SECTION[section_id]


@pytest.mark.parametrize("missing", CANONICAL_SECTION_IDS)
def test_every_section_is_required(missing: str) -> None:
    report = _valid_report()
    report = replace(
        report,
        sections=[section for section in report.sections if section.cell != missing],
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any(missing in reason for reason in validation.reasons)
    with pytest.raises(PublishBlockedError):
        build_published_report(report)


def test_summary_requires_fact_binding_exact_evidence_and_independent_status() -> None:
    report = _valid_report()
    first = report.summary_items[0]
    broken = replace(
        first,
        fact_ids=[],
        evidence_text="임의 근거",
        verification_status="verified",
    )

    validation = validate_publishable(
        replace(report, summary_items=[broken, *report.summary_items[1:]])
    )

    assert validation.publishable is False
    assert any("fact_id가 없습니다" in reason for reason in validation.reasons)


def test_summary_text_cannot_change_after_independent_verification() -> None:
    report = _valid_report()
    first = replace(report.summary_items[0], text="검증 뒤 바꾼 요약문")

    validation = validate_publishable(
        replace(report, summary_items=[first, *report.summary_items[1:]])
    )

    assert validation.publishable is False
    assert any("결속 지문" in reason for reason in validation.reasons)


def test_state_evidence_must_exist_in_source_hash_registry() -> None:
    report = _valid_report()
    report = _replace_fact(
        report,
        "identity-01",
        state_evidence="사업 종속회사라는 단어만 맞춘 임의 문장",
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("원문 해시 등록부" in reason for reason in validation.reasons)


def test_external_news_cannot_be_promoted_to_a_core_fact_even_with_complete_metadata() -> None:
    report = _valid_report()
    target = report.fact_records[0]
    source = next(
        item
        for item in report.citations
        if isinstance(item, Source) and item.source_id == target.source_id
    )
    source_date = source.published_at or source.disclosed_at or source.collected_at
    external = replace(
        source,
        kind=SourceKind.NEWS,
        published_at=source_date,
        disclosed_at="",
        collected_at="",
        domain="news.example",
        publisher="OO경제",
        host="news.example",
        url=f"https://news.example/articles/{source.document_id}",
        source_type="외부 보도",
    )
    citations = [
        external
        if isinstance(item, Source) and item.source_id == source.source_id
        else item
        for item in report.citations
    ]
    changed = replace(
        target,
        source_type=external.source_type,
        source_publisher=external.publisher,
        source_host=external.host,
        source_url=external.url,
        source_document_id=external.document_id,
    )
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    facts = [changed if fact.fact_id == target.fact_id else fact for fact in report.fact_records]

    validation = validate_publishable(
        replace(report, citations=citations, fact_records=facts)
    )

    assert validation.publishable is False
    assert any("공식 파트너·규제기관 원문" in reason for reason in validation.reasons)


def test_official_web_cannot_swap_to_a_self_declared_news_domain() -> None:
    report = _valid_report()
    target = next(fact for fact in report.fact_records if fact.fact_id == "culture-01")
    source = next(
        item
        for item in report.citations
        if isinstance(item, Source) and item.source_id == target.source_id
    )
    forged = replace(
        source,
        host="news.example",
        url="https://news.example/fake-story",
        document_id="fake-story",
    )
    citations = [forged if item is source else item for item in report.citations]
    changed = replace(
        target,
        source_host=forged.host,
        source_url=forged.url,
        source_document_id=forged.document_id,
    )
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    facts = [changed if fact.fact_id == target.fact_id else fact for fact in report.fact_records]

    validation = validate_publishable(
        replace(report, citations=citations, fact_records=facts)
    )

    assert not validation
    assert any("정확한 host URL" in reason for reason in validation.reasons)


def test_official_web_and_attester_cannot_be_rewritten_together() -> None:
    report = _valid_report()
    target = next(fact for fact in report.fact_records if fact.fact_id == "culture-01")
    website = next(
        item
        for item in report.citations
        if isinstance(item, Source) and item.source_id == target.source_id
    )
    attester = next(
        item
        for item in report.citations
        if isinstance(item, Source)
        and item.source_id == website.domain_attestation_source_id
    )
    forged_evidence = "official homepage https://news.example"
    forged_website = replace(
        website,
        host="news.example",
        url="https://news.example/fake-story",
        document_id="fake-story",
        domain_attestation_evidence=forged_evidence,
    )
    forged_attester = replace(
        attester,
        evidence_hashes=sorted(
            {*attester.evidence_hashes, evidence_text_hash(forged_evidence)}
        ),
    )
    citations = [
        forged_website
        if item is website
        else forged_attester
        if item is attester
        else item
        for item in report.citations
    ]
    changed = replace(
        target,
        source_host=forged_website.host,
        source_url=forged_website.url,
        source_document_id=forged_website.document_id,
    )
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    facts = [
        changed if fact.fact_id == target.fact_id else fact
        for fact in report.fact_records
    ]

    validation = validate_publishable(
        replace(report, citations=citations, fact_records=facts)
    )

    assert validation.publishable is False
    assert any("provenance seal" in reason for reason in validation.reasons)


def test_competitor_filing_cannot_serve_as_self_evidence_in_sections_one_to_eight() -> None:
    report = _valid_report()
    target = next(fact for fact in report.fact_records if fact.section_owner == "identity")
    source = next(
        item
        for item in report.citations
        if isinstance(item, Source) and item.source_id == target.source_id
    )
    competitor = replace(source, source_type="비교사 공식 공시", publisher="다른회사")
    citations = [
        competitor
        if isinstance(item, Source) and item.source_id == source.source_id
        else item
        for item in report.citations
    ]
    changed = replace(
        target,
        source_type=competitor.source_type,
        source_publisher=competitor.publisher,
    )
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    facts = [changed if fact.fact_id == target.fact_id else fact for fact in report.fact_records]

    validation = validate_publishable(
        replace(report, citations=citations, fact_records=facts)
    )

    assert validation.publishable is False
    assert any("비교사 원문을 자사 핵심 근거" in reason for reason in validation.reasons)


@pytest.mark.parametrize(
    ("field", "value", "needle"),
    [
        ("location", "전혀 다른 페이지", "하위 위치"),
        ("source_date", "2020-01-01", "source_date"),
        ("as_of", "2030-01-01", "원문 날짜보다 뒤"),
    ],
)
def test_location_and_two_dates_are_fail_closed(
    field: str, value: str, needle: str
) -> None:
    report = _replace_fact(_valid_report(), "identity-01", **{field: value})

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any(needle in reason for reason in validation.reasons)


def test_verification_status_and_fact_status_are_separate() -> None:
    report = _replace_fact(
        _valid_report(),
        "future-01",
        fact_status="actual",
        verification_status="verified",
        status="verified",
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("시간 상태와 모순" in reason for reason in validation.reasons)


def test_delivery_cannot_be_rewritten_as_a_main_contract() -> None:
    report = _replace_fact(
        _valid_report(),
        "biz-customer-01",
        claim="주문 사양 생산품은 가구 제조·유통 고객사와 본계약됐다.",
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("MOU·계약·납품·매출 상태" in reason for reason in validation.reasons)


def test_past_requires_exact_three_recent_completed_fiscal_years() -> None:
    report = _replace_fact(
        _valid_report(),
        "past-fin-2023",
        fiscal_year=2010,
        as_of="2010-12-31",
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("2023~2025" in reason for reason in validation.reasons)


def test_past_requires_completed_execution_and_visible_interpretation() -> None:
    report = _replace_fact(
        _replace_fact(
            _valid_report(),
            "past-change-finance-01",
            claim_type="completed_execution",
            basis_fact_ids=[],
        ),
        "past-change-01",
        claim_type="completed_execution",
        basis_fact_ids=[],
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("변화·실행 해석" in reason for reason in validation.reasons)


def test_past_interpretation_with_multiple_execution_bases_is_publishable_once(
) -> None:
    report = _report_with_second_completed_execution(_valid_report())

    validation = validate_publishable(report)

    assert validation.publishable is True, validation.reasons
    past = _past_section(report)
    blocks = section_content_blocks(report, past)
    owned = [fact_id for block in blocks for fact_id in block.fact_ids]
    assert owned.count("past-execution-01") == 1
    assert owned.count("past-execution-02") == 1
    assert owned.count("past-change-01") == 1
    visible_values = [field.value for block in blocks for field in block.fields]
    for prose, _cite in past.prose_lines:
        assert sum(value.count(prose) for value in visible_values) == 1


def test_historical_only_interpretation_binds_its_fact_and_basis_sources(
) -> None:
    report = _move_past_interpretation_to_source_one(_valid_report())

    validation = validate_publishable(report)

    assert validation.publishable is True, validation.reasons
    blocks = section_content_blocks(report, _past_section(report))
    interpretation = next(
        block for block in blocks if "past-change-01" in block.fact_ids
    )
    fields = {field.label: field.value for field in interpretation.fields}
    assert interpretation.source_numbers == (2, 1)
    assert fields["근거 사실"] == "2025 완료 사업연도 실적표"
    assert "past-fin-2025" not in "\n".join(fields.values())


def test_business_requires_source_bound_market_observation() -> None:
    report = _replace_fact(
        _valid_report(), "biz-customer-01", market_observation=""
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("market_observation" in reason for reason in validation.reasons)


def test_market_observation_must_be_an_exact_source_excerpt() -> None:
    report = _replace_fact(
        _valid_report(),
        "biz-customer-01",
        market_observation="해외가 최우선 성장 시장",
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("원문 근거에 결속" in reason for reason in validation.reasons)


def test_sales_occurrence_cannot_be_promoted_to_a_growth_market() -> None:
    report = _replace_fact(
        _valid_report(),
        "biz-customer-01",
        market_stage="성장",
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("시장 단계의 직접 원문 근거" in reason for reason in validation.reasons)


def test_legacy_market_priority_is_not_publishable() -> None:
    report = _replace_fact(
        _valid_report(), "biz-customer-01", market_priority="해외 성장"
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("market_priority" in reason for reason in validation.reasons)


def test_portfolio_requires_a_role_for_each_of_one_to_three_products() -> None:
    report = _replace_fact(_valid_report(), "portfolio-03", product_role="")

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("product_role" in reason for reason in validation.reasons)


def test_portfolio_role_is_functional_and_not_the_optional_stage() -> None:
    report = _replace_fact(_valid_report(), "portfolio-03", product_role="성장")

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("기능적 사업 역할" in reason for reason in validation.reasons)


def test_portfolio_product_references_a_section_two_revenue_model() -> None:
    report = _replace_fact(
        _valid_report(), "portfolio-03", revenue_model_fact_id=""
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("2장 revenue_model" in reason for reason in validation.reasons)


def test_portfolio_signals_must_bind_two_independent_evidence_clauses() -> None:
    report = _valid_report()
    fact = next(
        item for item in report.fact_records if item.fact_id == "portfolio-02"
    )
    same_event_evidence = (
        "반기보고서 제품 현황: 리얼 알루미늄 합지 필름 생산량 증가에 따른 "
        "국내 고객사 공급"
    )
    report = _replace_fact(
        report,
        fact.fact_id,
        state_evidence=same_event_evidence,
    )
    citations = [
        replace(
            item,
            evidence_hashes=sorted(
                {*item.evidence_hashes, evidence_text_hash(same_event_evidence)}
            ),
        )
        if isinstance(item, Source) and item.source_id == fact.source_id
        else item
        for item in report.citations
    ]

    validation = validate_publishable(replace(report, citations=citations))

    assert validation.publishable is False
    assert any("서로 다른 원문 사건 절" in reason for reason in validation.reasons)


def test_required_section_must_have_a_visible_structured_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _valid_report()
    monkeypatch.setattr(publish_module, "section_content_blocks", lambda *_args: ())

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("공개 구조 블록" in reason for reason in validation.reasons)


def test_visible_card_cannot_exceed_four_subheadings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _valid_report()
    original = publish_module.section_content_blocks

    def oversized_blocks(report: Report, section: ReportSection):
        blocks = original(report, section)
        if getattr(section, "cell", "") != "identity":
            return blocks
        first, *rest = blocks
        return (
            replace(
                first,
                fields=(
                    *first.fields,
                    ContentField("추가 항목", "추가 내용"),
                    ContentField("다섯째 항목", "다섯째 내용"),
                ),
            ),
            *rest,
        )

    monkeypatch.setattr(publish_module, "section_content_blocks", oversized_blocks)

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("소제목은 최대 4개" in reason for reason in validation.reasons)


def test_current_response_must_point_to_same_section_issue() -> None:
    report = _replace_fact(
        _valid_report(), "current-02", response_to_fact_id="identity-01"
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("response_to_fact_id" in reason for reason in validation.reasons)


def test_current_response_must_bind_to_the_linked_issue_subject() -> None:
    evidence = (
        "반기보고서 현재 과제: BetaY 재고 부담에 대응해 할인 판매를 진행 중"
    )
    report = _replace_visible_fact_evidence(
        _valid_report(),
        "current-02",
        evidence=evidence,
        claim="회사는 BetaY 재고 부담에 대응해 할인 판매를 진행 중이다.",
        subject_scope="BetaY 재고 부담",
        relationship_or_action="할인 판매를 진행 중",
        response_action="할인 판매를 진행 중",
        initial_signal="",
        evidence_support_terms=["BetaY", "판매"],
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("문제의 대상·범위와 결속되지" in reason for reason in validation.reasons)


def test_future_phrase_cannot_be_published_as_an_initial_signal() -> None:
    evidence = (
        "반기보고서 해외 신규 유통: 해외 신규 유통 협의를 추진 중; "
        "내년 판매 확대를 계획"
    )
    report = _replace_visible_fact_evidence(
        _valid_report(),
        "current-02",
        evidence=evidence,
        claim=(
            "회사는 해외 신규 유통 협의를 추진 중이며 내년 판매 확대를 "
            "계획했다."
        ),
        subject_scope="해외 신규 유통",
        relationship_or_action="해외 신규 유통 협의를 추진 중",
        response_action="해외 신규 유통 협의를 추진 중",
        initial_signal="내년 판매 확대를 계획",
        evidence_support_terms=["해외", "유통"],
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("관찰된 초기 진척·결과" in reason for reason in validation.reasons)


def test_conditional_plan_completion_clause_does_not_become_completed_execution() -> None:
    evidence = (
        "기업가치 제고 계획: 현지 규제 허가 완료를 선행 조건으로 "
        "미국 AlphaX 생산 설비 가동 계획"
    )
    report = _replace_visible_fact_evidence(
        _valid_report(),
        "future-02",
        evidence=evidence,
        claim=(
            "회사는 현지 규제 허가 완료를 선행 조건으로 미국 AlphaX "
            "생산 설비 가동을 계획했다."
        ),
        subject_scope="AlphaX 생산 설비",
        plan_status="conditional",
        plan_timing="",
        plan_condition="현지 규제 허가 완료를 선행 조건",
        plan_expected_effect="",
        plan_execution_signal="AlphaX 생산 설비 가동",
        evidence_support_terms=["AlphaX", "가동"],
    )

    assert validate_publishable(report).publishable is True


def test_completed_approval_procedure_keeps_the_plan_approved_and_unexecuted() -> None:
    evidence = "기업가치 제고 계획: 이사회가 승인했고 AlphaX 생산 설비 가동 계획"
    report = _replace_visible_fact_evidence(
        _valid_report(),
        "future-02",
        evidence=evidence,
        claim="이사회가 승인했고 회사는 AlphaX 생산 설비 가동을 계획했다.",
        subject_scope="AlphaX 생산 설비",
        plan_status="approved",
        plan_timing="",
        plan_condition="",
        plan_expected_effect="",
        plan_execution_signal="AlphaX 생산 설비 가동",
        evidence_support_terms=["AlphaX", "가동"],
    )

    assert validate_publishable(report).publishable is True


def test_past_result_cannot_be_published_as_a_plan_expected_effect() -> None:
    evidence = (
        "기업가치 제고 계획: 2025년 매출 증가를 확인했고 "
        "2027년 AlphaX 생산 설비 가동을 계획"
    )
    report = _replace_visible_fact_evidence(
        _valid_report(),
        "future-02",
        evidence=evidence,
        claim=(
            "회사는 2025년 매출 증가를 확인했고 2027년 AlphaX 생산 설비 "
            "가동을 계획했다."
        ),
        subject_scope="AlphaX 생산 설비",
        plan_status="announced",
        plan_timing="2027년",
        plan_condition="",
        plan_expected_effect="매출 증가",
        plan_execution_signal="AlphaX 생산 설비 가동",
        evidence_support_terms=["AlphaX", "가동"],
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("회사 제시 효과 표현이 아닙니다" in reason for reason in validation.reasons)


def test_cancelled_plan_cannot_be_published_as_current_future_strategy() -> None:
    evidence = "기업가치 제고 계획: 2024년 AlphaX 출시 계획을 취소"
    report = _replace_visible_fact_evidence(
        _valid_report(),
        "future-02",
        evidence=evidence,
        claim="회사는 2024년 AlphaX 출시 계획을 취소했다.",
        subject_scope="AlphaX",
        plan_status="announced",
        plan_timing="2024년",
        plan_condition="",
        plan_expected_effect="",
        plan_execution_signal="AlphaX 출시",
        evidence_support_terms=["AlphaX", "출시"],
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("취소·철회·중단된 계획" in reason for reason in validation.reasons)


def test_plan_timing_before_report_date_is_not_a_current_future_plan() -> None:
    evidence = "기업가치 제고 계획: 2024년 AlphaX 출시 계획"
    report = _replace_visible_fact_evidence(
        _valid_report(),
        "future-02",
        evidence=evidence,
        claim="회사는 2024년 AlphaX 출시를 계획했다.",
        subject_scope="AlphaX",
        plan_status="announced",
        plan_timing="2024년",
        plan_condition="",
        plan_expected_effect="",
        plan_execution_signal="AlphaX 출시",
        evidence_support_terms=["AlphaX", "출시"],
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("예정 시점이 지난 계획" in reason for reason in validation.reasons)


def test_mou_only_relationship_is_not_a_current_operating_partner() -> None:
    evidence = "반기보고서 제휴 현황: AlphaWorks와 유통 협력 MOU를 체결"
    report = _replace_visible_fact_evidence(
        _valid_report(),
        "operations-core-01",
        evidence=evidence,
        claim="회사는 AlphaWorks와 유통 협력 MOU를 체결했다.",
        claim_type="partner_role",
        subject_scope="AlphaWorks",
        relationship_or_action="유통 협력",
        value_chain_stage="distribution",
        relationship_type="joint_business",
        evidence_support_terms=["AlphaWorks", "MOU"],
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("현재 반복 운영 역할" in reason for reason in validation.reasons)


def test_numeric_fact_requires_recomputable_round_half_up_chain() -> None:
    report = _replace_fact(
        _valid_report(),
        "biz-mix-03",
        numeric_checks=["6.25|1|1|6.2"],
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("재계산과 다릅니다" in reason for reason in validation.reasons)


def test_public_table_cannot_repeat_won_and_eokwon_units() -> None:
    report = _valid_report()
    sections = []
    for section in report.sections:
        if section.cell != "past_changes":
            sections.append(section)
            continue
        table = section.tables[0]
        rows = [list(row) for row in table.rows]
        rows[0][1] = "30,903,000,000원 (309.0억원)"
        sections.append(replace(section, tables=[replace(table, rows=rows)]))

    validation = validate_publishable(replace(report, sections=sections))

    assert validation.publishable is False
    assert any("단위를 붙이지" in reason for reason in validation.reasons)


def test_public_eokwon_table_requires_hidden_won_ledger() -> None:
    report = _valid_report()
    sections = []
    for section in report.sections:
        if section.cell != "past_changes":
            sections.append(section)
            continue
        table = section.tables[0]
        sections.append(replace(section, tables=[replace(table, raw_rows=[])]))

    validation = validate_publishable(replace(report, sections=sections))

    assert validation.publishable is False
    assert any("원 단위 원값" in reason for reason in validation.reasons)


def test_causal_claim_requires_structured_direct_evidence() -> None:
    report = _replace_fact(
        _valid_report(),
        "past-change-01",
        supports_causality=False,
        causal_subject="",
        causal_mechanism="",
        causal_outcome="",
        causal_evidence="",
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("직접 인과 근거" in reason for reason in validation.reasons)


def test_comparator_must_be_a_different_official_legal_entity() -> None:
    report = _replace_fact(
        _valid_report(),
        "competition-01",
        comparator_source_id="JY-S2",
        comparison_target="주식회사 진영",
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("source_id가 같습니다" in reason for reason in validation.reasons)
    assert any("자사와 구분" in reason for reason in validation.reasons)


@pytest.mark.parametrize("judgment", ["", "comparison_unknown"])
def test_public_comparison_requires_a_closed_explicit_judgment(
    judgment: str,
) -> None:
    report = _replace_fact(
        _valid_report(),
        "competition-01",
        comparison_judgment=judgment,
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("공개 판정이 확정되지" in reason for reason in validation.reasons)


def test_comparison_limitation_is_internal_only_and_zero_valid_axes_stop_release() -> None:
    report = _replace_fact(
        _valid_report(),
        "competition-01",
        claim_type="comparison_limitation",
        comparison_judgment="",
    )
    section = next(
        item for item in report.sections if item.cell == "competitive_position"
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert "competitive_position" not in validation.included_section_ids
    assert any("내부 탈락 상태" in reason for reason in validation.reasons)
    assert any(
        "필수 장 competitive_position" in reason
        for reason in validation.reasons
    )
    assert section_content_blocks(report, section) == ()
    with pytest.raises(PublishBlockedError):
        build_published_report(report)


def test_comparison_axes_must_equal_the_context_recomputed_from_both_sources() -> None:
    report = _valid_report()
    target = next(fact for fact in report.fact_records if fact.fact_id == "competition-01")
    forged_conditions = dict(target.comparison_conditions)
    forged_conditions.update(
        customer="사업보고서·대상",
        product="사업보고서·대상",
        market="사업보고서·대상",
    )
    report = _replace_fact(
        report,
        target.fact_id,
        comparison_conditions=forged_conditions,
    )

    validation = validate_publishable(report)

    assert not validation
    assert any("다시 계산한 값과 다릅니다" in reason for reason in validation.reasons)


def test_comparison_period_definition_and_scope_must_bind_both_raw_payloads() -> None:
    report = _valid_report()
    target = next(fact for fact in report.fact_records if fact.fact_id == "competition-01")
    forged_conditions = dict(target.comparison_conditions)
    forged_conditions.update(
        self_period="2099",
        comparator_period="2099",
        self_definition="invented",
        comparator_definition="invented",
        self_accounting_scope="Mars",
        comparator_accounting_scope="Mars",
    )
    report = _replace_fact(
        report,
        target.fact_id,
        comparison_period="2099",
        comparison_definition="invented",
        comparison_scope="Mars",
        comparison_conditions=forged_conditions,
    )

    validation = validate_publishable(report)

    assert not validation
    assert any("비교 기간이 양사 공식 원 payload" in reason for reason in validation.reasons)
    assert any("지표 정의가 양사 공식 원 payload" in reason for reason in validation.reasons)
    assert any("회계·사업 범위가 양사 공식 원 payload" in reason for reason in validation.reasons)


def test_duplicate_claim_and_repeated_metric_are_blocked() -> None:
    report = _valid_report()
    first = report.fact_records[0]
    duplicate = replace(first, fact_id="duplicate-fact")

    validation = validate_publishable(
        replace(report, fact_records=[*report.fact_records, duplicate])
    )

    assert validation.publishable is False
    assert any("중복" in reason for reason in validation.reasons)


def test_excluded_preferences_are_blocked_but_welfare_platform_business_is_allowed() -> None:
    assert _forbidden_text_problem("임직원 평균보수와 근속연수")
    assert _forbidden_text_problem("지원자 복리후생 정보")
    assert _forbidden_text_problem("기업용 복지 플랫폼 사업을 운영한다") == ""


@pytest.mark.parametrize("duplicate_number", [1, 0, -1])
def test_source_numbers_must_be_unique_positive(duplicate_number: int) -> None:
    report = _valid_report()
    citations = list(report.citations)
    citations[1] = replace(citations[1], number=duplicate_number)

    validation = validate_publishable(replace(report, citations=citations))

    assert validation.publishable is False
    assert any("출처 번호" in reason for reason in validation.reasons)


def test_report_company_must_bind_every_fact_legal_entity() -> None:
    report = _replace_fact(_valid_report(), "identity-01", legal_entity="주식회사 다른회사")
    validation = validate_publishable(report)
    assert not validation
    assert any("report.company" in reason for reason in validation.reasons)


def test_fact_and_source_dates_cannot_be_after_report_as_of_date() -> None:
    report = _valid_report()
    source = report.citations[0]
    assert isinstance(source, Source)
    citations = [
        replace(item, collected_at="2026-08-20") if item is source else item
        for item in report.citations
    ]
    validation = validate_publishable(replace(report, citations=citations))
    assert not validation
    assert any("보고서 기준일 뒤" in reason for reason in validation.reasons)


def test_source_host_url_document_identity_is_bound_to_fact() -> None:
    report = _valid_report()
    target = report.fact_records[0]
    source = next(
        item for item in report.citations
        if isinstance(item, Source) and item.source_id == target.source_id
    )
    changed_source = replace(
        source,
        document_id="20260318009999",
        url="https://kind.krx.co.kr/external/2026/03/18/20260318009999/20260318009999/11011.htm",
    )
    citations = [changed_source if item is source else item for item in report.citations]
    validation = validate_publishable(replace(report, citations=citations))
    assert not validation
    assert any("document_id가 등록부와 다릅니다" in reason for reason in validation.reasons)


def test_numeric_raw_value_must_exist_in_collected_state_evidence() -> None:
    report = _valid_report()
    target = next(fact for fact in report.fact_records if fact.fact_id == "biz-mix-03")
    evidence = "반기보고서 주요 제품 매출 비중: 열분해유 매출 비율 미공개"
    report = _replace_fact(
        report,
        target.fact_id,
        state_evidence=evidence,
        evidence_support_terms=["열분해유", "매출"],
    )
    citations = [
        replace(
            item,
            evidence_hashes=sorted({*item.evidence_hashes, evidence_text_hash(evidence)}),
        )
        if isinstance(item, Source) and item.source_id == target.source_id
        else item
        for item in report.citations
    ]
    validation = validate_publishable(replace(report, citations=citations))
    assert not validation
    assert any("raw_value 원값" in reason for reason in validation.reasons)


def test_public_table_raw_rows_must_exactly_bind_fact_numeric_ledger() -> None:
    report = _valid_report()
    sections = []
    for section in report.sections:
        if section.cell != "past_changes":
            sections.append(section)
            continue
        table = section.tables[0]
        raw_rows = [list(row) for row in table.raw_rows]
        raw_rows[0][1] = "99999999999"
        sections.append(replace(section, tables=[replace(table, raw_rows=raw_rows)]))
    validation = validate_publishable(replace(report, sections=sections))
    assert not validation
    assert any("원값·표시값·numeric_checks" in reason for reason in validation.reasons)


def test_completed_three_fy_facts_without_one_bound_table_are_blocked() -> None:
    report = _valid_report()
    sections = [
        replace(section, tables=[])
        if section.cell == "past_changes"
        else section
        for section in report.sections
    ]
    validation = validate_publishable(replace(report, sections=sections))
    assert not validation
    assert any("한 개의 공개 실적표" in reason for reason in validation.reasons)


@pytest.mark.parametrize(
    ("fact_id", "claim", "evidence", "needle"),
    [
        (
            "current-01",
            "해외 신규 유통 문제는 모두 해결 완료됐다.",
            "반기보고서: 해외 신규 유통 문제는 모두 해결 완료됐다.",
            "해결 완료된 문제",
        ),
        (
            "future-01",
            "회사는 열분해 설비 14기 가동을 완료했다.",
            "기업가치 제고 자료: 열분해 설비 14기 가동 완료",
            "완료된 실행을 미래 계획",
        ),
        (
            "current-03",
            "회사는 새 품질시험을 시작할 계획이다.",
            "반기보고서: 새 품질시험을 시작할 계획이다.",
            "이미 착수했음",
        ),
        (
            "current-01",
            "현재 과제는 더 이상 문제가 아니다.",
            "반기보고서: 현재 과제는 더 이상 문제가 아니다.",
            "해결 완료된 문제",
        ),
        (
            "future-01",
            "회사의 확장 계획은 이미 모두 실현됐다.",
            "기업가치 제고 자료: 회사의 확장 계획은 이미 모두 실현됐다.",
            "이미 실현·완료된 내용",
        ),
    ],
)
def test_lexical_time_state_cannot_relabel_resolved_or_completed_events(
    fact_id: str, claim: str, evidence: str, needle: str
) -> None:
    report = _valid_report()
    original = next(fact for fact in report.fact_records if fact.fact_id == fact_id)
    report = _replace_fact(
        report,
        fact_id,
        claim=claim,
        state_evidence=evidence,
        evidence_support_terms=[term for term in ("문제", "해결", "열분해", "가동") if term in claim and term in evidence],
    )
    citations = [
        replace(item, evidence_hashes=sorted({*item.evidence_hashes, evidence_text_hash(evidence)}))
        if isinstance(item, Source) and item.source_id == original.source_id
        else item
        for item in report.citations
    ]
    validation = validate_publishable(replace(report, citations=citations))
    assert not validation
    assert any(needle in reason for reason in validation.reasons)


def test_semantic_duplicate_cannot_hide_by_reordering_claim_words() -> None:
    report = _valid_report()
    original = next(fact for fact in report.fact_records if fact.fact_id == "current-02")
    duplicate = replace(
        original,
        fact_id="current-02-paraphrase",
        claim="중국 대리점 8곳과 회사는 MOU를 체결했다.",
    )
    duplicate = replace(duplicate, evidence_binding=fact_evidence_binding(duplicate))
    validation = validate_publishable(
        replace(report, fact_records=[*report.fact_records, duplicate])
    )
    assert not validation
    assert any("의미가 같은 사실" in reason for reason in validation.reasons)


def test_duplicate_fact_cannot_hide_behind_a_cloned_source_id() -> None:
    report = _valid_report()
    original = next(fact for fact in report.fact_records if fact.fact_id == "current-02")
    original_source = next(
        item
        for item in report.citations
        if isinstance(item, Source) and item.source_id == original.source_id
    )
    clone_source = replace(
        original_source,
        number=max(item.number for item in report.citations if isinstance(item, Source)) + 1,
        source_id="cloned-source-id",
    )
    duplicate = replace(
        original,
        fact_id="current-02-cloned-source",
        source_id=clone_source.source_id,
        claim="중국 대리점 8곳과 회사는 MOU를 체결했다.",
    )
    duplicate = replace(duplicate, evidence_binding=fact_evidence_binding(duplicate))

    validation = validate_publishable(
        replace(
            report,
            citations=[*report.citations, clone_source],
            fact_records=[*report.fact_records, duplicate],
        )
    )

    assert not validation
    assert any("URL·document_id" in reason for reason in validation.reasons)
    assert any("의미가 같은 사실" in reason for reason in validation.reasons)
