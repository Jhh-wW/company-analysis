"""닫힌 현재 원문만 미검증 첫 소개로 옮기며 별도 검수는 생략하지 않는다."""

import hashlib
import json
from dataclasses import replace

import pytest

from src.features.composer.empty_section_recovery import recovery_evidence
from src.features.composer.grounding import grounding_problem
from src.features.composer.identity_opening import identity_opening_definition_form
from src.features.composer.identity_opening_source_draft import draft_identity_opening_from_sources
from src.features.composer.payment_method_scope import payment_method_scope_problem
from src.features.composer.port import CollectedFragment
from src.features.composer.scope_guard import scope_problem
from src.features.composer.verify import _apply_grounding


# 저장된 실제 공식 원문49·87과 UTF-8 SHA-256이 같은 독립 시험 fixture다.
SOURCE_49 = "금융부채는 계약상 의무가 이행, 취소 또는 만료되어 소멸되거나 기존 금융부채의 조건이 실질적으로 변경된 경우에 재무상태표에서 제거됩니다. 소멸하거나 제3자에게 양도한 금융부채의 장부금액과 지급한 대가(양도한 비현금자산이나 부담한 부채를 포함)의 차액은 당기손익으로 인식합니다.(3) 금융예수금회사는 전자지불결제대행업을 통하여 최종소비자가 결제한 거래금액에 대해 원천사로부터 대금을 받은 후, 회사의 가맹점으로 수수료와 부가가치세를 제외하여 지급하고 있습니다. 회사는 지급할 의무가 발생하는 시점에 해당 채무를 재무상태표 상 '예수금'으로 표시하고 있습니다."
SOURCE_87 = "(3) 금융예수금회사는 전자지불결제대행업을 통하여 최종소비자가 결제한 거래금액에 대해 원천사로부터 대금을 받은 후, 회사의 가맹점으로 수수료와 부가가치세를 제외하여 지급하고 있습니다. 회사는 지급할 의무가 발생하는 시점에 해당 채무를 재무상태표 상 '예수금'으로 표시하고 있습니다."
SOURCE_HASHES = (
    "861e1bbf7798a2247940ae005b7286d9ecc730298d18d1cc96fd7b993b207001",
    "b47274b85ce9c98521b62757baacef6d9890864f3c368322ea8521d8d7d7ad2a",
)
SLOT = "identity:business_definition"
EXPECTED = (
    "회사는 전자지불결제대행업을 통해 소비자의 결제 대금을 가맹점에 지급하는 기업이다."
)


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fragment(text=SOURCE_87, fragment_id="87", *, heading=""):
    document = heading + "\n" + text if heading else text
    start = len(heading) + 1 if heading else 0
    location = f"{start}-{start + len(text)}"
    context = ""
    if heading:
        context = json.dumps({
            "version": "source-section-context-v1", "document_id": "dart:test",
            "document_sha256": _sha(document), "text": heading,
            "location": f"0-{len(heading)}", "text_sha256": _sha(heading),
            "scope_location": f"0-{len(document)}", "fragment_location": location,
            "fragment_sha256": _sha(text),
        }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return CollectedFragment(
        fragment_id, "사업내용", text,
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260331000296",
        source_document_id="dart:test", document_identity="document:dart:test",
        document_content_sha256=_sha(document), identity_binding="dart_corp_code",
        document_date="2026-03-31", formal_source_kind="dart_audit_report",
        supported_claim_slots=(SLOT,), section_context_json=context, location=location,
    )


@pytest.mark.parametrize("text,digest", tuple(zip((SOURCE_49, SOURCE_87), SOURCE_HASHES)))
def test_실제원문해시를_보존하며_승인없는_첫소개후보만_만든다(text, digest):
    fragment = _fragment(text)
    assert _sha(fragment.text) == digest
    assert recovery_evidence((fragment,))["identity"] == (fragment,)

    draft = draft_identity_opening_from_sources((fragment,))

    assert draft is not None and draft.text == EXPECTED
    assert draft.citations == (fragment.fragment_id,)
    assert draft.grade == "확인" and draft.planned_claim_slot == SLOT
    assert draft.verification_state == "unverified"
    assert not draft.verified_fact_id and draft.structured_claim is None
    assert not draft.news_source_alternative and not draft.news_translation_receipt
    assert fragment.text == text and _sha(fragment.text) == digest


@pytest.mark.parametrize("business", ["전자지불결제대행업", "전자지급결제대행업", "결제대행업"])
def test_원문사업명을_임의동의어나_회사명으로_바꾸지않는다(business):
    fragment = _fragment(SOURCE_87.replace("전자지불결제대행업", business))
    draft = draft_identity_opening_from_sources((fragment,))
    assert draft is not None
    assert draft.text == EXPECTED.replace("전자지불결제대행업", business)


def test_복수_분명한_근거중_첫하나만_인용하고_원문문맥을_보존한다():
    first = _fragment(SOURCE_49, "49", heading="[기타부문-결제]")
    second = _fragment()
    originals = (first, second)
    draft = draft_identity_opening_from_sources(originals)
    assert draft is not None and draft.citations == ("49",)
    assert originals == (first, second)
    assert first.text == SOURCE_49 and first.section_context_json
    assert json.loads(first.section_context_json)["text"] == "[기타부문-결제]"


def test_무관한_사업정의태그는_새소개로_추론하지않고_다음_분명한원문을_본다():
    unrelated = _fragment("상장되어 있는 유사업종 기업군의 1년 평균 변동성을 사용하였습니다.", "30")
    assert draft_identity_opening_from_sources((unrelated,)) is None
    draft = draft_identity_opening_from_sources((unrelated, _fragment()))
    assert draft is not None and draft.citations == ("87",)


@pytest.mark.parametrize("change", [
    {"formal_source_kind": "news_report"}, {"document_identity": ""},
    {"document_content_sha256": ""}, {"identity_binding": ""},
    {"supported_claim_slots": ("identity:corporate_identity",)},
    {"supported_claim_slots": ()},
])
def test_공식복구자격과_사업정의지원이_없는조각은_사용하지않는다(change):
    assert draft_identity_opening_from_sources((replace(_fragment(), **change),)) is None


@pytest.mark.parametrize("context", [
    "과거의 설명입니다.", "종전에는 이 구조였습니다.", "당시의 거래입니다.",
    "이 거래는 2020년에 수행했습니다.",
    "향후 적용할 계획입니다.", "시행할 예정입니다.", "현재 도입을 검토합니다.",
    "가정한 사례입니다.", "가상 설명입니다.", "교육용 예시입니다.",
    "타사의 설명입니다.", "경쟁사의 거래입니다.", "종속기업이 운영합니다.",
    "이 사업을 운영하지 않습니다.", "이 설명은 사실이 아니다.", "사업을 중단했습니다.",
    "일부 거래에만 적용됩니다.", "특정 가맹점에 한정됩니다.",
])
@pytest.mark.parametrize("before", [True, False])
def test_조각전체의_과거_계획_가정_타사_부정_한정을_떼내_승격하지않는다(context, before):
    text = context + " " + SOURCE_87 if before else SOURCE_87 + " " + context
    assert draft_identity_opening_from_sources((_fragment(text),)) is None


@pytest.mark.parametrize("text", [
    SOURCE_87.replace("회사는", "경쟁사는", 1),
    SOURCE_87.replace("전자지불결제대행업을 통하여 ", ""),
    SOURCE_87.replace("전자지불결제대행업", "대출업"),
    SOURCE_87.replace("받은 후,", "받은 경우에만,"),
    SOURCE_87.replace("회사의 가맹점으로", "회사의 일부 가맹점으로"),
    SOURCE_87.replace("지급하고 있습니다.", "지급하고 있었습니다."),
    SOURCE_87.replace("지급하고 있습니다.", "지급할 예정입니다."),
    SOURCE_87.replace("지급하고 있습니다.", "지급하고 있지 않습니다."),
    SOURCE_87.replace("지급하고 있습니다.", "지급하고 있다고 가정합니다."),
])
def test_닫힌정산문형의_추가한정_사업누락_과거형은_후보가_아니다(text):
    assert draft_identity_opening_from_sources((_fragment(text),)) is None


def test_사업부문맥의_가정도_원문에_현재형이있다는_이유로_무시하지않는다():
    fragment = _fragment(heading="[기타부문-가상결제]")
    assert draft_identity_opening_from_sources((fragment,)) is None


def test_현행형식과_scope근거검사는_통과해도_모델의_거짓판정을_승인하지않는다():
    fragment = _fragment()
    draft = draft_identity_opening_from_sources((fragment,))
    assert draft is not None and identity_opening_definition_form(draft.text)
    own = {fragment.fragment_id: fragment.text}
    assert scope_problem(draft.text, own) == ""
    assert grounding_problem(draft.text, own, {"결과": "참"}) == ""
    assert payment_method_scope_problem(draft.text, own, section_id="identity") == ""
    response = json.dumps({"판정": [{"번호": 1, "결과": "거짓", "근거": ["87"]}]}, ensure_ascii=False)
    verdicts = _apply_grounding(response, {1: "거짓"}, {1: (draft.text, own)},
        diagnostic_contexts={1: ("identity", "본문", "확인")})
    assert verdicts[1] == "거짓" and draft.verification_state == "unverified"


def test_빈근거에서는_새요약을_만들지않는다():
    assert draft_identity_opening_from_sources(()) is None
