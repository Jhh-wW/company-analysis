"""거짓 사업 지원칸을 제외해도 원문과 유효한 다른 칸은 남긴다."""
import pytest

from src.features.chapter_evidence.normalize import normalize_documents, normalize_fragments
from src.features.chapter_evidence.select import select_section_fragments
from src.features.chapter_evidence.tests.fixtures import make_document, make_fragment, sha256_of


@pytest.mark.parametrize("section,slot,other,text", [
    ("business_model", "business_model:customer_type", "business_model:revenue_model",
     "회사는 투자등급 거래처에 한해 거래하고 신용등급을 평가하며 매출채권을 관리한다."),
    ("operations_partners", "operations_partners:operating_role", "operations_partners:value_chain",
     "회사는 제조물책임법에 따라 제조업자의 배상책임에 대비하여 보험계약을 체결한다."),
    ("operations_partners", "operations_partners:operating_role", "operations_partners:supply_relation",
     "회사는 공정거래위원회의 금형 제조위탁 조사결과에 이의를 제기했다."),
])
def test_거짓지원칸을_제외하고_원문지문과_다른칸을_그대로_유지한다(section, slot, other, text):
    raw = make_fragment(company_id="corp-example", fragment_id="f-1", document_id="doc-1",
                        section_id=section, slot_id=slot, text=text, score_millis=750)
    raw["covered_slot_ids"] = (slot, other)
    document = make_document(company_id="corp-example", document_id="doc-1", source_kind="dart_audit_report",
                             exact_evidence_hashes=(sha256_of(text),))
    result = select_section_fragments(section_id=section, company_id="corp-example",
                                     documents=normalize_documents([document]), fragments=normalize_fragments([raw]),
                                     max_chars=1000)
    assert len(result.fragments) == 1
    fragment = result.fragments[0]
    assert fragment.covered_slot_ids == (other,)
    assert fragment.slot_id == other
    assert fragment.text == text and fragment.text_sha256 == raw["text_sha256"]
    assert fragment.location == raw["location"]
    assert f"business_slot_scope_unsupported:1:{slot}" in result.reason_codes


def test_정상고객과_관리정책이_함께_있으면_고객칸을_유지한다():
    text = "회사는 거래처의 신용등급을 평가한다. 주요 고객사는 의료기관이다."
    raw = make_fragment(company_id="corp-example", fragment_id="f-1", document_id="doc-1",
                        section_id="business_model", slot_id="business_model:customer_type", text=text, score_millis=750)
    document = make_document(company_id="corp-example", document_id="doc-1", source_kind="dart_audit_report",
                             exact_evidence_hashes=(sha256_of(text),))
    result = select_section_fragments(section_id="business_model", company_id="corp-example",
                                     documents=normalize_documents([document]), fragments=normalize_fragments([raw]),
                                     max_chars=1000)
    assert result.fragments[0].covered_slot_ids == ("business_model:customer_type",)
    assert result.fragments[0].text == text
