"""선택적 다듬기가 도식 검수·요약 작성·요약 검수 몫을 먹지 못한다.

2026-09-10 멀티캠퍼스 실측 결함:
한 요청 AI 호출 상한 18회를 뉴스 5 + 장 작성 9 + 본문 검수 1이 채운 뒤,
「거짓」 판정 문장 «재작성» 3회가 남은 3회를 그대로 써버렸다. 그 결과
도식 경로 16줄이 「의미 검수 불능으로 공개 제외」됐고 핵심 요약은 새로
쓰지 못한 채 본문 문장으로 채워졌는데, 보고서는 «성공»으로 출고됐다.
직전 실행(재작성 0회)은 같은 회사·같은 자료량에서 멀쩡했다 — 즉 자료가
아니라 «검수 판정 수»에 따라 품질이 갈렸다.

이 시험은 그 굶김을 재현하고(음성 대조) 예약이 걸리면 순서가 뒤집히는 것을
못 박는다. 기준값은 생산 상수를 import해 비교하지 않고 리터럴로 적는다 —
상수를 낮추는 회귀를 그대로 통과시키지 않기 위해서다.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.budget import provider_budget
from src.features.composer.port import AskFatalError
from src.features.pipeline import real
from src.shared import report_recovery

# ★ 리터럴 오라클. 상수를 import해 같은 값끼리 비교하면 «값이 내려가는»
#   회귀를 못 잡는다. 아래 숫자는 실행 순서의 근거이기도 하다.
#   도식 검수 1 + 요약 고르기 1 = 2, 재검수 1.
#
# ★ 3에서 2로 내린 근거 (2026-09-11) — 요약이 「AI가 새로 쓴다」에서 「검증된
#   본문 문장 중 고른다」로 바뀌면서 «요약 검수» 단계가 사라졌다. 다시 검수할
#   새 글자가 없기 때문이다. 예전에는 그 1회를 예약해 두고도 최종 요약에
#   0문장을 기여한 실행이 있었다(4차 멀티캠퍼스: 초안 4 → 검수 후 1 → 수치
#   안전 검사 후 0). 예약이 남아 있으면 그만큼 앞 단계(뉴스·다듬기)가 굶는다.
_필수후속 = 2
_재검수몫 = 1
_재작성예약 = _필수후속 + _재검수몫  # 3
_상한 = 18
_뉴스몫 = 5
_장작성 = 9


class _응답기록:
    """전송된 요청만 세는 가짜 provider messages."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def count_tokens(self, **_kwargs: Any) -> Any:
        return SimpleNamespace(input_tokens=100)

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return SimpleNamespace(
            model="claude-haiku-4-5-20251001",
            stop_reason="end_turn",
            content=[SimpleNamespace(text='{"판정": []}')],
            usage=SimpleNamespace(
                input_tokens=100, output_tokens=10,
                cache_creation_input_tokens=0, cache_read_input_tokens=0,
            ),
        )


