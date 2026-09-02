"""typed DART 수집기 kill switch(``TYPED_DART_COLLECTOR``)의 동결 계약.

★ 왜 필요한가 — typed 수집기는 자기 docstring으로 「실제 네트워크로 시험하지
  않았다(LIVE_COLLECTION_UNVERIFIED)」고 선언한 코드다. 운영에 얹을 때는 한
  프로세스 안에서 값이 도중에 바뀌지 않아야 같은 조사가 반쯤은 typed, 반쯤은
  legacy로 갈리는 일을 막을 수 있다. 그래서 엔진 모드(``ENGINE_V2``)와 똑같이
  **프로세스당 1회 동결**한다 — 새 메커니즘을 발명하지 않고
  ``pipeline/engine_mode.py``의 동결 패턴을 그대로 본뜬다.

★ 시험 위치 — 스위치 자체는 ``src/core``에 있지만 이 계약을 실제로 소비하는
  것은 파이프라인 수집 경로다. 소유 경계 안인 이 폴더에 둔다.
"""

from __future__ import annotations

import pytest

from src.core import typed_collector_switch as switch


@pytest.fixture(autouse=True)
def _fresh_process_typed_collector_switch():
    """시험끼리 프로세스 동결 상태가 새지 않게 격리한다."""

    switch._reset_process_typed_collector_switch_for_tests()  # noqa: SLF001
    yield
    switch._reset_process_typed_collector_switch_for_tests()  # noqa: SLF001


def test_환경변수가_없으면_기본값은_off다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, raising=False)

    assert switch.freeze_process_typed_collector_switch() is (
        switch.TypedCollectorSwitch.OFF
    )
    assert switch.typed_dart_collector_enabled() is False


def test_1이_아닌_값은_전부_off다(monkeypatch: pytest.MonkeyPatch) -> None:
    """「켜짐」은 정확히 ``"1"``뿐이다 — true·yes·on 같은 관용 표기를 받으면
    오타 하나로 미검증 수집기가 운영에서 켜진다."""

    for raw in ("0", "", "true", "TRUE", "yes", "on", " 1"):
        switch._reset_process_typed_collector_switch_for_tests()  # noqa: SLF001
        monkeypatch.setenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, raw)

        assert switch.typed_dart_collector_enabled() is False, raw


def test_정확히_1일_때만_on이다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, "1")

    assert switch.typed_dart_collector_enabled() is True
    assert switch.process_typed_collector_switch() is switch.TypedCollectorSwitch.ON


def test_한번_동결하면_환경변수를_바꿔도_같은_값이다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """조사 도중 환경이 바뀌어도 같은 프로세스는 끝까지 같은 경로를 탄다."""

    monkeypatch.setenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, "1")
    assert switch.typed_dart_collector_enabled() is True

    monkeypatch.delenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, raising=False)

    assert switch.typed_dart_collector_enabled() is True
    assert switch.process_typed_collector_switch() is switch.TypedCollectorSwitch.ON


def test_동결된_값과_다른_값을_명시하면_거부한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """엔진 모드와 «같은 종류의 오류»를 낸다 — 조용히 갈아끼우지 않는다."""

    monkeypatch.delenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, raising=False)
    switch.freeze_process_typed_collector_switch()

    with pytest.raises(switch.TypedCollectorSwitchChangedError):
        switch.freeze_process_typed_collector_switch(switch.TypedCollectorSwitch.ON)


def test_문자열이나_bool은_스위치로_받지_않는다() -> None:
    """``"on"``·``True``처럼 «닮은 값»이 동결되면 이후 비교가 전부 어긋난다."""

    for impostor in ("on", True, 1, None.__class__):
        with pytest.raises(TypeError):
            switch.require_exact_typed_collector_switch(impostor)  # type: ignore[arg-type]


def test_읽지_않으면_동결되지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """v1 경로가 이 값을 «보지 않았다»를 기계적으로 확인할 수 있어야 한다.

    이 시험이 지키는 것은 「스위치를 한 번도 조회하지 않으면 프로세스 동결
    상태가 그대로 비어 있다」는 성질이다. 운영 경로 시험
    (``test_typed_dart_wiring.py``)이 이 성질을 v1 무변경의 증거로 쓴다.
    """

    monkeypatch.setenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, "1")

    assert switch.frozen_typed_collector_switch() is None

    switch.typed_dart_collector_enabled()

    assert switch.frozen_typed_collector_switch() is switch.TypedCollectorSwitch.ON
