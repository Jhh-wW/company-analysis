"""다개년 매출 구성표와 봉인된 원문 쌍의 직접 대조."""

from __future__ import annotations

import json
from pathlib import Path

from src.features.revenuemix.logic import build_multi_year
from src.shared.revenue_table_provenance import (
    canonical_json,
    revenue_row_evidence_matches,
    revenue_table_source_excerpt,
)


_SOURCE = (
    Path(__file__).resolve().parents[2]
    / "features"
    / "revenuemix"
    / "tests"
    / "fixtures"
    / "hybe_product_and_region.txt"
).read_text(encoding="utf-8")


def test_다개년_공개행과_금액원값이_같은_원문쌍에_결속된다() -> None:
    table = build_multi_year(_SOURCE)[0]
    excerpt = revenue_table_source_excerpt(table["evidence_rows"])

    for index, (row, raw_row, evidence) in enumerate(
        zip(table["rows"], table["raw_rows"], table["evidence_rows"])
    ):
        assert revenue_row_evidence_matches(
            evidence,
            cited_source_text=excerpt,
            filing_text=_SOURCE,
            headers=table["headers"],
            public_row=row,
            raw_row=raw_row,
            expected_selected_index=index,
            expected_row_count=len(table["rows"]) - 1,
        )


def test_다개년_원문쌍의_연도나_좌표를_바꾸면_대조가_실패한다() -> None:
    table = build_multi_year(_SOURCE)[0]
    evidence = json.loads(table["evidence_rows"][0])
    evidence["row"]["period_pairs"][0]["year"] = "2022"
    corrupted = canonical_json(evidence)

    assert not revenue_row_evidence_matches(
        corrupted,
        cited_source_text=revenue_table_source_excerpt(table["evidence_rows"]),
        filing_text=_SOURCE,
        headers=table["headers"],
        public_row=table["rows"][0],
        raw_row=table["raw_rows"][0],
        expected_selected_index=0,
        expected_row_count=len(table["rows"]) - 1,
    )
