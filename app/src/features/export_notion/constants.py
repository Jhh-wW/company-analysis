"""노션 전송 기능에서 쓰는 값 — API 주소·버전·블록 제한값·환경변수 이름·
화면(result.html)과 글자가 같아야 하는 문구를 한곳에 모은다.

★ 규칙 — 매직 값을 코드 여기저기에 문자열·숫자로 흩어 쓰지 않는다.
  여기만 고치면 전체가 맞춰진다 (`rules/general.md`).

정본:
  - 확정/07_출력/1_흐름/01_세형태.md          (노션에 보내기 · 페이지 제목 · 실패 처리)
  - 확정/07_출력/2_규칙/01_배치와근거표기.md   (배치 순서 · 빈칸 사유 문구 · 수집 현황 표시)
  - 확정/07_출력/3_기준/01_성공기준.md         (P3 — 화면·워드·노션 내용 일치)
  - Notion API 공식 문서(https://developers.notion.com) — API 주소·버전·블록 개수 제한
"""

from __future__ import annotations

from typing import Final

# ══════════════════════════════════════════════════════════
# 노션 API
# ══════════════════════════════════════════════════════════

NOTION_API_BASE: Final[str] = "https://api.notion.com/v1"
PAGES_PATH: Final[str] = "/pages"
#: `{block_id}`만 채워 쓴다. 페이지 첫 생성 이후 나머지 블록을 나눠 보낼 때 쓴다.
CHILDREN_PATH_TEMPLATE: Final[str] = "/blocks/{block_id}/children"

#: ⚠️ 이 프로젝트가 실제 통합(integration) 계정으로 접속해 확인한 값이 «아니다».
#:   노션 개발자 문서(https://developers.notion.com/reference/versioning) 기준으로
#:   알려진 최신 안정 버전을 적었다 — 실제 연동 전에 한 번 더 확인이 필요하다
#:   (최종 보고 §6 「위험 요소」 참고).
NOTION_VERSION: Final[str] = "2022-06-28"

#: 노션 서버 호출 타임아웃(초). 응답이 없을 때 화면이 무한히 멈추지 않게 한다.
HTTP_TIMEOUT_SEC: Final[int] = 15
#: 페이지/블록 응답 JSON의 선언값과 실제 읽기에 함께 적용하는 상한.
API_RESPONSE_MAX_BYTES: Final[int] = 2 * 1024 * 1024

#: 명시적 429 + 유효한 Retry-After만 재시도한다. 타임아웃·5xx는 원격 적용
#: 여부를 알 수 없으므로 절대 자동 재시도하지 않는다.
MAX_429_RETRIES: Final[int] = 2
MAX_RETRY_AFTER_SEC: Final[float] = 15.0
MAX_TOTAL_RETRY_WAIT_SEC: Final[float] = 30.0

# ══════════════════════════════════════════════════════════
# 환경변수 이름
# ══════════════════════════════════════════════════════════
# ★ 실제 값(토큰·페이지 ID)은 절대 코드에 넣지 않는다. os.environ에서만 읽는다.

#: 노션 통합(integration)이 발급한 비밀 토큰.
ENV_NOTION_TOKEN: Final[str] = "NOTION_TOKEN"
#: 보고서 페이지를 만들 부모 페이지 ID. 코드에 페이지 ID를 박지 않는다.
ENV_NOTION_PARENT_PAGE_ID: Final[str] = "NOTION_PARENT_PAGE_ID"

# ══════════════════════════════════════════════════════════
# 블록 제한값 (Notion API 제약)
# ══════════════════════════════════════════════════════════

#: 한 번의 요청(페이지 생성·children 추가)에 담을 수 있는 최상위 블록 개수 상한.
#: 넘으면 나눠 보낸다 (팀장 지시 §4).
MAX_BLOCKS_PER_REQUEST: Final[int] = 100
#: rich_text 항목 하나(content)에 담을 수 있는 글자 수 상한. 넘으면 나눠 담는다.
MAX_RICH_TEXT_LENGTH: Final[int] = 2000

