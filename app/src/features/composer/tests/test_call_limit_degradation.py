# -*- coding: utf-8 -*-
"""호출 «횟수» 상한이 완성된 보고서를 통째로 버리지 못하게 막는다.

★ 왜 이 파일이 생겼나 (실측)
  ─────────────────────────────────────────────────────────
  장별 최소 문장을 6→8로 올리자 작가 산출이 58→70·87문장으로 늘었고,
  «거짓» 판정 문장의 재작성 호출이 한 요청 상한(18회)을 넘었다.
  그 «한 번»의 초과로 이미 완성된 9개 장이 통째로 버려지고 화면에는
  「보고서를 만들다 오류가 났습니다」만 남았다(현대카드·우리은행 둘 다).

★ 무엇이 잘못이었나
  「돈이 떨어졌다」와 「이 요청에 허락된 호출 수를 다 썼다」가 같은 예외
  타입이라 구분할 수 없었다. 앞은 요청을 멈추는 게 맞고, 뒤는 손에 든
  보고서를 낼 수 있다.

★ 이 시험이 지키는 것
  ① 횟수 상한이면 «선택적 다듬기»만 포기하고 보고서는 나온다 (3곳)
  ② 돈·계정 장애는 «여전히» 요청 전체를 멈춘다  ← 안전선
  ③ 새 예외는 기존 예산 예외의 «자식»이라 기존 처리는 하나도 안 바뀐다
"""

from __future__ import annotations

import json

import pytest

from src.features.budget import provider_budget
from src.features.composer.diagram_check import (
    VERDICT_TRUE,
    check_diagrams,
)
from src.features.composer.verify import (
    MAX_REWRITE_CALLS_PER_VERIFY,
    REVIEW_PROMPT_HEADER,
    REWRITE_PROMPT_HEADER,
    verify_report,
)
from src.features.composer.port import (
    AskFatalError,
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)

_원문 = "회사는 캐스팅·트레이닝을 내부에서 수행하고 음반 유통은 협력사와 함께한다."


def _조각() -> tuple[CollectedFragment, ...]:
    return (CollectedFragment(fragment_id="7", kind="사업내용", text=_원문),)


def _보고서(rows: tuple[FlowRow, ...]) -> ComposedReport:
    return ComposedReport(
        sections=(
            ComposedSection(
                section_id="operations_partners",
                sentences=(
                    ComposedSentence(
                        text="음반 유통은 협력사와 함께한다.",
                        citations=("7",),
                        grade="확인",
                    ),
                ),
                flow_rows=rows,
            ),
        )
    )


_경로 = (FlowRow(cells=("연습생", "캐스팅·트레이닝", "데뷔 아티스트"), citations=("7",)),)


# ══════════════════════════════════════════════════════════
# ③ 새 예외는 기존 처리를 바꾸지 않는다
# ══════════════════════════════════════════════════════════


def test_횟수상한_예외는_예산초과_예외의_자식이다() -> None:
    """★ 기존에 ProviderBudgetExceeded 를 잡던 곳이 그대로 잡아야 한다."""
    assert issubclass(
        provider_budget.RequestCallLimitReached,
        provider_budget.ProviderBudgetExceeded,
    )


def test_치명예외의_기본값은_돈문제다() -> None:
    """★ 깃발을 안 주면 «멈춘다»가 기본 — 실수로 열리지 않게."""
    assert AskFatalError(RuntimeError("x")).call_limit is False
    assert AskFatalError(RuntimeError("x"), call_limit=True).call_limit is True


# ══════════════════════════════════════════════════════════
# ① 횟수 상한이면 도식 검수만 포기하고 보고서는 나온다
# ══════════════════════════════════════════════════════════


def test_도식검수가_횟수상한이면_보고서는_살아남는다() -> None:
    """★ 이게 수정의 «이유»다."""

    def ask(prompt: str) -> str:
        raise AskFatalError(RuntimeError("한도"), call_limit=True)

    보고서, 사유 = check_diagrams(_보고서(_경로), _조각(), ask=ask)

    장 = 보고서.sections[0]
    assert len(장.sentences) == 1, "★ 문장이 사라지면 안 된다"
    assert 장.flow_rows == (), "미확인 화살표는 빠진다(보수적)"
    assert any("검수" in 이유 for 이유 in 사유)


def test_도식검수가_돈문제면_여전히_멈춘다() -> None:
    """★ 안전선 — 예산 소진을 「도식만 빼고 계속」으로 숨기지 않는다."""

    def ask(prompt: str) -> str:
        raise AskFatalError(provider_budget.ProviderBudgetExceeded("돈"))

    with pytest.raises(AskFatalError):
        check_diagrams(_보고서(_경로), _조각(), ask=ask)


def test_도식검수가_정상이면_예전처럼_남는다() -> None:
    """회귀 방지 — 상한 갈래가 정상 경로를 바꾸지 않았다."""

    def ask(prompt: str) -> str:
        return json.dumps(
            {"판정": [{"번호": 1, "결과": VERDICT_TRUE}]}, ensure_ascii=False
        )

    보고서, 사유 = check_diagrams(_보고서(_경로), _조각(), ask=ask)

    assert len(보고서.sections[0].flow_rows) == 1
    assert tuple(사유) == ()


# ══════════════════════════════════════════════════════════
# ① 횟수 상한이면 «재작성»만 포기하고 나머지 문장은 살아남는다
# ══════════════════════════════════════════════════════════

