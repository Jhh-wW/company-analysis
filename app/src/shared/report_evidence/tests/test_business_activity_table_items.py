"""동일 회사의 당기 표에서 둘째 사업도 원문 조건을 그대로 지켜 읽는다."""
import pytest

from src.shared.report_evidence.business_activity_table import (
    activity_table_item, activity_table_items, activity_table_ranges,
)

TEXT = (
    "당기 중 당사가 제공한 서비스 내역입니다. ① 당기\n\n"
    "서비스종류 | 당기매출액 | 상태 ; 회계 자문 서비스 | 30 | 운영 ; "
    "교육 서비스 | 20 | 운영 ; 교육 서비스 | 10 | 운영"
)


def test_all_current_rows_preserve_first_item_and_exact_range():
    assert activity_table_items(TEXT) == ("회계 자문 서비스", "교육 서비스")
    assert activity_table_item(TEXT) == "회계 자문 서비스"
    assert tuple(activity_table_ranges(TEXT)) == ((0, len(TEXT)),)


@pytest.mark.parametrize("changed", (
    TEXT.replace("당사가", "고객사가"),
    TEXT.replace("당사가", "당사의 자회사가"),
    TEXT.replace("① 당기", "② 전기"),
    TEXT.replace("제공한", "제공할 예정인"),
    TEXT.replace("당기매출액", "계약예정금액"),
    TEXT.replace("서비스종류", "고객명"),
    "다른기업의 사업 개요: " + TEXT,
    TEXT + " ; 깨진 행 | 10",
))
def test_multiple_items_do_not_borrow_owner_period_or_malformed_rows(changed):
    assert activity_table_items(changed, "가온기업") == ()


def test_inactive_zero_and_generic_rows_do_not_become_additional_items():
    text = TEXT + " ; 분석 서비스 | 10 | 중단 ; 관리 서비스 | 0 | 운영 ; 기타 | 20 | 운영"
    assert activity_table_items(text) == ("회계 자문 서비스", "교육 서비스")


def test_prior_table_after_current_table_does_not_supply_an_item():
    text = TEXT + "\n\n② 전기\n\n서비스종류 | 당기매출액 ; 과거 자문 서비스 | 20"
    assert activity_table_items(text) == ("회계 자문 서비스", "교육 서비스")


@pytest.mark.parametrize("period", ("전기", "전기말", "전년도", "과거", "차기 예정", "계획"))
@pytest.mark.parametrize("prior_first", (False, True))
def test_explicit_row_period_does_not_borrow_current_table_heading(period, prior_first):
    rows = [f"교육 서비스 | 20 | {period}", "회계 자문 서비스 | 30 | 당기"]
    if not prior_first:
        rows.reverse()
    text = ("당기 중 당사가 제공한 서비스 내역입니다. ① 당기\n\n"
            "서비스종류 | 당기매출액 | 기간 ; " + " ; ".join(rows))
    assert activity_table_items(text) == ("회계 자문 서비스",)
    assert activity_table_item(text) == "회계 자문 서비스"
    assert tuple(activity_table_ranges(text)) == ((0, len(text)),)


def test_electrical_work_item_is_not_a_period_label():
    text = ("당기 중 당사가 제공한 서비스 내역입니다. ① 당기\n\n"
            "서비스종류 | 당기매출액 | 기간 ; 전기 설비 공사 | 30 | 당기")
    assert activity_table_items(text) == ("전기 설비 공사",)
