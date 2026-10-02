"""법인 소유 문서와 실제 문단·표행 주어를 구별하는 반례."""

import hashlib
import json

import pytest

from features.evidence_collection.dart_fetcher import _document_header_identity, _xml_to_plain_text
from features.evidence_collection.source_context import (
    table_source_contexts, heading_source_scopes, context_for_candidate,
    prepare_table_contexts, validate_source_context, different_document_actor,
)


_OWNER = "가람제조주식회사"
_OTHER = "다온설비주식회사"


def _xml(row_actor: str = _OTHER) -> bytes:
    return (f'<DOCUMENT><COMPANY-NAME AREGCIK="00000001">{_OWNER}</COMPANY-NAME><BODY>'
            '<TABLE><TR><TH>연구/개발 과제</TH><TH>연구기관</TH><TH>연구결과</TH><TH>비고</TH></TR>'
            f'<TR><TD>산업용 센서</TD><TD>{row_actor}</TD><TD>환경 규제에 대응하는 센서를 개발한다.</TD>'
            '<TD>양산 적용 예정</TD></TR></TABLE></BODY></DOCUMENT>').encode()


def test_exact_table_row_keeps_actor_and_pending_state_without_changing_fragment():
    raw = _xml()
    plain = _xml_to_plain_text(raw)
    contexts = table_source_contexts(raw.decode(), plain, document_actor=_OWNER)
    assert len(contexts) == 1
    context = validate_source_context(contexts[0], document_text=plain)
    assert context["actor"] == _OTHER
    assert context["status"] == "양산 적용 예정"
    start = plain.index("환경 규제에 대응")
    original = "환경 규제에 대응하는 센서를 개발한다."
    selected = context_for_candidate(text=original, start=start, end=start + len(original),
                                     table_contexts=prepare_table_contexts(contexts, document_text=plain), scopes=())
    assert selected == contexts[0]
    assert plain[start:start + len(original)] == original
    assert hashlib.sha256(original.encode()).hexdigest() != context["text_sha256"]


@pytest.mark.parametrize("actor", ["당사", "본사", "회사"])
def test_ambiguous_self_table_actor_is_not_declared_a_different_company(actor):
    raw = _xml(actor)
    assert table_source_contexts(raw.decode(), _xml_to_plain_text(raw), document_actor=_OWNER) == ()


def test_other_company_heading_closes_at_parent_section_and_target_return():
    text = (_OWNER + "\n\n[기타부문]\n\n(1) 영업개황" + _OTHER
            + "의 매출이 증가했다.\n\n(2) 사업구분\n\n당사는 설비를 제조합니다.\n\n"
            "2. 주요 제품 및 서비스\n\n당사는 산업용 센서를 제조합니다.\n\n"
            "[주력부문]\n\n(1) 영업개황" + _OWNER + "\n\n당사는 센서를 제조합니다.")
    scopes = heading_source_scopes(text, document_actor=_OWNER)
    foreign = text.index("당사는 설비")
    target = text.index("당사는 산업용")
    assert different_document_actor(context_for_candidate(text="", start=foreign, end=foreign+2, table_contexts=(), scopes=scopes))
    assert context_for_candidate(text="", start=target, end=target+2, table_contexts=(), scopes=scopes) == ""
    assert not different_document_actor(scopes[-1].context_json)


@pytest.mark.parametrize("injection", [
    '<!-- <COMPANY-NAME AREGCIK="00000002">위장법인</COMPANY-NAME> -->',
    '<!-- <BODY> -->',
    '<![CDATA[<COMPANY-NAME AREGCIK="00000002">위장법인</COMPANY-NAME>]]>',
])
def test_same_strict_xml_header_returns_code_and_name(injection):
    raw = _xml().replace(b"<DOCUMENT>", ("<DOCUMENT>"+injection).encode())
    assert _document_header_identity(raw) == ("00000001", _OWNER)


def test_actor_or_status_substrings_are_not_complete_table_cells():
    raw = _xml(); plain = _xml_to_plain_text(raw)
    context = json.loads(table_source_contexts(raw.decode(), plain, document_actor=_OWNER)[0])
    for field, value in (("actor", "주식회사"), ("status", "적용")):
        changed = dict(context, **{field: value})
        with pytest.raises(ValueError):
            validate_source_context(json.dumps(changed,ensure_ascii=False,sort_keys=True,separators=(",",":")), document_text=plain)


