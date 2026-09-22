"""동일 입력 분석 캐시의 보수적인 프로세스 메모리 경계."""

from typing import Final


ANALYSIS_CACHE_ENV: Final[str] = "NEWS_ANALYSIS_EXACT_CACHE"
# 단계 절감과 전체 비용 반례를 함께 검증했다. 첫 운영 적용은 명시적으로 선택한다.
ANALYSIS_CACHE_DEFAULT: Final[bool] = False
ANALYSIS_CACHE_VERSION: Final[str] = "news-analysis-exact-v1"
ANALYSIS_CACHE_TTL_SECONDS: Final[int] = 300
ANALYSIS_CACHE_MAX_ENTRIES: Final[int] = 128
ANALYSIS_CACHE_MAX_BYTES: Final[int] = 4 * 1024 * 1024
ANALYSIS_CACHE_ENTRY_MAX_BYTES: Final[int] = 64 * 1024
ANALYSIS_CACHE_SOURCE_FIELDS: Final[tuple[str, ...]] = (
    "text", "time_evidence", "subject_evidence",
)
