"""매출표 v2 kill switch(``REVENUE_TABLE_V2``)의 동결 계약.

★ 왜 필요한가 — 1단계는 매출 구성표를 찾는 방식을 「제목 화이트리스트」에서
  「표 모양 탐지」로 바꾼다. 표가 새로 생기는 회사가 늘어나므로, 무언가 어긋나면
  코드를 되감지 않고 «환경변수 하나»로 옛 동작으로 돌아갈 수 있어야 한다.

★ 왜 동결인가 — 값을 요청마다 다시 읽으면 한 프로세스 안에서 같은 조사가
  반쯤은 새 파서, 반쯤은 옛 파서로 갈린다. ``typed_collector_switch``와 같은
  패턴을 그대로 쓴다(새 메커니즘을 발명하지 않는다).

★ «선언하지 않는 것»이 off다 — ``render.yaml``에 키가 없으면 옛 동작이다.
  그 사실은 ``deploy/tests/test_release_contract_literal.py``가 따로 지킨다.
"""

from __future__ import annotations

import pytest

from src.core import revenue_table_switch as switch


@pytest.fixture(autouse=True)
def _fresh_process_revenue_table_switch():
    """시험끼리 프로세스 동결 상태가 새지 않게 격리한다."""

    switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001
    yield
    switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001


def test_환경변수가_없으면_기본값은_off다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(switch.REVENUE_TABLE_V2_ENV_NAME, raising=False)

    assert switch.freeze_process_revenue_table_switch() is (
        switch.RevenueTableSwitch.OFF
    )
    assert switch.revenue_table_v2_enabled() is False


def test_1이_아닌_값은_전부_off다(monkeypatch: pytest.MonkeyPatch) -> None:
    """「켜짐」은 정확히 ``"1"``뿐이다 — true·yes·on 같은 관용 표기를 받으면
    오타 하나로 미검증 파서가 운영에서 켜진다."""

    for raw in ("0", "", "true", "TRUE", "yes", "on", " 1"):
        switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001
        monkeypatch.setenv(switch.REVENUE_TABLE_V2_ENV_NAME, raw)

        assert switch.revenue_table_v2_enabled() is False, raw


def test_정확히_1일_때만_on이다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(switch.REVENUE_TABLE_V2_ENV_NAME, "1")

    assert switch.revenue_table_v2_enabled() is True
    assert switch.process_revenue_table_switch() is switch.RevenueTableSwitch.ON


def test_한번_동결하면_환경변수를_바꿔도_같은_값이다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """조사 도중 환경이 바뀌어도 같은 프로세스는 끝까지 같은 파서를 쓴다."""

    monkeypatch.setenv(switch.REVENUE_TABLE_V2_ENV_NAME, "1")
    assert switch.revenue_table_v2_enabled() is True

    monkeypatch.delenv(switch.REVENUE_TABLE_V2_ENV_NAME, raising=False)

    assert switch.revenue_table_v2_enabled() is True
    assert switch.process_revenue_table_switch() is switch.RevenueTableSwitch.ON


def test_동결된_값과_다른_값을_명시하면_거부한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """조용히 갈아끼우지 않는다 — 같은 종류의 오류를 낸다."""

    monkeypatch.delenv(switch.REVENUE_TABLE_V2_ENV_NAME, raising=False)
    switch.freeze_process_revenue_table_switch()

    with pytest.raises(switch.RevenueTableSwitchChangedError):
        switch.freeze_process_revenue_table_switch(switch.RevenueTableSwitch.ON)


def test_문자열이나_bool은_스위치로_받지_않는다() -> None:
    """``"on"``·``True``처럼 «닮은 값»이 동결되면 이후 비교가 전부 어긋난다."""

    for impostor in ("on", True, 1, None.__class__):
        with pytest.raises(TypeError):
            switch.require_exact_revenue_table_switch(impostor)  # type: ignore[arg-type]


def test_읽지_않으면_동결되지_않는다(monkeypatch: pytest.MonkeyPatch) -> None:
    """「이 경로가 스위치를 한 번도 조회하지 않았다」를 기계로 확인하는 통로다."""

    monkeypatch.setenv(switch.REVENUE_TABLE_V2_ENV_NAME, "1")

    assert switch.frozen_revenue_table_switch() is None

    switch.revenue_table_v2_enabled()

    assert switch.frozen_revenue_table_switch() is switch.RevenueTableSwitch.ON
