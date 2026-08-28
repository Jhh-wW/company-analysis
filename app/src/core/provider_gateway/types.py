"""provider별 adapter가 공유하는 비민감 관측 DTO."""

from __future__ import annotations

import math
import string
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, TypeVar


class TransportState(str, Enum):
    RESPONSE_RECEIVED = "RESPONSE_RECEIVED"
    TRANSPORT_AMBIGUOUS = "TRANSPORT_AMBIGUOUS"
    LOCAL_FAILURE = "LOCAL_FAILURE"


class BillingDisposition(str, Enum):
    KNOWN_COST = "KNOWN_COST"
    CONSERVATIVE_LIABILITY = "CONSERVATIVE_LIABILITY"
    KNOWN_ZERO = "KNOWN_ZERO"


_SAFE_CHARS = frozenset(string.ascii_letters + string.digits + "_.:-")


def _safe(value: str, *, maximum: int) -> bool:
    return (
        type(value) is str
        and len(value) <= maximum
        and all(character in _SAFE_CHARS for character in value)
    )


@dataclass(frozen=True)
class ProviderObservation:
    """본문·prompt·API key를 담을 칸이 없는 provider 결과 메타데이터."""

    transport_state: TransportState
    billing_disposition: BillingDisposition
    known_cost_krw: float
    liability_krw: float
    status_code: int | None
    error_type: str
    request_id: str

    def __post_init__(self) -> None:
        known = float(self.known_cost_krw)
        liability = float(self.liability_krw)
        if (
            not math.isfinite(known)
            or not math.isfinite(liability)
            or known < 0
            or liability < 0
        ):
            raise ValueError("provider 비용 관측값이 올바르지 않습니다")
        if self.billing_disposition is BillingDisposition.KNOWN_COST:
            if liability != 0:
                raise ValueError("확정비용과 보수부채를 한 관측에 함께 적을 수 없습니다")
        elif self.billing_disposition is BillingDisposition.CONSERVATIVE_LIABILITY:
            if known != 0 or liability <= 0:
                raise ValueError("provider 보수부채 관측값이 올바르지 않습니다")
        elif known != 0 or liability != 0:
            raise ValueError("known-zero 관측에 비용을 적을 수 없습니다")
        if self.status_code is not None and not 100 <= int(self.status_code) <= 599:
            raise ValueError("provider HTTP 상태가 올바르지 않습니다")
        if not _safe(self.error_type, maximum=128) or not _safe(
            self.request_id, maximum=128
        ):
            raise ValueError("provider 비민감 식별값 형식이 올바르지 않습니다")


ResponseT = TypeVar("ResponseT")


class ProviderAdapter(Protocol[ResponseT]):
    """provider feature를 budget에 직접 import하지 않게 하는 주입 경계."""

    def success(
        self, response: ResponseT, *, reserved_krw: float
    ) -> ProviderObservation: ...

    def failure(
        self, error: Exception, *, reserved_krw: float
    ) -> ProviderObservation: ...
