"""웹 완료 결과가 canonical 1~9장 관측 계약으로 옮겨지는지 검증한다."""

from __future__ import annotations

from dataclasses import replace

from src.features.observability.constants import COUNTED_CELLS
from src.features.pipeline.canonical_demo import build_demo_report
from src.web.recording import _observed_cells


def test_canonical_공개본은_9개_의미_ID를_모두_채운_것으로_기록한다():
    report = build_demo_report()

    cells_filled, cells_missing = _observed_cells(report)

    assert cells_filled == 9
    assert cells_missing == []
    assert tuple(report.cells) == COUNTED_CELLS


def test_canonical_장이_비면_숫자가_아닌_의미_ID로_미충족을_기록한다():
    report = build_demo_report()
    missing = "competitive_position"
    shortened = replace(
        report,
        sections=[section for section in report.sections if section.cell != missing],
        cells={cell: value for cell, value in report.cells.items() if cell != missing},
    )

    cells_filled, cells_missing = _observed_cells(shortened)

    assert cells_filled == 8
    assert cells_missing == [missing]


def test_하위_호환_비_canonical_보고서는_기존_6칸으로_관측한다():
    report = build_demo_report()
    legacy = replace(
        report,
        schema_version="",
        sections=[],
        cells={
            "1": True,
            "2": True,
            "3": True,
            "4-1": True,
            "4-2": False,
            "4-3": True,
        },
    )

    cells_filled, cells_missing = _observed_cells(legacy)

    assert cells_filled == 5
    assert cells_missing == ["4-2"]
