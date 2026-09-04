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
    REVENUE_ROW_RE,
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
