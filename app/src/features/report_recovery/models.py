"""회복 상태기계가 사용하는 결속된 영수증과 불변 결정."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from src.features.report_recovery.constants import (
    MAX_SUPPLEMENT_SECTIONS,
    MAX_TOTAL_AI_CALLS,
    PRIMARY_REVIEW_CALLS,
    PRIMARY_WRITER_CALLS,
    SUPPLEMENT_CALLS_PER_SECTION,
    SUPPLEMENT_REVIEW_CALLS,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_quality.models import (
    GenerationAssessment,
    QualityGrade,
    QualityProblemCode,
    ReleaseDecision,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _require_sha256(value: str, *, label: str) -> str:
    normalized = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{label}은 SHA-256 64자리여야 합니다")
    return normalized


def _canonical_value(value: Any) -> Any:
    """평가 자료형을 안정적인 JSON 값으로 바꾼다."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, "f")
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: _canonical_value(getattr(value, item.name))
            for item in fields(value)
        }
    if isinstance(value, tuple):
        return [_canonical_value(item) for item in value]
    if isinstance(value, list):
        return [_canonical_value(item) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"평가 영수증에 지원하지 않는 값이 있습니다: {type(value)!r}")


def _canonical_sha256(payload: dict[str, Any]) -> str:
    raw = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _section_sha256s(
    values: tuple[tuple[str, str], ...],
    *,
    label: str,
) -> tuple[tuple[str, str], ...]:
    normalized = tuple(
        (
            str(section_id).strip(),
            _require_sha256(digest, label=f"{label} {section_id}"),
        )
        for section_id, digest in values
    )
    if tuple(section_id for section_id, _ in normalized) != (
        REQUIRED_EVIDENCE_SECTION_IDS
    ):
        raise ValueError(f"{label}에는 정책 순서의 필수 아홉 장이 모두 필요합니다")
    return normalized


class RecoveryAction(str, Enum):
    """오케스트레이터가 다음에 할 수 있는 닫힌 행동."""

    STOP_NO_CHARGE = "STOP_NO_CHARGE"
    RUN_PRIMARY = "RUN_PRIMARY"
    RUN_SUPPLEMENTS = "RUN_SUPPLEMENTS"
    RELEASE_COMPLETE = "RELEASE_COMPLETE"


class ValidationRound(str, Enum):
    """평가 영수증이 어느 AI 묶음에서 나왔는지 표시한다."""

    PRIMARY = "PRIMARY"
    SUPPLEMENT = "SUPPLEMENT"


