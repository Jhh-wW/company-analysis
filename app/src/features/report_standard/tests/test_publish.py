from __future__ import annotations

import json
from dataclasses import replace
from datetime import date

import pytest

from src.features.business_candidate.dart_identity import DartCompanyRecord
from src.features.company_comparison.logic import (
    OfficialCompanyBundle,
    build_competitive_position,
)
from src.features.company_comparison.official_sources import (
    VERIFIED_FINAL_URL_FIELD,
    VERIFIED_FINAL_URL_VALUE,
    bind_dart_profile_attestation,
    candidate_sentences_from_fragments,
    register_candidate_sentence_evidence,
)
from src.features.export_pdf.automatic_release import report_sha256
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import FactRecord, Grade, Report, ReportSection
from src.features.provenance.citations import build_citations
from src.features.provenance.sources import (
    Source,
    SourceKind,
    evidence_text_hash,
    has_valid_provenance_seal,
    render_sources,
    seal_collected_source,
)
from src.features.report_standard.constants import (
    CANONICAL_CLAIM_TYPES_BY_SECTION,
    CANONICAL_SECTION_IDS,
    COMPARISON_SHORTFALL_REASON,
    CUSTOMER_MARKET_SHORTFALL_REASON,
    CULTURE_SHORTFALL_REASON,
    CURRENT_CHALLENGES_SHORTFALL_REASON,
    CURRENT_RESPONSE_SHORTFALL_REASON,
    IDENTITY_SUMMARY_SHORTFALL_REASON,
    MINIMUM_CORE_SECTION_IDS,
    PARTIAL_SECTION_SHORTFALL_REASONS,
    PAST_NARRATIVE_SHORTFALL_REASON,
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
    summary_evidence_text,
    summary_verification_binding,
    validate_publishable,
)
from src.features.report_standard.section_content import (
    ContentField,
    section_content_blocks,
)
from src.features.storage.reports import report_from_json, report_to_json
from src.shared.comparison_candidate_basis import (
    parse_comparison_source_basis_v2,
)


def _valid_report() -> Report:
    return build_demo_report()


def _without_sections(report: Report, *section_ids: str) -> Report:
    """장과 그 장이 소유한 사실·요약을 함께 제거해 실제 미수집 상태를 만든다."""

    missing = set(section_ids)
    return replace(
        report,
        sections=[
            section for section in report.sections if section.cell not in missing
        ],
        fact_records=[
            fact for fact in report.fact_records if fact.section_owner not in missing
        ],
        summary_items=[
            item for item in report.summary_items if item.section_id not in missing
        ],
    )


def _summary_for_fact(report: Report, fact_id: str):
    """검증 fact 문장을 그대로 재사용하는 테스트용 요약을 만든다."""

    facts = {fact.fact_id: fact for fact in report.fact_records}
    fact = facts[fact_id]
    fact_ids = [fact_id]
    evidence_text = summary_evidence_text(fact_ids, facts)
    support_terms = list(dict.fromkeys(fact.evidence_support_terms))[:2]
    status = report.summary_items[0].verification_status
    return replace(
        report.summary_items[0],
        text=fact.claim,
        section_id=fact.section_owner,
        fact_ids=fact_ids,
        evidence_text=evidence_text,
        verification_status=status,
        verification_binding=summary_verification_binding(
            fact.claim,
            fact.section_owner,
            fact_ids,
            evidence_text,
            status,
            support_terms,
        ),
        support_terms=support_terms,
    )


def _with_table_only_past(report: Report) -> Report:
    """4장을 공식 3개년 실적표만 남긴 부분 보고서 후보로 바꾼다."""

    performance_ids = {
        fact.fact_id
        for fact in report.fact_records
        if fact.section_owner == "past_changes"
        and fact.claim_type == "historical_performance"
    }
    report = replace(
        report,
        sections=[
            replace(
                section,
                fact_ids=[
                    fact_id
                    for fact_id in section.fact_ids
                    if fact_id in performance_ids
                ],
                prose_lines=[],
                lines=[],
            )
            if section.cell == "past_changes"
            else section
            for section in report.sections
        ],
        fact_records=[
            fact
            for fact in report.fact_records
            if fact.section_owner != "past_changes"
            or fact.fact_id in performance_ids
        ],
    )
    replacement_summary = _summary_for_fact(report, "past-fin-2025")
    return replace(
        report,
        summary_items=[
            replacement_summary if item.section_id == "past_changes" else item
            for item in report.summary_items
        ],
    )


