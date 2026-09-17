"""근거 결속 재작성 끄기 스위치의 계약."""

from __future__ import annotations

import pytest

from src.core import grounding_rewrite_switch as switch


def test_환경변수가_없으면_기본값은_켜짐이다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(switch.GROUNDING_REWRITE_ENV_NAME, raising=False)

    assert switch.grounding_rewrite_enabled() is True


def test_정확히_0일_때만_꺼진다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(switch.GROUNDING_REWRITE_ENV_NAME, "0")

    assert switch.grounding_rewrite_enabled() is False


def test_1은_켜짐이다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(switch.GROUNDING_REWRITE_ENV_NAME, "1")

    assert switch.grounding_rewrite_enabled() is True


def test_닫힌_값_0이_아닌_관용_표기는_끄지_못한다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(switch.GROUNDING_REWRITE_ENV_NAME, "off")

    assert switch.grounding_rewrite_enabled() is True
