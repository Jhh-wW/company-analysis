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
import logging
import re

import pytest

from src.features.composer.combined_relation_constants import (
    COMBINED_RELATION_ENFORCED,
    COMBINED_RELATION_GUIDE_JSON_LINE_HEAD,
    COMBINED_RELATION_REASON_TEXTS,
    COMBINED_RELATION_REVIEW_GUIDE_ENFORCED,
    COMBINED_RELATION_REVIEW_GUIDE_OBSERVED,
    COMBINED_SCOPE_CLAIM_NOT_COVERED,
    COMBINED_SCOPE_EVIDENCE_MISSING,
    COMBINED_SCOPE_FIELD_TYPE_INVALID,
    COMBINED_SCOPE_NEGATED_IN_SOURCE,
    COMBINED_SCOPE_QUOTE_NOT_IN_SOURCE,
    COMBINED_SCOPE_RANGE_NOT_IN_QUOTE,
    COMBINED_SCOPE_RELATION_NOT_IN_QUOTE,
    COMBINED_SCOPE_SOURCE_NOT_CITED,
    COMBINED_SCOPE_SPLIT_ACROSS_CLAUSES,
    RELATION_ACTION_NOUNS,
    RELATION_COMBINED,
    RELATION_VERBS,
    RELATION_VERB_STEMS,
)
from src.features.composer.combined_relation_guard import (
    combined_relation_hint,
    combined_relation_problem,
    combined_relation_report,
    combined_relation_review_guide,
    combined_relation_triggers,
)
from src.features.composer.direct_support_constants import RELATION_TYPES
from src.features.composer.grounding import constrain_verdicts, grounding_hint
from src.features.composer.grounding_constants import (
    REVIEW_GROUNDING_REJECTED, TABLE_SOURCE_ID,
)
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
)
from src.features.composer.news_usage import attribution_prefix
from src.features.composer.role_binding import (
    role_binding_problem, role_binding_report, role_binding_requirements,
)
from src.features.composer.role_binding_constants import ROLE_BINDING_ENTRY_TYPE_UNKNOWN
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verbatim_news import verbatim_news_source
from src.features.composer.verify import verify_report, verify_sentences
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import SOURCE_KIND_NEWS
from src.shared.report_quality.review_diagnostic_constants import REVIEW_SCOPE_ITEMS


CLAIM = "당사는 577개 종속회사로부터 배당수익을 수취한다."
SOURCE = "당사는 577개 종속회사로부터 배당수익을 수취합니다."
#: 기존 배당 전용 검사(`quantified_relation_guard`)와 «겹치지 않는» 발동 문장 —
#: 배선 시험이 새 검사 자신의 게이트만 재게 한다.
WIRED_CLAIM = "당사는 32개 협력사로부터 부품을 납품받는다."
WIRED_SOURCE = "당사는 32개 협력사로부터 부품을 납품받는다."
VERIFY_LOGGER = "src.features.composer.verify"


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
# 발동 꼴의 «경계» — 낱말 속 글자를 수량·조사·서술어로 읽지 않는다 (독립 검토 P1-1)
# ══════════════════════════════════════════════════════════
#
# ★ 아래 문장은 전부 실제 공시·기사 코퍼스(17개사)에서 «오탐»으로 발동했던 꼴이거나
#   그 꼴을 그대로 옮긴 것이다. 하나라도 다시 발동하면 경계가 무너진 것이다.

@pytest.mark.parametrize("text", [
    # ① 수량·단위가 낱말 «속» 글자로 읽히던 자리 — 「계열사」의 「열」(수관형사 10) + 「사」
    "계열사에 서비스를 제공한다.",
    "계열사와 장기 계약을 체결했다.",
    "계열사가 비용을 부담하고 당사가 운영한다.",
    "계열사는 별도 법인이 운영한다.",
    "계열사로부터 수수료를 수취한다.",
    "계열사에게 브랜드 사용료를 지급한다.",
])
def test_a_quantity_inside_a_word_is_not_a_quantity(text):
    assert combined_relation_triggers(text) == ()


