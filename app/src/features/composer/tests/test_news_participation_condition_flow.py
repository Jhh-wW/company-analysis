# -*- coding: utf-8 -*-
"""원문 그대로인 단일 행사 조건 보도가 «실제 진입점»을 지나 본문·목록에 이어지는지.

부르는 것은 가드 함수가 아니라 verify_report(평문·묶음) → retain_verified_news →
_augment_news_blocks 다. 검수 응답은 전부 시험용 합성값이며 운영 원응답이 아니다.
fixture 문장은 실측 사고의 정확한 보도 원문이고(회사명·날짜·금액 포함) 시험에만 둔다.

각 시험은 «살려야 하는 것»과 «막아야 하는 것»을 짝으로 둔다.
"""

import json
from dataclasses import replace

import pytest

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.news_usage import (
    news_citation_ids, retain_verified_news, supplement_news_candidates,
)
from src.features.composer.pipeline import _augment_news_blocks
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, PerformanceTable,
)
from src.features.composer.role_binding_constants import (
    RELATION_FEE,
    RELATION_ROLE,
    ROLE_BINDING_CONDITION_NOT_ACTION,
    ROLE_BINDING_ENTRY_TYPE_UNKNOWN,
    ROLE_BINDING_FIELD_TYPE_INVALID,
    ROLE_BINDING_HINT_REQUIRED_HEAD,
    ROLE_BINDING_HINT_WAIVED_HEAD,
    ROLE_BINDING_KIND_MISMATCH,
    ROLE_BINDING_MISSING,
    ROLE_BINDING_NOT_OWN_CITE,
    ROLE_BINDING_QUOTE_NOT_IN_SOURCE,
    ROLE_BINDING_RULE_VERSION,
    ROLE_BINDING_TARGET_NOT_IN_CANDIDATE,
)
from src.features.composer.tests.test_role_binding_participation_condition import (
    CANDIDATE, FID, KIWOOM, OBSERVED_FINGERPRINT, PREFIX, SECTION, news_fragment, relation,
)
from src.features.composer.verify import verify_report


def _draft(text, fragment, citations=None, section_id=SECTION, alternative=False):
    """작가가 낸 것처럼 보이는 초안 문장 하나(검수 전 unverified)."""

    return ComposedReport((ComposedSection(section_id, (
        ComposedSentence(
            text, tuple(citations or (fragment.fragment_id,)), GRADE_CONFIRMED,
            planned_claim_slot=fragment.supported_claim_slots[0] if fragment.supported_claim_slots
            else section_id + ":시험",
            news_source_alternative=alternative,
        ),
    )),))


def _response(entries=None, verdict="참", number=1, section_id=SECTION, citations=(FID,)):
    row = {"번호": number, "장": section_id, "근거": list(citations),
           "근거대조": f"{citations[0] if citations else ''}: 원문 동일", "결과": verdict}
    if entries is not None:
        row["검증근거"] = entries
    return json.dumps({"판정": [row]}, ensure_ascii=False)


def _run(report, fragments, raw, *, grouped, table=None, allowed=None):
    """검수 → 뉴스 게시 조건 → 보도표까지 실제 경로로 돌린다."""

    calls = []

    def ask(prompt):
        calls.append(prompt)
        return raw

    diagnostics = []
    kwargs = {}
    if grouped:
        kwargs["allowed_fragment_ids_by_section"] = allowed or {
            section.section_id: frozenset(f.fragment_id for f in fragments)
            for section in report.sections
        }
    reviewed = verify_report(report, fragments, table, ask, diagnostics=diagnostics, **kwargs)
    rejections = []
    kept = retain_verified_news(reviewed, fragments, review_input=report, diagnostics=rejections)
    block = _augment_news_blocks(
        kept, fragments, None, enabled=True,
        review_candidates=news_citation_ids(report, fragments),
    )
    return {
        "body": [s.text for section in kept.sections for s in section.sentences],
        "rows": block.row_count,
        "blocked": dict(block.blocked_counts_by_reason),
        "diagnostics": diagnostics,
        "rejections": rejections,
        "calls": calls,
    }


def _exact_case(fragment=None):
    """작가가 쓰지 않은 검증 조각을 보강 단계가 «원문 그대로» 후보로 만든 상태."""

    fragment = fragment or news_fragment()
    empty = ComposedReport((ComposedSection(SECTION, ()),))
    candidates, added = supplement_news_candidates(empty, (fragment,))
    assert added == (fragment.fragment_id,)
    assert candidates.sections[0].sentences[0].text == CANDIDATE
    return candidates, (fragment,)


