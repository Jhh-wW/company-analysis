"""수집기 결과(계약 인스턴스 또는 매핑)를 근거 계약 dataclass로 바꾼다.

수집기(DART·공식 웹 담당)는 이 트리 밖 별도 워크트리에서 작업 중이라 그
모듈을 import할 수 없고 해서도 안 된다. 그래서 계약 인스턴스든, 같은
필드 이름을 가진 평범한 Mapping(dict 등)이든 받아들이는 것이 결합점이다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import TypeVar

from src.features.chapter_evidence.constants import CompanyType
from src.shared.report_evidence.constants import (
    CollectionState,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.models import (
    CollectedEvidenceDocument,
    CollectionAttempt,
    DocumentTextRange,
    EvidenceFragment,
)


_EnumT = TypeVar("_EnumT")


def _coerce_enum(enum_cls: type[_EnumT], value: object, *, label: str) -> _EnumT:
    if isinstance(value, enum_cls):
        return value
    if isinstance(value, str):
        try:
            return enum_cls(value)  # type: ignore[call-arg]
        except ValueError as error:
            raise ValueError(f"{label} 값이 올바르지 않습니다") from error
    raise ValueError(f"{label} 값이 올바르지 않습니다")


def _coerce_str(value: object, *, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} 값은 문자열이어야 합니다")
    return value


def _coerce_int(value: object, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label} 값은 정수여야 합니다")
    return value


def _coerce_str_tuple(value: object, *, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{label} 목록 형식이 올바르지 않습니다")
    return tuple(_coerce_str(item, label=label) for item in value)


def _coerce_ranges(value: object) -> tuple[DocumentTextRange, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("문서 본문 구간 목록 형식이 올바르지 않습니다")
    ranges: list[DocumentTextRange] = []
    for item in value:
        if isinstance(item, DocumentTextRange):
            ranges.append(item)
        elif isinstance(item, Mapping):
            try:
                ranges.append(
                    DocumentTextRange(start=int(item["start"]), end=int(item["end"]))
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("문서 본문 구간 항목 형식이 올바르지 않습니다") from error
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            try:
                ranges.append(DocumentTextRange(start=int(item[0]), end=int(item[1])))
            except (TypeError, ValueError) as error:
                raise ValueError("문서 본문 구간 항목 형식이 올바르지 않습니다") from error
        else:
            raise ValueError("문서 본문 구간 항목 형식이 올바르지 않습니다")
    return tuple(ranges)


def to_document(
    value: CollectedEvidenceDocument | Mapping[str, object],
) -> CollectedEvidenceDocument:
    """계약 인스턴스이거나 같은 필드 이름의 매핑인 문서를 계약형으로 바꾼다."""

    if isinstance(value, CollectedEvidenceDocument):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("수집 문서는 계약 인스턴스이거나 매핑이어야 합니다")
    try:
        return CollectedEvidenceDocument(
            company_id=_coerce_str(value["company_id"], label="회사 식별자"),
            document_id=_coerce_str(value["document_id"], label="문서 식별자"),
            canonical_url=_coerce_str(value["canonical_url"], label="정규 URL"),
            source_tier=_coerce_enum(
                SourceTier, value["source_tier"], label="문서 출처 등급"
            ),
            source_kind=_coerce_str(value["source_kind"], label="출처 종류"),
            publisher=_coerce_str(value["publisher"], label="발행자"),
            title=_coerce_str(value["title"], label="문서 제목"),
            published_on=_coerce_str(value["published_on"], label="발행일"),
            collected_at=_coerce_str(value["collected_at"], label="수집 시각"),
            content_sha256=_coerce_str(value["content_sha256"], label="문서 내용 해시"),
            exact_evidence_hashes=_coerce_str_tuple(
                value["exact_evidence_hashes"], label="문서의 정확한 근거 조각 해시"
            ),
            identity_binding=_coerce_str(
                value["identity_binding"], label="회사 결속 근거"
            ),
            usable_ranges=_coerce_ranges(value["usable_ranges"]),
            collector_version=_coerce_str(
                value["collector_version"], label="수집기 버전"
            ),
            parser_version=_coerce_str(value["parser_version"], label="파서 버전"),
            requirement=_coerce_enum(
                SourceRequirement, value["requirement"], label="문서 필수 등급"
            ),
        )
    except KeyError as error:
        raise ValueError(f"수집 문서에 필수 항목이 빠졌습니다: {error}") from error


def to_fragment(
    value: EvidenceFragment | Mapping[str, object],
) -> EvidenceFragment:
    """계약 인스턴스이거나 같은 필드 이름의 매핑인 근거 조각을 계약형으로 바꾼다."""

    if isinstance(value, EvidenceFragment):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("근거 조각은 계약 인스턴스이거나 매핑이어야 합니다")
    try:
        return EvidenceFragment(
            company_id=_coerce_str(value["company_id"], label="회사 식별자"),
            fragment_id=_coerce_str(value["fragment_id"], label="근거 조각 식별자"),
            document_id=_coerce_str(value["document_id"], label="원본 문서 식별자"),
            location=_coerce_str(value["location"], label="원문 위치"),
            text_sha256=_coerce_str(value["text_sha256"], label="근거 원문 해시"),
            text=_coerce_str(value["text"], label="근거 원문"),
            section_id=_coerce_str(value["section_id"], label="장 식별자"),
            slot_id=_coerce_str(value["slot_id"], label="의미 칸 식별자"),
            score_millis=_coerce_int(value["score_millis"], label="근거 선택 점수"),
            reason_codes=_coerce_str_tuple(
                value["reason_codes"], label="근거 선택 사유 코드"
            ),
            period_start=_coerce_str(
                value.get("period_start", ""), label="기간 시작"
            ),
            period_end=_coerce_str(value.get("period_end", ""), label="기간 끝"),
            unit=_coerce_str(value.get("unit", ""), label="단위"),
            company_scope=_coerce_str(
                value.get("company_scope", ""), label="회사 범위"
            ),
            covered_slot_ids=_coerce_str_tuple(
                value.get("covered_slot_ids", (value["slot_id"],)),
                label="근거 조각이 채우는 의미 칸",
            ),
        )
    except KeyError as error:
        raise ValueError(f"근거 조각에 필수 항목이 빠졌습니다: {error}") from error


def to_attempt(
    value: CollectionAttempt | Mapping[str, object],
) -> CollectionAttempt:
    """계약 인스턴스이거나 같은 필드 이름의 매핑인 조회 기록을 계약형으로 바꾼다."""

    if isinstance(value, CollectionAttempt):
        return value
    if not isinstance(value, Mapping):
        raise ValueError("수집 시도 기록은 계약 인스턴스이거나 매핑이어야 합니다")
    try:
        return CollectionAttempt(
            company_id=_coerce_str(value["company_id"], label="회사 식별자"),
            attempt_id=_coerce_str(value["attempt_id"], label="수집 시도 식별자"),
            source_kind=_coerce_str(value["source_kind"], label="수집 출처 종류"),
            requirement=_coerce_enum(
                SourceRequirement, value["requirement"], label="수집 시도 필수 등급"
            ),
            state=_coerce_enum(CollectionState, value["state"], label="수집 결과 상태"),
            slot_ids=_coerce_str_tuple(value["slot_ids"], label="수집 대상 의미 칸"),
            reason_code=_coerce_str(value["reason_code"], label="수집 결과 사유 코드"),
            elapsed_ms=_coerce_int(value.get("elapsed_ms", 0), label="수집 소요 시간"),
            bytes_downloaded=_coerce_int(
                value.get("bytes_downloaded", 0), label="수집 바이트 수"
            ),
            documents_seen=_coerce_int(
                value.get("documents_seen", 0), label="확인한 문서 수"
            ),
        )
    except KeyError as error:
        raise ValueError(f"수집 시도 기록에 필수 항목이 빠졌습니다: {error}") from error


def normalize_documents(
    values: Iterable[CollectedEvidenceDocument | Mapping[str, object]],
) -> tuple[CollectedEvidenceDocument, ...]:
    return tuple(to_document(value) for value in values)


def normalize_fragments(
    values: Iterable[EvidenceFragment | Mapping[str, object]],
) -> tuple[EvidenceFragment, ...]:
    return tuple(to_fragment(value) for value in values)


def normalize_attempts(
    values: Iterable[CollectionAttempt | Mapping[str, object]],
) -> tuple[CollectionAttempt, ...]:
    return tuple(to_attempt(value) for value in values)


def normalize_company_type(value: CompanyType | str) -> CompanyType:
    """문자열이든 이미 열거형이든 회사 유형을 받아들인다."""

    if isinstance(value, CompanyType):
        return value
    if isinstance(value, str):
        try:
            return CompanyType(value)
        except ValueError as error:
            raise ValueError(f"알 수 없는 회사 유형입니다: {value!r}") from error
    raise ValueError("회사 유형 값이 올바르지 않습니다")
