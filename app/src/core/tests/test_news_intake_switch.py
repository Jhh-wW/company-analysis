from __future__ import annotations

import pytest

from src.core import news_intake_switch as switch


@pytest.fixture(autouse=True)
def _fresh_process_switch(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(switch.NEWS_INTAKE_ENV_NAME, raising=False)
    switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    yield
    switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", " 1"])
def test_정확한_1이_아니면_언론근거는_꺼진다(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is not None:
        monkeypatch.setenv(switch.NEWS_INTAKE_ENV_NAME, value)

    assert switch.news_intake_enabled() is False
    assert switch.process_news_intake_switch() is switch.NewsIntakeSwitch.OFF


def test_정확한_1만_언론근거를_켠다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(switch.NEWS_INTAKE_ENV_NAME, "1")

    assert switch.news_intake_enabled() is True
    assert switch.frozen_news_intake_switch() is switch.NewsIntakeSwitch.ON


def test_첫조회뒤_환경이_바뀌어도_동결값을_유지한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert switch.news_intake_enabled() is False
    monkeypatch.setenv(switch.NEWS_INTAKE_ENV_NAME, "1")

    assert switch.news_intake_enabled() is False
    with pytest.raises(switch.NewsIntakeSwitchChangedError):
        switch.freeze_process_news_intake_switch(switch.NewsIntakeSwitch.ON)


def test_정확한_enum만_명시동결값으로_받는다() -> None:
    with pytest.raises(TypeError):
        switch.require_exact_news_intake_switch("on")  # type: ignore[arg-type]
