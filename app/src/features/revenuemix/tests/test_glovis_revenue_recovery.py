"""실제 공시의 지역 매출표와 단위·머리말 경계를 검증한다.

픽스처는 운영 방식(태그를 공백으로 바꾸고 공백 접기)으로 변환한 원문의
연속 구간을 수정 없이 잘랐다. 20260318001205의 고객소재지 기준 표는
183777:184139(362자), 20250317001025의 법인소재지 기준 표는
181451:181772(321자)이며, 서로 다른 지역 기준을 합치지 않는다.
각각 2025·2024 사업연도의 당기와 전기를 함께 남겨 기간 선택을 검증한다.
표 자체에는 연도 숫자와 비중이 없으므로 접수연도나 계산한 비중을 붙이지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

import pytest

from src.core import revenue_table_switch as switch
from src.features.revenuemix.logic import build, build_multi_year
from src.shared.revenue_table_provenance import (
    revenue_region_names_in,
    revenue_row_evidence_matches,
    revenue_table_source_excerpt,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures"
CUSTOMER_FIXTURE = "glovis_customer_region_2026.txt"
ENTITY_FIXTURE = "glovis_entity_region_2025.txt"
CUSTOMER_ROWS = [
    ["시장 본사 소재지 국가", "8,901,123,985"],
    ["외국 중국", "692,047,836"],
    ["아시아", "3,493,845,796"],
    ["북미", "7,576,328,016"],
    ["중남미", "2,255,800,616"],
    ["유럽", "6,227,026,620"],
    ["기타", "420,236,468"],
    ["시장 합계", "29,566,409,337"],
]
ENTITY_ROWS = [
    ["본사 소재지 국가", "21,508,837,497"],
    ["중국", "380,564,075"],
    ["아시아", "1,375,770,523"],
    ["북미", "3,072,659,865"],
    ["중남미", "665,265,854"],
    ["유럽", "1,404,276,487"],
    ["합계", "28,407,374,301"],
]


@pytest.fixture(autouse=True)
def enable_v2(monkeypatch: pytest.MonkeyPatch):
    switch._reset_process_revenue_table_switch_for_tests()
    monkeypatch.setenv(switch.REVENUE_TABLE_V2_ENV_NAME, "1")
    yield
    switch._reset_process_revenue_table_switch_for_tests()


@pytest.mark.parametrize(
    ("fixture_name", "expected_digest"),
    (
        (CUSTOMER_FIXTURE, "9e3b210619889de72e53a3cf5e58b9e3d768f261ba9f106560f01ff7acaa4348"),
        (ENTITY_FIXTURE, "dea3be1b4b02024e69ff2fe3203523f98bc50937b77753c94830b5d7f5f60dc1"),
    ),
)
def test_fixtures_preserve_original_bytes(fixture_name: str, expected_digest: str):
    assert hashlib.sha256((FIXTURES / fixture_name).read_bytes()).hexdigest() == expected_digest


@pytest.mark.parametrize(
    ("fixture_name", "expected_rows", "expected_header", "prior_total"),
    (
        (
            CUSTOMER_FIXTURE,
            CUSTOMER_ROWS,
            "고객소재지 기준 지역에 대한 공시 당기 (단위 : 천원) 수익(매출액) ",
            "28,407,374,301",
        ),
        (
            ENTITY_FIXTURE,
            ENTITY_ROWS,
            "지역에 대한 공시 당기 (단위 : 천원) 지역 지역 합계 본사 소재지 국가 외국 중국 아시아 북미 중남미 유럽 수익(매출액)",
            "25,683,197,164",
        ),
    ),
)
def test_original_table_amounts_period_and_provenance(
    fixture_name: str, expected_rows: list[list[str]], expected_header: str, prior_total: str
):
    source = (FIXTURES / fixture_name).read_text(encoding="utf-8")
    tables = build(source, cite="[1]")

    assert len(tables) == 1
    table = tables[0]
    assert table["axis"] == "region"
    assert table["rows"] == table["raw_rows"] == expected_rows
    assert table["headers"] == ["구분", "매출액 (천원)"]
    assert table["caption"] == "어디서 번 돈인가 — 지역별 매출액 · 비중은 공시에 없어 적지 않았습니다"
    assert table["cite"] == "[1]"
    # 당기 2025·2024의 금액을 선택하며, 바로 뒤 전기 표와 혼합하지 않는다.
    assert prior_total in source
    assert all(row[1] != prior_total for row in table["rows"])
    assert sum(Decimal(row[1].replace(",", "")) for row in table["rows"][:-1]) == Decimal(
        table["rows"][-1][1].replace(",", "")
    )
    assert build_multi_year(source) == []
    for index, (row, evidence) in enumerate(zip(table["rows"], table["evidence_rows"])):
        assert json.loads(evidence)["table"]["header"]["text"] == expected_header
        assert revenue_row_evidence_matches(
            evidence,
            cited_source_text=revenue_table_source_excerpt(table["evidence_rows"]),
            filing_text=source,
            headers=table["headers"],
            public_row=row,
            raw_row=table["raw_rows"][index],
            expected_selected_index=index,
            expected_row_count=len(expected_rows) - 1,
        )
        assert not revenue_row_evidence_matches(
            evidence,
            cited_source_text=source,
            filing_text=source,
            headers=table["headers"],
            public_row=[row[0], "1"],
        )


def test_region_leaf_names_and_spans_preserve_central_south_america():
    header = "본사 소재지 국가 외국 북미 중남미 남미 유럽"
    names = revenue_region_names_in(header)
    assert [name.text for name in names] == ["본사 소재지 국가", "북미", "중남미", "남미", "유럽"]
    assert all(header[name.start:name.end] == name.text for name in names)


@pytest.mark.parametrize("label", ("수익(매출액)", "매출액", "영업수익"))
def test_adjacent_column_header_works_without_company_or_amount_special_cases(label: str):
    source = f"지역에 대한 공시 당기 (단위 : 백만원) {label} 국내 360 해외 240 합계 600"
    assert build(source)[0]["rows"] == [["국내", "360"], ["해외", "240"], ["합계", "600"]]


@pytest.mark.parametrize(
    "source",
    (
        # 먼 앞 문장에만 매출이 있으면 단위 뒤 자산표를 받아서는 안 된다.
        "매출은 증가했습니다. 지역에 대한 공시 당기 (단위 : 천원) 자산 국내 60 해외 40 합계 100",
        # 앞 문장이 단위 뒤 첫 행 이름의 열 머리말을 대신할 수 없다.
        "수익(매출액)은 증가했습니다. 지역별 자산 현황 (단위 : 천원) 국내 60 해외 40 합계 100",
        # 머리말이 붙어 있어도 기존 투자표 거부 관문은 그대로 유지한다.
        "지역별 투자계획 (단위 : 천원) 수익(매출액) 국내 60 해외 40 합계 100",
        # 첫 표 합계가 없으면 다음 단위 표시를 넘어 빌려오지 않는다.
        "지역에 대한 공시 (단위 : 천원) 수익(매출액) 국내 60 해외 40 (단위 : 천원) 합계 100",
    ),
)
def test_recovery_does_not_cross_table_or_revenue_boundaries(source: str):
    assert build(source) == []


def test_recovery_still_requires_exact_amount_sum():
    source = (FIXTURES / CUSTOMER_FIXTURE).read_text(encoding="utf-8")
    current_table = source.split(" 전기 ", 1)[0]
    assert build(current_table.replace("29,566,409,337", "29,566,409,338")) == []


def test_recovered_rows_reject_the_old_truncated_region_name():
    source = (FIXTURES / ENTITY_FIXTURE).read_text(encoding="utf-8")
    table = build(source)[0]
    index = next(index for index, row in enumerate(table["rows"]) if row[0] == "중남미")
    assert not revenue_row_evidence_matches(
        table["evidence_rows"][index],
        cited_source_text=source,
        filing_text=source,
        headers=table["headers"],
        public_row=["남미", "665,265,854"],
    )
