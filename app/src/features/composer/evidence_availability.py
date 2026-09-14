"""파이프라인이 확인한 «자료 확보 상태»를 작성기가 안내로만 쓰는 계약.

사용자 계약(2026-09-14): 분석 대상 회사라면 자료가 많든 적든 확보한 내용으로
보고서가 나와야 한다. 이 모듈은 그 계약의 «입력 쪽 절반»이다 — 파이프라인이
수집·사전검사에서 알아낸 «무엇을 확인하지 못했는가»를 닫힌 값으로 건네고,
작성기는 그 값을 (1) 등급·공개 정책 (2) 사람이 읽는 확인 범위 안내로만 바꾼다.

★ 이 값은 근거가 아니다. 어떤 필드도 본문 문장·표·도식의 재료가 되지 않으며,
  원문 검증·인용 결속을 느슨하게 만들지도 않는다. 바뀌는 것은 «수량 하한
  때문에 전체를 막지 않는다»는 출고 판단뿐이다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

COLLECTION_STATE_COMPLETE: Final[str] = "complete"
COLLECTION_STATE_PARTIAL: Final[str] = "partial"
COLLECTION_STATE_NONE: Final[str] = "none"
VALID_COLLECTION_STATES: Final[frozenset[str]] = frozenset(
    {COLLECTION_STATE_COMPLETE, COLLECTION_STATE_PARTIAL, COLLECTION_STATE_NONE}
)

#: 미확인 범위의 닫힌 종류 코드 모양 — 진단 기록용이며 화면에 노출하지 않는다.
_SCOPE_KIND_SHAPE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9_.:-]{1,120}$")
#: 화면 글자 «전체»가 영문 snake_case 토큰이면 v2 출고 검증(내부 키 노출)에
#: 걸린다. 안내문은 그 모양이면 안 된다.
_INTERNAL_KEY_SHAPE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*$")
_MAX_LABEL_CHARS: Final[int] = 200

#: 수집 시도의 출처 종류 → 독자가 읽는 자료 이름. 엔진·앱 수집기의 닫힌
#: source_kind 어휘(shared/report_evidence/constants.py)를 그대로 따른다.
SOURCE_KIND_LABELS: Final[dict[str, str]] = {
    "dart_business_report": "사업보고서",
    "dart_audit_report": "감사보고서",
    "dart_consolidated_audit_report": "연결감사보고서",
    "dart_semiannual_report": "반기보고서",
    "dart_quarterly_report": "분기보고서",
    "official_web_page": "회사 홈페이지",
    "official_recruit_page": "회사 채용 페이지",
    "official_ir_pdf": "회사 IR 자료",
    "official_identity_verified_web_page": "회사 관련 웹 페이지",
    "robots_txt": "회사 홈페이지 접근 규칙",
    "news": "언론 보도",
    "dart_financial_api": "전자공시 재무 정보",
}

#: 수집 상태 → «어떻게 못 봤는가». OK·MISSING은 확인을 마친 상태라 여기 없다.
COLLECTION_STATE_PHRASES: Final[dict[str, str]] = {
    "TRUNCATED": "일부만 확인했습니다",
    "FAILED": "확인하지 못했습니다",
}

#: 수집 사유 코드 → 짧은 원인 설명. 목록에 없는 코드는 원인을 지어내지 않고
#: 생략한다(코드 자체를 화면에 쓰면 내부 키 노출이 된다).
REASON_CODE_PHRASES: Final[dict[str, str]] = {
    "deadline_exceeded": "처리 시간 한도",
    "document_too_large": "문서 크기 한도",
    "total_bytes_exceeded": "전체 문서 크기 한도",
    "document_fetch_failed": "원문 내려받기 실패",
    "list_query_failed": "공시 목록 조회 실패",
    "document_section_count_exceeded": "문서 처리 한도",
    "document_fragment_count_exceeded": "문서 처리 한도",
    "document_fragment_chars_exceeded": "문서 처리 한도",
    "document_line_index_exceeded": "문서 처리 한도",
    "document_selection_compressed": "문서 처리 한도",
    "robots_disallowed": "회사 홈페이지의 접근 제한",
    "robots_unreachable": "회사 홈페이지 접근 확인 실패",
    "network_failed": "네트워크 오류",
    "truncated_page_cap": "페이지 수 한도",
    "truncated_time_cap": "처리 시간 한도",
    "truncated_byte_cap": "용량 한도",
    "filing_receipt_date_invalid": "공시 접수일 형식 오류",
}

#: 상태별 첫 줄. 사용자 계약의 표 그대로 — 자료가 없다고 단정하지 않는다.
COVERAGE_LINE_COMPLETE: Final[str] = (
    "공식 자료 확인을 끝냈고, 확인된 범위 안의 내용만 담았습니다."
)
COVERAGE_LINE_PARTIAL: Final[str] = (
    "공식 자료 중 일부는 확인하지 못했습니다. 확인한 자료로만 작성했으며, "
    "확인하지 못한 범위는 아래에 적었습니다."
)
COVERAGE_LINE_NONE: Final[str] = (
    "회사 신원은 확인했지만 이번 조사에서 추가 공식 자료를 확보하지 "
    "못했습니다. 회사 기본 정보와 자료 확인 상태만 담았습니다."
)
COVERAGE_SCOPE_PREFIX: Final[str] = "확인하지 못한 범위: "


@dataclass(frozen=True)
class UnverifiedScope:
    """확인하지 못한 자료 범위 하나 — 닫힌 종류 코드와 독자용 안내."""

    #: 진단·저장용 닫힌 코드(예: ``dart_business_report:TRUNCATED``). 화면에 안 쓴다.
    kind: str
    #: 독자가 읽는 한국어 안내. 원문·URL·예외문을 담지 않는다.
    label: str

    def __post_init__(self) -> None:
        kind = str(self.kind or "").strip()
        label = " ".join(str(self.label or "").split())
        if _SCOPE_KIND_SHAPE.fullmatch(kind) is None:
            raise ValueError("미확인 범위 종류 코드는 영문 소문자·숫자·구분 기호여야 합니다")
        if not label or len(label) > _MAX_LABEL_CHARS:
            raise ValueError("미확인 범위 안내는 비울 수 없고 200자를 넘을 수 없습니다")
        if _INTERNAL_KEY_SHAPE.fullmatch(label):
            raise ValueError("미확인 범위 안내가 내부 키 모양이라 화면에 실을 수 없습니다")
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "label", label)


@dataclass(frozen=True)
class EvidenceAvailability:
    """파이프라인이 넘기는 자료 확보 상태. 작성기는 등급·안내에만 쓴다."""

    collection_state: str
    unverified_scopes: tuple[UnverifiedScope, ...] = ()

    def __post_init__(self) -> None:
        state = str(self.collection_state or "").strip()
        if state not in VALID_COLLECTION_STATES:
            raise ValueError(
                "자료 확보 상태는 complete·partial·none 중 하나여야 합니다"
            )
        scopes: list[UnverifiedScope] = []
        seen: set[tuple[str, str]] = set()
        for scope in self.unverified_scopes:
            if not isinstance(scope, UnverifiedScope):
                raise ValueError("미확인 범위는 UnverifiedScope여야 합니다")
            key = (scope.kind, scope.label)
            if key in seen:
                continue
            seen.add(key)
            scopes.append(scope)
        object.__setattr__(self, "collection_state", state)
        object.__setattr__(self, "unverified_scopes", tuple(scopes))

    @property
    def coverage_lines(self) -> tuple[str, ...]:
        """보고서 «미제공 사유» 자리에 실을 확인 범위 안내 문장들."""

        if self.collection_state == COLLECTION_STATE_NONE:
            first = COVERAGE_LINE_NONE
        elif self.collection_state == COLLECTION_STATE_PARTIAL or self.unverified_scopes:
            first = COVERAGE_LINE_PARTIAL
        else:
            first = COVERAGE_LINE_COMPLETE
        return (
            first,
            *(f"{COVERAGE_SCOPE_PREFIX}{scope.label}" for scope in self.unverified_scopes),
        )


def unverified_scope_from_attempt(
    source_kind: str,
    state: str,
    reason_code: str = "",
    *,
    document_title: str = "",
) -> UnverifiedScope | None:
    """수집 시도 한 건을 독자용 미확인 범위로 바꾼다. 확인을 마친 시도는 None.

    ``state``가 TRUNCATED·FAILED일 때만 값을 만든다. OK·MISSING은 «확인을
    끝낸» 결과라 미확인 범위가 아니다(자료 부재를 미확인으로 위장하지 않는다).
    ``document_title``이 있으면 자료 이름 대신 실제 문서 제목을 쓴다.
    """

    clean_state = str(state or "").strip().upper()
    phrase = COLLECTION_STATE_PHRASES.get(clean_state)
    if phrase is None:
        return None
    clean_kind = str(source_kind or "").strip()
    clean_reason = str(reason_code or "").strip()
    title = " ".join(str(document_title or "").split())
    source_label = title or SOURCE_KIND_LABELS.get(clean_kind, "공식 자료")
    reason_phrase = REASON_CODE_PHRASES.get(clean_reason, "")
    suffix = f": {phrase}" + (f" ({reason_phrase})" if reason_phrase else "")
    # 문서 제목이 아주 길어도 안내 상한(200자)을 넘기지 않는다 — 제목만 줄인다.
    room = _MAX_LABEL_CHARS - len(suffix)
    if len(source_label) > room:
        source_label = source_label[: max(0, room - 1)].rstrip() + "…"
    label = f"{source_label}{suffix}"
    # 종류 코드는 소문자 닫힌 모양으로 고정한다(상태 값은 대문자 Enum이라 낮춘다).
    kind_parts = [
        part.lower()
        for part in (clean_kind or "official", clean_state, clean_reason)
        if part
    ]
    kind = ":".join(kind_parts)
    if _SCOPE_KIND_SHAPE.fullmatch(kind) is None:
        kind = f"official:{clean_state.lower()}"
    return UnverifiedScope(kind=kind, label=label)


__all__ = [
    "COLLECTION_STATE_COMPLETE",
    "COLLECTION_STATE_NONE",
    "COLLECTION_STATE_PARTIAL",
    "COVERAGE_LINE_COMPLETE",
    "COVERAGE_LINE_NONE",
    "COVERAGE_LINE_PARTIAL",
    "COVERAGE_SCOPE_PREFIX",
    "EvidenceAvailability",
    "UnverifiedScope",
    "unverified_scope_from_attempt",
]
