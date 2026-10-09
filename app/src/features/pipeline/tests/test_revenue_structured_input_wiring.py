"""원공시 평문과 신규 매출 파서 입력의 지문·검증 배선이 혼동되지 않는다."""
import hashlib
import json

from src.features.pipeline import real
from src.features.pipeline.tests.test_revenue_table_diagnostics_step import _collect
from src.features.product_names.tables import FilingTable


def test_원공시를_바꾸지_않고_새매출입력으로_행근거를_검증한다(monkeypatch):
    text = ("2. 주요 제품 및 서비스 [일반설비 부문] 주요 제품 현황 (누적) "
            "(단위: 백만원, %) 사업부문 매출유형 품목 구체적용도 매출액 비중 "
            "제조 제품 정밀밸브 유체제어 600 60.0% 제조 제품 순환펌프 유체이송 400 40.0% 합계 1,000 100.0%")
    table = FilingTable(title="주요 제품 현황 (누적)", population_heading="일반설비 부문", rows=(
        ("사업부문", "매출유형", "품목", "구체적용도", "매출액", "비중"),
        ("제조", "제품", "정밀밸브", "유체제어", "600", "60.0%"),
        ("제조", "제품", "순환펌프", "유체이송", "400", "40.0%"),
        ("합계", "합계", "합계", "합계", "1,000", "100.0%"),
    ))
    monkeypatch.setattr(real, "read_filing_tables", lambda _path: (table,))
    original_input = real.revenuemix.revenue_input_from_tables
    grid_inputs = []

    def structure_input(filing_text, grids):
        grid_inputs.extend(grids)
        return original_input(filing_text, grids)

    monkeypatch.setattr(real.revenuemix, "revenue_input_from_tables", structure_input)
    original_bind = real._bind_revenue_table_evidence_fragments
    observations = []

    def bind(frags, tables, *, filing, filing_text):
        result = original_bind(frags, tables, filing=filing, filing_text=filing_text)
        observations.append((filing_text, tables, result))
        return result

    monkeypatch.setattr(real, "_bind_revenue_table_evidence_fragments", bind)
    before = hashlib.sha256(text.encode()).hexdigest()
    steps = _collect(text, monkeypatch)
    assert grid_inputs[0]["title"] == table.title
    assert next(step for step in steps if step["step"] == "6_수집_매출구성")["표"] == 1
    new_input, raw_tables, (fragments, bound) = observations[0]
    assert new_input != text
    assert hashlib.sha256(text.encode()).hexdigest() == before
    assert [row[0] for row in bound[0]["rows"][:-1]] == ["정밀밸브", "순환펌프"]
    assert [row[1:] for row in bound[0]["rows"]] == [["600", "60.0%"], ["400", "40.0%"], ["1,000", "100.0%"]]
    payload = json.loads(raw_tables[0]["evidence_rows"][0])
    assert payload["source"]["filing_sha256"] == hashlib.sha256(new_input.encode()).hexdigest()
    assert payload["source"]["filing_sha256"] != before
    assert any(fragment.get("원문") == payload["source"]["excerpt"] for fragment in fragments.values())
