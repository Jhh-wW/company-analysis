"""작가→근거 대조를 통과한 문장과 실제 출처가 보고서에 함께 남는지 본다.

P-118의 실패는 작가·검증 단위 시험이 모두 통과해도 생겼다. 두 기능 사이에서
내부 근거 번호를 문자열로 합치는 파이프라인 경계가 근거를 버렸기 때문이다.
"""

from __future__ import annotations

from typing import Any

from src.features.pipeline.port import ReportSection, UserInput
from src.features.pipeline.real import _write_prose


class _Engine:
    MODEL = "시험모델"

    def __init__(self, verdict: bool = True, writer_text: str = "") -> None:
        self.calls = 0
        self.verdict = verdict
        self.writer_text = writer_text

    def _ask(
        self,
        _client: Any,
        _prompt: str,
        _schema: dict[str, Any],
        *,
        max_tokens: int,
    ) -> tuple[dict[str, Any], dict[str, int]]:
        assert max_tokens > 0
        self.calls += 1
        if self.calls == 1:
            return {
                "칸": [
                    {
                        "칸번호": "1",
                        "문장들": [
                            {
                                "글": self.writer_text
                                or "하이브는 2025년 위버스 디지털 멤버십 구독 서비스를 출시해 수익을 창출했다.",
                                "근거": "1-1",
                            }
                        ],
                    }
                ]
            }, {}
        return {"판정": [{"번호": 1, "근거에있다": self.verdict}]}, {}


def _section() -> ReportSection:
    return ReportSection(
        cell="1",
        title="사업 구조",
        lines=[
            (
                "하이브는 2025년 위버스 디지털 멤버십 구독 서비스를 출시해 수익을 창출했다.",
                "조각 10·사업보고서",
            )
        ],
    )


def test_통과한_작가문장에_실제출처를_붙이고_원문도_보존한다() -> None:
    original = _section()
    sections, written_cells = _write_prose(
        _Engine(),
        object(),
        UserInput(company="하이브", job="기획", region="서울"),
        [original],
        [],
        "시험모델",
    )

    assert written_cells == {"1"}
    assert sections[0].prose_lines == [
        (
            "하이브는 2025년 위버스 디지털 멤버십 구독 서비스를 출시해 수익을 창출했다.",
            "조각 10·사업보고서",
        )
    ]
    assert sections[0].lines == original.lines


def test_근거대조가_거짓이면_표시용글_없이_원문으로_돌아간다() -> None:
    original = _section()
    sections, written_cells = _write_prose(
        _Engine(
            verdict=False,
            writer_text="하이브는 위버스 서비스로 새로운 수익을 만들었다.",
        ),
        object(),
        UserInput(company="하이브", job="기획", region="서울"),
        [original],
        [],
        "시험모델",
    )

    assert written_cells == set()
    assert sections == [original]
