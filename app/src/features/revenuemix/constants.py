"""매출 구성 비중을 뜯는 규칙.

★ 왜 만드나 — 사용자가 증권사 리포트 11건에서 고른 항목 ①.
  **원문을 끝까지 읽은 11건이 «전부» 매출 구성을 실었다.** 유일한 만장일치다.

  지금 우리 1번 칸은 「음반·공연·MD 등으로 구분하여 관리한다」는 문장뿐이라
  **「그래서 이 회사는 뭘로 먹고사나」에 답을 못 한다.**

★★ **지어낼 자리가 없다** — 사업보고서가 «비중을 이미 계산해 놓았다».
  실측 (상장 엔터사 2025 사업보고서, 직접 확인) —

    구 분   품 목            2025년 제21기(당기)
                            매 출 액      비 중
    음반/음원  음반, 음원 등     772,960   29.17%
    공연      콘서트, 팬미팅 등   763,949   28.83%
    MD 및 라이선싱 …           570,571   21.53%
    합계                    2,649,870  100.00%

  우리가 하는 일은 **베껴 오는 것뿐**이다. 더하지도 나누지도 않는다.
  ⚠️ 비중을 «우리가» 계산하면 반올림 규칙이 공시와 달라져 합이 100%가 안 맞는다.
     공시가 적어 둔 값을 그대로 쓴다.

★ 덤으로 **지역별 매출**도 같은 자리에 있다 (사용자 후보 ⑧).
  「국내 27.28% · 아시아 40.62% · 북미 23.88%」 — 해외 비중을 한눈에 보여 준다.
"""

from __future__ import annotations

import re
from typing import Final

from src.shared.revenue_table_provenance import (
    REVENUE_AMOUNT_ONLY_CAPTION_BY_AXIS,
    REVENUE_AMOUNT_ONLY_FOOTNOTE,
    REVENUE_AXIS_PRODUCT,
    REVENUE_AXIS_REGION,
    REVENUE_CAPTION_BY_AXIS,
    REVENUE_CHANGE_CAPTION_BY_AXIS,
    REVENUE_HEADERS,
    REVENUE_HEADS_BY_AXIS,
    REVENUE_MAX_ROWS,
    REVENUE_MULTI_YEAR_MAX_PERIODS,
    REVENUE_MULTI_YEAR_SELECTION,
    REVENUE_NAME_NOISE,
    REVENUE_RATIO_HEAD_RE,
    REVENUE_ROW_RE,
    REVENUE_ROW_RE_V2,
)

#: 표를 찾는 표제. ★ 회사마다 표기가 조금씩 다르므로 여러 개 둔다.
PRODUCT_HEADS: Final[tuple[str, ...]] = REVENUE_HEADS_BY_AXIS[
    REVENUE_AXIS_PRODUCT
]

REGION_HEADS: Final[tuple[str, ...]] = REVENUE_HEADS_BY_AXIS[REVENUE_AXIS_REGION]

#: 현재 표의 끝을 정하는 닫힌 표제 목록. 제품·지역 순서가 뒤집히거나 같은
#: 표제가 다시 나와도 다음 occurrence가 현재 표의 경계가 된다.
KNOWN_TABLE_HEADS: Final[tuple[str, ...]] = tuple(
    dict.fromkeys((*PRODUCT_HEADS, *REGION_HEADS))
)

#: 표제를 찾은 뒤 살펴볼 길이(글자).
#: ⚠️ 짧으면 표가 잘리고, 길면 «다음 표»의 행까지 딸려 온다. 실측으로 정했다.
SCAN_CHARS: Final[int] = 2200

#: 한 행 — 「…이름… 772,960 29.17%」.
#: ★ 이름은 «최소한»으로 문다(non-greedy). 앞 행의 꼬리를 먹지 않게 하려는 것이다.
ROW_RE: Final[re.Pattern[str]] = REVENUE_ROW_RE

#: 이 말이 이름에 있으면 «합계 행»이다 — 표에 넣되 맨 끝에 둔다.
TOTAL_WORDS: Final[tuple[str, ...]] = ("합계", "합 계", "총계", "계")

#: 이 말이 이름에 있으면 «중간 소계»다 — 버린다.
#: ★ 왜 버리나 — 소계까지 넣으면 비중을 다 더했을 때 200%가 된다.
SUBTOTAL_WORDS: Final[tuple[str, ...]] = ("소계", "소 계")

