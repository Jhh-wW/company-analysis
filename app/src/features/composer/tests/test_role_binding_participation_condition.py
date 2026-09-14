# -*- coding: utf-8 -*-
"""반복 낱말이 «혜택 수령인의 참여 조건»인 자리 — 요구 제외와 역방향 차단의 단위 회귀.

실측 사고: 검증된 보도 원문 「…만기 재가입 고객에 현금 …을 지급하고…」를 그대로 옮긴
후보에 산문 반복 표지 「재가입」이 결속 항목을 요구했고, 합성 검수 응답의 유형이
어긋나자 문장과 뉴스 목록이 함께 사라졌다. 여기 fixture 는 그 정확한 문장(회사명·
날짜·금액 포함)이며 «시험 fixture 에만» 둔다. 검수 응답은 전부 시험용 합성값이다.

각 시험은 «살려야 하는 것»과 «막아야 하는 것»을 짝으로 둔다 — 한쪽만 있으면 가드가
전부 막거나 전부 열어도 녹색이 되기 때문이다.
"""

import hashlib
import re

import pytest

from src.features.composer.grounding import grounding_hint
from src.features.composer.port import CollectedFragment
from src.features.composer.role_binding import (
    _kind_answers_role,
    claims_role_or_fee,
    role_binding_problem,
    role_binding_report,
    role_binding_requirements,
)
from src.features.composer.role_binding_constants import (
    FEE_WORDS,
    PROSE_FEE_MARKER_RE,
    PROSE_FEE_WORDS,
    PROSE_REPEAT_MARKER_RE,
    PROSE_REPEAT_WORDS,
    RELATION_FEE,
    RELATION_ROLE,
    REPEAT_WORDS,
    ROLE_BINDING_CONDITION_DROPPED,
    ROLE_BINDING_CONDITION_NOT_ACTION,
    ROLE_BINDING_ENTRY_TYPE_UNKNOWN,
    ROLE_BINDING_FIELD_TYPE_INVALID,
    ROLE_BINDING_HINT_REQUIRED_HEAD,
    ROLE_BINDING_HINT_WAIVED_HEAD,
    ROLE_BINDING_KIND_MISMATCH,
    ROLE_BINDING_MISSING,
    ROLE_BINDING_NEGATED_IN_SOURCE,
    ROLE_BINDING_NOT_OWN_CITE,
    ROLE_BINDING_QUOTE_NOT_IN_SOURCE,
    ROLE_BINDING_REASON_TEXTS,
    ROLE_BINDING_REVIEW_GUIDE,
    ROLE_BINDING_RULE_VERSION,
    ROLE_BINDING_STAGE_BY_REASON,
    ROLE_BINDING_TARGET_NOT_IN_CANDIDATE,
    ROLE_BINDING_UNBOUND_IN_SOURCE,
    ROLE_BINDING_WAIVER_VERBATIM_CONDITION,
    ROLE_WORDS,
)
from src.features.composer.verbatim_news import VerbatimNewsSource, verbatim_news_source
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import SOURCE_KIND_NEWS
from src.shared.report_quality.review_diagnostic_constants import REVIEW_SCOPE_ITEMS


# ── fixture: 사고의 정확한 문장(시험 fixture 에만 둔다) ─────────────────────
KIWOOM = (
    "일례로 키움증권은 오는 30일까지 신규·이전·만기 재가입 고객에 현금 1만5000원을 "
    "지급하고, 순입금과 타사 이전 등에 최대 500만원을 내건 이벤트를 진행한다."
)
PREFIX = "2026-09-10 mt.co.kr 보도에 따르면, "
CANDIDATE = PREFIX + KIWOOM
FID = "250"
SECTION = "business_model"
#: 운영 로그의 후보지문 — fixture 가 실제 사고 문장과 같음을 고정한다.
OBSERVED_FINGERPRINT = "62ae499eaf2fc502fb404529028c25831eec7a8737c85acd3eda20fe67a9b6d6"


def news_fragment(text=KIWOOM, fragment_id=FID, section_id=SECTION, **overrides):
    """수집기가 봉인하는 모양의 «검증된» 보도 조각. 시험용 합성 조각이다."""

    fields = dict(
        fragment_id=fragment_id,
        kind="typed-evidence-v1:" + "0" * 8,
        text=text,
        source_url="https://media.example/article/" + fragment_id,
        document_title="시험용 기사",
        document_date="2026-09-10",
        document_identity="document:media.example:" + fragment_id,
        document_content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        counts_toward_document_floor=False,
        formal_source_kind=SOURCE_KIND_NEWS,
        source_document_id="https://media.example/article/" + fragment_id,
        source_publisher="mt.co.kr",
        source_collected_on="2026-09-13",
        news_grounded=True,
        news_event_key="시험_사건_" + fragment_id,
        supported_claim_slots=CLAIM_SLOTS_BY_SECTION[section_id] if section_id else (),
    )
    fields.update(overrides)
    return CollectedFragment(**fields)


