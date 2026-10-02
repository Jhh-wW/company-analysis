"""관리 표·약한 단일어의 장 오배치를 막고 실제 사업 설명은 보존한다."""

from __future__ import annotations

from features.evidence_collection import relevance


def _slots(text: str, heading: str = "") -> tuple[set[str], bool]:
    scores, observed = relevance.score_fragment_slots_with_signal(text, heading)
    return {score.slot_id for score in scores}, observed


def test_순수_개인보수표만_작성근거에서_제외하고_재분류도_막는다() -> None:
    table = (
        "이름 | 보수의 종류 | 보수의 종류 | 총액 | 산정기준 및 방법 ; "
        "가상임원 | 근로소득 | 급여 | 100 | 경영성과금과 주주가치 평가 ; "
        "가상임원 | 퇴직소득 | 퇴직소득 | 10 | 직무의 가치"
    )
    assert _slots(table) == (set(), True)
    assert "business_model:revenue_model" in _slots(
        "회사는 급여관리 서비스를 고객에게 과금하고 고객가치를 높인다."
    )[0]
    assert _slots(
        "회사는 급여관리 서비스를 고객에게 과금한다.\n\n" + table
    )[0]
    assert _slots(
        "이름 | 보수의 종류 | 총액 | 산정기준 및 방법 ; "
        "제품 | 서비스 | 100 | 고객에게 서비스를 제공한다"
    )[0]
    mixed_table = (
        "이름 | 보수의 종류 | 보수의 종류 | 총액 | 산정기준 및 방법 ; "
        "가상임원 | 근로소득 | 급여 | 100 | 경영성과금 ; "
        "가상서비스 | 고객서비스 | 이용료 | 200 | 고객에게 서비스를 제공하고 과금한다"
    )
    assert "business_model:revenue_model" in _slots(mixed_table)[0]


def test_주식_관리표의_생산_단독만_운영역할에서_제외한다() -> None:
    admin = (
        "구분 | 발행주식 총수 | 이사회 ; "
        "정관 사업목적 | 제품 생산 및 판매 | 주주 승인"
    )
    slots, observed = _slots(admin, "주식에 관한 사항")
    assert "operations_partners:operating_role" not in slots
    assert observed
    purpose_only = (
        "구분 | 발행주식 총수 | 이사회 ; "
        "정관 사업목적 | 제품을 생산하여 고객에게 공급한다 | 주주 승인"
    )
    slots, observed = _slots(purpose_only, "주식에 관한 사항")
    assert "operations_partners:operating_role" not in slots
    assert observed
    assert "operations_partners:operating_role" in _slots(
        "회사는 증권 거래 플랫폼을 운영한다.", "사업 현황"
    )[0]
    assert "operations_partners:operating_role" in _slots(
        "회사는 제품을 개발·제조하고 생산하여 거래처에 공급한다.", "사업 현황"
    )[0]
    mixed_table = (
        "구분 | 발행주식 총수 | 이사회 ; "
        "정관 사업목적 | 제품 생산 및 판매 | 주주 승인 ; "
        "실제 사업 | 제품을 생산하여 고객에게 공급한다 | 납품"
    )
    assert "operations_partners:operating_role" in _slots(
        mixed_table, "주식에 관한 사항"
    )[0]


def test_감사_진행현황과_상시판매절차는_미래계획이_아니다() -> None:
    audit = "구분 | 진행 현황 | 계약 ; 회계감사 | 진행 현황 | 이사회 승인"
    slots, observed = _slots(audit, "감사 계약")
    assert "future_strategy:plan_status" not in slots
    assert observed
    routine = (
        "고객 개발계획을 검토하고 개발에 착수한다. "
        "고객 생산계획에 따라 공급을 진행한다."
    )
    slots, observed = _slots(routine, "판매경로")
    assert "future_strategy:plan_status" not in slots
    assert observed


def test_명시적_미래사업_투자상태_취소는_계속_후보로_남는다() -> None:
    for text, heading, slot in (
        ("회사는 2026년 급여관리 서비스 확대를 추진할 계획입니다.", "사업 계획", "future_strategy:plan_timing"),
        ("설비 투자가 진행 중입니다.", "생산설비 현황", "future_strategy:plan_status"),
        ("투자 계획을 취소했습니다.", "투자계획", "future_strategy:plan_status"),
        ("2026년 설비 증설을 추진할 계획입니다.", "사업계획", "future_strategy:plan_timing"),
        ("회사는 향후 감사서비스 확대에 착수할 예정입니다.", "사업 계획", "future_strategy:plan_status"),
        ("2026년 기업대출 심사체계를 고도화할 계획입니다.", "사업계획", "future_strategy:plan_timing"),
    ):
        assert slot in _slots(text, heading)[0]


def test_명시적_업종중립_미래행동은_약신호_문맥에서_제외하지_않는다() -> None:
    from features.evidence_collection.weak_signal_context import future_signal_has_context

    for text, hit in (
        ("2027년 임상시험을 실시할 예정이다.", "예정"),
        ("내년 새 매장을 개점할 예정이다.", "예정"),
        ("하반기 채용을 시작할 계획이다.", "계획"),
    ):
        assert future_signal_has_context(text, "사업계획", (hit,))


def test_회계측정표의_가치_단독만_고객가치에서_제외한다() -> None:
    table = "구분 | 자산 가치평가 | 측정 ; 금융자산 | 자산 가치 | 100"
    slots, observed = _slots(table, "금융자산 측정")
    assert "business_model:value_exchange" not in slots
    assert observed
    assert "business_model:value_exchange" in _slots(
        "회사는 고객에게 자산 가치평가 서비스를 제공하고 고객가치를 높인다."
    )[0]
