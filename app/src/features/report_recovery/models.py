"""회복 상태기계가 외부 오케스트레이터에 돌려주는 불변 결정."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from src.features.report_recovery.constants import MAX_TOTAL_AI_CALLS


class RecoveryAction(str, Enum):
    """오케스트레이터가 다음에 할 수 있는 닫힌 행동."""

    STOP_NO_CHARGE = "STOP_NO_CHARGE"
    RUN_PRIMARY = "RUN_PRIMARY"
    RUN_SUPPLEMENTS = "RUN_SUPPLEMENTS"
    RELEASE_COMPLETE = "RELEASE_COMPLETE"


@dataclass(frozen=True)
class RecoveryDecision:
    """자료·품질 상태에서 파생된 공개·차감·호출 결정."""

    action: RecoveryAction
    reason_code: str
    supplement_section_ids: tuple[str, ...] = ()
    projected_total_ai_calls: int = 0
    publish_allowed: bool = False
    charge_allowed: bool = False

    def __post_init__(self) -> None:
        if not self.reason_code.strip():
            raise ValueError("회복 결정에는 기계 사유 코드가 필요합니다")
        if len(self.supplement_section_ids) != len(set(self.supplement_section_ids)):
            raise ValueError("같은 장을 두 번 보충할 수 없습니다")
        if not 0 <= self.projected_total_ai_calls <= MAX_TOTAL_AI_CALLS:
            raise ValueError("보고서 AI 호출 상한을 넘는 결정을 만들 수 없습니다")
        if self.publish_allowed != (
            self.action is RecoveryAction.RELEASE_COMPLETE
        ):
            raise ValueError("완성 공개 행동만 공개를 허용할 수 있습니다")
        if self.charge_allowed != self.publish_allowed:
            raise ValueError("완성 공개가 아닌 결과는 정상 차감을 허용할 수 없습니다")
        if self.action is RecoveryAction.RUN_SUPPLEMENTS:
            if not self.supplement_section_ids:
                raise ValueError("보충 행동에는 대상 장이 필요합니다")
        elif self.supplement_section_ids:
            raise ValueError("보충 행동이 아닌 결정에는 대상 장을 넣을 수 없습니다")
