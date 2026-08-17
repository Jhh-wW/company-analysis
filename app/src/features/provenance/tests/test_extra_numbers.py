"""附(참고 숫자) 시험 — 상장/비상장 분기, 빈칸 사유 3종.

★ 여기서 쓰는 응답 필드명(`jan_salary_am` 등)은 «확신하지 못하는 값»이다.
  자세한 사항은 `extra_numbers.parse_emp_status_table()` docstring 참고.
"""

from __future__ import annotations

import pytest

from src.core.constants import CELL_LABELS
from src.features.provenance.constants import (
    EMPTY_REASON_FETCH_FAILED,
    EMPTY_REASON_NO_DATA,
    EMPTY_REASON_STRUCTURAL,
)
from src.features.provenance.extra_numbers import (
    build_extra_numbers_section,
    emp_status_empty_reason,
    is_unlisted,
    parse_emp_status_table,
)

# DART OpenAPI 문서 기준으로 가장 유력한 필드명 (검증 안 됨 — 후보키 시험용)
정상_응답 = {
    "status": "000",
    "message": "정상",
    "list": [
        {
            "fo_bbm": "반도체",
            "sexdstn": "남",
            "jan_salary_am": "8천5백만원",
            "avrg_cnwk_sdytrn": "11년 3개월",
        },
        {
            "fo_bbm": "반도체",
            "sexdstn": "여",
            "jan_salary_am": "7천만원",
            "avrg_cnwk_sdytrn": "9년 1개월",
        },
    ],
}


# ══════════════════════════════════════════════════════════
# 표 만들기 — 순수 함수, 실제 API 호출 없음
# ══════════════════════════════════════════════════════════


def test_정상_응답이면_표가_나온다():
    table = parse_emp_status_table(정상_응답)
    assert table is not None
    assert table.is_valid
    assert table.headers == ["구분", "1인평균급여액", "평균근속연수"]
    assert len(table.rows) == 2


def test_행에_구분_급여_근속연수가_제자리에_들어간다():
    table = parse_emp_status_table(정상_응답)
    구분, 급여, 근속 = table.rows[0]
    assert 구분 == "반도체 · 남"
    assert 급여 == "8천5백만원"
    assert 근속 == "11년 3개월"


def test_출처를_밝힌다():
    table = parse_emp_status_table(정상_응답)
    assert table.cite


def test_附의_한계_문구가_표에_항상_같이_나온다():
    """1인평균급여액의 한계(임원 제외·전 직원 평균)는 수치와 항상 같이 출력해야 한다."""
    table = parse_emp_status_table(정상_응답)
    assert "임원 제외" in table.caption
    assert "신입 초봉이 아닙니다" in table.caption


@pytest.mark.parametrize(
    "response",
    [
        None,
        {},
        {"status": "013", "message": "조회된 데이터가 없습니다"},
        {"status": "000", "list": []},
        {"status": "000", "list": [{"fo_bbm": "반도체", "sexdstn": "남"}]},  # 숫자가 아예 없는 행
    ],
)
def test_쓸_수_있는_행이_없으면_표를_만들지_않는다(response):
    """모양이 안 맞는데 억지로 만들면 «없는 숫자»가 화면에 생긴다."""
    assert parse_emp_status_table(response) is None


def test_구분과_성별이_전부_빈칸이면_전체로_묶는다():
    응답 = {
        "status": "000",
        "list": [{"fo_bbm": "-", "sexdstn": "-", "jan_salary_am": "8천만원"}],
    }
    table = parse_emp_status_table(응답)
    assert table.rows[0][0] == "전체"


def test_급여나_근속연수_중_하나만_있어도_행을_만든다():
    응답 = {"status": "000", "list": [{"fo_bbm": "본사", "jan_salary_am": "8천만원"}]}
    table = parse_emp_status_table(응답)
    assert table is not None
    assert table.rows[0][2] == "-"  # 근속연수 자리표시자


def test_후보키_중_다른_이름이_와도_읽는다():
    """필드명을 확신할 수 없어 후보를 여러 개 허용한다 — 그중 하나로도 읽혀야 한다."""
    응답 = {"status": "000", "list": [{"사업부문": "반도체", "1인평균급여액": "8천만원"}]}
    table = parse_emp_status_table(응답)
    assert table is not None
    assert table.rows[0][1] == "8천만원"


# ══════════════════════════════════════════════════════════
# 상장/비상장 분기
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "corp_type, want",
    [("상장사", False), ("비상장 외감", True), ("비상장", True)],
)
def test_비상장_여부_판정(corp_type, want):
    assert is_unlisted(corp_type) is want


def test_표가_있으면_비상장이어도_사유가_없다():
    """표가 있으면 그 칸은 빈칸이 아니다 — 사유 자체가 필요 없다."""
    table = parse_emp_status_table(정상_응답)
    reason = emp_status_empty_reason(corp_type="비상장 외감", table=table)
    assert reason == ""


def test_비상장이면_호출이_실패했어도_구조적_사유가_1순위다():
    """구조적 부재(비상장)가 「우리가 못 가져왔다」보다 먼저다 — 원인을 섞지 않는다."""
    reason = emp_status_empty_reason(corp_type="비상장 외감", table=None, fetch_failed=True)
    assert reason == EMPTY_REASON_STRUCTURAL


def test_비상장이고_표가_없으면_구조적_사유다():
    reason = emp_status_empty_reason(corp_type="비상장 외감", table=None)
    assert reason == EMPTY_REASON_STRUCTURAL


def test_상장사인데_API_호출이_실패하면_못_가져온_사유다():
    reason = emp_status_empty_reason(corp_type="상장사", table=None, fetch_failed=True)
    assert reason == EMPTY_REASON_FETCH_FAILED


def test_상장사인데_자료가_없으면_회사에_없는_사유다():
    reason = emp_status_empty_reason(corp_type="상장사", table=None, fetch_failed=False)
    assert reason == EMPTY_REASON_NO_DATA


def test_사유_셋은_서로_다르다():
    """❌·⚠️·구조적 사유를 섞으면 오거부다 — 세 문구가 우연히라도 같으면 안 된다."""
    사유들 = {EMPTY_REASON_NO_DATA, EMPTY_REASON_FETCH_FAILED, EMPTY_REASON_STRUCTURAL}
    assert len(사유들) == 3


# ══════════════════════════════════════════════════════════
# ReportSection 조립 — real.py·demo.py 연결 지점
# ══════════════════════════════════════════════════════════


def test_표가_있으면_附_항목이_채워진다():
    section = build_extra_numbers_section(response=정상_응답, corp_type="상장사")
    assert section.cell == "附"
    assert section.title == CELL_LABELS["附"]
    assert section.is_filled is True
    assert section.empty_reason == ""


def test_비상장이면_附_항목이_구조적_사유로_빈다():
    section = build_extra_numbers_section(response=None, corp_type="비상장 외감")
    assert section.is_filled is False
    assert section.empty_reason == EMPTY_REASON_STRUCTURAL


def test_상장사인데_호출_실패면_못_가져온_사유로_빈다():
    section = build_extra_numbers_section(
        response=None, corp_type="상장사", fetch_failed=True
    )
    assert section.is_filled is False
    assert section.empty_reason == EMPTY_REASON_FETCH_FAILED
