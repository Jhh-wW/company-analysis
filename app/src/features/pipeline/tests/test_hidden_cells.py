"""회사분석 보고서에서 옛 직무·공고 칸이 정말 안 나가는지 못 박는다.

⚠️ 이 시험이 없으면 나중에 누가 `HIDDEN_CELLS`를 안 거치는 경로를 하나 더 만들어도
  **조용히 되살아난다.** 실제로 이 기능을 넣었을 때 시험 1,068개가 하나도 안 깨졌다 —
  «지켜 주는 것이 아무것도 없다»는 뜻이었다.
"""

from __future__ import annotations

from src.core.constants import COUNTED_CELLS, HIDDEN_CELLS
from src.features.pipeline import demo
from src.features.pipeline.port import Grade, Report


def test_뺀_칸이_정해져_있다():
    assert HIDDEN_CELLS == ("5", "6", "7", "8")


def test_데모_보고서에_직무공고_칸이_안_나온다():
    """★ 데모와 진짜 조사가 «같은 보고서»를 내놔야 한다.

    갈라지면 사용자가 같은 상황에서 다른 화면을 보고 다른 문제로 착각한다.
    """
    record = demo._find_record("하이브")
    report = demo._load_report(record)
    assert report is not None

    assert [s.cell for s in report.sections if s.cell in HIDDEN_CELLS] == []


def test_뺀_칸은_등급에도_안_들어간다():
    """★★ 거의 항상 비던 칸이다 — 등급에 남겨 두면 «내용은 좋은데 등급만 낮은» 보고서가 된다.

    실측 — 10곳 중 4곳 이상은 거래처 실명이 애초에 공시에 없고(57%),
    있는 회사도 재료의 91%가 표라 문장으로 못 만든다.
    """
    record = demo._find_record("하이브")
    report = demo._load_report(record)
    assert report is not None

    for cell in HIDDEN_CELLS:
        assert cell not in report.cells


def test_만드는_코드는_살아_있다():
    """★ 되살리려면 목록에서 빼기만 하면 된다 — 만드는 쪽을 지우지 않았다.

    ⚠️ 여기가 깨지면 「없앤 것」이 아니라 「지운 것」이 된 것이다.
    """
    from src.core.constants import CELL_LABELS

    for cell in HIDDEN_CELLS:
        assert cell in CELL_LABELS


def test_채운_개수는_공고블록과_비등급_회사칸을_세지_않는다():
    report = Report(
        company="가나다",
        job="영업",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=[],
        cells={
            **{cell: cell in {"1", "2"} for cell in COUNTED_CELLS},
            "5": True,
            "8": True,
            "9": True,
        },
    )

    assert report.filled_count == 2
