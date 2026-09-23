"""웹·PDF가 보고서 표시 정책을 함께 사용하는 조립 경계."""


def empty_section_notice(report: object, section: object) -> str:
    """빈 장 표시 정책을 소유 feature에 위임한다."""
    from src.features.report_standard.empty_section import (
        empty_section_notice as notice_for_section,
    )

    return notice_for_section(report, section)


def reader_citation_groups(report: object) -> tuple:
    """봉인된 표시 묶음을 우선하며 구형 FULL은 재계산하지 않는다."""
    projection = getattr(report, "public_projection", None)
    if projection is not None:
        return projection.citation_groups
    from src.features.report_standard.reader_display import citation_groups
    return citation_groups(report)


def reader_scope_notes(report: object) -> tuple[str, ...]:
    projection = getattr(report, "public_projection", None)
    if projection is not None:
        return projection.reader_notes
    from src.features.report_standard.reader_display import reader_notes
    return reader_notes(report)


def reader_summary_notes(report: object) -> dict[str, str]:
    projection = getattr(report, "public_projection", None)
    if projection is not None:
        return dict(projection.summary_notes)
    from src.features.report_standard.reader_display import summary_notes
    return dict(summary_notes(report))


def reader_section_content(section: object) -> tuple:
    from src.features.report_standard.reader_display import section_display_content
    return section_display_content(section)
