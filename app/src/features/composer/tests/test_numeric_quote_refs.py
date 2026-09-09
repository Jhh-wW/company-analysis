"""검증근거.수치 배열의 원문참조(제안 A) — 판정/근거 일치와 공격 경계를 검증한다.

기존 전체 원문 형식(직접 '원문' 문자열)이 계속 통과하는지, 원문참조로 바꿔도
같은 판정이 나오는지, 참조가 유효하지 않을 때만 해당 후보가 닫히는지를 본다.
공급자 실제 토큰 절감은 재지 않는다(네트워크·유료 호출 없음) — 여기서 재는
것은 이 파이썬 프로세스 안의 JSON 바이트 크기뿐이다.
"""

from __future__ import annotations

import json

from src.features.composer.grounding import grounding_problem
from src.features.composer.grounding_constants import GROUNDING_INVALID, NUMERIC_KEY
from src.features.composer.numeric_quote_refs import resolve_numeric_quote_refs


def _numeric(
    expression: str,
    metric: str,
    quote: str,
    value: str,
    source_id: str = "공시",
    metric_source: str | None = None,
) -> dict[str, str]:
    return {
        "표현": expression,
        "항목": metric,
        "근거": source_id,
        "원문": quote,
        "원문항목": metric_source or metric,
        "원문값": value,
    }


def _ref(expression: str, metric: str, ref: object, value: str, source_id: str = "공시") -> dict:
    return {
        "표현": expression,
        "항목": metric,
        "근거": source_id,
        "원문참조": ref,
        "원문항목": metric,
        "원문값": value,
    }


# ══════════════════════════════════════════════════════════
# ① 최소 재현 — 3개년도, 직접형과 참조형이 같은 판정·같은 근거를 낸다
# ══════════════════════════════════════════════════════════

_THREE_YEAR_TEXT = (
    "매출액은 2023년 매출액 80억원, 2024년 매출액 100억원, "
    "2025년 매출액 120억원을 기록했다."
)
_THREE_YEAR_QUOTE = _THREE_YEAR_TEXT  # 원문이 후보 문장 전체를 그대로 담은 공시라고 본다.
_THREE_YEAR_SOURCE = {"공시": _THREE_YEAR_QUOTE}


def _three_year_direct_entries() -> list[dict]:
    return [
        _numeric("2023년 매출액 80억원", "매출액", _THREE_YEAR_QUOTE, "80억원"),
        _numeric("2024년 매출액 100억원", "매출액", _THREE_YEAR_QUOTE, "100억원"),
        _numeric("2025년 매출액 120억원", "매출액", _THREE_YEAR_QUOTE, "120억원"),
    ]


def _three_year_ref_entries() -> list[dict]:
    return [
        _numeric("2023년 매출액 80억원", "매출액", _THREE_YEAR_QUOTE, "80억원"),
        _ref("2024년 매출액 100억원", "매출액", 1, "100억원"),
        _ref("2025년 매출액 120억원", "매출액", 1, "120억원"),
    ]


def test_three_year_direct_and_ref_forms_agree_on_verdict() -> None:
    direct_evidence = {"검증근거": {NUMERIC_KEY: _three_year_direct_entries()}}
    ref_evidence = {"검증근거": {NUMERIC_KEY: _three_year_ref_entries()}}

    direct_result = grounding_problem(_THREE_YEAR_TEXT, _THREE_YEAR_SOURCE, direct_evidence)
    ref_result = grounding_problem(_THREE_YEAR_TEXT, _THREE_YEAR_SOURCE, ref_evidence)

    assert direct_result == "" == ref_result


def test_three_year_ref_form_produces_smaller_json_bytes() -> None:
    """공급자 토큰 절감을 재지 않는다 — 이 프로세스 안의 JSON 바이트만 잰다."""

    direct_evidence = {"검증근거": {NUMERIC_KEY: _three_year_direct_entries()}}
    ref_evidence = {"검증근거": {NUMERIC_KEY: _three_year_ref_entries()}}

    direct_bytes = len(json.dumps(direct_evidence, ensure_ascii=False).encode("utf-8"))
    ref_bytes = len(json.dumps(ref_evidence, ensure_ascii=False).encode("utf-8"))

    assert ref_bytes < direct_bytes
    # 이 고정 재현에서 정확히 얼마나 줄었는지도 남겨 둔다(회귀 감지용).
    assert direct_bytes - ref_bytes == 218


