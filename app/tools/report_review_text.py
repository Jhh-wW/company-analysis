"""저장 보고서 검수와 지표 도구가 공유하는 표시 문구 판정."""

import re

from src.features.composer.constants import (
    LEGACY_EVIDENCE_AVAILABLE_NOTICES,
    NOTICE_AI_UNAVAILABLE,
    NOTICE_COMPOSE_FAILED,
    NOTICE_DUPLICATE_MOVED,
    NOTICE_DUPLICATE_MOVED_TABLE_KEPT,
    NOTICE_INSUFFICIENT_EVIDENCE,
    NOTICE_INSUFFICIENT_EVIDENCE_TABLE_KEPT,
    SECTION_IDS,
    SECTION_TITLES,
)
from src.features.composer.dedupe_notice_constants import (
    MATERIAL_JOIN, MATERIAL_NAME_FLOW, MATERIAL_NAME_NEWS, MATERIAL_NAME_TABLE,
    NOTICE_MOVED_MATERIALS_SUFFIX_TEMPLATE, NOTICE_MOVED_TARGETS_TEMPLATE,
    NOTICE_ONLY_MATERIALS_TEMPLATE,
)

CITATION_RE = re.compile(r"\[\d+\]|〔\d+〕")
INTERPRETATION_SUFFIX_RE = re.compile(r"\s+—\s+해석\s*$")
LEGACY_MOVED_NOTICE = (
    "이 장에 담겼던 내용이 다른 장에서 더 자세히 다뤄져, 같은 설명을 두 번 "
    "싣지 않으려고 그쪽으로 모았습니다. 자료가 없어서 비어 있는 것이 아닙니다."
)
SECTION_NOTICES = frozenset({
    NOTICE_AI_UNAVAILABLE, NOTICE_COMPOSE_FAILED, NOTICE_DUPLICATE_MOVED,
    NOTICE_DUPLICATE_MOVED_TABLE_KEPT, NOTICE_INSUFFICIENT_EVIDENCE,
    NOTICE_INSUFFICIENT_EVIDENCE_TABLE_KEPT, LEGACY_MOVED_NOTICE,
    LEGACY_MOVED_NOTICE + " 아래 표는 이 장에 그대로 남아 있습니다.",
    *LEGACY_EVIDENCE_AVAILABLE_NOTICES,
})
SECTION_LABELS = tuple(
    f"{index}장 «{SECTION_TITLES[section_id]}»"
    for index, section_id in enumerate(SECTION_IDS, start=1)
)
MATERIAL_LABELS = (MATERIAL_NAME_TABLE, MATERIAL_NAME_FLOW, MATERIAL_NAME_NEWS)
MOVED_NOTICE_RE = re.compile(
    re.escape(NOTICE_MOVED_TARGETS_TEMPLATE).replace(re.escape("{targets}"), r"(?P<targets>.+)")
    + "(?:" + re.escape(NOTICE_MOVED_MATERIALS_SUFFIX_TEMPLATE).replace(
        re.escape("{materials}"), r"(?P<materials>.+)",
    ) + ")?"
)
MATERIAL_NOTICE_RE = re.compile(
    re.escape(NOTICE_ONLY_MATERIALS_TEMPLATE).replace(re.escape("{materials}"), r"(?P<materials>.+)")
)


def normalized_public_claim(text: str) -> str:
    """인용 표기와 문장 끝 표시만 제거한다. 본문의 해석 단어는 보존한다."""
    value = INTERPRETATION_SUFFIX_RE.sub("", text)
    return " ".join(CITATION_RE.sub("", value).split())


def is_section_notice(text: str) -> bool:
    """고정 문구와 유효한 대상·자료 종류의 정본 안내문만 전체 일치로 구분한다."""
    value = " ".join(str(text).split())
    if value in SECTION_NOTICES:
        return True
    moved = MOVED_NOTICE_RE.fullmatch(value)
    if moved is not None:
        return _ordered_labels(moved["targets"], SECTION_LABELS) and (
            moved["materials"] is None or _ordered_labels(moved["materials"], MATERIAL_LABELS)
        )
    materials = MATERIAL_NOTICE_RE.fullmatch(value)
    return materials is not None and _ordered_labels(materials["materials"], MATERIAL_LABELS)


def _ordered_labels(value: str, allowed: tuple[str, ...]) -> bool:
    """실제 안내문 생성 순서의 닫힌 이름만 인정하며 임의 본문은 숨기지 않는다."""
    parts = tuple(value.split(MATERIAL_JOIN))
    return bool(parts) and parts == tuple(label for label in allowed if label in parts)
