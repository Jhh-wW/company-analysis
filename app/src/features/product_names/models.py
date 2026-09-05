"""DART 원문에 글자 그대로 고정된 이름 후보 모델."""

from __future__ import annotations

from dataclasses import dataclass

from .constants import SUBJECT_KINDS


@dataclass(frozen=True, slots=True)
class NameCandidate:
    name: str
    subject_kind: str
    description: str
    source_kind: str
    location: str
    excerpt: str
    excerpt_sha256: str

    def __post_init__(self) -> None:
        if self.subject_kind not in SUBJECT_KINDS:
            raise ValueError(f"허용하지 않는 이름 후보 종류입니다: {self.subject_kind!r}")


__all__ = ["NameCandidate"]

