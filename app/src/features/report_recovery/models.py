"""회복 feature가 소유하는 승인과 불변 결정."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from src.features.report_recovery.constants import (
    MAX_SUPPLEMENT_SECTIONS,
    MAX_TOTAL_AI_CALLS,
    PRIMARY_REVIEW_CALLS,
    PRIMARY_WRITER_CALLS,
    SUPPLEMENT_CALLS_PER_SECTION,
    SUPPLEMENT_REVIEW_CALLS,
)
from src.shared.generation_validation_receipt import (
    GenerationValidationReceipt,
    ValidationRound,
    canonical_sha256,
    require_sha256,
)


class RecoveryAction(str, Enum):
    """오케스트레이터가 다음에 할 수 있는 닫힌 행동."""

    STOP_NO_CHARGE = "STOP_NO_CHARGE"
    RUN_PRIMARY = "RUN_PRIMARY"
    RUN_SUPPLEMENTS = "RUN_SUPPLEMENTS"
    RELEASE_COMPLETE = "RELEASE_COMPLETE"


@dataclass(frozen=True)
class SupplementAuthorization:
    """기본 평가가 허용한 정확한 장과 후보에만 유효한 한 번짜리 승인."""

    company_id: str
    base_candidate_sha256: str
    base_receipt_sha256: str
    section_ids: tuple[str, ...]
    authorization_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        company_id = str(self.company_id).strip()
        if not company_id:
            raise ValueError("보충 승인에는 회사 식별자가 필요합니다")
        base_candidate_sha256 = require_sha256(
            self.base_candidate_sha256,
            label="기본 후보 지문",
        )
        base_receipt_sha256 = require_sha256(
            self.base_receipt_sha256,
            label="기본 검증 영수증 지문",
        )
        section_ids = tuple(str(item).strip() for item in self.section_ids)
        if not 1 <= len(section_ids) <= MAX_SUPPLEMENT_SECTIONS:
            raise ValueError("보충 승인은 장 1~2개만 담을 수 있습니다")
        if any(not item for item in section_ids):
            raise ValueError("보충 승인 장 식별자는 비어 있을 수 없습니다")
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("같은 장을 두 번 승인할 수 없습니다")
        authorization_sha256 = canonical_sha256(
            {
                "version": 1,
                "company_id": company_id,
                "base_candidate_sha256": base_candidate_sha256,
                "base_receipt_sha256": base_receipt_sha256,
                "section_ids": list(section_ids),
            }
        )
        object.__setattr__(self, "company_id", company_id)
        object.__setattr__(self, "base_candidate_sha256", base_candidate_sha256)
        object.__setattr__(self, "base_receipt_sha256", base_receipt_sha256)
        object.__setattr__(self, "section_ids", section_ids)
        object.__setattr__(self, "authorization_sha256", authorization_sha256)


@dataclass(frozen=True)
class RecoveryDecision:
    """자료·품질 상태에서 파생된 공개·차감·호출 결정."""

    action: RecoveryAction
    reason_code: str
    observed_total_ai_calls: int = 0
    authorized_additional_ai_calls: int = 0
    supplement_authorization: SupplementAuthorization | None = None
    publish_allowed: bool = False
    charge_allowed: bool = False

    def __post_init__(self) -> None:
        if not self.reason_code.strip():
            raise ValueError("회복 결정에는 기계 사유 코드가 필요합니다")
        if (
            self.observed_total_ai_calls < 0
            or self.authorized_additional_ai_calls < 0
        ):
            raise ValueError("AI 호출 수는 음수가 될 수 없습니다")
        if self.projected_total_ai_calls > MAX_TOTAL_AI_CALLS:
            raise ValueError("보고서 AI 호출 상한을 넘는 결정을 만들 수 없습니다")
        if self.publish_allowed != (
            self.action is RecoveryAction.RELEASE_COMPLETE
        ):
            raise ValueError("완성 공개 행동만 공개를 허용할 수 있습니다")
        if self.charge_allowed != self.publish_allowed:
            raise ValueError("완성 공개가 아닌 결과는 정상 차감을 허용할 수 없습니다")
        if self.action is RecoveryAction.RUN_SUPPLEMENTS:
            if self.supplement_authorization is None:
                raise ValueError("보충 행동에는 결속된 승인이 필요합니다")
            if self.observed_total_ai_calls != (
                PRIMARY_WRITER_CALLS + PRIMARY_REVIEW_CALLS
            ):
                raise ValueError("보충 승인은 기본 9회 작성·1회 검수 뒤에만 가능합니다")
            expected = (
                len(self.supplement_authorization.section_ids)
                * SUPPLEMENT_CALLS_PER_SECTION
                + SUPPLEMENT_REVIEW_CALLS
            )
            if self.authorized_additional_ai_calls != expected:
                raise ValueError("보충 승인과 추가 AI 호출 수가 다릅니다")
        elif self.supplement_authorization is not None:
            raise ValueError("보충 행동이 아닌 결정에는 보충 승인을 넣을 수 없습니다")
        if self.action is RecoveryAction.RUN_PRIMARY:
            if self.observed_total_ai_calls != 0:
                raise ValueError("기본 생성 전에는 관측된 AI 호출이 없어야 합니다")
            if self.authorized_additional_ai_calls != (
                PRIMARY_WRITER_CALLS + PRIMARY_REVIEW_CALLS
            ):
                raise ValueError("기본 생성은 9회 작성·1회 검수만 승인할 수 있습니다")
        elif self.action is not RecoveryAction.RUN_SUPPLEMENTS:
            if self.authorized_additional_ai_calls:
                raise ValueError("실행 행동이 아니면 추가 AI 호출을 승인할 수 없습니다")

    @property
    def supplement_section_ids(self) -> tuple[str, ...]:
        if self.supplement_authorization is None:
            return ()
        return self.supplement_authorization.section_ids

    @property
    def projected_total_ai_calls(self) -> int:
        """호환 이름. 실제 관측치와 새로 승인한 상한의 합이다."""

        return self.observed_total_ai_calls + self.authorized_additional_ai_calls


__all__ = [
    "GenerationValidationReceipt",
    "RecoveryAction",
    "RecoveryDecision",
    "SupplementAuthorization",
    "ValidationRound",
]