# ── 살려야 하는 것: 정확한 원문 + 불필요한 관계 항목 없음 ────────────────────
@pytest.mark.parametrize("grouped", (False, True))
def test_exact_article_without_a_relation_entry_reaches_body_and_list(grouped):
    """변경 전에는 「재가입」 결속 항목이 없다는 이유(role_binding_evidence_missing)로
    본문 0·목록 0 이었다. 요구 제외 뒤에는 본문 1·목록 1 이다."""

    report, fragments = _exact_case()
    result = _run(report, fragments, _response(), grouped=grouped)
    assert result["body"] == [CANDIDATE]
    assert result["rows"] == 1 and result["blocked"] == {}
    assert result["diagnostics"] == [] and result["rejections"] == []
    assert len(result["calls"]) == 1
    prompt = result["calls"][0]
    assert "  추가 검증 필요: 없음\n" in prompt
    assert ROLE_BINDING_HINT_WAIVED_HEAD + "「재가입」" in prompt
    assert ROLE_BINDING_HINT_REQUIRED_HEAD not in prompt


def test_single_and_grouped_prompts_carry_the_same_waiver_line():
    report, fragments = _exact_case()
    flat = _run(report, fragments, _response(), grouped=False)["calls"][0]
    grouped = _run(report, fragments, _response(), grouped=True)["calls"][0]
    flat_line = next(line for line in flat.splitlines() if line.startswith(ROLE_BINDING_HINT_WAIVED_HEAD))
    grouped_line = next(line for line in grouped.splitlines() if line.startswith(ROLE_BINDING_HINT_WAIVED_HEAD))
    assert flat_line == grouped_line


@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("target", ("고객", "키움증권"))
def test_exact_article_with_a_correct_entry_still_publishes(grouped, target):
    report, fragments = _exact_case()
    result = _run(report, fragments, _response({"관계": [relation(target=target)]}), grouped=grouped)
    assert result["body"] == [CANDIDATE] and result["rows"] == 1


def test_auto_added_performance_table_does_not_break_the_single_news_waiver():
    """평문 경로는 실적표가 있으면 모든 후보의 원문에 보조 재무표를 함께 싣는다.
    그 표는 후보가 인용한 조각이 아니므로 «단일 인용» 판단에 세지 않는다."""

    table = PerformanceTable(
        caption="3개년 주요 실적", headers=("항목", "2022", "2023", "2024"),
        rows=(("매출액", "1,500", "1,600", "1,683"),), unit="억원", cite="조각 1·사업내용",
    )
    report, fragments = _exact_case()
    result = _run(report, fragments, _response(), grouped=False, table=table)
    assert result["body"] == [CANDIDATE] and result["rows"] == 1
    assert ROLE_BINDING_HINT_WAIVED_HEAD in result["calls"][0]


# ── 막아야 하는 것: 잘못된 제출 항목은 여전히 탈락 ──────────────────────────
@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("item,expected", (
    (relation(kind=RELATION_ROLE), ROLE_BINDING_KIND_MISMATCH),
    (relation(value="지급"), ROLE_BINDING_KIND_MISMATCH),
    (relation(source="999"), ROLE_BINDING_NOT_OWN_CITE),
    (relation(quote=KIWOOM.replace("1만5000원", "3만원")), ROLE_BINDING_QUOTE_NOT_IN_SOURCE),
    (relation(target="다른증권"), ROLE_BINDING_TARGET_NOT_IN_CANDIDATE),
    (dict(relation(), 역할값=["재가입"]), ROLE_BINDING_FIELD_TYPE_INVALID),
))
def test_wrong_submitted_entry_is_still_rejected_and_not_forced_through(grouped, item, expected):
    """잘못된 과거 응답(유형 «역할» 등)을 그대로 강제 통과시키지 않는다."""

    report, fragments = _exact_case()
    result = _run(report, fragments, _response({"관계": [item]}), grouped=grouped)
    assert result["body"] == [] and result["rows"] == 0
    assert result["blocked"] == {"body_review_rejected": 1}
    assert [row["사유코드"] for row in result["rejections"]] == ["review_removed"]
    assert len(result["calls"]) == 1
    [diagnostic] = result["diagnostics"]
    assert diagnostic["reason_code"] == expected
    assert diagnostic["candidate_sha256"] == OBSERVED_FINGERPRINT
    detail = diagnostic["role_binding"]
    assert detail["rule_version"] == ROLE_BINDING_RULE_VERSION
    assert detail["required"] == [] and [w["marker"] for w in detail["waived"]] == ["재가입"]
    assert detail["verbatim_source"]["source_id"] == FID
    assert detail["verbatim_source"]["candidate_sha256"] == OBSERVED_FINGERPRINT
    serialized = json.dumps(result["diagnostics"], ensure_ascii=False)
    assert KIWOOM not in serialized and "1만5000원" not in serialized