# ══════════════════════════════════════════════════════════
# 등급 아이콘 — 화면의 🟡/🔴와 맞춘다 (result.html)
# ══════════════════════════════════════════════════════════

GRADE_ICON_PARTIAL: Final[str] = "▫️"
GRADE_ICON_INCOMPLETE: Final[str] = "▫️"

# ══════════════════════════════════════════════════════════
# 화면(result.html)과 한 글자도 다르면 안 되는 문구 (P3 — 형태 간 불일치 0건)
# ⚠️ result.html의 해당 문구가 바뀌면 여기도 같이 고친다.
# ══════════════════════════════════════════════════════════

SOURCES_HEADING: Final[str] = "부록. 출처와 검증 상태"
SOURCES_SUBTITLE: Final[str] = "본문의 번호가 아래 원문을 가리킵니다."
COLLECTION_HEADING: Final[str] = "어디서 가져왔나"
SUMMARY_HEADING: Final[str] = "핵심 요약"

# ══════════════════════════════════════════════════════════
# 노션 표의 «열 이름»
# ══════════════════════════════════════════════════════════
# ★ 이 값들은 보고서 내용이 아니라 표의 머리글이다. 그래서 공개 봉인 블록에
#   들어 있지 않고 여기서 이름을 붙인다 — 칸에 들어가는 «값»은 v2 갈래에서
#   전부 봉인 블록에서 그대로 온다.
# ★ v1·v2 갈래가 «같은 이 값»을 쓴다. 한쪽만 고치면 같은 보고서의 두 갈래가
#   서로 다른 열 이름을 내게 된다.

SUMMARY_TABLE_HEADERS: Final[tuple[str, ...]] = ("#", "짧은 제목", "요약", "관련 장")
CITATION_TABLE_HEADERS: Final[tuple[str, ...]] = (
    "#",
    "자료",
    "기준일·자료 상태",
    "사실 검증",
    "원문 위치",
    "본문 사용 장",
)
#: 표지 실적 띠(``PublicCoverMetricsBlock``)를 표로 낼 때의 열 이름.
COVER_METRICS_TABLE_HEADERS: Final[tuple[str, ...]] = ("지표", "값", "단위")
#: 3개년 변화 요약 띠(``PublicPeriodSummaryBlock``)를 표로 낼 때의 열 이름.
#: 봉인된 10개 필드 중 ``change_kind``·``direction``은 화면 «강조 방식»을 고르는
#: 표식이라 노션에는 대응하는 자리가 없어 싣지 않는다(글자가 아니다).
PERIOD_SUMMARY_TABLE_HEADERS: Final[tuple[str, ...]] = (
    "항목",
    "기준 시점",
    "기준 값",
    "최근 시점",
    "최근 값",
    "단위",
    "변화",
    "비고",
)

#: 요구역량(5번 칸)은 공고 원문 목록이라 다른 칸과 다르게 다룬다 (result.html requirements_block()).
REQUIREMENTS_CELL: Final[str] = "5"
REQUIREMENTS_NOTE: Final[str] = (
    "공고 원문 그대로입니다. 다듬지 않았습니다 — 자소서에 그대로 옮겨 쓰시라고요."
)
REQUIREMENTS_EMPTY_TEXT: Final[str] = (
    "비어 있습니다 — 올려주신 공고에서 요구 조건을 뽑지 못했습니다"
)

#: 빈 칸 문구의 머리말 + 사유가 없을 때 쓰는 기본 사유 (result.html의 기본값).
EMPTY_SECTION_PREFIX: Final[str] = "비어 있습니다 — "
EMPTY_SECTION_FALLBACK: Final[str] = "해당 자료를 찾지 못했습니다"

#: 소스별 수집 현황 상태 표시 (정본 §수집 현황 — ⭕/❌/⚠️ 셋을 섞으면 오거부다).
SOURCE_STATE_LABELS: Final[dict[str, str]] = {
    "ok": "⭕ 찾음",
    "none": "❌ 없음",
    "failed": "⚠️ 못 가져옴",
}
COLLECTION_TABLE_HEADERS: Final[tuple[str, str, str]] = ("소스", "결과", "내용")
