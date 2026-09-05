"""제품·지역별 매출 비중을 연도별로 따로 검산하는 다개년 계약."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.core import revenue_table_switch as switch
from src.features.revenuemix.logic import (
    build,
    build_multi_year,
    build_multi_year_with_diagnostics,
)
from src.shared.revenue_table_provenance import (
    REVENUE_MULTI_YEAR_SELECTION,
    revenue_row_evidence_matches,
    revenue_table_axis_matches,
    revenue_table_section_id_from_caption,
    revenue_table_source_excerpt,
)


_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_V1_HYBE_GOLDEN_SHA256 = "a620b1a7bc2208f2a8f98ddc27cfd33222ab809099653e595974820a1d876e0f"
_V2_HYBE_GOLDEN_SHA256 = "0fba38c3ff88b8b1d920bfedfae530e1cb74b7daca604319ae9f6415ac4a4bf3"


def 픽스처(이름: str) -> str:
    return (_FIXTURES / f"{이름}.txt").read_text(encoding="utf-8")


@pytest.fixture(autouse=True)
def _fresh_process_revenue_table_switch():
    switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001
    yield
    switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001


def _table_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_하이브_3개년은_연도_오름차순의_고유_비중열을_만든다() -> None:
    tables = build_multi_year(픽스처("hybe_product_and_region"), cite="[8]")

    assert [table["axis"] for table in tables] == ["product", "region"]
    assert tables[0]["headers"] == ["구분", "2023 비중", "2024 비중", "2025 비중"]
    assert len(set(tables[0]["headers"])) == len(tables[0]["headers"])
    assert tables[0]["rows"][0] == [
        "음반/음원 음반, 음원 등",
        "44.56%",
        "38.17%",
        "29.17%",
    ]
    assert tables[0]["raw_rows"][0] == [
        "음반/음원 음반, 음원 등",
        "970,463",
        "860,962",
        "772,960",
    ]
    assert tables[0]["numeric_checks"][0] == [
        "44.56|1|2|44.56",
        "38.17|1|2|38.17",
        "29.17|1|2|29.17",
    ]
    assert tables[0]["raw_unit"] == "백만원"


def test_삼성전자_모양의_3개년_음수도_모든_쌍을_보존한다() -> None:
    table = build_multi_year(픽스처("samsung_product_three_year_synthetic"))[0]

    assert table["headers"] == ["구분", "2023 비중", "2024 비중", "2025 비중"]
    assert table["rows"][2] == ["기타 부문간 내부거래 제거 등", "0.0%", "△5.0%", "△10.0%"]
    assert table["numeric_checks"][2] == [
        "0.0|1|1|0.0",
        "-5.0|1|1|-5.0",
        "-10.0|1|1|-10.0",
    ]
    evidence = json.loads(table["evidence_rows"][2])
    assert evidence["row"]["selection"] == REVENUE_MULTI_YEAR_SELECTION
    assert [pair["year"] for pair in evidence["row"]["period_pairs"]] == [
        "2025",
        "2024",
        "2023",
    ]
    assert [pair["amount"]["value"] for pair in evidence["row"]["period_pairs"]] == [
        "△10,000",
        "△5,000",
        "0",
    ]


def test_한_연도_검산만_실패하면_그_연도만_빼고_진단한다() -> None:
    source = 픽스처("hybe_product_and_region").replace("860,962", "860,961", 1)

    tables, diagnostics = build_multi_year_with_diagnostics(source)
    product = next(table for table in tables if table["axis"] == "product")

    assert product["headers"] == ["구분", "2023 비중", "2025 비중"]
    assert diagnostics["제외_연도"] == {"product": ["2024"]}
    assert diagnostics["탈락_사유"]["연도 검산 실패"] == 1


def test_모든_연도가_검산에_실패하면_표없이_제외연도를_진단한다() -> None:
    source = (
        "가. 제품별 매출 (단위 : 백만원) 구분 "
        "2025년 제3기 2024년 제2기 2023년 제1기 "
        "매출액 비중 매출액 비중 매출액 비중 "
        "제품가 60 60.0% 60 60.0% 60 60.0% "
        "제품나 40 40.0% 40 40.0% 40 40.0% "
        "합계 101 100.0% 101 100.0% 101 100.0%"
    )

    tables, diagnostics = build_multi_year_with_diagnostics(source)

    assert tables == []
    assert diagnostics["제외_연도"] == {"product": ["2025", "2024", "2023"]}


def test_단년_머리말은_다개년_표를_만들지_않는다() -> None:
    assert build_multi_year(픽스처("samsung_product")) == []


def test_머리말보다_쌍이_적은_행은_추측하지_않고_버린다() -> None:
    source = (
        "가. 제품별 매출 (단위 : 백만원) 구분 "
        "2025년 제3기 2024년 제2기 2023년 제1기 "
        "매출액 비중 매출액 비중 매출액 비중 "
        "제품가 60 60.0% 60 60.0% 60 60.0% "
        "제품나 40 40.0% 40 40.0% 40 40.0% "
        "쌍부족 0 0.0% 0 0.0% "
        "합계 100 100.0% 100 100.0% 100 100.0%"
    )

    table = build_multi_year(source)[0]

    assert [row[0] for row in table["rows"]] == ["제품가", "제품나", "합계"]
    assert all(
        "쌍부족" not in json.loads(evidence)["row"]["raw_match"]
        for evidence in table["evidence_rows"]
    )


def test_다개년_행도_원문_payload_대조를_모두_통과한다() -> None:
    source = 픽스처("hybe_product_and_region")

    for table in build_multi_year(source):
        excerpt = revenue_table_source_excerpt(table["evidence_rows"])
        assert revenue_table_axis_matches(
            axis=table["axis"],
            caption=table["caption"],
            evidence_rows=table["evidence_rows"],
            cited_source_text=excerpt,
        )
        assert revenue_table_section_id_from_caption(table["caption"]) in {
            "portfolio",
            "business_model",
        }
        for index, (row, raw_row, evidence) in enumerate(
            zip(table["rows"], table["raw_rows"], table["evidence_rows"])
        ):
            assert revenue_row_evidence_matches(
                evidence,
                cited_source_text=source,
                filing_text=source,
                headers=table["headers"],
                public_row=row,
                raw_row=raw_row,
                expected_selected_index=index,
                expected_row_count=len(table["rows"]) - 1,
            )


@pytest.mark.parametrize(
    "enabled, expected",
    ((False, _V1_HYBE_GOLDEN_SHA256), (True, _V2_HYBE_GOLDEN_SHA256)),
)
def test_기존_단년_build_payload는_바이트_골든과_같다(
    enabled: bool,
    expected: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if enabled:
        monkeypatch.setenv(switch.REVENUE_TABLE_V2_ENV_NAME, "1")
    else:
        monkeypatch.delenv(switch.REVENUE_TABLE_V2_ENV_NAME, raising=False)

    payload = build(픽스처("hybe_product_and_region"), cite="[8]")

    assert hashlib.sha256(_table_bytes(payload)).hexdigest() == expected
