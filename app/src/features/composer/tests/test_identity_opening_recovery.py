"""첫 소개 누락의 보충은 원래 승인 본문을 보존하고 별도 검수 한 번만 쓴다."""

import json
from dataclasses import replace

import pytest

from src.features.composer import identity_opening_recovery as recovery_module
from src.features.composer.constants import IDENTITY_FIRST_SENTENCE_WRITER_GUIDE
from src.features.composer.empty_section_recovery import rejected_sentence_fingerprint
from src.features.composer.identity_opening_recovery import has_bound_identity_opening, recover_identity_opening
from src.features.composer.identity_opening_recovery_constants import (
    IDENTITY_OPENING_RECOVERY_ADDED, IDENTITY_OPENING_RECOVERY_ATTEMPTED,
    IDENTITY_OPENING_RECOVERY_NO_BUDGET, IDENTITY_OPENING_RECOVERY_NO_CANDIDATE,
    IDENTITY_OPENING_RECOVERY_NO_EVIDENCE, IDENTITY_OPENING_RECOVERY_PARSE_FAILED,
    IDENTITY_OPENING_RECOVERY_PRESENT, IDENTITY_OPENING_RECOVERY_REJECTED,
)
from src.features.composer.port import (
    AskFatalError, ComposedReport, ComposedSection, ComposedSentence, FlowRow,
)
from src.features.composer.tests.review_evidence_fixture import grounded_review_response, review_items
from src.features.composer.tests.test_empty_section_recovery import _fragment
from src.features.composer.tests.test_identity_opening import _pair
from src.features.composer.quality_projection import select_bound_public_sentences
from src.features.composer.render import render_report


COMPANY = "토스페이먼츠"
MERGER = "이후 회사는 2023년 11월 21일에 2023년 10월 31일을 간주취득일로 하여 지배회사 주식회사 비바리퍼블리카의 종속기업인 블리츠패스트 주식회사와 합병을 완료하였다."
OPERATION = "사업의 실질적 작동 방식을 보면, 회사는 전자지불결제대행업을 통하여 최종소비자가 결제한 거래금액에 대해 원천사로부터 대금을 받은 후, 수수료와 부가가치세를 제외하여 가맹점에 지급하는 구조를 운영한다."
OPENING = "토스페이먼츠는 온라인 가맹점에 결제대행 서비스를 제공하는 전자결제 대행사다."
SLOT = "identity:business_definition"


def _existing():
    identity = ComposedSection("identity", (
        ComposedSentence(MERGER, ("1",), "확인", "identity:corporate_identity", "verified"),
        ComposedSentence(OPERATION, ("1",), "확인", SLOT, "verified"),
    ), notice="기존 확인 범위", flow_rows=(FlowRow(("원래", "도식"), ("1",)),))
    other = ComposedSection("portfolio", (ComposedSentence(
        "회사는 가맹점 결제 서비스를 제공한다.", ("1",), "확인",
        "portfolio:product_role", "verified",
    ),))
    return ComposedReport((identity, other))


def _evidence():
    return (replace(
        _fragment("identity", " ".join((OPENING, MERGER, OPERATION))),
        supported_claim_slots=(SLOT,), source_publisher=COMPANY,
    ),)


def _response(text=OPENING, *, citation="1", slot=SLOT, extras=()):
    return json.dumps({"장들": {"identity": {"문장들": [
        {"글": item, "인용": [citation], "등급": "확인", "주장슬롯": slot,
         "verification_state": "verified", "verified_fact_id": "작가의-위조-ID"}
        for item in (text, *extras)
    ]}}}, ensure_ascii=False)


class _Calls:
    def __init__(self, *, writer_response=None, reviewer_response=None, rejected=False):
        self.writes = []
        self.reviews = []
        self.writer_response = writer_response if writer_response is not None else _response()
        self.reviewer_response = reviewer_response
        self.rejected = rejected

    def writer(self, prompt):
        self.writes.append(prompt)
        return self.writer_response

    def reviewer(self, prompt):
        self.reviews.append(prompt)
        if self.reviewer_response is not None:
            return self.reviewer_response
        return grounded_review_response(prompt, false_texts=(OPENING,) if self.rejected else ())


def _run(calls, report=None, **kwargs):
    options = dict(
        bound_facts=(), evidence=_evidence(), allowed_fragment_ids=frozenset(("1",)),
        writer=calls.writer, reviewer=calls.reviewer, can_start=lambda: True,
        baseline_date="2026-10-23",
    )
    options.update(kwargs)
    return recover_identity_opening(COMPANY, report or _existing(), **options)


