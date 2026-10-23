"""계획 발언과 현재 실행 상태를 구분하는 공개 안내."""

from typing import Final

FUTURE_SECTION_ID: Final[str] = "future_strategy"
STATED_PLAN_SLOT: Final[str] = "future_strategy:stated_plan"
PLAN_SCOPE_MARKERS: Final[tuple[str, ...]] = (
    "현재 이행·유지 상태 미확인",
    "해당 발언만으로 현재 이행·유지 상태가 확인되는 것은 아닙니다",
)
PLAN_SCOPE_NOTICE: Final[str] = (
    "계획 발언의 확인 범위: 발표 당시의 의사이며, "
    "해당 발언만으로 현재 이행·유지 상태가 확인되는 것은 아닙니다."
)
