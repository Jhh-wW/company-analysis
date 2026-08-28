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