#: 표에 넣을 최대 행 수 (합계 제외).
MAX_ROWS: Final[int] = REVENUE_MAX_ROWS

#: 이름에서 지워 버릴 찌꺼기 — 표 머리말이 행 이름에 섞여 들어온다.
NAME_NOISE: Final[tuple[str, ...]] = REVENUE_NAME_NOISE

#: ★ 이름 길이 상한을 두지 않는다 (제품 결정) — 예전에는 26자에서
#:   말줄임(…)으로 «잘랐다». 「MD 및 라이선싱 공식 상품(MD), IP 라이…」처럼
#:   품목명이 중간에 끊겨 무엇을 파는지 못 읽는 사고가 실측됐다.
#:   자르는 대신 화면·PDF 칸이 줄바꿈으로 흘려 받게 한다 — 이름을 «버리지» 않는다.
#:   안전장치는 이미 ROW_RE에 있다: 이름 캡처 그룹이 `{2,40}?`이라 원본 자체가
#:   최대 40자다. NAME_NOISE 제거는 글자를 지우기만 하므로 다듬은 이름은
#:   40자를 넘지 않는다 — 별도 상한이 없어도 무한정 길어지지 않는다.

#: 표 제목.
PRODUCT_CAPTION: Final[str] = REVENUE_CAPTION_BY_AXIS[REVENUE_AXIS_PRODUCT]
REGION_CAPTION: Final[str] = REVENUE_CAPTION_BY_AXIS[REVENUE_AXIS_REGION]

#: 열 이름.
HEADERS: Final[tuple[str, str, str]] = REVENUE_HEADERS

#: 3개년 구성 변화 표 계약. 연도를 더 받더라도 최근 3개만 근거로 삼고,
#: 머리말에 연도가 없으면 단년으로 처리해 변화 표를 만들지 않는다.
MULTI_YEAR_MAX_PERIODS: Final[int] = REVENUE_MULTI_YEAR_MAX_PERIODS
MULTI_YEAR_RATIO_HEADER_FORMAT: Final[str] = "{year} 비중"
MULTI_YEAR_SELECTION: Final[str] = REVENUE_MULTI_YEAR_SELECTION
MULTI_YEAR_CAPTION_BY_AXIS: Final[dict[str, str]] = REVENUE_CHANGE_CAPTION_BY_AXIS

#: 표 밑에 붙이는 말 — **프로그램이 붙인다.** AI가 아니다.
FOOTNOTE: Final[str] = "공시에 적힌 비중을 그대로 옮긴 것입니다 (계산하지 않았습니다)"


# ══════════════════════════════════════════════════════════════════════
# v2 — 표 «모양»으로 찾기 (스위치 ``REVENUE_TABLE_V2`` 가 켜졌을 때만)
# ══════════════════════════════════════════════════════════════════════
#
# ★ 왜 바꾸나 (사용자 결정 2026-09-05) — 표제 목록으로 찾는 v1은 검사판 5곳
#   중 상장 엔터사 한 곳에서만 표를 만든다(0단계 실측 ``stage0_data_map.md`` B절).
#   표제를 늘려도 4곳 중 3곳은 여전히 실패한다: 대형 제조사는 표제와 표 사이에
#   설명 문단 340자가 끼고 음수가 ``△``이며, 카드사는 열 이름이 「구성비」에
#   값에 ``%``가 없고, 소재 제조사는 열 이름이 「비율」이다.
#
# ★ 그래서 v2는 «제목 대신 모양»을 본다. 비중 열 이름(비중·비율·구성비)이
#   붙은 머리말을 찾고, 뒤따르는 「이름 + 금액 + 비중」 행을 모은 뒤
#   **행 금액의 합이 합계 행과 글자 그대로 맞는지** 검산한다. 표제는 축을
#   가리키는 힌트일 뿐이고, 통과 여부를 정하는 것은 검산이다.

#: 비중 열 이름. 「비 중」·「비율」·「구성비」 — 글자 사이가 벌어져도 찾는다.
RATIO_HEAD_RE: Final[re.Pattern[str]] = REVENUE_RATIO_HEAD_RE

#: v2 행 모양(이름 80자·음수 부호·``%`` 선택).
ROW_RE_V2: Final[re.Pattern[str]] = REVENUE_ROW_RE_V2

