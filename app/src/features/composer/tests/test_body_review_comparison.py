"""대조 지시와 통제 판정의 적용 경계만 검증하며 모델 의미 품질은 증명하지 않는다."""

import json
import re

import pytest

from src.features.composer.body_review_constants import BODY_REVIEW_COMPARISON_GUIDE
from src.features.composer.grounding_constants import GROUNDING_GUIDE
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
)
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import (
    REVIEW_JSON_GUIDE, REWRITE_PROMPT_HEADER, _GroupedReviewItem, _ReviewItem,
    _build_grouped_review_prompt, _build_review_prompt, verify_report, verify_sentences,
)


# 실제 감사의 의미 추가 유형을 회사·금액에 의존하지 않는 짝으로 재현한다.
CONTRASTS = (
    (
        "ownership", "operations_partners",
        "종속회사의 여행사업부문은 출장관리·호텔예약 플랫폼을 보유한다.",
        "종속회사의 여행사업부문은 자체 개발한 출장관리·호텔예약 플랫폼을 보유한다.",
        "종속회사의 여행사업부문은 출장관리·호텔예약 플랫폼을 보유한다.",
        "1: 보유만, 개발 주체 없음",
    ),
    (
        "sales_role", "operations_partners",
        "음반의 내부 판매조직은 음악사업센터이며 유통 대행사를 통해 판매한다.",
        "음악사업센터가 음반을 기획·제작하고 유통 대행사를 통해 판매한다.",
        "음반은 내부 판매조직인 음악사업센터와 유통 대행사를 통해 판매한다.",
        "1: 판매조직만, 제작 역할 없음",
    ),
    (
        "unrelated_table", "business_model",
        "연결재무제표의 영업수익과 영업비용을 공시한다.",
        "서민금융 상품은 정부 보증기관의 보증서 발급을 조건으로 한다.",
        "연결재무제표는 영업수익과 영업비용을 공시한다.",
        "1: 재무 공시만, 상품 조건 없음",
    ),
    (
        "plan", "future_strategy",
        "회사는 새로운 수익원을 발굴할 계획이다.",
        "회사는 새로운 수익원을 발굴하고 있다.",
        "회사는 새로운 수익원을 발굴할 계획이다.",
        "1: 계획만, 현재 활동 없음",
    ),
    (
        "cause", "past_changes",
        "신용비용 부담에도 불구하고 안정적인 순영업수익을 창출했다.",
        "신용비용 부담이 이익 감소의 주요 원인이다.",
        "신용비용 부담에도 안정적인 순영업수익을 창출했다.",
        "1: 부담만, 감소 주원인 없음",
    ),
)


def _items(prompt):
    # 기존 독자가 묶음 문장의 별도 등급 줄을 지원하지 않는 부분만 건너뛴다.
    return review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))


def _entry(item, verdict, comparison):
    return {
        "번호": item.number, "장": item.section, "근거": list(item.citations),
        "근거대조": comparison, "결과": verdict,
    }


def _run(section, sentences, fragments, ask, grouped):
    return verify_report(
        ComposedReport((ComposedSection(section, tuple(sentences)),)),
        fragments, None, ask,
        allowed_fragment_ids_by_section=(
            {section: frozenset(fragment.fragment_id for fragment in fragments)}
            if grouped else None
        ),
    )


