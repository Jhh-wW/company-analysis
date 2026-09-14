"""개별로 맞는 두 사실(집단 수·관계)을 합쳐 원문에 없는 새 관계를 만드는 회귀를
막는 일반 검사.

설계안: ``tmp/lead/combined-facts-guard-design.md``. 이 검사는 A단계(진단 우선
모드, `COMBINED_RELATION_ENFORCED=False`)라 지금은 발동해도 후보를 막지 않는다 —
`combined_relation_report`(스위치 무관)가 실제 판정을, `combined_relation_problem`
(스위치를 본다)이 운영 배선이 쓰는 값을 돌려준다. 이 파일의 시험은 특별히 밝히지
않는 한 전부 `combined_relation_report`를 부른다.
"""

import hashlib
import json
import re

import pytest

from src.features.composer.combined_relation_constants import (
    COMBINED_RELATION_ENFORCED,
    COMBINED_RELATION_REASON_TEXTS,
    COMBINED_RELATION_REVIEW_GUIDE,
    COMBINED_SCOPE_CLAIM_NOT_COVERED,
    COMBINED_SCOPE_EVIDENCE_MISSING,
    COMBINED_SCOPE_FIELD_TYPE_INVALID,
    COMBINED_SCOPE_NEGATED_IN_SOURCE,
    COMBINED_SCOPE_QUOTE_NOT_IN_SOURCE,
    COMBINED_SCOPE_RANGE_NOT_IN_QUOTE,
    COMBINED_SCOPE_RELATION_NOT_IN_QUOTE,
    COMBINED_SCOPE_SOURCE_NOT_CITED,
    COMBINED_SCOPE_SPLIT_ACROSS_CLAUSES,
    RELATION_COMBINED,
    RELATION_VERBS,
)
from src.features.composer.combined_relation_guard import (
    combined_relation_hint,
    combined_relation_problem,
    combined_relation_report,
    combined_relation_triggers,
)
from src.features.composer.direct_support_constants import RELATION_TYPES
from src.features.composer.grounding import constrain_verdicts, grounding_hint
from src.features.composer.grounding_constants import REVIEW_GROUNDING_REJECTED
from src.features.composer.port import CollectedFragment, ComposedSentence
from src.features.composer.role_binding import role_binding_problem
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_sentences
from src.shared.report_quality.review_diagnostic_constants import REVIEW_SCOPE_ITEMS


CLAIM = "당사는 577개 종속회사로부터 배당수익을 수취한다."
SOURCE = "당사는 577개 종속회사로부터 배당수익을 수취합니다."


def _combined_item(**overrides) -> dict:
    item = {"근거": "1", "범위": "577개", "관계": "수취", "원문": SOURCE, "유형": RELATION_COMBINED}
    item.update(overrides)
    return item


# ══════════════════════════════════════════════════════════
# §7.1 살려야 할 정상 결합 — 10건
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("text", [
    "연결대상 종속회사는 총 577개사이다.",
    "회사는 종속회사와 기타 투자사로부터 배당수익을 수취한다.",
    "577개 종속회사를 통해 여러 사업을 영위한다.",
    "당사의 사업부문은 SI, ITO, Cloud 등 6개 서비스를 제공한다.",
    "사업부문의 고객은 국내 기업들이 주요 수요처이며, 당사는 ERP, CRM, MIS 등 다양한 "
    "기업 정보시스템을 구축하여 고객사의 비즈니스 요구사항을 충족시키고 있다.",
    "당사는 투자부문과 사업부문으로 구분되며, 투자부문은 종속회사와 기타 투자사로부터 "
    "배당수익 및 브랜드 사용수익을 수취하고, 사업부문은 Digital 기술을 기반으로 "
    "국내외 IT서비스 재화와 용역을 공급한다.",
    "반도체 분야에서는 기존 D램 중심의 사업군을 다양화하여 고부가가치 라인업을 지속 "
    "확장하고, 배터리 분야에서는 동박 등 차세대 유망소재 개발에 투자하여 이를 "
    "내재화할 계획이다.",
    "5개 사업이 전년 대비 성장했다.",
])
def test_normal_combinations_never_trigger(text):
    assert combined_relation_triggers(text) == ()
    assert combined_relation_report(text, {}, None).problem == ""


