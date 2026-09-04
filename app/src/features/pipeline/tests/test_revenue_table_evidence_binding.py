"""매출표가 실제 공시 원문 조각을 끝까지 함께 운반하는지 시험한다."""

from __future__ import annotations

import copy
import json

import pytest

from src.core.citations import citation_number
from src.features.pipeline.real import (
    RevenueTableEvidenceBindingError,
    _bind_revenue_table_evidence_fragments,
)
from src.features.pipeline.port import ReportTable
from src.features.revenuemix.logic import build
from src.shared.revenue_table_provenance import (
    canonical_json,
    revenue_table_source_excerpt,
)


FILING_TEXT = (
    "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
    "제품가 6,000 60.00% 제품나 4,000 40.00% 합계 10,000 100.00% "
    "지역별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
    "국내 7,000 70.00% 해외 3,000 30.00% 합계 10,000 100.00%"
)
FILING = {
    "rcept_no": "20260331000123",
    "rcept_dt": "20260331",
    "report_nm": "사업보고서 (2025.12)",
}


def test_제품과_지역_표가_각자_정확한_원문_조각을_인용한다() -> None:
    tables = build(FILING_TEXT)

    fragments, bound = _bind_revenue_table_evidence_fragments(
        {1: {"종류": "사업내용", "원문": "회사는 제품을 판매합니다."}},
        tables,
        filing=FILING,
        filing_text=FILING_TEXT,
    )

    assert len(bound) == 2
    assert all("axis" not in table for table in bound)
    assert all(ReportTable(**table).is_valid for table in bound)
    cited_numbers = tuple(citation_number(table["cite"]) for table in bound)
    assert cited_numbers == ("2", "3")
    assert len(set(cited_numbers)) == 2
    for number, table in zip(cited_numbers, bound):
        assert number is not None
        cited = fragments[int(number)]
        assert cited["종류"] == "매출수주"
        assert cited["문서ID"] == FILING["rcept_no"]
        assert cited["문서일"] == "2026-03-31"
        assert cited["원문"] == revenue_table_source_excerpt(table["evidence_rows"])
        assert cited["원문"] in FILING_TEXT


def test_서로_다른_표의_행_근거를_이어붙이면_AI전에_거절한다() -> None:
    tables = build(FILING_TEXT)
    corrupted = copy.deepcopy(tables)
    corrupted[0]["evidence_rows"][0] = tables[1]["evidence_rows"][0]

    with pytest.raises(RevenueTableEvidenceBindingError):
        _bind_revenue_table_evidence_fragments(
            {}, corrupted, filing=FILING, filing_text=FILING_TEXT
        )


def test_공시_문서_ID가_없는_표는_출처를_지어내지_않는다() -> None:
    with pytest.raises(RevenueTableEvidenceBindingError):
        _bind_revenue_table_evidence_fragments(
            {}, build(FILING_TEXT), filing=None, filing_text=FILING_TEXT
        )


def test_다른_원문에서_만든_자가일관_표도_AI전에_거절한다() -> None:
    other_filing_text = FILING_TEXT.replace("국내 7,000", "국내 6,999")
    self_consistent_but_wrong = build(other_filing_text)

    with pytest.raises(RevenueTableEvidenceBindingError):
        _bind_revenue_table_evidence_fragments(
            {},
            self_consistent_but_wrong,
            filing=FILING,
            filing_text=FILING_TEXT,
        )


def test_공개행을_바꿔도_원문근거와_맞지_않아_AI전에_거절한다() -> None:
    corrupted = copy.deepcopy(build(FILING_TEXT))
    corrupted[0]["rows"][0][1] = "9,999"

    with pytest.raises(RevenueTableEvidenceBindingError):
        _bind_revenue_table_evidence_fragments(
            {}, corrupted, filing=FILING, filing_text=FILING_TEXT
        )


@pytest.mark.parametrize("field", ("axis", "caption", "missing_axis"))
def test_typed축이나_caption을_바꾸면_행값이_맞아도_AI전에_거절한다(
    field: str,
) -> None:
    corrupted = copy.deepcopy(build(FILING_TEXT))
    if field == "axis":
        corrupted[0]["axis"] = "region"
    elif field == "caption":
        corrupted[0]["caption"] = "어디서 번 돈인가 — 지역별 매출 비중 (2025년)"
    else:
        del corrupted[0]["axis"]

    with pytest.raises(RevenueTableEvidenceBindingError):
        _bind_revenue_table_evidence_fragments(
            {}, corrupted, filing=FILING, filing_text=FILING_TEXT
        )


def test_evidence의_축만_자가일관되게_바꿔도_header와_달라_AI전에_거절한다() -> None:
    corrupted = copy.deepcopy(build(FILING_TEXT))
    changed_rows: list[str] = []
    for evidence in corrupted[0]["evidence_rows"]:
        payload = json.loads(evidence)
        payload["table"]["axis"] = "region"
        changed_rows.append(canonical_json(payload))
    corrupted[0]["evidence_rows"] = changed_rows

    with pytest.raises(RevenueTableEvidenceBindingError):
        _bind_revenue_table_evidence_fragments(
            {}, corrupted, filing=FILING, filing_text=FILING_TEXT
        )


@pytest.mark.parametrize(
    "malformed",
    (object(), {"caption": "빈 표", "headers": ["구분"], "rows": []}),
)
def test_형식이_깨진_표는_조용히_버리지_않고_AI전에_거절한다(
    malformed: object,
) -> None:
    with pytest.raises(RevenueTableEvidenceBindingError):
        _bind_revenue_table_evidence_fragments(
            {}, [malformed], filing=FILING, filing_text=FILING_TEXT
        )
