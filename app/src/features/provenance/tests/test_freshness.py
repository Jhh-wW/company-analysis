"""날짜 경고(W6) 시험 — 4-3 전용, 항상 붙는다, 경계값을 갈라 본다."""

from __future__ import annotations

import datetime as dt

import pytest

from src.features.pipeline.port import ReportSection
from src.features.provenance.constants import DIRECTION_WARNING_LINES
from src.features.provenance.freshness import append_direction_warning, is_stale


def _section(cell: str, *, lines=None, empty_reason: str = "") -> ReportSection:
    return ReportSection(
        cell=cell, title=cell, lines=lines or [], empty_reason=empty_reason
    )


# ══════════════════════════════════════════════════════════
# append_direction_warning — 4-3 전용, 항상 붙는다
# ══════════════════════════════════════════════════════════


def test_4_3에는_경고_두_줄이_붙는다():
    section = _section("4-3", lines=[("2027년까지 해외 매출 비중을 40%로 늘린다", "5")])
    got = append_direction_warning(section)
    붙은_경고 = tuple(text for text, _cite in got.lines[-2:])
    assert 붙은_경고 == DIRECTION_WARNING_LINES


def test_경고_문구는_기획서_원문과_한_글자도_다르지_않다():
    section = _section("4-3", lines=[("방향 문장", "1")])
    got = append_direction_warning(section)
    assert got.lines[-2][0] == "⚠️ 이 시점 이후 방향이 바뀌었을 수 있습니다."
    assert got.lines[-1][0] == "   면접 전 최근 뉴스를 한 번 더 확인하세요."


def test_4_3이_아니면_경고를_붙이지_않는다():
    section = _section("2", lines=[("잘하는 것", "1")])
    got = append_direction_warning(section)
    assert got == section
    assert got.lines == section.lines


def test_4_3이지만_빈칸이면_경고를_붙이지_않는다():
    """빈칸에 경고를 붙이면 「내용이 있었는데 지웠나」로 잘못 읽힌다."""
    section = _section("4-3", empty_reason="이 회사의 공개 자료에 해당 내용이 없습니다")
    got = append_direction_warning(section)
    assert got == section
    assert got.lines == []


def test_원본_항목은_바뀌지_않는다():
    """frozen dataclass다 — 새 객체를 돌려줘야지 원본을 고치면 안 된다."""
    section = _section("4-3", lines=[("방향 문장", "1")])
    원본_lines_id = id(section.lines)
    append_direction_warning(section)
    assert len(section.lines) == 1
    assert id(section.lines) == 원본_lines_id


def test_두_번_불러도_경고가_한_번만_붙는다():
    """idempotent — 재시도 파이프라인에서 여러 번 불러도 경고가 안 쌓인다."""
    section = _section("4-3", lines=[("방향 문장", "1")])
    한번 = append_direction_warning(section)
    두번 = append_direction_warning(한번)
    assert 두번 == 한번
    assert len(두번.lines) == 3  # 문장 1 + 경고 2


def test_표만_있어도_채워진_것으로_보고_경고를_붙인다():
    """문장이 없어도 tables가 있으면 is_filled다 (D13) — 표에도 경고가 붙어야 한다."""
    from src.features.pipeline.port import ReportTable

    표 = ReportTable(caption="c", headers=["a"], rows=[["1"]], cite="c")
    section = ReportSection(cell="4-3", title="4-3", tables=[표])
    got = append_direction_warning(section)
    assert len(got.lines) == 2


# ══════════════════════════════════════════════════════════
# is_stale — 경계값 · 파싱 실패
# ══════════════════════════════════════════════════════════


def test_상한_안이면_신선하다():
    assert is_stale("2024-01-01", max_years=3, today=dt.date(2026, 8, 15)) is False


def test_상한을_넘으면_낡았다():
    assert is_stale("2020-01-01", max_years=3, today=dt.date(2026, 8, 15)) is True


def test_경계일_당일은_아직_신선하다():
    """딱 3년째 되는 날은 «넘은» 게 아니다 — 하루가 지나야 넘는다."""
    assert is_stale("2023-08-15", max_years=3, today=dt.date(2026, 8, 15)) is False


def test_경계일_다음날부터_낡았다():
    assert is_stale("2023-08-15", max_years=3, today=dt.date(2026, 8, 16)) is True


def test_윤년_2월29일_기준일도_처리한다():
    """2024-02-29 + 3년 = 2027년(평년)에는 2월 29일이 없다 — 2월 28일로 앞당긴다."""
    assert is_stale("2024-02-29", max_years=3, today=dt.date(2027, 2, 28)) is False
    assert is_stale("2024-02-29", max_years=3, today=dt.date(2027, 3, 1)) is True


@pytest.mark.parametrize("bad", ["", "모름", "2025/03/12", "2025-13-01", None])
def test_날짜를_못_읽으면_모름으로_돌려준다(bad):
    """신선함(False)으로 잘못 세면 실제로 낡은 자료를 놓친다 — None으로 구분한다."""
    assert is_stale(bad, today=dt.date(2026, 8, 15)) is None


def test_today를_안_주면_실제_오늘을_기준으로_한다():
    아주_오래된_날짜 = "2000-01-01"
    assert is_stale(아주_오래된_날짜, max_years=3) is True
