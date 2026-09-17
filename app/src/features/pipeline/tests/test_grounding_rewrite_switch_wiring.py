"""실행기가 근거 결속 재작성 스위치 값을 composer 에 «실제 인자»로 넘기는지 단정한다.

시험 안에서 스위치 함수를 따로 불러 검사하면 운영 배선이 빠져도 초록이 된다.
그래서 `real._run_v2_composer` 를 실제로 돌리고 `composer_pipeline.run_v2` 가
받은 키워드 인자를 본다(`test_v2_ask_reserved_calls_wiring._돌린다` 재사용).
"""

from __future__ import annotations

import pytest

from src.core.grounding_rewrite_switch import (
    GROUNDING_REWRITE_ENV_NAME, GROUNDING_REWRITE_ENV_OFF,
)
from src.features.pipeline.tests.test_v2_ask_reserved_calls_wiring import (  # noqa: F401 - 픽스처 재사용
    _돌린다, _유료문맥, _검증된_배포에서_시험한다, engine,
)


def test_스위치가_켜져_있으면_run_v2에_grounding_rewrite_enabled_참을_넘긴다(
    engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(GROUNDING_REWRITE_ENV_NAME, raising=False)
    _호출자, 받은인자 = _돌린다(engine, monkeypatch)
    assert 받은인자.get("grounding_rewrite_enabled") is True


def test_스위치를_0으로_끄면_run_v2에_grounding_rewrite_enabled_거짓을_넘긴다(
    engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(GROUNDING_REWRITE_ENV_NAME, GROUNDING_REWRITE_ENV_OFF)
    _호출자, 받은인자 = _돌린다(engine, monkeypatch)
    assert 받은인자.get("grounding_rewrite_enabled") is False
