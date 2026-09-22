"""검증된 작성 공급자 경계의 동시 실행 계약."""

from typing import Final


# 실제 장 스케줄러의 활성화 여부는 composer의 환경 스위치가 결정한다.
WRITER_MAX_PARALLEL_CALLS: Final[int] = 3
WRITER_SEQUENTIAL_CALLS: Final[int] = 1
WRITER_PARALLEL_CALLS_ENV: Final[str] = "REPORT_WRITER_MAX_PARALLEL_CALLS"
DEFAULT_PROVIDER_STAGE: Final[str] = "unspecified"