#: ⚠️ 숫자를 넣지 마라 — 숫자가 있으면 «의미 검수 전»에 기계 수치 검사가
#:   먼저 해석으로 강등해 버려서 재작성 경로를 아예 타지 않는다.
_거짓문장 = "회사는 달에 공장을 세워 운영한다."
_참문장 = "회사는 캐스팅·트레이닝을 내부에서 수행한다."


def _검증할_보고서() -> ComposedReport:
    return ComposedReport(
        sections=(
            ComposedSection(
                section_id="identity",
                sentences=(
                    ComposedSentence(text=_참문장, citations=("7",), grade="확인"),
                    ComposedSentence(text=_거짓문장, citations=("7",), grade="확인"),
                ),
            ),
        )
    )


def _판정하는_ask(재작성시: Exception | None):
    """검수는 «참/거짓»을 돌려주고, 재작성 요청에서만 예외를 던진다."""

    def ask(prompt: str) -> str:
        if prompt.startswith(REWRITE_PROMPT_HEADER):
            if 재작성시 is not None:
                raise 재작성시
            return json.dumps({"문장들": []}, ensure_ascii=False)
        if prompt.startswith(REVIEW_PROMPT_HEADER):
            판정 = []
            for 줄 in prompt.splitlines():
                if 줄.startswith("[") and "] (등급" in 줄:
                    번호 = int(줄[1 : 줄.index("]")])
                    판정.append({"번호": 번호, "결과": "참"})
            # 두 번째 문장만 «거짓»으로 만들어 재작성을 부른다.
            for 항목 in 판정:
                if 항목["번호"] == 2:
                    항목["결과"] = "거짓"
            return json.dumps({"판정": 판정}, ensure_ascii=False)
        return "{}"

    return ask


def test_재작성이_횟수상한이면_참문장은_살아남는다() -> None:
    """★ 다듬기 한 번을 못 불렀다고 «참» 문장까지 버리지 않는다."""
    ask = _판정하는_ask(AskFatalError(RuntimeError("한도"), call_limit=True))

    결과 = verify_report(_검증할_보고서(), _조각(), None, ask)

    남은 = [문장.text for 절 in 결과.sections for 문장 in 절.sentences]
    assert _참문장 in 남은, "★ 참 판정 문장이 사라지면 안 된다"
    assert _거짓문장 not in 남은, "거짓 문장은 재작성 대신 제거된다(보수적)"


def test_재작성이_돈문제면_여전히_멈춘다() -> None:
    """★ 안전선 — 예산 소진을 「재작성만 포기」로 숨기지 않는다."""
    ask = _판정하는_ask(
        AskFatalError(provider_budget.ProviderBudgetExceeded("돈"))
    )

    with pytest.raises(AskFatalError):
        verify_report(_검증할_보고서(), _조각(), None, ask)


# ══════════════════════════════════════════════════════════
# ④ 재작성 호출은 «예산 안에서만» 쓴다 (넘치면 제거)
# ══════════════════════════════════════════════════════════


def _여러_거짓_보고서(개수: int) -> ComposedReport:
    return ComposedReport(
        sections=(
            ComposedSection(
                section_id="identity",
                sentences=tuple(
                    ComposedSentence(
                        text=f"회사는 달에 제{번호}공장을 세워 운영한다.".replace(
                            str(번호), "일이삼사오육칠"[번호 - 1]
                        ),
                        citations=("7",),
                        grade="확인",
                    )
                    for 번호 in range(1, 개수 + 1)
                ),
            ),
        )
    )


def _전부_거짓_ask(재작성_기록: list[str]):
    def ask(prompt: str) -> str:
        if prompt.startswith(REWRITE_PROMPT_HEADER):
            재작성_기록.append(prompt)
            return json.dumps({"문장들": []}, ensure_ascii=False)
        if prompt.startswith(REVIEW_PROMPT_HEADER):
            판정 = [
                {"번호": int(줄[1 : 줄.index("]")]), "결과": "거짓"}
                for 줄 in prompt.splitlines()
                if 줄.startswith("[") and "] (등급" in 줄
            ]
            return json.dumps({"판정": 판정}, ensure_ascii=False)
        return "{}"

    return ask


def test_재작성_호출은_예산_상한을_넘지_않는다() -> None:
    """★ 재작성은 «거짓 문장 1개당 1회»라 문장이 늘면 호출이 선형으로 는다.

    실측(현대카드): 이 상한이 없어 한 요청 상한(18회)을 3회
    초과했고, 그 초과 하나로 완성된 9개 장이 통째로 버려졌다.
    """
    기록: list[str] = []
    초과개수 = MAX_REWRITE_CALLS_PER_VERIFY + 3

    verify_report(_여러_거짓_보고서(초과개수), _조각(), None, _전부_거짓_ask(기록))

    assert len(기록) == MAX_REWRITE_CALLS_PER_VERIFY, (
        f"★ 재작성 호출이 예산({MAX_REWRITE_CALLS_PER_VERIFY}회)을 넘었다: {len(기록)}회"
    )


def test_예산을_넘긴_거짓문장은_남지_않고_제거된다() -> None:
    """★ 안전선 — 못 살린 «거짓» 문장을 검증 없이 남기지 않는다."""
    기록: list[str] = []
    초과개수 = MAX_REWRITE_CALLS_PER_VERIFY + 3

    결과 = verify_report(
        _여러_거짓_보고서(초과개수), _조각(), None, _전부_거짓_ask(기록)
    )

    남은 = [문장.text for 절 in 결과.sections for 문장 in 절.sentences]
    assert 남은 == [], "★ 거짓 판정 문장이 보고서에 남았다"
