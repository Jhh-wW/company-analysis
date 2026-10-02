"""원장 보존·구형 봉인 호환·새 표시 그룹과 자료 범위의 회귀."""

from dataclasses import replace

import pytest

from src.core.report_display import reader_scope_notes
from src.features.pipeline.port import Grade, Report, ReportSection, ReportTable, SourceStatus, SummaryItem
from src.features.provenance.sources import Source, SourceKind
from src.features.report_standard.constants import SECTION_SPECS
from src.features.report_standard.public_projection import build_public_projection
from src.features.report_standard.reader_display import audit_notes, citation_groups, reader_notes, section_display_content, summary_notes
from src.shared.report_generation.models import canonical_sha256, canonical_value
from src.shared.report_generation.public_projection import PublicProjectionError, build_report_digest, public_report_projection_from_dict, public_report_projection_to_dict
from src.shared.report_generation.constants import ENGINE_V2_SCHEMA_VERSION
from src.features.report_standard.reader_display_constants import (
    NUMERIC_SCOPE_NOTE, SEMANTIC_SCOPE_NOTE, UNVERIFIED_TABLE_NOTE,
)


def _report() -> Report:
    return Report(company="합성 검증 대상", job="", corp_type="비상장 외감", grade=Grade.PARTIAL,
                  schema_version=ENGINE_V2_SCHEMA_VERSION,
                  sections=[ReportSection(cell=spec.section_id, title=spec.title, display_number=spec.display_number) for spec in SECTION_SPECS])


def _source(number: int, **changes) -> Source:
    return replace(Source(number=number, kind=SourceKind.NEWS, label="동일 기사", title="동일 기사",
                          source_id=f"news-{number}", url="https://example.invalid/article?a=1", host="example.invalid",
                          document_id="same-document", published_at="2026-09-01", document_content_sha256="a" * 64,
                          location=f"{number * 1000}-{number * 1000 + 100}", used_in=["identity"]), **changes)


def test_same_article_keeps_all_source_numbers_and_ledger():
    report = replace(_report(), citations=[_source(1), _source(2, exact_evidence_hashes=["b" * 64], used_in=["future_strategy"])])
    before = canonical_sha256(report.citations)
    rows = citation_groups(report)
    assert len(rows) == 1 and rows[0].numbers == (1, 2)
    assert rows[0].location == "본문 발췌"
    assert rows[0].used_in_display == "1장 · 6장"
    assert canonical_sha256(report.citations) == before


@pytest.mark.parametrize("change", [{"document_content_sha256": "b" * 64}, {"published_at": "2026-09-02"}, {"url": "https://example.invalid/article?a=2"}, {"document_content_sha256": ""}])
def test_article_identity_mismatch_stays_separate(change):
    report = replace(_report(), citations=[_source(1), _source(2, **change)])
    assert len(citation_groups(report)) == 2


def test_reader_failure_notice_is_fixed_text_without_internal_diagnostics():
    report = replace(_report(), sources=[SourceStatus("뉴스", "failed", "SECRET 내부 flag threshold"), SourceStatus("회사 공식 IR", "failed")])
    notes = " ".join(reader_notes(report))
    assert "일부 항목" in notes and "수집을 끝까지 완료하지 못했습니다" in notes
    assert "전체가 없다는 뜻은 아닙니다" in notes and "SECRET" not in notes


@pytest.mark.parametrize("reason,expected", [
    ("숫자·날짜 문장의 항목·기간·계산 관계를 원문과 맞춰 확인하지 못해 제외했습니다 (본문 2개). 자료 자체가 없다는 뜻은 아닙니다.", NUMERIC_SCOPE_NOTE),
    ("원문과 맞춰 보지 못한 숫자·날짜 문장 1개를 뺐습니다 (실적).", NUMERIC_SCOPE_NOTE),
    ("핵심 요약에서도 같은 이유로 숫자 문장 1개를 뺐습니다.", NUMERIC_SCOPE_NOTE),
    ("원문과 계획·조건·공식 설명의 범위가 일치하는지 확인하지 못한 문장을 제외했습니다 (도식 1개). 자료 자체가 없다는 뜻은 아닙니다.", SEMANTIC_SCOPE_NOTE),
    ("표와 도식은 아직 하나씩 확인하지 못했습니다. 숫자를 그대로 인용하기 전에 부록의 원문을 함께 확인해 주세요.", UNVERIFIED_TABLE_NOTE),
])
def test_evidence_available_limits_show_scope_without_exposing_internal_reason(reason, expected):
    report = replace(_report(), publication_policy="evidence-available-v1", shortfall_reasons=[reason, reason, "SECRET 내부 오류 코드"])
    before = canonical_sha256(report)
    notes = reader_notes(report)
    assert notes.count(expected) == 1
    assert "부분 완성" in notes[0]
    assert "SECRET" not in " ".join(notes)
    assert canonical_sha256(report) == before