def _재생(*, 재작성예약: int, 재검수예약: int, 뉴스: int) -> tuple[list[str], list[str], int]:
    """실행 순서를 그대로 재생하고 «무엇이 나갔고 무엇이 거부됐는지»를 돌려준다.

    composer 의 실제 단계 순서다 — 뉴스 → 장 작성 9 → 본문 검수 →
    (선택) 재작성 ≤3 → (선택) 재검수 → 도식 검수 → 요약 고르기.
    맨 뒤에 있던 «요약 검수»는 2026-09-11에 사라졌다(모듈 docstring 참고).
    """

    messages = _응답기록()
    engine = real._MeteredEngine(SimpleNamespace(MODEL="claude-haiku-4-5"))
    client = real._metered_client(engine, SimpleNamespace(messages=messages))
    관측: list[Any] = []
    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _stage, _reserved: len(관측) + 1,
        lambda _: None,
        lambda _: None,
        lambda _attempt, observation: 관측.append(observation),
    )

    만든다 = real._v2_ask_via_provider
    호출자 = {
        "뉴스": 만든다(engine, client, stage="news_grounding", max_tokens=1000),
        "작성": 만든다(engine, client, stage="v2_compose",
                     max_tokens=real.V2_WRITER_MAX_TOKENS),
        "검수": 만든다(engine, client, stage="v2_review",
                     max_tokens=real.V2_REVIEWER_MAX_TOKENS),
        "재작성": 만든다(engine, client, stage="v2_review",
                      max_tokens=real.V2_REVIEWER_MAX_TOKENS,
                      reserved_calls=재작성예약),
        "재검수": 만든다(engine, client, stage="v2_review",
                      max_tokens=real.V2_REVIEWER_MAX_TOKENS,
                      reserved_calls=재검수예약),
        "도식": 만든다(engine, client, stage="v2_diagram",
                     max_tokens=real.V2_DIAGRAM_MAX_TOKENS),
    }
    나갔다: list[str] = []
    거부됐다: list[str] = []

    def 부른다(이름: str, 열쇠: str) -> bool:
        try:
            with provider_budget.activate(100_000), attempt_context.activate(callbacks):
                호출자[열쇠](f"{이름} 프롬프트")
        except AskFatalError as error:
            # 돈·계정 장애가 아니라 «이 요청 몫을 다 썼다»여야 한다.
            assert error.degradable, f"{이름}이(가) 강등 불가 장애로 죽었습니다"
            assert error.call_limit, f"{이름}을(를) 막은 것이 호출 «횟수» 상한이 아닙니다"
            거부됐다.append(이름)
            return False
        나갔다.append(이름)
        return True

    for 번호 in range(뉴스):
        부른다(f"뉴스#{번호 + 1}", "뉴스")
    for 번호 in range(_장작성):
        부른다(f"장작성#{번호 + 1}", "작성")
    부른다("본문검수", "검수")
    재작성수 = sum(부른다(f"재작성#{번호 + 1}", "재작성") for 번호 in range(3))
    if 재작성수:
        부른다("재검수", "재검수")
    부른다("도식검수", "도식")
    부른다("요약고르기", "작성")
    return 나갔다, 거부됐다, len(messages.requests)


def test_필수후속몫은_도식_요약고르기_2회다() -> None:
    """예약값이 «보충 계산식»이 아니라 «필수 후속»에서 나왔음을 못 박는다."""
    assert report_recovery.DIAGRAM_REVIEW_CALLS == 1
    assert report_recovery.SUMMARY_WRITER_CALLS == 1
    # ★ 요약 검수 0회 — 요약이 검증된 본문 문장을 글자 그대로 실으면서 다시
    #   검수할 새 글자가 없어졌다. 1로 되돌리면 그 호출은 최종 요약에 아무것도
    #   기여하지 않으면서 앞 단계 몫만 줄인다(2026-09-11 실측 근거는 모듈 상단).
    assert report_recovery.SUMMARY_REVIEW_CALLS == 0
    assert report_recovery.MANDATORY_TAIL_AI_CALLS == _필수후속
    assert report_recovery.REWRITE_RECHECK_CALLS == _재검수몫
    # 뉴스가 남겨야 하는 몫 = 장 작성 9 + 본문 검수 1 + 필수 후속 2.
    assert report_recovery.MANDATORY_REPORT_AI_CALLS == 12
    # ★ 보충 몫(MAX_TOTAL_AI_CALLS)과 «값이 같아도 뜻이 다르다». 예전에는
    #   보충 상수를 별칭으로 재사용해 우연히 맞아 보였다. 유도식이 서로
    #   독립인지만 본다 — 값이 같은 것을 근거로 삼지 않는다.
    assert report_recovery.MANDATORY_REPORT_AI_CALLS == (
        report_recovery.PRIMARY_WRITER_CALLS
        + report_recovery.PRIMARY_REVIEW_CALLS
        + report_recovery.MANDATORY_TAIL_AI_CALLS
    )