def test_resolved_entries_carry_the_exact_same_quote_string_byte_for_byte() -> None:
    """원문 문자열은 한 글자도 정규화·요약·변경하지 않는다."""

    resolved = resolve_numeric_quote_refs(_three_year_ref_entries())
    assert resolved[0]["원문"] == _THREE_YEAR_QUOTE
    assert resolved[1]["원문"] == _THREE_YEAR_QUOTE
    assert resolved[2]["원문"] == _THREE_YEAR_QUOTE
    # 참조로 채운 원문이 앞 행의 원문과 파이썬 == 비교로 완전히 같다(부분 일치 아님).
    assert resolved[1]["원문"] == resolved[0]["원문"]


def test_resolver_does_not_mutate_input_payload_in_place() -> None:
    entries = _three_year_ref_entries()
    original_row2 = dict(entries[1])

    resolve_numeric_quote_refs(entries)

    assert entries[1] == original_row2
    assert "원문참조" in entries[1]
    assert "원문" not in entries[1]


# ══════════════════════════════════════════════════════════
# ② 기존 전체 원문 형식과의 호환 — 원문참조를 전혀 안 쓰면 그대로 통과한다
# ══════════════════════════════════════════════════════════

def test_legacy_full_quote_form_still_passes_unchanged() -> None:
    text = "2025년 매출액 90억원을 기록했다."
    source = "매출액 | 2024년 | 100억원\n매출액 | 2025년 | 90억원"
    evidence = {
        "검증근거": {
            NUMERIC_KEY: [
                _numeric(
                    "2025년 매출액 90억원", "매출액",
                    "매출액 | 2025년 | 90억원", "90억원",
                )
            ]
        }
    }

    assert grounding_problem(text, {"공시": source}, evidence) == ""


def test_entries_without_any_ref_pass_through_object_identity_preserved() -> None:
    entries = _three_year_direct_entries()
    resolved = resolve_numeric_quote_refs(entries)
    assert resolved == entries
    assert resolved is not entries  # 새 목록이되 원소 자체는 그대로다.
    for original, out in zip(entries, resolved, strict=True):
        assert out is original


# ══════════════════════════════════════════════════════════
# ③ 공격 경계 — 전부 해당 후보 하나만 근거검증 실패로 닫혀야 한다
# ══════════════════════════════════════════════════════════

_TWO_ROW_TEXT = "매출액은 2024년 매출액 100억원, 2025년 매출액 120억원을 기록했다."
_TWO_ROW_QUOTE = _TWO_ROW_TEXT
_TWO_ROW_SOURCE = {"공시": _TWO_ROW_QUOTE}


def _closed(entries: list[dict]) -> str:
    evidence = {"검증근거": {NUMERIC_KEY: entries}}
    return grounding_problem(_TWO_ROW_TEXT, _TWO_ROW_SOURCE, evidence)


def test_self_reference_is_rejected() -> None:
    entries = [
        _ref("2024년 매출액 100억원", "매출액", 1, "100억원"),
        _numeric("2025년 매출액 120억원", "매출액", _TWO_ROW_QUOTE, "120억원"),
    ]
    assert _closed(entries) == GROUNDING_INVALID


def test_future_reference_is_rejected() -> None:
    entries = [
        _ref("2024년 매출액 100억원", "매출액", 2, "100억원"),
        _numeric("2025년 매출액 120억원", "매출액", _TWO_ROW_QUOTE, "120억원"),
    ]
    assert _closed(entries) == GROUNDING_INVALID


def test_out_of_range_reference_is_rejected() -> None:
    entries = [
        _numeric("2024년 매출액 100억원", "매출액", _TWO_ROW_QUOTE, "100억원"),
        _ref("2025년 매출액 120억원", "매출액", 5, "120억원"),
    ]
    assert _closed(entries) == GROUNDING_INVALID


def test_negative_index_reference_is_rejected() -> None:
    entries = [
        _numeric("2024년 매출액 100억원", "매출액", _TWO_ROW_QUOTE, "100억원"),
        _ref("2025년 매출액 120억원", "매출액", -1, "120억원"),
    ]
    assert _closed(entries) == GROUNDING_INVALID


def test_zero_index_reference_is_rejected() -> None:
    entries = [
        _numeric("2024년 매출액 100억원", "매출액", _TWO_ROW_QUOTE, "100억원"),
        _ref("2025년 매출액 120억원", "매출액", 0, "120억원"),
    ]
    assert _closed(entries) == GROUNDING_INVALID


