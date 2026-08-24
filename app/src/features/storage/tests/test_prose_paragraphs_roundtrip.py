"""문단이 저장·재로드에서 살아남는지 못 박는다.

★ 왜 (같은 유형의 실측 사고) — v2 본문이 저장 로더에서 통째로 사라진 적이
  있다(v2-08). 화면에서 잘 보이던 것이 다시 열면 없어지는 사고다. 문단도
  저장하지 않으면 재로드 시 사라져 «한 덩어리»로 돌아간다.
★ 동시에, 옛 보고서의 저장 바이트는 그대로여야 한다 — 해시 결속 검사가
  바이트를 본다. 그래서 문단이 없으면 키 자체를 넣지 않는다.
"""

from __future__ import annotations

import json

from src.features.pipeline.port import ReportSection
from src.features.storage.reports import _section_from_dict, _section_to_dict


def _section(paragraphs: list[str]) -> ReportSection:
    return ReportSection(
        cell="identity",
        title="기업 정체성",
        prose_lines=[("첫 문장이다. [1]", ""), ("둘째 문장이다. [2]", "")],
        prose_paragraphs=paragraphs,
    )


def test_문단이_저장_왕복에서_살아남는다():
    원본 = _section(["첫 문장이다. [1]", "둘째 문장이다. [2]"])

    복원 = _section_from_dict(
        json.loads(json.dumps(_section_to_dict(원본))), is_v2=True
    )

    assert 복원.prose_paragraphs == ["첫 문장이다. [1]", "둘째 문장이다. [2]"]


def test_문단이_없으면_저장_키_자체를_넣지_않는다():
    """옛 보고서의 저장 바이트와 해시가 그대로 유지돼야 한다."""
    저장본 = _section_to_dict(_section([]))

    assert "prose_paragraphs" not in 저장본


def test_문단이_없는_옛_저장본도_읽힌다():
    저장본 = _section_to_dict(_section([]))

    복원 = _section_from_dict(json.loads(json.dumps(저장본)), is_v2=True)

    assert 복원.prose_paragraphs == []
    assert len(복원.prose_lines) == 2
