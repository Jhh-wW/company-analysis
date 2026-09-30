"""본문·카드의 공통 검수는 부문 매출을 회사 전체로 넓힐 수 없다."""
from src.features.composer.scope_guard import flow_scope_problem, scope_problem


def test_제품카드와_본문의_전사매출_확대는_모두_거절한다():
    sources = {"18": "[제조 부문]\n주요 제품 현황 (누적)\n품목: 설비 | 매출액: 600 | 비중: 60%"}
    assert scope_problem("설비는 전체 매출의 대부분을 차지한다.", sources) == "scope_condition_unbound"
    assert flow_scope_problem(("설비", "산업장비", "전체 매출의 대부분", "주력 제품"), sources) == "scope_condition_unbound"
    assert not scope_problem("제조 부문의 전체 매출에서 설비는 60%를 차지한다.", sources)


def test_본문과_카드는_같은_부문_포트폴리오_범위를_검사한다():
    sources = {"18": "[제조 부문]\n품목 | 매출액 | 비율 ; 설비 | 600 | 60%"}
    assert scope_problem("설비는 전체 사업 포트폴리오에서 압도적 비중을 차지한다.", sources)
    assert flow_scope_problem(("설비", "산업장비", "주력 제품으로 포트폴리오의 핵심", "주력 사업"), sources)
    assert not scope_problem("설비는 제조 부문 내 포트폴리오에서 주력 사업이다.", sources)
