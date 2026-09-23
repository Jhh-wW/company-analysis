"""필수 장 하한과 구분한, 실제 존재하는 선택 장의 공개 관측 계약."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from src.shared.report_quality.constants import STRICT_REQUIRED_QUALITY_SECTION_IDS

OPTIONAL_SECTIONS_VERSION = "optional-section-observation-v1"
OPTIONAL_SECTION_WIRE_KEYS = frozenset({"optional_sections_version", "optional_sections"})


@dataclass(frozen=True)
class OptionalSectionObservation:
    """문장 수를 사실 수로 대체하지 않고 미결속 공개 여부도 보존한다."""

    section_id: str
    public_sentence_count: int
    bound_fact_ids: tuple[str, ...]
    notice_only: bool
    has_unbound_public_content: bool


def validate_optional_sections(
    version: str,
    sections: tuple[OptionalSectionObservation, ...],
    *,
    required_section_ids: tuple[str, ...] = (),
) -> None:
    if type(version) is not str or type(sections) is not tuple:
        raise ValueError("선택 장 관측 형식이 손상됐습니다")
    if version == "" and sections == ():
        return  # 과거 저장본에는 관측 자체가 없었다.
    if version != OPTIONAL_SECTIONS_VERSION:
        raise ValueError("알 수 없는 선택 장 관측 버전입니다")
    seen: set[str] = set()
    for section in sections:
        if type(section) is not OptionalSectionObservation:
            raise ValueError("선택 장 관측 객체 형식이 다릅니다")
        if (
            section.section_id not in STRICT_REQUIRED_QUALITY_SECTION_IDS
            or section.section_id in required_section_ids
            or section.section_id in seen
            or type(section.public_sentence_count) is not int
            or section.public_sentence_count < 0
            or type(section.notice_only) is not bool
            or type(section.has_unbound_public_content) is not bool
            or type(section.bound_fact_ids) is not tuple
            or any(type(value) is not str or not value or value != value.strip()
                   for value in section.bound_fact_ids)
            or len(section.bound_fact_ids) != len(set(section.bound_fact_ids))
        ):
            raise ValueError("선택 장 관측의 장·문장 수·결속 정보가 손상됐습니다")
        seen.add(section.section_id)


def optional_sections_to_wire(
    version: str, sections: tuple[OptionalSectionObservation, ...],
) -> dict[str, object]:
    validate_optional_sections(version, sections)
    if not version:
        return {}
    return {
        "optional_sections_version": version,
        "optional_sections": [
            {**asdict(section), "bound_fact_ids": list(section.bound_fact_ids)}
            for section in sections
        ],
    }


def optional_sections_from_wire(data: dict) -> tuple[str, tuple[OptionalSectionObservation, ...]]:
    present = OPTIONAL_SECTION_WIRE_KEYS.intersection(data)
    if not present:
        return "", ()
    if present != OPTIONAL_SECTION_WIRE_KEYS:
        raise ValueError("선택 장 관측 버전과 본문이 함께 필요합니다")
    version, raw = data["optional_sections_version"], data["optional_sections"]
    if version != OPTIONAL_SECTIONS_VERSION or type(raw) is not list:
        raise ValueError("선택 장 관측 JSON 형식이 다릅니다")
    keys = frozenset(OptionalSectionObservation.__dataclass_fields__)
    sections = []
    for item in raw:
        if type(item) is not dict or set(item) != keys or type(item["bound_fact_ids"]) is not list:
            raise ValueError("선택 장 관측 JSON 항목이 손상됐습니다")
        sections.append(OptionalSectionObservation(**{**item, "bound_fact_ids": tuple(item["bound_fact_ids"])}))
    result = tuple(sections)
    validate_optional_sections(version, result)
    return version, result