def relation(kind=RELATION_FEE, target="고객", value="재가입", quote=KIWOOM, source=FID):
    return {"유형": kind, "대상": target, "역할값": value, "근거": source, "원문": quote}


def bind(*items):
    return {"관계": list(items)}


def context(text=CANDIDATE, fragment=None, section_id=SECTION, allowed=None):
    fragment = fragment or news_fragment()
    return verbatim_news_source(
        text, (fragment.fragment_id,), {fragment.fragment_id: fragment},
        section_id=section_id, allowed_fragment_ids=allowed,
    )


def test_fixture_is_the_observed_candidate():
    assert len(KIWOOM) == 91
    assert hashlib.sha256(CANDIDATE.encode("utf-8")).hexdigest() == OBSERVED_FINGERPRINT


# ── ① 자격은 수집·보고서 객체로만 증명한다 ──────────────────────────────────
def test_exact_verified_single_news_citation_qualifies():
    ctx = context()
    assert ctx is not None
    assert ctx.source_id == FID and ctx.section_id == SECTION
    assert ctx.document_date == "2026-09-10" and ctx.publisher == "mt.co.kr"
    assert ctx.candidate_sha256 == OBSERVED_FINGERPRINT
    assert ctx.source_sha256 == hashlib.sha256(KIWOOM.encode("utf-8")).hexdigest()
    assert ctx.matches(KIWOOM, {FID: KIWOOM})


@pytest.mark.parametrize("field,value", (
    ("news_grounded", False),
    ("news_grounded", "False"),        # 참 같은 문자열은 검증 표시가 아니다
    ("news_grounded", "True"),
    ("news_grounded", 1),              # packet 계약은 bool — «정확히 True»만
    ("document_date", ""),
    ("source_publisher", ""),
    ("formal_source_kind", "filing"),
))
def test_unverified_or_non_news_fragment_does_not_qualify(field, value):
    fragment = news_fragment(**{field: value})
    text = CANDIDATE if field != "document_date" else " mt.co.kr 보도에 따르면, " + KIWOOM
    assert context(text=text, fragment=fragment) is None


@pytest.mark.parametrize("slots", (
    ("business_model:unknown_slot",),     # 접두사만 같은 정본 밖 칸
    ("business_model:",),
    ("business_model",),
    (),
))
def test_only_canonical_claim_slots_grant_ownership(slots):
    """장 소유권은 보도표와 같은 정본 역매핑을 쓴다 — 칸 이름 접두사로 추정하지 않는다."""

    assert context(fragment=news_fragment(supported_claim_slots=slots)) is None


def test_two_citations_or_missing_citation_do_not_qualify():
    news = news_fragment()
    filing = CollectedFragment("77", "사업내용", "회사는 ISA 계좌를 취급한다.")
    frag_by_id = {news.fragment_id: news, filing.fragment_id: filing}
    assert verbatim_news_source(CANDIDATE, (FID, "77"), frag_by_id, section_id=SECTION) is None
    # 잘못된 인용을 걸러낸 뒤 «하나 남았다»는 이유로 자격을 주지 않는다.
    assert verbatim_news_source(CANDIDATE, (FID, "없는조각"), frag_by_id, section_id=SECTION) is None
    assert verbatim_news_source(CANDIDATE, (), frag_by_id, section_id=SECTION) is None


@pytest.mark.parametrize("section_id,allowed", (
    ("operations_partners", None),          # 조각의 의미 칸이 그 장 것이 아니다
    ("identity", None),                     # 뉴스를 싣지 않는 장
    ("summary", None),                      # 요약 묶음은 소유 장이 없다
    (SECTION, frozenset({"다른조각"})),       # packet 허용 표 밖
    ("", None),
))
def test_section_ownership_is_required(section_id, allowed):
    assert context(section_id=section_id, allowed=allowed) is None
    assert context(section_id=SECTION, allowed=frozenset({FID})) is not None


