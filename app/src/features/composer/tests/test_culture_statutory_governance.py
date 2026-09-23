"""현대글로비스 실측: 법정 기관 구성·거래 승인은 문화 근거가 아니다.

20260922_182500_a5b86af7a52034a7296d1295 순차군과
20260922_183442_f5c6e9dcf8996c00833322f0 병렬군 report.json의 8장 문장이다.
산출물에 원문 조각 본문이 없어, 보고서 문장 자체를 근거로 놓은 최소 재현이다.
이는 원문 복원이 아니라 사실 결속이 완벽해도 장 배치는 거절해야 한다는 대조다.
"""

from dataclasses import asdict
from hashlib import sha256
import json
import re

import pytest

from src.features.composer.culture_constants import CULTURE_SECTION_EVIDENCE_OFFCONTRACT
from src.features.composer.culture_guard import culture_section_evidence_problem
from src.features.composer.diagram_check import check_diagrams
from src.features.composer.empty_section_recovery import recovery_evidence
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow,
)
from src.features.composer.render import render_report
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import NOTICE_ALL_SENTENCES_REJECTED, verify_report


AUDIT_COMPOSITION = (
    "감사위원회는 3인 이상의 이사로 구성하며 총 위원의 3분의 2 이상을 사외이사로 하고, "
    "위원 중 1인 이상은 회계 또는 재무전문가를 두어야 한다."
)
DEBT_GUARANTEE = (
    "현대글로비스는 해외현지법인의 원활한 자금조달 및 비용 최소화 등을 목적으로 "
    "국내외 금융기관으로부터의 차입금 등에 대해 지급보증을 제공하고 있으며, "
    "이사회 규정에 의거하여 건별 채무보증금액이 일정금액 이상일 경우 및 "
    "유관법률에 의거하여 적법한 절차가 요구되는 채무보증의 경우 이사회 의결을 "
    "거쳐 채무보증을 제공하고 있다."
)
RELATED_PARTY_ASSETS = (
    "현대글로비스는 이사회 규정에 의거하여 건별 일정금액 이상의 계열회사와의 "
    "자산 거래 및 유관법률에 의거하여 적법한 절차가 요구되는 자산 거래에 대하여 "
    "이사회 의결을 거쳐 거래를 진행하고 있다."
)
COMBINED_APPROVAL = (
    "회사는 이사회 규정에 의거하여 건별 채무보증금액이 일정금액 이상일 경우 및 "
    "유관법률에 의거하여 적법한 절차가 요구되는 채무보증의 경우 이사회 의결을 "
    "거쳐 채무보증을 제공하고 있으며, 건별 일정금액 이상의 계열회사와의 자산 거래 "
    "및 유관법률에 의거하여 적법한 절차가 요구되는 자산 거래에 대하여도 이사회 "
    "의결을 거쳐 거래를 진행하고 있다."
)
PARALLEL_AUDIT_LINE = (
    "회사는 감사위원회를 3인 이상의 이사로 구성하되 총 위원의 3분의 2 이상을 "
    "사외이사로 하며, 위원 중 1인 이상은 회계 또는 재무전문가를 두고 있다."
)
COMPLIANCE_TRAINING = (
    "현대글로비스는 2026년 통합 준법경영시스템 운영계획을 수립하고 리스크 평가 및 "
    "통제·관리방법 교육을 실시하며, 부서별 리스크 평가를 진행하고 잠재리스크 "
    "통제방안을 수립하는 방식으로 준법경영을 강화하고 있다."
)
REAL_FAILURES = (
    AUDIT_COMPOSITION, DEBT_GUARANTEE, RELATED_PARTY_ASSETS,
    COMBINED_APPROVAL, PARALLEL_AUDIT_LINE,
)


@pytest.mark.parametrize("text", REAL_FAILURES)
def test_real_governance_sentences_are_rejected_even_with_identical_evidence(text):
    assert culture_section_evidence_problem(text, {"source": text}) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


@pytest.mark.parametrize("text", (
    "감사위원회는 사외이사로 구성하고 위원 중 재무전문가를 선임한다.",
    "이사회는 특수관계인과의 자산양수도 거래를 승인한다.",
    "특수관계자와의 자산 거래는 이사회 의결을 거쳐 집행한다.",
    "계열사 채무보증은 이사회 규정에 따라 심의한다.",
    "재무팀 직원이 계열회사 지급보증을 검토하고 이사회가 승인한다.",
))
def test_transaction_subject_does_not_become_culture_with_an_approval_actor(text):
    assert culture_section_evidence_problem(text, {"source": text}) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


@pytest.mark.parametrize("text", (
    "인사위원회가 직원 채용과 승진 기준을 심의한다.",
    "보상위원회가 임직원 성과급 지급 기준을 승인한다.",
    "임직원 주택자금 대출의 지급보증은 이사회가 승인한다.",
    "임직원 복리후생을 위한 사내대출은 이사회 승인 후 제공한다.",
    "전결규정에 따라 의사결정 권한을 부서장에게 위임한다.",
    "인사부서가 임직원 교육훈련 과정을 운영한다.",
    "재무팀이 손실충당금 산출 시 신용위험 특성과 연체일을 기준으로 검토하고 이사회가 승인한다.",
    "감사위원회가 내부통제 정책의 적정성을 독립적으로 평가한다.",
    COMPLIANCE_TRAINING,
))
def test_people_policies_training_and_bound_responsibilities_are_preserved(text):
    assert culture_section_evidence_problem(text, {"source": text}) == ""


