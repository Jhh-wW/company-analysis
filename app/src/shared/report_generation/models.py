"""FULL 생성기가 release 권위에 넘기는 불변·비권위 생산 증거.

이 자료형은 공개·차감 결정을 갖지 않는다. 정확히 한 번 평가한 후보와 실제
AI 호출, 입력 packet 및 최종 공개 내용의 지문을 손실 없이 운반할 뿐이다.
최종 release 영수증과 상태기계는 별도 shared 권위가 이 값을 소비해 만든다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, fields, is_dataclass
from decimal import Decimal
from enum import Enum
from typing import Any, Final, Mapping

from src.shared.generation_validation_receipt import (
    GenerationValidationReceipt,
    ValidationRound,
    generation_assessment_from_dict,
    generation_assessment_to_dict,
    receipt_from_dict,
    receipt_to_dict,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_quality.models import (
    GenerationAssessment,
    QualityGrade,
    ReleaseDecision,
)
from src.shared.report_quality.integrity import (
    assert_complete_generation_assessment,
)


GENERATION_PRODUCER_EVIDENCE_VERSION: Final[str] = (
    "generation-producer-evidence-v2"
)
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_COMPANY_ID_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{8}")
_CALL_ROLES: Final[frozenset[str]] = frozenset({"writer", "reviewer"})
_CALL_OUTCOMES: Final[frozenset[str]] = frozenset({"returned", "failed"})


def require_sha256(value: object, *, label: str) -> str:
    """외부 경계에서 받은 완전한 SHA-256만 허용한다."""

    if type(value) is not str or value != value.strip():
        raise ValueError(f"{label}은 소문자 SHA-256 64자리여야 합니다")
    normalized = value
    if _SHA256_RE.fullmatch(normalized) is None:
        raise ValueError(f"{label}은 SHA-256 64자리여야 합니다")
    return normalized


def canonical_value(value: Any) -> Any:
    """dataclass·enum·Decimal을 안정적인 JSON 값으로 바꾼다."""

    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Decimal):
        return format(value, "f")
    if is_dataclass(value) and not isinstance(value, type):
        return {
            item.name: canonical_value(getattr(value, item.name))
            for item in fields(value)
            if item.init
        }
    if isinstance(value, Mapping):
        return {
            str(key): canonical_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (tuple, list)):
        return [canonical_value(item) for item in value]
    if value is None or isinstance(value, (str, int, bool)):
        return value
    raise TypeError(f"canonical 생성 증거가 지원하지 않는 값입니다: {type(value)!r}")


def canonical_json(value: Any) -> str:
    return json.dumps(
        canonical_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def exact_text_sha256(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _ordered_section_digests(
    values: tuple[tuple[str, str], ...], *, label: str
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
    if tuple(section_id for section_id, _digest in normalized) != (
        REQUIRED_EVIDENCE_SECTION_IDS
    ):
        raise ValueError(f"{label}에는 정책 순서의 필수 아홉 장이 필요합니다")
    return normalized


@dataclass(frozen=True)
class GenerationCallRecord:
    """원문을 저장하지 않는 실제 AI 호출 한 건의 기계 영수증."""

    sequence: int
    role: str
    role_index: int
    section_id: str
    prompt_sha256: str
    response_sha256: str
    outcome: str
    validation_round: ValidationRound = ValidationRound.PRIMARY
    error_kind: str = ""

    def __post_init__(self) -> None:
        if (
            type(self.sequence) is not int
            or type(self.role_index) is not int
            or self.sequence <= 0
            or self.role_index <= 0
        ):
            raise ValueError("AI 호출 순번은 1 이상이어야 합니다")
        if type(self.role) is not str or type(self.outcome) is not str:
            raise TypeError("AI 호출 역할과 결과는 닫힌 문자열이어야 합니다")
        if self.role not in _CALL_ROLES:
            raise ValueError(f"알 수 없는 AI 호출 역할입니다: {self.role!r}")
        if self.outcome not in _CALL_OUTCOMES:
            raise ValueError(f"알 수 없는 AI 호출 결과입니다: {self.outcome!r}")
        if type(self.validation_round) is not ValidationRound:
            raise TypeError("AI 호출에는 닫힌 validation round가 필요합니다")
        if (
            type(self.section_id) is not str
            or self.section_id != self.section_id.strip()
            or not self.section_id
        ):
            raise ValueError("AI 호출에는 소유 장 또는 bundled 범위가 필요합니다")
        if type(self.error_kind) is not str:
            raise TypeError("AI 오류 종류는 문자열이어야 합니다")
        object.__setattr__(
            self,
            "prompt_sha256",
            require_sha256(self.prompt_sha256, label="AI prompt 지문"),
        )
        if self.outcome == "returned":
            object.__setattr__(
                self,
                "response_sha256",
                require_sha256(self.response_sha256, label="AI 응답 지문"),
            )
            if self.error_kind:
                raise ValueError("정상 AI 호출에는 오류 종류를 넣을 수 없습니다")
        else:
            if self.response_sha256:
                raise ValueError("실패 AI 호출에는 응답 지문을 넣을 수 없습니다")
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", self.error_kind):
                raise ValueError("AI 오류 종류는 원문 없는 클래스 이름이어야 합니다")


@dataclass(frozen=True)
class GenerationCallLedger:
    """생성기가 실제 관측한 호출만 순서대로 보존하는 불변 장부."""

    records: tuple[GenerationCallRecord, ...]

    def __post_init__(self) -> None:
        if type(self.records) is not tuple or any(
            type(record) is not GenerationCallRecord for record in self.records
        ):
            raise TypeError("AI 호출 장부에는 정확한 record tuple이 필요합니다")
        if tuple(record.sequence for record in self.records) != tuple(
            range(1, len(self.records) + 1)
        ):
            raise ValueError("AI 호출 장부의 전체 순번이 연속적이지 않습니다")
        round_projection = tuple(
            record.validation_round for record in self.records
        )
        if round_projection and round_projection[0] is not ValidationRound.PRIMARY:
            raise ValueError("AI 호출 장부는 PRIMARY 검증 회차부터 시작해야 합니다")
        supplement_started = False
        for validation_round in round_projection:
            if validation_round is ValidationRound.SUPPLEMENT:
                supplement_started = True
            elif supplement_started:
                raise ValueError(
                    "SUPPLEMENT 검증 회차가 시작된 뒤 PRIMARY로 돌아갈 수 없습니다"
                )
        # role_index는 전체 실행 누적 번호가 아니라 «그 검증 회차 안에서의
        # 역할 순번»이다. PRIMARY writer 1..9 뒤 SUPPLEMENT writer 1..N이
        # 다시 시작하며, 전체 실행 순서는 위 sequence만 1..13으로 잇는다.
        for validation_round in ValidationRound:
            for role in sorted(_CALL_ROLES):
                indexes = tuple(
                    record.role_index
                    for record in self.records
                    if record.validation_round is validation_round
                    and record.role == role
                )
                if indexes != tuple(range(1, len(indexes) + 1)):
                    raise ValueError(
                        f"{validation_round.value} {role} 호출 역할 순번이 "
                        "1부터 연속적이지 않습니다"
                    )

    @property
    def writer_calls(self) -> int:
        return sum(record.role == "writer" for record in self.records)

    @property
    def reviewer_calls(self) -> int:
        return sum(record.role == "reviewer" for record in self.records)


@dataclass(frozen=True)
class GenerationRunMetrics:
    """cache 재사용 뒤에도 0으로 꾸미지 않는 생성 당시의 정확한 네 지표."""

    fragments_collected: int
    fragments_cited: int
    sentences_made: int
    sentences_passed: int
    version: str = "generation-run-metrics-v1"

    def __post_init__(self) -> None:
        if self.version != "generation-run-metrics-v1" or type(self.version) is not str:
            raise ValueError("지원하지 않는 생성 지표 버전입니다")
        for name in (
            "fragments_collected",
            "fragments_cited",
            "sentences_made",
            "sentences_passed",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name}은 0 이상의 실제 정수여야 합니다")
        if self.fragments_cited > self.fragments_collected:
            raise ValueError("인용 조각 수가 수집 조각 수보다 클 수 없습니다")
        if self.sentences_passed > self.sentences_made:
            raise ValueError("통과 문장 수가 공개 후보 문장 수보다 클 수 없습니다")


def generation_metrics_to_dict(value: GenerationRunMetrics) -> dict[str, object]:
    if type(value) is not GenerationRunMetrics:
        raise TypeError("정확한 GenerationRunMetrics가 필요합니다")
    return {
        "version": value.version,
        "fragments_collected": value.fragments_collected,
        "fragments_cited": value.fragments_cited,
        "sentences_made": value.sentences_made,
        "sentences_passed": value.sentences_passed,
    }


def generation_metrics_from_dict(data: Mapping[str, object]) -> GenerationRunMetrics:
    expected = {
        "version",
        "fragments_collected",
        "fragments_cited",
        "sentences_made",
        "sentences_passed",
    }
    if type(data) is not dict or set(data) != expected:
        raise ValueError("생성 지표의 key 또는 객체 형식이 계약과 다릅니다")
    value = GenerationRunMetrics(
        version=data["version"],
        fragments_collected=data["fragments_collected"],
        fragments_cited=data["fragments_cited"],
        sentences_made=data["sentences_made"],
        sentences_passed=data["sentences_passed"],
    )
    if generation_metrics_to_dict(value) != data:
        raise ValueError("생성 지표가 canonical wire 왕복과 다릅니다")
    return value


@dataclass(frozen=True)
class GenerationProducerEvidence:
    """최종 release receipt의 입력이 되는 FULL 생성 생산 증거.

    ``public_content_sha256``은 이 객체와 manifest를 제외한 명시적 공개 projection
    지문이다. 따라서 Report가 자기 자신을 포함해 순환 해시하는 구조가 아니다.
    """

    company_id: str
    evidence_generation_sha256: str
    build_identity_sha256: str
    candidate_sha256: str
    assessment: GenerationAssessment
    public_manifest_sha256: str
    public_content_sha256: str
    section_sha256s: tuple[tuple[str, str], ...]
    evidence_packet_sha256s: tuple[tuple[str, str], ...]
    validation_receipts: tuple[GenerationValidationReceipt, ...]
    call_ledger: GenerationCallLedger
    version: str = GENERATION_PRODUCER_EVIDENCE_VERSION
    assessment_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            type(self.company_id) is not str
            or self.company_id != self.company_id.strip()
        ):
            raise ValueError("생성 증거의 company_id 형식이 손상됐습니다")
        company_id = self.company_id
        if _COMPANY_ID_RE.fullmatch(company_id) is None:
            raise ValueError("생성 증거의 company_id는 정확한 gen8 식별자여야 합니다")
        if self.version != GENERATION_PRODUCER_EVIDENCE_VERSION:
            raise ValueError("지원하지 않는 생성 생산 증거 버전입니다")
        if type(self.version) is not str:
            raise TypeError("생성 생산 증거 버전은 문자열이어야 합니다")
        if type(self.assessment) is not GenerationAssessment:
            raise TypeError("생성 증거에는 실제 GenerationAssessment 객체가 필요합니다")
        if type(self.call_ledger) is not GenerationCallLedger:
            raise TypeError("생성 증거에는 정확한 실제 호출 장부가 필요합니다")
        if type(self.validation_receipts) is not tuple:
            raise TypeError("생성 증거 검증 영수증은 tuple이어야 합니다")
        object.__setattr__(self, "company_id", company_id)
        for name, label in (
            ("evidence_generation_sha256", "근거 generation 지문"),
            ("build_identity_sha256", "생성 build identity 지문"),
            ("candidate_sha256", "평가 후보 지문"),
            ("public_manifest_sha256", "공개 manifest bytes 지문"),
            ("public_content_sha256", "최종 공개 content 지문"),
        ):
            object.__setattr__(
                self,
                name,
                require_sha256(getattr(self, name), label=label),
            )
        object.__setattr__(
            self,
            "section_sha256s",
            _ordered_section_digests(self.section_sha256s, label="최종 장 지문"),
        )
        object.__setattr__(
            self,
            "evidence_packet_sha256s",
            _ordered_section_digests(
                self.evidence_packet_sha256s,
                label="근거 packet 지문",
            ),
        )
        versions = {
            self.assessment.contract_version,
            self.assessment.quality.contract_version,
            self.assessment.safety.contract_version,
        }
        if "" in versions or len(versions) != 1:
            raise ValueError("생성 평가의 품질·안전 계약 버전이 서로 다릅니다")
        if any(
            record.outcome != "returned" for record in self.call_ledger.records
        ):
            raise ValueError("성공 FULL 생산 증거에는 실패한 AI 호출이 있을 수 없습니다")
        self._assert_receipt_call_chain()
        if (
            self.assessment.quality.grade is not QualityGrade.COMPLETE
            or self.assessment.publication_grade is not QualityGrade.COMPLETE
            or self.assessment.safety.decision
            is not ReleaseDecision.RELEASE_ALLOWED
        ):
            raise ValueError("성공 FULL 생산 증거에는 COMPLETE·RELEASE_ALLOWED 평가가 필요합니다")
        assert_complete_generation_assessment(self.assessment)
        object.__setattr__(
            self,
            "assessment_sha256",
            self.validation_receipts[-1].assessment_sha256,
        )

    def _assert_round_records(
        self,
        receipt: GenerationValidationReceipt,
    ) -> tuple[GenerationCallRecord, ...]:
        records = tuple(
            record
            for record in self.call_ledger.records
            if record.validation_round is receipt.round
        )
        writers = tuple(record for record in records if record.role == "writer")
        reviewers = tuple(record for record in records if record.role == "reviewer")
        if len(writers) != receipt.writer_calls or len(reviewers) != receipt.reviewer_calls:
            raise ValueError("검증 영수증의 호출 수가 실제 전체 장부와 다릅니다")
        if tuple(record.role for record in records) != (
            ("writer",) * len(writers) + ("reviewer",) * len(reviewers)
        ):
            raise ValueError("검증 회차의 writer/reviewer 실제 호출 순서가 깨졌습니다")
        if len(reviewers) != 1 or reviewers[0].section_id != "bundled":
            raise ValueError("각 검증 회차에는 고정 bundled reviewer 한 번이 필요합니다")
        return writers

    def _assert_receipt_call_chain(self) -> None:
        receipts = self.validation_receipts
        if len(receipts) not in {1, 2} or any(
            type(receipt) is not GenerationValidationReceipt for receipt in receipts
        ):
            raise TypeError("생성 증거에는 공용 검증 영수증 한두 개가 정확히 필요합니다")
        primary = receipts[0]
        if primary.round is not ValidationRound.PRIMARY:
            raise ValueError("첫 검증 영수증은 PRIMARY여야 합니다")
        if primary.writer_calls != 9 or primary.reviewer_calls != 1:
            raise ValueError("PRIMARY 검증 영수증은 실제 writer 9·reviewer 1이어야 합니다")
        primary_writers = self._assert_round_records(primary)
        if tuple(record.section_id for record in primary_writers) != (
            REQUIRED_EVIDENCE_SECTION_IDS
        ):
            raise ValueError("PRIMARY writer 장부가 정책 순서 아홉 장과 다릅니다")
        if primary.company_id != self.company_id:
            raise ValueError("PRIMARY 검증 영수증의 회사가 생산 증거와 다릅니다")
        if primary.evidence_packet_sha256s != self.evidence_packet_sha256s:
            raise ValueError("PRIMARY 검증 영수증의 packet이 생산 증거와 다릅니다")

        final = primary
        if len(receipts) == 2:
            supplement = receipts[1]
            if supplement.round is not ValidationRound.SUPPLEMENT:
                raise ValueError("두 번째 검증 영수증은 SUPPLEMENT여야 합니다")
            targets = supplement.supplemented_section_ids
            if not 1 <= len(targets) <= 2:
                raise ValueError("SUPPLEMENT는 실패 장 한두 개만 한 번씩 허용합니다")
            if (
                supplement.company_id != primary.company_id
                or supplement.base_receipt_sha256 != primary.receipt_sha256
                or supplement.evidence_packet_sha256s
                != primary.evidence_packet_sha256s
            ):
                raise ValueError("SUPPLEMENT가 PRIMARY 회사·packet·receipt와 다릅니다")
            if supplement.writer_calls != len(targets) or supplement.reviewer_calls != 1:
                raise ValueError("SUPPLEMENT 실제 호출 수가 대상 장+bundled 검수와 다릅니다")
            supplement_writers = self._assert_round_records(supplement)
            if tuple(record.section_id for record in supplement_writers) != targets:
                raise ValueError("SUPPLEMENT writer 장부가 승인 대상 장과 다릅니다")
            base_sections = dict(primary.section_sha256s)
            final_sections = dict(supplement.section_sha256s)
            target_set = set(targets)
            for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
                changed = base_sections[section_id] != final_sections[section_id]
                if changed != (section_id in target_set):
                    raise ValueError("SUPPLEMENT가 대상 밖 장을 바꾸거나 대상 장을 안 바꿨습니다")
            final = supplement
        elif any(
            record.validation_round is ValidationRound.SUPPLEMENT
            for record in self.call_ledger.records
        ):
            raise ValueError("SUPPLEMENT 영수증 없이 추가 호출을 숨길 수 없습니다")

        if (
            final.company_id != self.company_id
            or final.candidate_sha256 != self.candidate_sha256
            or final.assessment != self.assessment
            or final.section_sha256s != self.section_sha256s
            or final.evidence_packet_sha256s != self.evidence_packet_sha256s
        ):
            raise ValueError("최종 검증 영수증이 생산 증거의 후보·평가·장·packet과 다릅니다")
        expected_records = sum(
            receipt.writer_calls + receipt.reviewer_calls for receipt in receipts
        )
        if len(self.call_ledger.records) != expected_records:
            raise ValueError("검증 영수증 밖의 실제 호출을 숨기거나 덧붙일 수 없습니다")

    @property
    def writer_calls(self) -> int:
        return self.call_ledger.writer_calls

    @property
    def reviewer_calls(self) -> int:
        return self.call_ledger.reviewer_calls


def _tuple_pairs(value: object, *, integer_value: bool) -> tuple[tuple[Any, Any], ...]:
    if type(value) is not list:
        raise ValueError("생성 증거 pair 필드가 JSON 배열이 아닙니다")
    out: list[tuple[Any, Any]] = []
    for item in value:
        if (
            type(item) is not list
            or len(item) != 2
            or type(item[0]) is not str
            or item[0] != item[0].strip()
            or not item[0]
        ):
            raise ValueError("생성 증거 pair 항목의 모양이 깨졌습니다")
        if integer_value:
            if type(item[1]) is not int or item[1] < 0:
                raise ValueError("생성 증거 pair count가 정수가 아닙니다")
        elif type(item[1]) is not str:
            raise ValueError("생성 증거 pair digest가 문자열이 아닙니다")
        out.append((item[0], item[1]))
    return tuple(out)


def producer_evidence_to_dict(value: GenerationProducerEvidence) -> dict[str, Any]:
    payload = canonical_value(value)
    if not isinstance(payload, dict):  # pragma: no cover - dataclass 계약 방어
        raise TypeError("생성 생산 증거를 JSON 객체로 바꿀 수 없습니다")
    # ``assessment_sha256``은 계산 필드라 canonical dataclass projection에서는
    # 제외된다. transport에는 반드시 싣고, reload 시 별도 값과 재계산 결과를
    # 대조한다. 문자열 등급에서 평가를 다시 꾸미지 않는다.
    payload["assessment_sha256"] = value.assessment_sha256
    payload["assessment"] = generation_assessment_to_dict(value.assessment)
    payload["validation_receipts"] = [
        receipt_to_dict(receipt)
        for receipt in value.validation_receipts
    ]
    return payload


def producer_evidence_from_dict(data: Mapping[str, Any]) -> GenerationProducerEvidence:
    expected_keys = {
        "company_id",
        "evidence_generation_sha256",
        "build_identity_sha256",
        "candidate_sha256",
        "assessment",
        "public_manifest_sha256",
        "public_content_sha256",
        "section_sha256s",
        "evidence_packet_sha256s",
        "validation_receipts",
        "call_ledger",
        "version",
        "assessment_sha256",
    }
    if type(data) is not dict or set(data) != expected_keys:
        raise ValueError("생성 생산 증거의 key 또는 객체 형식이 계약과 다릅니다")
    ledger_raw = data["call_ledger"]
    if type(ledger_raw) is not dict or set(ledger_raw) != {"records"}:
        raise ValueError("AI 호출 장부 JSON 모양이 깨졌습니다")
    records_raw = ledger_raw["records"]
    if type(records_raw) is not list:
        raise ValueError("AI 호출 장부 JSON 모양이 깨졌습니다")
    record_keys = {
        "sequence",
        "role",
        "role_index",
        "section_id",
        "prompt_sha256",
        "response_sha256",
        "outcome",
        "validation_round",
        "error_kind",
    }
    if any(type(item) is not dict or set(item) != record_keys for item in records_raw):
        raise ValueError("AI 호출 record의 key 또는 객체 형식이 계약과 다릅니다")
    records = tuple(
        GenerationCallRecord(
            sequence=item["sequence"],
            role=item["role"],
            role_index=item["role_index"],
            section_id=item["section_id"],
            prompt_sha256=item["prompt_sha256"],
            response_sha256=item["response_sha256"],
            outcome=item["outcome"],
            validation_round=ValidationRound(item["validation_round"]),
            error_kind=item["error_kind"],
        )
        for item in records_raw
    )
    assessment_raw = data["assessment"]
    if type(assessment_raw) is not dict:
        raise ValueError("생성 생산 증거의 assessment가 누락됐습니다")
    section_values = data["section_sha256s"]
    packet_values = data["evidence_packet_sha256s"]
    receipts_raw = data["validation_receipts"]
    if type(receipts_raw) is not list or any(
        type(item) is not dict for item in receipts_raw
    ):
        raise ValueError("생성 생산 증거의 검증 영수증 체인이 깨졌습니다")
    value = GenerationProducerEvidence(
        company_id=data["company_id"],
        evidence_generation_sha256=data["evidence_generation_sha256"],
        build_identity_sha256=data["build_identity_sha256"],
        candidate_sha256=data["candidate_sha256"],
        assessment=generation_assessment_from_dict(assessment_raw),
        public_manifest_sha256=data["public_manifest_sha256"],
        public_content_sha256=data["public_content_sha256"],
        section_sha256s=_tuple_pairs(section_values, integer_value=False),
        evidence_packet_sha256s=_tuple_pairs(packet_values, integer_value=False),
        validation_receipts=tuple(
            receipt_from_dict(item) for item in receipts_raw
        ),
        call_ledger=GenerationCallLedger(records),
        version=data["version"],
    )
    stored_assessment_sha256 = require_sha256(
        data.get("assessment_sha256"), label="저장된 GenerationAssessment 지문"
    )
    if stored_assessment_sha256 != value.assessment_sha256:
        raise ValueError("저장된 GenerationAssessment 지문이 평가 원본과 다릅니다")
    if producer_evidence_to_dict(value) != data:
        raise ValueError("생성 생산 증거가 canonical wire 왕복과 다릅니다")
    return value


def assert_canonical_producer_evidence(value: object) -> str:
    """release 직전 exact type·wire 왕복을 확인하고 transport 지문을 돌려준다."""

    if type(value) is not GenerationProducerEvidence:
        raise TypeError("정확한 GenerationProducerEvidence 객체가 필요합니다")
    payload = producer_evidence_to_dict(value)
    restored = producer_evidence_from_dict(payload)
    if type(restored) is not GenerationProducerEvidence or restored != value:
        raise ValueError("생성 생산 증거가 canonical wire 왕복과 다릅니다")
    return canonical_sha256(payload)


__all__ = [
    "GENERATION_PRODUCER_EVIDENCE_VERSION",
    "GenerationCallLedger",
    "GenerationCallRecord",
    "GenerationProducerEvidence",
    "GenerationRunMetrics",
    "assert_canonical_producer_evidence",
    "canonical_json",
    "canonical_sha256",
    "canonical_value",
    "exact_text_sha256",
    "generation_metrics_from_dict",
    "generation_metrics_to_dict",
    "producer_evidence_from_dict",
    "producer_evidence_to_dict",
    "require_sha256",
]
