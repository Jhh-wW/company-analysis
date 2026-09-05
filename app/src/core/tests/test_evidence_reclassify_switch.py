"""근거 재판정 kill switch의 프로세스 동결 계약."""

from __future__ import annotations

import pytest

from src.core import evidence_reclassify_switch as switch


@pytest.fixture(autouse=True)
def _fresh_process_evidence_reclassify_switch():
    """시험 사이에 프로세스 동결 상태가 새지 않게 격리한다."""

    switch._reset_process_evidence_reclassify_switch_for_tests()  # noqa: SLF001
    yield
    switch._reset_process_evidence_reclassify_switch_for_tests()  # noqa: SLF001


def test_환경변수가_없으면_기본값은_off다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(switch.EVIDENCE_RECLASSIFY_ENV_NAME, raising=False)

    assert switch.freeze_process_evidence_reclassify_switch() is (
        switch.EvidenceReclassifySwitch.OFF
    )
    assert switch.evidence_reclassify_enabled() is False


def test_1이_아닌_값은_전부_off다(monkeypatch: pytest.MonkeyPatch) -> None:
    for raw in ("0", "", "true", "TRUE", "yes", "on", " 1"):
        switch._reset_process_evidence_reclassify_switch_for_tests()  # noqa: SLF001
        monkeypatch.setenv(switch.EVIDENCE_RECLASSIFY_ENV_NAME, raw)

        assert switch.evidence_reclassify_enabled() is False, raw


def test_정확히_1일_때만_on이다(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(switch.EVIDENCE_RECLASSIFY_ENV_NAME, "1")

    assert switch.evidence_reclassify_enabled() is True
    assert switch.process_evidence_reclassify_switch() is (
        switch.EvidenceReclassifySwitch.ON
    )


def test_한번_동결하면_환경변수를_바꿔도_같은_값이다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(switch.EVIDENCE_RECLASSIFY_ENV_NAME, "1")
    assert switch.evidence_reclassify_enabled() is True

    monkeypatch.delenv(switch.EVIDENCE_RECLASSIFY_ENV_NAME, raising=False)

    assert switch.evidence_reclassify_enabled() is True
    assert switch.process_evidence_reclassify_switch() is (
        switch.EvidenceReclassifySwitch.ON
    )


def test_동결된_값과_다른_값을_명시하면_거부한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(switch.EVIDENCE_RECLASSIFY_ENV_NAME, raising=False)
    switch.freeze_process_evidence_reclassify_switch()

    with pytest.raises(switch.EvidenceReclassifySwitchChangedError):
        switch.freeze_process_evidence_reclassify_switch(
            switch.EvidenceReclassifySwitch.ON
        )


def test_문자열이나_bool은_스위치로_받지_않는다() -> None:
    for impostor in ("on", True, 1, None.__class__):
        with pytest.raises(TypeError):
            switch.require_exact_evidence_reclassify_switch(  # type: ignore[arg-type]
                impostor
            )


def test_읽지_않으면_동결되지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(switch.EVIDENCE_RECLASSIFY_ENV_NAME, "1")

    assert switch.frozen_evidence_reclassify_switch() is None

    switch.evidence_reclassify_enabled()

    assert switch.frozen_evidence_reclassify_switch() is (
        switch.EvidenceReclassifySwitch.ON
    )
