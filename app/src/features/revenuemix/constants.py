"""매출 구성 비중을 뜯는 규칙.

★ 왜 만드나 — 사용자가 증권사 리포트 11건에서 고른 항목 ①.
  **원문을 끝까지 읽은 11건이 «전부» 매출 구성을 실었다.** 유일한 만장일치다.

  지금 우리 1번 칸은 「음반·공연·MD 등으로 구분하여 관리한다」는 문장뿐이라
  **「그래서 이 회사는 뭘로 먹고사나」에 답을 못 한다.**

★★ **지어낼 자리가 없다** — 사업보고서가 «비중을 이미 계산해 놓았다».
  실측 (하이브 2025 사업보고서, 직접 확인) —

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
    REVENUE_AXIS_PRODUCT,
    REVENUE_AXIS_REGION,
    REVENUE_CAPTION_BY_AXIS,
    REVENUE_HEADERS,
    REVENUE_HEADS_BY_AXIS,
    REVENUE_MAX_ROWS,
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

#: 표 밑에 붙이는 말 — **프로그램이 붙인다.** AI가 아니다.
FOOTNOTE: Final[str] = "공시에 적힌 비중을 그대로 옮긴 것입니다 (계산하지 않았습니다)"


# ══════════════════════════════════════════════════════════════════════
# v2 — 표 «모양»으로 찾기 (스위치 ``REVENUE_TABLE_V2`` 가 켜졌을 때만)
# ══════════════════════════════════════════════════════════════════════
#
# ★ 왜 바꾸나 (사용자 결정 2026-09-05) — 표제 목록으로 찾는 v1은 검사판 5곳
#   중 하이브 한 곳에서만 표를 만든다(0단계 실측 ``stage0_data_map.md`` B절).
#   표제를 늘려도 4곳 중 3곳은 여전히 실패한다: 삼성전자는 표제와 표 사이에
#   설명 문단 340자가 끼고 음수가 ``△``이며, 현대카드는 열 이름이 「구성비」에
#   값에 ``%``가 없고, 진영은 열 이름이 「비율」이다.
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
#:   실측 최대 간격은 하이브의 10자(「 매 출 액 」)다.
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
#: ⚠️ 진영 매출실적처럼 내수·수출 2단 중첩 표는 행이 길다. 짧으면 합계를
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
#: ⚠️ 「연결/별도」 표기는 머리말이 아니라 그 앞 문장에 있다(실측: 진영
#:   「(3) 당기와 전기 중 «연결회사»의 지역별 매출액은 다음과 같습니다」).
V2_SCORE_LOOKBACK: Final[int] = 200

#: 같은 축에서 후보가 여럿일 때 주는 가산점.
#: 표제가 있으면 +2, 「연결」 기준이 적혀 있으면 +1, 「별도」면 -1.
V2_SCORE_KNOWN_HEAD: Final[int] = 2
V2_SCORE_CONSOLIDATED: Final[int] = 1
V2_SCORE_SEPARATE: Final[int] = -1
