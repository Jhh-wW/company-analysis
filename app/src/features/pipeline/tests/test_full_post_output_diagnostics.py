"""FULL 생성 뒤 최종 근거 결속 오류가 안전한 내부 사유로 닫히는지 검증한다."""

from __future__ import annotations

import pytest

import src.features.composer.pipeline as composer_pipeline
import src.shared.report_generation.canonical as generation_canonical
from src.core import deployment_identity
from src.features.budget import provider_budget
from src.features.company_comparison.tests.test_logic import _v2_comparison_result
from src.features.pipeline import real
from src.features.pipeline.official_evidence_transport_adapter import (
    merge_official_evidence_fragments,
)
from src.features.pipeline.port import Outcome
from src.features.pipeline.tests.test_full_evidence_end_to_end import (
    _BUSINESS_DATE,
    _COMPANY_ID,
    _FILING,
    _FILING_TEXT,
    _ExactBundledReviewer,
    _ExactPacketWriter,
    _official_evidence,
)
from src.features.pipeline.tests.test_real_cache import FakeEngine
from src.features.revenuemix.logic import build as build_revenue_mix
from src.shared import engine_build_identity as build_identity_contract
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
)
from src.shared.report_evidence.constants import ReleaseMode


@pytest.fixture
def _full_runtime(monkeypatch: pytest.MonkeyPatch):
    real.engine_mode._reset_process_engine_mode_for_tests()  # noqa: SLF001
    build_identity_contract._reset_process_engine_build_identity_for_tests()  # noqa: SLF001
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.FULL.value)
    generation_mode = real.engine_mode.freeze_process_engine_mode(
        real.engine_mode.EngineMode.V2
    )
    build_identity = build_identity_contract.freeze_process_engine_build_identity()
    yield generation_mode, build_identity
    real.engine_mode._reset_process_engine_mode_for_tests()  # noqa: SLF001
    build_identity_contract._reset_process_engine_build_identity_for_tests()  # noqa: SLF001


@pytest.mark.parametrize("error_type", (TypeError, ValueError))
def test_FULL_생성후_manifest_결속형식오류는_자료부족이_아닌_내부계약오류다(
    _full_runtime,
    monkeypatch: pytest.MonkeyPatch,
    error_type: type[Exception],
) -> None:
    generation_mode, build_identity = _full_runtime
    fake_engine = FakeEngine()
    financials, _years = fake_engine.fetch_financials(
        _COMPANY_ID,
        object(),
        business_date=_BUSINESS_DATE,
    )
    revenue_fragments, revenue_tables = real._bind_revenue_table_evidence_fragments(
        {},
        build_revenue_mix(_FILING_TEXT),
        filing=_FILING,
        filing_text=_FILING_TEXT,
    )
    fragments, added = merge_official_evidence_fragments(
        revenue_fragments,
        _official_evidence(),
    )
    assert added == 9
    financial_fragment = next(
        dict(fragment)
        for fragment in fake_engine.make_fragments("", financials).values()
        if fragment.get("종류") == "재무"
        and str(fragment.get("원문") or "").startswith("주요계정(DART API):")
    )
    fragments[max(fragments) + 1] = financial_fragment
    writer = _ExactPacketWriter()
    reviewer = _ExactBundledReviewer()

    def fake_ask_factory(_engine, _client, *, stage: str, max_tokens: int):
        assert max_tokens > 0
        if stage == "v2_compose":
            return writer
        if stage == "v2_review":
            return reviewer

        def forbidden_diagram(_prompt: str) -> str:
            raise AssertionError("FULL 구성 도식은 별도 AI를 부르면 안 됩니다")

        return forbidden_diagram

    def fail_post_output_binding(*_args, **_kwargs) -> None:
        raise error_type("시험 원문·내부 예외문은 최종 사유에 실리면 안 됩니다")

    monkeypatch.setattr(real, "_v2_ask_via_provider", fake_ask_factory)
    monkeypatch.setattr(real, "_v2_cache_save", lambda **_kwargs: None)
    # composer는 모듈 import 때 잡은 원래 검증 함수를 써 정상 출력을 만든다.
    # real.py가 출력 뒤 지연 import하는 마지막 결속 검사만 여기서 깨뜨린다.
    monkeypatch.setattr(
        generation_canonical,
        "assert_report_matches_generation_evidence",
        fail_post_output_binding,
    )
    assert (
        composer_pipeline.assert_report_matches_generation_evidence
        is not fail_post_output_binding
    )
    steps: list[dict[str, object]] = []

    with provider_budget.activate(100_000.0):
        result = real._run_v2_composer(
            engine=real._MeteredEngine(fake_engine),
            client=object(),
            company_name="가나다회사",
            corp_type="상장사",
            frags=fragments,
            financials=financials,
            filing=_FILING,
            revenue_tables=revenue_tables,
            sources=[],
            business_date=_BUSINESS_DATE,
            model="가짜모델",
            steps=steps,
            corp_id=_COMPANY_ID,
            current_fiscal_year=2025,
            source_identity_digest="a" * 64,
            build_identity=build_identity,
            generation_mode=generation_mode,
            comparison_result=_v2_comparison_result(),
        )

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
    assert "시험 원문" not in result.message
    assert steps[-1] == {
        "step": "v2_출고검증_차단",
        "사유": ["FULL 생성 생산 증거와 최종 보고서 결속이 깨졌습니다"],
    }
    assert "시험 원문" not in str(steps)
    assert len(writer.prompts) == 9
    assert len(reviewer.prompts) == 1
