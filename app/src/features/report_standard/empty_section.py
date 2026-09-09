"""저장 사실이나 품질 판정을 바꾸지 않는 무봉인 v2 빈 장 표시 계약."""

from src.features.report_standard.empty_section_constants import EMPTY_SECTION_NOTICE
from src.shared.report_generation.constants import ENGINE_V2_SCHEMA_VERSION


def empty_section_notice(report: object, section: object) -> str:
    """공개 내용이 없으면 명시 안내를 우선하고, 없을 때만 중립 안내를 쓴다.

    v2 카드도 tables에 담긴다. lines와 fact_ids는 감사 자료이므로 공개
    본문으로 세지 않으며, 전역 품질 수치·사유에서 수집 원인을 추정하지 않는다.
    """
    if (
        getattr(report, "schema_version", "") != ENGINE_V2_SCHEMA_VERSION
        or getattr(report, "public_projection", None) is not None
        or getattr(report, "release_mode", "") == "FULL"
    ):
        return ""
    if (
        any(str(text).strip() for text in getattr(section, "prose_paragraphs", ()))
        or any(str(text).strip() for text, _cite in getattr(section, "prose_lines", ()))
        or getattr(section, "tables", ())
    ):
        return ""
    explicit = [
        str(getattr(section, "empty_reason", "")).strip(),
        *(str(text).strip() for text in getattr(section, "guidance_lines", ())),
    ]
    if any(explicit):
        return "\n".join(dict.fromkeys(text for text in explicit if text))
    return EMPTY_SECTION_NOTICE