def test_토스_합병과_긴운영문장이_남아도_첫소개_한문장만_독립검수해_추가한다(monkeypatch):
    report = _existing()
    calls = _Calls(writer_response=_response(extras=("회사는 결제대행 사업을 운영하는 기업이다.",)))
    drafts = []
    actual_verify = recovery_module.verify_report

    def capture_verify(candidate, *args, **kwargs):
        drafts.append(candidate)
        assert kwargs["allowed_fragment_ids_by_section"] == {"identity": frozenset(("1",))}
        assert kwargs["allow_sentence_rewrite"] is False
        assert kwargs["second_review_call_available"]() is False
        return actual_verify(candidate, *args, **kwargs)

    monkeypatch.setattr(recovery_module, "verify_report", capture_verify)
    result = _run(calls, report)

    assert result.reason == IDENTITY_OPENING_RECOVERY_ADDED
    assert result.attempted and result.added == 1
    assert len(calls.writes) == len(calls.reviews) == 1
    assert calls.writes[0].count(IDENTITY_FIRST_SENTENCE_WRITER_GUIDE) == 1
    assert len(drafts[0].sections[0].sentences) == 1
    assert drafts[0].sections[0].sentences[0].verification_state == "unverified"
    assert not drafts[0].sections[0].sentences[0].verified_fact_id
    identity = result.report.sections[0]
    assert identity.sentences[0].text == OPENING
    assert identity.sentences[0].verification_state == "verified"
    assert not identity.sentences[0].verified_fact_id
    assert identity.sentences[1] is report.sections[0].sentences[0]
    assert identity.sentences[2] is report.sections[0].sentences[1]
    assert identity.notice == report.sections[0].notice
    assert identity.flow_rows is report.sections[0].flow_rows
    assert result.report.sections[1] is report.sections[1]
    assert [item.text for item in review_items(calls.reviews[0])] == [OPENING]
    assert report.sections[0].sentences[0].text == MERGER


def test_이미_현재사업_정의문이_결속돼있으면_추가호출은_없다():
    sentence, fact = _pair()
    report = ComposedReport((ComposedSection("identity", (sentence,)),))
    calls = _Calls()
    result = _run(calls, report, bound_facts=(fact,))
    assert result.report is report
    assert result.reason == IDENTITY_OPENING_RECOVERY_PRESENT
    assert not result.attempted and not result.added
    assert not calls.writes and not calls.reviews


@pytest.mark.parametrize("text", [
    "제이와이피엔터테인먼트는 아티스트를 발굴·육성하고 음악·영상 콘텐츠의 제작·유통, 공연과 IP 라이선싱 사업을 전개하는 엔터테인먼트 기업이다.",
    "가나다전자는 반도체 검사장비를 제조하는 기업이다.",
    "가나다소프트는 기업용 소프트웨어를 개발하는 회사다.",
    "가나다마켓은 판매자와 구매자의 거래를 중개하는 플랫폼이다.",
    "가나다전자는 반도체 검사 장비 전문기업이다.",
    "가나다소프트는 업무용 소프트웨어 전문회사다.",
    "가나다물류는 냉장 운송 전업회사다.",
])
def test_구형_corporate슬롯의_첫현재사업소개는_그대로_인정하여_호출하지않는다(text):
    sentence, fact = _pair(text, slot="identity:corporate_identity")
    operation = _existing().sections[0].sentences[1]
    report = ComposedReport((ComposedSection("identity", (sentence, operation)),))
    calls = _Calls()
    result = _run(calls, report, bound_facts=(fact,))

    assert result.reason == IDENTITY_OPENING_RECOVERY_PRESENT
    assert result.report is report
    assert result.report.sections[0].sentences[0] is sentence
    assert sentence.planned_claim_slot == fact.claim_slot == "identity:corporate_identity"
    assert not result.attempted and not result.added
    assert not calls.writes and not calls.reviews


@pytest.mark.parametrize("text", [
    "회사는 합병을 완료하고 검사장비를 제조하는 기업이다.",
    "회사는 2020년에 설립된 회사다.",
    "회사는 제품 판매를 목적으로 설립된 회사다.",
    "회사는 서울특별시에 본사를 두고 있는 회사다.",
    "회사는 내부회계관리제도를 운영하는 회사다.",
    "회사는 재무제표를 작성하는 회사다.",
    "회사는 전문기업이다.",
    "가나다전자는 전업회사다.",
    "전문기업이다.",
    "회사는 분할된 반도체 검사 장비 전문기업이다.",
])
def test_구형_corporate슬롯의_합병_설립_행정문장은_현재사업소개로_승격하지않는다(text):
    sentence, fact = _pair(text, slot="identity:corporate_identity")
    report = ComposedReport((ComposedSection("identity", (sentence,)),))
    calls = _Calls()
    result = _run(calls, report, bound_facts=(fact,))

    assert result.reason == IDENTITY_OPENING_RECOVERY_ADDED
    assert len(calls.writes) == len(calls.reviews) == 1
    assert result.report.sections[0].sentences[1] is sentence
    assert sentence.planned_claim_slot == fact.claim_slot == "identity:corporate_identity"