@pytest.mark.parametrize("text", [
    # ② 조사가 낱말 «속» 글자로 읽히던 자리 — 「이상·이하」의 「이」
    "100명 이상의 인력을 보유하고 있다.",
    "50개 이상의 기관과 협력 관계를 맺고 있다.",
    "5개 이하 회사에 공급한다.",
    "연결대상 종속회사는 총 577개사이다.",
])
def test_a_particle_inside_a_word_is_not_a_particle(text):
    assert combined_relation_triggers(text) == ()


@pytest.mark.parametrize("text", [
    # ③ 관계 낱말이 명사로 쓰인 자리 — 용언 어간이 붙지 않으면 서술어가 아니다
    "변동이자를 수취하는 모든 이자율스왑계약은 이자율변동으로 평가된다.",
    "무장애나눔길 112곳에 대한 위치 및 경로 등 관련 정보 제공",
    "구매 파트너 총 131곳과 체결",
])
def test_a_relation_word_used_as_a_noun_is_not_a_predicate(text):
    assert combined_relation_triggers(text) == ()


@pytest.mark.parametrize("text", [
    # ④ 수량이 «상대편»이 아니라 견주는 대상이거나 그 수량에 «대한» 서술인 자리
    "300개가 넘는 비즈니스 API를 제공한다.",
    "112곳에 대한 위치 정보를 제공한다.",
    "5개 기관에 관한 자료를 공급한다.",
    "3개 회사에 비해 낮은 수수료를 수취한다.",
])
def test_a_quantity_that_is_not_the_counterparty_does_not_trigger(text):
    assert combined_relation_triggers(text) == ()


@pytest.mark.parametrize("text", [
    # ⑤ 전량 표현은 «닫힌 집단 이름»을 한정할 때만 본다 — 상투어를 걸러낸다
    "모든 이용자에게 맞춤형 서비스를 제공한다.",
    "모두에게 차별화된 가치를 제공한다.",
    "전부를 대상으로 운영한다.",
])
def test_a_totality_word_without_a_closed_group_noun_does_not_trigger(text):
    assert combined_relation_triggers(text) == ()


@pytest.mark.parametrize("text,scope", [
    ("모든 종속회사가 회계감사인으로부터 적정의견을 받았습니다.", "모든 종속회사"),
    ("종속회사 모두로부터 브랜드 사용료를 수취한다.", "종속회사 모두"),
    ("전 계열사에 통합 보안 서비스를 제공한다.", "전 계열사"),
])
def test_a_totality_word_with_a_closed_group_noun_still_triggers(text, scope):
    """전량 표현을 좁혔다고 «집단을 지목한» 꼴까지 놓치지는 않는다 — 집단 이름은
    앞에 와도(「종속회사 모두」) 뒤에 와도(「모든 종속회사」) 같다."""

    triggers = combined_relation_triggers(text)
    assert [trigger.scope for trigger in triggers] == [scope]


def test_relation_verb_lists_are_split_without_losing_or_adding_a_word():
    """안내문이 쓰는 전체 목록과 정규식이 쓰는 두 갈래가 «같은 낱말 집합»이어야 한다."""

    assert set(RELATION_VERBS) == set(RELATION_ACTION_NOUNS) | set(RELATION_VERB_STEMS)
    assert not set(RELATION_ACTION_NOUNS) & set(RELATION_VERB_STEMS)
    assert len(RELATION_VERBS) == len(RELATION_ACTION_NOUNS) + len(RELATION_VERB_STEMS)


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
    """9장(회사가 밝힌 차별점)도 예외 없이 적용된다 — 자리 둘이면 항목 둘이 필요하다.

    ★ 설계안 §7.4 가 든 «원래» 표본이다. 목적격 조사 「을·를」이 상대편 조사 목록에
      없던 동안 이 꼴이 통째로 미탐이었다(독립 검토 P2-3). 이 시험이 그 구멍을 못 박는다.
    """

    text = "A사는 3개 공장을 보유하고, B사는 5개 공장을 보유한다."
    source = "A사는 3개 공장을 보유하고 있다. B사는 5개 공장을 보유하고 있다."
    two_items = {"관계": [
        _combined_item(범위="3개", 관계="보유", 원문="A사는 3개 공장을 보유하고 있다."),
        _combined_item(범위="5개", 관계="보유", 원문="B사는 5개 공장을 보유하고 있다."),
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
    "회사는 현재 해당 사업을 운영하고 있다.",
    "회사는 종속회사로부터 배당수익을 과거부터 지금까지 계속 수취하고 있다.",
])
def test_actor_transfer_and_time_binding_without_a_quantity_do_not_trigger(text):
    """주체 전이·시점 결합 문장이라도 «수량 한정이 없으면» 이 검사는 보지 않는다.

    앞 둘은 주체 전이(설계안 §2.2 c), 뒤 둘은 시점 결합(§2.2 d) 꼴이다. 나중에
    그 축까지 넓히려면 이 시험을 일부러 고쳐야 한다.
    """

    assert combined_relation_triggers(text) == ()