@pytest.mark.parametrize("grouped", (False, True))
def test_prompt_compares_before_verdict_and_keeps_full_own_sources(grouped):
    source = "주체·조건을 설명하는 본문. " * 120 + "마지막 예외: 개발 주체는 별도 법인이다."
    fragment = CollectedFragment("1", "사업내용", source)
    sentence = ComposedSentence("회사는 플랫폼을 보유한다.", ("1",), "확인")
    if grouped:
        prompt = _build_grouped_review_prompt(
            (_GroupedReviewItem(1, "identity", "문장", ("1",), sentence=sentence),),
            {"1": fragment, "2": CollectedFragment("2", "사업내용", "인용하지 않은 자료")}, None,
        )
        schema = prompt.split("형식: 설명 없이 ", 1)[1].split(" JSON만", 1)[0]
    else:
        prompt = _build_review_prompt((_ReviewItem(1, sentence, "identity"),), {"1": fragment}, "")
        schema = REVIEW_JSON_GUIDE
    assert schema.index('"근거대조"') < schema.index('"결과"')
    assert prompt.count(BODY_REVIEW_COMPARISON_GUIDE) == 1
    assert "인용 id 포함 16자 이내" in prompt
    assert GROUNDING_GUIDE in prompt
    assert json.dumps(source, ensure_ascii=False) in prompt
    assert json.dumps(sentence.text, ensure_ascii=False) in prompt
    assert "인용하지 않은 자료" not in prompt
    for relation in ("주체·대상/기능·범위·시점·원인/관계", "자체 개발", "기획·제작",
                     "주된 원인", "무관한 인용", "빈 문자열", "여러 해석", "수치·추세·시점 배열"):
        assert relation in prompt
    assert "확인할 수 없으면 «애매»" not in prompt


@pytest.mark.parametrize("case", CONTRASTS, ids=lambda case: case[0])
@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("grade", ("확인", "해석"))
def test_controlled_false_removes_addition_and_true_keeps_supported_peer(case, grouped, grade):
    _, section, source, bad, good, comparison = case
    calls = []

    def ask(prompt):
        calls.append(prompt)
        if REWRITE_PROMPT_HEADER in prompt:
            return ""
        assert BODY_REVIEW_COMPARISON_GUIDE in prompt
        return json.dumps({"판정": [
            _entry(item, "거짓" if item.text == bad else "참",
                   comparison if item.text == bad else "1: 같은 대상·관계 일치")
            for item in _items(prompt)
        ]}, ensure_ascii=False)

    result = _run(section, (
        ComposedSentence(bad, ("1",), grade), ComposedSentence(good, ("1",), "확인"),
    ), (CollectedFragment("1", "사업내용", source),), ask, grouped)
    assert [sentence.text for sentence in result.sections[0].sentences] == [good]
    assert result.sections[0].sentences[0].verification_state == "verified"
    # 기존 해석 거짓은 재작성 없이 제거하며 확인 거짓에만 재작성 기회를 준다.
    assert len(calls) == (2 if not grouped and grade == "확인" else 1)


@pytest.mark.parametrize("section,text", (
    ("culture", "회사의 인사 절차는 직원의 경력개발계획을 수립하고 직무 교육을 제공하는 것이다."),
    ("past_changes", "회사는 공급 차질이 납기 지연의 주된 원인이라고 밝혔다."),
    ("business_model", "이 기업대출 상품의 이용 조건은 보증기관의 보증서 발급이다."),
    ("operations_partners", "회사는 여행 예약 플랫폼을 자체 개발했다."),
))
@pytest.mark.parametrize("grouped", (False, True))
def test_controlled_true_preserves_explicit_hr_cause_condition_and_development(section, text, grouped):
    calls = []

    def ask(prompt):
        calls.append(prompt)
        entries = [_entry(item, "참", "1: 해당 주체·관계 직접 명시") for item in _items(prompt)]
        if section == "operations_partners":
            # 역할 결속 가드도 같은 «관계» 배열을 읽는다. 정상 개발 주장도 그 원문
            # 구절을 실제로 댄다 — 비교 설명 한 줄로 결속을 대신하지 않는다.
            for entry in entries:
                entry["검증근거"] = {"관계": [{
                    "유형": "역할", "근거": "1", "원문": text,
                    "대상": "여행 예약 플랫폼", "역할값": "개발",
                }]}
        if section == "past_changes":
            # 정상 인과 보존 사례도 실제로 새 검수 요청이 요구한 근거를 낸다.
            # 비교 설명 한 줄로 관계 근거를 대신하지 않는다.
            for entry in entries:
                entry["검증근거"] = {"관계": [{
                    "유형": "인과", "근거": "1", "원문": text,
                    "원인": "공급 차질", "결과": "납기 지연",
                }]}
        return json.dumps({"판정": entries}, ensure_ascii=False)

    result = _run(section, (ComposedSentence(text, ("1",), "확인"),),
                  (CollectedFragment("1", "공식자료", text),), ask, grouped)
    assert [sentence.text for sentence in result.sections[0].sentences] == [text]
    assert result.sections[0].sentences[0].verification_state == "verified"
    assert len(calls) == 1


