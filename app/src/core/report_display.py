"""웹·PDF가 보고서 표시 정책을 함께 사용하는 조립 경계."""


def empty_section_notice(report: object, section: object) -> str:
    """빈 장 표시 정책을 소유 feature에 위임한다."""
    from src.features.report_standard.empty_section import (
        empty_section_notice as notice_for_section,
    )

    return notice_for_section(report, section)
