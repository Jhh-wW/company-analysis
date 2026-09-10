# -*- coding: utf-8 -*-
"""임원 직함 시점 가드의 단위 회귀.

양성 fixture는 실제 실행(멀티캠퍼스, 979d5e3f3cdd68ad4f4249c4bca3adef, 기준일
2026-09-10)의 조각 원문에서 그대로 옮겼다 — DART 사업보고서 정정(2026-03-16,
접수번호 20260316000476)의 「다.등기임원 선임 후보자 및 해임 대상자 현황」
(정석목 해임 대상자)과 「나. 경영진 및 감사의 중요한 변동」(김주호 사외이사
신규 선임, 2025-03-19) 두 대목이다.

각 시험은 role_binding.py의 관례를 따라 «막아야 하는 것»과 «살려야 하는 것»을
짝으로 둔다 — 한쪽만 있으면 가드가 전부 막아도 녹색이 되기 때문이다.
"""

from src.features.composer.executive_status_constants import (
    EXECUTIVE_STATUS_OUTDATED,
    EXECUTIVE_STATUS_REASON_TEXTS,
)
from src.features.composer.executive_status_guard import executive_status_problem
from src.shared.report_quality.review_diagnostic_constants import REVIEW_SCOPE_ITEMS


#: 실제 사업보고서 정정(2026-03-16) 64~73행 — 정석목 해임 대상자 현황. 공백만
#: 줄바꿈 대신 넣었을 뿐 낱말은 원문 그대로다.
DART_DISMISSAL_EXCERPT = (
    "해임 정석목 남 1966년 06월 해당없음 삼성SDS 인사팀장 겸 안전환경센터장 "
    "(부사장)삼성SDS 인사팀장 겸 사회공헌단장 (전무)삼성SDS 인사팀장 (상무)"
    "삼성SDS 솔루션사업부문 지원팀장 (상무)삼성SDS 인사팀 인사지원그룹장 (상무) "
    "2026년 03월 18일 계열회사 임원(당사 임원)"
)

#: 위와 같은 공시의 같은 네 조각(직함 맥락·이름·이탈 표지·날짜)만 남기고 경력
#: 나열을 뺀 발췌 — 원문에서 이름과 날짜 사이가 200자 넘게 떨어져 있어 창(40자)
#: 안에서 날짜를 못 찾는다. 날짜 문턱 자체를 시험하려고 같은 낱말들을 더
#: 가깝게 모았다(낱말은 모두 원문에 실제로 있다. 새로 지어낸 말은 없다).
DART_DISMISSAL_EXCERPT_COMPACT = "해임 정석목 2026년 03월 18일 계열회사 임원(당사 임원)"

#: 실제 사업보고서 499~505행 — 2025-03-19 정기주총에서 김주호가 사외이사로
#: «신규 선임»됐다는 기록뿐이다. 이탈 표지가 전혀 없다.
DART_CURRENT_OFFICER_EXCERPT = (
    "2025년 03월 19일 정기주총 사내이사 조송기사외이사 김주호 - "
    "사내이사 윤중근사외이사 이찬"
)

#: 실제 본문(본문덤프.txt 106행)의 보도 인용 문장. edu.donga.com 2025-11-18 기사를
#: 그대로 옮긴 문장이며, 이 가드가 막아야 하는 실제 결함(C6) 그 문장이다.
REAL_CANDIDATE_SENTENCE = (
    "2025-11-18 edu.donga.com 보도에 따르면, 연세대학교 AI혁신연구원"
    "(원장 윤동섭)과 IT교육 전문기업 멀티캠퍼스(대표이사 정석목)가 AI 기반 "
    "차세대 교육 혁신 및 에듀테크 생태계 확산을 위한 전략적 협약을 체결했다."
)


def guard(text, sources, cells=None, baseline_date=None):
    return executive_status_problem(text, sources, cells, baseline_date=baseline_date)