def test_bool_index_reference_is_rejected() -> None:
    """bool은 int의 하위 타입이라 별도로 막아야 한다."""
    entries = [
        _numeric("2024년 매출액 100억원", "매출액", _TWO_ROW_QUOTE, "100억원"),
        _ref("2025년 매출액 120억원", "매출액", True, "120억원"),
    ]
    assert _closed(entries) == GROUNDING_INVALID


def test_string_index_reference_is_rejected() -> None:
    entries = [
        _numeric("2024년 매출액 100억원", "매출액", _TWO_ROW_QUOTE, "100억원"),
        _ref("2025년 매출액 120억원", "매출액", "1", "120억원"),
    ]
    assert _closed(entries) == GROUNDING_INVALID


def test_different_evidence_id_reference_is_rejected() -> None:
    entries = [
        _numeric("2024년 매출액 100억원", "매출액", _TWO_ROW_QUOTE, "100억원", source_id="공시"),
        _ref("2025년 매출액 120억원", "매출액", 1, "120억원", source_id="다른출처"),
    ]
    assert _closed(entries) == GROUNDING_INVALID


def test_quote_and_reference_both_present_is_rejected_even_if_quote_is_itself_valid() -> None:
    """직접 원문이 그 자체로 유효해도, 참조를 같이 주면 그 행은 닫는다."""
    entries = [
        _numeric("2024년 매출액 100억원", "매출액", _TWO_ROW_QUOTE, "100억원"),
        {
            "표현": "2025년 매출액 120억원", "항목": "매출액", "근거": "공시",
            "원문": _TWO_ROW_QUOTE, "원문참조": 1,
            "원문항목": "매출액", "원문값": "120억원",
        },
    ]
    assert _closed(entries) == GROUNDING_INVALID


def test_reference_chain_is_rejected() -> None:
    """참조가 참조를 가리키면(연쇄) 닫는다 — 대상은 반드시 직접 원문이어야 한다."""
    text = (
        "매출액은 2023년 매출액 80억원, 2024년 매출액 100억원, "
        "2025년 매출액 120억원을 기록했다."
    )
    quote = text
    entries = [
        _numeric("2023년 매출액 80억원", "매출액", quote, "80억원"),
        _ref("2024년 매출액 100억원", "매출액", 1, "100억원"),
        _ref("2025년 매출액 120억원", "매출액", 2, "120억원"),  # row2를 가리킴(row2도 참조)
    ]
    evidence = {"검증근거": {NUMERIC_KEY: entries}}
    assert grounding_problem(text, {"공시": quote}, evidence) == GROUNDING_INVALID


def test_invalid_reference_closes_only_that_candidate_not_other_verdict_numbers() -> None:
    """유효한 다른 후보(다른 판정 번호)까지 잘못 폐기하지 않는다."""
    from src.features.composer.grounding import constrain_verdicts

    broken_text = _TWO_ROW_TEXT
    broken_entries = [
        _numeric("2024년 매출액 100억원", "매출액", _TWO_ROW_QUOTE, "100억원"),
        _ref("2025년 매출액 120억원", "매출액", 9, "120억원"),  # 범위밖 — 이 후보만 닫혀야 한다.
    ]
    good_text = "2025년 영업이익 30억원을 기록했다."
    good_source = "영업이익 | 2025년 | 30억원"
    good_entries = [
        _numeric("2025년 영업이익 30억원", "영업이익", "영업이익 | 2025년 | 30억원", "30억원"),
    ]

    candidates = {
        1: (broken_text, _TWO_ROW_SOURCE),
        2: (good_text, {"공시": good_source}),
    }
    raw = json.dumps({
        "판정": [
            {"번호": 1, "결과": "참", "검증근거": {NUMERIC_KEY: broken_entries}},
            {"번호": 2, "결과": "참", "검증근거": {NUMERIC_KEY: good_entries}},
        ]
    }, ensure_ascii=False)
    verdicts = {1: "참", 2: "참"}

    result, problems = constrain_verdicts(raw, verdicts, candidates)

    assert result[1] == "근거결속실패"
    assert 1 in problems
    assert result[2] == "참"
    assert 2 not in problems


# ══════════════════════════════════════════════════════════
# ④ 부호·단위·기간·다른 항목 — 참조를 거쳐도 기존 검증이 그대로 걸린다
# ══════════════════════════════════════════════════════════

