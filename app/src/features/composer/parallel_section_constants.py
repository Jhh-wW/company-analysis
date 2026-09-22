"""장별 작성의 동시 실행과 진단 상한."""

from typing import Final

MAX_PARALLEL_SECTION_CALLS: Final[int] = 3
SERIAL_SECTION_CALLS: Final[int] = 1
MILLISECONDS_PER_SECOND: Final[int] = 1_000
SECTION_THREAD_NAME_PREFIX: Final[str] = "report-section"