@pytest.mark.parametrize("grouped", (False, True))
def test_a_correct_entry_does_not_cover_a_second_wrong_entry(grouped):
    report, fragments = _exact_case()
    entries = {"관계": [relation(), relation(kind=RELATION_ROLE, target="키움증권")]}
    result = _run(report, fragments, _response(entries), grouped=grouped)
    assert result["body"] == [] and result["rows"] == 0
    assert result["diagnostics"][0]["reason_code"] == ROLE_BINDING_KIND_MISMATCH


# ── 막아야 하는 것: 참여 조건을 실제 행동으로 바꾼 후보 ─────────────────────
@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("text", ("키움증권 고객은 재가입한다.", "고객의 재가입이 반복된다."))
def test_condition_generalised_to_an_action_is_blocked_even_if_the_reviewer_says_true(grouped, text):
    fragment = news_fragment()
    report = _draft(PREFIX + text, fragment)
    result = _run(report, (fragment,), _response({"관계": [relation()]}), grouped=grouped)
    assert result["body"] == [] and result["rows"] == 0
    [diagnostic] = result["diagnostics"]
    assert diagnostic["reason_code"] == ROLE_BINDING_CONDITION_NOT_ACTION
    assert diagnostic["role_binding"]["verbatim_bound"] is False
    assert diagnostic["role_binding"]["verbatim_source"] is None
    assert ROLE_BINDING_HINT_REQUIRED_HEAD + f'「재가입」→유형 「{RELATION_FEE}」' in result["calls"][0]


@pytest.mark.parametrize("grouped", (False, True))
def test_a_news_sentence_stating_the_actual_action_still_needs_and_accepts_a_binding(grouped):
    action = "키움증권 고객은 만기 후 재가입한다."
    fragment = news_fragment(text=action)
    empty = ComposedReport((ComposedSection(SECTION, ()),))
    report, _ = supplement_news_candidates(empty, (fragment,))
    without = _run(report, (fragment,), _response(), grouped=grouped)
    assert without["body"] == [] and without["diagnostics"][0]["reason_code"] == ROLE_BINDING_MISSING
    with_binding = _run(report, (fragment,), _response({"관계": [relation(quote=action)]}), grouped=grouped)
    assert with_binding["body"] == [PREFIX + action] and with_binding["rows"] == 1


# ── 막아야 하는 것: 숫자 없는 반례 — 숫자 검사가 빈틈을 가리지 않게 ─────────
NUMBER_FREE = "키움증권은 만기인 경우에만 재가입 고객에 사은품을 지급하지 않는다."


@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("candidate,target", (
    (NUMBER_FREE.replace("키움증권", "다른증권"), "다른증권"),       # 다른 회사
    (NUMBER_FREE.replace("만기인 경우에만 ", ""), "키움증권"),          # 조건 삭제
    (NUMBER_FREE.replace("지급하지 않는다", "지급한다"), "키움증권"),   # 부정 삭제
    (NUMBER_FREE.replace("고객에", "임직원에"), "키움증권"),            # 다른 지급 대상
))
def test_number_free_alterations_are_blocked_with_or_without_an_entry(grouped, candidate, target):
    fragment = news_fragment(text=NUMBER_FREE)
    report = _draft(PREFIX + candidate, fragment)
    entry = relation(target=target, quote=NUMBER_FREE)
    with_entry = _run(report, (fragment,), _response({"관계": [entry]}), grouped=grouped)
    assert with_entry["body"] == [] and with_entry["rows"] == 0
    without = _run(report, (fragment,), _response(), grouped=grouped)
    assert without["body"] == [] and without["rows"] == 0
    assert ROLE_BINDING_HINT_WAIVED_HEAD not in without["calls"][0]


