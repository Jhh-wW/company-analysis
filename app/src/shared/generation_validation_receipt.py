"""작성기·복구·캐시·출고가 함께 쓰는 중립 생성 검증 영수증."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping

from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_quality.integrity import (
    assert_complete_generation_assessment,
)
from src.shared.report_quality.models import (
    GenerationAssessment,
    QualityAssessment,
    QualityGrade,
    QualityProblemCode,
    ReleaseDecision,
    SafetyAssessment,
)
from src.shared.report_quality.constants import STRICT_QUALITY_CONTRACT_VERSION


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def require_sha256(value: str, *, label: str) -> str:
    """외부에서 받은 값이 소문자 canonical SHA-256인지 확인한다."""

    if type(value) is not str or value != value.strip():
        raise ValueError(f"{label}은 소문자 SHA-256 64자리여야 합니다")
    normalized = value
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


def _canonical_assessment_value(value: GenerationAssessment) -> dict[str, Any]:
    """v3 추가 필드는 새 영수증에만 넣고 과거 wire bytes는 그대로 둔다."""

    payload = _canonical_value(value)
    if type(payload) is not dict:  # pragma: no cover - dataclass 계약 방어
        raise TypeError("GenerationAssessment를 JSON 객체로 바꿀 수 없습니다")
    quality = payload.get("quality")
    if type(quality) is not dict:  # pragma: no cover - 정확한 타입은 호출부가 검사
        raise TypeError("QualityAssessment를 JSON 객체로 바꿀 수 없습니다")
    if value.contract_version != STRICT_QUALITY_CONTRACT_VERSION:
        # report-quality-v1과 이미 발급된 v2 FULL의 canonical assessment에는
        # 이 키가 존재하지 않았다. 빈 기본 필드를 직렬화하면 같은 저장본의
        # assessment/receipt SHA-256이 달라지므로 과거 버전에서만 생략한다.
        quality.pop("section_interpretation_counts", None)
    return payload


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
    if type(values) is not tuple or any(
        type(item) is not tuple
        or len(item) != 2
        or type(item[0]) is not str
        or item[0] != item[0].strip()
        or not item[0]
        for item in values
    ):
        raise ValueError(f"{label} 저장 형식이 손상됐습니다")
    normalized = tuple(
        (
            section_id,
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
    #: 장별 공개 봉인 블록 지문(``PublicSectionContentBlock.block_sha256``).
    #: ``section_sha256s``와 «다른 것»이다 — 저건 pre-render 공개 content
    #: 봉인(지문 A)에서 와 «보이는 것»만 덮고, 이건 display와 감사 장부를 함께
    #: 덮는다. 보충 회차가 승인하지 않은 장의 FactRecord·등급 기여를 조용히
    #: 바꾸는 표류는 이 값으로만 잡힌다(``report_recovery`` 결속 검사).
    #: 빈 값은 이 필드가 생기기 전 영수증이며, 보충 결속은 빈 값을 거부한다.
    section_block_sha256s: tuple[tuple[str, str], ...] = ()
    assessment_sha256: str = field(init=False)
    receipt_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            type(self.company_id) is not str
            or self.company_id != self.company_id.strip()
            or not self.company_id
        ):
            raise ValueError("검증 영수증에는 회사 식별자가 필요합니다")
        company_id = self.company_id
        if type(self.assessment) is not GenerationAssessment:
            raise TypeError("검증 영수증에는 실제 GenerationAssessment가 필요합니다")
        if (
            type(self.assessment.quality) is not QualityAssessment
            or type(self.assessment.safety) is not SafetyAssessment
        ):
            raise TypeError("검증 영수증의 품질·안전 판정 형식이 손상됐습니다")
        if type(self.round) is not ValidationRound:
            raise TypeError("검증 회차는 닫힌 ValidationRound여야 합니다")
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
        if (
            self.assessment.quality.grade is QualityGrade.COMPLETE
            and self.assessment.publication_grade is QualityGrade.COMPLETE
            and self.assessment.safety.decision is ReleaseDecision.RELEASE_ALLOWED
        ):
            assert_complete_generation_assessment(self.assessment)
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
        if self.section_block_sha256s == ():
            section_block_sha256s: tuple[tuple[str, str], ...] = ()
        else:
            section_block_sha256s = _section_sha256s(
                self.section_block_sha256s,
                label="장별 봉인 블록 지문",
            )
        if type(self.supplemented_section_ids) is not tuple or any(
            type(item) is not str
            or item != item.strip()
            or not item
            for item in self.supplemented_section_ids
        ):
            raise ValueError("보충 장 식별자 저장 형식이 손상됐습니다")
        section_ids = self.supplemented_section_ids
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
            {"generation_assessment": _canonical_assessment_value(self.assessment)}
        )
        receipt_sha256 = canonical_sha256(
            {
                # v2 — section_block_sha256s가 지문 입력에 들어갔다. 이 값이
                # 지문 밖에 있으면 장부 지문만 바꿔치기해도 영수증 사슬이
                # 그대로라 보충 결속 검사를 우회할 수 있다.
                "version": 2,
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
                "section_block_sha256s": [
                    list(item) for item in section_block_sha256s
                ],
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
        object.__setattr__(
            self, "section_block_sha256s", section_block_sha256s
        )
        object.__setattr__(self, "assessment_sha256", assessment_sha256)
        object.__setattr__(self, "receipt_sha256", receipt_sha256)

    @property
    def observed_ai_calls(self) -> int:
        return self.writer_calls + self.reviewer_calls


_ASSESSMENT_KEYS = frozenset(
    {"contract_version", "quality", "safety", "publication_grade"}
)
_LEGACY_QUALITY_KEYS = frozenset(
    {
        "contract_version",
        "grade",
        "substantive_claims",
        "verified_claims",
        "verified_ratio",
        "document_sources",
        "notice_only_sections",
        "one_claim_sections",
        "section_claim_counts",
        "shortfall_reasons",
        "section_public_sentence_counts",
        "underfilled_sections",
        "semantic_underfilled_sections",
        "problem_codes",
    }
)
_QUALITY_KEYS = frozenset(
    {*_LEGACY_QUALITY_KEYS, "section_interpretation_counts"}
)
_SAFETY_KEYS = frozenset(
    {
        "contract_version",
        "decision",
        "verified_fact_ids",
        "unverified_fact_ids",
        "rejected_fact_ids",
        "problems",
    }
)
_RECEIPT_WIRE_KEYS = frozenset(
    {
        "version",
        "company_id",
        "candidate_sha256",
        "assessment",
        "round",
        "writer_calls",
        "reviewer_calls",
        "section_sha256s",
        "evidence_packet_sha256s",
        "base_receipt_sha256",
        "supplemented_section_ids",
        "section_block_sha256s",
        "assessment_sha256",
        "receipt_sha256",
    }
)


def _require_exact_dict(
    value: object,
    *,
    keys: frozenset[str],
    label: str,
) -> dict[str, Any]:
    if type(value) is not dict or set(value) != keys:
        raise ValueError(f"{label}의 키 또는 객체 형식이 계약과 다릅니다")
    return value


def _wire_strings(value: object, *, label: str) -> tuple[str, ...]:
    if type(value) is not list or any(
        type(item) is not str or item != item.strip() or not item
        for item in value
    ):
        raise ValueError(f"{label}은 공백 없는 문자열 JSON 배열이어야 합니다")
    return tuple(value)


def _wire_pairs(
    value: object,
    *,
    label: str,
    integer_value: bool,
) -> tuple[tuple[str, Any], ...]:
    if type(value) is not list:
        raise ValueError(f"{label}은 JSON 배열이어야 합니다")
    out: list[tuple[str, Any]] = []
    for item in value:
        if (
            type(item) is not list
            or len(item) != 2
            or type(item[0]) is not str
            or item[0] != item[0].strip()
            or not item[0]
        ):
            raise ValueError(f"{label} pair 저장 형식이 손상됐습니다")
        if integer_value:
            if type(item[1]) is not int or item[1] < 0:
                raise ValueError(f"{label} count는 0 이상의 정수여야 합니다")
            pair_value: Any = item[1]
        else:
            if type(item[1]) is not str:
                raise ValueError(f"{label} digest는 문자열이어야 합니다")
            pair_value = item[1]
        out.append((item[0], pair_value))
    return tuple(out)


def generation_assessment_to_dict(value: GenerationAssessment) -> dict[str, Any]:
    """정확한 평가 객체를 문자열 등급 재구성 없이 canonical wire로 바꾼다."""

    if (
        type(value) is not GenerationAssessment
        or type(value.quality) is not QualityAssessment
        or type(value.safety) is not SafetyAssessment
    ):
        raise TypeError("정확한 GenerationAssessment·QualityAssessment·SafetyAssessment가 필요합니다")
    return _canonical_assessment_value(value)


def generation_assessment_from_dict(data: Mapping[str, Any]) -> GenerationAssessment:
    """unknown/missing key와 느슨한 scalar 변환 없이 평가 원본을 복원한다."""

    raw = _require_exact_dict(data, keys=_ASSESSMENT_KEYS, label="GenerationAssessment")
    # v3부터 장별 해석 수가 영수증 정본이다. 과거 v1/v2 저장 bytes는 키가
    # 없던 모양 그대로 읽되, v3가 그 옛 모양으로 빠지는 것은 허용하지 않는다.
    v3_wire = raw.get("contract_version") == STRICT_QUALITY_CONTRACT_VERSION
    quality_raw = _require_exact_dict(
        raw["quality"],
        keys=_QUALITY_KEYS if v3_wire else _LEGACY_QUALITY_KEYS,
        label="QualityAssessment",
    )
    safety_raw = _require_exact_dict(
        raw["safety"],
        keys=_SAFETY_KEYS,
        label="SafetyAssessment",
    )
    for value, label in (
        (raw["contract_version"], "GenerationAssessment contract"),
        (quality_raw["contract_version"], "QualityAssessment contract"),
        (safety_raw["contract_version"], "SafetyAssessment contract"),
    ):
        if type(value) is not str or value != value.strip() or not value:
            raise ValueError(f"{label} 형식이 손상됐습니다")
    for key in ("substantive_claims", "verified_claims", "document_sources"):
        if type(quality_raw[key]) is not int or quality_raw[key] < 0:
            raise ValueError(f"QualityAssessment {key}는 0 이상의 정수여야 합니다")
    for value, label in (
        (quality_raw["grade"], "품질 등급"),
        (raw["publication_grade"], "공개 등급"),
        (safety_raw["decision"], "안전 판정"),
    ):
        if type(value) is not str or value != value.strip() or not value:
            raise ValueError(f"{label} enum 문자열 형식이 손상됐습니다")
    if type(quality_raw["verified_ratio"]) is not str:
        raise ValueError("QualityAssessment verified_ratio는 canonical 문자열이어야 합니다")
    ratio = Decimal(quality_raw["verified_ratio"])
    if not ratio.is_finite() or format(ratio, "f") != quality_raw["verified_ratio"]:
        raise ValueError("QualityAssessment verified_ratio가 canonical Decimal이 아닙니다")
    problem_codes_raw = quality_raw["problem_codes"]
    if type(problem_codes_raw) is not list or any(
        type(value) is not str or value != value.strip() or not value
        for value in problem_codes_raw
    ):
        raise ValueError("QualityAssessment 문제 코드가 JSON 문자열 배열이 아닙니다")
    quality = QualityAssessment(
        contract_version=quality_raw["contract_version"],
        grade=QualityGrade(quality_raw["grade"]),
        substantive_claims=quality_raw["substantive_claims"],
        verified_claims=quality_raw["verified_claims"],
        verified_ratio=ratio,
        document_sources=quality_raw["document_sources"],
        notice_only_sections=_wire_strings(
            quality_raw["notice_only_sections"], label="안내문 장"
        ),
        one_claim_sections=_wire_strings(
            quality_raw["one_claim_sections"], label="한 claim 장"
        ),
        section_claim_counts=_wire_pairs(
            quality_raw["section_claim_counts"],
            label="장별 의미 claim 수",
            integer_value=True,
        ),
        shortfall_reasons=_wire_strings(
            quality_raw["shortfall_reasons"], label="품질 부족 사유"
        ),
        section_public_sentence_counts=_wire_pairs(
            quality_raw["section_public_sentence_counts"],
            label="장별 공개 문장 수",
            integer_value=True,
        ),
        underfilled_sections=_wire_strings(
            quality_raw["underfilled_sections"], label="공개 문장 부족 장"
        ),
        semantic_underfilled_sections=_wire_strings(
            quality_raw["semantic_underfilled_sections"], label="의미 부족 장"
        ),
        section_interpretation_counts=(
            _wire_pairs(
                quality_raw["section_interpretation_counts"],
                label="장별 해석 claim 수",
                integer_value=True,
            )
            if v3_wire
            else ()
        ),
        problem_codes=tuple(QualityProblemCode(value) for value in problem_codes_raw),
    )
    safety = SafetyAssessment(
        contract_version=safety_raw["contract_version"],
        decision=ReleaseDecision(safety_raw["decision"]),
        verified_fact_ids=_wire_strings(
            safety_raw["verified_fact_ids"], label="검증 fact"
        ),
        unverified_fact_ids=_wire_strings(
            safety_raw["unverified_fact_ids"], label="미검증 fact"
        ),
        rejected_fact_ids=_wire_strings(
            safety_raw["rejected_fact_ids"], label="거절 fact"
        ),
        problems=_wire_strings(safety_raw["problems"], label="안전 문제"),
    )
    value = GenerationAssessment(
        contract_version=raw["contract_version"],
        quality=quality,
        safety=safety,
        publication_grade=QualityGrade(raw["publication_grade"]),
    )
    if generation_assessment_to_dict(value) != raw:
        raise ValueError("GenerationAssessment가 canonical wire 왕복과 다릅니다")
    return value


def receipt_to_dict(value: GenerationValidationReceipt) -> dict[str, Any]:
    if type(value) is not GenerationValidationReceipt:
        raise TypeError("정확한 GenerationValidationReceipt가 필요합니다")
    return {
        "version": 2,
        "company_id": value.company_id,
        "candidate_sha256": value.candidate_sha256,
        "assessment": generation_assessment_to_dict(value.assessment),
        "round": value.round.value,
        "writer_calls": value.writer_calls,
        "reviewer_calls": value.reviewer_calls,
        "section_sha256s": [list(item) for item in value.section_sha256s],
        "evidence_packet_sha256s": [
            list(item) for item in value.evidence_packet_sha256s
        ],
        "base_receipt_sha256": value.base_receipt_sha256,
        "supplemented_section_ids": list(value.supplemented_section_ids),
        "section_block_sha256s": [
            list(item) for item in value.section_block_sha256s
        ],
        "assessment_sha256": value.assessment_sha256,
        "receipt_sha256": value.receipt_sha256,
    }


def receipt_from_dict(data: Mapping[str, Any]) -> GenerationValidationReceipt:
    raw = _require_exact_dict(
        data,
        keys=_RECEIPT_WIRE_KEYS,
        label="GenerationValidationReceipt",
    )
    if type(raw["version"]) is not int or raw["version"] != 2:
        raise ValueError("지원하지 않는 검증 영수증 wire 버전입니다")
    if type(raw["round"]) is not str:
        raise ValueError("검증 영수증 round는 닫힌 enum 문자열이어야 합니다")
    for key in ("writer_calls", "reviewer_calls"):
        if type(raw[key]) is not int or raw[key] < 0:
            raise ValueError(f"검증 영수증 {key}는 0 이상의 정수여야 합니다")
    assessment = generation_assessment_from_dict(raw["assessment"])
    value = GenerationValidationReceipt(
        company_id=raw["company_id"],
        candidate_sha256=raw["candidate_sha256"],
        assessment=assessment,
        round=ValidationRound(raw["round"]),
        writer_calls=raw["writer_calls"],
        reviewer_calls=raw["reviewer_calls"],
        section_sha256s=_wire_pairs(
            raw["section_sha256s"], label="장 지문", integer_value=False
        ),
        evidence_packet_sha256s=_wire_pairs(
            raw["evidence_packet_sha256s"],
            label="근거 꾸러미 지문",
            integer_value=False,
        ),
        base_receipt_sha256=raw["base_receipt_sha256"],
        supplemented_section_ids=_wire_strings(
            raw["supplemented_section_ids"], label="보충 장"
        ),
        section_block_sha256s=_wire_pairs(
            raw["section_block_sha256s"],
            label="장별 봉인 블록 지문",
            integer_value=False,
        ),
    )
    if (
        type(raw["assessment_sha256"]) is not str
        or type(raw["receipt_sha256"]) is not str
        or raw["assessment_sha256"] != value.assessment_sha256
        or raw["receipt_sha256"] != value.receipt_sha256
        or receipt_to_dict(value) != raw
    ):
        raise ValueError("검증 영수증 지문 또는 canonical wire가 원본 필드와 다릅니다")
    return value


def receipt_to_json(value: GenerationValidationReceipt) -> str:
    return json.dumps(
        receipt_to_dict(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def receipt_from_json(value: bytes | str) -> GenerationValidationReceipt:
    if type(value) is bytes:
        try:
            raw_text = value.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("검증 영수증 bytes가 UTF-8이 아닙니다") from error
    elif type(value) is str:
        raw_text = value
    else:
        raise TypeError("검증 영수증 canonical bytes 또는 문자열이 필요합니다")
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as error:
        raise ValueError("검증 영수증 JSON을 읽을 수 없습니다") from error
    receipt = receipt_from_dict(data)
    if receipt_to_json(receipt) != raw_text:
        raise ValueError("검증 영수증 JSON bytes가 canonical 표현이 아닙니다")
    return receipt
__all__ = [
    "GenerationValidationReceipt",
    "ValidationRound",
    "canonical_sha256",
    "generation_assessment_from_dict",
    "generation_assessment_to_dict",
    "receipt_from_dict",
    "receipt_from_json",
    "receipt_to_dict",
    "receipt_to_json",
    "require_sha256",
]
