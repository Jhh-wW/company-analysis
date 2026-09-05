"""수집 feature와 delivery feature가 공유하는 비민감 출처 지문 계약."""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final


_DART_RECEIPT_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{14}")
_DART_PRIMARY_KEYS: Final[tuple[str, ...]] = ("rcept_no", "rceptNo")
_DART_IDENTITY_LIST_KEYS: Final[tuple[str, ...]] = (
    "source_identity_rcept_nos",
    "source_identity_receipt_numbers",
)


class ReportSourceIdentityError(ValueError):
    """실제 DART 출처를 비민감 지문으로 확정할 수 없다."""


def _canonical_json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ReportSourceIdentityError(
            "재무 응답에는 유한한 JSON 값만 올 수 있습니다"
        ) from exc


def _normalized_json_value(value: Any, *, unordered_lists: bool) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ReportSourceIdentityError(
                "재무 응답에 NaN이나 무한대를 넣을 수 없습니다"
            )
        return value
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return {
            str(key).strip(): _normalized_json_value(
                item,
                unordered_lists=unordered_lists,
            )
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        items = [
            _normalized_json_value(item, unordered_lists=unordered_lists)
            for item in value
        ]
        if unordered_lists:
            items.sort(key=_canonical_json_bytes)
        return items
    raise ReportSourceIdentityError(
        f"재무 응답에 넣을 수 없는 값입니다: {type(value).__name__}"
    )


def normalize_financial_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    """DART 재무 응답을 키·행 순서와 가장자리 공백에 흔들리지 않게 만든다."""

    if not isinstance(payload, Mapping):
        raise TypeError("재무 응답은 JSON 객체여야 합니다")
    # message는 같은 status를 설명하는 문구라 자료값 신원에서만 제외한다.
    # 새 필드는 보수적으로 포함해, 불필요한 miss보다 정정 누락을 막는다.
    stable = {
        str(key).strip(): value
        for key, value in payload.items()
        if str(key).strip().lower() != "message"
    }
    normalized = _normalized_json_value(stable, unordered_lists=True)
    if not isinstance(normalized, dict):  # pragma: no cover - 위 입력 검사 방어선
        raise TypeError("재무 응답 정규화 결과가 객체가 아닙니다")
    return normalized


def financial_payload_digest(payload: Mapping[str, Any]) -> str:
    """정규화한 실제 DART 재무 응답 전체의 SHA-256."""

    normalized = normalize_financial_payload(payload)
    return hashlib.sha256(_canonical_json_bytes(normalized)).hexdigest()


def require_financial_payload_digest(value: str, *, allow_empty: bool) -> str:
    """원문 payload 대신 전달된 재무 SHA-256의 형식을 검산한다."""

    clean = str(value).strip()
    if allow_empty and not clean:
        return ""
    if len(clean) != 64 or any(char not in "0123456789abcdef" for char in clean):
        raise ReportSourceIdentityError("재무 응답 SHA-256 형식이 올바르지 않습니다")
    return clean


def normalize_dart_receipt_numbers(values: Sequence[str]) -> tuple[str, ...]:
    """DART 접수번호만 중복 없이 정렬하며 잘못된 값은 버리지 않고 막는다."""

    cleaned = tuple(str(value).strip() for value in values if str(value).strip())
    invalid = tuple(value for value in cleaned if _DART_RECEIPT_RE.fullmatch(value) is None)
    if invalid:
        raise ReportSourceIdentityError("DART 접수번호는 14자리 숫자여야 합니다")
    return tuple(sorted(set(cleaned)))


def dart_receipt_numbers_from_filing(
    filing: Mapping[str, Any] | None,
) -> tuple[str, ...]:
    """선택한 공시와 함께 확인한 정정공시 접수번호를 정확한 키에서만 뽑는다."""

    if filing is None:
        return ()
    if not isinstance(filing, Mapping):
        raise TypeError("DART 공시 정보는 JSON 객체여야 합니다")
    candidates: list[str] = []
    for key in _DART_PRIMARY_KEYS:
        value = filing.get(key)
        if value not in (None, ""):
            candidates.append(str(value))
    for key in _DART_IDENTITY_LIST_KEYS:
        value = filing.get(key)
        if value in (None, ""):
            continue
        if not isinstance(value, Sequence) or isinstance(
            value,
            (str, bytes, bytearray),
        ):
            raise ReportSourceIdentityError(
                "DART 정정공시 접수번호 묶음은 목록이어야 합니다"
            )
        candidates.extend(str(item) for item in value)
    return normalize_dart_receipt_numbers(candidates)


@dataclass(frozen=True)
class ReportSourceIdentity:
    """원문·재무값을 싣지 않고 completion까지 전달하는 출처 신원."""

    dart_receipt_numbers: tuple[str, ...] = ()
    financial_payload_digest: str = ""

    def __post_init__(self) -> None:
        if self.dart_receipt_numbers != normalize_dart_receipt_numbers(
            self.dart_receipt_numbers
        ):
            raise ReportSourceIdentityError(
                "DART 접수번호가 중복 제거·정렬되지 않았습니다"
            )
        require_financial_payload_digest(
            self.financial_payload_digest,
            allow_empty=True,
        )

    @property
    def cache_usable(self) -> bool:
        return bool(self.dart_receipt_numbers and self.financial_payload_digest)

    @property
    def cache_digest(self) -> str:
        """공시 접수번호와 재무 응답을 한 번에 비교하는 캐시 전용 SHA-256.

        둘 중 하나라도 모르면 빈 문자열을 돌려준다. 부분 신원을 완전한 신원처럼
        캐시 열쇠로 쓰면, 정정공시나 재무값 변경을 놓칠 수 있기 때문이다.
        """

        if not self.cache_usable:
            return ""
        payload = {
            "dart_receipt_numbers": list(self.dart_receipt_numbers),
            "financial_payload_digest": self.financial_payload_digest,
        }
        return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()

    def cache_digest_with_official_snapshot(
        self,
        official_snapshot_sha256: str,
    ) -> str:
        """DART 기본 신원과 이번 공식 자료 전체를 묶은 생성 캐시 신원.

        예전 캐시 열쇠는 최신 공시 접수번호와 재무 API 응답만 보았다. 회사
        홈페이지·공식 IR·typed DART 보조 문서가 바뀌어도 최대 캐시 수명 동안
        옛 보고서를 돌려줄 수 있었다. FULL 생성은 실제로 작가에게 건넨 공식
        자료 snapshot까지 같은 열쇠에 넣어야 한다.

        둘 중 하나라도 확정할 수 없으면 빈 문자열을 돌려 캐시 재사용을 막는다.
        부분 신원을 완전한 신원처럼 쓰는 것보다 다시 만드는 편이 안전하다.
        """

        base_digest = self.cache_digest
        clean_snapshot = str(official_snapshot_sha256 or "").strip()
        try:
            require_financial_payload_digest(clean_snapshot, allow_empty=False)
        except ReportSourceIdentityError:
            return ""
        if not base_digest:
            return ""
        payload = {
            "version": 1,
            "dart_financial_snapshot_sha256": base_digest,
            "official_evidence_snapshot_sha256": clean_snapshot,
        }
        return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()

    def generation_digest_without_financials(
        self,
        official_snapshot_sha256: str,
    ) -> str:
        """재무 API 자료가 «없음»으로 확정된 회사의 생성 신원.

        감사보고서만 내는 비상장사는 DART 주요계정 API가 세 사업연도 모두
        «조회된 데이터 없음(013)»을 돌려준다(2026-09-05 인이지 실측). 그러면
        ``financial_payload_digest``가 비어 ``cache_digest_with_official_snapshot``
        이 빈 문자열을 돌리고, 운영은 이를 내부 계약 실패로 읽어 AI 작성 전에
        멈췄다. 재무 자료가 없는 것은 회사 자료의 실제 상태이므로 공식 접수번호와
        공식 자료 snapshot만으로 생성 신원을 세운다.

        캐시 재사용 열쇠(``cache_digest``·``cache_usable``)는 그대로 비워 둔다 —
        재무 도장 없는 신원으로 옛 보고서를 돌려주지 않기 위해서다. 이 지문은
        생성 조정(single-flight)과 저장 신원에만 쓴다.

        재무 도장이 «있는» 신원에는 쓰지 않는다(빈 문자열). 그 경우는
        ``cache_digest_with_official_snapshot``가 정본이다.
        """

        if self.financial_payload_digest:
            return ""
        if not self.dart_receipt_numbers:
            return ""
        clean_snapshot = str(official_snapshot_sha256 or "").strip()
        try:
            require_financial_payload_digest(clean_snapshot, allow_empty=False)
        except ReportSourceIdentityError:
            return ""
        payload = {
            "version": 1,
            "dart_receipt_numbers": list(self.dart_receipt_numbers),
            "financial_payload_state": "absent",
            "official_evidence_snapshot_sha256": clean_snapshot,
        }
        return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()

    @classmethod
    def capture(
        cls,
        *,
        filing: Mapping[str, Any] | None,
        financial_payload: Mapping[str, Any] | None,
    ) -> "ReportSourceIdentity":
        return cls(
            dart_receipt_numbers=dart_receipt_numbers_from_filing(filing),
            financial_payload_digest=(
                financial_payload_digest(financial_payload)
                if financial_payload is not None
                else ""
            ),
        )
