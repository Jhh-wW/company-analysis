"""파생 비율 기록이 «운영 진입점»을 지나 실행 기록 싱크까지 닿는지 못 박는다.

★ 왜 따로 있나 (2026-09-11 독립 검토 P1) — 첫 판은 `check_diagrams`까지만
  배선하고 운영 호출부에는 아무도 인자를 넘기지 않았다. 그래서
  `derived_ratio_recomputed` 기록이 «시험에서만» 생기고 실행에는 남지 않았다.
  진단을 남기라는 규칙이 코드에는 있는데 운영에는 없는 상태였다.

★ 무엇을 단정하나 — 호출 인자를 «실제로» 잡아서, 운영이 넘긴 목록이
  `run_v2`가 받은 싱크와 «같은 객체»인지 본다. 시험 안에서 따로 목록을 만들어
  검사하면 배선 결함을 못 잡는다.
"""

from __future__ import annotations

import pytest

from src.features.composer import pipeline
from src.shared.report_evidence.constants import ReleaseMode
from src.features.composer.tests.test_pipeline import (
    _FakeReviewer,
    _FakeWriter,
    _raw_fragments,
    _strict_fragments,
    _strict_packet_set,
)
from src.features.composer.validate import V2ValidationError
from src.shared.report_quality.composition_diagnostic_constants import (
    DERIVED_RATIO_RECOMPUTED,
    DERIVED_RATIO_SHARE_KIND,
    DERIVED_RATIO_STEP,
)
from src.shared.report_quality.composition_diagnostics import (
    observed_composition_steps,
)

_기록_하나 = {
    "step": DERIVED_RATIO_STEP,
    "장": "portfolio",
    "사유코드": DERIVED_RATIO_RECOMPUTED,
    "종류": DERIVED_RATIO_SHARE_KIND,
    "백분율": "90",
    "분자": "24514287835",
    "분모": "27351053389",
}


def _잡아서_기록을_남긴다(monkeypatch: pytest.MonkeyPatch, 이름: str) -> list[object]:
    """그 함수가 받은 `derived_ratio_diagnostics` 인자를 그대로 모은다."""

    원본 = getattr(pipeline, 이름)
    받은: list[object] = []

    def 대역(*args, **kwargs):
        싱크 = kwargs.get("derived_ratio_diagnostics")
        받은.append(싱크)
        if isinstance(싱크, list):
            싱크.append(dict(_기록_하나))
        return 원본(*args, **kwargs)

    monkeypatch.setattr(pipeline, 이름, 대역)
    return 받은


@pytest.mark.parametrize(
    ("release_mode", "함수"),
    (
        (ReleaseMode.FULL, "check_diagram_numbers"),
        (ReleaseMode.SHADOW, "check_diagrams"),
    ),
)
def test_run_v2가_파생비율_기록_싱크를_도식_관문에_넘긴다(
    monkeypatch: pytest.MonkeyPatch, release_mode: ReleaseMode, 함수: str,
) -> None:
    받은 = _잡아서_기록을_남긴다(monkeypatch, 함수)
    싱크: list[dict] = []
    엄격 = release_mode is not ReleaseMode.SHADOW
    실행 = lambda: pipeline.run_v2(  # noqa: E731 - 두 갈래가 같은 인자를 쓴다
        "가나다전자",
        _strict_fragments() if 엄격 else _raw_fragments(),
        None,
        writer_ask=_FakeWriter(),
        reviewer_ask=_FakeReviewer(),
        generated_at="2026-09-10",
        as_of_date="2026-09-10",
        release_mode=release_mode,
        composition_diagnostics_sink=싱크,
        **(
            {
                "section_evidence_packets": _strict_packet_set(),
                "company_id": "00123456",
                "build_identity_sha256": "b" * 64,
            }
            if 엄격
            else {}
        ),
    )
    if 엄격:
        # FULL 갈래는 가짜 입력이라 최종 게이트에서 막힐 수 있다. 배선은 그
        # «앞»에 있으므로 막히는 것 자체는 이 시험의 관심이 아니다.
        try:
            실행()
        except V2ValidationError:
            pass
    else:
        실행()

    assert 받은, f"{함수}가 한 번도 불리지 않았습니다"
    assert all(항목 is 싱크 for 항목 in 받은), (
        f"{함수}에 실행 기록 싱크가 아닌 것이 넘어갔습니다: {받은}"
    )
    남은 = [항목 for 항목 in 싱크 if 항목.get("step") == DERIVED_RATIO_STEP]
    assert 남은, "파생 비율 기록이 실행 기록 싱크에 남지 않았습니다"
    assert observed_composition_steps(싱크), "실행 기록 계약이 이 기록을 버립니다"
