# -*- coding: utf-8 -*-
"""출고 모드 진단 한 줄(2026-09-24 후속) — 실제 run_v2 가 남기는 줄을 그대로 본다.

평가 산출물(diagnostics.json)에서 «작성 전 SHADOW 강등»과 «FULL 장부를 쓰고 난 뒤
강등»을 가르고, 검수 재요청 자리(bundled_retry)가 실제로 쓰였는지 보려는 줄이다.

★ 배선 시험이다 — 기대하는 줄을 시험 안에서 만들어 검사하지 않는다. run_v2 가
  진단 목록에 «실제로» 넣은 줄과, real.py 가 그 목록을 실행 기록으로 옮길 때 쓰는
  정화기(observed_composition_steps)를 지난 줄을 리터럴 기대값과 맞댄다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.composer.pipeline import run_v2
from src.features.composer.port import SectionEvidencePacketSet
from src.features.composer.tests.test_evidence_available_report import (
    _PARTIAL,
    _BudgetDiesAtThirdSection,
    _StrictThinWriter,
    _one_sentence_writer,
)
from src.features.composer.tests.test_pipeline import (
    _FakeReviewer,
    _FakeWriter,
    _raw_fragments,
    _strict_fragments,
    _strict_packet_set,
)
from src.features.composer.tests.test_section_public_manifest import (
    _BoundGroupedReviewer,
    _CompletePacketWriter,
    _FirstRowBrokenReviewer,
    _GlobalFailureReviewer,
    _NoDiagram,
    _packets,
)
from src.features.composer.validate import V2ValidationError
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality.composition_diagnostics import (
    observed_composition_steps,
)

_STEP = "8_출고모드_적용"


def _release_lines(diagnostics: list[dict]) -> list[dict]:
    """run_v2 가 넣은 출고 모드 줄 — 정화기를 지나도 한 글자도 안 바뀌어야 한다."""
    raw = [record for record in diagnostics if record.get("step") == _STEP]
    sanitized = [
        record for record in observed_composition_steps(diagnostics)
        if record.get("step") == _STEP
    ]
    assert sanitized == raw, "정화기가 출고 모드 줄을 바꾸거나 버렸다"
    return raw


def _line(requested, applied, downgraded_from, review_calls, ledger_used):
    return {
        "step": _STEP,
        "요청모드": requested,
        "적용모드": applied,
        "강등출처": downgraded_from,
        "검수호출": review_calls,
        "장부사용": ledger_used,
    }


def _full(**overrides):
    arguments = {
        "writer_ask": _CompletePacketWriter(),
        "reviewer_ask": _BoundGroupedReviewer(),
        "diagram_ask": _NoDiagram(),
        "release_mode": ReleaseMode.FULL,
        "section_evidence_packets": _packets(),
        "company_id": "00123456",
        "build_identity_sha256": "b" * 64,
    }
    arguments.update(overrides)
    return run_v2("가나다전자", (), None, **arguments)


def test_FULL_재요청을_쓴_실행은_검수호출_1_1과_적용모드_FULL을_남긴다():
    diagnostics: list[dict] = []
    output = _full(
        initial_reviewer_ask=_FirstRowBrokenReviewer(),
        initial_retry_reviewer_ask=_BoundGroupedReviewer(),
        composition_diagnostics_sink=diagnostics,
    )

    assert output.effective_release_mode == "FULL"
    assert _release_lines(diagnostics) == [
        _line("FULL", "FULL", "", {"bundled": 1, "bundled_retry": 1}, True),
    ]


def test_FULL_재요청이_없던_실행은_재요청_자리를_0으로_남긴다():
    diagnostics: list[dict] = []
    _full(
        initial_reviewer_ask=_BoundGroupedReviewer(),
        initial_retry_reviewer_ask=_BoundGroupedReviewer(),
        composition_diagnostics_sink=diagnostics,
    )

    assert _release_lines(diagnostics) == [
        _line("FULL", "FULL", "", {"bundled": 1, "bundled_retry": 0}, True),
    ]


def test_FULL_작성_뒤_강등은_장부사용_참과_강등출처_FULL을_남긴다():
    diagnostics: list[dict] = []
    output = run_v2(
        "가나다전자",
        _strict_fragments(),
        None,
        writer_ask=_StrictThinWriter(),
        reviewer_ask=_FakeReviewer(),
        release_mode=ReleaseMode.FULL,
        section_evidence_packets=_strict_packet_set(),
        company_id="00123456",
        build_identity_sha256="b" * 64,
        evidence_available_fallback=True,
        composition_diagnostics_sink=diagnostics,
    )

    assert (output.effective_release_mode, output.downgraded_from_release_mode) == (
        "SHADOW", "FULL",
    )
    assert _release_lines(diagnostics) == [
        _line("FULL", "SHADOW", "FULL", {"bundled": 1, "bundled_retry": 0}, True),
    ]


def test_FULL_작성_전_강등은_장부사용_거짓과_검수호출_null을_남긴다():
    # 한 장의 근거가 필수 의미칸을 하나도 받치지 못하면 FULL 사전 검사가 AI 호출
    # «전»에 SHADOW 로 다시 돈다 — 그 안쪽 실행이 자기 줄을 연다(장부 없음).
    packets = _packets()
    starved = replace(
        packets.packets[0],
        fragments=tuple(
            replace(fragment, supported_claim_slots=())
            for fragment in packets.packets[0].fragments
        ),
    )
    packet_set = SectionEvidencePacketSet(
        company_id=packets.company_id,
        evidence_generation_sha256=packets.evidence_generation_sha256,
        packets=(starved, *packets.packets[1:]),
    )
    writer = _CompletePacketWriter()
    diagnostics: list[dict] = []
    output = _full(
        writer_ask=writer,
        section_evidence_packets=packet_set,
        evidence_available_fallback=True,
        composition_diagnostics_sink=diagnostics,
    )

    assert writer.prompts == []
    assert (output.effective_release_mode, output.downgraded_from_release_mode) == (
        "SHADOW", "FULL",
    )
    assert _release_lines(diagnostics) == [
        _line("FULL", "SHADOW", "FULL", None, False),
    ]


def test_출고_검증에서_멈춘_FULL은_적용모드_빈_값과_재요청_사용을_남긴다():
    # AI 전역 장애 폴백(두 번째 검수 호출의 비강등 장애) 뒤 FULL 사후 판정이 막는
    # 경로다. 예외가 나도 real.py 의 finally 가 이 목록을 실행 기록으로 옮긴다.
    diagnostics: list[dict] = []
    with pytest.raises(V2ValidationError, match="post_validation_safety_blocked"):
        _full(
            initial_reviewer_ask=_FirstRowBrokenReviewer(),
            initial_retry_reviewer_ask=_GlobalFailureReviewer(),
            preserve_on_ask_failure=True,
            composition_diagnostics_sink=diagnostics,
        )

    assert _release_lines(diagnostics) == [
        _line("FULL", "", "", {"bundled": 1, "bundled_retry": 1}, True),
    ]


def test_폴백이_막힌_전역_장애로_run_v2_밖으로_나가도_실제_검수호출_수가_남는다():
    # 2026-09-24 독립 검토 F2(탐침 09행) — 두 번째 검수 호출이 폴백 금지 원인(조정
    # 오류·epoch 변경·전역 취소)을 만나면 AI 전역 장애 갈래가 그대로 재전파한다.
    # 검수 직후의 셈이 finally 로 옮겨져, 그때도 줄에 실제 횟수 {1,1}이 남는다.
    from src.features.composer.port import AskFatalError
    from src.features.composer.tests.test_section_public_manifest import (
        _CauseRaisingReviewer,
    )
    from src.shared.generation_coordination import GenerationCoordinationError

    diagnostics: list[dict] = []
    with pytest.raises(AskFatalError) as raised:
        _full(
            initial_reviewer_ask=_FirstRowBrokenReviewer(),
            initial_retry_reviewer_ask=_CauseRaisingReviewer(
                GenerationCoordinationError("전역 취소"),
            ),
            preserve_on_ask_failure=True,
            composition_diagnostics_sink=diagnostics,
        )

    assert isinstance(raised.value.cause, GenerationCoordinationError)
    assert _release_lines(diagnostics) == [
        _line("FULL", "", "", {"bundled": 1, "bundled_retry": 1}, True),
    ]


def test_부분보고서_경로는_장부사용_거짓과_검수호출_null을_남긴다():
    diagnostics: list[dict] = []
    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=_one_sentence_writer(),
        reviewer_ask=_FakeReviewer(),
        evidence_availability=_PARTIAL,
        composition_diagnostics_sink=diagnostics,
    )

    assert output.effective_release_mode == "SHADOW"
    assert _release_lines(diagnostics) == [
        _line("SHADOW", "SHADOW", "", None, False),
    ]


def test_SHADOW_AI_전역_장애_폴백도_적용모드_SHADOW를_남긴다():
    diagnostics: list[dict] = []
    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=_BudgetDiesAtThirdSection(),
        reviewer_ask=_FakeReviewer(),
        evidence_available_fallback=True,
        composition_diagnostics_sink=diagnostics,
    )

    assert output.ai_stages_skipped == ("compose_verify",)
    assert _release_lines(diagnostics) == [
        _line("SHADOW", "SHADOW", "", None, False),
    ]


def test_조각이_없는_AI_0회_보고서도_출고모드_줄을_남긴다():
    writer = _FakeWriter()
    diagnostics: list[dict] = []
    output = run_v2(
        "가나다전자",
        {},
        None,
        writer_ask=writer,
        reviewer_ask=_FakeReviewer(),
        company_id="00123456",
        evidence_availability=_PARTIAL,
        composition_diagnostics_sink=diagnostics,
    )

    assert writer.prompts == []
    assert output.effective_release_mode == "SHADOW"
    assert _release_lines(diagnostics) == [
        _line("SHADOW", "SHADOW", "", None, False),
    ]
