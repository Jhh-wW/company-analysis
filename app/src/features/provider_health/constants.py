"""provider 건강 상태 기계의 명시적 정책값."""

from __future__ import annotations

from typing import Final


PROVIDER_ANTHROPIC: Final[str] = "anthropic"
PROVIDER_DART: Final[str] = "dart"
PROVIDER_GOOGLE_PLACES: Final[str] = "google_places"

#: 서로 다른 실패 사유를 묶지 않더라도, 연속 실패라면 해당
#: provider만 잠시 연다. 이 임계값은 운영 결과에 맞춰 시험을 통과시키는
#: 값이 아니라, 2회의 독립 관측을 요구하는 시작 정책이다.
FAILURES_TO_OPEN: Final[int] = 2

#: 429의 흔한 1분 창이 지나면 단 한 번의 탐색 호출을 허용한다.
#: 영구 잠금을 금지하는 유한 기본값이며 provider별 실측 후에만 바꾼다.
OPEN_COOLDOWN_SEC: Final[int] = 60

#: Anthropic의 현재 180초 호출 상한보다 긴 탐색 lease. 호출이 아직 진행
#: 중인데 두 번째 탐색을 보내지 않도록 여유를 둠다.
PROBE_LEASE_SEC: Final[int] = 300

REASON_AVAILABLE: Final[str] = "available"
REASON_DEGRADED: Final[str] = "degraded"
REASON_COOLDOWN: Final[str] = "cooldown"
REASON_PROBE_AVAILABLE: Final[str] = "probe_available"
REASON_PROBE_IN_PROGRESS: Final[str] = "probe_in_progress"
