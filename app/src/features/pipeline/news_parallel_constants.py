"""검증된 운영 뉴스 본문 콜백의 병렬 실행 경계."""

from typing import Final

NEWS_BODY_CONCURRENCY_ENV: Final[str] = "NEWS_BODY_FETCH_CONCURRENCY"
NEWS_BODY_CONCURRENCY_DEFAULT: Final[int] = 2
NEWS_BODY_CONCURRENCY_MAX: Final[int] = 3
NEWS_BODY_SEQUENTIAL: Final[int] = 1