@pytest.mark.parametrize("text", (
    KIWOOM,                                                     # 접두사 없음
    "2026-09-11 mt.co.kr 보도에 따르면, " + KIWOOM,             # 날짜가 다른 접두사
    PREFIX + "키움증권은 만기 재가입 고객에 현금 1만5000원을 지급한다.",  # 의역
    PREFIX + KIWOOM.split(",")[0] + ".",                        # 해당 절만
    PREFIX + KIWOOM + " 이 행사는 매년 반복된다.",               # 거짓 절 덧붙임
))
def test_anything_but_the_whole_fragment_does_not_qualify(text):
    assert context(text=text) is None


def test_context_rejects_inputs_it_was_not_built_for():
    ctx = context()
    assert not ctx.matches("키움증권 고객은 재가입한다.", {FID: KIWOOM})
    # 원문 지문이 다르면(조각 본문이 바뀜) 문맥을 쓰지 않는다.
    assert not ctx.matches(KIWOOM, {FID: KIWOOM + " "})
    assert not ctx.matches(KIWOOM, {"다른조각": KIWOOM})
    assert not ctx.matches(KIWOOM, {FID: KIWOOM, "공시": "공시 원문"})
    # 보조 재무표는 후보가 인용한 조각이 아니라 자동으로 실리는 값이라 세지 않는다.
    assert ctx.matches(KIWOOM, {FID: KIWOOM, "실적표": "매출액 2024 1,000억원"})


# ── ② 요구 계산 — 안내와 판정이 같은 결과를 쓴다 ───────────────────────────
def test_exact_single_participation_condition_is_waived():
    requirements = role_binding_requirements(KIWOOM, {FID: KIWOOM}, None, context())
    assert requirements.rule_version == ROLE_BINDING_RULE_VERSION
    assert requirements.verbatim_bound is True
    assert [(item.marker, item.kind, item.waiver) for item in requirements.items] == [
        ("재가입", RELATION_FEE, ROLE_BINDING_WAIVER_VERBATIM_CONDITION),
    ]
    assert requirements.required == ()


def test_without_context_or_with_a_mismatched_context_nothing_is_waived():
    plain = role_binding_requirements(KIWOOM, {FID: KIWOOM}, None, None)
    assert [(item.marker, item.waiver) for item in plain.items] == [("재가입", "")]
    assert plain.verbatim_bound is False
    other = VerbatimNewsSource(FID, SECTION, PREFIX, "0" * 64, "1" * 64, "2026-09-10", "mt.co.kr")
    mismatched = role_binding_requirements(KIWOOM, {FID: KIWOOM}, None, other)
    assert mismatched.verbatim_bound is False and mismatched.required == plain.required


def test_flow_cells_are_never_waived():
    requirements = role_binding_requirements(
        "고객 ; 재가입", {FID: KIWOOM}, ["고객", "재가입"], context())
    assert requirements.verbatim_bound is False
    assert [item.waiver for item in requirements.items] == [""]


@pytest.mark.parametrize("text", (
    # 표지가 둘 — 어느 자리도 제외하지 않는다
    "키움증권은 만기 재가입 고객에 현금을 지급하고, 재예치 고객에 상품권을 증정한다.",
    # 참여 조건이 아니라 실제 행동
    "키움증권 고객은 만기 후 재가입한다.",
    # 수령인과 혜택 사이에 다른 주체
    "키움증권은 재가입 고객에 대해 타사가 지급한다고 밝혔다.",
    # 수령인과 혜택 사이에 쉼표(다른 항목)
    "키움증권은 재가입 고객에 한해, 이벤트 기간에 지급한다.",
    # 여격이 아니라 주격 — 수익 주장 꼴(총괄 반례 ④)
    "재가입 고객이 주요 수익원이다.",
    # 반복이 아니라 대가 낱말
    "키움증권은 계좌 이전 고객에 수수료를 면제한다.",
))
def test_verbatim_news_outside_the_narrow_shape_keeps_the_requirement(text):
    fragment = news_fragment(text=text)
    ctx = context(text=PREFIX + text, fragment=fragment)
    assert ctx is not None, "원문 그대로인 검증 보도라 자격 자체는 있다"
    requirements = role_binding_requirements(text, {FID: text}, None, ctx)
    assert requirements.verbatim_bound is True
    assert requirements.items and requirements.waived == ()


# ── ③ 요구가 제외돼도 «제출된» 항목은 전부 대조한다 ──────────────────────────
def test_waived_candidate_without_entries_has_no_problem():
    assert role_binding_problem(KIWOOM, {FID: KIWOOM}, None, None, context()) == ""
    assert role_binding_problem(KIWOOM, {FID: KIWOOM}, {"관계": []}, None, context()) == ""
    # 같은 후보라도 문맥이 없으면 예전처럼 근거 누락이다.
    assert role_binding_problem(KIWOOM, {FID: KIWOOM}, None, None, None) == ROLE_BINDING_MISSING


