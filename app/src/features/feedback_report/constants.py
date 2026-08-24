"""오류 신고(feedback_report)의 닫힌 값 목록·한도·안내 문구.

★ 값 목록을 닫아 두는 이유 — 신고 화면의 select와 관리자 필터가 같은 목록을
  써야 집계가 어긋나지 않는다. 새 값이 필요하면 여기 한 곳만 고친다.
"""

from __future__ import annotations

from typing import Final


# ══════════════════════════════════════════════════════════
# 신고 ID
# ══════════════════════════════════════════════════════════

#: 신고 ID 모양: ``RPT-YYYYMMDD-일련번호`` (예: RPT-20260824-001).
REPORT_ID_PREFIX: Final[str] = "RPT"

#: 하루 일련번호 최소 자릿수. 999건을 넘으면 자릿수가 자연히 늘어난다.
REPORT_ID_SERIAL_DIGITS: Final[int] = 3

# ══════════════════════════════════════════════════════════
# 신고가 발생한 단계 (닫힌 목록)
# ══════════════════════════════════════════════════════════

STAGE_NO_SEARCH: Final[str] = "검색없음"
STAGE_COMPANY_SELECT: Final[str] = "기업선택"
STAGE_GENERATING: Final[str] = "생성중"
STAGE_REPORT: Final[str] = "보고서"

#: 화면 표시 순서를 겸하는 정본 목록.
REPORT_STAGES: Final[tuple[str, ...]] = (
    STAGE_NO_SEARCH,
    STAGE_COMPANY_SELECT,
    STAGE_GENERATING,
    STAGE_REPORT,
)

# ══════════════════════════════════════════════════════════
# 신고 유형 (닫힌 목록)
# ══════════════════════════════════════════════════════════

CATEGORY_WRONG_INFO: Final[str] = "잘못된 정보"
CATEGORY_SOURCE_ERROR: Final[str] = "출처 오류"
CATEGORY_STALE_BASIS: Final[str] = "오래된 기준일"
CATEGORY_COMPANY_IDENTITY: Final[str] = "기업 식별 오류"
CATEGORY_COMPANY_MISSING: Final[str] = "기업 정보 누락"
CATEGORY_OTHER: Final[str] = "기타"

REPORT_CATEGORIES: Final[tuple[str, ...]] = (
    CATEGORY_WRONG_INFO,
    CATEGORY_SOURCE_ERROR,
    CATEGORY_STALE_BASIS,
    CATEGORY_COMPANY_IDENTITY,
    CATEGORY_COMPANY_MISSING,
    CATEGORY_OTHER,
)

# ══════════════════════════════════════════════════════════
# 처리 상태 (닫힌 목록 + 닫힌 전이)
# ══════════════════════════════════════════════════════════

STATUS_OPEN: Final[str] = "미처리"
STATUS_REVIEWING: Final[str] = "검토중"
STATUS_RESOLVED: Final[str] = "처리완료"
STATUS_REJECTED: Final[str] = "반려"

REPORT_STATUSES: Final[tuple[str, ...]] = (
    STATUS_OPEN,
    STATUS_REVIEWING,
    STATUS_RESOLVED,
    STATUS_REJECTED,
)

#: 허용된 상태 전이. 미처리→검토중→(처리완료|반려)만 인정한다.
#: 건너뛰기(미처리→처리완료)를 막아 «검토 없이 닫힌 신고»가 생기지 않게 한다.
ALLOWED_STATUS_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    STATUS_OPEN: frozenset({STATUS_REVIEWING}),
    STATUS_REVIEWING: frozenset({STATUS_RESOLVED, STATUS_REJECTED}),
    STATUS_RESOLVED: frozenset(),
    STATUS_REJECTED: frozenset(),
}

# ══════════════════════════════════════════════════════════
# 입력 한도
# ══════════════════════════════════════════════════════════

MAX_BODY_CHARS: Final[int] = 2000
MAX_COMPANY_NAME_CHARS: Final[int] = 200
MAX_ITEM_LABEL_CHARS: Final[int] = 200
MAX_REPORT_REF_CHARS: Final[int] = 128
MAX_REF_URL_CHARS: Final[int] = 1000
MAX_ADMIN_NOTE_CHARS: Final[int] = 2000
MAX_REPORTER_KEY_CHARS: Final[int] = 128
MAX_ACTOR_CHARS: Final[int] = 80
MAX_KEYWORD_CHARS: Final[int] = 100

#: 참고 URL에 허용하는 주소 방식. 그 외(javascript: 등)는 저장하지 않는다.
ALLOWED_REF_URL_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})

# ══════════════════════════════════════════════════════════
# 스팸 방어 최소선
# ══════════════════════════════════════════════════════════

#: 같은 신고자 식별자(세션·LINK 해시)가 하루(KST)에 접수할 수 있는 최대 건수.
DAILY_CREATE_LIMIT_PER_REPORTER: Final[int] = 20

# ══════════════════════════════════════════════════════════
# 페이지네이션
# ══════════════════════════════════════════════════════════

DEFAULT_PAGE_SIZE: Final[int] = 20
MAX_PAGE_SIZE: Final[int] = 100

# ══════════════════════════════════════════════════════════
# 화면 안내 문구 (꼭 필요한 것만)
# ══════════════════════════════════════════════════════════

#: 신고 폼 상단 안내.
FORM_GUIDE_NOTICE: Final[str] = (
    "작성하신 내용은 관리자가 검토 후 조치합니다. "
    "개인정보(이름·연락처 등)는 입력하지 마세요."
)

#: 하루 접수 상한에 걸렸을 때 안내.
DAILY_LIMIT_MESSAGE: Final[str] = (
    "오늘 접수 가능한 신고 건수를 모두 사용했습니다. 내일 다시 시도해 주세요."
)