@pytest.mark.parametrize("prefix", ["2026년 1분기 누적 ", "2026년 상반기 누적 "])
def test_closed_reporting_period_prefix_keeps_foreign_heading_scope(prefix):
    text = (_OWNER + "\n\n[기타부문]\n\n(1) 영업개황" + prefix + _OTHER
            + " 매출액은 증가했다.\n\n(2) 사업구분\n\n당사는 산업장비를 제조합니다.")
    scopes = heading_source_scopes(text, document_actor=_OWNER)
    assert len(scopes) == 1
    parsed = validate_source_context(scopes[0].context_json, document_text=text)
    assert parsed["actor"] == _OTHER
    assert different_document_actor(scopes[0].context_json)


def test_company_first_table_column_keeps_verified_product_item():
    raw = (f'<DOCUMENT><COMPANY-NAME AREGCIK="00000001">{_OWNER}</COMPANY-NAME><BODY>'
           '<TABLE><TR><TH>회사명</TH><TH>품목</TH><TH>진행상황</TH></TR>'
           f'<TR><TD>{_OWNER}</TD><TD>산업장비</TD><TD>양산 적용 예정</TD></TR>'
           '</TABLE></BODY></DOCUMENT>').encode()
    plain = _xml_to_plain_text(raw)
    context = validate_source_context(table_source_contexts(raw.decode(), plain, document_actor=_OWNER)[0], document_text=plain)
    assert context["item"] == "산업장비"
    assert context["actor"] == _OWNER


def test_context_budget_excess_is_explicit_failure(monkeypatch):
    from features.evidence_collection import source_context_constants as constants
    from features.evidence_collection.source_context import SourceContextBudgetExceeded
    monkeypatch.setattr(constants, "MAX_CONTEXT_ROWS", 0)
    raw = _xml()
    with pytest.raises(SourceContextBudgetExceeded):
        table_source_contexts(raw.decode(), _xml_to_plain_text(raw), document_actor=_OWNER)


def test_actor_table_existing_row_limit_cannot_silently_remove_context():
    from features.evidence_collection.source_context import SourceContextBudgetExceeded
    raw = (f'<DOCUMENT><COMPANY-NAME AREGCIK="00000001">{_OWNER}</COMPANY-NAME><BODY>'
           '<TABLE><TR><TH>회사명</TH><TH>품목</TH></TR>'
           + ''.join(f'<TR><TD>{_OTHER}</TD><TD>산업장비{i}</TD></TR>' for i in range(129))
           + '</TABLE></BODY></DOCUMENT>').encode()
    with pytest.raises(SourceContextBudgetExceeded):
        table_source_contexts(raw.decode(), _xml_to_plain_text(raw), document_actor=_OWNER)


def test_unclassified_serialization_keeps_optional_expected_context_binding():
    from dataclasses import replace
    from features.evidence_collection.models import DartEvidenceHarvest
    from features.evidence_collection.serialize import harvest_to_mapping
    from features.evidence_collection.tests.test_serialize import _document, _fragment, _COMPANY_ID
    raw = _xml()
    plain = _xml_to_plain_text(raw)
    context = table_source_contexts(raw.decode(), plain, document_actor=_OWNER)[0]
    document = _document("dart_business_report:20250315000001")
    fragment = replace(_fragment("raw-1", document.document_id), section_id="", slot_id="",
                       covered_slot_ids=(), score_millis=0, reason_codes=("no_signal",), source_context_json=context)
    harvest = DartEvidenceHarvest(company_id=_COMPANY_ID, company_type="listed", documents=(),
                                  fragments=(), attempts=(), unclassified_documents=(document,),
                                  unclassified_fragments=(fragment,))
    mapping = harvest_to_mapping(harvest)
    binding = mapping["unclassified_documents"][0]["exact_source_context_bindings"]
    assert binding == [{"location": fragment.location, "text_sha256": fragment.text_sha256,
                        "source_context_sha256": hashlib.sha256(context.encode()).hexdigest()}]
    assert mapping["unclassified_fragments"][0]["source_context_json"] == context
    empty = harvest_to_mapping(replace(harvest, unclassified_fragments=(replace(fragment, source_context_json=""),)))
    assert "exact_source_context_bindings" not in empty["unclassified_documents"][0]
    assert "source_context_json" not in empty["unclassified_fragments"][0]
