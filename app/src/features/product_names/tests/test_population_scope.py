"""XML 제품표의 부문·누적 범위가 제품 행까지 남는다."""
from src.features.product_names.tables import parse_filing_tables
from src.features.product_names.logic import collect_name_candidates_from_tables
from src.shared.revenue_population_scope import revenue_population_claim_problem


def test_같은행의_부문과_누적머리말을_이름근거에_보존한다():
    markup = ("<P>2. 주요 제품 및 서비스</P><P>[일반설비 부문]</P><P>가. 주요 제품 현황 (누적)</P>"
              "<TABLE><TR><TH>품목</TH><TH>매출액</TH><TH>비중</TH></TR>"
              "<TR><TD>정밀 밸브</TD><TD>600</TD><TD>60%</TD></TR></TABLE>"
              "<P>3. 원재료와 생산설비</P><P>새 표</P><TABLE><TR><TD>다른 항목</TD></TR></TABLE>")
    tables = parse_filing_tables(markup)
    assert tables[0].population_heading == "일반설비 부문"
    assert tables[1].population_heading == ""
    candidates = collect_name_candidates_from_tables(tables, source_kind="사업보고서")
    candidate = next(candidate for candidate in candidates if candidate.name == "정밀 밸브")
    assert candidate.excerpt.startswith("[일반설비 부문]\n가. 주요 제품 현황 (누적)")
    assert "600" in candidate.excerpt and "60%" in candidate.excerpt
    assert "품목 | 매출액 | 비중" in candidate.excerpt
    assert revenue_population_claim_problem("정밀 밸브는 전체 매출의 대부분이다.", {"1": candidate.excerpt})
