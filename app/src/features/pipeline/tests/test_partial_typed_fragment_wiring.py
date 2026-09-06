"""부분 보고서에서 작성기가 «실제로» 무엇을 받는지 본다.

★ 왜 이 시험이 따로 필요한가 — 기존 배선 시험은 부분 경로를 돌린 뒤 시험 «안에서»
  따로 장별 packet을 만들어 검사했다. 그래서 「packet을 만들면 뉴스 조각에 매체와
  발행일이 있다」만 지켰고, 정작 작성기가 받은 것이 raw dict인지 typed 조각인지는
  아무 시험도 보지 않았다. 운영에서는 부분 보고서의 보조 보도표가 소유 장을 잃어
  구조적으로 만들어지지 않았다.

★ 그래서 여기서는 `_wire_runtime`이 기록한 «진짜 연결부 인자»를 그대로 다시
  `_run_v2_composer`에 흘려 넣고, 작성기 진입점(`composer_pipeline.run_v2`)이 받은
  두 번째 인자를 직접 본다. 가짜 작성기는 `_wire_runtime`이 연결부 자체를
  통째로 갈아 끼우므로 그 기록만으로는 이 결함을 볼 수 없다(실측).

★ AI·네트워크 0회.
"""

from __future__ import annotations

from typing import Any

import pytest

import src.features.composer.pipeline as composer_pipeline
from src.core import news_intake_switch
from src.features.company_comparison import v2_bridge
from src.features.composer.port import CollectedFragment
from src.features.pipeline import real
from src.features.pipeline.evidence_transport import EvidenceTransportError
from src.features.pipeline.port import Grade, Outcome, Report
from src.features.pipeline.tests.test_news_block_wiring import _partial_path_calls
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID,
)
from src.shared.report_evidence.constants import ReleaseMode, SOURCE_KIND_NEWS

#: 연결부는 `_wire_runtime`이 가짜로 갈아 끼운다. 진짜 연결부를 다시 부르려고
#: monkeypatch 이전의 원본 함수를 import 시점에 붙잡아 둔다.
_ORIGINAL_RUN_V2_COMPOSER = real._run_v2_composer  # noqa: SLF001

_TYPED_STEP = "v2_조각_typed전달"
_TYPED_STEP_BLOCKED = "v2_조각_typed전달_불가"


@pytest.fixture(autouse=True)
def _reset_news_switch(monkeypatch: pytest.MonkeyPatch):
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    monkeypatch.delenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, raising=False)
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001


class _FakeWriter:
    """작성기 진입점이 받은 인자를 기록하고 최소 산출물을 돌려준다."""

    def __init__(self) -> None:
        self.fragments: Any = None
        self.calls = 0

    def __call__(self, company_name, fragments, performance_table, **kwargs):
        self.calls += 1
        self.fragments = fragments
        return composer_pipeline.V2RunOutput(
            report=Report(
                company=company_name,
                job="",
                corp_type="상장사",
                grade=Grade.PARTIAL,
                sections=[],
                citations=[],
            ),
            composed_sentences=0,
            verified_sentences=0,
        )


def _replay_connector(
    monkeypatch: pytest.MonkeyPatch,
    composer_kwargs: dict[str, Any],
    **overrides: Any,
) -> tuple[Any, _FakeWriter, list[dict[str, Any]]]:
    """기록된 연결부 인자를 진짜 `_run_v2_composer`에 그대로 다시 흘린다."""

    writer = _FakeWriter()
    monkeypatch.setattr(composer_pipeline, "run_v2", writer)
    monkeypatch.setattr(real, "_v2_cache_save", lambda **_kwargs: None)
    steps: list[dict[str, Any]] = []
    result = _ORIGINAL_RUN_V2_COMPOSER(
        **{**composer_kwargs, "steps": steps, **overrides}
    )
    return result, writer, steps


def _step_names(steps: list[dict[str, Any]]) -> list[str]:
    return [str(step.get("step", "")) for step in steps]


# ══════════════════════════════════════════════════════════
# ① 부분 보고서에서 작성기가 typed 조각을 받는다
# ══════════════════════════════════════════════════════════


def test_부분보고서_작성기는_raw_dict가_아니라_typed_조각을_받는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _result = _partial_path_calls(monkeypatch)
    composer_kwargs = calls.composers[0]
    assert composer_kwargs["release_mode_override"] is ReleaseMode.SHADOW

    result, writer, steps = _replay_connector(monkeypatch, composer_kwargs)

    assert result.outcome is Outcome.REPORT, result.message
    assert writer.calls == 1
    assert type(writer.fragments) is tuple, type(writer.fragments)
    assert writer.fragments, "작성기가 조각을 하나도 못 받았습니다"
    assert all(
        type(fragment) is CollectedFragment for fragment in writer.fragments
    )
    # 공개 번호는 raw dict의 키 그대로다 — 부록 번호가 밀리면 안 된다.
    assert [fragment.fragment_id for fragment in writer.fragments] == [
        str(public_id) for public_id in sorted(composer_kwargs["frags"])
    ]


