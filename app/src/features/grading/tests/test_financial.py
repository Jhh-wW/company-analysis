"""재무 표 복원 시험 (결정기록 D13).

★ 가장 중요한 것 — **없는 숫자를 만들어내지 않는다.**
  모양이 조금이라도 다르면 표로 만들지 않고 원래 규칙(D12)에 맡긴다.
"""

from __future__ import annotations

import pytest

from src.features.grading.financial import (
    DART_FINANCIAL_PREFIX,
    MIN_FINANCIAL_ROWS,
    is_financial_line,
    parse_financial_table,
)

# 1판이 실제로 만든 줄이다. 줄이면 시험의 뜻이 없어진다.
로보스타_재무 = (
    "주요계정(DART API): 유동자산 76,330,498,769(2025.12.31 현재) · "
    "비유동자산 32,811,922,450(2025.12.31 현재) · "
    "자산총계 109,142,421,219(2025.12.31 현재) · "
    "유동부채 22,041,433,666(2025.12.31 현재) · "
    "비유동부채 290,867,720(2025.12.31 현재)"
)


# ── 알아보기 ────────────────────────────────────────────

def test_재무_줄을_알아본다():
    assert is_financial_line(로보스타_재무) is True


@pytest.mark.parametrize(
    "text",
    [
        "당사는 재생의학 기술을 기반으로 의약품을 제조 및 판매합니다.",
        "(단위: 원) 구분 회사명 거래내역 당기 전기",
        "",
    ],
)
def test_재무_줄이_아닌_것은_안_건드린다(text):
    assert is_financial_line(text) is False
    assert parse_financial_table(text) is None


# ── 표로 되돌리기 ───────────────────────────────────────

def test_뭉개진_재무_줄이_표가_된다():
    table = parse_financial_table(로보스타_재무)
    assert table is not None
    assert table.is_valid
    assert len(table.rows) == 5
    assert table.headers == ["계정", "금액(원)", "기준"]


def test_계정명과_금액과_기준일이_제자리에_들어간다():
    table = parse_financial_table(로보스타_재무)
    이름, 금액, 기준 = table.rows[0]
    assert 이름 == "유동자산"
    assert 금액.startswith("76,330,498,769")
    assert 기준 == "2025.12.31 현재"


def test_원본_숫자를_지우지_않는다():
    """읽기 쉬우라고 「억」을 «덧붙이는» 것이지 바꾸는 게 아니다."""
    table = parse_financial_table(로보스타_재무)
    for _이름, 금액, _기준 in table.rows:
        assert "," in 금액, "원본 숫자가 사라졌습니다"


def test_큰_금액에는_억_단위를_덧붙인다():
    table = parse_financial_table(로보스타_재무)
    assert "억)" in table.rows[0][1]


def test_출처를_밝힌다():
    table = parse_financial_table(로보스타_재무)
    assert table.cite


# ── ★ 없는 숫자를 만들지 않는다 ─────────────────────────

def test_모양이_다른_조각이_하나라도_있으면_표로_만들지_않는다():
    """억지로 맞추면 «화면에 없는 숫자»가 생긴다. 그럴 바엔 안 만든다."""
    깨진 = 로보스타_재무 + " · 이건금액이없는항목"
    assert parse_financial_table(깨진) is None


def test_항목이_너무_적으면_표로_만들지_않는다():
    """한두 줄은 표보다 문장이 낫다."""
    짧은 = f"{DART_FINANCIAL_PREFIX} 유동자산 1,000,000(2025.12.31 현재)"
    assert parse_financial_table(짧은) is None


def test_문턱_경계에서_갈린다():
    항목 = " · ".join(
        f"계정{i} {i},000,000(2025.12.31 현재)" for i in range(1, MIN_FINANCIAL_ROWS + 1)
    )
    assert parse_financial_table(f"{DART_FINANCIAL_PREFIX} {항목}") is not None

    부족 = " · ".join(
        f"계정{i} {i},000,000(2025.12.31 현재)" for i in range(1, MIN_FINANCIAL_ROWS)
    )
    assert parse_financial_table(f"{DART_FINANCIAL_PREFIX} {부족}") is None


def test_기준일이_없어도_표는_만든다():
    """괄호가 빠진 형태도 있다. 기준 칸만 비우고 표는 살린다."""
    text = f"{DART_FINANCIAL_PREFIX} 자산총계 1,000,000 · 부채총계 2,000,000 · 자본총계 3,000,000"
    table = parse_financial_table(text)
    assert table is not None
    assert table.rows[0][2] == ""


# ── 화면과의 약속 ───────────────────────────────────────

def test_표가_있으면_그_칸은_채워진_것이다():
    """숫자 표도 «내용»이다. 문장이 없다고 빈칸으로 두면 안 된다 (D13)."""
    from src.features.pipeline.port import ReportSection

    table = parse_financial_table(로보스타_재무)
    section = ReportSection(cell="3", title="요즘 잘 되고 있나", tables=[table])
    assert section.is_filled is True


def test_완전히_같은_행은_한_번만_보여준다():
    """전자공시가 같은 계정을 두 번 내려주는 경우가 있다."""
    text = (
        f"{DART_FINANCIAL_PREFIX} 자산총계 1,000,000(2025) · 부채총계 2,000,000(2025) · "
        "자본총계 3,000,000(2025) · 자산총계 1,000,000(2025)"
    )
    table = parse_financial_table(text)
    assert len(table.rows) == 3


def test_값이_하나라도_다르면_남긴다():
    """★ 다른 숫자를 지우면 «사실이 사라진다»."""
    text = (
        f"{DART_FINANCIAL_PREFIX} 매출액 1,000,000(2025) · 매출액 2,000,000(2024) · "
        "자본총계 3,000,000(2025)"
    )
    table = parse_financial_table(text)
    assert len(table.rows) == 3


def test_열_개수가_안_맞는_표는_내보내지_않는다():
    """화면이 깨진다."""
    from src.features.pipeline.port import ReportTable

    나쁜표 = ReportTable(caption="x", headers=["a", "b"], rows=[["1"]])
    assert 나쁜표.is_valid is False
