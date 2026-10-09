"""집계 부문을 지키며 같은 품목 행의 숫자·지문을 보존한다."""
import json

from src.features.revenuemix.logic import _build_v2
from src.shared.revenue_table_provenance import revenue_row_evidence_matches, revenue_table_axis_matches, revenue_table_source_excerpt


def test_부문누적표의_명시품목열과_원문_행결속을_보존한다():
    text = ("2. 주요 제품 및 서비스\n[일반설비 부문]\n주요 제품 현황 (누적)\n"
            "(단위: 백만원)\n사업부문 | 품목 | 매출액 비중\n"
            "제조 | 정밀 밸브 600 60.0%\n상품 | 순환 펌프 400 40.0%\n합계 1,000 100.0%")
    tables, _ = _build_v2(text)
    assert len(tables) == 1
    table = tables[0]
    assert table["rows"] == [["정밀 밸브", "600", "60.0%"], ["순환 펌프", "400", "40.0%"], ["합계", "1,000", "100.0%"]]
    assert "일반설비 부문의 표 합계 기준 · 누적" in table["caption"]
    excerpt = revenue_table_source_excerpt(table["evidence_rows"])
    assert excerpt.startswith("[일반설비 부문]")
    assert revenue_table_axis_matches(axis=table["axis"], caption=table["caption"], evidence_rows=table["evidence_rows"], cited_source_text=excerpt)
    for index, evidence in enumerate(table["evidence_rows"]):
        assert revenue_row_evidence_matches(evidence, cited_source_text=excerpt, filing_text=text,
            headers=table["headers"], public_row=table["rows"][index], raw_row=table["raw_rows"][index])
        payload = json.loads(evidence)
        assert text[payload["row"]["start"]:payload["row"]["end"]] == payload["row"]["raw_match"]
    assert not revenue_row_evidence_matches(table["evidence_rows"][0], cited_source_text=excerpt, filing_text=text,
        headers=table["headers"], public_row=["순환 펌프", "400", "40.0%"], raw_row=table["raw_rows"][0])
