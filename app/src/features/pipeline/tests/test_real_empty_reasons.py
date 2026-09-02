"""빈칸 사유가 «사실»을 말하는지 못 박는다.

★ 이 시험이 잡는 것 — **있는 것을 없다고 말하는 것.**
  1판 엔진은 칸마다 고정 문구를 조건 없이 붙였다(`run_pilot.py:509-514` + `:535`).
  그래서 뉴스를 6건 모아 놓고도 「채택 조건을 통과한 기사 없음」이라고 말했다.
  같은 화면의 수집 현황은 「검색 20건 중 6건 채택」이라 말하고 있었다 —
  **화면이 스스로 모순된 상태였다.**

★ 왜 중요한가 — 사용자가 할 일이 갈린다.
  「재료가 없다」면 회사를 바꿔야 하고, 「재료는 있는데 안 뽑혔다」면 다시 돌리면 된다.
  섞어 말하면 멀쩡한 회사를 포기한다.

★ 진짜 조사 실측(유료)에서 이 거짓 때문에 **루트로닉·토스씨엑스의
  4번이 왜 비었는지 판정할 수 없었다.** 진단을 막는 결함이었다.
"""

from __future__ import annotations

from src.core.constants import (
    EMPTY_REASON_HOMEPAGE,
    EMPTY_REASON_NOT_COMPANY_SPECIFIC,
    SUBSTANCE_FAILED_REASON,
    TABLE_DUMP_REASON,
)
from src.features.homepage.constants import FRAGMENT_KIND as HOMEPAGE_KIND
from src.features.pipeline.port import ReportSection
from src.features.pipeline.real import _refresh_empty_reasons
from src.features.spanselect.constants import NEWS_FRAGMENT_KIND

#: 1판이 붙이던 고정 문구 — 이것이 갈아 끼울 대상이다.
_1판_고정문구 = "채택 조건(제목 회사명·3년·동명 단서)을 통과한 기사 없음 · MD&A 재료 없음"


class _가짜엔진:
    CELL_SOURCES = {"4-1": ("MD&A", NEWS_FRAGMENT_KIND)}
    EMPTY_REASONS = {"4-1": _1판_고정문구}


def _빈칸(cell: str = "4-1", reason: str = _1판_고정문구) -> ReportSection:
    return ReportSection(cell=cell, title="지금 뭐가 문제인가", empty_reason=reason)


def _사유(section: ReportSection, **kwargs) -> str:
    기본 = {
        "homepage_state": "",
        "homepage_detail": "",
        "engine": _가짜엔진(),
        "collected_kinds": set(),
        "news_step": {},
    }
    기본.update(kwargs)
    state = 기본.pop("homepage_state")
    detail = 기본.pop("homepage_detail")
    return _refresh_empty_reasons([section], state, detail, **기본)[0].empty_reason


# ══════════════════════════════════════════════════════════
# ① 있는 것을 없다고 말하지 않는다 — 이 시험의 핵심
# ══════════════════════════════════════════════════════════


def test_뉴스를_모았으면_없다고_말하지_않는다():
    """★ 루트로닉이 기사 6건을 모아 두고도 「채택된 기사 없음」이라 말하던 그 버그."""
    사유 = _사유(_빈칸(), collected_kinds={NEWS_FRAGMENT_KIND})

    assert "없음" not in 사유, f"재료를 모았는데 「없음」이라 말합니다: {사유}"
    assert NEWS_FRAGMENT_KIND in 사유
    assert "배치된 문장이 없습니다" in 사유


def test_재료가_있을_때와_없을_때의_사유가_다르다():
    """섞어 말하면 사용자가 «회사를 바꿔야 하는지»를 판단할 수 없다."""
    있음 = _사유(_빈칸(), collected_kinds={NEWS_FRAGMENT_KIND})
    없음 = _사유(_빈칸(), news_step={"검색결과": 12})

    assert 있음 != 없음
    assert "못 구했습니다" in 없음


def test_다른_회사에도_통하는_일반론은_그_이유를_정확히_말한다():
    사유 = _사유(
        _빈칸(),
        collected_kinds={NEWS_FRAGMENT_KIND},
        specificity_rejected_cells={"4-1"},
    )

    assert 사유 == EMPTY_REASON_NOT_COMPANY_SPECIFIC


def test_재료가_없으면_소스마다_실제_상태를_말한다():
    사유 = _사유(_빈칸(), news_step={"검색결과": 12})

    assert "12" in 사유, "검색은 12건 했다는 사실이 빠지면 「아예 안 찾아봤나」와 구별이 안 된다"
    assert "MD&A" in 사유


def test_뉴스_검색이_실패한_것과_결과가_0건인_것을_가른다():
    """⚠️(우리가 못 가져옴)와 ❌(회사에 자료가 없음)를 섞으면 안 된다."""
    실패 = _사유(_빈칸(), news_step={"오류": "타임아웃"})
    없음 = _사유(_빈칸(), news_step={"검색결과": 0})

    assert "실패" in 실패
    assert "실패" not in 없음


# ══════════════════════════════════════════════════════════
# ② 앱이 «직접» 붙인 사유는 이미 사실이므로 건드리지 않는다
# ══════════════════════════════════════════════════════════


def test_표_덩어리_사유는_덮어쓰지_않는다():
    사유 = _사유(_빈칸(reason=TABLE_DUMP_REASON), collected_kinds={NEWS_FRAGMENT_KIND})

    assert 사유 == TABLE_DUMP_REASON


def test_알맹이_미달_사유는_덮어쓰지_않는다():
    사유 = _사유(
        _빈칸(reason=SUBSTANCE_FAILED_REASON), collected_kinds={NEWS_FRAGMENT_KIND}
    )

    assert 사유 == SUBSTANCE_FAILED_REASON


def test_문장이_있는_칸은_손대지_않는다():
    채워진칸 = ReportSection(
        cell="4-1", title="지금 뭐가 문제인가", lines=[("회사가 무엇을 했다.", "조각 1·뉴스")]
    )

    사유 = _사유(채워진칸, collected_kinds={NEWS_FRAGMENT_KIND})

    assert 사유 == ""


# ══════════════════════════════════════════════════════════
# ③ 홈페이지 — 붙여 놓고 「안 붙였다」고 말하지 않는다
# ══════════════════════════════════════════════════════════


def test_홈페이지를_읽었으면_그렇게_말한다():
    class _홈페이지쓰는엔진(_가짜엔진):
        CELL_SOURCES = {"4-3": ("MD&A", NEWS_FRAGMENT_KIND)}
        EMPTY_REASONS = {"4-3": _1판_고정문구}

    사유 = _사유(
        _빈칸(cell="4-3"),
        engine=_홈페이지쓰는엔진(),
        homepage_state="ok",
        news_step={"검색결과": 0},
    )

    assert EMPTY_REASON_HOMEPAGE["ok"] in 사유
    assert "미연결" not in 사유, "붙여 놓고 「아직 안 붙였다」고 말하면 거짓이다 (P-49)"


def test_홈페이지_조각을_모았으면_없다고_말하지_않는다():
    class _홈페이지쓰는엔진(_가짜엔진):
        CELL_SOURCES = {"4-3": ("MD&A",)}
        EMPTY_REASONS = {"4-3": _1판_고정문구}

    사유 = _사유(
        _빈칸(cell="4-3"),
        engine=_홈페이지쓰는엔진(),
        homepage_state="ok",
        collected_kinds={HOMEPAGE_KIND},
    )

    assert HOMEPAGE_KIND in 사유
    assert "배치된 문장이 없습니다" in 사유