def test_sign_mismatch_through_reference_is_rejected() -> None:
    """참조로 실제 원문에 없는 부호(양수를 음수로)를 주장하면 실패한다."""
    text = "매출액은 2023년 매출액 -80억원, 2024년 매출액 100억원을 기록했다."
    quote = text
    entries = [
        _numeric("2023년 매출액 -80억원", "매출액", quote, "-80억원"),
        _ref("2024년 매출액 100억원", "매출액", 1, "-100억원"),  # 원문엔 -100억원이 없다.
    ]
    evidence = {"검증근거": {NUMERIC_KEY: entries}}
    assert grounding_problem(text, {"공시": quote}, evidence) == GROUNDING_INVALID


def test_sign_preserved_correctly_through_reference_passes() -> None:
    text = "매출액은 2023년 매출액 -80억원, 2024년 매출액 100억원을 기록했다."
    quote = text
    entries = [
        _numeric("2023년 매출액 -80억원", "매출액", quote, "-80억원"),
        _ref("2024년 매출액 100억원", "매출액", 1, "100억원"),
    ]
    evidence = {"검증근거": {NUMERIC_KEY: entries}}
    assert grounding_problem(text, {"공시": quote}, evidence) == ""


def test_dimension_mismatch_through_reference_is_rejected() -> None:
    """참조 대상(row1)의 «좁은» 원문에 없는 다른 차원(비율)을 다음 행이 빌리면 실패한다."""
    narrow_quote = "2024년 매출액 100억원"
    full_source = "2024년 매출액 100억원, 영업이익률은 2024년 영업이익률 5%였다."
    text = "매출액은 2024년 매출액 100억원, 영업이익률은 2024년 영업이익률 5%였다."
    entries = [
        _numeric("2024년 매출액 100억원", "매출액", narrow_quote, "100억원"),
        _ref("2024년 영업이익률 5%", "영업이익률", 1, "5%"),
    ]
    evidence = {"검증근거": {NUMERIC_KEY: entries}}
    assert grounding_problem(text, {"공시": full_source}, evidence) == GROUNDING_INVALID


def test_period_mismatch_through_reference_is_rejected() -> None:
    """참조 대상 원문에 없는 다른 연도·값을 다음 행이 빌리면 실패한다."""
    text = "매출액은 2023년 매출액 100억원, 2024년 매출액 120억원을 기록했다."
    quote = text
    entries = [
        _numeric("2023년 매출액 100억원", "매출액", quote, "100억원"),
        _ref("2025년 매출액 999억원", "매출액", 1, "999억원"),  # 표현 자체가 text에 없다.
    ]
    evidence = {"검증근거": {NUMERIC_KEY: entries}}
    assert grounding_problem(text, {"공시": quote}, evidence) == GROUNDING_INVALID


def test_different_item_through_reference_is_rejected() -> None:
    """참조 대상(row1)의 좁은 원문이 다른 항목(영업이익)만 담고 있으면
    매출액을 주장하는 참조 행은 실패한다."""
    narrow_quote = "2024년 영업이익 50억원"
    full_source = "2024년 영업이익 50억원, 2024년 매출액 100억원을 기록했다."
    text = "영업이익은 2024년 영업이익 50억원, 매출액은 2024년 매출액 100억원을 기록했다."
    entries = [
        _numeric("2024년 영업이익 50억원", "영업이익", narrow_quote, "50억원"),
        _ref("2024년 매출액 100억원", "매출액", 1, "100억원"),
    ]
    evidence = {"검증근거": {NUMERIC_KEY: entries}}
    assert grounding_problem(text, {"공시": full_source}, evidence) == GROUNDING_INVALID


# ══════════════════════════════════════════════════════════
# ⑤ 빈/공백 근거 ID — "정확히 같은 비어있지 않은 근거 ID"만 참조를 허용한다.
#    ID가 둘 다 "" 또는 공백뿐이면 문자열 비교(current_id == target_id)는
#    통과하지만 이는 "같은 근거"라는 사실 자체가 없는 것이므로 별도로
#    거절해야 한다.
# ══════════════════════════════════════════════════════════

_SHARED_TWO_ROW_QUOTE = "2023년 매출액 80억원, 2024년 매출액 100억원을 기록했다."


def _empty_id_entries(current_id: str, target_id: str) -> list[dict]:
    return [
        _numeric("2023년 매출액 80억원", "매출액", _SHARED_TWO_ROW_QUOTE, "80억원", source_id=target_id),
        _ref("2024년 매출액 100억원", "매출액", 1, "100억원", source_id=current_id),
    ]