# ── 실제 조각 양성/음성 (팀장 요청 최소 시험) ──────────────────────────────
def test_real_multicampus_dismissal_fragment_is_rejected():
    """실제 결함 재현: 해임 대상자 공시가 «같은 인용 조각»이면 잡는다.

    ⚠️ 실제 사고(C6)에서는 이 공시 조각이 그 뉴스 문장의 인용에 «없었다»
    (근본 원인은 조각이 애초에 다른 장으로 갈렸거나 수집되지 않은 것 — 조사
    보고서 참고). 이 시험은 «조각이 같이 있었다면 이 가드가 잡는가»만 본다.
    """

    sources = {"210": REAL_CANDIDATE_SENTENCE, "dart-officer": DART_DISMISSAL_EXCERPT}
    assert guard(REAL_CANDIDATE_SENTENCE, sources) == EXECUTIVE_STATUS_OUTDATED


def test_real_multicampus_current_officer_sentence_is_kept():
    """현직 재직자를 소개하는 정상 문장은 막지 않는다(김주호, 2025-03 신규 선임)."""

    candidate = "IT교육 전문기업 멀티캠퍼스(사외이사 김주호)는 이사회에 참여한다."
    sources = {"gov-table": DART_CURRENT_OFFICER_EXCERPT}
    assert guard(candidate, sources) == ""


# ── 기준일 문턱 ──────────────────────────────────────────────────────────
def test_departure_before_baseline_is_rejected():
    sources = {"dart-officer": DART_DISMISSAL_EXCERPT_COMPACT}
    assert guard(
        REAL_CANDIDATE_SENTENCE, sources, baseline_date="2026-09-10",
    ) == EXECUTIVE_STATUS_OUTDATED


def test_departure_scheduled_after_baseline_is_not_yet_effective():
    """해임 예정일이 «기준일보다 뒤»면 아직 발효되지 않은 예정이라 걸지 않는다."""

    sources = {"dart-officer": DART_DISMISSAL_EXCERPT_COMPACT}
    assert guard(REAL_CANDIDATE_SENTENCE, sources, baseline_date="2026-02-01") == ""


def test_missing_date_near_marker_falls_back_to_marker_only():
    """날짜가 «아예 없으면» 표지만으로 판정한다(닫힌 날짜 꼴 밖인 경우 포함)."""

    sources = {"dart-officer": "해임 정석목 남 해당없음 계열회사 임원(당사 임원)"}
    assert guard(
        REAL_CANDIDATE_SENTENCE, sources, baseline_date="2026-09-10",
    ) == EXECUTIVE_STATUS_OUTDATED
    # 「올해 3월」처럼 닫힌 숫자 꼴이 아닌 표기도 «날짜 없음»으로 본다.
    assert guard(
        REAL_CANDIDATE_SENTENCE,
        {"dart-officer": "해임 정석목 남 올해 3월 계열회사 임원(당사 임원)"},
        baseline_date="2026-09-10",
    ) == EXECUTIVE_STATUS_OUTDATED


def test_departure_date_is_found_across_a_real_table_row():
    """★ 예정 이탈 면제가 «실제 표 거리»에서도 작동한다.

    표지는 이름 바로 옆에 붙지만 날짜는 그 행의 끝에 있다(실측 129자).
    표지와 같은 창(40자)으로 날짜를 찾으면 못 찾고 표지만 믿게 되어, 아직
    오지 않은 예정 이탈까지 거절한다.
    """

    sources = {"dart-officer": DART_DISMISSAL_EXCERPT}
    # 해임 예정일 2026-03-18 «앞» 기준일 — 아직 발효되지 않았다.
    assert guard(REAL_CANDIDATE_SENTENCE, sources, baseline_date="2026-02-01") == ""
    # 같은 조각, 기준일만 뒤로 — 그때는 그대로 거절한다.
    assert guard(
        REAL_CANDIDATE_SENTENCE, sources, baseline_date="2026-09-10",
    ) == EXECUTIVE_STATUS_OUTDATED


