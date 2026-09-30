"""검수 결과를 다른 행·원문·장으로 재사용할 수 없고 정상 도식은 보존한다."""

import json
import hashlib
from dataclasses import asdict, replace

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
from src.shared.report_generation.models import canonical_sha256

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


def _source_context(actor="가람제조주식회사", status="양산 예정"):
    owner = "가람제조주식회사"
    text = " | ".join((actor, "산업장비", "장비 개발을 완료했다.", status))
    return json.dumps({
        "version": "source-context-v1", "text": text,
        "location": f"100-{100+len(text)}",
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "actor": actor, "document_actor": owner,
        "document_actor_location": f"0-{len(owner)}",
        "document_actor_sha256": hashlib.sha256(owner.encode()).hexdigest(),
        "origin": "table_row", "status": status, "item": "산업장비",
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("changed_context", [
    _source_context(actor="다온설비주식회사"),
    _source_context(status="양산 완료"),
    "",
])
def test_changed_actor_stage_or_removed_context_cannot_reuse_flow_review(changed_context):
    source = replace(SOURCE, source_context_json=_source_context())
    row = bind_reviewed_flow_row(ROW, section_id=SECTION, fragments={"one": source},
                                 review_path="legacy", baseline_date=BASELINE)
    assert not flow_review_problem(row, section_id=SECTION, fragments={"one": source}, baseline_date=BASELINE)
    changed = replace(source, source_context_json=changed_context)
    assert flow_review_problem(row, section_id=SECTION, fragments={"one": changed}, baseline_date=BASELINE) == FLOW_REVIEW_BINDING_INVALID
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=(row,)),))
    assert filter_reviewed_flow_rows(report, (changed,), baseline_date=BASELINE).sections[0].flow_rows == ()
    assert report.sections[0].flow_rows == (row,)


def test_adding_context_to_old_review_requires_new_review():
    source = replace(SOURCE, source_context_json=_source_context())
    assert flow_review_problem(bind(), section_id=SECTION, fragments={"one": source}, baseline_date=BASELINE) == FLOW_REVIEW_BINDING_INVALID


def test_empty_context_preserves_original_review_bytes_and_source_scope():
    binding = bind().review_binding
    assert canonical_sha256(asdict(binding)) == "e5f8f11e3a62bbb60c68249b230ffb9939f6ce2a6cca0ace5b1f3e50de6bcf08"
    assert binding.evidence_refs[0].source_scope_sha256 == "463ef207a87643bdc584018946f71b34938833995fc6dc2321acb573b75669f9"


@pytest.mark.parametrize("identity", ["", "document:example:filing", "document:dart.fss.or.kr:20260923000001"])
@pytest.mark.parametrize("review_path", ["legacy", "grouped"])
@pytest.mark.parametrize("context,text,expected", [
    (_source_context(actor="다온설비주식회사"), "회사는 산업장비 개발을 완료했다.", 0),
    (_source_context(), "회사는 산업장비 양산을 완료했다.", 0),
    (_source_context(status="개발 완료, 양산 예정"), "회사는 산업장비 개발을 완료했다.", 1),
    ("", "회사는 산업장비 개발을 완료했다.", 1),
])
def test_both_table_review_paths_consume_actor_and_stage_without_inventing_identity(identity, review_path, context, text, expected):
    from src.features.composer.verify import verify_report

    source = replace(SOURCE, fragment_id="one", text=text, document_identity=identity,
                     source_context_json=context)
    row = FlowRow((text, "산업장비", "완료"), ("one",))
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=(row,)),))
    diagnostics = []
    # 개발 사실의 긍정 시험도 기존 역할 결속 계약을 그대로 충족한다.
    reviewer = lambda _: json.dumps({"판정": [{
        "번호": 1, "장": SECTION, "근거": ["one"], "결과": "참",
        "검증근거": {"관계": [{"유형": "역할", "대상": "산업장비", "역할값": "개발", "근거": "one", "원문": text}]},
    }]}, ensure_ascii=False)
    if review_path == "legacy":
        checked, _ = check_diagrams(report, (source,), reviewer, diagnostics=diagnostics, baseline_date=BASELINE)
    else:
        checked = verify_report(report, (source,), None, reviewer, diagnostics=diagnostics,
                                allowed_fragment_ids_by_section={SECTION: frozenset({"one"})}, baseline_date=BASELINE)
    assert len(checked.sections[0].flow_rows) == expected
    if not expected:
        assert any(d.get("reason_code") == "scope_condition_unbound" for d in diagnostics)
    assert report.sections[0].flow_rows == (row,)
    assert source.source_context_json == context and source.document_identity == identity


def test_malformed_nonempty_source_context_is_rejected_at_fragment_boundary():
    with pytest.raises(ValueError):
        replace(SOURCE, source_context_json='{"actor":"가람제조주식회사"}')


@pytest.mark.parametrize("identity", ["", "document:example:filing"])
def test_noncanonical_actor_constraint_never_borrows_uncited_footnote(identity):
    from src.features.composer.entity_scope_constraints import build_entity_scope_contexts
    from src.shared.report_evidence.legacy_fragment_kinds import LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE

    source = replace(SOURCE, document_identity=identity, source_context_json=_source_context())
    footnote = replace(SOURCE, fragment_id="two", document_identity=identity,
                       kind=LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE, text="당기 중 종속기업에서 제외되었다.")
    context, = build_entity_scope_contexts(("one",), {"one": source, "two": footnote})
    assert context.document_identity == identity
    assert context.source_contexts == (source.source_context_json,)
    assert context.constraint_sources == {}
    assert context.cited_sources == context.actor_relation_sources == {"one": source.text}


def test_conflicting_cited_document_hashes_cannot_erase_own_actor_constraint():
    from src.features.composer.entity_scope_constraints import build_entity_scope_contexts

    identity = "document:dart.fss.or.kr:20260923000001"
    source = replace(SOURCE, document_identity=identity, source_context_json=_source_context())
    other = replace(SOURCE, fragment_id="two", document_identity=identity, document_content_sha256="b"*64)
    context, = build_entity_scope_contexts(("one", "two"), {"one": source, "two": other})
    assert context.source_contexts == (source.source_context_json,)
    assert context.constraint_sources == {}


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