def test_missing_scope_reason_and_other_publication_policy_do_not_invent_limits():
    reason = "표와 도식은 아직 하나씩 확인하지 못했습니다."
    assert UNVERIFIED_TABLE_NOTE not in reader_notes(replace(_report(), publication_policy="evidence-available-v1"))
    assert UNVERIFIED_TABLE_NOTE not in reader_notes(replace(_report(), publication_policy="structured-safety-v1", shortfall_reasons=[reason]))


def test_reader_grade_notice_matches_actual_grade():
    partial = " ".join(reader_notes(_report()))
    incomplete = " ".join(reader_notes(replace(_report(), grade="미완성")))
    complete = " ".join(reader_notes(replace(_report(), grade=Grade.COMPLETE)))
    assert "부분 완성 보고서" in partial
    assert "미완성 보고서" in incomplete and "부분 완성 보고서" not in incomplete
    assert "자료 범위:" not in complete


def test_old_sealed_reader_notes_remain_exact_even_when_current_reasons_exist():
    projection = replace(build_public_projection(_report()), reader_notes=("발행 당시 안내입니다.",))
    report = replace(_report(), public_projection=projection, publication_policy="evidence-available-v1", shortfall_reasons=["표와 도식은 아직 하나씩 확인하지 못했습니다."])
    assert reader_scope_notes(report) == ("발행 당시 안내입니다.",)


def test_legacy_projection_omits_new_fields_and_preserves_canonical_digest():
    current = build_public_projection(_report())
    old = replace(current, citation_groups=(), reader_notes=(), summary_notes=())
    wire = public_report_projection_to_dict(old)
    assert not {"citation_groups", "reader_notes", "summary_notes"} & wire.keys()
    assert not {"citation_groups", "reader_notes", "summary_notes"} & canonical_value(old).keys()
    restored = public_report_projection_from_dict(wire)
    assert build_report_digest(restored) == build_report_digest(old)
    assert reader_scope_notes(replace(_report(), public_projection=restored)) == ()


def test_new_projection_roundtrip_and_group_exact_cover():
    projection = build_public_projection(replace(_report(), citations=[_source(1), _source(2)]))
    assert public_report_projection_from_dict(public_report_projection_to_dict(projection)) == projection
    with pytest.raises(PublicProjectionError, match="정확히 한 번"):
        replace(projection, citation_groups=(replace(projection.citation_groups[0], numbers=(1,)),))


def test_typed_unaudited_note_does_not_rewrite_claim_or_summary():
    text = "2024년 매출은 100억원이다. [1]"
    table = ReportTable(caption="재무", headers=["연도", "매출"], rows=[["2024", "100"]], cite="[1]", unaudited_years=["2024"])
    section = ReportSection(cell="past_changes", title="실적", prose_lines=[(text, "")], prose_paragraphs=[text], tables=[table])
    report = replace(_report(), sections=[section], summary_items=[SummaryItem(text=text, section_id="past_changes")])
    assert "감사받지 않은 비교 재무제표" in audit_notes(section)[0]
    assert summary_notes(report)[0][0] == "01"
    assert section.prose_paragraphs == [text] and report.summary_items[0].text == text
    assert audit_notes(section, "2024년 다른 자료다. [2]") == ()


def test_only_known_notice_loses_paragraph_number():
    notice = "확인된 자료가 부족해 이 장은 비어 있습니다."
    section = ReportSection(cell="culture", title="문화", prose_paragraphs=[notice, "출처가 없는 일반 문장을 안내로 추측하지 않는다."])
    paragraphs, guidance = section_display_content(section)
    assert paragraphs == ("출처가 없는 일반 문장을 안내로 추측하지 않는다.",)
    assert guidance == (notice,)
