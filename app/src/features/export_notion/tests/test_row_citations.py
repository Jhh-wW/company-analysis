"""Notion 표의 행별 인용 표식 보존 계약."""

from dataclasses import replace

import pytest

from src.features.export_notion import logic
from src.features.pipeline.port import ReportTable
from src.features.composer.tests.test_section_public_manifest import _run_full
from src.features.export_notion.tests.test_export_notion import (
    _sealed_v2_report,
)
from src.shared.report_generation.public_projection import PublicTableBlock


def _cell_text(row: dict, index: int) -> str:
    return "".join(
        rich["text"]["content"] for rich in row["table_row"]["cells"][index]
    )


def _matrix(block: dict) -> list[list[str]]:
    return [
        [_cell_text(row, index) for index in range(block["table"]["table_width"])]
        for row in block["table"]["children"]
    ]


def test_legacy_empty_row_cites_preserve_existing_notion_rows_exactly():
    table = ReportTable(
        caption="옛 표",
        headers=["항목", "값"],
        rows=[["첫째", "10"], ["둘째", "20"]],
        row_cites=[],
    )

    table_block = logic._table_blocks(table)[1]

    assert _matrix(table_block) == [table.headers, *table.rows]


def test_report_table_row_cites_are_attached_to_their_actual_rows():
    table = ReportTable(
        caption="행별 표식",
        headers=["항목", "값"],
        rows=[["첫째", "10"], ["둘째", "20"]],
        cite="[99]",
        row_cites=[["[11]"], ["[22]", "[33]"]],
    )

    table_block = logic._table_blocks(table)[1]

    assert _matrix(table_block) == [
        table.headers,
        ["첫째", "10 〔11〕"],
        ["둘째", "20 〔22〕 〔33〕"],
    ]
    assert "〔99〕" not in " ".join(_matrix(table_block)[1][-1:])


def test_public_table_block_row_cites_are_preserved_in_sealed_notion_output():
    table = PublicTableBlock(
        caption="봉인 표",
        headers=("항목", "값"),
        rows=(("첫째", "10"), ("둘째", "20")),
        cite="[99]",
        numeric=True,
        presentation="table",
        display_unit="",
        manifest_ref="a" * 64,
        row_cites=(("[11]",), ("[22]",)),
    )

    table_block = logic._v2_table_blocks(table)[1]

    assert _matrix(table_block) == [
        list(table.headers),
        ["첫째", "10 〔11〕"],
        ["둘째", "20 〔22〕"],
    ]


def test_replace_injected_row_cites_render_in_projection_dto_unit_only():
    """사후 replace를 쓰는 DTO 렌더 단위이며 생산 봉인을 검증하지 않는다."""

    report = _sealed_v2_report()
    projection = report.public_projection
    assert projection is not None
    target = next(section for section in projection.sections if section.display.tables)
    original_table = target.display.tables[0]
    row_cites = tuple((f"[{index}]",) for index in range(1, len(original_table.rows) + 1))
    cited_table = replace(original_table, row_cites=row_cites)
    cited_display = replace(
        target.display,
        tables=(cited_table, *target.display.tables[1:]),
    )
    cited_sections = tuple(
        replace(section, display=cited_display)
        if section.display.cell == target.display.cell
        else section
        for section in projection.sections
    )
    cited_report = replace(
        report,
        public_projection=replace(projection, sections=cited_sections),
    )

    blocks = logic.build_blocks(cited_report)
    matrices = [_matrix(block) for block in blocks if block["type"] == "table"]
    expected = [
        list(original_table.headers),
        *[
            [*row[:-1], f"{row[-1]} 〔{index}〕"]
            for index, row in enumerate(original_table.rows, start=1)
        ],
    ]

    assert expected in matrices
    assert any(original_table.caption in text for text in [
        "".join(item["text"]["content"] for item in block["paragraph"]["rich_text"])
        for block in blocks
        if block["type"] == "paragraph"
    ])


def test_run_v2_produced_distinct_row_cites_survive_notion_build_blocks():
    """실제 run_v2→manifest→projection 결과의 서로 다른 행 인용을 확인한다."""

    output, _writer, _reviewer, _diagram = _run_full(flow=True)
    report = output.report
    projection = report.public_projection
    assert projection is not None
    assert report.public_structure_manifest

    flow_section = next(
        section for section in projection.sections
        if section.display.cell == "business_model"
    )
    flow = next(
        table for table in flow_section.display.tables
        if table.presentation == "flow"
    )
    assert flow.row_cites == (("[2]",), ("[20]",))

    blocks = logic.build_blocks(report)
    matrices = [_matrix(block) for block in blocks if block["type"] == "table"]

    assert [
        list(flow.headers),
        [*flow.rows[0][:-1], f"{flow.rows[0][-1]} 〔2〕"],
        [*flow.rows[1][:-1], f"{flow.rows[1][-1]} 〔20〕"],
    ] in matrices


@pytest.mark.parametrize(
    "row_cites",
    (
        [["[1]"], ["[2]"], ["[3]"]],
        [["1"], ["[2]"]],
        [["[2]", "[1]"], ["[3]"]],
    ),
)
def test_invalid_row_cites_are_rejected_instead_of_repaired(row_cites):
    table = ReportTable(
        caption="잘못된 표식",
        headers=["항목", "값"],
        rows=[["첫째", "10"], ["둘째", "20"]],
        row_cites=row_cites,
    )

    with pytest.raises(ValueError):
        logic._table_blocks(table)