@dataclass(frozen=True)
class GenerationValidationReceipt:
    """후보·평가·실제 호출 수를 한 번의 검증 결과로 결속한다."""

    company_id: str
    candidate_sha256: str
    assessment: GenerationAssessment
    round: ValidationRound
    writer_calls: int
    reviewer_calls: int
    section_sha256s: tuple[tuple[str, str], ...]
    evidence_packet_sha256s: tuple[tuple[str, str], ...]
    base_receipt_sha256: str = ""
    supplemented_section_ids: tuple[str, ...] = ()
    assessment_sha256: str = field(init=False)
    receipt_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        company_id = str(self.company_id).strip()
        if not company_id:
            raise ValueError("검증 영수증에는 회사 식별자가 필요합니다")
        if not isinstance(self.assessment, GenerationAssessment):
            raise TypeError("검증 영수증에는 실제 GenerationAssessment가 필요합니다")
        if not all(
            isinstance(code, QualityProblemCode)
            for code in self.assessment.quality.problem_codes
        ):
            raise TypeError("품질 문제는 닫힌 QualityProblemCode여야 합니다")
        assessment_versions = {
            self.assessment.contract_version,
            self.assessment.quality.contract_version,
            self.assessment.safety.contract_version,
        }
        if "" in assessment_versions or len(assessment_versions) != 1:
            raise ValueError("품질·안전 평가의 계약 버전이 서로 다릅니다")
        safety_blocked = bool(
            self.assessment.safety.problems
            or self.assessment.safety.unverified_fact_ids
            or self.assessment.safety.rejected_fact_ids
        )
        if safety_blocked != (
            self.assessment.safety.decision is ReleaseDecision.BLOCKED
        ):
            raise ValueError("안전 문제 목록과 공개 차단 판정이 서로 다릅니다")
        expected_publication_grade = (
            self.assessment.quality.grade
            if self.assessment.safety.decision is ReleaseDecision.RELEASE_ALLOWED
            else QualityGrade.INCOMPLETE
        )
        if self.assessment.publication_grade is not expected_publication_grade:
            raise ValueError("공개 등급이 실제 품질·안전 판정과 다릅니다")
        if bool(self.assessment.quality.shortfall_reasons) != bool(
            self.assessment.quality.problem_codes
        ):
            raise ValueError("품질 부족 문구와 닫힌 문제 코드가 서로 다릅니다")

        candidate_sha256 = _require_sha256(
            self.candidate_sha256,
            label="후보 지문",
        )
        section_sha256s = _section_sha256s(
            self.section_sha256s,
            label="장 지문",
        )
        evidence_packet_sha256s = _section_sha256s(
            self.evidence_packet_sha256s,
            label="근거 꾸러미 지문",
        )
        section_ids = tuple(
            str(item).strip() for item in self.supplemented_section_ids
        )
        if any(not item for item in section_ids):
            raise ValueError("보충 장 식별자는 비어 있을 수 없습니다")
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("같은 장의 보충 영수증을 두 번 넣을 수 없습니다")

        if self.round is ValidationRound.PRIMARY:
            if (
                self.writer_calls != PRIMARY_WRITER_CALLS
                or self.reviewer_calls != PRIMARY_REVIEW_CALLS
            ):
                raise ValueError("기본 생성 영수증은 실제 9회 작성·1회 검수여야 합니다")
            if self.base_receipt_sha256 or section_ids:
                raise ValueError("기본 생성 영수증에는 보충 이력이 없어야 합니다")
            base_receipt_sha256 = ""
        elif self.round is ValidationRound.SUPPLEMENT:
            if not 1 <= len(section_ids) <= MAX_SUPPLEMENT_SECTIONS:
                raise ValueError("보충 영수증에는 승인된 장 1~2개가 필요합니다")
            if self.writer_calls != len(section_ids) * SUPPLEMENT_CALLS_PER_SECTION:
                raise ValueError("보충 작성 호출 수가 완료한 장 수와 다릅니다")
            if self.reviewer_calls != SUPPLEMENT_REVIEW_CALLS:
                raise ValueError("보충 묶음은 실제 검수 1회가 필요합니다")
            base_receipt_sha256 = _require_sha256(
                self.base_receipt_sha256,
                label="기본 생성 영수증 지문",
            )
        else:
            raise ValueError("지원하지 않는 검증 회차입니다")

        assessment_sha256 = _canonical_sha256(
            {"generation_assessment": _canonical_value(self.assessment)}
        )
        receipt_sha256 = _canonical_sha256(
            {
                "version": 1,
                "company_id": company_id,
                "candidate_sha256": candidate_sha256,
                "assessment_sha256": assessment_sha256,
                "round": self.round.value,
                "writer_calls": self.writer_calls,
                "reviewer_calls": self.reviewer_calls,
                "section_sha256s": [list(item) for item in section_sha256s],
                "evidence_packet_sha256s": [
                    list(item) for item in evidence_packet_sha256s
                ],
                "base_receipt_sha256": base_receipt_sha256,
                "supplemented_section_ids": list(section_ids),
            }
        )
        object.__setattr__(self, "company_id", company_id)
        object.__setattr__(self, "candidate_sha256", candidate_sha256)
        object.__setattr__(self, "section_sha256s", section_sha256s)
        object.__setattr__(
            self,
            "evidence_packet_sha256s",
            evidence_packet_sha256s,
        )
        object.__setattr__(self, "base_receipt_sha256", base_receipt_sha256)
        object.__setattr__(self, "supplemented_section_ids", section_ids)
        object.__setattr__(self, "assessment_sha256", assessment_sha256)
        object.__setattr__(self, "receipt_sha256", receipt_sha256)

    @property
    def observed_ai_calls(self) -> int:
        return self.writer_calls + self.reviewer_calls


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
        base_candidate_sha256 = _require_sha256(
            self.base_candidate_sha256,
            label="기본 후보 지문",
        )
        base_receipt_sha256 = _require_sha256(
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
        authorization_sha256 = _canonical_sha256(
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