@pytest.mark.parametrize("grouped", (False, True))
def test_a_false_clause_appended_to_the_exact_clause_gets_no_waiver(grouped):
    """같은 원문 절에 거짓 절을 덧붙인 후보 — 원문 «전체» 일치가 아니므로 요구를 제외하지
    않는다. 항목 없이는 차단된다.

    ⚠️ 기존 한계(변경 전과 같다): 덧붙인 절의 「반복된다」는 산문 반복 표지에서 의도적으로
       제외된 낱말이라(「지속적인 채용」 같은 일반 문장 오탐 방지), 그 절에 올바른 과금 항목이
       함께 오면 역할·과금 가드는 덧붙인 절을 검사하지 않는다. 숫자도 없어 수치 가드도 없다.
       그 절의 진위는 검수 AI의 거짓 판정 몫이며, 이 시험은 그 한계를 «그대로» 적어 두어
       조용히 넓어지거나 좁아지면 드러나게 한다.
    """

    fragment = news_fragment(text=NUMBER_FREE)
    report = _draft(PREFIX + NUMBER_FREE + " 이 행사는 매년 반복된다.", fragment)
    without = _run(report, (fragment,), _response(), grouped=grouped)
    assert without["body"] == [] and without["rows"] == 0
    assert without["diagnostics"][0]["reason_code"] == ROLE_BINDING_MISSING
    assert ROLE_BINDING_HINT_WAIVED_HEAD not in without["calls"][0]
    assert ROLE_BINDING_HINT_REQUIRED_HEAD + f'「재가입」→유형 「{RELATION_FEE}」' in without["calls"][0]
    with_entry = _run(report, (fragment,), _response({"관계": [relation(target="키움증권", quote=NUMBER_FREE)]}),
                      grouped=grouped)
    assert len(with_entry["body"]) == 1, "기존 한계: 덧붙인 절은 이 가드의 범위 밖(검수 AI 몫)"
    assert with_entry["diagnostics"] == []


@pytest.mark.parametrize("grouped", (False, True))
def test_the_number_free_exact_article_itself_is_kept(grouped):
    """위 반례의 짝 — 같은 원문을 그대로 옮기면(숫자 없이도) 본문·목록에 실린다."""

    fragment = news_fragment(text=NUMBER_FREE)
    empty = ComposedReport((ComposedSection(SECTION, ()),))
    report, _ = supplement_news_candidates(empty, (fragment,))
    result = _run(report, (fragment,), _response(), grouped=grouped)
    assert result["body"] == [PREFIX + NUMBER_FREE] and result["rows"] == 1


@pytest.mark.parametrize("candidate", (
    KIWOOM.replace("1만5000원", "3만원"),
    KIWOOM.replace("최대 500만원", "500만원"),
    KIWOOM.replace("키움증권", "다른증권"),
    KIWOOM.replace("오는 30일까지", "연중"),
))
def test_changed_amount_max_company_or_period_is_blocked(candidate):
    fragment = news_fragment()
    report = _draft(PREFIX + candidate, fragment)
    target = "다른증권" if "다른증권" in candidate else "키움증권"
    result = _run(report, (fragment,), _response({"관계": [relation(target=target)]}), grouped=False)
    assert result["body"] == [] and result["rows"] == 0


# ── 자격 없음: 기존 검사 유지 ────────────────────────────────────────────────
@pytest.mark.parametrize("grouped", (False, True))
def test_mixed_news_and_filing_citations_get_no_waiver(grouped):
    fragment = news_fragment()
    filing = CollectedFragment("77", "사업내용", "회사는 ISA 계좌를 취급한다.",
                               supported_claim_slots=fragment.supported_claim_slots)
    report = _draft(CANDIDATE, fragment, citations=(FID, "77"))
    result = _run(report, (fragment, filing), _response(citations=(FID, "77")), grouped=grouped)
    assert result["body"] == []
    assert result["diagnostics"][0]["reason_code"] == ROLE_BINDING_MISSING
    assert ROLE_BINDING_HINT_WAIVED_HEAD not in result["calls"][0]


