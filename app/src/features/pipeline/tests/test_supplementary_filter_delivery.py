"""불일치 제거 뒤에도 실제 출고 인용·부록 계약이 유지된다."""
from dataclasses import replace

import pytest

from src.features.composer.validate import validate_v2
from src.features.pipeline.constants import EVIDENCE_AVAILABLE_PUBLICATION_POLICY
from src.features.pipeline.port import ReportTable
from src.features.pipeline.supplementary_research_filter import (
    prune_unused_supplementary_citations, reconcile_supplementary_citations,
)
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.output_validation import V2ValidationError
from src.core.source_verification_adapter import supplementary_research_source_verifier
from src.features.pipeline.tests.test_supplementary_research_filter import (
    _assert_storage_roundtrip, _enforce, _with_summaries,
    _news_intake_enabled,
)
from src.features.pipeline.tests.test_supplementary_research_producer_independent import _rendered_report


def test_removed_last_fact_drops_unused_valid_source_before_delivery():
    report, evidence = _rendered_report()
    report = replace(_with_summaries(report), publication_policy=EVIDENCE_AVAILABLE_PUBLICATION_POLICY)
    validate_v2(report)
    first = next(fact for fact in report.fact_records if fact.section_owner == "identity")
    # 출처 신원은 정상이나 사실의 원문 결속이 깨진 경우다.
    tampered = replace(report, fact_records=[
        replace(fact, state_evidence="원문과 다른 근거") if fact is first else fact
        for fact in report.fact_records
    ])
    filtered = _enforce(tampered, evidence).report
    assert filtered is not None
    assert first.fact_id not in {fact.fact_id for fact in filtered.fact_records}
    validate_v2(filtered)
    assert first.source_id not in {source.source_id for source in filtered.citations}
    assert "1" not in filtered.source_grades
    validate_v2(_assert_storage_roundtrip(filtered))
    assert prune_unused_supplementary_citations(filtered) is filtered


def test_restores_implicit_citation_when_first_sentence_was_removed():
    report, evidence = _rendered_report()
    first = next(fact for fact in report.fact_records if fact.section_owner == "identity")
    normal = replace(first, fact_id=first.fact_id + "-normal", claim=first.claim.rstrip("."))
    normal = replace(normal, evidence_binding=fact_evidence_binding(normal))
    report = replace(_with_summaries(report),
        publication_policy=EVIDENCE_AVAILABLE_PUBLICATION_POLICY,
        fact_records=[*report.fact_records, normal],
        sections=[replace(section,
            prose_lines=[*section.prose_lines, (normal.claim, "")],
            prose_paragraphs=[*(text for text, _cite in section.prose_lines), normal.claim],
            fact_ids=[*section.fact_ids, normal.fact_id],
        ) if section.cell == "identity" else section for section in report.sections],
    )
    validate_v2(report)
    tampered = replace(report, fact_records=[
        replace(fact, state_evidence="원문과 다른 근거") if fact.fact_id == first.fact_id else fact
        for fact in report.fact_records
    ])
    filtered = _enforce(tampered, evidence).report
    assert filtered is not None
    identity = next(section for section in filtered.sections if section.cell == "identity")
    assert identity.prose_lines == [(normal.claim + " [1]", "")]
    assert identity.prose_paragraphs == [normal.claim + " [1]"]
    assert identity.fact_ids == [normal.fact_id]
    assert filtered.fact_records[-1] == normal
    validate_v2(_assert_storage_roundtrip(filtered))


def test_keeps_valid_source_still_used_only_by_table():
    report, evidence = _rendered_report()
    first = next(fact for fact in report.fact_records if fact.section_owner == "identity")
    table = ReportTable(caption="기존 검증 표", headers=["항목", "내용"],
                        rows=[["회사 유형", "제조기업"]], cite="[1]", source_cites=["[1]"], row_cites=[["[1]"]])
    report = replace(_with_summaries(report),
        publication_policy=EVIDENCE_AVAILABLE_PUBLICATION_POLICY,
        sections=[replace(section, tables=[table]) if section.cell == "business_model" else section
                  for section in report.sections],
    )
    validate_v2(report)
    tampered = replace(report, fact_records=[
        replace(fact, state_evidence="원문과 다른 근거") if fact is first else fact
        for fact in report.fact_records
    ])
    filtered = _enforce(tampered, evidence).report
    assert filtered is not None
    assert first.source_id in {source.source_id for source in filtered.citations}
    validate_v2(_assert_storage_roundtrip(filtered))


def test_pruning_does_not_hide_source_still_referenced_by_locked_fact():
    report, _evidence = _rendered_report()
    report = replace(report, publication_policy=EVIDENCE_AVAILABLE_PUBLICATION_POLICY,
        sections=[replace(section, prose_lines=[], prose_paragraphs=[])
                  if section.cell == "identity" else section for section in report.sections])
    # 인용 표시를 복원할 본문도 없는 상태를 부록 삭제로 통과시키지 않는다.
    pruned = prune_unused_supplementary_citations(report)
    assert pruned is report
    with pytest.raises(V2ValidationError, match=r"\[1\]"):
        validate_v2(pruned)


def test_restoring_implicit_citation_requires_unchanged_fact_binding():
    report, _evidence = _rendered_report()
    first = next(fact for fact in report.fact_records if fact.section_owner == "identity")
    report = replace(report, publication_policy=EVIDENCE_AVAILABLE_PUBLICATION_POLICY,
        fact_records=[replace(fact, state_evidence="원문과 다른 근거") if fact is first else fact
                      for fact in report.fact_records],
        sections=[replace(section, prose_lines=[(first.claim, "")], prose_paragraphs=[first.claim])
                  if section.cell == "identity" else section for section in report.sections])
    result = reconcile_supplementary_citations(report, source_verifier=supplementary_research_source_verifier())
    assert result is report
    with pytest.raises(V2ValidationError, match=r"\[1\]"):
        validate_v2(result)
