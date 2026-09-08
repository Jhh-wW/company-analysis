"""공식 수집 attempt의 run 진단 보존 경계 시험."""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.observability import run_diagnostics
from src.features.pipeline import real
from src.features.pipeline.official_collection_diagnostic_constants import (
    OFFICIAL_COLLECTION_DIAGNOSTICS_STEP,
    UNKNOWN_OFFICIAL_COLLECTION_VALUE,
)
from src.features.pipeline.official_collection_diagnostics import (
    official_collection_attempt_step,
)
from src.features.pipeline.port import Outcome
from src.features.pipeline.tests.test_official_evidence_runtime import (
    _Collector,
    _freeze_runtime,
    _official_result,
    _official_result_with_partial_failures,
    _run,
    _wire_runtime,
)
from src.features.pipeline.tests.test_real_cache import FakeEngine
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT,
)
from src.shared.report_evidence.constants import CollectionState, ReleaseMode
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult


@pytest.mark.parametrize("reason", [
    "robots_missing", "root_identity_name_only", "sitemap_missing_404",
    "ir_pdf_failed", "ir_pdf_none", "page_failed_503", "sitemap_transient_429",
    "truncated_time_cap", "truncated_client_redirect_cap",
])
def test_실제_공식웹_관측과_표준_HTTP_사유를_잃지_않는다(reason: str) -> None:
    base = _official_result(first_state=CollectionState.FAILED)
    first = base.candidates[0]
    candidate = replace(first, attempts=(replace(first.attempts[0], reason_code=reason),))
    result = replace(base, candidates=(candidate, *base.candidates[1:]))
    assert official_collection_attempt_step(result)["histogram"][0]["reason_code"] == reason


@pytest.mark.parametrize("reason", [
    "page_failed_999", "sitemap_failed_503_private", "robots_missing_https_secret",
])
def test_상태_사유_접두사가_같아도_임의_접미사는_보존하지_않는다(reason: str) -> None:
    base = _official_result(first_state=CollectionState.FAILED)
    first = base.candidates[0]
    candidate = replace(first, attempts=(replace(first.attempts[0], reason_code=reason),))
    result = replace(base, candidates=(candidate, *base.candidates[1:]))
    assert official_collection_attempt_step(result)["histogram"][0]["reason_code"] == UNKNOWN_OFFICIAL_COLLECTION_VALUE


def test_재분류로_복제된_시도는_한번만_집계하고_미등록값은_unknown으로_닫는다() -> None:
    base = _official_result(first_state=CollectionState.FAILED)
    original = base.candidates[0]
    # 실제 시도 식별자는 같은데 장 후보가 둘이면 재시도 두 번으로 부풀리면 안 된다.
    duplicate = replace(
        original.attempts[0],
        reason_code="unregistered_transport_detail",
    )
    candidates = list(base.candidates)
    candidates[0] = replace(original, attempts=(duplicate,))
    candidates[1] = replace(candidates[1], attempts=(duplicate,))
    result = OfficialEvidenceCollectionResult(
        company_id=base.company_id,
        candidates=tuple(candidates),
    )

    step = official_collection_attempt_step(result)

    assert step == {
        "step": OFFICIAL_COLLECTION_DIAGNOSTICS_STEP,
        "attempt_count": 1,
        "histogram": [
            {
                "source_kind": "official_web_page",
                "state": "FAILED",
                "reason_code": UNKNOWN_OFFICIAL_COLLECTION_VALUE,
                "requirement": "REQUIRED",
                "count": 1,
            }
        ],
    }
    assert duplicate.attempt_id not in repr(step)
    assert base.company_id not in repr(step)


def test_실제_게이트중단에도_수집직후_histogram이_run_steps에_남는다(
    monkeypatch,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([_official_result(first_state=CollectionState.FAILED)])
    _wire_runtime(monkeypatch, engine=engine)

    with run_diagnostics.capture() as captured:
        result = _run(collector)

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT
    assert [step for step in captured.steps if step["step"] == OFFICIAL_COLLECTION_DIAGNOSTICS_STEP] == [
        {
            "step": OFFICIAL_COLLECTION_DIAGNOSTICS_STEP,
            "attempt_count": 1,
            "histogram": [
                {
                    "source_kind": "official_web_page",
                    "state": "FAILED",
                    "reason_code": UNKNOWN_OFFICIAL_COLLECTION_VALUE,
                    "requirement": "REQUIRED",
                    "count": 1,
                }
            ],
        }
    ]


def test_성공_출력에도_공식웹_실패와_절단_관측을_한번씩_남긴다(
    monkeypatch,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([_official_result_with_partial_failures()])
    _wire_runtime(monkeypatch, engine=engine)

    with run_diagnostics.capture() as captured:
        result = _run(collector)

    step = next(
        step for step in captured.steps
        if step["step"] == OFFICIAL_COLLECTION_DIAGNOSTICS_STEP
    )
    assert result.outcome is Outcome.REPORT
    assert step["attempt_count"] == 3
    assert step["histogram"] == [
        {
            "source_kind": "official_web_page",
            "state": "FAILED",
            "reason_code": "robots_disallowed",
            "requirement": "OPTIONAL",
            "count": 1,
        },
        {
            "source_kind": "official_web_page",
            "state": "TRUNCATED",
            "reason_code": "truncated_page_cap",
            "requirement": "OPTIONAL",
            "count": 2,
        },
    ]