@pytest.mark.parametrize("changes", [
    {"time_state": "past"}, {"plan_status": "announced"}, {"fact_status": "planned"},
])
def test_구형_첫기업소개도_현재확정사실이_아니면_보충을_생략하지않는다(changes):
    sentence, fact = _pair("회사는 반도체 검사장비를 제조하는 기업이다.",
                           slot="identity:corporate_identity", **changes)
    report = ComposedReport((ComposedSection("identity", (sentence,)),))
    calls = _Calls()
    result = _run(calls, report, bound_facts=(fact,))
    assert result.reason == IDENTITY_OPENING_RECOVERY_ADDED
    assert len(calls.writes) == len(calls.reviews) == 1


def test_구형_corporate소개를_첫문장으로_옮기거나_순서를_바꾸지않는다():
    sentence, fact = _pair("회사는 반도체 검사장비를 제조하는 기업이다.", slot="identity:corporate_identity")
    original = _existing().sections[0].sentences[0]
    report = ComposedReport((ComposedSection("identity", (original, sentence)),))
    calls = _Calls()
    result = _run(calls, report, bound_facts=(fact,))
    assert result.reason == IDENTITY_OPENING_RECOVERY_ADDED
    assert result.report.sections[0].sentences[1] is original
    assert result.report.sections[0].sentences[2] is sentence


def test_구형_첫소개라도_Fact정확결속이_없으면_임의로_인정하지않는다():
    sentence, fact = _pair("회사는 반도체 검사장비를 제조하는 기업이다.", slot="identity:corporate_identity")
    report = ComposedReport((ComposedSection("identity", (replace(sentence, verification_state="unverified"),)),))
    calls = _Calls()
    result = _run(calls, report, bound_facts=(fact,))
    assert result.reason == IDENTITY_OPENING_RECOVERY_ADDED
    assert len(calls.writes) == len(calls.reviews) == 1


def test_새보충도_최종_Fact결속을_거치며_원래_검증사실_ID를_보존한다():
    original = _existing()
    original = replace(original, sections=(replace(original.sections[0], flow_rows=()), original.sections[1]))
    fragments = _evidence()
    before = render_report(COMPANY, original, fragments, None, as_of_date="2026-10-23")
    result = _run(_Calls(), original)
    after = render_report(COMPANY, result.report, fragments, None, as_of_date="2026-10-23")
    selection = select_bound_public_sentences(result.report, after)

    assert has_bound_identity_opening(selection.report, after.fact_records)
    assert selection.report.sections[0].sentences[0].text == OPENING
    previous_ids = {fact.claim: fact.fact_id for fact in before.fact_records}
    new_ids = {fact.claim: fact.fact_id for fact in after.fact_records}
    assert OPERATION in previous_ids
    assert all(new_ids[claim] == fact_id for claim, fact_id in previous_ids.items())
    assert new_ids[OPENING] not in previous_ids.values()


@pytest.mark.parametrize("calls,reason,review_count", [
    (_Calls(writer_response="깨진 응답"), IDENTITY_OPENING_RECOVERY_PARSE_FAILED, 0),
    (_Calls(writer_response=_response(OPERATION)), IDENTITY_OPENING_RECOVERY_NO_CANDIDATE, 0),
    (_Calls(writer_response=_response(citation="99")), IDENTITY_OPENING_RECOVERY_NO_CANDIDATE, 0),
    (_Calls(writer_response=_response(slot="identity:corporate_identity")), IDENTITY_OPENING_RECOVERY_NO_CANDIDATE, 0),
    (_Calls(reviewer_response="깨진 검수 응답"), IDENTITY_OPENING_RECOVERY_REJECTED, 1),
    (_Calls(rejected=True), IDENTITY_OPENING_RECOVERY_REJECTED, 1),
])
def test_실패는_기존본문을_보존하며_작성이나_검수를_반복하지않는다(calls, reason, review_count):
    report = _existing()
    result = _run(calls, report)
    assert result.report is report
    assert result.reason == reason and result.attempted and not result.added
    assert len(calls.writes) == 1 and len(calls.reviews) == review_count
    repeated = _run(calls, result.report, attempted=result.attempted)
    assert repeated.reason == IDENTITY_OPENING_RECOVERY_ATTEMPTED
    assert len(calls.writes) == 1 and len(calls.reviews) == review_count


