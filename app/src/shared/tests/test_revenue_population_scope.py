"""매출표의 부문·분모·기간이 바뀌거나 전사로 확대되지 않는다."""
import pytest

from src.shared.revenue_population_scope import (
    revenue_population_caption, revenue_population_caption_matches,
    revenue_population_claim_problem, revenue_population_context_start,
)


def test_부문표_캡션은_분모와_누적기간을_명시한다():
    source = "[일반설비 부문] 주요 제품 현황 (누적) 별도기준 (단위: 백만원)"
    caption = revenue_population_caption("제품·서비스별 매출 비중", source)
    assert caption == "제품·서비스별 매출 비중 · 집계 범위: 일반설비 부문의 표 합계 기준 · 별도기준 · 누적"
    assert revenue_population_caption_matches(caption, source)
    assert not revenue_population_caption_matches(caption.replace("별도", "연결"), source)
    assert not revenue_population_caption_matches(caption.replace("누적", "1분기"), source)


def test_기간기준은_실제머리말에서만_읽는다():
    header = "[일반설비 부문] 주요 제품 현황 (누적) (단위: 백만원) 품목 매출액 비율"
    source = header + " 2025년 연결모듈 600 60.0% 펌프 400 40.0% 합계 1000 100.0%"
    caption = revenue_population_caption("제품·서비스별 매출 비중", source, header_text=header)
    assert "2025" not in caption and "연결" not in caption
    assert caption.endswith("누적")


@pytest.mark.parametrize("separator", ["\n", " "])
def test_부문머리말은_다음상위절로_이어지지_않는다(separator):
    text = f"[제조 부문] 주요 제품{separator}3. 원재료와 생산설비{separator}(단위: 백만원) 구분 매출액 비율"
    header_start = text.index("(단위")
    assert revenue_population_context_start(text, header_start) == header_start


def test_구형_캡션은_새표시기준으로_차단하거나_고치지_않는다():
    old = "제품·서비스별 매출 비중"
    assert revenue_population_caption_matches(old, "[제조 부문] 주요 제품 현황 (누적)")
    assert old == "제품·서비스별 매출 비중"


@pytest.mark.parametrize("candidate", ["타이어는 전체 매출의 대부분을 차지한다.", "설비의 비중은 회사 전체 매출의 60%이다."])
def test_부문표만으로_회사전체_매출을_단정할_수_없다(candidate):
    source = "[제조 부문] 품목 | 매출액 | 비중 ; 설비 | 600 | 60.0% ; 펌프 | 400 | 40.0%"
    assert revenue_population_claim_problem(candidate, {"1": source}) == "scope_condition_unbound"


def test_해당부문_분모와_원문명시_전사분모는_유지한다():
    limited = "[제조 부문] 품목 | 매출액 | 비중 ; 설비 | 600 | 60.0%"
    assert not revenue_population_claim_problem("제조 부문의 전체 매출에서 설비는 60%이다.", {"1": limited})
    whole = "회사 전체 매출 | 품목 | 비중 ; 설비 | 60.0%"
    assert not revenue_population_claim_problem("설비는 회사 전체 매출의 60%이다.", {"1": whole})


def test_모집단_미확인_표를_회사전체로_승격하지_않는다():
    assert revenue_population_claim_problem("설비는 전체 매출의 60%이다.", {"1": "품목 | 매출액 | 비중 ; 설비 | 600 | 60.0%"})


def test_품목명에_있는_전체매출은_전사분모가_아니다():
    source = "[설비 부문] 품목 | 매출액 | 비중 ; 회사 전체 매출 분석 서비스 | 600 | 60.0%"
    assert revenue_population_claim_problem("회사 전체 매출의 60%는 분석 서비스이다.", {"1": source})


def test_품목명의_부문표시는_집계범위가_아니다():
    source = "주요 제품 현황(누적)\n품목: [설비부문] 분석 서비스 | 매출액: 600 | 비중: 60%"
    assert revenue_population_claim_problem("설비부문 내 전체 매출의 60%는 분석 서비스이다.", {"1": source})


def test_부문명과_제품의_연결은_연결재무_기준이_아니다():
    caption = revenue_population_caption("매출 비중", "[연결솔루션사업부문] 제품별 매출액 2025년 누적")
    assert caption.endswith("표 합계 기준 · 2025년 · 누적")


def test_기간별_매출금액표는_구성비_분모검사의_대상이_아니다():
    source = "매출액 | 2022년 | 400억원\n매출액 | 2023년 | 300억원\n매출액 | 2024년 | 200억원\n매출액 | 2025년 | 100억원"
    assert not revenue_population_claim_problem("전체 매출 규모가 3년 연속 감소했다.", {"1": source})


@pytest.mark.parametrize("candidate", [
    "설비 부문은 전체 사업 포트폴리오에서 압도적 비중을 차지하는 주력 사업이다.",
    "설비는 회사 전체에서 비중이 가장 큰 사업이다.",
    "설비는 포트폴리오의 핵심이다.",
    "설비는 주력 사업이다.",
    "제조 부문 내 전체 매출에서 설비는 60%이고 회사 전체에서는 1위다.",
])
def test_부문_분모는_각_포트폴리오_비교표현에도_결속된다(candidate):
    source = "[제조 부문] 품목 | 매출액 | 비중 ; 설비 | 600 | 60% ; 펌프 | 400 | 40%"
    assert revenue_population_claim_problem(candidate, {"1": source})


@pytest.mark.parametrize("candidate", [
    "제조 부문 내 포트폴리오에서 설비는 주력 사업이다.",
    "제조 부문 내 전체 매출에서 설비는 대부분의 비중을 차지한다.",
    "제조 부문에서 설비는 주력 사업이다.",
    "제조 부문에서는 설비가 핵심 사업이다.",
    "설비는 주력 제품이며 고객에게 공급한다.",
])
def test_부문내_비교와_제품_정의는_유지한다(candidate):
    source = "[제조 부문] 품목 | 매출액 | 비중 ; 설비 | 600 | 60% ; 펌프 | 400 | 40%"
    assert not revenue_population_claim_problem(candidate, {"1": source})


def test_전사_분모와_직접_사업정의는_유지한다():
    whole = "회사 전체 매출 | 품목 | 비중 ; 설비 | 600 | 60%"
    assert not revenue_population_claim_problem("설비는 전체 사업 포트폴리오의 주력 사업이다.", {"1": whole})
    part = "[제조 부문] 품목 | 매출액 | 비중 ; 설비 | 600 | 60%"
    definition = "회사는 산업 설비 제작을 주력 사업으로 영위한다."
    assert not revenue_population_claim_problem("설비 제작은 주력 사업이다.", {"1": part, "2": definition})
    assert revenue_population_claim_problem("설비 제작은 전체 사업 포트폴리오에서 압도적 비중이다.", {"1": part, "2": definition})
