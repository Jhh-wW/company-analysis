"""보완조사 출고 guard가 legacy 조각 출처 때문에 닫히지 않는지 고정한다.

손으로 만든 ``Report``를 쓰지 않는다. 운영과 같은 v2 composer를 돌려 나온 실제
보고서와, 운영이 실제로 주입하는 provenance 검증자
(``supplementary_research_source_verifier``)를 그대로 써서 판정한다. 그래야
「시험 안에서 따로 만든 등록부는 통과하는데 운영 등록부만 닫히는」 배선 결함이
드러난다.

배경(재현된 결함): v2 보고서의 출처 등록부에는 typed 공식 출처와 함께 legacy
조각 출처(공시 원문 조각·재무 API 응답)가 남는다. legacy 출처는 v3 출처표
필수 필드가 없어 정본 검증이 ``None``을 돌려주는데, guard가 그것을 등록부
전체의 실패로 읽어 ``supplementary_release_invalid_citation_registry``로 닫았고,
runtime이 그 코드를 ``internal_evidence_contract``(내부 계약 오류)로 올렸다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

import src.features.pipeline.tests.test_full_evidence_end_to_end as full_evidence_e2e
from src.core.source_verification_adapter import (
    supplementary_research_source_verifier,
)
from src.features.budget import provider_budget
from src.features.company_comparison.tests.test_logic import _v2_comparison_result
from src.features.pipeline import real
from src.features.pipeline.official_evidence_transport_adapter import (
    merge_official_evidence_fragments,
)
from src.features.pipeline.port import Outcome, Report
from src.features.pipeline.supplementary_research_release import (
    assess_supplementary_research_release,
)
from src.features.pipeline.supplementary_research_release_constants import (
    SUPPLEMENTARY_RELEASE_ALLOWED,
    SUPPLEMENTARY_RELEASE_INVALID_CITATION_REGISTRY,
)
from src.features.pipeline.supplementary_research_runtime import (
    enforce_supplementary_research_release,
)
from src.features.pipeline.tests.test_full_evidence_end_to_end import (
    _BUSINESS_DATE,
    _COMPANY_ID,
    _FILING,
    _FILING_TEXT,
    _ExactPacketWriter,
    _official_evidence,
)
from src.features.pipeline.tests.test_full_post_output_diagnostics import (  # noqa: F401
    _full_runtime,
    _FuturePlanBundledReviewer,
    _section_sentences_with_future_plans,
)
from src.features.pipeline.tests.test_real_cache import FakeEngine
from src.features.provenance.sources import Source
from src.features.revenuemix.logic import build as build_revenue_mix
from src.shared.report_evidence.registry_eligibility import (
    registry_indexing_ineligible,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.source_verification import SourceVerification


#: 운영 v2 보고서의 출처 등록부에 반드시 남는 legacy 조각 출처 최소 개수.
#: 이 값이 0이 되면 아래 시험은 「legacy가 섞여도 안 닫힌다」를 더는 증명하지
#: 못한다. 상수를 낮추지 말고 왜 legacy가 사라졌는지부터 확인해야 한다.
_MINIMUM_LEGACY_CITATIONS = 1

#: 이 보고서가 최소한 채워야 할 본문 장 수(guard의 하한과 별개로 고정한다).
_MINIMUM_QUALIFIED_SECTIONS = 3


def _real_report_and_evidence(
    monkeypatch: pytest.MonkeyPatch,
    runtime: tuple[object, object],
) -> tuple[Report, OfficialEvidenceCollectionResult]:
    """운영 v2 composer를 그대로 돌려 실제 보고서와 공식 근거를 만든다."""

    generation_mode, build_identity = runtime
    # 공식 근거 조각을 만들기 «전»에 갈아 끼운다. 원문 조각과 작가 응답이 같은
    # 계획 문장을 쓰게 하는 유일한 자리다.
    monkeypatch.setattr(
        full_evidence_e2e,
        "_section_sentences",
        _section_sentences_with_future_plans,
    )
    fake_engine = FakeEngine()
    financials, _years = fake_engine.fetch_financials(
        _COMPANY_ID, object(), business_date=_BUSINESS_DATE
    )
    revenue_fragments, revenue_tables = real._bind_revenue_table_evidence_fragments(
        {}, build_revenue_mix(_FILING_TEXT), filing=_FILING, filing_text=_FILING_TEXT
    )
    official_evidence = _official_evidence()
    fragments, added = merge_official_evidence_fragments(
        revenue_fragments, official_evidence
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
    reviewer = _FuturePlanBundledReviewer()

    def fake_ask_factory(
        _engine, _client, *, stage: str, max_tokens: int, reserved_calls: int = 0,
    ):
        assert max_tokens > 0
        if stage == "v2_compose":
            return writer
        if stage == "v2_review":
            return reviewer

        def forbidden_diagram(_prompt: str) -> str:
            raise AssertionError("FULL 구성 도식은 별도 AI를 부르면 안 됩니다")

        return forbidden_diagram

    monkeypatch.setattr(real, "_v2_ask_via_provider", fake_ask_factory)
    monkeypatch.setattr(real, "_v2_cache_save", lambda **_kwargs: None)
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
            steps=[],
            corp_id=_COMPANY_ID,
            current_fiscal_year=2025,
            source_identity_digest="a" * 64,
            build_identity=build_identity,
            generation_mode=generation_mode,
            comparison_result=_v2_comparison_result(),
        )
    assert result.outcome is Outcome.REPORT, result.message
    assert result.report is not None
    return result.report, official_evidence


def test_실제보고서의_legacy_조각출처는_내부계약오류로_닫지_않는다(
    _full_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report, official_evidence = _real_report_and_evidence(monkeypatch, _full_runtime)
    verifier = supplementary_research_source_verifier()
    registry = tuple(report.citations)

    # ① 이 시험이 지키려는 조건 자체를 먼저 확인한다 — 운영 등록부에는 정본
    #    검증을 통과하지 못하는 legacy 조각 출처가 실제로 섞여 있다.
    rejected = [
        source
        for source in registry
        if type(source) is Source
        and verifier(
            source, registry, reference_date=report.as_of_date, evidence_text=""
        )
        is None
    ]
    assert len(rejected) >= _MINIMUM_LEGACY_CITATIONS
    assert all(not source.formal_source_kind.strip() for source in rejected)
    assert all(not source.is_canonical_valid for source in rejected)

    # ② 그런데도 판정은 내부 계약 오류가 아니어야 한다.
    decision = assess_supplementary_research_release(
        report, official_evidence=official_evidence, source_verifier=verifier
    )
    assert decision.code != SUPPLEMENTARY_RELEASE_INVALID_CITATION_REGISTRY
    assert decision.code == SUPPLEMENTARY_RELEASE_ALLOWED
    assert decision.allowed is True

    # ③ 결속 검증은 그대로다 — 본문 장은 실제 공식 수집 문서와 재대조된
    #    출처로만 세어졌고, 핵심 두 장과 DART 본문 하한도 채워졌다.
    assert len(decision.qualified_section_ids) >= _MINIMUM_QUALIFIED_SECTIONS
    assert "identity" in decision.official_grounded_core_section_ids
    assert "business_model" in decision.official_grounded_core_section_ids
    assert decision.dart_grounded_section_ids


def test_실제보고서는_보완조사_runtime에서도_출고권한을_잃지_않는다(
    _full_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report, official_evidence = _real_report_and_evidence(monkeypatch, _full_runtime)
    result = real.RunResult(outcome=Outcome.REPORT, report=report)
    steps: list[dict] = []

    enforced = enforce_supplementary_research_release(
        result, official_evidence=official_evidence, steps=steps
    )

    assert enforced.outcome is Outcome.REPORT
    assert enforced.report is report
    assert enforced.final_gate_reason == ""
    assert [step["step"] for step in steps] == ["8_보완조사_최종검사"]
    assert steps[0]["허용"] is True
    assert steps[0]["사유코드"] == SUPPLEMENTARY_RELEASE_ALLOWED
    assert "identity" in steps[0]["검증본문장"]


def test_한_줄도_검증되지_않는_등록부는_그대로_내부계약오류로_닫는다(
    _full_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """legacy 제외는 «전부 실패»까지 눈감아 주지 않는다(fail-closed 유지)."""

    report, official_evidence = _real_report_and_evidence(monkeypatch, _full_runtime)

    def reject_every_source(_source, _registry, *, reference_date, evidence_text):
        assert reference_date == report.as_of_date
        assert evidence_text == ""
        return None

    decision = assess_supplementary_research_release(
        report,
        official_evidence=official_evidence,
        source_verifier=reject_every_source,
    )

    assert decision.allowed is False
    assert decision.code == SUPPLEMENTARY_RELEASE_INVALID_CITATION_REGISTRY


def test_형식이_깨진_검증결과와_중복_등록부는_그대로_닫는다(
    _full_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """검증자가 «자격 없음»이 아니라 «깨진 값»을 주면 예전처럼 닫는다."""

    report, official_evidence = _real_report_and_evidence(monkeypatch, _full_runtime)
    honest = supplementary_research_source_verifier()

    def zero_numbered(_source, registry, *, reference_date, evidence_text):
        verified = honest(
            _source, registry, reference_date=reference_date, evidence_text=evidence_text
        )
        return None if verified is None else replace(verified, number=0)

    def one_shared_id(_source, registry, *, reference_date, evidence_text):
        verified = honest(
            _source, registry, reference_date=reference_date, evidence_text=evidence_text
        )
        return None if verified is None else replace(verified, source_id="같은-아이디")

    for broken in (zero_numbered, one_shared_id):
        decision = assess_supplementary_research_release(
            report, official_evidence=official_evidence, source_verifier=broken
        )
        assert decision.allowed is False
        assert decision.code == SUPPLEMENTARY_RELEASE_INVALID_CITATION_REGISTRY

    # 검증자가 정직하게 답하면 같은 보고서가 통과한다 — 위 두 실패가 보고서
    # 자체의 문제가 아니라 등록부 형식 방어에서 나온 것임을 못박는다.
    assert (
        assess_supplementary_research_release(
            report, official_evidence=official_evidence, source_verifier=honest
        ).code
        == SUPPLEMENTARY_RELEASE_ALLOWED
    )
    assert type(honest(report.citations[2], tuple(report.citations),
                       reference_date=report.as_of_date,
                       evidence_text="")) is SourceVerification


def test_등록부의_자격없는_줄과_정본_줄이_실제로_갈린다(
    _full_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """이 시험이 지키려는 «구분» 자체를 실측으로 못 박는다.

    ★ legacy 모양은 실제로 두 가지다(둘 다 도장·등록부가 깨진 것이 아니다):
        · 공시 원문 조각 — ``source_type``·``fact_status``가 빈 문자열
        · DART 재무 API 응답 — 세 날짜 필드가 모두 빈 문자열
      술어가 앞의 한 가지만 알면 두 번째가 «검증 실패»로 분류돼 legacy 보존
      시험이 빨간불이 된다. 그래서 두 모양이 등록부에 실제로 함께 있다는
      사실을 여기서 고정한다.
    """

    report, _official_evidence = _real_report_and_evidence(monkeypatch, _full_runtime)
    verifier = supplementary_research_source_verifier()
    registry = tuple(report.citations)

    unverified = [
        source
        for source in registry
        if verifier(
            source, registry, reference_date=report.as_of_date, evidence_text=""
        )
        is None
    ]
    assert len(unverified) >= _MINIMUM_LEGACY_CITATIONS
    # 검증되지 않은 줄은 «전부» 색인 자격이 없는 legacy 줄이어야 한다.
    assert all(registry_indexing_ineligible(source) for source in unverified)
    # 그리고 검증을 통과한 줄은 «하나도» 자격 없음으로 분류되면 안 된다.
    assert all(
        not registry_indexing_ineligible(source)
        for source in registry
        if source not in unverified
    )
    assert any(
        not source.source_type.strip() and not source.fact_status.strip()
        for source in unverified
    ), "공시 원문 조각 모양(필수 필드 부재)이 등록부에서 사라졌습니다"


def test_정본출처가_한_건이라도_검증불가면_그대로_닫는다(
    _full_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 독립 검토의 재현 그대로 — 정본 줄을 한 건씩 «검증 불가»로 만든다.

    변경 전에는 ``verified is None``을 전부 건너뛰어, 정본 출처의 도장이
    깨져도(=검증자가 ``None``) 보고서가 그대로 나갔다. 이제는 자격 없는
    legacy 줄만 건너뛰고 나머지는 예전처럼 등록부 전체를 닫는다.
    """

    report, official_evidence = _real_report_and_evidence(monkeypatch, _full_runtime)
    honest = supplementary_research_source_verifier()
    registry = tuple(report.citations)
    canonical = [
        source
        for source in registry
        if honest(
            source, registry, reference_date=report.as_of_date, evidence_text=""
        )
        is not None
    ]
    assert len(canonical) >= 10, "정본 출처가 너무 적어 이 시험이 의미를 잃습니다"

    for target in canonical:
        def one_source_unavailable(
            source, registry_arg, *, reference_date, evidence_text, _target=target
        ):
            if source is _target:
                return None
            return honest(
                source,
                registry_arg,
                reference_date=reference_date,
                evidence_text=evidence_text,
            )

        decision = assess_supplementary_research_release(
            report,
            official_evidence=official_evidence,
            source_verifier=one_source_unavailable,
        )
        assert decision.allowed is False, f"{target.number}번 정본 출처"
        assert decision.code == SUPPLEMENTARY_RELEASE_INVALID_CITATION_REGISTRY


def test_정본출처의_도장이_깨지면_그대로_닫는다(
    _full_runtime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """가짜 검증자가 아니라 «실제 원문 도장»을 깨뜨려도 닫히는지 본다."""

    report, official_evidence = _real_report_and_evidence(monkeypatch, _full_runtime)
    honest = supplementary_research_source_verifier()
    registry = tuple(report.citations)
    target = next(
        source
        for source in registry
        if honest(
            source, registry, reference_date=report.as_of_date, evidence_text=""
        )
        is not None
    )
    assert target.provenance_seal, "도장이 없으면 이 시험이 아무것도 증명하지 못합니다"

    broken = replace(target, provenance_seal="a" * 64)
    tampered = replace(
        report,
        citations=[broken if source is target else source for source in registry],
    )

    decision = assess_supplementary_research_release(
        tampered, official_evidence=official_evidence, source_verifier=honest
    )

    assert decision.allowed is False
    assert decision.code == SUPPLEMENTARY_RELEASE_INVALID_CITATION_REGISTRY