def test_both_empty_evidence_ids_reference_is_rejected() -> None:
    """둘 다 빈 문자열 근거 ID면 문자열은 같아도 참조를 승인하지 않는다."""
    entries = _empty_id_entries("", "")
    evidence = {"검증근거": {NUMERIC_KEY: entries}}
    result = grounding_problem(_SHARED_TWO_ROW_QUOTE, {"": _SHARED_TWO_ROW_QUOTE}, evidence)
    assert result == GROUNDING_INVALID


def test_both_whitespace_only_evidence_ids_reference_is_rejected() -> None:
    """공백뿐인 근거 ID도 "같은 근거"로 인정하지 않는다."""
    entries = _empty_id_entries("   ", "   ")
    evidence = {"검증근거": {NUMERIC_KEY: entries}}
    result = grounding_problem(_SHARED_TWO_ROW_QUOTE, {"   ": _SHARED_TWO_ROW_QUOTE}, evidence)
    assert result == GROUNDING_INVALID


def test_resolver_does_not_strip_ids_to_force_equality() -> None:
    """앞뒤 공백만 다른 ID끼리는(strip하면 같아 보여도) 원래 문자열 그대로
    비교해 다른 ID로 취급한다 — 빈/공백 거절과는 별개로 정확 일치 규칙 자체는
    느슨해지지 않았음을 확인한다."""
    entries = [
        _numeric("2023년 매출액 80억원", "매출액", _SHARED_TWO_ROW_QUOTE, "80억원", source_id="공시"),
        _ref("2024년 매출액 100억원", "매출액", 1, "100억원", source_id=" 공시"),
    ]
    resolved = resolve_numeric_quote_refs(entries)
    assert "원문" not in resolved[1]
    evidence = {"검증근거": {NUMERIC_KEY: entries}}
    result = grounding_problem(
        _SHARED_TWO_ROW_QUOTE, {"공시": _SHARED_TWO_ROW_QUOTE, " 공시": _SHARED_TWO_ROW_QUOTE}, evidence
    )
    assert result == GROUNDING_INVALID


def test_meaningful_nonempty_evidence_id_reference_still_passes() -> None:
    """빈/공백 ID 거절이 정상 비어있지 않은 근거 ID의 참조까지 막지 않는다."""
    entries = _empty_id_entries("공시", "공시")
    evidence = {"검증근거": {NUMERIC_KEY: entries}}
    result = grounding_problem(_SHARED_TWO_ROW_QUOTE, {"공시": _SHARED_TWO_ROW_QUOTE}, evidence)
    assert result == ""


def test_meaningful_nonempty_id_resolved_quote_is_byte_for_byte_unchanged() -> None:
    """정상 참조는 여전히 원문 문자열을 한 글자도 바꾸지 않고 복사한다."""
    entries = _empty_id_entries("공시", "공시")
    resolved = resolve_numeric_quote_refs(entries)
    assert resolved[1]["원문"] == _SHARED_TWO_ROW_QUOTE == resolved[0]["원문"]


def test_empty_id_rejection_closes_only_that_candidate_not_other_verdict_numbers() -> None:
    """빈 근거 ID로 닫힌 후보가 다른 정상 후보(다른 판정 번호)까지 폐기하지 않는다."""
    from src.features.composer.grounding import constrain_verdicts

    broken_entries = _empty_id_entries("", "")
    good_text = "2025년 영업이익 30억원을 기록했다."
    good_source = "영업이익 | 2025년 | 30억원"
    good_entries = [
        _numeric("2025년 영업이익 30억원", "영업이익", "영업이익 | 2025년 | 30억원", "30억원"),
    ]

    candidates = {
        1: (_SHARED_TWO_ROW_QUOTE, {"": _SHARED_TWO_ROW_QUOTE}),
        2: (good_text, {"공시": good_source}),
    }
    raw = json.dumps({
        "판정": [
            {"번호": 1, "결과": "참", "검증근거": {NUMERIC_KEY: broken_entries}},
            {"번호": 2, "결과": "참", "검증근거": {NUMERIC_KEY: good_entries}},
        ]
    }, ensure_ascii=False)
    verdicts = {1: "참", 2: "참"}

    result, problems = constrain_verdicts(raw, verdicts, candidates)

    assert result[1] == "근거결속실패"
    assert 1 in problems
    assert result[2] == "참"
    assert 2 not in problems
