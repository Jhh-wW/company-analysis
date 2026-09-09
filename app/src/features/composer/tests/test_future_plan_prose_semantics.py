# -*- coding: utf-8 -*-
"""성장 전략 «본문» 계획 주장의 의미 시험 — 막을 것과 살릴 것을 짝지어 둔다.

표 계약은 그대로다. 여기서는 새 산문 경로(`future_plan_prose_problem`)의 «판정»만
본다. 실제 검수 경로(`verify_report`) 회귀는 `test_future_prose_scope_integration.py`
가 맡는다.

막는 사례마다 살리는 사례를 하나씩 붙였다. 전부 막는 가드도 초록불로 보이기 때문이다.
확정 반례의 문장·원문은 이 폴더의 `fixtures/future_prose_scope_case.json` 에서 읽는다.
"""

import json
from pathlib import Path

import pytest

from src.features.composer.future_plan_constants import (
    FUTURE_ACTIVITY_KEY,
    FUTURE_EVIDENCE_MISSING,
    FUTURE_FIELD_TYPE_INVALID,
    FUTURE_KEY,
    FUTURE_MODE_KEY,
    FUTURE_MODE_MISDECLARED,
    FUTURE_MODE_OUTLOOK,
    FUTURE_MODE_PLAN,
    FUTURE_OUTLOOK_HARDENED,
    FUTURE_PLAN_DENIED_IN_SOURCE,
    FUTURE_POLARITY_FLIPPED,
    FUTURE_QUOTE_KEY,
    FUTURE_QUOTE_NOT_IN_SOURCE,
    FUTURE_REASON_CODES,
    FUTURE_SECOND_CLAIM_UNPROVEN,
    FUTURE_SOURCE_KEY,
    FUTURE_SOURCE_STATES_CURRENT,
    FUTURE_SUBJECT_MISMATCH,
    FUTURE_TARGET_KEY,
)
from src.features.composer.future_plan_guard import (
    future_plan_problem,
    future_plan_prose_problem,
)

_FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
_CASE = json.loads(
    (_FIXTURE_DIR / "future_prose_scope_case.json").read_text(encoding="utf-8")
)
ACTUAL_PROSE = _CASE["paragraph"]
ACTUAL_SOURCE = _CASE["source"]
ACTUAL_SOURCE_ID = _CASE["fragment_id"]


def item(target, activity, quote, mode=FUTURE_MODE_PLAN, source="s1"):
    return {
        FUTURE_SOURCE_KEY: source,
        FUTURE_TARGET_KEY: target,
        FUTURE_ACTIVITY_KEY: activity,
        FUTURE_QUOTE_KEY: quote,
        FUTURE_MODE_KEY: mode,
    }


def evidence(*items):
    return {FUTURE_KEY: list(items)}


# ── 확정 반례 그대로 ────────────────────────────────────────────────────
def test_actual_sm_industry_present_cannot_become_a_company_plan():
    """저장된 보고서 문장과 원문을 그대로 넣은 회귀다."""

    sources = {ACTUAL_SOURCE_ID: ACTUAL_SOURCE}
    best_effort = evidence(item(
        "부가사업의 비중", "확대",
        "스트리밍, 영상 콘텐츠, 온라인 공연, 팬 커뮤니티 플랫폼 등 부가사업의 비중도"
        " 지속적으로 확대되고 있습니다",
        source=ACTUAL_SOURCE_ID,
    ))
    problem = future_plan_prose_problem(ACTUAL_PROSE, sources, best_effort)
    assert problem, "산업의 현재 변화가 회사 방침으로 그대로 통과했다"
    assert problem in FUTURE_REASON_CODES


def test_actual_sm_prose_without_any_future_evidence_is_blocked():
    sources = {ACTUAL_SOURCE_ID: ACTUAL_SOURCE}
    assert future_plan_prose_problem(ACTUAL_PROSE, sources, None) == FUTURE_EVIDENCE_MISSING
    assert future_plan_prose_problem(ACTUAL_PROSE, sources, {}) == FUTURE_EVIDENCE_MISSING