@pytest.mark.parametrize("text,scope,relation", [
    # 주체 전이 꼴 — 「회사는 밝혔다」가 뒤에 붙어도 앞 절의 수량 한정은 그대로다.
    ("종속회사 5곳은 협력사로부터 부품을 납품받는다고 회사는 밝혔다.", "5곳", "납품"),
    # 시점 결합 꼴 — 「2020년부터 … 하고 있다」도 마찬가지다.
    ("회사는 2020년부터 5개 자회사로부터 배당을 수취하고 있다.", "5개 자회사", "수취"),
])
def test_actor_transfer_and_time_binding_with_a_quantity_do_trigger(text, scope, relation):
    """★ 축은 «수량 한정이 관계 서술어에 걸린 것» 하나다 — 문장이 주체 전이나 시점
    결합처럼 읽히더라도 그 꼴이면 이 검사의 발동이다(총괄 결정, 2026-09-14).

    설계안 §2.2 c·d 는 이 두 축을 「이 검사 몫 아님」으로 적었고 §7.4 는 「발동하지
    않음을 단정하라」고 했으나, 실측하면 수량 한정이 있는 순간 발동한다. 고친 것은
    코드가 아니라 «설계의 범위 서술»이다 — 발동해도 A단계에서는 막지 않으며, 원문
    한 절이 그 관계를 말하면 그대로 통과한다(§7.1 9·10 시험).
    """

    triggers = combined_relation_triggers(text)
    assert [(t.scope, t.relation) for t in triggers] == [(scope, relation)]


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
        assert verb in COMBINED_RELATION_REVIEW_GUIDE_OBSERVED, verb
        assert verb in COMBINED_RELATION_REVIEW_GUIDE_ENFORCED, verb


# ── 진단 모드는 «관측만» 한다 — 프롬프트도 그래야 한다 ──────────────────────

def test_observed_guide_asks_for_the_entry_without_ordering_a_verdict():
    """A단계 프롬프트에는 「거짓으로 판정한다」가 없어야 한다.

    ★ 코드가 막지 않는데 안내가 검수 AI에게 판정을 지시하면, 코드 대신 모델이 후보를
      지운다 — 관측 기간의 발동 수를 오탐률로 읽을 수 없고 「막지 않는다」도 거짓이
      된다(독립 검토 §3-A). 요청(항목을 내 달라)은 그대로 남는다.
    """

    assert COMBINED_RELATION_ENFORCED is False
    guide = combined_relation_review_guide()
    assert guide == COMBINED_RELATION_REVIEW_GUIDE_OBSERVED
    assert "거짓으로 판정" not in guide
    assert "결과를 거짓" not in guide
    # 요청 자체는 살아 있어야 한다 — 항목을 안 받으면 관측할 것이 없다.
    assert RELATION_COMBINED in guide
    assert "수량 범위 결속" in guide and "배열에 넣고" in guide


def test_enforced_guide_adds_the_verdict_instruction(monkeypatch):
    """스위치를 켜면 «그때» 판정 지시가 붙는다 — 안내문을 부를 때 고르기 때문이다."""

    monkeypatch.setattr(
        "src.features.composer.combined_relation_guard.COMBINED_RELATION_ENFORCED", True)
    guide = combined_relation_review_guide()
    assert guide == COMBINED_RELATION_REVIEW_GUIDE_ENFORCED
    assert "결과를 거짓으로 판정한다" in guide
    # 두 판은 «요청» 부분이 글자 그대로 같아야 한다 — 갈라지면 한쪽만 고쳐진다.
    head = COMBINED_RELATION_GUIDE_JSON_LINE_HEAD
    assert head in COMBINED_RELATION_REVIEW_GUIDE_OBSERVED and head in guide
    shared = "".join(COMBINED_RELATION_REVIEW_GUIDE_OBSERVED.splitlines(True)[:-1])
    assert guide.startswith(shared)


