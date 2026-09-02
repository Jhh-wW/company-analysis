"""출처 목록·날짜 경고에 쓰는 값.

★ 규칙 — 이 값들을 코드 여기저기에 문자열·숫자로 박지 않는다.
   기준이 바뀌면 여기도 같이 고친다.

★ 附(참고 숫자 — 1인평균급여액·평균근속연수)는 **걷어냈다.**
  이 보고서는 「지원동기를 합격 퀄리티로 만드는 정보」만 담는다(사용자 지시).
  급여는 그 목적과 무관하고, 지원동기에 쓸 수도 없다.

다루는 범위:
  - 출처 목록 형식
  - 4-3 날짜 경고(W6)
  - 신선도 상한 3년
"""

from __future__ import annotations

from typing import Final

# ── 출처 목록 ────────────────────────────────────────────
SOURCES_HEADER: Final[str] = "[출처]"

# ── 날짜 경고 (4-3 전용) ────────────────────────────
#: 경고를 붙이는 칸. 기획서에 4-3(앞으로 어디로 가려 하나) 전용으로 못박혀 있다.
DIRECTION_CELL: Final[str] = "4-3"

#: ★ 문구를 그대로 옮긴 것 — 원문과 한 글자도
#:   다르지 않아야 한다. 여기서 고치면 화면 문구도 바뀐다.
DIRECTION_WARNING_LINES: Final[tuple[str, str]] = (
    "⚠️ 이 시점 이후 방향이 바뀌었을 수 있습니다.",
    "   면접 전 최근 뉴스를 한 번 더 확인하세요.",
)

# ── 신선도 상한 (년) ─────────────────────────────────────
# ★ 지금은 4-3 경고를 기간과 무관하게 «항상» 붙인다 (아래 freshness.py 참고).
#   이 상한값은 03 수집 단계의 신선도 판정(O9)이나 08 관측 지표처럼, 날짜로
#   신선도를 따져야 하는 다른 곳에서 바로 쓸 수 있도록 미리 옮겨 둔 것이다.
DIRECTION_STALE_YEARS: Final[int] = 3
NEWS_STALE_YEARS: Final[int] = 3
PLAN_MAX_YEARS: Final[int] = 5


# ── 출처 재료 뽑기 (citations.py) ────────────────────────
# 1판 엔진(analysis_engine/tools/run_pilot.py)이 수집 조각에 붙이는 "종류" 문자열.
# ★ 여기서 새로 정의하지만 값 자체는 그쪽·`homepage/constants.py`의 FRAGMENT_KIND와
#   같아야 한다 — 같은 전자공시·홈페이지 수집기의 산출물이기 때문이다.
#   (feature 간 직접 import는 금지이므로 값만 맞춰 각자 갖고 있는다.)
FRAGMENT_KIND_NEWS: Final[str] = "뉴스"
FRAGMENT_KIND_HOMEPAGE: Final[str] = "홈페이지"
FRAGMENT_KIND_OFFICIAL_IR: Final[str] = "공식 IR"

#: 원 기사 도메인을 못 뽑았을 때 1판 엔진이 넣는 자리표시자 (run_pilot.collect_news).
#: 실제 도메인이 아니므로 출처로 그대로 옮기면 지어낸 값이 된다 — 만나면 비운다.
NEWS_UNKNOWN_DOMAIN: Final[str] = "출처미상"

#: 재무 API(fnlttSinglAcnt.json, 주요계정) 조각의 원문 앞머리 (run_pilot.make_fragments).
#: 공시 원문(filing)과는 «다른» DART API 호출이라 그 보고서의 공시일이 반드시
#: 같다는 보장이 없다 — 만나면 공시일을 비우고 출처만 밝힌다.
DART_ACCOUNT_FRAGMENT_PREFIX: Final[str] = "주요계정(DART API):"
DART_ACCOUNT_FRAGMENT_LABEL: Final[str] = "전자공시 주요계정(DART API)"

#: 홈페이지 조각에 실제 읽은 URL이 없을 때(있어선 안 되지만 방어적으로) 쓸 라벨.
HOMEPAGE_FALLBACK_LABEL: Final[str] = "회사 홈페이지"


#: 기타(홈페이지 등) 출처의 날짜 앞에 붙는 말. 공시의 「수집 …」과 «반드시» 달라야 한다.
#: 같으면 저장했다 다시 읽을 때 공시로 잘못 분류된다.
OTHER_DATE_PREFIX: Final[str] = "확인 "
