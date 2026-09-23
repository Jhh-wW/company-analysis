"""빈 장을 허용하면서 장별 근거 소유권을 고정하는 확보자료 작성 입력."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from src.features.composer.constants import CLAIM_SLOTS_BY_SECTION, SECTION_IDS
from src.features.composer.culture_constants import SOURCE_CLAUSE_SPLIT_RE
from src.features.composer.culture_guard import _clause_carries_section_subject
from src.features.composer.port import CollectedFragment
from src.shared.report_evidence.constants import FORMAL_DOCUMENT_SOURCE_KINDS
from src.shared.report_evidence.legacy_fragment_kinds import (
    LEGACY_FRAGMENT_KINDS,
    legacy_fragment_kind_is_owned_by,
)


@dataclass(frozen=True)
class PartialEvidenceView:
    """FULL 출고 권위를 갖지 않는 불변 장별 근거 보기."""

    fragments: tuple[CollectedFragment, ...]
    packets: Mapping[str, tuple[CollectedFragment, ...]]
    allowed_fragment_ids_by_section: Mapping[str, frozenset[str]]


def _supports_section(fragment: CollectedFragment, section_id: str) -> bool:
    if not fragment.text.strip():
        return False
    supported = set(fragment.supported_claim_slots)
    if supported:
        eligible = bool(supported.intersection(CLAIM_SLOTS_BY_SECTION[section_id]))
    else:
        kind = fragment.formal_source_kind or fragment.kind
        eligible = kind in LEGACY_FRAGMENT_KINDS and legacy_fragment_kind_is_owned_by(
            kind, section_id,
        )
    if not eligible:
        return False
    if section_id == "culture":
        # 최초 작성과 빈 장 복구 모두 실제 조직문화 근거가 있는 공식 조각만 쓴다.
        return bool(
            fragment.formal_source_kind in FORMAL_DOCUMENT_SOURCE_KINDS
            and fragment.document_identity and fragment.document_content_sha256
            and fragment.identity_binding
            and supported.intersection(CLAIM_SLOTS_BY_SECTION[section_id])
            and any(_clause_carries_section_subject(clause)
                    for clause in SOURCE_CLAUSE_SPLIT_RE.split(fragment.text))
        )
    return True


def build_partial_evidence_view(
    fragments: Sequence[CollectedFragment],
) -> PartialEvidenceView:
    """기존 slot 또는 등록된 종류만 사용하며 모르는 근거를 새 장에 승격하지 않는다."""
    by_id: dict[str, CollectedFragment] = {}
    for fragment in fragments:
        fragment_id = fragment.fragment_id
        if not fragment_id or not fragment_id.isascii() or not fragment_id.isdecimal() or int(fragment_id) < 1:
            raise ValueError("확보자료 조각 id는 양의 정수 문자열이어야 합니다")
        if str(int(fragment_id)) != fragment_id:
            raise ValueError("확보자료 조각 id 표기가 정본과 다릅니다")
        if fragment_id in by_id and by_id[fragment_id] != fragment:
            raise ValueError("같은 확보자료 조각 id에 서로 다른 근거가 있습니다")
        by_id.setdefault(fragment_id, fragment)
    packets = {
        section_id: tuple(fragment for fragment in by_id.values()
                          if _supports_section(fragment, section_id))
        for section_id in SECTION_IDS
    }
    used_ids = {fragment.fragment_id for values in packets.values() for fragment in values}
    return PartialEvidenceView(
        fragments=tuple(fragment for fragment in by_id.values() if fragment.fragment_id in used_ids),
        packets=MappingProxyType(packets),
        allowed_fragment_ids_by_section=MappingProxyType({
            section_id: frozenset(fragment.fragment_id for fragment in values)
            for section_id, values in packets.items()
        }),
    )