def _with_execution_only_past(report: Report) -> Report:
    """4장의 검증 완료 실행과 3개년 표만 남기고 해석 문장은 제거한다."""

    kept_ids = {
        fact.fact_id
        for fact in report.fact_records
        if fact.section_owner == "past_changes"
        and fact.claim_type in {"completed_execution", "historical_performance"}
    }
    execution = next(
        fact
        for fact in report.fact_records
        if fact.section_owner == "past_changes"
        and fact.claim_type == "completed_execution"
    )
    report = replace(
        report,
        sections=[
            replace(
                section,
                fact_ids=[fact_id for fact_id in section.fact_ids if fact_id in kept_ids],
                prose_lines=[
                    line for line in section.prose_lines if line[0] == execution.claim
                ],
                lines=[line for line in section.lines if line[0] == execution.claim],
            )
            if section.cell == "past_changes"
            else section
            for section in report.sections
        ],
        fact_records=[
            fact
            for fact in report.fact_records
            if fact.section_owner != "past_changes" or fact.fact_id in kept_ids
        ],
    )
    replacement_summary = _summary_for_fact(report, execution.fact_id)
    return replace(
        report,
        summary_items=[
            replacement_summary if item.section_id == "past_changes" else item
            for item in report.summary_items
        ],
    )


def _comparison_bundle(
    corp_code: str,
    company_name: str,
    *,
    revenue: int,
    operating_income: int,
) -> OfficialCompanyBundle:
    def row(account_id: str, account_nm: str, amount: int) -> dict[str, str]:
        return {
            "account_id": account_id,
            "account_nm": account_nm,
            "sj_div": "IS",
            "fs_div": "CFS",
            "thstrm_dt": "2025.01.01 ~ 2025.12.31",
            "thstrm_amount": str(amount),
            "bsns_year": "2025",
            "reprt_code": "11011",
            "currency": "KRW",
        }

    receipt_number = f"20260315{corp_code}"
    return OfficialCompanyBundle(
        corp_code=corp_code,
        company_name=company_name,
        filing={
            "report_nm": "사업보고서 (2025.12)",
            "rcept_no": receipt_number,
            "rcept_dt": "20260315",
            "reprt_code": "11011",
        },
        financials={
            "status": "000",
            "reprt_code": "11011",
            "list": [
                row("ifrs-full_Revenue", "매출액", revenue),
                row(
                    "dart_OperatingIncomeLoss",
                    "영업이익",
                    operating_income,
                ),
            ],
        },
        official_text=(
            "전자 제조 고객 대상 반도체 검사장비 제품을 "
            "반도체 검사장비 시장에 공급한다. "
            "연결재무제표의 매출액과 영업이익을 공시한다."
        ),
    )


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


def test_identity_without_author_summary_is_published_only_as_partial() -> None:
    report = _replace_fact(
        _valid_report(),
        "identity-01",
        claim_type="official_self_definition",
    )

    validation = validate_publishable(report)
    published = build_published_report(report)

    assert validation.publishable is True
    assert published.grade is Grade.PARTIAL
    assert IDENTITY_SUMMARY_SHORTFALL_REASON in published.shortfall_reasons


@pytest.mark.parametrize(
    "claim_type",
    ["official_self_definition", "operating_scope"],
)
def test_partial_identity_accepts_existing_verified_identity_types(
    claim_type: str,
) -> None:
    report = _without_sections(
        _valid_report(),
        "portfolio",
        "competitive_position",
    )
    report = _replace_fact(
        report,
        "identity-01",
        claim_type=claim_type,
    )

    validation = validate_publishable(report)
    published = build_published_report(report)

    assert validation.publishable is True
    assert published.grade is Grade.PARTIAL


def test_upstream_claim_type_enum_is_a_subset_of_the_publish_contract() -> None:
    for section_id, upstream_types in CLAIM_TYPES_BY_SECTION.items():
        assert upstream_types <= CANONICAL_CLAIM_TYPES_BY_SECTION[section_id]


@pytest.mark.parametrize("missing", sorted(MINIMUM_CORE_SECTION_IDS))
def test_every_minimum_core_section_is_required(missing: str) -> None:
    report = _without_sections(_valid_report(), missing)

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any(missing in reason for reason in validation.reasons)
    with pytest.raises(PublishBlockedError):
        build_published_report(report)


@pytest.mark.parametrize(
    ("missing", "expected_reason"),
    sorted(PARTIAL_SECTION_SHORTFALL_REASONS.items()),
)
def test_missing_partial_section_publishes_with_its_standard_shortfall_reason(
    missing: str,
    expected_reason: str,
) -> None:
    report = _without_sections(_valid_report(), missing)

    validation = validate_publishable(report)
    published = build_published_report(report)

    assert validation.publishable is True
    assert missing not in validation.included_section_ids
    assert published.grade is Grade.PARTIAL
    assert missing not in {section.cell for section in published.sections}
    assert all(fact.section_owner != missing for fact in published.fact_records)
    assert published.shortfall_reasons == [expected_reason]