# ── 발동 범위: 회사의 계획·전망을 «명시»했을 때만 문이 열린다 ───────────
NORMAL_PROSE = (
    "산업 구조는 빠르게 진화하고 있으며, 부가사업의 비중도 지속적으로 확대되고 있다.",
    "회사는 북미 시장에서의 현지화 전략을 바탕으로 글로벌 영향력을 확대하고 있다.",
    "회사는 2025년 중 디어유를 연결 종속회사로 편입시켰다.",
    "음악 콘텐츠는 소득 탄력성이 높고 소비자 충성도가 강한 특성을 가진다.",
)


@pytest.mark.parametrize("prose", NORMAL_PROSE)
def test_present_facts_industry_outlook_and_background_do_not_fire(prose):
    """계획 주장이 아닌 산문에는 아무것도 요구하지 않으므로 정상 문장이 살아남는다."""

    assert future_plan_prose_problem(prose, {"s1": "무관한 원문"}, None) == ""


def test_company_plan_with_matching_own_source_is_preserved():
    source = "당사는 향후 팬 플랫폼 사업을 확대할 계획입니다."
    prose = "회사는 팬 플랫폼 사업을 확대할 계획이다."
    good = evidence(item("팬 플랫폼 사업", "확대", source))
    assert future_plan_prose_problem(prose, {"s1": source}, good) == ""


def test_company_outlook_kept_as_outlook_is_preserved():
    source = "당사는 신규 플랫폼 사업을 확대할 것으로 기대하고 있습니다."
    prose = "회사는 신규 플랫폼 사업을 확대할 것으로 기대한다."
    good = evidence(item("신규 플랫폼 사업", "확대", source, mode=FUTURE_MODE_OUTLOOK))
    assert future_plan_prose_problem(prose, {"s1": source}, good) == ""


# ── 확정된 실패 꼴 ──────────────────────────────────────────────────────
def test_present_source_declared_as_plan_is_rejected():
    source = "당사는 팬 플랫폼 사업을 확대하고 있습니다."
    prose = "회사는 팬 플랫폼 사업을 확대할 방침이다."
    claimed = evidence(item("팬 플랫폼 사업", "확대", source))
    assert future_plan_prose_problem(prose, {"s1": source}, claimed) == (
        FUTURE_SOURCE_STATES_CURRENT
    )


def test_another_subject_plan_cannot_be_borrowed():
    source = "경쟁사는 팬 플랫폼 사업을 확대할 계획입니다."
    prose = "회사는 팬 플랫폼 사업을 확대할 계획이다."
    borrowed = evidence(item("팬 플랫폼 사업", "확대", source))
    assert future_plan_prose_problem(prose, {"s1": source}, borrowed) == (
        FUTURE_SUBJECT_MISMATCH
    )


def test_a_later_plan_in_the_source_cannot_cover_a_present_activity():
    """「A를 운영하고 있으며 B를 확대할 계획」에서 A는 계획이 아니다."""

    source = "당사는 여행 상품을 운영하고 있으며 팬 플랫폼 사업을 확대할 계획입니다."
    prose = "회사는 여행 상품을 운영할 계획이다."
    borrowed = evidence(item("여행 상품", "운영", source))
    assert future_plan_prose_problem(prose, {"s1": source}, borrowed) == (
        FUTURE_SOURCE_STATES_CURRENT
    )


def test_outlook_hardened_into_a_plan_is_rejected():
    source = "당사는 신규 플랫폼 사업을 확대할 것으로 기대하고 있습니다."
    prose = "회사는 신규 플랫폼 사업을 확대할 방침이다."
    hardened = evidence(item("신규 플랫폼 사업", "확대", source, mode=FUTURE_MODE_OUTLOOK))
    assert future_plan_prose_problem(prose, {"s1": source}, hardened) == (
        FUTURE_OUTLOOK_HARDENED
    )


def test_declaring_the_wrong_mode_is_rejected():
    source = "당사는 신규 플랫폼 사업을 확대할 것으로 기대하고 있습니다."
    prose = "회사는 신규 플랫폼 사업을 확대할 것으로 기대한다."
    misdeclared = evidence(item("신규 플랫폼 사업", "확대", source, mode=FUTURE_MODE_PLAN))
    assert future_plan_prose_problem(prose, {"s1": source}, misdeclared) == (
        FUTURE_MODE_MISDECLARED
    )