@pytest.mark.parametrize("claim,source", [
    ("회사는 577개 종속회사로부터 배당수익을 수취한다.",
     "당사는 577개 종속회사로부터 배당수익을 수취합니다."),
    ("당사는 32개 협력사로부터 부품을 납품받는다.",
     "당사는 32개 협력사로부터 부품을 납품받고 있습니다."),
])
def test_triggering_claims_pass_when_one_source_clause_states_the_relation(claim, source):
    scope = "577개" if "577" in claim else "32개"
    relation = "수취" if "수취" in claim else "납품"
    entries = {"관계": [_combined_item(범위=scope, 관계=relation, 원문=source)]}
    report = combined_relation_report(claim, {"1": source}, entries)
    assert report.triggers
    assert report.problem == ""


# ══════════════════════════════════════════════════════════
# §7.2 막아야 할 새 관계 — 12건(수량 축). 근거를 내지 않으면 반드시 발동+탈락한다.
# ══════════════════════════════════════════════════════════

@pytest.mark.parametrize("text", [
    "577개 종속회사로부터 배당수익을 수취한다",  # 기본형(기존 검사도 잡음)
    "종속회사 577개사로부터 배당금을 수취했다",  # 어순 반대
    "577개 종속회사가 당사에 배당금을 지급했다",  # 지급형
    "연결대상 회사 577개로부터 배당수익을 수취한다",  # 공시의 실제 집단 이름
    "32개 협력사로부터 부품을 납품받는다",  # 배당 아닌 술어 — 기존 검사 미탐
    "12개 국가에 제품을 공급한다",  # 상대편이 지역
    "5개 자회사에 출자하고 있다",  # 출자
    "전국 250개 지점에서 대출 상품을 판매한다",  # 판매·지점
    "고객사 40곳과 장기 공급 계약을 체결했다",  # 계약 체결
    "종속회사 모두로부터 브랜드 사용료를 수취한다",  # 전량 표현
    "두 개 자회사로부터 배당을 받는다",  # 한국어 수관형사
    "577개 종속회사로부터 배당수익을 수취했고, 향후 배당 계획을 수립했다",  # 뒤 절의
    # 계획으로 앞 절을 가리지 못함
])
def test_new_relations_without_combined_evidence_are_rejected(text):
    report = combined_relation_report(text, {"1": "아무 관련 없는 원문"}, None)
    assert report.triggers, "이 문장은 발동해야 한다"
    assert report.problem == COMBINED_SCOPE_EVIDENCE_MISSING


# ══════════════════════════════════════════════════════════
# §7.3 판정 절차 — 단계별 사유 코드
# ══════════════════════════════════════════════════════════

def test_missing_source_id_is_rejected():
    entries = {"관계": [_combined_item(근거="")]}
    assert combined_relation_report(CLAIM, {"1": SOURCE}, entries).problem == (
        COMBINED_SCOPE_SOURCE_NOT_CITED)


def test_uncited_source_id_is_rejected():
    entries = {"관계": [_combined_item(근거="9")]}
    assert combined_relation_report(CLAIM, {"1": SOURCE}, entries).problem == (
        COMBINED_SCOPE_SOURCE_NOT_CITED)


def test_quote_not_verbatim_in_source_is_rejected():
    altered = "당사는 577개 종속회사로부터 배당수익을 수령합니다."
    entries = {"관계": [_combined_item(원문=altered)]}
    assert combined_relation_report(CLAIM, {"1": SOURCE}, entries).problem == (
        COMBINED_SCOPE_QUOTE_NOT_IN_SOURCE)


def test_quote_missing_the_scope_is_rejected():
    quote = "당사는 배당수익을 수취합니다."
    source = "당사는 배당수익을 수취합니다. 577개 종속회사가 있습니다."
    entries = {"관계": [_combined_item(원문=quote)]}
    assert combined_relation_report(CLAIM, {"1": source}, entries).problem == (
        COMBINED_SCOPE_RANGE_NOT_IN_QUOTE)