@pytest.mark.parametrize("guide", [
    COMBINED_RELATION_REVIEW_GUIDE_OBSERVED,
    COMBINED_RELATION_REVIEW_GUIDE_ENFORCED,
])
def test_review_guide_prose_has_no_double_quote(guide):
    """산문 줄에 큰따옴표가 없어야 한다 — 모델이 답의 문자열 값 안에 그대로 옮기면
    응답 JSON 이 깨진다(2026-09-14 운영 실측, 커밋 d201592f 가 세운 관례).
    큰따옴표는 JSON 예시 «한 줄»에만 둔다."""

    json_lines = [
        line for line in guide.splitlines()
        if line.startswith(COMBINED_RELATION_GUIDE_JSON_LINE_HEAD)
    ]
    assert len(json_lines) == 1, "JSON 예시 줄은 하나여야 한다"
    for line in guide.splitlines():
        if line.startswith(COMBINED_RELATION_GUIDE_JSON_LINE_HEAD):
            continue
        assert '"' not in line, line


def test_review_guide_example_carries_no_real_company_number():
    """안내문 예시가 실제 결함 회사의 수치를 모든 회사 검수에 내보내지 않는다
    (독립 검토 P3-4)."""

    for guide in (COMBINED_RELATION_REVIEW_GUIDE_OBSERVED,
                  COMBINED_RELATION_REVIEW_GUIDE_ENFORCED):
        assert not re.search(r"\d", guide.split(COMBINED_RELATION_GUIDE_JSON_LINE_HEAD)[0])


# ── 실적표 원문은 결합 근거의 원문 후보가 아니다 (설계안 §4) ────────────────

def test_table_source_cannot_be_used_as_the_combined_evidence():
    """실적표 결속 원문은 후보가 «인용해서» 들어온 값이 아니라 보고서에 표가 있으면
    모든 후보에 함께 실리는 값이라, 결합 근거로 빌려 쓸 수 없어야 한다."""

    entries = {"관계": [_combined_item(근거=TABLE_SOURCE_ID)]}
    sources = {"1": "아무 관련 없는 원문", TABLE_SOURCE_ID: SOURCE}
    assert combined_relation_report(CLAIM, sources, entries).problem == (
        COMBINED_SCOPE_SOURCE_NOT_CITED)
    # 음성 대조 — 같은 원문을 «후보가 인용한» 조각으로 대면 그대로 통과한다.
    own = {"관계": [_combined_item(근거="1")]}
    assert combined_relation_report(CLAIM, {"1": SOURCE}, own).problem == ""


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


def _waived_role_binding_case():
    """역할·과금 요구를 «전부 제외»한 후보 하나를 운영 함수로 만든다.

    ★ 이 자리에서만 `_unknown_type_entry_count`가 판정에 쓰인다(`role_binding.py`의
      「요구를 모두 제외한 자리에서 유형이 비었거나 계약 밖인 항목은 무시가 아니라
      탈락이다」). 요구가 남은 보통 후보로는 §3.4의 구멍을 재현할 수 없다.
    조각·문장은 전부 시험용 합성값이며 회사명·실제 보도가 아니다.
    """

    section, fragment_id = "business_model", "9"
    news = "회사는 이달 말까지 재가입 고객에게 상품권을 지급한다."
    fragment = CollectedFragment(
        fragment_id=fragment_id,
        kind="typed-evidence-v1:" + "0" * 8,
        text=news,
        source_url="https://media.example/article/" + fragment_id,
        document_title="시험용 기사",
        document_date="2026-09-10",
        document_identity="document:media.example:" + fragment_id,
        document_content_sha256=hashlib.sha256(news.encode("utf-8")).hexdigest(),
        counts_toward_document_floor=False,
        formal_source_kind=SOURCE_KIND_NEWS,
        source_document_id="https://media.example/article/" + fragment_id,
        source_publisher="media.example",
        source_collected_on="2026-09-13",
        news_grounded=True,
        news_event_key="시험_사건",
        supported_claim_slots=CLAIM_SLOTS_BY_SECTION[section],
    )
    context = verbatim_news_source(
        attribution_prefix(fragment) + news, (fragment_id,), {fragment_id: fragment},
        section_id=section,
    )
    assert context is not None
    sources = {fragment_id: news}
    requirements = role_binding_requirements(news, sources, None, context)
    assert requirements.items and not requirements.required, "요구가 전부 제외돼야 한다"
    return news, sources, context


