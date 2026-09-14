"""문서 순회 완료와 선택 보관 상태의 버전 있는 계약."""
from __future__ import annotations

import re
from dataclasses import dataclass

DOCUMENT_SCAN_VERSION = "document_scan/1"
DOCUMENT_SCAN_COMPLETE = "COMPLETE"
DOCUMENT_SCAN_INCOMPLETE = "INCOMPLETE"


@dataclass(frozen=True)
class DocumentScan:
    version: str
    document_id: str
    content_sha256: str
    state: str
    total_chars: int
    scanned_chars: int
    candidates_seen: int
    candidates_retained: int
    unclassified_seen: int
    unclassified_retained: int
    selection_compressed: bool
    line_index_saturated: bool
    windowed_paragraphs: bool

    def __post_init__(self) -> None:
        if self.version != DOCUMENT_SCAN_VERSION:
            raise ValueError("지원하지 않는 문서 순회 계약 버전입니다")
        if not isinstance(self.document_id, str) or not self.document_id.strip():
            raise ValueError("문서 순회 기록에 문서 식별자가 필요합니다")
        if not isinstance(self.content_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", self.content_sha256) is None:
            raise ValueError("문서 순회 기록의 원문 해시가 올바르지 않습니다")
        if self.state not in {DOCUMENT_SCAN_COMPLETE, DOCUMENT_SCAN_INCOMPLETE}:
            raise ValueError("문서 순회 상태가 올바르지 않습니다")
        for value in (self.total_chars, self.scanned_chars, self.candidates_seen,
                      self.candidates_retained, self.unclassified_seen, self.unclassified_retained):
            if type(value) is not int or value < 0:
                raise ValueError("문서 순회 계수는 0 이상의 정수여야 합니다")
        for value in (self.selection_compressed, self.line_index_saturated, self.windowed_paragraphs):
            if type(value) is not bool:
                raise ValueError("문서 순회 선택 표식은 불리언이어야 합니다")
        if self.scanned_chars > self.total_chars:
            raise ValueError("검사 좌표가 원문 길이를 넘었습니다")
        if self.state == DOCUMENT_SCAN_COMPLETE and self.scanned_chars != self.total_chars:
            raise ValueError("미검사 꼬리가 있는 원문을 완료로 기록할 수 없습니다")
        if not (self.unclassified_retained <= self.unclassified_seen <= self.candidates_seen
                and self.unclassified_retained <= self.candidates_retained <= self.candidates_seen):
            raise ValueError("문서 순회 관측·보관 계수가 서로 맞지 않습니다")