def test_a_nonexistent_second_citation_never_reaches_the_waiver():
    fragment = news_fragment()
    report = _draft(CANDIDATE, fragment, citations=(FID, "없는조각"))
    result = _run(report, (fragment,), _response(), grouped=False)
    assert result["body"] == [] and result["rows"] == 0
    assert all(ROLE_BINDING_HINT_WAIVED_HEAD not in prompt for prompt in result["calls"])


def test_another_sections_ownership_gets_no_waiver():
    fragment = news_fragment()                       # 의미 칸은 business_model 의 것
    report = _draft(CANDIDATE, fragment, section_id="operations_partners")
    result = _run(report, (fragment,), _response(section_id="operations_partners"),
                  grouped=True, allowed={"operations_partners": frozenset({FID})})
    assert result["body"] == []
    assert result["diagnostics"][0]["reason_code"] == ROLE_BINDING_MISSING
    assert ROLE_BINDING_HINT_WAIVED_HEAD not in result["calls"][0]


@pytest.mark.parametrize("field,value", (
    ("news_grounded", False), ("document_date", ""), ("source_publisher", ""),
))
def test_missing_verification_date_or_publisher_gets_no_waiver(field, value):
    fragment = news_fragment(**{field: value})
    report = _draft(CANDIDATE, fragment)
    result = _run(report, (fragment,), _response(), grouped=False)
    assert result["body"] == [] and result["rows"] == 0
    assert all(ROLE_BINDING_HINT_WAIVED_HEAD not in prompt for prompt in result["calls"])


@pytest.mark.parametrize("text", (
    "키움증권은 만기 재가입 고객에 현금을 지급하고, 재예치 고객에 상품권을 증정한다.",
    "키움증권은 재가입 고객에 대해 타사가 지급한다고 밝혔다.",
))
def test_two_markers_or_another_subject_keep_the_requirement(text):
    fragment = news_fragment(text=text)
    empty = ComposedReport((ComposedSection(SECTION, ()),))
    report, _ = supplement_news_candidates(empty, (fragment,))
    result = _run(report, (fragment,), _response(), grouped=True)
    assert result["body"] == []
    assert result["diagnostics"][0]["reason_code"] == ROLE_BINDING_MISSING
    assert ROLE_BINDING_HINT_WAIVED_HEAD not in result["calls"][0]


def test_the_alternative_flag_alone_grants_nothing():
    """「원문대체후보」 표식은 자격 계산에 쓰이지 않는다 — 의역이면 요구가 남는다.

    의역에는 숫자를 두지 않는다 — 금액이 있으면 수치 결속이 먼저 발동해 이 가드의 사유가
    가려진다(숫자 검사 실패가 다른 검사의 빈틈을 가리는 시험을 피한다).
    """

    fragment = news_fragment()
    report = _draft(PREFIX + "키움증권은 만기 재가입 고객에게 현금을 지급한다.",
                    fragment, alternative=True)
    result = _run(report, (fragment,), _response(), grouped=False)
    assert result["body"] == []
    assert result["diagnostics"][0]["reason_code"] == ROLE_BINDING_MISSING
    assert ROLE_BINDING_HINT_WAIVED_HEAD not in result["calls"][0]


@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("items", (
    [{"유형": "틀린유형", "대상": "고객", "역할값": "재가입", "근거": FID, "원문": KIWOOM}],
    [{"대상": "고객", "역할값": "재가입", "근거": FID, "원문": KIWOOM}],
    [{}],
))
def test_waived_candidate_with_an_unknown_or_missing_type_entry_is_rejected(grouped, items):
    """요구를 제외한 자리에서 계약 밖 유형은 무시가 아니라 탈락이다(총괄 실측 반례)."""

    report, fragments = _exact_case()
    result = _run(report, fragments, _response({"관계": items}), grouped=grouped)
    assert result["body"] == [] and result["rows"] == 0
    [diagnostic] = result["diagnostics"]
    assert diagnostic["reason_code"] == ROLE_BINDING_ENTRY_TYPE_UNKNOWN
    assert diagnostic["role_binding"]["stage"] == "format"


@pytest.mark.parametrize("grouped", (False, True))
def test_waived_candidate_with_a_causal_entry_is_left_to_the_causal_guard(grouped):
    report, fragments = _exact_case()
    causal = {"유형": "인과", "원인": "행사", "결과": "가입", "근거": FID, "원문": KIWOOM}
    result = _run(report, fragments, _response({"관계": [causal]}), grouped=grouped)
    assert result["body"] == [CANDIDATE] and result["rows"] == 1