def test_quote_missing_the_relation_verb_is_rejected():
    quote = "당사는 577개 종속회사가 있습니다."
    source = "당사는 577개 종속회사가 있습니다. 배당수익을 수취합니다."
    entries = {"관계": [_combined_item(원문=quote)]}
    assert combined_relation_report(CLAIM, {"1": source}, entries).problem == (
        COMBINED_SCOPE_RELATION_NOT_IN_QUOTE)


def test_quote_that_splices_two_sentences_is_rejected():
    """「…577개사입니다. 배당수익을 수취합니다.」처럼 절을 넘어 이어 붙인 구절."""

    spliced = "당사는 577개 종속회사가 있습니다. 배당수익을 수취합니다."
    entries = {"관계": [_combined_item(원문=spliced)]}
    assert combined_relation_report(CLAIM, {"1": spliced}, entries).problem == (
        COMBINED_SCOPE_SPLIT_ACROSS_CLAUSES)


def test_source_sentence_that_denies_the_relation_is_rejected():
    source = "당사는 577개 종속회사로부터 배당수익을 수취하지 않습니다."
    entries = {"관계": [_combined_item(원문="당사는 577개 종속회사로부터 배당수익을 수취")]}
    assert combined_relation_report(CLAIM, {"1": source}, entries).problem == (
        COMBINED_SCOPE_NEGATED_IN_SOURCE)


def test_source_sentence_that_hedges_the_relation_is_rejected():
    source = "당사는 577개 종속회사로부터 배당수익을 수취한다고 보기는 어렵다."
    entries = {"관계": [_combined_item(원문="당사는 577개 종속회사로부터 배당수익을 수취")]}
    assert combined_relation_report(CLAIM, {"1": source}, entries).problem == (
        COMBINED_SCOPE_NEGATED_IN_SOURCE)


@pytest.mark.parametrize("bad_value", [577, None, ["577개"], True])
def test_non_string_field_is_rejected(bad_value):
    entries = {"관계": [_combined_item(범위=bad_value)]}
    assert combined_relation_report(CLAIM, {"1": SOURCE}, entries).problem == (
        COMBINED_SCOPE_FIELD_TYPE_INVALID)


def test_one_position_left_uncovered_is_rejected():
    text = "당사는 577개 종속회사로부터 배당수익을 수취한다. 당사는 12개 국가에 제품을 공급한다."
    entries = {"관계": [_combined_item()]}
    assert combined_relation_report(text, {"1": SOURCE}, entries).problem == (
        COMBINED_SCOPE_CLAIM_NOT_COVERED)


def test_no_combined_typed_entry_at_all_is_evidence_missing():
    assert combined_relation_report(CLAIM, {"1": SOURCE}, None).problem == (
        COMBINED_SCOPE_EVIDENCE_MISSING)
    assert combined_relation_report(CLAIM, {"1": SOURCE}, {"관계": []}).problem == (
        COMBINED_SCOPE_EVIDENCE_MISSING)
    other_typed = {"관계": [{"근거": "1", "원인": "x", "결과": "y", "원문": SOURCE, "유형": "인과"}]}
    assert combined_relation_report(CLAIM, {"1": SOURCE}, other_typed).problem == (
        COMBINED_SCOPE_EVIDENCE_MISSING)


# ══════════════════════════════════════════════════════════
# §7.4 예외
# ══════════════════════════════════════════════════════════

def test_summary_and_body_get_the_same_verdict_for_the_same_text():
    """요약은 발췌라 같은 문자열이면 같은 판정이 난다 — 장 구분을 이 검사가 모른다."""

    body = combined_relation_report(CLAIM, {"1": "아무 원문"}, None)
    summary = combined_relation_report(CLAIM, {"1": "아무 원문"}, None)
    assert body.problem == summary.problem == COMBINED_SCOPE_EVIDENCE_MISSING