def test_combined_type_is_registered_and_role_binding_ignores_it(monkeypatch):
    """§3.4의 구멍을 못 박는다 — 결합 항목이 역할 가드의 «계약 밖 유형» 탈락을
    일으키지 않아야 한다.

    ★ 음성 대조가 핵심이다. 등록을 빼면 «같은 후보·같은 항목»이
      `role_binding_entry_type_unknown` 으로 탈락한다 — 그 차이가 이 등록이 실제로
      무언가를 지킨다는 증거다. 상수를 다시 읽는 단정만으로는 아무것도 못 지킨다.
    """

    assert RELATION_COMBINED in RELATION_TYPES
    text, sources, context = _waived_role_binding_case()
    entries = {"관계": [_combined_item()]}
    assert role_binding_report(text, sources, entries, None, context).problem == ""

    monkeypatch.setattr(
        "src.features.composer.role_binding.RELATION_TYPES",
        tuple(kind for kind in RELATION_TYPES if kind != RELATION_COMBINED))
    assert role_binding_report(text, sources, entries, None, context).problem == (
        ROLE_BINDING_ENTRY_TYPE_UNKNOWN)


def test_combined_entry_does_not_disturb_an_ordinary_role_binding_candidate():
    """요구가 남은 보통 후보에서는 결합 항목이 아예 읽히지 않는다."""

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


# ══════════════════════════════════════════════════════════
# 배선 — 운영 함수의 «실제 호출 인자»와 «실제 산출물»을 단정한다 (독립 검토 P1-2)
# ══════════════════════════════════════════════════════════
#
# ★ 아래 셋은 지우면 전체 묶음이 그대로 초록이던 자리다: ①게이트로 넘기는 인자,
#   ②A단계의 «유일한 산출물»인 관측 로그, ③평문·묶음 두 프롬프트의 공통 안내문.

def _wired_raw(number: int = 1, section: str = "business_model") -> str:
    return json.dumps({"판정": [
        {"번호": number, "장": section, "근거": ["1"], "결과": "참"},
    ]}, ensure_ascii=False)


def test_apply_grounding_passes_confirmed_prose_numbers_to_the_gate(monkeypatch):
    """① `_apply_grounding` 이 `constrain_verdicts` 로 넘기는 인자를 그대로 단정한다.

    이 한 줄이 빠지면 게이트는 기본값(빈 집합)을 받아 «스위치를 켜도» 아무것도 막지
    않는다. 그런데도 모든 시험이 초록이었다.
    """

    from src.features.composer import verify as verify_module

    real = verify_module.constrain_verdicts
    seen: dict[str, object] = {}

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(verify_module, "constrain_verdicts", spy)
    verify_module._apply_grounding(
        _wired_raw(), {1: "참"}, {1: (WIRED_CLAIM, {"1": WIRED_SOURCE})},
        confirmed_prose_numbers=frozenset({1}),
    )
    assert seen.get("confirmed_prose_numbers") == frozenset({1})


def test_observation_log_is_the_only_output_of_stage_a(caplog):
    """② 관측 로그가 실제로 나가고, 후보 번호·장·사유·발동 자리 수·표면형·지문을 담는다."""

    from src.features.composer.verify import _apply_grounding

    with caplog.at_level(logging.INFO, logger=VERIFY_LOGGER):
        _apply_grounding(
            _wired_raw(7), {7: "참"},
            {7: (WIRED_CLAIM, {"1": "아무 관련 없는 원문"})},
            diagnostic_contexts={7: ("business_model", "본문", WIRED_CLAIM)},
            confirmed_prose_numbers=frozenset({7}),
        )
    lines = [
        record.getMessage() for record in caplog.records
        if record.name == VERIFY_LOGGER and "수량 범위 결속 관측" in record.getMessage()
    ]
    assert len(lines) == 1, caplog.text
    line = lines[0]
    assert "후보 7" in line
    assert "장 business_model" in line
    assert COMBINED_SCOPE_EVIDENCE_MISSING in line
    assert "발동 자리 1개" in line
    assert "범위 32개 협력사" in line and "관계 납품" in line
    assert hashlib.sha256(WIRED_CLAIM.encode("utf-8")).hexdigest() in line
    # 후보 본문·원문·응답 본문은 로그에 담지 않는다.
    assert WIRED_CLAIM not in line


