"""검수 결과를 다른 행·원문·장으로 재사용할 수 없고 정상 도식은 보존한다."""

import json
from dataclasses import replace

import pytest

from src.features.composer.diagram_check import check_diagrams, check_diagram_numbers
from src.features.composer.flow_review_binding import (
    bind_reviewed_flow_row, filter_reviewed_flow_rows, flow_review_problem,
)
from src.features.composer.flow_review_constants import (
    FLOW_REVIEW_BINDING_INVALID, FLOW_REVIEW_BINDING_MISSING,
)
from src.features.composer.logic import parse_flow_rows
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow

SECTION = "operations_partners"
BASELINE = "2026-09-23"
SOURCE = CollectedFragment("one", "사업내용", "회사는 폴리우레탄 수액세트를 의료기관에 공급한다.",
                           document_identity="document:example:filing",
                           document_content_sha256="a" * 64, document_date="2026-08-01")
ROW = FlowRow(("폴리우레탄 수액세트", "의료기관에 공급", "의료기관"), ("one",))


def bind(row=ROW):
    return bind_reviewed_flow_row(row, section_id=SECTION, fragments={"one": SOURCE},
                                 review_path="legacy", baseline_date=BASELINE)


def test_unreviewed_row_cannot_be_published_even_with_a_real_citation():
    assert flow_review_problem(ROW, section_id=SECTION, fragments={"one": SOURCE}, baseline_date=BASELINE) == FLOW_REVIEW_BINDING_MISSING
    diagnostics = []
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=(ROW,)),))
    checked = filter_reviewed_flow_rows(report, (SOURCE,), baseline_date=BASELINE, diagnostics=diagnostics)
    assert checked.sections[0].flow_rows == ()
    assert diagnostics[0]["reason_code"] == FLOW_REVIEW_BINDING_MISSING
    assert "verification_items" in diagnostics[0]


def test_valid_binding_preserves_cells_citations_and_exact_source():
    row = bind()
    assert row.cells == ROW.cells and row.citations == ROW.citations
    assert row.review_binding is not None
    assert row.review_binding.evidence_refs[0].fragment_id == "one"
    assert flow_review_problem(row, section_id=SECTION, fragments={"one": SOURCE}, baseline_date=BASELINE) == ""


@pytest.mark.parametrize("changes", [
    {"cells": ("폴리우레탄 수액세트", "개인에게 공급", "의료기관")},
    {"citations": ()}, {"citations": ("one", "two")},
    {"citations": ("one", "one")}, {"citations": (" one",)},
])
def test_changed_row_cannot_reuse_the_review_binding(changes):
    row = replace(bind(), **changes)
    assert flow_review_problem(row, section_id=SECTION, fragments={"one": SOURCE}, baseline_date=BASELINE) == FLOW_REVIEW_BINDING_INVALID


@pytest.mark.parametrize("changes", [
    {"text": SOURCE.text + " "},
    {"document_identity": "document:example:other"},
    {"document_content_sha256": "b" * 64},
    {"document_date": "2026-09-01"},
    {"fragment_id": "other"},
    {"source_url": "https://other.example/document"},
    {"supported_claim_slots": ("portfolio:product_role",)},
    {"document_title": "다른 문서 제목"},
    {"location": "다른 문단"},
])
def test_changed_source_cannot_reuse_the_review_binding(changes):
    assert flow_review_problem(bind(), section_id=SECTION, fragments={"one": replace(SOURCE, **changes)}, baseline_date=BASELINE) == FLOW_REVIEW_BINDING_INVALID


@pytest.mark.parametrize("section,baseline", [("portfolio", BASELINE), (SECTION, ""), (SECTION, "2026-09-24")])
def test_section_and_baseline_date_belong_to_the_review_input(section, baseline):
    assert flow_review_problem(bind(), section_id=section, fragments={"one": SOURCE}, baseline_date=baseline) == FLOW_REVIEW_BINDING_INVALID


@pytest.mark.parametrize("citations", [(), ("missing",), ("one", "one"), ("",), (" one",)])
def test_setter_rejects_missing_empty_or_duplicate_sources(citations):
    with pytest.raises(ValueError):
        bind(replace(ROW, citations=citations))


def test_writer_cannot_supply_a_review_receipt():
    payload = {"경로표": [{"칸": list(ROW.cells), "인용": list(ROW.citations),
                         "verified": True, "review_binding": {"review_path": "grouped"}}]}
    parsed = parse_flow_rows(json.dumps(payload, ensure_ascii=False))
    assert len(parsed) == 1 and parsed[0].review_binding is None


def test_review_path_and_rule_version_cannot_be_relabelled():
    row = bind()
    for change in ({"review_path": "grouped"}, {"rule_version": "other"}):
        changed = replace(row, review_binding=replace(row.review_binding, **change))
        assert flow_review_problem(changed, section_id=SECTION, fragments={"one": SOURCE}, baseline_date=BASELINE) == FLOW_REVIEW_BINDING_INVALID


def test_duplicate_fragment_ids_do_not_choose_the_last_source():
    row = bind()
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=(row,)),))
    checked = filter_reviewed_flow_rows(report, (SOURCE, SOURCE), baseline_date=BASELINE)
    assert checked.sections[0].flow_rows == ()


def test_legacy_review_produces_binding_even_when_no_row_was_dropped():
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=(ROW,)),))
    checked, problems = check_diagrams(report, (SOURCE,), lambda _: '{"판정":[{"번호":1,"결과":"참"}]}', baseline_date=BASELINE)
    assert problems == ()
    reviewed, = checked.sections[0].flow_rows
    assert reviewed.review_binding is not None
    assert flow_review_problem(reviewed, section_id=SECTION, fragments={"one": SOURCE}, baseline_date=BASELINE) == ""


@pytest.mark.parametrize("ask", [None, lambda _: '{"판정":[{"번호":1,"결과":"거짓"}]}', lambda _: '{"판정":[{"번호":1,"결과":"애매"}]}'])
def test_no_review_or_non_true_result_cannot_produce_a_public_row(ask):
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=(ROW,)),))
    checked, _ = check_diagrams(report, (SOURCE,), ask, baseline_date=BASELINE)
    assert checked.sections[0].flow_rows == ()


def test_number_check_alone_never_issues_a_semantic_receipt():
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=(ROW,)),))
    checked, _ = check_diagram_numbers(report, (SOURCE,))
    assert checked.sections[0].flow_rows[0].review_binding is None


def test_grouped_review_binds_only_final_true_rows_to_the_exact_inputs():
    from src.features.composer.verify import verify_report

    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=(ROW,)),))
    calls = []

    def reviewer(prompt):
        calls.append(prompt)
        return json.dumps({"판정": [{
            "번호": 1, "장": SECTION, "근거": ["one"], "결과": "참",
        }]}, ensure_ascii=False)

    checked = verify_report(
        report, (SOURCE,), None, reviewer,
        allowed_fragment_ids_by_section={SECTION: frozenset({"one"})},
        baseline_date=BASELINE,
    )
    assert len(calls) == 1
    row, = checked.sections[0].flow_rows
    assert row.review_binding is not None
    assert row.review_binding.review_path == "grouped"
    assert flow_review_problem(row, section_id=SECTION, fragments={"one": SOURCE}, baseline_date=BASELINE) == ""
    assert flow_review_problem(row, section_id=SECTION, fragments={"one": SOURCE}, baseline_date="") == FLOW_REVIEW_BINDING_INVALID
