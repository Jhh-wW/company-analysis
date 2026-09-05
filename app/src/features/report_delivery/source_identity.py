"""DART 접수번호와 실제 재무 응답값으로 출처 신원을 만든다."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from src.shared.report_source_identity import (
    ReportSourceIdentity,
    financial_payload_digest,
    normalize_dart_receipt_numbers,
    normalize_financial_payload,
    require_financial_payload_digest,
)
from src.features.report_delivery.canonical import (
    canonical_digest,
    canonical_json_bytes,
    require_aware,
    require_sha256_hex,
    utc_text,
)


__all__ = (
    "SourceSnapshot",
    "financial_payload_digest",
    "normalize_financial_payload",
)


def _clean_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({str(value).strip() for value in values if str(value).strip()}))


def _identity_digest(
    *,
    dart_receipt_nos: tuple[str, ...],
    financial_payload_sha256: str,
    official_document_ids: tuple[str, ...],
    adapter_versions: tuple[tuple[str, str], ...],
    preflight_identity_digest: str,
) -> str:
    payload: dict[str, object] = {
        "dart_receipt_nos": dart_receipt_nos,
        "financial_payload_sha256": financial_payload_sha256,
        "official_document_ids": official_document_ids,
        "adapter_versions": adapter_versions,
    }
    # 옛 snapshot은 이 열쇠 자체가 없었다. 빈 값을 payload에 새로 넣으면
    # 기존 불변 ID를 전부 바꾸므로, 새 FULL 경로의 정확한 사전 지문이 있을
    # 때만 확장한다. 그러면 옛 행은 읽되 새 cache 권위로 승격하지 않는다.
    if preflight_identity_digest:
        payload["preflight_identity_digest"] = preflight_identity_digest
    return canonical_digest(payload)


def _snapshot_id(
    *,
    identity_digest: str,
    captured_at: dt.datetime,
    source_as_of: dt.date,
) -> str:
    return "source_" + canonical_digest(
        {
            "identity_digest": identity_digest,
            "captured_at": utc_text(captured_at, label="출처 확인"),
            "source_as_of": source_as_of.isoformat(),
        }
    )


@dataclass(frozen=True)
class SourceSnapshot:
    """생성을 마친 뒤 실제로 사용한 전체 출처 신원과 확인 시각.

    ``identity_digest``에는 DART·재무뿐 아니라 생성 중 확인한 공식 문서와
    adapter 버전도 들어간다. 따라서 이것은 감사 가능한 post-generation
    provenance(생성 뒤 원본 계보)이며, 생성 전 cache 조회 열쇠가 아니다.
    조회에는 ``CacheLookupKey.preflight_identity_digest``를 별도로 쓴다.
    ``snapshot_id``는 같은 값을 언제 확인했는지까지 포함하므로 같은 출처를
    다음 날 다시 확인한 두 관측을 같은 사건이라고 거짓말하지 않는다.
    """

    snapshot_id: str
    identity_digest: str
    captured_at: dt.datetime
    source_as_of: dt.date
    dart_receipt_nos: tuple[str, ...]
    financial_payload_sha256: str
    official_document_ids: tuple[str, ...]
    adapter_versions: tuple[tuple[str, str], ...]
    cache_usable: bool
    #: pipeline이 cache 조회·single-flight owner 선정에 실제로 쓴 정확한 지문.
    #: FULL에서는 DART·재무뿐 아니라 공식 웹/IR snapshot까지 결합된 값이다.
    preflight_identity_digest: str = ""

    def __post_init__(self) -> None:
        require_aware(self.captured_at, label="출처 확인")
        if not isinstance(self.source_as_of, dt.date):
            raise TypeError("자료 기준일은 날짜여야 합니다")
        if self.dart_receipt_nos != normalize_dart_receipt_numbers(
            self.dart_receipt_nos
        ):
            raise ValueError("DART 접수번호 지문이 정규화되지 않았습니다")
        if self.official_document_ids != _clean_unique(self.official_document_ids):
            raise ValueError("공식 문서 지문이 정규화되지 않았습니다")
        clean_versions = tuple(
            sorted(
                (str(name).strip(), str(version).strip())
                for name, version in self.adapter_versions
                if str(name).strip() and str(version).strip()
            )
        )
        if self.adapter_versions != clean_versions:
            raise ValueError("수집 adapter 버전 지문이 정규화되지 않았습니다")
        finance_digest = require_sha256_hex(
            self.financial_payload_sha256,
            label="재무 응답",
            allow_empty=True,
        )
        preflight_digest = require_sha256_hex(
            self.preflight_identity_digest,
            label="생성 전 출처",
            allow_empty=True,
        )
        # ★ 재무 도장은 없어도 된다 — 감사보고서만 내는 비상장사는 DART 재무 API가
        #   «자료 없음(013)»을 답해 finance_digest가 비고, pipeline은 접수번호+공식
        #   snapshot만으로 생성 전 지문을 만든다(ReportSourceIdentity.
        #   generation_digest_without_financials, 2026-09-05 인이지 실측). 그 지문을
        #   여기서 거절하면 AI 비용을 다 쓴 뒤 출고 직전에 실패한다(실측 사고).
        #   cache_usable은 아래에서 여전히 receipts·finance 둘 다 있을 때만 True다.
        if preflight_digest and not self.dart_receipt_nos:
            raise ValueError("DART 접수번호 없이 생성 전 출처 지문을 저장할 수 없습니다")
        expected_identity = _identity_digest(
            dart_receipt_nos=self.dart_receipt_nos,
            financial_payload_sha256=finance_digest,
            official_document_ids=self.official_document_ids,
            adapter_versions=self.adapter_versions,
            preflight_identity_digest=preflight_digest,
        )
        if self.identity_digest != expected_identity:
            raise ValueError("저장된 출처 신원 지문이 자료와 맞지 않습니다")
        expected_snapshot_id = _snapshot_id(
            identity_digest=expected_identity,
            captured_at=self.captured_at,
            source_as_of=self.source_as_of,
        )
        if self.snapshot_id != expected_snapshot_id:
            raise ValueError("저장된 출처 snapshot ID가 자료와 맞지 않습니다")
        expected_cache_usable = bool(self.dart_receipt_nos and finance_digest)
        if self.cache_usable is not expected_cache_usable:
            raise ValueError("출처 확인 상태와 캐시 사용 표시가 맞지 않습니다")

    @classmethod
    def capture(
        cls,
        *,
        dart_receipt_nos: Sequence[str],
        financial_payload: Mapping[str, Any] | None,
        financial_payload_sha256: str = "",
        captured_at: dt.datetime,
        source_as_of: dt.date,
        official_document_ids: Sequence[str] = (),
        adapter_versions: Mapping[str, str] | None = None,
        preflight_identity_digest: str = "",
    ) -> "SourceSnapshot":
        captured = require_aware(captured_at, label="출처 확인")
        if not isinstance(source_as_of, dt.date):
            raise TypeError("자료 기준일은 날짜여야 합니다")
        receipts = normalize_dart_receipt_numbers(dart_receipt_nos)
        documents = _clean_unique(official_document_ids)
        versions = tuple(
            sorted(
                (
                    str(name).strip(),
                    str(version).strip(),
                )
                for name, version in (adapter_versions or {}).items()
                if str(name).strip() and str(version).strip()
            )
        )
        supplied_digest = require_financial_payload_digest(
            financial_payload_sha256,
            allow_empty=True,
        )
        if financial_payload is not None and supplied_digest:
            raise ValueError("재무 원문과 미리 계산한 지문을 동시에 넣을 수 없습니다")
        finance_digest = (
            financial_payload_digest(financial_payload)
            if financial_payload is not None
            else supplied_digest
        )
        raw_preflight_digest = str(preflight_identity_digest).strip()
        preflight_digest = require_sha256_hex(
            (
                raw_preflight_digest
                or ReportSourceIdentity(
                    dart_receipt_numbers=receipts,
                    financial_payload_digest=finance_digest,
                ).cache_digest
            ),
            label="생성 전 출처",
            allow_empty=True,
        )
        # 재무 도장이 없는 회사(DART 재무 API 013)도 접수번호가 있으면 생성 전
        # 지문을 그대로 봉인한다. 캐시 재사용은 cache_usable=False로 계속 막힌다.
        # (__post_init__의 같은 규칙과 한 쌍 — 한쪽만 고치면 capture는 통과하고
        #  저장 복원에서 다시 떨어진다.)
        if preflight_digest and not receipts:
            raise ValueError("DART 접수번호 없이 생성 전 출처 지문을 저장할 수 없습니다")
        identity_digest = _identity_digest(
            dart_receipt_nos=receipts,
            financial_payload_sha256=finance_digest,
            official_document_ids=documents,
            adapter_versions=versions,
            preflight_identity_digest=preflight_digest,
        )
        snapshot_id = _snapshot_id(
            identity_digest=identity_digest,
            captured_at=captured,
            source_as_of=source_as_of,
        )
        # 최신 DART 문서와 재무 응답 중 하나라도 확인하지 못한 상태를
        # 캐시에서 "같다"고 추정하지 않는다.
        cache_usable = bool(receipts and finance_digest)
        return cls(
            snapshot_id=snapshot_id,
            identity_digest=identity_digest,
            captured_at=captured,
            source_as_of=source_as_of,
            dart_receipt_nos=receipts,
            financial_payload_sha256=finance_digest,
            official_document_ids=documents,
            adapter_versions=versions,
            preflight_identity_digest=preflight_digest,
            cache_usable=cache_usable,
        )

    def identity_json(self) -> bytes:
        """감사·저장 비교에 쓸 원문 없는 신원 JSON."""

        payload: dict[str, object] = {
            "dart_receipt_nos": self.dart_receipt_nos,
            "financial_payload_sha256": self.financial_payload_sha256,
            "official_document_ids": self.official_document_ids,
            "adapter_versions": self.adapter_versions,
        }
        if self.preflight_identity_digest:
            payload["preflight_identity_digest"] = self.preflight_identity_digest
        return canonical_json_bytes(payload)