def test_report_requires_verified_three_year_past_section_even_with_future() -> None:
    report = _without_sections(
        _valid_report(),
        "past_changes",
        "current_challenges",
        "portfolio",
        "operations_partners",
        "culture",
        "competitive_position",
    )
    report = replace(
        report,
        summary_items=[_summary_for_fact(report, "identity-01"), *report.summary_items],
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("past_changes 장이 필요" in reason for reason in validation.reasons)
    with pytest.raises(PublishBlockedError):
        build_published_report(report)


def test_minimum_three_section_report_with_table_only_past_publishes_partial() -> None:
    report = _with_table_only_past(_valid_report())
    report = _without_sections(
        report,
        "portfolio",
        "current_challenges",
        "future_strategy",
        "operations_partners",
        "culture",
        "competitive_position",
    )
    report = replace(
        report,
        summary_items=[_summary_for_fact(report, "identity-01"), *report.summary_items],
    )

    validation = validate_publishable(report)
    published = build_published_report(report)

    assert validation.publishable is True
    assert validation.included_section_ids == (
        "identity",
        "business_model",
        "past_changes",
    )
    assert published.grade is Grade.PARTIAL
    assert [section.cell for section in published.sections] == [
        "identity",
        "business_model",
        "past_changes",
    ]
    assert published.shortfall_reasons == [
        PARTIAL_SECTION_SHORTFALL_REASONS[section_id]
        for section_id in (
            "portfolio",
            "current_challenges",
            "future_strategy",
            "operations_partners",
            "culture",
            "competitive_position",
        )
    ] + [PAST_NARRATIVE_SHORTFALL_REASON]


@pytest.mark.parametrize("partial", [False, True])
def test_customer_market_is_optional_only_for_a_partial_report(partial: bool) -> None:
    report = (
        _without_sections(_valid_report(), "portfolio")
        if partial
        else _valid_report()
    )
    customer = next(
        fact
        for fact in report.fact_records
        if fact.section_owner == "business_model"
        and fact.claim_type == "customer_market"
    )
    report = replace(
        report,
        sections=[
            replace(
                section,
                fact_ids=[
                    fact_id for fact_id in section.fact_ids if fact_id != customer.fact_id
                ],
                prose_lines=[
                    line for line in section.prose_lines if line[0] != customer.claim
                ],
                lines=[line for line in section.lines if line[0] != customer.claim],
            )
            if section.cell == "business_model"
            else section
            for section in report.sections
        ],
        fact_records=[
            fact for fact in report.fact_records if fact.fact_id != customer.fact_id
        ],
    )

    validation = validate_publishable(report)
    published = build_published_report(report)

    assert validation.publishable is True
    assert published.grade is Grade.PARTIAL
    assert CUSTOMER_MARKET_SHORTFALL_REASON in published.shortfall_reasons


@pytest.mark.parametrize("partial", [False, True])
def test_past_table_only_is_valid_only_for_a_partial_report(partial: bool) -> None:
    report = _with_table_only_past(_valid_report())
    if partial:
        report = _without_sections(report, "portfolio")

    validation = validate_publishable(report)
    published = build_published_report(report)

    assert validation.publishable is True
    assert published.grade is Grade.PARTIAL
    assert PAST_NARRATIVE_SHORTFALL_REASON in published.shortfall_reasons


def test_each_missing_optional_section_gets_its_own_standard_shortfall_reason() -> None:
    report = _without_sections(
        _valid_report(),
        "current_challenges",
        "culture",
        "competitive_position",
    )

    validation = validate_publishable(report)
    published = build_published_report(report)

    assert validation.publishable is True
    assert published.grade is Grade.PARTIAL
    assert published.shortfall_reasons == [
        CURRENT_CHALLENGES_SHORTFALL_REASON,
        CULTURE_SHORTFALL_REASON,
        COMPARISON_SHORTFALL_REASON,
    ]
    assert not any(
        "5장" in reason and "9장" in reason
        for reason in published.shortfall_reasons
    )
    assert not any(
        "8장" in reason and "9장" in reason
        for reason in published.shortfall_reasons
    )


def test_partial_current_challenges_can_publish_a_verified_issue_without_response() -> None:
    report = _valid_report()
    issue = next(
        fact
        for fact in report.fact_records
        if fact.section_owner == "current_challenges"
        and fact.claim_type == "current_issue"
    )
    current = next(
        section for section in report.sections if section.cell == "current_challenges"
    )
    issue_lines = [
        line for line in current.prose_lines if line[0] == issue.claim
    ]
    report = replace(
        report,
        sections=[
            replace(
                section,
                fact_ids=[issue.fact_id],
                prose_lines=issue_lines,
                lines=list(issue_lines),
            )
            if section.cell == "current_challenges"
            else section
            for section in report.sections
        ],
        fact_records=[
            fact
            for fact in report.fact_records
            if fact.section_owner != "current_challenges"
            or fact.fact_id == issue.fact_id
        ],
    )

    validation = validate_publishable(report)
    published = build_published_report(report)

    assert validation.publishable is True, validation.reasons
    assert published.grade is Grade.PARTIAL
    assert CURRENT_RESPONSE_SHORTFALL_REASON in published.shortfall_reasons
    published_current = next(
        section
        for section in published.sections
        if section.cell == "current_challenges"
    )
    fields = {
        field.label: field.value
        for block in section_content_blocks(published, published_current)
        for field in block.fields
    }
    assert fields["진행 중 대응"] == "공식 근거에서 착수한 대응을 확인하지 못함"


def test_current_response_without_its_issue_is_still_blocked() -> None:
    report = _valid_report()
    response = next(
        fact
        for fact in report.fact_records
        if fact.section_owner == "current_challenges"
        and fact.claim_type == "current_response"
    )
    current = next(
        section for section in report.sections if section.cell == "current_challenges"
    )
    response_lines = [
        line for line in current.prose_lines if line[0] == response.claim
    ]
    report = replace(
        report,
        sections=[
            replace(
                section,
                fact_ids=[response.fact_id],
                prose_lines=response_lines,
                lines=list(response_lines),
            )
            if section.cell == "current_challenges"
            else section
            for section in report.sections
        ],
        fact_records=[
            fact
            for fact in report.fact_records
            if fact.section_owner != "current_challenges"
            or fact.fact_id == response.fact_id
        ],
        summary_items=[
            item
            for item in report.summary_items
            if item.section_id != "current_challenges"
        ],
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("실제 대응은 같은 장의 미해결 문제" in reason for reason in validation.reasons)


def test_summary_requires_fact_binding_exact_evidence_and_reuse_status() -> None:
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


def test_summary_text_cannot_change_after_verification() -> None:
    report = _valid_report()
    first = replace(report.summary_items[0], text="검증 뒤 바꾼 요약문")

    validation = validate_publishable(
        replace(report, summary_items=[first, *report.summary_items[1:]])
    )

    assert validation.publishable is False
    assert any("결속 지문" in reason for reason in validation.reasons)


def test_summary_cannot_rebind_a_changed_sentence_as_verified_reuse() -> None:
    report = _valid_report()
    first = report.summary_items[0]
    changed_text = f"{first.text} 그리고 업계 최고다"
    changed = replace(
        first,
        text=changed_text,
        verification_binding=summary_verification_binding(
            changed_text,
            first.section_id,
            first.fact_ids,
            first.evidence_text,
            first.verification_status,
            first.support_terms,
        ),
    )

    validation = validate_publishable(
        replace(report, summary_items=[changed, *report.summary_items[1:]])
    )

    assert validation.publishable is False
    assert any("글자 그대로 재사용" in reason for reason in validation.reasons)


def test_summary_must_bind_exactly_one_verified_fact() -> None:
    report = _valid_report()
    first = report.summary_items[0]
    other = next(
        fact
        for fact in report.fact_records
        if fact.section_owner == first.section_id
        and fact.fact_id not in first.fact_ids
    )
    facts = {fact.fact_id: fact for fact in report.fact_records}
    fact_ids = [*first.fact_ids, other.fact_id]
    evidence_text = summary_evidence_text(fact_ids, facts)
    changed = replace(
        first,
        fact_ids=fact_ids,
        evidence_text=evidence_text,
        verification_binding=summary_verification_binding(
            first.text,
            first.section_id,
            fact_ids,
            evidence_text,
            first.verification_status,
            first.support_terms,
        ),
    )

    validation = validate_publishable(
        replace(report, summary_items=[changed, *report.summary_items[1:]])
    )

    assert validation.publishable is False
    assert any("정확히 한 개" in reason for reason in validation.reasons)


def test_summary_can_reference_each_section_at_most_once() -> None:
    report = _valid_report()
    first, second, *rest = report.summary_items
    changed = replace(second, section_id=first.section_id)

    validation = validate_publishable(
        replace(report, summary_items=[first, changed, *rest])
    )

    assert validation.publishable is False
    assert any("같은 장" in reason and "중복" in reason for reason in validation.reasons)


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


def test_오래된_newsroom_공식웹은_재봉인해도_현재fact로_출고되지않는다() -> None:
    report = _valid_report()
    target = next(fact for fact in report.fact_records if fact.fact_id == "culture-01")
    source = next(
        item
        for item in report.citations
        if isinstance(item, Source) and item.source_id == target.source_id
    )
    historic = seal_collected_source(
        replace(
            source,
            url=f"https://{source.host}/newsroom/2015-culture",
            document_id="/newsroom/2015-culture",
            location="/newsroom/2015-culture",
            published_at="2015-06-01",
            collected_at="2026-08-19",
            fact_status="과거·현재성 미확정 문서 수집 참고",
        )
    )
    changed = replace(
        target,
        as_of="2015-06-01",
        source_url=historic.url,
        source_document_id=historic.document_id,
        location=historic.location,
        source_date="2015-06-01",
    )
    changed = replace(changed, evidence_binding=fact_evidence_binding(changed))
    validation = validate_publishable(
        replace(
            report,
            citations=[historic if item is source else item for item in report.citations],
            fact_records=[
                changed if fact.fact_id == target.fact_id else fact
                for fact in report.fact_records
            ],
        )
    )

    assert validation.publishable is False
    assert any("최근 문서일" in reason for reason in validation.reasons)


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


def test_past_completed_execution_without_interpretation_publishes_partial() -> None:
    report = _with_execution_only_past(_valid_report())

    validation = validate_publishable(report)
    published = build_published_report(report)

    assert validation.publishable is True, validation.reasons
    assert published.grade is Grade.PARTIAL
    assert PAST_NARRATIVE_SHORTFALL_REASON in published.shortfall_reasons
    published_past = next(
        section for section in published.sections if section.cell == "past_changes"
    )
    fields = {
        field.label: field.value
        for block in section_content_blocks(published, published_past)
        for field in block.fields
    }
    assert fields["확인된 결과·의미"] == "공식 근거에서 결과를 별도로 확인하지 못함"


def test_past_interpretation_without_completed_execution_is_still_blocked() -> None:
    report = _valid_report()
    execution_ids = {
        fact.fact_id
        for fact in report.fact_records
        if fact.section_owner == "past_changes"
        and fact.claim_type == "completed_execution"
    }
    report = replace(
        report,
        sections=[
            replace(
                section,
                fact_ids=[
                    fact_id
                    for fact_id in section.fact_ids
                    if fact_id not in execution_ids
                ],
                prose_lines=[
                    line
                    for line in section.prose_lines
                    if all(
                        line[0] != fact.claim
                        for fact in report.fact_records
                        if fact.fact_id in execution_ids
                    )
                ],
                lines=[
                    line
                    for line in section.lines
                    if all(
                        line[0] != fact.claim
                        for fact in report.fact_records
                        if fact.fact_id in execution_ids
                    )
                ],
            )
            if section.cell == "past_changes"
            else section
            for section in report.sections
        ],
        fact_records=[
            fact for fact in report.fact_records if fact.fact_id not in execution_ids
        ],
    )

    validation = validate_publishable(report)

    assert validation.publishable is False
    assert any("확인된 완료 실행 사실이 필요" in reason for reason in validation.reasons)


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


def test_검증된_영문IR의_현재감소와_미래계획_시간상태를_읽는다() -> None:
    report = _valid_report()
    issue = next(
        fact for fact in report.fact_records if fact.claim_type == "current_issue"
    )
    future = next(
        fact for fact in report.fact_records if fact.claim_type == "future_plan"
    )

    issue = replace(
        issue,
        claim="JYP의 최근 분기 매출이 전년 동기보다 감소했다.",
        state_evidence="Quarterly Revenue decreased 15.1% YoY.",
    )
    future = replace(
        future,
        claim="Stray Kids는 2026년 하반기와 2027년에 IP 활용을 극대화할 계획이다.",
        state_evidence=(
            "Stray Kids IP Leverage Impact Maximization in 2026 H2 & 2027."
        ),
    )

    assert publish_module._temporal_lexical_problems(issue) == []
    assert publish_module._temporal_lexical_problems(future) == []


def test_영문IR의_완료된_release를_미래계획으로_바꾸지_않는다() -> None:
    future = next(
        fact
        for fact in _valid_report().fact_records
        if fact.claim_type == "future_plan"
    )
    future = replace(
        future,
        claim="Stray Kids는 2026년 하반기에 새 앨범을 발매할 계획이다.",
        state_evidence="Stray Kids released a new album in H2 2026.",
    )

    assert any(
        "완료된 실행" in problem
        for problem in publish_module._temporal_lexical_problems(future)
    )


def test_영문IR_Q2와_한국어_2분기는_같은_분기숫자로_검산한다() -> None:
    issue = next(
        fact
        for fact in _valid_report().fact_records
        if fact.claim_type == "current_issue"
    )
    issue = replace(
        issue,
        claim="JYP의 2026년 2분기 매출은 전년 동기보다 15.1% 감소했다.",
        state_evidence="2026 Q2 Quarterly Revenue decreased 15.1% YoY.",
        raw_value="2026 | 2 | 15.1",
        calculation="원문 표시값을 1로 나누어 직접 대조",
        display_value="2026 | 2 | 15.1",
        rounding_rule="ROUND_HALF_UP 미적용(원문 표시값 그대로)",
        numeric_checks=[
            "2026|1|0|2026",
            "2|1|0|2",
            "15.1|1|1|15.1",
        ],
    )

    assert publish_module._numeric_problems(issue) == []


def test_영문IR_Q5와_한국어_5분기는_유효한_분기숫자로_인정하지_않는다() -> None:
    issue = next(
        fact
        for fact in _valid_report().fact_records
        if fact.claim_type == "current_issue"
    )
    issue = replace(
        issue,
        claim="JYP의 2026년 5분기 매출은 전년 동기보다 15.1% 감소했다.",
        state_evidence="2026 Q5 Quarterly Revenue decreased 15.1% YoY.",
        raw_value="2026 | 5 | 15.1",
        calculation="원문 표시값을 1로 나누어 직접 대조",
        display_value="2026 | 5 | 15.1",
        rounding_rule="ROUND_HALF_UP 미적용(원문 표시값 그대로)",
        numeric_checks=[
            "2026|1|0|2026",
            "5|1|0|5",
            "15.1|1|1|15.1",
        ],
    )

    assert publish_module._numeric_problems(issue)


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


def test_jyp_체결완료와_현재유통처확대가_결속된_공식원문은_독립출고검사도_통과한다() -> None:
    evidence = (
        "당사는 Sony Music, TME (Tencent Music Entertainment), Republic Records 등 "
        "글로벌 유수의 음반/음원 유통 전문 회사들과 파트너십을 체결하여 당사가 "
        "제작한 음악 컨텐츠에 대한 글로벌 유통처를 확대해 가고 있습니다."
    )
    operation_role = (
        "당사가 제작한 음악 컨텐츠에 대한 글로벌 유통처를 확대해 가고 있습니다"
    )
    report = _replace_visible_fact_evidence(
        _valid_report(),
        "operations-core-01",
        evidence=evidence,
        claim=(
            "회사는 Sony Music 등 유통 전문 회사들과 체결한 파트너십으로 "
            "글로벌 유통처를 확대하고 있다."
        ),
        claim_type="partner_role",
        subject_scope="Sony Music",
        relationship_or_action=operation_role,
        value_chain_stage="distribution",
        relationship_type="distribution",
        evidence_support_terms=["Sony Music", "파트너십", "글로벌 유통처"],
    )

    validation = validate_publishable(report)

    assert validation.publishable is True, validation.reasons


@pytest.mark.parametrize(
    "evidence",
    [
        (
            "가나다전자는 AlphaWorks를 소개했다. 가나다전자는 BetaWorks와 유통 "
            "파트너십을 체결하여 글로벌 유통처를 확대해 가고 있습니다."
        ),
        (
            "가나다전자는 AlphaWorks, 신규 파트너 후보를 소개한 뒤 BetaWorks, "
            "GammaWorks 등 글로벌 유통 전문 회사들과 파트너십을 체결하여 "
            "글로벌 유통처를 확대해 가고 있습니다."
        ),
    ],
)
def test_다른법인_계약과_가까운_이름은_현재유통파트너로_오결속하지_않는다(
    evidence: str,
) -> None:
    report = _replace_visible_fact_evidence(
        _valid_report(),
        "operations-core-01",
        evidence=evidence,
        claim="회사는 AlphaWorks와 체결한 파트너십으로 유통처를 확대하고 있다.",
        claim_type="partner_role",
        subject_scope="AlphaWorks",
        relationship_or_action="글로벌 유통처를 확대해 가고 있습니다",
        value_chain_stage="distribution",
        relationship_type="distribution",
        evidence_support_terms=["AlphaWorks", "유통처"],
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


@pytest.mark.parametrize("source_role", ["self", "comparator"])
def test_attestation_only는_자사나_비교사_fact의_직접근거가_될수없다(
    source_role: str,
) -> None:
    report = _valid_report()
    fact = next(
        item for item in report.fact_records if item.fact_id == "competition-01"
    )
    source_id = (
        fact.source_id if source_role == "self" else fact.comparator_source_id
    )
    citations = [
        seal_collected_source(replace(source, provenance_role="attestation_only"))
        if isinstance(source, Source) and source.source_id == source_id
        else source
        for source in report.citations
    ]

    validation = validate_publishable(replace(report, citations=citations))

    assert validation.publishable is False
    assert any(
        "attestation_only Source" in reason for reason in validation.reasons
    )


def test_실서비스_9장은_후보선정_source를_공개하고_used_in을_보존한다() -> None:
    report = _valid_report()
    candidate_evidence = "주식회사 베타는 당사의 경쟁사입니다."
    candidate_source = seal_collected_source(
        Source(
            number=max(source.number for source in report.citations) + 1,
            kind=SourceKind.FILING,
            label=f"{report.company} 2025 사업보고서 경쟁 현황",
            disclosed_at="2026-03-15",
            collected_at="2026-08-19",
            source_id="candidate-origin-source",
            title="2025 사업보고서",
            publisher=report.company,
            host="dart.fss.or.kr",
            url=(
                "https://dart.fss.or.kr/dsaf001/main.do?"
                "rcpNo=20260315000001"
            ),
            document_id="20260315000001",
            location="사업의 내용 · 경쟁 현황",
            source_type="공식 공시",
            fact_status="공시 실제값",
            used_in=["identity"],
            evidence_hashes=[evidence_text_hash(candidate_evidence)],
        )
    )
    candidate_fact = FactRecord(
        fact_id="candidate-origin-fact",
        legal_entity=report.company,
        subject_scope="반도체 검사장비 시장",
        relationship_or_action="주식회사 베타는 당사의 경쟁사",
        claim=candidate_evidence,
        claim_type="identity_summary",
        section_owner="identity",
        time_state="standing",
        as_of="2026-03-15",
        source_id=candidate_source.source_id,
        source_type=candidate_source.source_type,
        source_title=candidate_source.title,
        source_publisher=candidate_source.publisher,
        source_host=candidate_source.host,
        source_url=candidate_source.url,
        source_document_id=candidate_source.document_id,
        location=candidate_source.location,
        status="verified",
        fact_status="actual",
        verification_status="verified",
        state_evidence=candidate_evidence,
        source_date="2026-03-15",
        evidence_support_terms=["주식회사 베타", "경쟁사"],
    )
    candidate_fact = replace(
        candidate_fact,
        evidence_binding=fact_evidence_binding(candidate_fact),
    )
    identity = next(section for section in report.sections if section.cell == "identity")
    draft = replace(
        report,
        sections=[
            replace(
                section,
                prose_lines=[
                    *section.prose_lines,
                    (candidate_fact.claim, f"[{candidate_source.number}]"),
                ],
                fact_ids=[*section.fact_ids, candidate_fact.fact_id],
            )
            if section.cell == identity.cell
            else section
            for section in report.sections
        ],
        citations=[*report.citations, candidate_source],
        fact_records=[*report.fact_records, candidate_fact],
    )
    comparison = build_competitive_position(
        draft,
        self_bundle=_comparison_bundle(
            "00000001",
            report.company,
            revenue=2_000,
            operating_income=300,
        ),
        catalog=(
            DartCompanyRecord("00000001", report.company),
            DartCompanyRecord("00000002", "주식회사 베타"),
        ),
        fetch_comparator=lambda _record: _comparison_bundle(
            "00000002",
            "주식회사 베타",
            revenue=1_000,
            operating_income=50,
        ),
        collected_on="2026-08-19",
    )
    draft = replace(
        draft,
        sections=[
            section
            for section in draft.sections
            if section.cell != "competitive_position"
        ]
        + [comparison.section],
        citations=[*draft.citations, *comparison.sources],
        fact_records=[
            fact
            for fact in draft.fact_records
            if fact.section_owner != "competitive_position"
        ]
        + list(comparison.facts),
    )

    published = build_published_report(draft)
    competitive = next(
        section
        for section in published.sections
        if section.cell == "competitive_position"
    )
    blocks = section_content_blocks(published, competitive)
    published_candidate_source = next(
        source
        for source in published.citations
        if source.source_id == candidate_source.source_id
    )

    assert all(candidate_source.number in block.source_numbers for block in blocks)
    assert "competitive_position" in published_candidate_source.used_in
    assert "identity" in published_candidate_source.used_in
    # build_published_report의 최종 used_in 투영은 수집 봉인 범위 밖이다.
    assert has_valid_provenance_seal(published_candidate_source)


def test_공식웹_v2_순수경쟁후보는_JSON_재출고에도_attester를_보존하되_숨긴다() -> None:
    report = _valid_report()
    candidate_evidence = "당사는 주식회사 베타와 경쟁 관계에 있다."
    fragments = register_candidate_sentence_evidence(
        {
            21: {
                "종류": "홈페이지",
                "원문": candidate_evidence,
                "출처": "https://www.jinyoung.example/company/competition",
                VERIFIED_FINAL_URL_FIELD: VERIFIED_FINAL_URL_VALUE,
                "문서명": "경쟁 환경",
                "원문위치": "/company/competition",
            }
        }
    )
    bound = bind_dart_profile_attestation(
        fragments,
        profile={
            "status": "000",
            "corp_code": "00000001",
            "corp_name": report.company,
            "hm_url": "https://www.jinyoung.example",
        },
        corp_code="00000001",
        company_name=report.company,
        collected_on="2026-08-19",
    )
    assert bound.attester is not None

    candidate_sources = build_citations(
        bound.fragments,
        filing=None,
        collected_on=date(2026, 8, 19),
        company_publisher=report.company,
    )
    candidate_sources.append(bound.attester)
    candidate_rows = candidate_sentences_from_fragments(
        bound.fragments,
        candidate_sources,
    )
    candidate_source = next(
        source for source in candidate_sources if source.number == 21
    )
    attester = bound.attester
    comparison = build_competitive_position(
        report,
        self_bundle=_comparison_bundle(
            "00000001",
            report.company,
            revenue=2_000,
            operating_income=300,
        ),
        catalog=(
            DartCompanyRecord("00000001", report.company),
            DartCompanyRecord("00000002", "주식회사 베타"),
        ),
        fetch_comparator=lambda _record: _comparison_bundle(
            "00000002",
            "주식회사 베타",
            revenue=1_000,
            operating_income=50,
        ),
        collected_on="2026-08-19",
        official_candidate_sentences=candidate_rows,
        candidate_source_registry=candidate_sources,
    )
    basis = parse_comparison_source_basis_v2(
        comparison.facts[0].comparison_basis
    )
    assert basis is not None
    assert basis["candidate_source_id"] == candidate_source.source_id
    assert basis["self_attestation_source_id"] == attester.source_id
    assert basis["evidence_text"] == candidate_evidence

    draft = replace(
        report,
        sections=[
            section
            for section in report.sections
            if section.cell != "competitive_position"
        ]
        + [comparison.section],
        citations=[*report.citations, *comparison.sources],
        fact_records=[
            fact
            for fact in report.fact_records
            if fact.section_owner != "competitive_position"
        ]
        + list(comparison.facts),
    )

    published = build_published_report(draft)
    restored = report_from_json(report_to_json(published))
    assert validate_publishable(restored).publishable is True
    republished = build_published_report(restored)
    published_sources = {
        source.source_id: source for source in republished.citations
    }
    published_candidate = published_sources[candidate_source.source_id]
    published_attester = published_sources[attester.source_id]

    assert published_candidate.domain_attestation_source_id == attester.source_id
    assert "competitive_position" in published_candidate.used_in
    assert published_attester.provenance_role == "attestation_only"
    assert "competitive_position" in published_attester.used_in
    rendered = render_sources(republished.citations)
    assert published_candidate.label in rendered
    assert published_attester.label not in rendered

    old_candidate_source = seal_collected_source(
        replace(
            candidate_source,
            published_at="2022-06-01",
            collected_at="2022-06-20",
            fact_status="과거·현재성 미확정 문서 수집 참고",
        )
    )
    old_basis = dict(basis)
    old_basis["source_date"] = "2022-06-01"
    old_fact = replace(
        comparison.facts[0],
        comparison_basis=json.dumps(
            old_basis,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
    )
    old_sources = {
        source.source_id: (
            old_candidate_source
            if source.source_id == candidate_source.source_id
            else source
        )
        for source in comparison.sources
    }
    old_problems = publish_module._comparison_candidate_basis_problems(
        old_fact,
        old_sources,
        {old_fact.fact_id: old_fact},
        set(),
        report_as_of="2026-08-19",
    )
    assert any("현재성 상한" in problem for problem in old_problems)


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
    assert not any(
        "필수 장 competitive_position" in reason
        for reason in validation.reasons
    )
    assert section_content_blocks(report, section) == ()
    with pytest.raises(PublishBlockedError):
        build_published_report(report)


def test_verified_sections_one_to_eight_publish_as_basic_report_without_comparison() -> None:
    report = _valid_report()
    report = replace(
        report,
        grade=Grade.PARTIAL,
        sections=[
            section
            for section in report.sections
            if section.cell != "competitive_position"
        ],
        fact_records=[
            fact
            for fact in report.fact_records
            if fact.section_owner != "competitive_position"
        ],
    )

    validation = validate_publishable(report)
    published = build_published_report(report)

    assert validation.publishable is True
    assert "competitive_position" not in validation.included_section_ids
    assert published.grade is Grade.PARTIAL
    assert len(published.sections) == 8
    assert published.shortfall_reasons
    assert "9장 동종업계 비교" in published.shortfall_reasons[0]


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
