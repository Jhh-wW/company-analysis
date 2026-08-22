"""출처 목록·附(참고 숫자)·날짜 경고에 쓰는 값.

★ 규칙 — 이 값들을 코드 여기저기에 문자열·숫자로 박지 않는다.
   기획서(`기획서.ver2/확정/`)를 고치면 여기도 같이 고친다.

정본:
  - 확정/07_출력/2_규칙/01_배치와근거표기.md       (출처 목록 형식)
  - 확정/05_생성/2_규칙/01_출력틀.md §附의 한계    (참고 숫자)
  - 확정/05_생성/2_규칙/03_프로그램이붙이는것.md §3 (빈칸 사유 — S6)
  - 확정/05_생성/3_기준/01_성공기준.md (W6)         (4-3 날짜 경고)
  - 확정/03_수집/2_규칙/02_수집범위와신선도.md §2·3 (신선도 상한)
"""

from __future__ import annotations

from typing import Final

# ── 빈칸 사유 문구 (S6) ─────────────────────────────────
# ⚠️ AI가 아니라 프로그램이 붙인다. 원인마다 정해진 문구 «셋만» 쓴다.
#   ❌와 ⚠️를 섞으면 오거부다 — 사용자가 그 회사를 포기해버린다.

#: 회사에 자료가 없다 (❌) — 수집은 성공했는데 자료 자체가 없다.
EMPTY_REASON_NO_DATA: Final[str] = "이 회사의 공개 자료에 해당 내용이 없습니다"

#: 우리가 못 가져왔다 (⚠️) — 우리 쪽 수집 실패다.
#: ★ 기획서 예시 문구는 「홈페이지에 접속하지 못해...」이지만, 附의 소스는
#:   홈페이지가 아니라 전자공시 API다. 같은 원인 범주(우리 쪽 실패)를
#:   소스에 맞게 옮겨 썼다 — 팀장 확인 필요 (최종 보고 §5 참고).
EMPTY_REASON_FETCH_FAILED: Final[str] = "자료를 가져오지 못해 확인하지 못했습니다"

#: 구조적으로 없다 — 비상장은 애초에 이 항목을 공시할 의무가 없다.
EMPTY_REASON_STRUCTURAL: Final[str] = "비상장 회사는 이 항목을 공시할 의무가 없습니다"

# ── 附(참고 숫자) ────────────────────────────────────────
# 정본: 확정/05_생성/2_규칙/01_출력틀.md §附의 한계
# ★ 한계 문구는 «수치와 항상 같이 출력»해야 하므로 caption에 못박아 둔다.
EMP_STATUS_CAPTION: Final[str] = (
    "전자공시 사업보고서 임원 및 직원 현황 (참고용) — "
    "1인평균급여액은 임원 제외 전 직원 평균이며 신입 초봉이 아닙니다"
)
EMP_STATUS_HEADERS: Final[tuple[str, ...]] = ("구분", "1인평균급여액", "평균근속연수")
EMP_STATUS_CITE: Final[str] = "전자공시 사업보고서 (임원 및 직원 등의 현황)"

#: DART 응답이 정상일 때의 status 값 (다른 DART API와 공통).
EMP_STATUS_OK: Final[str] = "000"

# ── 출처 목록 ────────────────────────────────────────────
# 정본: 확정/07_출력/2_규칙/01_배치와근거표기.md
SOURCES_HEADER: Final[str] = "[출처]"

# ── 날짜 경고 (W6 · 4-3 전용) ────────────────────────────
# 정본: 확정/05_생성/3_기준/01_성공기준.md (W6)
#: 경고를 붙이는 칸. 기획서에 4-3(앞으로 어디로 가려 하나) 전용으로 못박혀 있다.
DIRECTION_CELL: Final[str] = "4-3"

#: ★ 문구를 그대로 옮긴 것 — 원문(확정/05_생성/2_규칙/03_프로그램이붙이는것.md)과 한 글자도
#:   다르지 않아야 한다. 여기서 고치면 화면 문구도 바뀐다.
DIRECTION_WARNING_LINES: Final[tuple[str, str]] = (
    "⚠️ 이 시점 이후 방향이 바뀌었을 수 있습니다.",
    "   면접 전 최근 뉴스를 한 번 더 확인하세요.",
)

# ── 신선도 상한 (년) ─────────────────────────────────────
# 정본: 확정/03_수집/2_규칙/02_수집범위와신선도.md §2
# ★ 지금은 4-3 경고를 기간과 무관하게 «항상» 붙인다 (아래 freshness.py 참고).
#   이 상한값은 03 수집 단계의 신선도 판정(O9)이나 08 관측 지표처럼, 날짜로
#   신선도를 따져야 하는 다른 곳에서 바로 쓸 수 있도록 미리 옮겨 둔 것이다.
DIRECTION_STALE_YEARS: Final[int] = 3
NEWS_STALE_YEARS: Final[int] = 3
PLAN_MAX_YEARS: Final[int] = 5


#: 평균근속연수에 이미 단위가 붙어 있는지 알아보는 말.
#: 전자공시가 회사마다 「7년 3개월」 또는 「3.7」처럼 다른 모양으로 준다.
TENURE_UNIT_HINTS: Final[tuple[str, ...]] = ("년", "개월", "月", "yr", "year")

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
