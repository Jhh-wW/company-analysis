"""작성기·복구·캐시·출고가 함께 쓰는 중립 생성 검증 영수증."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import Any

from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_quality.models import (
    GenerationAssessment,
    QualityGrade,
    QualityProblemCode,
    ReleaseDecision,
)


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def require_sha256(value: str, *, label: str) -> str:
    """외부에서 받은 값이 소문자 canonical SHA-256인지 확인한다."""

    normalized = str(value).strip().lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{label}은 SHA-256 64자리여야 합니다")
    return normalized


def _canonical_value(value: Any) -> Any:
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
    raise TypeError(f"생성 영수증에 지원하지 않는 값이 있습니다: {type(value)!r}")


def canonical_sha256(payload: dict[str, Any]) -> str:
    """자료형과 키 순서에 흔들리지 않는 canonical JSON SHA-256을 만든다."""

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
            require_sha256(digest, label=f"{label} {section_id}"),
        )
        for section_id, digest in values
    )
    if tuple(section_id for section_id, _ in normalized) != (
        REQUIRED_EVIDENCE_SECTION_IDS
    ):
        raise ValueError(f"{label}에는 정책 순서의 필수 아홉 장이 모두 필요합니다")
    return normalized


class ValidationRound(str, Enum):
    """평가 영수증이 어느 AI 묶음에서 나왔는지 표시한다."""

    PRIMARY = "PRIMARY"
    SUPPLEMENT = "SUPPLEMENT"


@dataclass(frozen=True)
class GenerationValidationReceipt:
    """후보·평가·실제 호출 수를 한 번의 검증 결과로 결속한다.

    이 공유 자료형은 호출 수 정책이나 공개·차감 결정을 내리지 않는다. 실제
    호출 수를 정직하게 봉인하고, 각 소비 feature가 자기 정책과 대조한다.
    """

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
        for label, count in (
            ("작성 호출 수", self.writer_calls),
            ("검수 호출 수", self.reviewer_calls),
        ):
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"{label}는 0 이상의 실제 정수여야 합니다")

        candidate_sha256 = require_sha256(
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
        if set(section_ids) - set(REQUIRED_EVIDENCE_SECTION_IDS):
            raise ValueError("보충 영수증에 정책 밖 장이 있습니다")

        if self.round is ValidationRound.PRIMARY:
            if self.base_receipt_sha256 or section_ids:
                raise ValueError("기본 생성 영수증에는 보충 이력이 없어야 합니다")
            base_receipt_sha256 = ""
        elif self.round is ValidationRound.SUPPLEMENT:
            if not section_ids:
                raise ValueError("보충 영수증에는 실제 완료한 장이 필요합니다")
            base_receipt_sha256 = require_sha256(
                self.base_receipt_sha256,
                label="기본 생성 영수증 지문",
            )
        else:
            raise ValueError("지원하지 않는 검증 회차입니다")

        assessment_sha256 = canonical_sha256(
            {"generation_assessment": _canonical_value(self.assessment)}
        )
        receipt_sha256 = canonical_sha256(
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


__all__ = [
    "GenerationValidationReceipt",
    "ValidationRound",
    "canonical_sha256",
    "require_sha256",
]