#: 같은 머리말로 볼 「비중」 열 사이 간격(글자).
#: ⚠️ 3개년 표는 「매 출 액 비 중」이 세 번 반복된다. 이 간격 안에 다음
#:   비중 열이 있으면 같은 머리말의 연속으로 보고 «마지막» 비중까지 자른다.
#:   실측 최대 간격은 상장 엔터사의 10자(「 매 출 액 」)다.
V2_HEADER_RUN_GAP: Final[int] = 40

#: 머리말이 어디서 시작하는지 되짚어 볼 범위(글자).
V2_HEADER_LOOKBACK: Final[int] = 600

#: 머리말 시작 경계. 표 바로 앞에 「(단위 : 백만원)」 1행짜리 표가 따로
#: 실리는 것이 DART 서식의 규칙이라(0단계 D-2) 이것이 가장 좋은 경계다.
#: 없으면 「가.」·「(1)」·「4.」 같은 절 번호를 쓴다.
V2_ZONE_BOUNDARY_RE: Final[re.Pattern[str]] = re.compile(
    r"\(\s*단위"
    r"|(?<=\s)\(\d{1,2}\)\s"
    r"|(?<=\s)[가-힣]\.\s"
    r"|(?<=\s)\d{1,2}\.\s"
)

#: 경계를 못 찾았을 때 머리말로 볼 길이(글자).
V2_FALLBACK_HEADER_CHARS: Final[int] = 200

#: 머리말 뒤에서 행과 합계를 찾을 범위(글자).
#: ⚠️ 소재 제조사 매출실적처럼 내수·수출 2단 중첩 표는 행이 길다. 짧으면 합계를
#:   못 만나 통째로 버려지고, 길면 다음 표를 먹는다. 다음 표 머리말에서
#:   한 번 더 자르므로 이 값은 상한일 뿐이다.
V2_ROW_SCAN_CHARS: Final[int] = 3000

#: 표로 인정할 최소 구성 행 수(합계 제외).
V2_MIN_ROWS: Final[int] = 2

#: 이 말이 머리말·행 이름·합계 이름 어디에도 없으면 매출표가 아니다.
#: ⚠️ 이 관문이 없으면 은행 보고서의 「자금조달실적」처럼 비중 열이 있고
#:   금액 합이 맞는 «매출이 아닌» 표가 매출표로 올라온다(0단계 C-3 8번).
V2_REVENUE_WORDS: Final[tuple[str, ...]] = (
    "매출",
    "영업수익",
    "영업실적",
    "매출실적",
    "수익합계",
)

#: 가산점을 매길 때 머리말 앞으로 더 살펴볼 범위(글자).
#: ⚠️ 「연결/별도」 표기는 머리말이 아니라 그 앞 문장에 있다(실측: 소재 제조사
#:   「(3) 당기와 전기 중 «연결회사»의 지역별 매출액은 다음과 같습니다」).
V2_SCORE_LOOKBACK: Final[int] = 200

#: 같은 축에서 후보가 여럿일 때 주는 가산점.
#: 표제가 있으면 +2, 「연결」 기준이 적혀 있으면 +1, 「별도」면 -1.
V2_SCORE_KNOWN_HEAD: Final[int] = 2
V2_SCORE_CONSOLIDATED: Final[int] = 1
V2_SCORE_SEPARATE: Final[int] = -1


# ══════════════════════════════════════════════════════════════════════
# v3 — 비중 열이 «없는» 표 (스위치 ``REVENUE_TABLE_V2`` 가 켜졌을 때만)
# ══════════════════════════════════════════════════════════════════════
#
# ★ 왜 더 넓히나 (2026-09-07 실측) — 검사판 23건 중 지역별 매출을 «공시한»
#   회사가 15곳인데 표가 나온 것은 6곳뿐이었다. 나머지는 두 가지 모양이다.
#     ① 금액만 세로형 — 「구 분 | 제57기 | 제56기 | 제55기」처럼 비중 열이
#        아예 없다. v2는 비중 열 이름을 찾아 시작하므로 출발조차 못 한다.
#     ② 가로형 — 지역이 «열 머리말»에 있고 금액이 그 아래 한 줄에 이어진다
#        (연결재무제표 주석 「영업부문정보 → 지역에 대한 공시」).
# ★ 검산은 그대로다. 비중이 없으니 「비중 합 100%」는 못 묻지만,
#   **행 금액의 합 == 합계**는 여전히 묻는다. 합계가 없으면 싣지 않는다.
# ⚠️ 비중을 «우리가» 계산해 채우지 않는다 — 계약(맨 위 ``FOOTNOTE``)은 그대로다.
#   비중 열 없이 「구분 · 금액」 두 열로만 싣고 아래 문구를 붙인다.

