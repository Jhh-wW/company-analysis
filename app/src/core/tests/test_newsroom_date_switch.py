"""뉴스룸 날짜 AI 예비 단계 kill switch의 프로세스 동결 계약."""

from __future__ import annotations

import pytest

from src.core import newsroom_date_switch as switch


@pytest.fixture(autouse=True)
def _fresh_process_newsroom_date_switch():
    switch._reset_process_newsroom_date_switch_for_tests()  # noqa: SLF001
    yield
    switch._reset_process_newsroom_date_switch_for_tests()  # noqa: SLF001


def test_환경변수가_없으면_기본값은_off다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(switch.NEWSROOM_DATE_AI_ENV_NAME, raising=False)

    assert switch.newsroom_date_ai_enabled() is False
    assert switch.process_newsroom_date_switch() is switch.NewsroomDateSwitch.OFF


@pytest.mark.parametrize("raw", ["0", "", "true", "TRUE", "yes", "on", " 1"])
def test_1이_아닌_값은_전부_off다(
    monkeypatch: pytest.MonkeyPatch,
    raw: str,
) -> None:
    monkeypatch.setenv(switch.NEWSROOM_DATE_AI_ENV_NAME, raw)

    assert switch.newsroom_date_ai_enabled() is False


def test_정확히_1일_때만_on이다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(switch.NEWSROOM_DATE_AI_ENV_NAME, "1")

    assert switch.newsroom_date_ai_enabled() is True
    assert switch.process_newsroom_date_switch() is switch.NewsroomDateSwitch.ON


def test_한번_동결하면_환경변수를_바꿔도_같은_값이다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(switch.NEWSROOM_DATE_AI_ENV_NAME, "1")
    assert switch.newsroom_date_ai_enabled() is True

    monkeypatch.delenv(switch.NEWSROOM_DATE_AI_ENV_NAME, raising=False)

    assert switch.newsroom_date_ai_enabled() is True


def test_동결된_값과_다른_값을_명시하면_거부한다() -> None:
    switch.freeze_process_newsroom_date_switch(switch.NewsroomDateSwitch.OFF)

    with pytest.raises(switch.NewsroomDateSwitchChangedError):
        switch.freeze_process_newsroom_date_switch(switch.NewsroomDateSwitch.ON)


def test_문자열과_bool은_스위치로_받지_않는다() -> None:
    for impostor in ("on", True, 1):
        with pytest.raises(TypeError):
            switch.require_exact_newsroom_date_switch(impostor)  # type: ignore[arg-type]


def test_읽지_않으면_동결되지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(switch.NEWSROOM_DATE_AI_ENV_NAME, "1")

    assert switch.frozen_newsroom_date_switch() is None

    switch.newsroom_date_ai_enabled()

    assert switch.frozen_newsroom_date_switch() is switch.NewsroomDateSwitch.ON