def test_부분보고서_뉴스조각에_매체와_발행일과_의미칸이_실려_온다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """raw dict 어댑터가 버리던 값들이 실제 작성기 인자에 있는지 본다."""

    calls, _result = _partial_path_calls(monkeypatch)

    _result2, writer, _steps = _replay_connector(monkeypatch, calls.composers[0])

    news = [
        fragment
        for fragment in writer.fragments
        if fragment.formal_source_kind == SOURCE_KIND_NEWS
    ]
    assert news, "작성기가 받은 조각에 뉴스가 하나도 없습니다"
    for fragment in news:
        assert fragment.source_publisher == "media.example"
        assert fragment.document_date == "2026-09-01"
        assert fragment.supported_claim_slots
        assert fragment.counts_toward_document_floor is False
        # 장 선언이 살아 있어야 보도표가 소유 장을 되찾는다.
        assert fragment.location.startswith("기사 본문 · news-fragment-")


def test_부분보고서는_typed전달_단계를_실행기록에_남긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls, _result = _partial_path_calls(monkeypatch)

    _result2, writer, steps = _replay_connector(monkeypatch, calls.composers[0])

    typed_step = next(
        step for step in steps if step.get("step") == _TYPED_STEP
    )
    assert typed_step["조각"] == len(writer.fragments)
    assert typed_step["보조"] == len(
        [
            fragment
            for fragment in writer.fragments
            if fragment.formal_source_kind == SOURCE_KIND_NEWS
        ]
    )
    assert typed_step["보조"] > 0
    assert _TYPED_STEP_BLOCKED not in _step_names(steps)


# ══════════════════════════════════════════════════════════
# ② 변환이 막혀도 부분 보고서를 잃지 않는다
# ══════════════════════════════════════════════════════════


def test_typed변환이_막히면_raw_dict로_돌아가고_보고서는_그대로_나온다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이 변환 하나 때문에 부분 보고서를 통째로 잃으면 안 된다."""

    calls, _result = _partial_path_calls(monkeypatch)

    def 막힌_변환(**_kwargs: Any):
        raise EvidenceTransportError(
            "시험용 차단", detail_code=FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID
        )

    monkeypatch.setattr(real, "typed_fragments_from_raw", 막힌_변환)

    result, writer, steps = _replay_connector(monkeypatch, calls.composers[0])

    assert result.outcome is Outcome.REPORT, result.message
    assert writer.fragments is calls.composers[0]["frags"]
    blocked = next(
        step for step in steps if step.get("step") == _TYPED_STEP_BLOCKED
    )
    assert blocked["사유코드"] == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID
    assert _TYPED_STEP not in _step_names(steps)


# ══════════════════════════════════════════════════════════
# ③ 대조군 — FULL은 아무것도 바뀌지 않는다
# ══════════════════════════════════════════════════════════


def test_FULL은_예전처럼_raw_dict를_넘기고_새_단계를_남기지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """packet이 정본인 FULL 갈래를 이 변경이 건드리지 않았는지 본다.

    packet 생산·양사 비교 결속·문서 하한은 이 시험의 관심사가 아니라 다른
    시험의 관심사다. 여기서는 그 세 협력자만 통과시키고 «연결부가 작성기에
    무엇을 넘겼나»만 본다.
    """

    calls, _result = _partial_path_calls(monkeypatch)
    packet_sentinel = object()
    monkeypatch.setattr(
        real, "_full_section_evidence_packets", lambda **_kwargs: packet_sentinel
    )
    monkeypatch.setattr(
        v2_bridge,
        "attach_comparison_program_evidence",
        lambda packets, _comparison: packets,
    )

    class _통과한_사전검사:
        can_call_ai = True
        detail_code = ""
        independent_document_count = 8

    monkeypatch.setattr(
        real, "assess_packet_document_sources", lambda _packets: _통과한_사전검사()
    )

    result, writer, steps = _replay_connector(
        monkeypatch,
        calls.composers[0],
        release_mode_override=ReleaseMode.FULL,
        # 부분 갈래는 양사 비교 생산물을 만들지 않는다. FULL 계약이 그 존재를
        # 요구하므로 자리표시자를 넣는다 — 위에서 결속 함수도 항등으로 뒀다.
        comparison_result=object(),
    )

    # ★ 결과 등급은 여기서 보지 않는다 — 가짜 작성기의 최소 산출물은 FULL의
    #   «출력 뒤» 생산 증거 계약(실제 지표·품질 관측)을 못 채워 GATE_STOPPED가
    #   된다. 그건 이 변경과 무관한 다른 겹의 방어이고, 그 겹은 FULL 종단
    #   시험이 따로 지킨다. 여기서 보는 것은 «작성기가 무엇을 받았나»다.
    assert writer.calls == 1, "FULL에서 작성기까지 가지 못했습니다"
    assert writer.fragments is calls.composers[0]["frags"]
    assert _TYPED_STEP not in _step_names(steps)
    assert _TYPED_STEP_BLOCKED not in _step_names(steps)
    assert result.outcome in (Outcome.REPORT, Outcome.GATE_STOPPED), result.outcome