#: 비중 없는 표에 붙이는 말.
FOOTNOTE_WITHOUT_RATIO: Final[str] = REVENUE_AMOUNT_ONLY_FOOTNOTE

#: 비중 없는 표의 캡션.
AMOUNT_ONLY_CAPTION_BY_AXIS: Final[dict[str, str]] = dict(
    REVENUE_AMOUNT_ONLY_CAPTION_BY_AXIS
)

#: 표를 못 세운 이유 — **닫힌 목록**이다. 화면·로그에 나가는 값이라
#: 자유 문장을 쓰면 같은 이유가 회사마다 다른 말로 흩어진다.
REJECT_ROWS_BELOW_MIN: Final[str] = "rows_below_min"
REJECT_ROWS_OVERFLOW: Final[str] = "rows_overflow"
REJECT_NO_TOTAL_ROW: Final[str] = "no_total_row"
REJECT_SUM_MISMATCH: Final[str] = "sum_mismatch"
REJECT_NO_RATIO_COLUMN: Final[str] = "no_ratio_column"
REJECT_RATIO_SUM_MISMATCH: Final[str] = "ratio_sum_mismatch"
REJECT_NOT_REVENUE: Final[str] = "not_revenue"
REJECT_UNIT_UNKNOWN: Final[str] = "unit_unknown"
REJECT_UNIT_CONFLICT: Final[str] = "unit_conflict"
REJECT_AXIS_UNKNOWN: Final[str] = "axis_unknown"
REJECT_NO_HEADING: Final[str] = "no_heading"
REJECT_HORIZONTAL_NO_TOTAL: Final[str] = "horizontal_no_total"
REJECT_HORIZONTAL_NAMES_UNMATCHED: Final[str] = "horizontal_names_unmatched"
REJECT_DUPLICATE_TABLE: Final[str] = "duplicate_table"
REJECT_PERIODS_MISSING: Final[str] = "periods_missing"
#: 비중 열이 «있는» 표는 이 경로가 건드리지 않는다. 비중 검산에 떨어진 표를
#: 금액만 다시 실으면 「잘못 잘린 표」가 조용히 나간다 — 그건 더 나쁘다.
REJECT_RATIO_COLUMN_PRESENT: Final[str] = "ratio_column_present"

#: 위 코드 전부. 진단에 이 목록 밖의 값이 들어가면 시험이 깨진다.
REJECT_REASON_CODES: Final[tuple[str, ...]] = (
    REJECT_ROWS_BELOW_MIN,
    REJECT_ROWS_OVERFLOW,
    REJECT_NO_TOTAL_ROW,
    REJECT_SUM_MISMATCH,
    REJECT_NO_RATIO_COLUMN,
    REJECT_RATIO_SUM_MISMATCH,
    REJECT_NOT_REVENUE,
    REJECT_UNIT_UNKNOWN,
    REJECT_UNIT_CONFLICT,
    REJECT_AXIS_UNKNOWN,
    REJECT_NO_HEADING,
    REJECT_HORIZONTAL_NO_TOTAL,
    REJECT_HORIZONTAL_NAMES_UNMATCHED,
    REJECT_DUPLICATE_TABLE,
    REJECT_PERIODS_MISSING,
    REJECT_RATIO_COLUMN_PRESENT,
)

#: 기존 v2가 세던 «한국어» 이유를 위 코드로 옮기는 표. 한국어 쪽은 그대로
#: 둔다 — 이미 그 이름을 못 박은 시험이 있고, 기대값을 바꾸지 않기로 했다.
V2_REASON_CODES: Final[dict[str, str]] = {
    "행 부족": REJECT_ROWS_BELOW_MIN,
    "행 넘침": REJECT_ROWS_OVERFLOW,
    "합계 없음": REJECT_NO_TOTAL_ROW,
    "금액 합 불일치": REJECT_SUM_MISMATCH,
    "비중 합 불일치": REJECT_RATIO_SUM_MISMATCH,
    "매출 표현 없음": REJECT_NOT_REVENUE,
    "단위 미확인": REJECT_UNIT_UNKNOWN,
    "단위 충돌": REJECT_UNIT_CONFLICT,
    "축 불명": REJECT_AXIS_UNKNOWN,
    "중복 표": REJECT_DUPLICATE_TABLE,
    "비교 연도 부족": REJECT_PERIODS_MISSING,
    "연도 열 초과": REJECT_PERIODS_MISSING,
    "유효 연도 부족": REJECT_PERIODS_MISSING,
    "연도 검산 실패": REJECT_SUM_MISMATCH,
}

