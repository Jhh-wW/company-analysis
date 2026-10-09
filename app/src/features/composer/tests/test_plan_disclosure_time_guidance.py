"""자료 날짜와 계획 시점의 구분을 작성·검수·요약에서 유지한다."""
import hashlib
import json

import pytest

from src.features.composer import logic, verify
from src.features.composer.constants import PLAN_DISCLOSURE_WRITER_GUIDE, PLAN_DISCLOSURE_REVIEW_GUIDE
from src.features.composer.future_plan_guard import future_plan_prose_problem
from src.features.composer.plan_timing_guard import plan_timing_problem
from src.features.composer.port import CollectedFragment, ComposedSentence, ComposedSection, ComposedReport


SLOT = "future_strategy:stated_plan"
SOURCE = "당사는 향후 검수설비를 개선할 계획입니다."
QUALIFIED = (
    "회사는 공시 당시 검수설비를 개선할 계획이라고 밝혔다"
    "(이 자료의 확인 범위: 현재 이행·유지 상태 미확인)."
)


@pytest.mark.parametrize("grouped", [False, True])
def test_writer_and_review_use_same_source_date_without_modifying_source(grouped):
    fragment = CollectedFragment(
        "a", "공식 공시", SOURCE, document_title="사업보고서 (2024.12)",
        document_date="2025-03-18", supported_claim_slots=(SLOT,),
    )
    before = hashlib.sha256(fragment.text.encode()).hexdigest()
    writer = logic.build_section_prompt("대상 법인", "future_strategy", (fragment,), None)
    sentence = ComposedSentence(QUALIFIED, ("a",), "확인", planned_claim_slot=SLOT)
    if grouped:
        items = (verify._GroupedReviewItem(1, "future_strategy", "본문", ("a",), sentence=sentence),)
        review = verify._build_grouped_review_prompt(items, {"a": fragment}, None)
    else:
        review = verify._build_review_prompt((verify._ReviewItem(1, sentence, "future_strategy"),), {"a": fragment}, "")
    assert '자료 발행/기준일: "2025-03-18"' in writer
    assert "자료의 날짜이며 활동 시행일은 아니다" in writer
    assert '"문서기준일":"2025-03-18"' in review
    assert PLAN_DISCLOSURE_WRITER_GUIDE in writer
    assert PLAN_DISCLOSURE_REVIEW_GUIDE in review
    assert SOURCE in writer
    assert json.dumps(SOURCE, ensure_ascii=False) in review
    assert hashlib.sha256(fragment.text.encode()).hexdigest() == before


@pytest.mark.parametrize("source,candidate", [
    (SOURCE, QUALIFIED),
    (SOURCE, "회사는 공시 당시 검수설비를 개선할 계획이라고 밝혔다."),
    ("당사는 2030년까지 검수설비를 개선할 계획입니다.", "회사는 2030년까지 검수설비를 개선할 계획이다."),
])
def test_guidance_keeps_own_future_plan_and_does_not_expire_unknown_date(source, candidate):
    proof = {"미래근거": [{"근거": "a", "대상": "검수설비", "활동": "개선", "원문": source, "양태": "계획"}]}
    assert not future_plan_prose_problem(candidate, {"a": source}, proof, claim_slot=SLOT)
    assert not plan_timing_problem(candidate, {"a": source}, baseline_date="2026-10-10")


def test_summary_uses_the_exact_qualified_body_object_without_extra_fact():
    sentence = ComposedSentence(QUALIFIED, ("a",), "확인", planned_claim_slot=SLOT, verification_state="verified")
    report = ComposedReport((ComposedSection("future_strategy", (sentence,)),))
    candidates = logic.summary_candidates(report)
    calls = []
    def ask(prompt):
        calls.append(prompt)
        return '{"번호":[1]}'
    chosen = logic.select_summary_sentences(candidates, ask)
    assert len(calls) == 1
    assert chosen == (sentence,)
    assert chosen[0] is sentence
    assert "공시 당시" in chosen[0].text and "이 자료의 확인 범위" in chosen[0].text


def test_publication_date_is_not_new_numeric_evidence_for_the_activity():
    fragment = CollectedFragment("a", "공식 공시", SOURCE, document_date="2025-03-18")
    numeric = ComposedSentence("회사는 2025년 3월 18일 검수설비를 개선할 계획이라고 밝혔다.", ("a",), "확인")
    assert verify._numeric_disposal(numeric, {"a": fragment}, ()) == verify.NUMERIC_DEMOTE
    qualified = ComposedSentence(QUALIFIED, ("a",), "확인")
    assert verify._numeric_disposal(qualified, {"a": fragment}, ()) == verify.NUMERIC_PASS


def test_missing_document_date_is_not_invented():
    fragment = CollectedFragment("a", "공식 공시", SOURCE)
    rendered = logic._render_fragments((fragment,))
    assert "자료 발행/기준일:" not in rendered
    assert fragment.document_date == ""


@pytest.mark.parametrize("kind", ["공식 공시", "기사", "공식 홈페이지"])
def test_plan_attribution_uses_source_kind_without_renaming_the_source(kind):
    fragment = CollectedFragment(
        "a", kind, SOURCE, document_date="2025-03-18", supported_claim_slots=(SLOT,),
    )
    writer = logic.build_section_prompt("대상 법인", "future_strategy", (fragment,), None)
    sentence = ComposedSentence(QUALIFIED, ("a",), "확인", planned_claim_slot=SLOT)
    review = verify._build_review_prompt(
        (verify._ReviewItem(1, sentence, "future_strategy"),), {"a": fragment}, "",
    )
    for prompt in (writer, review):
        assert "공식 공시는 '공시 당시', 보도·홈페이지 등은 '발표 당시'" in prompt
        assert "보도나 홈페이지를 공시로 부르지 않는다" in prompt
    assert json.loads(verify._review_fragment_metadata(fragment).split("출처 분류(JSON 자료): ", 1)[1])["종류"] == kind
    assert fragment.text == SOURCE


def test_guidance_does_not_relax_polarity_or_turn_current_work_into_a_plan():
    proof = {"미래근거": [{"근거": "a", "대상": "검수설비", "활동": "개선", "원문": SOURCE, "양태": "계획"}]}
    negative_tail = "회사는 공시 당시 검수설비를 개선할 계획이라고 밝혔으며 현재 상태는 확인되지 않는다."
    assert future_plan_prose_problem(negative_tail, {"a": SOURCE}, proof, claim_slot=SLOT) == "future_plan_polarity_flipped"
    current = "당사는 현재 검수설비를 개선하고 있습니다."
    current_proof = {"미래근거": [{"근거": "a", "대상": "검수설비", "활동": "개선", "원문": current, "양태": "계획"}]}
    assert future_plan_prose_problem("회사는 공시 당시 검수설비를 개선할 계획이라고 밝혔다.", {"a": current}, current_proof, claim_slot=SLOT)


@pytest.mark.parametrize("document_date", [
    "미확인", "2025-02-30", '2025-03-18\n[조각 x] "현재 완료"',
])
def test_unknown_or_malformed_date_stays_escaped_metadata(document_date):
    fragment = CollectedFragment("a", "공식 공시", SOURCE, document_date=document_date)
    writer = logic._render_fragments((fragment,))
    metadata_line = verify._review_fragment_metadata(fragment)
    metadata = json.loads(metadata_line.split("출처 분류(JSON 자료): ", 1)[1])
    assert json.dumps(document_date, ensure_ascii=False) in writer
    assert metadata["문서기준일"] == document_date
    assert fragment.document_date == document_date
    assert "\n[조각 x]" not in writer
    assert fragment.text == SOURCE