def test_예약이_걸린_호출은_경계에서_정확히_한_번_더_거부된다() -> None:
    """N-1은 열리고 N은 닫힌다 — 예약을 0으로 낮추면 이 시험이 빨개진다."""
    engine = real._MeteredEngine(SimpleNamespace())
    for _ in range(_상한 - _필수후속 - 1):  # 15회
        engine.reserve_provider_call()
    # 16번째까지는 «필수 후속 2를 남기고도» 부를 수 있다.
    assert engine.reserve_provider_call(reserved_calls=_필수후속) == 16
    # 17번째부터는 남은 2가 필수 후속 몫이라 선택적 호출은 막힌다.
    with pytest.raises(provider_budget.RequestCallLimitReached):
        engine.reserve_provider_call(reserved_calls=_필수후속)
    # 예약 없는(=필수) 호출은 그대로 나간다.
    assert engine.reserve_provider_call() == 17


def test_예약수는_0이상의_정수여야_한다() -> None:
    engine = real._MeteredEngine(SimpleNamespace())
    with pytest.raises(ValueError):
        engine.reserve_provider_call(reserved_calls=-1)
    with pytest.raises(ValueError):
        engine.reserve_provider_call(reserved_calls=True)


def test_예약값을_감싼_엔진에서_읽지_않는다() -> None:
    """계량 껍데기의 __getattr__ 은 모르는 이름을 1판 엔진으로 넘긴다.

    예약값을 자기 칸에 만들어 두지 않으면 «남의 객체»에 같은 이름이 있을 때
    그 값을 읽어, 필수 단계가 스스로를 굶기거나 선택적 호출이 그냥 통과한다.
    """
    감싼엔진 = SimpleNamespace(MODEL="가짜모델", _reserved_calls=99)
    engine = real._MeteredEngine(감싼엔진)
    assert engine.reserved_calls == 0
    with engine.stage_context("v2_review", reserved_calls=_재작성예약):
        assert engine.reserved_calls == _재작성예약
        # 중첩해도 안쪽 값이 이기고, 빠져나오면 바깥 값으로 되돌아온다.
        with engine.stage_context("v2_diagram"):
            assert engine.reserved_calls == 0
        assert engine.reserved_calls == _재작성예약
    assert engine.reserved_calls == 0


def test_뉴스5_작성9_검수1_뒤_재작성은_거부되고_도식요약이_실행된다() -> None:
    나갔다, 거부됐다, 전송 = _재생(
        재작성예약=_재작성예약, 재검수예약=_필수후속, 뉴스=_뉴스몫,
    )
    assert 거부됐다 == ["재작성#1", "재작성#2", "재작성#3"], (
        "재검수를 못 할 재작성이 시작됐습니다 — 그 호출은 판정 없이 버려집니다"
    )
    assert 나갔다[-2:] == ["도식검수", "요약고르기"]
    # 요약 검수가 없어져 필수 후속이 2회다 — 상한 18 중 17회만 나간다.
    assert 전송 == _상한 - 1


def test_예약없는_옛_배선이면_도식요약이_굶는다() -> None:
    """음성 대조 — 예약을 빼면 2026-09-10 실측과 «같은» 결말이 나와야 한다."""
    나갔다, 거부됐다, 전송 = _재생(재작성예약=0, 재검수예약=0, 뉴스=_뉴스몫)
    assert 나갔다[-3:] == ["재작성#1", "재작성#2", "재작성#3"]
    assert 거부됐다 == ["재검수", "도식검수", "요약고르기"], (
        "옛 배선에서 도식·요약이 굶지 않는다면 이 시험이 재현하는 결함이 아니다"
    )
    assert 전송 == _상한


def test_뉴스가_적으면_다듬기도_돌고_필수후속도_그대로_남는다() -> None:
    """뉴스 2회(인텍에프에이 실측 모양) — 여유가 있으면 다듬기를 죽이지 않는다."""
    나갔다, 거부됐다, 전송 = _재생(
        재작성예약=_재작성예약, 재검수예약=_필수후속, 뉴스=2,
    )
    assert "재작성#1" in 나갔다 and "재검수" in 나갔다
    assert 나갔다[-2:] == ["도식검수", "요약고르기"]
    # ★ 필수 후속이 3→2로 줄면서 남은 한 자리가 «세 번째 다듬기»로 간다.
    #   예전에는 재작성#3이 예약에 막혀 거부됐다.
    assert 거부됐다 == []
    assert 전송 <= _상한