#: 「(단위 : 천원)」 앞으로 되짚어 볼 «표제» 길이(글자).
#: ⚠️ 숫자를 만나면 거기서 멈춘다 — 앞 표의 꼬리를 물면 단위가 둘이 되어
#:   표 전체가 「단위 충돌」로 버려진다(실측: 카카오 주석은 앞 표 금액이 바로 붙는다).
AMOUNT_ONLY_HEADING_LOOKBACK: Final[int] = 80

#: 머리말 뒤에서 행을 찾을 범위(글자). v2와 같은 상한을 쓴다.
AMOUNT_ONLY_ROW_SCAN_CHARS: Final[int] = V2_ROW_SCAN_CHARS

#: 가로형에서 이름이 될 수 있는 최소 열 수(합계 제외).
AMOUNT_ONLY_MIN_COLUMNS: Final[int] = V2_MIN_ROWS

#: 가로형 금액 줄의 «이름표» — 이 말이 금액 바로 앞에 있어야 매출 줄이다.
#: ⚠️ 같은 표에 「비유동자산」 줄이 나란히 있다(실측). 이름표를 안 보면
#:   자산 금액이 매출표로 올라온다.
AMOUNT_ONLY_ROW_LABEL_WORDS: Final[tuple[str, ...]] = (
    "매출액",
    "순매출액",
    "매출",
    "영업수익",
    "수익",
)

#: 표제 되짚기를 멈추는 글자. 숫자(앞 표의 금액·절 번호)와 문장 끝이다.
#: ⚠️ 이 목록이 짧아지면 표 앞 «문장»이 머리말로 딸려 들어와, 매출이 아닌
#:   표가 매출 관문을 통과하거나 축이 뒤집힌다(실측 2건, 2026-09-07).
AMOUNT_ONLY_HEADING_STOPS: Final[frozenset[str]] = frozenset(
    "0123456789.!?;:※"
)

#: 첫 행 이름 앞에 붙어 오는 «기간 열 이름의 꼬리». 이름 캡처는 숫자를 물 수
#: 없어서 「제57기」의 「기」만 남는다 — 그대로 두면 「기 내수 국내」가 된다.
#: ⚠️ 닫힌 목록이다. 「전기」·「당기」처럼 사업부문 이름이 될 수 있는 말은
#:   일부러 넣지 않았다 — 진짜 이름을 지우면 그게 더 큰 사고다.
AMOUNT_ONLY_PERIOD_TAIL_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "기",
        "년",
        "월",
        "일",
        "분기",
        "반기",
        "개월",
        "누적",
        "기말",
        "말",
        "차",
        "기)",
        ")기",
        "전)기",
        "당)기",
        "전전)기",
    }
)

#: 비중 열이 없는 표에서 「이 표가 매출인가」를 묻는 말. v2보다 «좁다».
#: ★★ 왜 좁히나 (실측) — v2 목록의 「영업실적」은 카드사 「취급업무별 영업실적
#:   (취급액 기준)」까지 통과시킨다. 그건 회사가 «번 돈»이 아니라 카드로
#:   결제된 «금액»이다. 비중 열이 없으면 구조로 걸러낼 근거가 한 겹 적으므로
#:   이름 관문을 더 좁게 잡는다.
AMOUNT_ONLY_REVENUE_WORDS: Final[tuple[str, ...]] = (
    "매출",
    "영업수익",
    "매출실적",
    "수익합계",
)

#: 글자 「매출」은 있지만 회사가 번 돈의 구성이 아닌 2열 표제. 금액 전용
#: 경로는 비중이라는 두 번째 구조 검산이 없으므로 이 표들을 매출액으로
#: 바꾸어 부르지 않는다. 문장 전체의 일반 부정어가 아니라 실제 재무 표제의
#: 닫힌 목록만 둬 정상 매출표를 넓게 막지 않는다.
AMOUNT_ONLY_NON_REVENUE_WORDS: Final[tuple[str, ...]] = (
    "매출채권",
    "매출원가",
)