@pytest.mark.parametrize("target", ("고객", "키움증권"))
def test_waived_candidate_with_a_correct_entry_still_passes(target):
    assert role_binding_problem(
        KIWOOM, {FID: KIWOOM}, bind(relation(target=target)), None, context()) == ""


@pytest.mark.parametrize("item,expected", (
    (relation(kind=RELATION_ROLE), ROLE_BINDING_KIND_MISMATCH),          # 관측 사유 그대로
    (relation(value="지급"), ROLE_BINDING_KIND_MISMATCH),                 # 발동 낱말 없는 역할값
    (relation(source="999"), ROLE_BINDING_NOT_OWN_CITE),
    (relation(quote=KIWOOM.replace("키움증권", "다른증권")), ROLE_BINDING_QUOTE_NOT_IN_SOURCE),
    (relation(target="다른증권"), ROLE_BINDING_TARGET_NOT_IN_CANDIDATE),
    (dict(relation(), 역할값=["재가입"]), ROLE_BINDING_FIELD_TYPE_INVALID),
))
def test_waiver_does_not_force_a_wrong_submitted_entry_through(item, expected):
    """요구 표지가 0개가 됐다는 이유로 제출 항목 검사를 건너뛰지 않는다."""

    assert role_binding_problem(KIWOOM, {FID: KIWOOM}, bind(item), None, context()) == expected


def test_a_second_wrong_entry_is_not_hidden_behind_the_waiver():
    entries = bind(relation(), relation(kind=RELATION_ROLE, target="키움증권"))
    assert role_binding_problem(KIWOOM, {FID: KIWOOM}, entries, None, context()) == ROLE_BINDING_KIND_MISMATCH


@pytest.mark.parametrize("item", (
    relation(kind="틀린유형"),
    relation(kind=""),
    {"대상": "고객", "역할값": "재가입", "근거": FID, "원문": KIWOOM},   # 유형 누락
    {},
))
def test_waived_candidate_rejects_entries_of_unknown_or_missing_type(item):
    """요구를 제외한 자리에서 계약 밖 유형은 «무시»가 아니라 탈락이다(총괄 실측 반례)."""

    assert role_binding_problem(KIWOOM, {FID: KIWOOM}, bind(item), None, context()) == (
        ROLE_BINDING_ENTRY_TYPE_UNKNOWN)
    # 올바른 과금 항목이 함께 있어도 계약 밖 항목이 숨지 않는다.
    assert role_binding_problem(KIWOOM, {FID: KIWOOM}, bind(relation(), item), None, context()) == (
        ROLE_BINDING_ENTRY_TYPE_UNKNOWN)


@pytest.mark.parametrize("kind", ("인과", "양보"))
def test_waived_candidate_keeps_entries_of_other_known_types_for_their_own_guard(kind):
    entries = bind({"유형": kind, "원인": "행사", "결과": "가입", "근거": FID, "원문": KIWOOM})
    report = role_binding_report(KIWOOM, {FID: KIWOOM}, entries, None, context())
    assert report.problem == "" and report.other_relation_entries == 1


def test_required_candidate_keeps_the_existing_tolerance_for_other_types():
    """요구가 남은 후보(면제 아님)의 계약 밖 항목 처리는 바꾸지 않는다 — 기존 계약대로
    역할·과금 항목만 읽고, 없으면 근거 누락이다."""

    assert role_binding_problem(KIWOOM, {FID: KIWOOM}, bind(relation(kind="틀린유형")), None, None) == (
        ROLE_BINDING_MISSING)
    assert role_binding_problem(KIWOOM, {FID: KIWOOM}, bind(relation(), {}), None, None) == ""


def test_report_carries_rule_version_stage_and_submitted_kinds_without_text():
    report = role_binding_report(KIWOOM, {FID: KIWOOM}, bind(relation(kind=RELATION_ROLE)), None, context())
    detail = report.as_diagnostic()
    assert report.problem == ROLE_BINDING_KIND_MISMATCH
    assert detail["rule_version"] == ROLE_BINDING_RULE_VERSION
    assert detail["stage"] == "kind"
    assert detail["required"] == []
    assert detail["waived"] == [{"unit": 0, "marker": "재가입", "kind": RELATION_FEE,
                                 "waiver": ROLE_BINDING_WAIVER_VERBATIM_CONDITION}]
    assert detail["submitted_kinds"] == [RELATION_ROLE]
    assert detail["verbatim_bound"] is True
    assert detail["verbatim_source"]["candidate_sha256"] == OBSERVED_FINGERPRINT
    serialized = repr(detail)
    assert KIWOOM not in serialized and "1만5000원" not in serialized and "키움증권" not in serialized