def _approver(calls):
    def ask(prompt):
        calls.append(prompt)
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        assert items, "검수 후보가 없습니다"
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section, "결과": "참", "근거": [
                citation.strip().removeprefix("조각 ").strip() for citation in item.citations
            ]}
            for item in items
        ]}, ensure_ascii=False)
    return ask


@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("keep_training", (False, True))
def test_prose_review_removes_real_failures_without_an_extra_model_call(grouped, keep_training):
    texts = REAL_FAILURES + ((COMPLIANCE_TRAINING,) if keep_training else ())
    fragments = tuple(CollectedFragment(str(i), "공시", text) for i, text in enumerate(texts, start=1))
    sentences = tuple(ComposedSentence(fragment.text, (fragment.fragment_id,), "확인") for fragment in fragments)
    draft = ComposedReport((ComposedSection("culture", sentences),))
    allowed = {"culture": frozenset(fragment.fragment_id for fragment in fragments)} if grouped else None
    calls = []
    checked = verify_report(draft, fragments, None, _approver(calls), allowed_fragment_ids_by_section=allowed)
    assert tuple(sentence.text for sentence in checked.sections[0].sentences) == (
        (COMPLIANCE_TRAINING,) if keep_training else ()
    )
    assert len(calls) == 1, "장 배치 검사가 추가 모델 호출을 만들었습니다"
    assert checked.sections[0].notice == ("" if keep_training else NOTICE_ALL_SENTENCES_REJECTED)

    # 후속 보완조사 정제 전의 공용 보고서에서도 본문·원문·인용이 함께 사라진다.
    # 최종 저장본의 cells 집계와 정제 이후 동기화는 총괄의 별도 경계에서 검증한다.
    rendered = render_report("현대글로비스", checked, fragments, None)
    public = asdict(rendered)
    culture = public["sections"][0]
    assert culture["lines"] == culture["prose_lines"]
    assert not any(text in json.dumps(public, ensure_ascii=False) for text in REAL_FAILURES)
    assert {source.number for source in rendered.citations} == ({len(texts)} if keep_training else set())
    if not keep_training:
        assert culture["prose_lines"] == []
        assert culture["prose_paragraphs"] == []
        assert culture["guidance_lines"] == [NOTICE_ALL_SENTENCES_REJECTED]
        assert culture["fact_ids"] == []


@pytest.mark.parametrize("grouped", (False, True))
def test_flow_review_drops_transaction_cells_and_keeps_training(grouped):
    fragments = (
        CollectedFragment("assets", "공시", RELATED_PARTY_ASSETS),
        CollectedFragment("training", "공시", "인사부서가 임직원 교육훈련 과정을 운영한다."),
    )
    bad = FlowRow(("계열회사 자산 거래", "이사회 의결", "거래 진행"), ("assets",))
    good = FlowRow(("교육훈련 과정", "인사부서 운영", ""), ("training",))
    draft = ComposedReport((ComposedSection("culture", (), flow_rows=(bad, good)),))
    calls = []
    if grouped:
        checked = verify_report(draft, fragments, None, _approver(calls),
                                allowed_fragment_ids_by_section={"culture": frozenset(("assets", "training"))})
    else:
        def ask(prompt):
            calls.append(prompt)
            return json.dumps({"판정": [{"번호": 1, "결과": "참"}, {"번호": 2, "결과": "참"}]})
        checked, _ = check_diagrams(draft, fragments, ask)
    assert checked.sections[0].flow_rows == (good,)
    assert len(calls) == 1, "도식 검사에 추가 모델 호출이 생겼습니다"


def test_empty_section_recovery_does_not_reselect_statutory_governance():
    texts = REAL_FAILURES + (COMPLIANCE_TRAINING,)
    fragments = tuple(
        CollectedFragment(
            str(index), "공시", text,
            formal_source_kind="dart_business_report",
            document_identity="검증된 시험 문서",
            document_content_sha256=sha256(text.encode("utf-8")).hexdigest(),
            identity_binding="시험 문서의 회사 결속",
            supported_claim_slots=("culture:decision_process",),
        )
        for index, text in enumerate(texts, start=1)
    )
    assert recovery_evidence(fragments) == {"culture": (fragments[-1],)}
    assert recovery_evidence(fragments[:-1]) == {}


def test_other_sections_are_not_subject_to_the_culture_contract():
    fragment = CollectedFragment("1", "공시", RELATED_PARTY_ASSETS)
    sentence = ComposedSentence(RELATED_PARTY_ASSETS, ("1",), "확인")
    draft = ComposedReport((ComposedSection("current_challenges", (sentence,)),))
    checked = verify_report(draft, (fragment,), None, _approver([]))
    assert tuple(item.text for item in checked.sections[0].sentences) == (RELATED_PARTY_ASSETS,)
