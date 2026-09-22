"""유료 단계 안에서 허용하는 호출 수와 종료 요청 사건."""

from typing import Final


SEQUENTIAL_ATTEMPT_LIMIT: Final[int] = 1
MAX_PARALLEL_PROVIDER_ATTEMPTS: Final[int] = 3
PHASE_CLOSE_FAILED_REASON: Final[str] = "provider-phase-close-failed"
PHASE_CLOSE_SUCCEEDED_REASON: Final[str] = "provider-phase-close-succeeded"
PROVIDER_BUDGET_WAIT_SECONDS: Final[float] = 0.25