def test_ninth_chapter_needs_one_item_per_differentiator_position():
    """9장(회사가 밝힌 차별점)도 예외 없이 적용된다 — 자리 둘이면 항목 둘이 필요하다."""

    text = "A사는 12개 국가에 공장을 보유하고, B사는 5개 지역에 매장을 보유한다."
    source = "A사는 12개 국가에 공장을 보유하고 있다. B사는 5개 지역에 매장을 보유하고 있다."
    two_items = {"관계": [
        _combined_item(범위="12개", 관계="보유", 원문="A사는 12개 국가에 공장을 보유하고 있다."),
        _combined_item(범위="5개", 관계="보유", 원문="B사는 5개 지역에 매장을 보유하고 있다."),
    ]}
    report = combined_relation_report(text, {"1": source}, two_items)
    assert len(report.triggers) == 2
    assert report.problem == ""

    one_item = {"관계": [two_items["관계"][0]]}
    assert combined_relation_report(text, {"1": source}, one_item).problem == (
        COMBINED_SCOPE_CLAIM_NOT_COVERED)


def test_diagram_cells_do_not_trigger_in_stage_one():
    """도식 칸은 1단계에서 제외한다 — 칸을 이어 붙인 문자열로 걸면 서로 다른 칸의
    수량과 술어가 하나로 묶인다."""

    report = combined_relation_report(
        CLAIM, {"1": SOURCE}, None, cells=("577개 종속회사로부터", "배당수익을 수취한다"))
    assert report.triggers == ()
    assert report.problem == ""


@pytest.mark.parametrize("text", [
    "종속회사는 협력사로부터 부품을 납품받는다고 회사는 밝혔다.",
    "회사는 해외법인이 운영하는 사업을 직접 운영한다고 설명했다.",
])
def test_actor_transfer_without_a_quantity_does_not_trigger(text):
    """주체 전이(설계안 §2.2 c)는 이 검사의 몫이 아니다 — 수량 표현이 없으면
    발동하지 않는다. 나중에 넓히려면 이 시험을 일부러 고쳐야 한다."""

    assert combined_relation_triggers(text) == ()


@pytest.mark.parametrize("text", [
    "회사는 현재 해당 사업을 운영하고 있다.",
    "회사는 종속회사로부터 배당수익을 과거부터 지금까지 계속 수취하고 있다.",
])
def test_time_binding_without_a_quantity_does_not_trigger(text):
    """시점 결합(설계안 §2.2 d)도 이 검사의 몫이 아니다 — 수량 표현이 없으면
    발동하지 않는다."""

    assert combined_relation_triggers(text) == ()


def test_interpreted_grade_is_excluded_only_through_confirmed_prose_numbers(monkeypatch):
    """「해석」 등급 제외는 코드 수준에서는 confirmed_prose_numbers 로만 표현된다 —
    combined_relation_report 자체는 등급을 모른다. 스위치를 임시로 켜서
    _apply_grounding이 넘기는 이 목록이 constrain_verdicts의 실제 차단 여부를
    가른다는 것을 증명한다(운영 배선을 그대로 부른다)."""

    monkeypatch.setattr(
        "src.features.composer.combined_relation_guard.COMBINED_RELATION_ENFORCED", True)
    # ★ 기존 배당 전용 검사(quantified_relation_guard)는 confirmed_prose_numbers와
    #   무관하게 첫째 고리에서 이미 돈다. 그 검사와 겹치지 않는 「납품」 술어를 써서
    #   이 시험이 «새 검사 자신의» 게이트만 증명하게 한다.
    text = "당사는 32개 협력사로부터 부품을 납품받는다."
    raw = json.dumps({"판정": [
        {"번호": 1, "장": "identity", "근거": ["1"], "결과": "참"},
    ]}, ensure_ascii=False)
    candidates = {1: (text, {"1": "아무 관련 없는 원문"})}
    verdicts = {1: "참"}

    excluded, _ = constrain_verdicts(
        raw, verdicts, candidates, confirmed_prose_numbers=frozenset())
    assert excluded[1] == "참"

    included, problems = constrain_verdicts(
        raw, verdicts, candidates, confirmed_prose_numbers=frozenset({1}))
    assert included[1] == REVIEW_GROUNDING_REJECTED
    assert problems[1] == COMBINED_SCOPE_EVIDENCE_MISSING