@pytest.mark.parametrize("changes", [
    {"allowed_fragment_ids": frozenset()},
    {"evidence": tuple(replace(fragment, formal_source_kind="news") for fragment in _evidence())},
    {"evidence": tuple(replace(fragment, supported_claim_slots=("identity:corporate_identity",)) for fragment in _evidence())},
])
def test_공식_identity슬롯과_FULL허용ID를_벗어난_근거로_보충하지않는다(changes):
    calls = _Calls()
    result = _run(calls, **changes)
    assert result.reason == IDENTITY_OPENING_RECOVERY_NO_EVIDENCE
    assert result.attempted and not result.added
    assert not calls.writes and not calls.reviews


def test_호출예산이_없으면_실패사유를_남기고_새문장을_만들지않는다():
    calls = _Calls()
    diagnostics = []
    result = _run(calls, can_start=lambda: False, protocol_diagnostics=diagnostics)
    assert result.reason == IDENTITY_OPENING_RECOVERY_NO_BUDGET
    assert diagnostics[-1]["상태"] == IDENTITY_OPENING_RECOVERY_NO_BUDGET
    assert not calls.writes and not calls.reviews


def test_예전_검수탈락문장을_동일문구로_재승인하지않는다():
    calls = _Calls()
    result = _run(calls, rejected_fingerprints=frozenset((rejected_sentence_fingerprint(OPENING),)))
    assert result.reason == IDENTITY_OPENING_RECOVERY_NO_CANDIDATE
    assert len(calls.writes) == 1 and not calls.reviews


def test_전역_호출중단은_임의의_승인이나_성공결과로_바꾸지않는다():
    calls = _Calls()
    report = _existing()

    def stopped(_prompt):
        raise AskFatalError(ValueError("전역 요청 취소"))

    with pytest.raises(AskFatalError, match="전역 요청 취소"):
        _run(calls, report, writer=stopped)
    assert report.sections[0].sentences[0].text == MERGER
    assert len(report.sections[0].sentences) == 2
    assert not calls.reviews


def _source_opening_case():
    from src.features.composer.tests.test_identity_opening_source_draft import _fragment
    fragment = replace(_fragment(), source_document_id="20260331000296",
                       document_identity="document:dart.fss.or.kr:20260331000296",
                       document_title="감사보고서 (2025.12)", source_publisher=COMPANY)
    report = ComposedReport((ComposedSection("identity", ()),))
    return report, fragment


def test_명시원문_첫소개는_작성호출없이_검수한번과_새Fact결속을_거친다():
    from src.features.composer.tests.test_identity_opening_source_draft import EXPECTED
    report, fragment = _source_opening_case()
    calls = _Calls()
    result = _run(calls, report, evidence=(fragment,),
                  allowed_fragment_ids=frozenset((fragment.fragment_id,)), writer=None)
    assert result.reason == IDENTITY_OPENING_RECOVERY_ADDED
    assert not calls.writes and len(calls.reviews) == 1
    sentence = result.report.sections[0].sentences[0]
    assert sentence.text == EXPECTED and sentence.verification_state == "verified"
    assert not sentence.verified_fact_id
    assert [item.text for item in review_items(calls.reviews[0])] == [EXPECTED]
    assert fragment.text in calls.reviews[0]
    rendered = render_report(COMPANY, result.report, (fragment,), None, as_of_date="2026-10-24")
    selected = select_bound_public_sentences(result.report, rendered)
    assert has_bound_identity_opening(selected.report, rendered.fact_records)
    assert selected.report.sections[0].sentences[0].text == EXPECTED
    assert rendered.fact_records[0].claim == EXPECTED
    assert rendered.fact_records[0].claim_slot == SLOT


def test_원문후보도_모델이거절하면_공개하거나_작성재시도하지않는다():
    report, fragment = _source_opening_case()
    calls = _Calls(reviewer_response='{"판정":[{"번호":1,"결과":"거짓"}]}')
    result = _run(calls, report, evidence=(fragment,),
                  allowed_fragment_ids=frozenset((fragment.fragment_id,)))
    assert result.reason == IDENTITY_OPENING_RECOVERY_REJECTED
    assert result.report is report and result.added == 0
    assert not calls.writes and len(calls.reviews) == 1


def test_같은원문후보가_이미탈락했다면_우회하여_재검수하지않는다():
    from src.features.composer.tests.test_identity_opening_source_draft import EXPECTED
    report, fragment = _source_opening_case()
    calls = _Calls()
    result = _run(calls, report, evidence=(fragment,), writer=None,
                  allowed_fragment_ids=frozenset((fragment.fragment_id,)),
                  rejected_fingerprints=frozenset((rejected_sentence_fingerprint(EXPECTED),)))
    assert result.reason == IDENTITY_OPENING_RECOVERY_NO_BUDGET
    assert result.report is report
    assert not calls.writes and not calls.reviews
