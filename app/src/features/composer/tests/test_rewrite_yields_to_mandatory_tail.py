"""「거짓」 판정 문장 재작성은 필수 후속 단계 몫을 만나면 스스로 물러난다.

재작성·재검수는 «선택적 다듬기»다 — 못 하면 그 문장이 제거될 뿐이다.
반면 도식 의미 검수·핵심 요약 작성·요약 검수는 못 하면 보고서에서 통째로
빠진다(2026-09-10 실측: 도식 16줄 「의미 검수 불능으로 공개 제외」).
그래서 부르는 쪽이 «필수 후속 몫을 남긴» 전용 호출자를 넣어 준다.

여기서는 그 전용 호출자가 실제로 쓰이는지, 그리고 그것이 한도에 걸렸을 때
보고서가 죽지 않고 해당 문장만 빠지는지를 본다.
"""

from __future__ import annotations

from typing import Optional

from src.features.budget.provider_budget import RequestCallLimitReached
from src.features.composer.port import (
    AskFatalError,
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.verify import verify_report

_원문 = (
    "가나다전자는 2025년에 반도체 검사 장비를 국내 대기업 세 곳에 공급했고 "
    "같은 해 매출은 1,200억 원이었다."
)


def _조각() -> CollectedFragment:
    return CollectedFragment(fragment_id="1", kind="공식", text=_원문)


def _보고서(문장: str) -> ComposedReport:
    return ComposedReport(
        sections=(
            ComposedSection(
                section_id="business_model",
                sentences=(
                    ComposedSentence(text=문장, citations=("1",), grade="확인"),
                ),
            ),
        ),
        summary=(),
    )


class _검수:
    """모든 문장을 «거짓»으로 판정해 재작성 경로를 반드시 타게 한다."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(str(prompt))
        return '{"판정": [{"번호": 1, "결과": "거짓"}]}'


class _한도:
    """호출 «횟수» 상한에 닿은 호출자 — 부르면 강등 가능한 전역 장애를 던진다."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _prompt: str) -> str:
        self.calls += 1
        raise AskFatalError(
            RequestCallLimitReached("한 요청의 AI 호출 횟수 상한을 넘었습니다"),
            call_limit=True,
        )


class _재작성:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(str(prompt))
        return "가나다전자는 2025년에 반도체 검사 장비를 공급했다."


def _문장수(report: Optional[ComposedReport]) -> int:
    assert report is not None
    return sum(len(section.sentences) for section in report.sections)


def test_재작성이_한도에_걸리면_그_문장은_제거되고_보고서는_이어진다() -> None:
    검수 = _검수()
    재작성 = _한도()
    재검수 = _한도()
    verified = verify_report(
        _보고서(_원문), [_조각()], None, 검수,
        diagnostics=[],
        rewrite_ask=재작성,
        recheck_ask=재검수,
    )
    # 예외가 밖으로 나가지 않았고(= 보고서가 죽지 않았고), 장은 그대로 남는다.
    assert len(verified.sections) == 1
    # 살릴 기회를 못 얻은 «거짓» 문장은 라벨만 바꿔 공개하지 않고 제거한다.
    assert _문장수(verified) == 0
    # 재작성 전용 호출자가 실제로 쓰였다 — 한 번 막히면 나머지도 시도하지 않는다.
    assert 재작성.calls == 1
    assert 재검수.calls == 0


def test_재작성_전용_호출자가_없으면_예전처럼_공용_검수자를_쓴다() -> None:
    """기본값 None은 기존 계약 그대로 — 다른 호출자·시험이 바뀌지 않는다."""
    검수 = _검수()
    verified = verify_report(_보고서(_원문), [_조각()], None, 검수, diagnostics=[])
    assert len(verified.sections) == 1
    # 검수 1회 + 재작성 1회 + 재검수 1회가 «같은» 호출자로 나간다.
    assert len(검수.prompts) >= 2, (
        f"공용 검수자로 재작성이 흐르지 않았습니다: {len(검수.prompts)}회"
    )


def test_재작성과_재검수는_서로_다른_호출자로_나간다() -> None:
    """예약값이 다른 두 클로저라 «어느 것이 쓰였나»가 예산 결과를 바꾼다."""
    검수 = _검수()
    재작성 = _재작성()
    재검수 = _검수()
    verify_report(
        _보고서(_원문), [_조각()], None, 검수,
        diagnostics=[],
        rewrite_ask=재작성,
        recheck_ask=재검수,
    )
    assert len(재작성.prompts) == 1, "재작성이 전용 호출자로 나가지 않았습니다"
    assert len(재검수.prompts) == 1, "재검수가 전용 호출자로 나가지 않았습니다"
    # 공용 검수자는 «최초 본문 검수» 한 번만 쓴다 — 다듬기는 전용 호출자 몫이다.
    assert len(검수.prompts) == 1