def test_multiple_dates_in_the_window_fall_back_to_marker_only():
    """넓힌 날짜 창에 다른 사람의 날짜가 섞이면 면제하지 않는다(fail-closed)."""

    two_people = (
        "해임 정석목 남 1966년 06월 계열회사 임원 2026년 03월 18일. "
        "해임 김철수 남 1970년 02월 계열회사 임원 2027년 05월 20일."
    )
    assert guard(
        REAL_CANDIDATE_SENTENCE, {"s": two_people}, baseline_date="2026-02-01",
    ) == EXECUTIVE_STATUS_OUTDATED


# ── 오탐 회귀 (독립 검토 F2) ─────────────────────────────────────────────
#: 실제 DART 「임원 현황」 표의 열 이름 순서 그대로. 해임·사임·퇴임이 한 번도
#: 없는 «깨끗한» 표이고, 임기만료일 값(2028-03-20)은 기준일보다 뒤다.
DART_CLEAN_OFFICER_TABLE = (
    "성명 성별 출생년월 직위 등기임원여부 상근여부 담당업무 주요경력 "
    "소유주식수 최대주주와의관계 재직기간 임기만료일 "
    "김하늘 남 1971년 04월 대표이사 사내이사 상근 경영총괄 "
    "서울대학교 경영학과 20년 2028년 03월 20일"
)

#: 같은 표에서 임기만료일 «값»이 「-」인 행(미등기·비상근 임원에 실제로 흔하다).
#: 이 모양에서는 날짜 창을 아무리 넓혀도 날짜가 없으므로, 열 이름 오탐을 막는
#: 것은 오직 「임기만료(?!일)」 좁힘 하나뿐이다 — 두 수정이 겹치지 않는 자리다.
DART_CLEAN_OFFICER_TABLE_WITHOUT_DATE = (
    "성명 성별 출생년월 직위 등기임원여부 상근여부 담당업무 재직기간 임기만료일 "
    "김하늘 남 1971년 04월 대표이사 사내이사 상근 경영총괄 20년 -"
)

#: 실제 「다.등기임원 선임 후보자 및 해임 대상자 현황」 표의 제목 대목.
DART_APPOINTMENT_TABLE = (
    "다. 등기임원 선임 후보자 및 해임 대상자 현황 "
    "구분 성명 성별 출생년월 최대주주와의 관계 "
    "선임 김하늘 남 1971년 04월 해당없음"
)


def test_clean_officer_table_column_name_does_not_reject_a_sitting_officer():
    """★ 「임기만료일」은 열 «이름»이다 — 사람의 이탈 기록이 아니다.

    이 표에는 해임·사임·퇴임이 한 번도 없다. 그런데 「임기만료」를 문자열
    포함으로 찾으면 열 이름 하나 때문에 재직 중인 대표이사 문장이 거절된다.
    """

    candidate = "2025년 12월 31일 기준 대표이사 김하늘이 회사를 총괄하고 있다."
    assert guard(
        candidate, {"dart-officer": DART_CLEAN_OFFICER_TABLE},
        baseline_date="2026-09-10",
    ) == ""
    # ★ 임기만료일 «값»이 「-」인 행 — 날짜가 아예 없어 날짜 창 확대로는 구제되지
    #   않는다. 이 줄이 초록인 이유는 오직 「임기만료(?!일)」 좁힘 때문이다.
    assert guard(
        candidate, {"dart-officer": DART_CLEAN_OFFICER_TABLE_WITHOUT_DATE},
        baseline_date="2026-09-10",
    ) == ""


def test_expired_term_written_as_a_real_departure_is_still_rejected():
    """좁히되 없애지 않는다 — 서술로 쓰인 「임기만료」는 그대로 잡는다."""

    source = "김하늘 대표이사는 2025년 03월 20일 임기만료로 물러났다."
    candidate = "대표이사 김하늘이 회사를 총괄하고 있다."
    assert guard(
        candidate, {"s": source}, baseline_date="2026-09-10",
    ) == EXECUTIVE_STATUS_OUTDATED