def test_report_counts_relation_entries_of_other_kinds():
    entries = {"관계": [relation(), {"유형": "인과", "원인": "x", "결과": "y"}]}
    report = role_binding_report(KIWOOM, {FID: KIWOOM}, entries, None, context())
    assert report.problem == "" and report.other_relation_entries == 1


# ── ④ 역방향 — 참여 조건 구절로 실제 행동·수익을 증명하지 못한다 ─────────────
@pytest.mark.parametrize("candidate", (
    "키움증권 고객은 재가입한다.",
    "고객의 재가입이 반복된다.",
))
def test_condition_clause_cannot_prove_an_actual_repeat_action(candidate):
    assert claims_role_or_fee(candidate)
    assert role_binding_problem(candidate, {FID: KIWOOM}, bind(relation()), None, None) == (
        ROLE_BINDING_CONDITION_NOT_ACTION)


@pytest.mark.parametrize("claim", (
    "고객의 재가입 증가가 매출 확대를 이끌었다.",
    "재구매 기반 수익이 발생한다.",
    "재계약 매출이 핵심이다.",
    "재가입 고객이 주요 수익원이다.",
    "계약 연장 수익을 얻는다.",
))
def test_noun_form_revenue_claims_keep_the_existing_guard(claim):
    """총괄 반례 다섯 문장 — 원문 그대로인 보도라도 참여 조건 꼴이 아니라 제외되지 않는다."""

    assert claims_role_or_fee(claim)
    assert role_binding_problem(claim, {FID: KIWOOM}) == ROLE_BINDING_MISSING
    fragment = news_fragment(text=claim)
    ctx = context(text=PREFIX + claim, fragment=fragment)
    assert ctx is not None
    assert role_binding_problem(claim, {FID: claim}, None, None, ctx) == ROLE_BINDING_MISSING


def test_noun_form_claim_with_the_condition_clause_as_proof_is_blocked():
    for claim in ("고객의 재가입 증가가 매출 확대를 이끌었다.", "재가입 고객이 주요 수익원이다."):
        assert role_binding_problem(claim, {FID: KIWOOM}, bind(relation()), None, None) == (
            ROLE_BINDING_CONDITION_NOT_ACTION)


def test_actual_action_stated_by_the_source_still_proves():
    action = "키움증권 고객은 만기 후 재가입한다."
    assert role_binding_problem(action, {FID: action}, bind(relation(quote=action)), None, None) == ""
    # 같은 인용의 «다른 절»이 실제 행동을 명시하면 그 절로 증명된다.
    both = KIWOOM + " " + action
    assert role_binding_problem(
        "키움증권 고객은 재가입한다.", {FID: both}, bind(relation(quote=both)), None, None) == ""


@pytest.mark.parametrize("source,expected", (
    ("고객은 재가입하지 않는다.", ROLE_BINDING_NEGATED_IN_SOURCE),
    ("고객은 만기인 경우에만 재가입한다.", ROLE_BINDING_CONDITION_DROPPED),
    ("타인은 재가입한다.", ROLE_BINDING_UNBOUND_IN_SOURCE),
))
def test_existing_negation_condition_and_subject_checks_remain(source, expected):
    assert role_binding_problem(
        "고객은 재가입한다.", {FID: source}, bind(relation(quote=source)), None, None) == expected


def test_flow_row_with_the_same_word_keeps_the_guard():
    assert role_binding_problem("", {FID: KIWOOM}, None, ["고객", "재가입"], context()) == ROLE_BINDING_MISSING


