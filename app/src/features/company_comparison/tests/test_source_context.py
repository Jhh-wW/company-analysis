"""다른 사업 법인의 자기 비교가 보고서 회사 비교로 바뀌지 않는다."""

import hashlib
import json
from dataclasses import replace

import pytest

from src.features.company_comparison.logic import discover_official_source_candidates
from src.features.company_comparison.official_sources import OfficialCandidateSentence
from src.features.company_comparison.stated_differentiator import build_stated_differentiator_result
from src.features.company_comparison.tests.test_logic import _v2_official_registry, CATALOG
from src.features.provenance.sources import Source, SourceKind, seal_collected_source, evidence_text_hash, exact_evidence_text_hash
from src.shared.report_evidence.source_context import source_context_company_subject_problem


def _context(*, owner="주식회사 알파", actor="주식회사 다온", status=""):
    text = " | ".join((actor, "산업장비", "비교 설명", status))
    payload = {"version": "source-context-v1", "text": text,
        "location": f"100-{100+len(text)}", "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "actor": actor, "document_actor": owner, "document_actor_location": f"0-{len(owner)}",
        "document_actor_sha256": hashlib.sha256(owner.encode()).hexdigest(), "origin": "table_row", "status": status, "item": "산업장비"}
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("sentence", ["당사는 베타와 경쟁 관계에 있습니다.", "베타와 경쟁 관계에 있습니다."])
def test_foreign_actor_cannot_become_document_owner_comparison(sentence):
    _bound, sources, rows = _v2_official_registry(sentence)
    rows = tuple(replace(row, source_context_json=_context()) for row in rows)
    assert rows and rows[0].source_context_problem == "source_actor_unbound"
    assert discover_official_source_candidates(rows, sources, CATALOG, self_corp_code="00000001", self_company="주식회사 알파") == ()


def test_explicit_target_comparison_inside_other_actor_context_keeps_existing_checks():
    sentence = "주식회사 알파는 베타와 경쟁 관계에 있습니다."
    _bound, sources, rows = _v2_official_registry(sentence)
    rows = tuple(replace(row, source_context_json=_context()) for row in rows)
    assert rows[0].source_context_problem == ""
    candidates = discover_official_source_candidates(rows, sources, CATALOG, self_corp_code="00000001", self_company="주식회사 알파")
    assert [candidate.candidate_corp_code for candidate in candidates] == ["00000002"]
    assert candidates[0].evidence_text == sentence


def test_same_actor_context_and_empty_legacy_context_allow_existing_self_resolution():
    text = "당사는 베타와 경쟁 관계에 있습니다."
    assert source_context_company_subject_problem(text, _context(actor="주식회사 알파"), company_name="주식회사 알파") == ""
    assert source_context_company_subject_problem(text, "", company_name="주식회사 알파") == ""


def test_target_name_mentioned_later_is_not_explicit_target_subject():
    assert source_context_company_subject_problem("당사는 베타와 경쟁하며 주식회사 알파와 협력합니다.", _context(), company_name="주식회사 알파") == "source_actor_unbound"


@pytest.mark.parametrize(("status", "text", "expected"), [
    ("양산 적용 예정", "당사는 세계 최초로 산업장비의 양산 적용을 완료했습니다.", False),
    ("개발 예정", "당사는 세계 최초로 산업장비를 독자 개발했습니다.", False),
    ("개발 완료, 양산 적용 예정", "당사는 세계 최초로 산업장비를 독자 개발했습니다.", True),
])
def test_stated_differentiator_consumes_same_pending_stage_rule_as_writer(status, text, expected):
    owner = "주식회사 알파"
    source = seal_collected_source(Source(number=1, kind=SourceKind.FILING, label="사업보고서",
        disclosed_at="2026-03-15", collected_at="2026-09-30", source_id="pending-source", title="사업보고서",
        publisher=owner, host="dart.fss.or.kr", url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260315000001",
        document_id="20260315000001", location="사업의 내용", source_type="공식 공시", fact_status="공시 실제값",
        used_in=["competitive_position"], evidence_hashes=[evidence_text_hash(text)], exact_evidence_hashes=[exact_evidence_text_hash(text)]))
    candidate = OfficialCandidateSentence(source, text, source_context_json=_context(owner=owner, actor=owner, status=status))
    assert candidate.source_context_problem == ("" if expected else "source_status_unbound")
    result = build_stated_differentiator_result(company_name=owner, official_candidate_sentences=(candidate,), candidate_source_registry=(source,))
    assert (result is not None) is expected