def test_observation_log_is_silent_when_nothing_triggers(caplog):
    """음성 대조 — 발동하지 않는 후보는 관측 줄을 남기지 않는다."""

    from src.features.composer.verify import _apply_grounding

    text = "회사는 최신 기술을 활용한다."
    with caplog.at_level(logging.INFO, logger=VERIFY_LOGGER):
        _apply_grounding(
            _wired_raw(), {1: "참"}, {1: (text, {"1": text})},
            diagnostic_contexts={1: ("business_model", "본문", text)},
            confirmed_prose_numbers=frozenset({1}),
        )
    assert "수량 범위 결속 관측" not in caplog.text


def _flat_inputs():
    return (
        (ComposedSentence(WIRED_CLAIM, ("1",), "확인"),),
        (CollectedFragment("1", "사업내용", WIRED_SOURCE),),
    )


def test_review_guide_reaches_both_prompt_builders():
    """③ 공통 안내문이 평문 경로와 묶음 경로 «양쪽» 프롬프트에 실제로 실린다."""

    guide = combined_relation_review_guide()
    sentences, fragments = _flat_inputs()

    flat_calls: list[str] = []
    verify_sentences(sentences, fragments, None, _all_true_ask(flat_calls))
    assert len(flat_calls) == 1
    assert guide in flat_calls[0]

    section = "business_model"
    grouped_calls: list[str] = []
    verify_report(
        ComposedReport((ComposedSection(section, sentences),)), fragments, None,
        _all_true_ask(grouped_calls),
        allowed_fragment_ids_by_section={section: frozenset(("1",))},
    )
    assert len(grouped_calls) == 1
    assert guide in grouped_calls[0]
    # 후보 밑 안내 줄도 두 경로에 함께 간다.
    for prompt in (flat_calls[0], grouped_calls[0]):
        assert "수량 범위 결속 요구: 「32개 협력사…납품」" in prompt


@pytest.mark.parametrize("grouped", (False, True))
def test_switch_off_keeps_the_sentence_and_logs_only(grouped, caplog):
    """④ 진단 모드에서는 발동 문장이 «그대로 남고» 관측 줄만 나간다."""

    sentences, fragments = _flat_inputs()
    section = "business_model"
    with caplog.at_level(logging.INFO, logger=VERIFY_LOGGER):
        if grouped:
            checked = verify_report(
                ComposedReport((ComposedSection(section, sentences),)), fragments, None,
                _all_true_ask([]),
                allowed_fragment_ids_by_section={section: frozenset(("1",))},
            ).sections[0].sentences
        else:
            checked = verify_sentences(sentences, fragments, None, _all_true_ask([]))
    assert [sentence.text for sentence in checked] == [WIRED_CLAIM]
    assert "수량 범위 결속 관측" in caplog.text


@pytest.mark.parametrize("grouped", (False, True))
def test_switch_on_removes_the_triggering_sentence(grouped, monkeypatch):
    """④ 스위치를 켜면 같은 문장이 같은 경로에서 실제로 빠진다 — 배선이 살아 있다는 증명."""

    monkeypatch.setattr(
        "src.features.composer.combined_relation_guard.COMBINED_RELATION_ENFORCED", True)
    sentences, fragments = _flat_inputs()
    section = "business_model"
    if grouped:
        checked = verify_report(
            ComposedReport((ComposedSection(section, sentences),)), fragments, None,
            _all_true_ask([]),
            allowed_fragment_ids_by_section={section: frozenset(("1",))},
        ).sections[0].sentences
    else:
        checked = verify_sentences(sentences, fragments, None, _all_true_ask([]))
    assert [sentence.text for sentence in checked] == []
