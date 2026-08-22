from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.pilot_evaluation.contract import (
    PilotCase,
    PilotCategory,
    PilotResult,
    evaluate_pilot,
)


def _passing_rows() -> list[PilotResult]:
    rows: list[PilotResult] = []
    categories = [PilotCategory.LISTED] * 10
    for index, category in enumerate(categories, start=1):
        rows.append(
            PilotResult(
                case=PilotCase(f"P{index:02d}", category, f"회사 {index}"),
                legal_entity_correct=True,
                completed=index <= 8,
                stopped=index > 8,
                error_type="" if index <= 8 else "GATE_STOPPED",
                automatic_judgment="release" if index <= 8 else "stop",
                user_judgment="release" if index <= 8 else "stop",
                judgments_agree=True,
                elapsed_sec=1200,
                internal_ai_cost_krw=250,
            )
        )
    return rows


def test_10건_합격선을_결과보기전에_고정해평가한다():
    summary = evaluate_pilot(_passing_rows())
    assert summary.passed is True
    assert summary.correct_identities == 10
    assert summary.complete_reports == 8
    assert summary.average_ai_cost_krw == 250


def test_원가목표실패가_품질합격선을_낮추지않는다():
    rows = [
        replace(row, internal_ai_cost_krw=600) for row in _passing_rows()
    ]
    summary = evaluate_pilot(rows)
    assert summary.passed is False
    assert any("원가" in reason for reason in summary.reasons)
    assert summary.correct_identities == 10
    assert summary.complete_reports == 8


def test_25건_manifest_후보를_실제10건_평가에_섞지않는다():
    rows = _passing_rows() + [
        replace(
            _passing_rows()[0],
            case=PilotCase(
                "P11", PilotCategory.UNLISTED_DISCLOSURE, "승인 밖 회사"
            ),
        )
    ]

    summary = evaluate_pilot(rows)

    assert summary.passed is False
    assert any("P01~P10 10/10" in reason for reason in summary.reasons)
    assert any("상장사 10건" in reason for reason in summary.reasons)


def test_완성보고서에는_최종게이트사유를_허용하지않는다():
    rows = _passing_rows()
    rows[0] = replace(rows[0], final_gate_reason="comparison_blocked")
    with pytest.raises(ValueError, match="완성 보고서"):
        evaluate_pilot(rows)


def test_최종게이트사유는_닫힌코드만_허용한다():
    rows = _passing_rows()
    rows[-1] = replace(rows[-1], final_gate_reason="raw provider message")
    with pytest.raises(ValueError, match="닫힌 코드"):
        evaluate_pilot(rows)
