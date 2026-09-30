"""본문·카드의 공통 검수는 부문 매출을 회사 전체로 넓힐 수 없다."""
from src.features.composer.scope_guard import flow_scope_problem, scope_problem


def test_제품카드와_본문의_전사매출_확대는_모두_거절한다():
    sources = {"18": "[제조 부문]\n주요 제품 현황 (누적)\n품목: 설비 | 매출액: 600 | 비중: 60%"}
    assert scope_problem("설비는 전체 매출의 대부분을 차지한다.", sources) == "scope_condition_unbound"
    assert flow_scope_problem(("설비", "산업장비", "전체 매출의 대부분", "주력 제품"), sources) == "scope_condition_unbound"
    assert not scope_problem("제조 부문의 전체 매출에서 설비는 60%를 차지한다.", sources)
