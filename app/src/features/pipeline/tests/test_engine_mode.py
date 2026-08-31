"""프로세스 단일 엔진 모드 영수증을 지킨다."""

from __future__ import annotations

import pytest

from src.features.pipeline import engine_mode


def test_최초환경을_동결한뒤_raw환경이_바뀌어도_재포착하지않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(engine_mode.ENGINE_V2_ENV_NAME, engine_mode.ENGINE_V2_ENV_ON)
    frozen = engine_mode.freeze_process_engine_mode()

    monkeypatch.setenv(engine_mode.ENGINE_V2_ENV_NAME, "0")

    assert frozen is engine_mode.EngineMode.V2
    assert engine_mode.process_engine_mode() is frozen
    assert engine_mode.freeze_process_engine_mode() is frozen


@pytest.mark.parametrize("fake", [True, "v2", object()])
def test_bool_문자열_duck객체는_exact모드영수증이_아니다(fake: object) -> None:
    with pytest.raises(TypeError, match="정확한 EngineMode"):
        engine_mode.freeze_process_engine_mode(fake)  # type: ignore[arg-type]


def test_동결된_process모드를_다른_typed값으로_바꿀수없다() -> None:
    engine_mode.freeze_process_engine_mode(engine_mode.EngineMode.V1)

    with pytest.raises(engine_mode.EngineModeChangedError):
        engine_mode.freeze_process_engine_mode(engine_mode.EngineMode.V2)