def test_a_denied_plan_cannot_be_truncated_into_a_positive_one():
    source = "당사는 팬 플랫폼 사업을 확대할 계획은 없습니다."
    prose = "회사는 팬 플랫폼 사업을 확대할 계획이다."
    truncated = evidence(item("팬 플랫폼 사업", "확대", source))
    assert future_plan_prose_problem(prose, {"s1": source}, truncated) == (
        FUTURE_PLAN_DENIED_IN_SOURCE
    )


def test_a_negative_plan_cannot_be_flipped_into_a_positive_one():
    source = "당사는 여행 상품 사업을 확대하지 않을 계획입니다."
    prose = "회사는 여행 상품 사업을 확대할 계획이다."
    flipped = evidence(item("여행 상품 사업", "확대", source))
    assert future_plan_prose_problem(prose, {"s1": source}, flipped) == (
        FUTURE_POLARITY_FLIPPED
    )


def test_a_negative_plan_kept_negative_is_preserved():
    source = "당사는 여행 상품 사업을 확대하지 않을 계획입니다."
    prose = "회사는 여행 상품 사업을 확대하지 않을 계획이다."
    kept = evidence(item("여행 상품 사업", "확대", source))
    assert future_plan_prose_problem(prose, {"s1": source}, kept) == ""


def test_quote_must_come_from_one_cited_source():
    prose = "회사는 팬 플랫폼 사업을 확대할 계획이다."
    sources = {"s1": "당사는 팬 플랫폼 사업을", "s2": " 확대할 계획입니다."}
    stitched = evidence(item(
        "팬 플랫폼", "확대", "당사는 팬 플랫폼 사업을 확대할 계획입니다."))
    assert future_plan_prose_problem(prose, sources, stitched) == (
        FUTURE_QUOTE_NOT_IN_SOURCE
    )


def test_a_second_plan_claim_in_the_same_sentence_needs_its_own_proof():
    source = "당사는 팬 플랫폼 사업을 확대할 계획입니다."
    prose = "회사는 팬 플랫폼 사업을 확대할 계획이며, 여행 상품도 늘릴 방침이다."
    only_first = evidence(item("팬 플랫폼 사업", "확대", source))
    assert future_plan_prose_problem(prose, {"s1": source}, only_first) == (
        FUTURE_SECOND_CLAIM_UNPROVEN
    )


# ── 같은 대상·활동이 되풀이될 때 ────────────────────────────────────────
_REPEAT_PROSE = (
    "회사는 팬 플랫폼 사업을 확대할 계획이다."
    " 회사는 내년에도 팬 플랫폼 사업을 확대할 방침이다."
)
_REPEAT_A = "당사는 팬 플랫폼 사업을 확대할 계획입니다."
_REPEAT_B = "당사는 내년에도 팬 플랫폼 사업을 확대할 계획입니다."
_REPEAT_SOURCES = {"s1": _REPEAT_A, "s2": _REPEAT_B}


def test_a_repeated_plan_backed_on_both_sides_is_preserved():
    """되풀이된 두 자리를 각자의 원문이 뒷받침하면 통과한다.

    근거가 «첫 자리»에만 결속되면 뒤 자리가 미증명으로 몰려 정상 계획이 잘린다.
    """

    both = evidence(item("팬 플랫폼 사업", "확대", _REPEAT_A, source="s1"),
                    item("팬 플랫폼 사업", "확대", _REPEAT_B, source="s2"))
    assert future_plan_prose_problem(_REPEAT_PROSE, _REPEAT_SOURCES, both) == ""


def test_a_repeated_plan_proven_only_once_is_rejected():
    """문장은 위와 똑같고 근거만 하나다 — 두 번째 자리는 여전히 미증명이다."""

    only_first = evidence(item("팬 플랫폼 사업", "확대", _REPEAT_A, source="s1"))
    assert future_plan_prose_problem(_REPEAT_PROSE, _REPEAT_SOURCES, only_first) == (
        FUTURE_SECOND_CLAIM_UNPROVEN
    )


