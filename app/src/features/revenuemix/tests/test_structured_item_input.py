"""원XML 셀 구조로 연결된 품목만 별도 매출 입력에 표시한다."""
import copy
import json

import pytest

from src.features.revenuemix.logic import _build_v2, revenue_input_from_tables
from src.shared.revenue_table_provenance import revenue_row_evidence_matches, revenue_table_source_excerpt


HEADER = ("사업부문", "매출유형", "품 목", "구체적용도", "주요상표등", "매출액 (비율)", "매출액 (비율)")
ROWS = [HEADER,
        ("설비", "제품", "정밀 밸브", "공장 유체 제어", "자체상품", "600", "60.0%"),
        ("설비", "제품", "순환 펌프", "공장 유체 이송", "자체상품", "400", "40.0%"),
        ("합계", "합계", "합계", "합계", "합계", "1,000", "100.0%")]
TEXT = ("2. 주요 제품 및 서비스 [일반설비 부문] 주요 제품 현황 (누적) "
        "(단위: 백만원, %) 사업부문 매출유형 품 목 구체적용도 주요상표등 매출액 (비율) "
        "설비 제품 정밀 밸브 공장 유체 제어 자체상품 600 60.0% "
        "설비 제품 순환 펌프 공장 유체 이송 자체상품 400 40.0% 합계 1,000 100.0%")


def grid():
    return {"population_heading": "일반설비 부문", "rows": copy.deepcopy(ROWS)}


@pytest.fixture(autouse=True)
def fresh_switch():
    from src.core import revenue_table_switch
    revenue_table_switch._reset_process_revenue_table_switch_for_tests()
    yield
    revenue_table_switch._reset_process_revenue_table_switch_for_tests()


@pytest.mark.parametrize("distinct_ratio_header", [False, True])
def test_명시품목셀과_수치분모범위를_기존검증기로_결속한다(monkeypatch, distinct_ratio_header):
    monkeypatch.setenv("REVENUE_TABLE_V2", "1")
    original = _build_v2(TEXT)[0][0]
    cells = grid()
    if distinct_ratio_header:
        cells["rows"][0] = (*HEADER[:-2], "매출액", "비중")
    updated = revenue_input_from_tables(TEXT, [cells])
    assert updated != TEXT
    new = _build_v2(updated)[0][0]
    assert [row[0] for row in new["rows"][:-1]] == ["정밀 밸브", "순환 펌프"]
    assert [row[1:] for row in new["rows"]] == [row[1:] for row in original["rows"]]
    assert new["caption"] == original["caption"]
    assert new["headers"] == original["headers"]
    excerpt = revenue_table_source_excerpt(new["evidence_rows"])
    assert "품 목 | 매출액" in excerpt
    for i, evidence in enumerate(new["evidence_rows"]):
        assert revenue_row_evidence_matches(evidence, cited_source_text=excerpt, filing_text=updated,
            headers=new["headers"], public_row=new["rows"][i], raw_row=new["raw_rows"][i])
        assert not revenue_row_evidence_matches(evidence, cited_source_text=excerpt, filing_text=TEXT,
            headers=new["headers"], public_row=new["rows"][i], raw_row=new["raw_rows"][i])
        assert json.loads(evidence)["source"]["filing_sha256"] != json.loads(original["evidence_rows"][i])["source"]["filing_sha256"]
    # 과거 봉인은 과거 평문에 계속 결속한다.
    old_excerpt = revenue_table_source_excerpt(original["evidence_rows"])
    assert revenue_row_evidence_matches(original["evidence_rows"][0], cited_source_text=old_excerpt, filing_text=TEXT,
        headers=original["headers"], public_row=original["rows"][0], raw_row=original["raw_rows"][0])


@pytest.mark.parametrize("problem", ["duplicate_table", "other_population", "changed_amount", "changed_ratio", "missing_cell", "swapped_items", "ambiguous_header", "wrong_xml_item", "missing_total", "long_item", "disabled"])
def test_불명확한_표나_행은_기존긴원문입력을_유지한다(monkeypatch, problem):
    monkeypatch.setenv("REVENUE_TABLE_V2", "1")
    candidate = grid()
    cells = [list(row) for row in candidate["rows"]]
    candidate["rows"] = cells
    grids = [candidate]
    if problem == "duplicate_table": grids.append(copy.deepcopy(candidate))
    elif problem == "other_population": candidate["population_heading"] = "다른회사 다른부문"
    elif problem == "changed_amount": cells[1][-2] = "700"
    elif problem == "changed_ratio": cells[1][-1] = "70.0%"
    elif problem == "missing_cell": cells[1].pop(2)
    elif problem == "swapped_items": cells[1][2], cells[2][2] = cells[2][2], cells[1][2]
    elif problem == "ambiguous_header": cells[0][3] = "품목"
    elif problem == "wrong_xml_item": cells[1][2] = "다른 장비"
    elif problem == "missing_total": cells.pop()
    elif problem == "long_item": cells[1][2] = "아주 긴 원문 품목 " * 30
    elif problem == "disabled": monkeypatch.setenv("REVENUE_TABLE_V2", "0")
    assert revenue_input_from_tables(TEXT, grids) == TEXT


