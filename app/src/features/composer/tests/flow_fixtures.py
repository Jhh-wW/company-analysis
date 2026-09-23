"""렌더 시험에서 이미 합격한 합성 도식의 검수 입력을 명시한다."""

from dataclasses import replace

from src.features.composer.flow_review_binding import (
    bind_reviewed_flow_row,
    filter_reviewed_flow_rows,
)
from src.features.composer.logic import _normalize_fragments


def reviewed_flow_fixture(report, fragments, *, baseline_date=""):
    """의미 검수 자체를 시험하지 않는 렌더 fixture만 합성 영수증을 갖춘다.

    실제 검수 입력처럼 같은 원문·기준일을 결속한다. 인용이 없는 행은
    승인하지 않으며 생산 파이프라인과 같은 최종 필터를 통과시킨다.
    """
    sources = {item.fragment_id: item for item in _normalize_fragments(fragments)}
    sections = []
    for section in report.sections:
        rows = []
        for row in section.flow_rows:
            if any(cell.strip() for cell in row.cells) and row.citations and all(fid in sources for fid in row.citations):
                row = bind_reviewed_flow_row(
                    row, section_id=section.section_id, fragments=sources,
                    review_path="legacy", baseline_date=baseline_date,
                )
            rows.append(row)
        sections.append(replace(section, flow_rows=tuple(rows)))
    return filter_reviewed_flow_rows(
        replace(report, sections=tuple(sections)), sources,
        baseline_date=baseline_date,
    )
