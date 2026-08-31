from __future__ import annotations

import pytest

from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_evidence.release_mode import (
    DEFAULT_REPORT_RELEASE_MODE,
    REPORT_RELEASE_MODE_ENV_NAME,
    parse_release_mode,
)


def test_설정이_없으면_새차단을_켜지_않고_SHADOW다() -> None:
    assert REPORT_RELEASE_MODE_ENV_NAME == "REPORT_RELEASE_MODE"
    assert DEFAULT_REPORT_RELEASE_MODE is ReleaseMode.SHADOW
    assert parse_release_mode() is ReleaseMode.SHADOW
    assert parse_release_mode("") is ReleaseMode.SHADOW


@pytest.mark.parametrize("mode", tuple(ReleaseMode))
def test_명시한_정확한_운영모드만_받는다(mode: ReleaseMode) -> None:
    assert parse_release_mode(mode.value) is mode


@pytest.mark.parametrize(
    "value",
    (
        "full",
        " FULL",
        "FULL ",
        "ENFORCE",
        "UNKNOWN",
        1,
        True,
    ),
)
def test_오타나_다른자료형을_조용히_추측하지_않는다(value: object) -> None:
    with pytest.raises(ValueError):
        parse_release_mode(value)
