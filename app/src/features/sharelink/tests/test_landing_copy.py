"""초대 링크 첫 화면(랜딩) 문구를 글자 그대로 못 박는다 (티켓 G-S6).

★ 왜 문구에 시험이 필요한가 — 문구는 「대충 비슷하면 된다」가 아니다. 이 화면의
  독자는 개발자가 아니라 인사팀이고, 코드 용어 한 단어가 「고장 났나」로 읽힌다.
  화면 시험(`web/tests/test_link_landing.py`)은 «렌더된 결과»를 보고, 여기서는
  «원본 문구»를 본다. 둘 다 있어야 상수만 조용히 바뀌는 것도 걸린다.

★ 판정 기준은 설계 03장 §5(쓰지 않을 말)와 2026-09-02 사용자 지시(「우리」·「내」).
"""

from __future__ import annotations

from src.features.sharelink import constants


#: 설계 03장 §5가 「관리자 화면에서만 쓴다」고 못 박은 말 + 코드에서만 쓰는 영어.
_금지어 = (
    "LINK",
    "capability",
    "KRW",
    "hash",
    "bucket",
    "audience",
    "provider",
    "통장",
    "갈래",
    "예약",
    "원장",
    "철회",
    "쿼터",
    "우리",
)

#: 랜딩에 쓰는 문구 상수 전부. 하나를 새로 만들고 여기에 안 넣으면 금지어 검사를
#: 빠져나가므로, 새 문구를 더할 때 이 목록도 같이 늘린다.
_랜딩문구 = (
    "LANDING_TITLE",
    "LANDING_INTRO",
    "LANDING_REPORT_BUTTON_TEMPLATE",
    "LANDING_REPORT_MADE_ON_TEMPLATE",
    "LANDING_MADE_ON_DATE_TEMPLATE",
    "LANDING_REPORT_NOT_READY_TEMPLATE",
    "LANDING_REPORT_NOT_READY_NOTE",
    "LANDING_OTHER_COMPANY_BUTTON",
    "LANDING_OTHER_COMPANY_NOTE",
    "LANDING_BUDGET_LEFT_TEMPLATE",
)


def test_랜딩_문구는_내부용어와_만든쪽_사정을_쓰지_않는다():
    """★ 「우리 회사 보고서」는 받는 사람에게 만든 쪽을 가리켜 헷갈린다."""
    for 이름 in _랜딩문구:
        문구 = getattr(constants, 이름)
        for 금지 in _금지어:
            assert 금지.casefold() not in 문구.casefold(), f"{이름}: {금지}"


def test_버튼A는_회사명을_그대로_넣는다():
    """★ 예: 「하이브 보고서 보기」. 「우리 회사」도 「내 회사」도 아니다 (D-G10)."""
    assert (
        constants.LANDING_REPORT_BUTTON_TEMPLATE.format(company="하이브")
        == "하이브 보고서 보기"
    )
    assert (
        constants.LANDING_REPORT_NOT_READY_TEMPLATE.format(company="하이브")
        == "하이브 보고서는 준비 중입니다"
    )


def test_버튼B는_다른_회사_분석해_보기다():
    """★ 「다른 회사도 된다」가 이 링크의 두 번째 할 일이다 (D-G10)."""
    assert constants.LANDING_OTHER_COMPANY_BUTTON == "다른 회사 분석해 보기"


def test_생성일은_한국어_날짜로_쓴다():
    """★ ISO(2026-08-19)는 개발자 표기다. 인사팀에게는 「8월 19일」이다."""
    made_on = constants.LANDING_MADE_ON_DATE_TEMPLATE.format(
        year=2026, month=9, day=2
    )
    assert made_on == "2026년 9월 2일"
    assert (
        constants.LANDING_REPORT_MADE_ON_TEMPLATE.format(made_on=made_on)
        == "2026년 9월 2일에 만든 보고서"
    )


def test_남은_한도는_원_단위로_두_가지를_말한다():
    """★ 하루와 전체는 «다른 값»이다. 하나만 보이면 언제 막힐지 모른다 (D-G1)."""
    assert (
        constants.LANDING_BUDGET_LEFT_TEMPLATE.format(daily="2,999", total="3,000")
        == "남은 이용 한도: 오늘 2,999원 · 전체 3,000원"
    )


def test_소진_문구는_하루와_수명전체가_서로_다르다():
    """★ 하루 소진은 내일 열리고 누적 소진은 안 열린다. 같은 말을 하면 거짓말이다."""
    하루 = constants.LINK_BUDGET_EXHAUSTED_MESSAGE
    누적 = constants.LINK_TOTAL_BUDGET_EXHAUSTED_MESSAGE

    assert 하루 != 누적
    assert "내일 다시 열립니다" in 하루
    assert "내일" not in 누적
    # 둘 다 «그래도 볼 수 있는 것»을 같이 말한다.
    assert "보실 수 있습니다" in 하루
    assert "볼 수 있습니다" in 누적
