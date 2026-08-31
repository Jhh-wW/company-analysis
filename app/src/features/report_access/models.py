"""새 보고서의 열람 소유권과 DB 결속 결과 계약."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ReportAudience(Enum):
    """작업 입장에서 한 번 고정하는 보고서 열람 소유권."""

    PUBLIC = "public"
    MEMBER = "member"
    LINK = "link"
    ADMIN = "admin"


@dataclass(frozen=True)
class ReportBindingResult:
    """DB에서 확인한 정확한 audience와 결속 여부."""

    audience: ReportAudience
    bound: bool

    def __post_init__(self) -> None:
        if type(self.audience) is not ReportAudience:
            raise TypeError("보고서 결속 audience는 닫힌 Enum이어야 합니다")
        if type(self.bound) is not bool:
            raise TypeError("보고서 결속 여부는 bool이어야 합니다")

    def __bool__(self) -> bool:
        raise TypeError("보고서 결속은 audience와 bound를 명시적으로 검사해야 합니다")