def test_common_nouns_beside_a_title_are_not_taken_as_names():
    """★ 「선임」·「보고서」·「비중」은 사람 이름이 아니다.

    실제로 「선임」이 이름으로 잡혀, 「등기임원 선임 후보자 및 해임 대상자
    현황」 표를 인용한 정상 문장이 표 안의 「해임」과 묶여 거절됐다.
    """

    from src.features.composer.executive_status_guard import _candidate_names

    assert _candidate_names("신임 대표이사 선임이 예정돼 있다.") == ()
    assert _candidate_names("감사보고서의 의견을 공시했다.") == ()
    assert _candidate_names("사외이사 비중을 높였다.") == ()
    # 사람 이름은 그대로 잡힌다 — 위 좁힘이 검사를 꺼 버린 것이 아니다.
    assert "정석목" in _candidate_names("멀티캠퍼스(대표이사 정석목)가 협약했다.")
    assert "김하늘" in _candidate_names("대표이사 김하늘이 총괄한다.")


def test_appointment_table_sentence_is_kept():
    """실측 오탐 그대로 — 선임 안건 문장이 같은 표의 「해임」과 묶이지 않는다."""

    candidate = "2026년 3월 주주총회에서 신임 대표이사 선임이 예정돼 있다."
    assert guard(
        candidate, {"dart-officer": DART_APPOINTMENT_TABLE},
        baseline_date="2026-09-10",
    ) == ""


# ── 발동 경계 ────────────────────────────────────────────────────────────
def test_no_title_mention_is_untouched():
    assert guard("회사는 우수한 제품을 만든다.", {"s": "아무 관련 없는 원문."}) == ""


def test_own_citation_boundary_uncited_dismissal_is_not_seen():
    """다른 후보가 인용한 조각(이 후보는 인용하지 않음)의 해임 기록은 빌리지 않는다."""

    sources = {"210": REAL_CANDIDATE_SENTENCE}  # 해임 조각을 일부러 빼둔다.
    assert guard(REAL_CANDIDATE_SENTENCE, sources) == ""


def test_reappointment_marker_anywhere_in_source_withholds_judgment():
    """같은 조각 안에 재선임 기록도 있으면 이 가드는 판단을 보류한다(합성 예)."""

    source = (
        "해임 김철수 2024년 03월 01일 임시주주총회. "
        "재선임 김철수 2024년 09월 20일 임시주주총회에서 사내이사로 다시 선임됨."
    )
    candidate = "회사(대표이사 김철수)는 신규 투자를 발표했다."
    assert guard(candidate, {"s": source}) == ""


def test_name_after_title_word_order_is_also_detected():
    """«이름 직함» 어순도 잡는다(도식 칸·짧은 소개문에 흔한 꼴)."""

    source = "해임 정석목 2026년 03월 18일 계열회사 임원(당사 임원)"
    assert guard("정석목 대표이사는 협약에 서명했다.", {"s": source}) == EXECUTIVE_STATUS_OUTDATED


def test_cells_candidate_is_checked_like_prose():
    """도식 후보는 칸을 이어붙여 같은 방식으로 본다."""

    cells = ["회사개요", "대표이사 정석목"]
    sources = {"dart-officer": DART_DISMISSAL_EXCERPT}
    assert guard("", sources, cells=cells) == EXECUTIVE_STATUS_OUTDATED


# ── 안전 입력 ────────────────────────────────────────────────────────────
def test_empty_inputs_never_crash():
    assert guard("", {}) == ""
    assert guard("대표이사 정석목", {}) == ""
    assert guard("대표이사 정석목", {"s": ""}) == ""


def test_non_string_source_values_are_ignored_not_crashed():
    sources = {"s": None, "t": 12345, "u": DART_DISMISSAL_EXCERPT}
    assert guard(REAL_CANDIDATE_SENTENCE, sources) == EXECUTIVE_STATUS_OUTDATED


# ── 계약 ────────────────────────────────────────────────────────────────
def test_reason_code_is_registered_in_the_diagnostic_contract():
    for code in EXECUTIVE_STATUS_REASON_TEXTS:
        assert code in REVIEW_SCOPE_ITEMS, code
        assert EXECUTIVE_STATUS_REASON_TEXTS[code].strip()
