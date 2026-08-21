from __future__ import annotations

from dataclasses import replace

from src.features.pilot_evaluation.contract import (
    PilotCase,
    PilotCategory,
    PilotResult,
    evaluate_pilot,
)


def _passing_rows() -> list[PilotResult]:
    rows: list[PilotResult] = []
    categories = (
        [PilotCategory.LISTED] * 10
        + [PilotCategory.UNLISTED_DISCLOSURE] * 8
        + [PilotCategory.SPARSE_OR_AMBIGUOUS] * 7
    )
    for index, category in enumerate(categories, start=1):
        rows.append(
            PilotResult(
                case=PilotCase(f"P{index:02d}", category, f"회사 {index}"),
                legal_entity_correct=index <= 23,
                completed=index <= 20,
                stopped=index > 20,
                error_type="" if index <= 20 else "GATE_STOPPED",
                automatic_judgment="release" if index <= 20 else "stop",
                user_judgment="release" if index <= 20 else "stop",
                judgments_agree=True,
                elapsed_sec=1200,
                internal_ai_cost_krw=250,
            )
        )
    return rows


def test_25건_합격선을_결과보기전에_고정해평가한다():
    summary = evaluate_pilot(_passing_rows())
    assert summary.passed is True
    assert summary.correct_identities == 23
    assert summary.complete_reports == 20
    assert summary.average_ai_cost_krw == 250


def test_원가목표실패가_품질합격선을_낮추지않는다():
    rows = [
        replace(row, internal_ai_cost_krw=600) for row in _passing_rows()
    ]
    summary = evaluate_pilot(rows)
    assert summary.passed is False
    assert any("원가" in reason for reason in summary.reasons)
    assert summary.correct_identities == 23
    assert summary.complete_reports == 20