# ══════════════════════════════════════════════════════════
# §7.5 배선·불변 시험
# ══════════════════════════════════════════════════════════

def test_hint_and_report_share_the_same_trigger_computation():
    triggers = combined_relation_triggers(CLAIM)
    assert triggers
    report = combined_relation_report(CLAIM, {"1": "아무 원문"}, None)
    assert report.triggers == triggers
    hint = grounding_hint(CLAIM, {"1": "아무 원문"})
    assert combined_relation_hint(triggers) != ""
    assert combined_relation_hint(triggers) in hint


def test_review_guide_lists_every_closed_relation_verb():
    for verb in RELATION_VERBS:
        assert verb in COMBINED_RELATION_REVIEW_GUIDE, verb


def _all_true_ask(calls):
    def ask(prompt):
        calls.append(prompt)
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        assert items, "검수 입력에서 후보를 읽지 못했습니다"
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section, "근거": list(item.citations),
             "결과": "참"}
            for item in items
        ]}, ensure_ascii=False)
    return ask


@pytest.mark.parametrize("text,source", [
    (CLAIM, SOURCE),
    ("회사는 최신 기술을 활용한다.", "회사는 최신 기술을 활용한다고 밝혔다."),
])
def test_no_extra_ai_call_is_added_whether_or_not_a_candidate_triggers(text, source):
    sentences = (ComposedSentence(text, ("1",), "확인"),)
    fragments = (CollectedFragment("1", "사업내용", source),)
    calls = []
    verify_sentences(sentences, fragments, None, _all_true_ask(calls))
    assert len(calls) == 1


def test_raw_response_bytes_are_not_mutated_by_the_new_check():
    raw = json.dumps({"판정": [
        {"번호": 1, "장": "identity", "근거": ["1"], "결과": "참",
         "검증근거": {"관계": [_combined_item()]}},
    ]}, ensure_ascii=False)
    fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    constrain_verdicts(raw, {1: "참"}, {1: (CLAIM, {"1": SOURCE})},
                        confirmed_prose_numbers=frozenset({1}))
    assert hashlib.sha256(raw.encode("utf-8")).hexdigest() == fingerprint


def test_hint_line_is_absent_when_nothing_triggers():
    hint = grounding_hint("회사는 최신 기술을 활용한다.", {"1": "아무 원문"})
    assert "수량 범위 결속 요구" not in hint


def test_combined_type_is_registered_and_role_binding_ignores_it():
    """§3.4의 구멍을 못 박는다 — 결합 항목이 역할 가드의 «계약 밖 유형» 탈락을
    일으키지 않아야 한다."""

    assert RELATION_COMBINED in RELATION_TYPES
    entries = {"관계": [_combined_item()]}
    assert role_binding_problem(CLAIM, {"1": SOURCE}, entries) == ""


def test_all_reason_codes_are_registered_in_the_diagnostic_scope_table():
    assert set(COMBINED_RELATION_REASON_TEXTS) <= set(REVIEW_SCOPE_ITEMS)
    assert all(
        REVIEW_SCOPE_ITEMS[code] == "수량 범위 결속"
        for code in COMBINED_RELATION_REASON_TEXTS
    )


def test_차단_스위치의_현재값():
    """A단계는 진단 우선 모드다 — 계산은 하되 사유를 돌려주지 않는다."""

    assert COMBINED_RELATION_ENFORCED is False
    assert combined_relation_problem(CLAIM, {"1": "아무 관련 없는 원문"}, None) == ""
    assert combined_relation_report(CLAIM, {"1": "아무 관련 없는 원문"}, None).problem == (
        COMBINED_SCOPE_EVIDENCE_MISSING)