def test_다른제품표와_지역표를_합치거나_수치를_옮기지_않는다(monkeypatch):
    monkeypatch.setenv("REVENUE_TABLE_V2", "1")
    region = " 3. 지역별 매출 (단위: 백만원) 매출지역 매출액 비중 국내 700 70.0% 해외 300 30.0% 합계 1,000 100.0%"
    text = TEXT + region
    updated = revenue_input_from_tables(text, [grid()])
    assert updated.endswith(region)
    old_regions = [t for t in _build_v2(text)[0] if t["axis"] == "region"]
    new_regions = [t for t in _build_v2(updated)[0] if t["axis"] == "region"]
    assert len(old_regions) == len(new_regions) == 1
    assert old_regions[0]["rows"] == new_regions[0]["rows"]
    assert old_regions[0]["caption"] == new_regions[0]["caption"]


def test_같은금액과비율의_다른품목행은_순서만으로_결속하지_않는다(monkeypatch):
    monkeypatch.setenv("REVENUE_TABLE_V2", "1")
    text = TEXT.replace("600 60.0%", "500 50.0%").replace("400 40.0%", "500 50.0%")
    candidate = grid()
    candidate["rows"] = [list(row) for row in candidate["rows"]]
    candidate["rows"][1][-2:] = ["500", "50.0%"]
    candidate["rows"][2][-2:] = ["500", "50.0%"]
    assert _build_v2(text)[0]
    assert revenue_input_from_tables(text, [candidate]) == text


@pytest.mark.parametrize("old_period,new_period", [("2025년 누적", "2024년"), ("제25기", "제24기"), ("2025년 3분기", "2025년 2분기"), ("2025년 상반기", "2025년 하반기")])
def test_같은수치라도_명시된기간이_충돌하면_기존입력을_유지한다(monkeypatch, old_period, new_period):
    monkeypatch.setenv("REVENUE_TABLE_V2", "1")
    text = TEXT.replace("현황 (누적)", f"현황 ({old_period})")
    candidate = grid()
    candidate["rows"][0] = (*HEADER[:-2], f"{new_period} 매출액", "비중")
    assert revenue_input_from_tables(text, [candidate]) == text


@pytest.mark.parametrize("old_period,new_period", [("2025년 누적", "2025년"), ("누적", "2025년"), ("2025년 누적", "")])
def test_기간일치나_한쪽기간미표시는_기존유일행결속을_유지한다(monkeypatch, old_period, new_period):
    monkeypatch.setenv("REVENUE_TABLE_V2", "1")
    text = TEXT.replace("현황 (누적)", f"현황 ({old_period})")
    candidate = grid()
    candidate["rows"][0] = (*HEADER[:-2], f"{new_period} 매출액", "비중")
    updated = revenue_input_from_tables(text, [candidate])
    assert updated != text
    assert f"현황 ({old_period})" in updated
    assert [row[1:] for row in _build_v2(updated)[0][0]["rows"]] == [row[1:] for row in _build_v2(text)[0][0]["rows"]]


@pytest.mark.parametrize("upper_header,compatible", [("2024년", False), ("단위: 천원", False), ("2025년", True), ("단위: 백만원", True)])
def test_다단머리말의_명시기간과단위도_같은문맥으로_검증한다(monkeypatch, upper_header, compatible):
    monkeypatch.setenv("REVENUE_TABLE_V2", "1")
    text = TEXT.replace("현황 (누적)", "현황 (2025년 누적)")
    candidate = grid()
    candidate["rows"].insert(0, (upper_header,) * len(HEADER))
    updated = revenue_input_from_tables(text, [candidate])
    assert (updated != text) is compatible


@pytest.mark.parametrize("title,compatible", [("제품 현황 2024년", False), ("제품 현황 (단위: 천원)", False), ("제품 현황 2025년 (단위: 백만원)", True)])
def test_XML표제의_명시기간단위도_평문머리말과_대조한다(monkeypatch, title, compatible):
    monkeypatch.setenv("REVENUE_TABLE_V2", "1")
    text = TEXT.replace("현황 (누적)", "현황 (2025년 누적)")
    candidate = grid()
    candidate["title"] = title
    assert (revenue_input_from_tables(text, [candidate]) != text) is compatible
