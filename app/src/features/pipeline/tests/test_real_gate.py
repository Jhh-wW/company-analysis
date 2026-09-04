"""사전 게이트가 «있는 재료를 있다고» 세는지 검사한다.

게이트는 생성 AI를 부르기 «전에» 0원으로 멈춰 세우는 장치다. 그래서 반대로
**재료가 있는데 없다고 세면 멀쩡한 회사가 보고서를 못 받는다.**

★ 이 시험이 잡는 것 — 1판 엔진의 `CELL_SOURCES`에는 「홈페이지」가 **어느 칸에도
  없다**(`analysis_engine/tools/run_pilot.py:124-132`). 홈페이지 수집을 붙여 놓고도
  게이트가 그 조각을 못 세서, 2번·4-2·4-3의 재료가 홈페이지뿐인 회사는
  「미달」로 멈췄다. 1판을 못 고치므로 앱 층이 게이트 계산에서만 넓힌다.
"""

from __future__ import annotations

import pytest

from src.core.constants import HOMEPAGE_GATE_CELLS
from src.features.homepage.constants import FRAGMENT_KIND as HOMEPAGE_KIND
from src.features.pipeline import real


def _engine_cell_sources() -> dict[str, tuple[str, ...]]:
    """1판 엔진의 칸↔조각종류 대응표. 엔진을 «실행하지 않고» 글자로 읽는다."""
    import ast

    source = (real.paths.PROJECT_ROOT / "analysis_engine" / "tools" / "run_pilot.py").read_text(
        encoding="utf-8"
    )
    tree = ast.parse(source)
    for node in tree.body:
        targets = getattr(node, "targets", []) or [getattr(node, "target", None)]
        for target in targets:
            if isinstance(target, ast.Name) and target.id == "CELL_SOURCES":
                return ast.literal_eval(node.value)
    pytest.skip("1판 엔진에서 CELL_SOURCES를 찾지 못했습니다")


def test_1판_대응표에는_홈페이지가_없다():
    """★ 이 보정의 근거. 이게 깨지면 1판이 바뀐 것이니 아래 보정을 다시 볼 것."""
    sources = _engine_cell_sources()

    쓰이는_종류 = {kind for kinds in sources.values() for kind in kinds}

    assert HOMEPAGE_KIND not in 쓰이는_종류, (
        "1판이 홈페이지를 대응표에 넣었다면 앱 층 보정(HOMEPAGE_GATE_CELLS)은 이제 불필요하다"
    )


def test_홈페이지_조각만_있어도_게이트가_그_칸을_인정한다():
    """보정이 없으면 이 세 칸이 전부 False가 되어 게이트에서 멈춘다."""
    sources = _engine_cell_sources()
    kinds = {HOMEPAGE_KIND}

    rough = {c: any(k in kinds for k in srcs) for c, srcs in sources.items()}
    assert not any(rough.values()), "보정 «전»에는 아무 칸도 안 세어져야 한다 — 이게 깨지면 1판이 바뀐 것이다"

    for cell in HOMEPAGE_GATE_CELLS:
        if cell in rough:
            rough[cell] = True

    assert all(rough[c] for c in HOMEPAGE_GATE_CELLS if c in rough)


def test_보정하는_칸은_정본이_정한_세_칸뿐이다():
    """넓히면 게이트를 통과하는 요청이 늘어 **비용이 늘어난다.** 함부로 늘리지 않는다.

    2번(홈페이지·기술블로그) ·
    4-2(회사 채용 페이지) · 4-3(홈페이지 회사소개·IR).
    """
    assert set(HOMEPAGE_GATE_CELLS) == {"2", "4-2", "4-3"}


def test_보정_대상_칸이_1판_대응표에_실제로_있다():
    """칸 이름을 잘못 적으면 보정이 «조용히 아무 일도 안 한다»."""
    sources = _engine_cell_sources()

    for cell in HOMEPAGE_GATE_CELLS:
        assert cell in sources, f"1판 대응표에 없는 칸 이름입니다: {cell}"