# ── ⑤ 안내와 판정이 같은 요구를 말한다 ──────────────────────────────────────
def test_hint_lists_the_marker_kind_and_waiver_the_guard_uses():
    with_context = grounding_hint(KIWOOM, {FID: KIWOOM}, None, verbatim_source=context())
    assert with_context.startswith("  추가 검증 필요: 없음\n")
    assert ROLE_BINDING_HINT_WAIVED_HEAD + "「재가입」" in with_context
    without = grounding_hint(KIWOOM, {FID: KIWOOM}, None)
    assert without.startswith("  추가 검증 필요: 관계\n")
    assert ROLE_BINDING_HINT_REQUIRED_HEAD + f'「재가입」→유형 「{RELATION_FEE}」' in without
    assert ROLE_BINDING_HINT_WAIVED_HEAD not in without
    flow = grounding_hint("고객 ; 재가입", {FID: KIWOOM}, ["고객", "재가입"], verbatim_source=context())
    assert "2번째 칸 「재가입」" in flow and ROLE_BINDING_HINT_WAIVED_HEAD not in flow


def test_hint_bytes_are_unchanged_for_candidates_without_role_or_fee_claims():
    assert grounding_hint("회사는 ISA 계좌를 취급한다.", {FID: "회사는 ISA 계좌를 취급한다."}) == (
        "  추가 검증 필요: 없음\n")


def test_review_guide_is_derived_from_the_same_word_lists():
    for word in (*ROLE_WORDS, *FEE_WORDS, *REPEAT_WORDS, *PROSE_FEE_WORDS, *PROSE_REPEAT_WORDS):
        assert word in ROLE_BINDING_REVIEW_GUIDE, word
    assert PROSE_REPEAT_MARKER_RE.pattern == "(" + "|".join(PROSE_REPEAT_WORDS) + ")"
    assert "|".join(PROSE_FEE_WORDS) in PROSE_FEE_MARKER_RE.pattern
    assert set(PROSE_REPEAT_WORDS) <= set(REPEAT_WORDS)
    assert set(PROSE_FEE_WORDS) <= set(FEE_WORDS)
    # 안내가 알려 준 낱말을 역할값에 그대로 적으면 유형 검사가 받아들인다.
    for word in (*PROSE_REPEAT_WORDS, *PROSE_FEE_WORDS):
        assert _kind_answers_role(RELATION_FEE, word), word
    for word in ROLE_WORDS:
        assert _kind_answers_role(RELATION_ROLE, word), word


def test_review_guide_maps_repeat_words_to_the_fee_kind_explicitly():
    assert re.search(r"반복 낱말 «[^»]+» → 유형 「" + RELATION_FEE + r"」", ROLE_BINDING_REVIEW_GUIDE)
    assert f'「{RELATION_ROLE}」이 아니다' in ROLE_BINDING_REVIEW_GUIDE
    # 산문 안내에 큰따옴표가 없어야 한다 — 모델이 옮겨 적으면 JSON이 깨진다(2026-09-14 운영 실측).
    prose = ROLE_BINDING_REVIEW_GUIDE.split("항목은 검증근거의")[0]
    assert '"' not in prose
    assert "결속 요구 제외" in ROLE_BINDING_REVIEW_GUIDE and "관계 결속 요구" in ROLE_BINDING_REVIEW_GUIDE


def test_review_guide_restricts_only_role_and_fee_entries_and_keeps_the_one_entry_rule():
    """인과·양보 항목은 같은 관계 배열을 쓴다 — 역할 표지가 없다는 이유로 금지하지 않는다.
    「자리마다 뒷받침」은 같은 대상·역할값을 항목 하나로 증명하는 기존 규칙과 충돌하지 않는다."""

    assert "역할·과금 유형의 항목은 「관계 결속 요구」 줄에 적힌 자리에만 낸다" in ROLE_BINDING_REVIEW_GUIDE
    assert "인과·양보 항목은 인과 안내가 따로 정하며 이 줄의 유무와 무관하다" in ROLE_BINDING_REVIEW_GUIDE
    assert "같은 대상·같은 역할값의 여러 자리는 항목 하나로 증명할 수 있고" in ROLE_BINDING_REVIEW_GUIDE
    assert "자리마다 항목 하나를 낸다" not in ROLE_BINDING_REVIEW_GUIDE
    waived_hint = grounding_hint(KIWOOM, {FID: KIWOOM}, None, verbatim_source=context())
    assert "역할·과금 항목을 넣지 않는다" in waived_hint
    assert "관계 항목을 넣지 않는다" not in waived_hint


def test_new_reason_codes_are_registered_and_every_code_has_a_stage():
    for code in (ROLE_BINDING_CONDITION_NOT_ACTION, ROLE_BINDING_ENTRY_TYPE_UNKNOWN):
        assert code in REVIEW_SCOPE_ITEMS
        assert code in ROLE_BINDING_REASON_TEXTS
    assert set(ROLE_BINDING_STAGE_BY_REASON) == set(ROLE_BINDING_REASON_TEXTS)