def test_a_plan_after_a_present_sentence_binds_to_the_plan_position():
    """앞 문장이 같은 대상·활동을 현재로 적어도 근거는 뒤 계획 자리에 붙는다."""

    prose = (
        "회사는 팬 플랫폼 사업을 확대하고 있다."
        " 회사는 팬 플랫폼 사업을 확대할 계획이다."
    )
    one = evidence(item("팬 플랫폼 사업", "확대", _REPEAT_A, source="s1"))
    assert future_plan_prose_problem(prose, _REPEAT_SOURCES, one) == ""


def test_a_leading_future_adverb_is_not_a_separate_claim():
    """「향후·내년」은 시점을 말하는 부사일 뿐 별도의 계획 주장이 아니다.

    부사를 주장 자리로 세우면 활동이 언제나 그 뒤에 와서, 원문이 완벽히
    뒷받침하는 정상 문장까지 영영 증명되지 못한다.
    """

    prose = "회사는 향후 팬 플랫폼 사업을 확대할 계획이다."
    one = evidence(item("팬 플랫폼 사업", "확대", _REPEAT_A, source="s1"))
    assert future_plan_prose_problem(prose, _REPEAT_SOURCES, one) == ""


def test_an_adverb_only_future_sentence_still_needs_its_evidence():
    """서술어 표지 없이 부사만 있어도 회사의 미래 주장이면 근거를 요구한다."""

    prose = "회사는 내년에 팬 플랫폼 사업을 확대한다."
    assert future_plan_prose_problem(prose, _REPEAT_SOURCES, None) == (
        FUTURE_EVIDENCE_MISSING
    )
    one = evidence(item("팬 플랫폼 사업", "확대", _REPEAT_A, source="s1"))
    assert future_plan_prose_problem(prose, _REPEAT_SOURCES, one) == ""


def test_an_adverb_does_not_excuse_a_second_unproven_claim():
    """부사가 붙어도 서술어 표지가 둘이면 여전히 각자의 근거가 필요하다."""

    prose = "회사는 향후 팬 플랫폼 사업을 확대할 계획이며, 여행 상품도 늘릴 방침이다."
    only_first = evidence(item("팬 플랫폼 사업", "확대", _REPEAT_A, source="s1"))
    assert future_plan_prose_problem(prose, _REPEAT_SOURCES, only_first) == (
        FUTURE_SECOND_CLAIM_UNPROVEN
    )


@pytest.mark.parametrize("broken", ("문자열", 3, [1]))
def test_malformed_future_evidence_never_approves(broken):
    prose = "회사는 팬 플랫폼 사업을 확대할 계획이다."
    problem = future_plan_prose_problem(prose, {"s1": "무관"}, {FUTURE_KEY: broken})
    assert problem in (FUTURE_FIELD_TYPE_INVALID, FUTURE_EVIDENCE_MISSING)


# ── 표 계약은 움직이지 않는다 ───────────────────────────────────────────
def test_table_rows_keep_their_existing_contract():
    source = "당사는 향후 팬 플랫폼 사업을 확대할 계획입니다."
    cells = ("팬 플랫폼 사업 확대", "", "팬 플랫폼 사업 확대")
    good = evidence(item("팬 플랫폼 사업", "확대", source))
    assert future_plan_problem(cells, {"s1": source}, good) == ""
    assert future_plan_problem(cells, {"s1": source}, None) == FUTURE_EVIDENCE_MISSING


def test_prose_guard_reuses_the_registered_reason_codes_only():
    """새 사유 코드를 만들지 않았으므로 진단 전송 계약이 그대로다."""

    prose = "회사는 팬 플랫폼 사업을 확대할 방침이다."
    seen = {
        future_plan_prose_problem(prose, {"s1": "당사는 팬 플랫폼 사업을 확대하고 있습니다."},
                                  evidence(item("팬 플랫폼 사업", "확대",
                                                "당사는 팬 플랫폼 사업을 확대하고 있습니다."))),
        future_plan_prose_problem(prose, {"s1": "무관"}, None),
    }
    assert seen and all(code in FUTURE_REASON_CODES for code in seen)
