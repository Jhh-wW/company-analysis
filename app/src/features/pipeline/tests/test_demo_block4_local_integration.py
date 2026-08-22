"""저장소 밖의 과거 데모 15건을 명시적으로 선택해 검수하는 로컬 통합 시험."""

from __future__ import annotations

from functools import lru_cache

import pytest

from src.core.constants import SITUATION_CELLS
from src.features.pipeline import demo
from src.features.pipeline.demo import _find_record, _load_report
from src.features.pipeline.port import Outcome, ReportSection


pytestmark = pytest.mark.local_integration

_EXPECTED_SITUATION_FILLED = 9


@lru_cache(maxsize=1)
def _historical_demo_reports() -> tuple[tuple[str, object], ...]:
    made: list[tuple[str, object]] = []
    seen: set[str] = set()
    for row in demo._load_runs():
        if demo._outcome_of(str(row.get("outcome", ""))) is not Outcome.REPORT:
            continue
        company = str((row.get("input") or {}).get("company", "")).strip()
        if not company or company in seen:
            continue
        record = _find_record(company)
        report = _load_report(record) if record else None
        if report is not None:
            made.append((company, report))
            seen.add(company)
    if not made:
        pytest.fail(
            "로컬 통합 시험을 선택했지만 과거 데모 runs·reports·fragments 자료를 찾지 못했습니다"
        )
    return tuple(made)


def _cells_of(report) -> dict[str, ReportSection]:
    return {section.cell: section for section in report.sections}


def test_로컬통합_과거데모에서_4번이_채워지는_회사수():
    filled = [
        company
        for company, report in _historical_demo_reports()
        if any(
            _cells_of(report).get(cell) and _cells_of(report)[cell].lines
            for cell in SITUATION_CELLS
        )
    ]

    assert len(filled) == _EXPECTED_SITUATION_FILLED, f"4번이 채워진 곳: {filled}"


def test_로컬통합_과거데모는_8번교차표_대신_일반활용칸을_가진다():
    for company, report in _historical_demo_reports():
        cells = _cells_of(report)
        assert not ({"5", "6", "7", "8"} & set(cells)), company
        assert "활용" in cells, company