@pytest.mark.parametrize("grouped", (False, True))
def test_comparison_cannot_replace_required_numeric_grounding(grouped):
    text = "매출액은 5억원이다."

    def ask(prompt):
        return json.dumps({"판정": [_entry(item, "참", "1: 같은 항목·값 직접 명시")
                                  for item in _items(prompt)]}, ensure_ascii=False)

    result = _run("past_changes", (ComposedSentence(text, ("1",), "확인"),),
                  (CollectedFragment("1", "사업내용", "매출액은 500000000원이다."),), ask, grouped)
    assert result.sections[0].sentences == ()


@pytest.mark.parametrize("defect", ("owner", "source", "result", "missing"))
def test_grouped_comparison_cannot_rescue_wrong_owner_sources_or_missing_verdict(defect):
    calls = []

    def ask(prompt):
        calls.append(prompt)
        item = _items(prompt)[0]
        entry = _entry(item, "참", "1: 주체와 역할 일치")
        if defect == "owner":
            entry["장"] = "business_model"
        elif defect == "source":
            entry["근거"] = ["2"]
        elif defect == "result":
            del entry["결과"]
        return json.dumps({"판정": [] if defect == "missing" else [entry]}, ensure_ascii=False)

    result = _run("identity", (ComposedSentence("회사는 제품을 판매한다.", ("1",), "확인"),),
                  (CollectedFragment("1", "사업내용", "회사는 제품을 판매한다."),), ask, True)
    assert result.sections[0].sentences == ()
    assert len(calls) == 1


def test_summary_rewrite_is_reviewed_again_with_the_same_comparison_contract():
    bad, corrected = "회사는 플랫폼을 자체 개발했다.", "회사는 플랫폼을 보유한다."
    calls = []

    def ask(prompt):
        calls.append(prompt)
        if REWRITE_PROMPT_HEADER in prompt:
            return corrected
        assert BODY_REVIEW_COMPARISON_GUIDE in prompt
        return json.dumps({"판정": [
            _entry(item, "거짓" if item.text == bad else "참", "1: 보유만 직접 명시")
            for item in _items(prompt)
        ]}, ensure_ascii=False)

    result = verify_sentences((ComposedSentence(bad, ("1",), "확인"),),
                              (CollectedFragment("1", "사업내용", corrected),), None, ask)
    assert [sentence.text for sentence in result] == [corrected]
    assert result[0].verification_state == "verified"
    assert len(calls) == 3


@pytest.mark.parametrize("grouped", (False, True))
def test_grounded_multiple_interpretations_still_demote(grouped):
    text = "판매 경로의 다변화는 고객 접점을 넓힐 여지가 있다."

    def ask(prompt):
        return json.dumps({"판정": [_entry(item, "애매", "1: 경로 사실, 효과는 여러 해석")
                                  for item in _items(prompt)]}, ensure_ascii=False)

    result = _run("competitive_position", (ComposedSentence(text, ("1",), "확인"),),
                  (CollectedFragment("1", "사업내용", "회사는 온라인과 오프라인 판매 경로를 보유한다."),),
                  ask, grouped)
    assert result.sections[0].sentences[0].text == text
    assert result.sections[0].sentences[0].grade == "해석"
    assert result.sections[0].sentences[0].verification_state == "unverified"
