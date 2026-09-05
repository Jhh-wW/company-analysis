"""공식 근거 수집기가 실제 ``RealPipeline`` 경계에 묶였는지 검증한다.

단위 계약만 초록불이어도 운영 배선이 옛 수집·캐시 순서를 타면 아무 소용이
없다. 이 시험은 가짜 DART 엔진과 메모리 collector만 사용해 FULL 런타임의
수집→사전검사→snapshot 캐시 신원→typed 숫자 인용 전달 순서를 고정한다.
진짜 AI·네트워크·파일 수집은 한 번도 열지 않는다.
"""

from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import pytest

from src.core import deployment_identity
from src.features.business_candidate.dart_identity import DartCompanyRecord
from src.features.company_comparison import (
    ComparisonSourceConfigurationError,
    ComparisonSourceInternalError,
    ComparisonSourceTransientError,
)
from src.features.company_comparison.official_sources import (
    dart_profile_attestation_material,
)
from src.features.company_comparison.v2_bridge import (
    attach_comparison_program_evidence,
)
from src.features.pipeline import real
from src.features.pipeline.evidence_transport import (
    RAW_EVIDENCE_COMPANY_ID_KEY,
    RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY,
    RAW_EVIDENCE_SECTION_IDS_KEY,
    RAW_EVIDENCE_SLOT_IDS_KEY,
)
from src.features.pipeline.official_evidence_transport_adapter import (
    merge_official_evidence_fragments,
)
from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
from src.features.pipeline.tests.test_real_cache import (
    CORP_ID,
    JOB,
    POSTING,
    FakeEngine,
)
from src.shared import engine_build_identity as build_identity_contract
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID,
    FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED,
    FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT,
)
from src.shared.report_evidence.constants import (
    CollectionState,
    EvidenceReadiness,
    ReleaseMode,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates,
    CollectedEvidenceDocument,
    CollectionAttempt,
    DocumentTextRange,
    EvidenceFragment,
)
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
)
from src.shared.report_evidence.runtime_port import (
    OfficialEvidenceCollectionRequest,
    OfficialEvidenceCollectionResult,
    UnclassifiedEvidenceObservation,
)
from src.shared.report_source_identity import ReportSourceIdentity


_DEPLOYMENT_COMMIT = "a" * 40


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _freeze_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: real.engine_mode.EngineMode,
    release_mode: ReleaseMode | None,
) -> None:
    """각 시험이 환경을 추측하지 않고 process 두 신원을 먼저 확정한다."""

    real.engine_mode._reset_process_engine_mode_for_tests()  # noqa: SLF001
    build_identity_contract._reset_process_engine_build_identity_for_tests()  # noqa: SLF001
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", _DEPLOYMENT_COMMIT)
    if mode is real.engine_mode.EngineMode.V2:
        monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)
    else:
        monkeypatch.delenv(real.ENGINE_V2_ENV_NAME, raising=False)
    if release_mode is None:
        monkeypatch.delenv(real.REPORT_RELEASE_MODE_ENV_NAME, raising=False)
    else:
        monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, release_mode.value)

    assert real.engine_mode.freeze_process_engine_mode() is mode
    frozen_build = build_identity_contract.freeze_process_engine_build_identity()
    assert frozen_build.deployment_revision == _DEPLOYMENT_COMMIT
    assert frozen_build.cache_usable is True


def _request() -> tuple[UserInput, CompanyCard]:
    return (
        UserInput(
            company="가나다전자",
            job=JOB,
            region="서울 강남구",
            posting_text=POSTING,
        ),
        CompanyCard(
            legal_name="가나다전자",
            typed_name="가나다전자",
            address="서울특별시 강남구 테헤란로 1",
            ceo="홍길동",
            founded="20000101",
            ref=CORP_ID,
        ),
    )


def _official_result(
    *,
    document_count: int = 9,
    first_state: CollectionState | None = None,
    variant: str = "기본",
    unclassified_evidence: UnclassifiedEvidenceObservation | None = None,
    profile: dict[str, Any] | None = None,
) -> OfficialEvidenceCollectionResult:
    """아홉 장을 채운 정식 수집 결과 또는 한 장 실패 결과를 만든다."""

    texts = {
        section_id: (
            f"{variant} {section_id} 장의 공식 회사 사실과 구체적인 사업 근거입니다."
        )
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
    }
    all_hashes = tuple(_sha(text) for text in texts.values())
    attestation_profile = profile or {
        "status": "000",
        "corp_code": CORP_ID,
        "corp_name": "가나다전자",
        "hm_url": "https://example.com",
    }
    attestation_source_id, attestation_evidence = (
        dart_profile_attestation_material(
            profile=attestation_profile,
            corp_code=CORP_ID,
            company_name="가나다전자",
        )
    )
    assert attestation_source_id and attestation_evidence
    candidates: list[ChapterEvidenceCandidates] = []
    for index, section_id in enumerate(REQUIRED_EVIDENCE_SECTION_IDS):
        slots = collector_slots_for(section_id)
        if index == 0 and first_state is not None:
            candidates.append(
                ChapterEvidenceCandidates(
                    company_id=CORP_ID,
                    section_id=section_id,
                    documents=(),
                    fragments=(),
                    attempts=(
                        CollectionAttempt(
                            company_id=CORP_ID,
                            attempt_id=f"attempt-{variant}-first",
                            source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
                            requirement=SourceRequirement.REQUIRED,
                            state=first_state,
                            slot_ids=slots,
                            reason_code="fixture_state",
                        ),
                    ),
                    candidate_readiness=(
                        EvidenceReadiness.UNKNOWN
                        if first_state is CollectionState.FAILED
                        else EvidenceReadiness.INSUFFICIENT
                    ),
                    reason_codes=(),
                    estimated_tokens=0,
                    max_chars=10_000,
                    max_estimated_tokens=10_000,
                )
            )
            continue

        document_index = index % document_count
        document_id = f"document-{variant}-{document_index}"
        document = CollectedEvidenceDocument(
            company_id=CORP_ID,
            document_id=document_id,
            canonical_url=f"https://example.com/{variant}/{document_index}",
            source_tier=SourceTier.TIER_1_OFFICIAL,
            source_kind=SOURCE_KIND_OFFICIAL_WEB_PAGE,
            publisher="가나다전자",
            title=f"{variant} 공식 자료 {document_index}",
            published_on="2026-09-04",
            collected_at="2026-09-04",
            content_sha256=_sha(f"{variant} document body {document_index}"),
            exact_evidence_hashes=all_hashes,
            identity_binding="dart_profile_homepage",
            domain_attestation_source_id=attestation_source_id,
            domain_attestation_evidence=attestation_evidence,
            usable_ranges=(DocumentTextRange(0, 10),),
            collector_version="runtime-fixture-v1",
            parser_version="runtime-fixture-v1",
            requirement=SourceRequirement.REQUIRED,
        )
        text = texts[section_id]
        fragment = EvidenceFragment(
            company_id=CORP_ID,
            fragment_id=f"fragment-{variant}-{section_id}",
            document_id=document_id,
            location=f"section:{section_id}",
            text_sha256=_sha(text),
            text=text,
            section_id=section_id,
            slot_id=slots[0],
            covered_slot_ids=slots,
            score_millis=900,
            reason_codes=("fixture_match",),
        )
        candidates.append(
            ChapterEvidenceCandidates(
                company_id=CORP_ID,
                section_id=section_id,
                documents=(document,),
                fragments=(fragment,),
                attempts=(),
                candidate_readiness=EvidenceReadiness.READY,
                reason_codes=(),
                estimated_tokens=50,
                max_chars=10_000,
                max_estimated_tokens=10_000,
            )
        )
    return OfficialEvidenceCollectionResult(
        company_id=CORP_ID,
        candidates=tuple(candidates),
        unclassified_evidence=unclassified_evidence,
    )


def _official_result_with_dart_comparison_sentence(
    *,
    profile: dict[str, Any] | None = None,
) -> OfficialEvidenceCollectionResult:
    """아홉 장 formal 계약 중 9장 자사 문맥을 실제 DART 문장으로 바꾼다."""

    base = _official_result(profile=profile)
    text = (
        "가나다전자는 베타전자와 경쟁 관계인 반도체 검사 장비 전문기업이다. "
        "반도체 제조 고객 대상 검사 장비 시장에 제품을 공급한다."
    )
    digest = _sha(text)
    receipt = "20260315000123"
    document_id = f"{SOURCE_KIND_DART_BUSINESS_REPORT}:{receipt}"
    document = CollectedEvidenceDocument(
        company_id=CORP_ID,
        document_id=document_id,
        canonical_url=(
            "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt
        ),
        source_tier=SourceTier.TIER_1_OFFICIAL,
        source_kind=SOURCE_KIND_DART_BUSINESS_REPORT,
        publisher="금융감독원 전자공시시스템",
        title="사업보고서 (2025.12)",
        published_on="2026-03-15",
        collected_at="2026-09-04",
        content_sha256=digest,
        exact_evidence_hashes=(digest,),
        identity_binding=(
            f"corp_code={CORP_ID};rcept_no={receipt};identity_check=verified"
        ),
        usable_ranges=(DocumentTextRange(0, len(text)),),
        collector_version="runtime-fixture-v1",
        parser_version="runtime-fixture-v1",
        requirement=SourceRequirement.REQUIRED,
    )
    slot = "competitive_position:self_context"
    fragment = EvidenceFragment(
        company_id=CORP_ID,
        fragment_id=f"{document_id}:competitive",
        document_id=document_id,
        location="사업의 내용/경쟁 현황",
        text_sha256=digest,
        text=text,
        section_id="competitive_position",
        slot_id=slot,
        covered_slot_ids=(slot,),
        score_millis=950,
        reason_codes=("fixture_explicit_competition",),
    )
    candidates = tuple(
        replace(
            candidate,
            documents=(document,),
            fragments=(fragment,),
            estimated_tokens=50,
        )
        if candidate.section_id == "competitive_position"
        else candidate
        for candidate in base.candidates
    )
    return OfficialEvidenceCollectionResult(
        company_id=CORP_ID,
        candidates=candidates,
    )


def _comparison_profile(
    get_json: Any,
    counter: Any,
) -> dict[str, Any]:
    """한 profile snapshot으로 formal 문서 proof와 비교 입력을 함께 만든다.

    기존 fixture는 ``hm_url``이 빈 실제 profile을 비교 함수에 넘기면서,
    공식 문서는 별도의 ``https://example.com`` profile로 수동 인증했다.
    실서비스 수집 요청에서는 만들 수 없는 조합이므로 이 helper가 만든 같은
    snapshot을 생산 attestation helper와 비교 transport 양쪽에 사용한다.
    """

    profile = dict(get_json("company.json", {"corp_code": CORP_ID}, counter))
    profile["hm_url"] = "https://example.com"
    return profile


@dataclass
class _Collector:
    results: list[object]
    requests: list[OfficialEvidenceCollectionRequest] = field(default_factory=list)

    def collect(self, request: OfficialEvidenceCollectionRequest) -> object:
        self.requests.append(request)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


@dataclass
class _RuntimeCalls:
    coordinates: list[dict[str, Any]] = field(default_factory=list)
    cache_lookups: list[dict[str, Any]] = field(default_factory=list)
    legacy_collects: list[dict[str, Any]] = field(default_factory=list)
    composers: list[dict[str, Any]] = field(default_factory=list)
    comparisons: list[dict[str, Any]] = field(default_factory=list)
    paid_phase_count: int = 0


def _wire_runtime(
    monkeypatch: pytest.MonkeyPatch,
    *,
    engine: FakeEngine,
    legacy_fragments: dict[int, dict[str, object]] | None = None,
) -> _RuntimeCalls:
    """외부 효과를 모두 기록 가능한 메모리 경계로 바꾼다."""

    calls = _RuntimeCalls()
    monkeypatch.setattr(real, "_engine", lambda: engine)
    monkeypatch.setattr(real.generation_coordination, "is_active", lambda: False)

    def coordinate(**kwargs: Any) -> None:
        calls.coordinates.append(kwargs)
        return None

    def cache_lookup(**kwargs: Any) -> None:
        calls.cache_lookups.append(kwargs)
        return None

    def collect(*_args: Any, **kwargs: Any):
        calls.legacy_collects.append(kwargs)
        fragments = (
            {
                1: {
                    "종류": "사업내용",
                    "원문": "legacy DART에서 확인한 별도의 회사 사업 사실입니다.",
                }
            }
            if legacy_fragments is None
            else legacy_fragments
        )
        return dict(fragments), [], "legacy filing text"

    def compose(**kwargs: Any) -> RunResult:
        calls.composers.append(kwargs)
        return RunResult(outcome=Outcome.REPORT, message="가짜 composer 완료")

    comparison_result = object()

    def prepare_comparison(**kwargs: Any) -> object:
        # 이 파일은 formal collector→cache→composer 배선 시험이다. 공식 양사
        # 생산기 자체는 company_comparison의 fake DART 종단시험이 검증하므로,
        # 여기서는 의존성을 명시 주입해 기존 시험 목적을 바꾸지 않는다.
        calls.comparisons.append(kwargs)
        return comparison_result

    def ensure_paid_phase() -> None:
        calls.paid_phase_count += 1

    monkeypatch.setattr(real.generation_coordination, "coordinate", coordinate)
    monkeypatch.setattr(real, "_v2_cache_lookup", cache_lookup)
    monkeypatch.setattr(
        real,
        "_company_cache_lookup",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(real, "_collect", collect)
    monkeypatch.setattr(real, "_run_v2_composer", compose)
    monkeypatch.setattr(
        real,
        "_prepare_v2_comparison_result",
        prepare_comparison,
    )
    monkeypatch.setattr(
        real,
        "_comparison_generation_digest",
        lambda source_identity_digest, _comparison: source_identity_digest,
    )
    monkeypatch.setattr(
        real.generation_coordination,
        "ensure_paid_phase",
        ensure_paid_phase,
    )
    return calls


def _run(collector: _Collector | None) -> RunResult:
    user_input, card = _request()
    return real.RealPipeline(official_evidence_collector=collector).run(
        user_input,
        card,
    )


def test_FULL은_주입된_formal_collector를_캐시보다_먼저_부르고_typed원문을_composer까지_보낸다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    official = _official_result()
    collector = _Collector([official])
    calls = _wire_runtime(monkeypatch, engine=engine)

    result = _run(collector)

    assert result.outcome is Outcome.REPORT
    assert engine.posting_ai_calls == 0
    assert engine.generate_ai_calls == 0
    assert len(collector.requests) == 1
    request = collector.requests[0]
    assert request.company_id == CORP_ID
    assert request.company_name == "가나다전자"
    assert request.company_aliases == ()
    assert request.root_homepage_url == ""
    assert request.company_registration_numbers == ("1234567890",)
    assert request.official_candidate_urls == ()
    assert callable(request.dart_get_json)
    assert callable(request.dart_download_document)

    assert len(calls.coordinates) == 1
    assert len(calls.cache_lookups) == 1
    assert len(calls.legacy_collects) == 1
    assert calls.legacy_collects[0]["generation_mode"] is None
    assert calls.legacy_collects[0]["formal_official_evidence"] is official
    assert len(calls.composers) == 1
    assert calls.paid_phase_count == 0

    financials, _years = engine.fetch_financials(CORP_ID, engine.UsageCounter())
    filing = engine.latest_report_rcept(
        CORP_ID,
        "상장기업",
        engine.UsageCounter(),
    )
    expected_digest = ReportSourceIdentity.capture(
        filing=filing,
        financial_payload=financials,
    ).cache_digest_with_official_snapshot(official.source_snapshot_sha256)
    assert expected_digest
    assert calls.coordinates[0]["preflight_identity_digest"] == expected_digest
    assert calls.cache_lookups[0]["source_identity_digest"] == expected_digest
    assert calls.composers[0]["source_identity_digest"] == expected_digest

    composer_fragments = calls.composers[0]["frags"]
    typed = [
        raw
        for raw in composer_fragments.values()
        if RAW_EVIDENCE_SECTION_IDS_KEY in raw
    ]
    assert len(typed) == len(REQUIRED_EVIDENCE_SECTION_IDS)
    assert {raw["종류"] for raw in typed} == {SOURCE_KIND_OFFICIAL_WEB_PAGE}
    assert {raw[RAW_EVIDENCE_COMPANY_ID_KEY] for raw in typed} == {CORP_ID}
    assert {
        section_id
        for raw in typed
        for section_id in raw[RAW_EVIDENCE_SECTION_IDS_KEY]
    } == set(REQUIRED_EVIDENCE_SECTION_IDS)
    assert all(raw[RAW_EVIDENCE_SLOT_IDS_KEY] for raw in typed)
    assert all(raw[RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY] for raw in typed)


def test_FULL은_같은_company_profile을_한번만_읽고_등록번호와_IR후보를_수집기로_보낸다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """공식 웹을 보강하려고 company.json을 다시 읽는 회귀를 막는다.

    회사 확인에 이미 사용한 한 snapshot에서 사업자·법인등록번호와 DART
    ``ir_url``을 함께 운반해야 한다. 재조회하면 호출 비용뿐 아니라 서로 다른
    시점의 profile을 한 보고서에 섞을 수 있다.
    """

    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    original_get_json = engine.get_json
    company_profile_calls = 0

    def get_json(endpoint: str, params: dict[str, Any], counter: Any):
        nonlocal company_profile_calls
        payload = original_get_json(endpoint, params, counter)
        if endpoint == "company.json":
            company_profile_calls += 1
            payload = dict(payload)
            payload.update(
                {
                    "bizr_no": "123-45-67890",
                    "jurir_no": "110111-1234567",
                    "ir_url": " https://ir.ganada.example/company ",
                }
            )
        return payload

    monkeypatch.setattr(engine, "get_json", get_json)
    collector = _Collector([_official_result()])
    _wire_runtime(monkeypatch, engine=engine)

    result = _run(collector)

    assert result.outcome is Outcome.REPORT
    assert company_profile_calls == 1
    assert len(collector.requests) == 1
    request = collector.requests[0]
    assert request.company_registration_numbers == (
        "1234567890",
        "1101111234567",
    )
    assert request.official_candidate_urls == (
        "https://ir.ganada.example/company",
    )


def test_FULL_formal수집_뒤에는_legacy홈페이지와_IR을_다시_열거나_재삽입하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    official = _official_result()

    def forbidden_legacy_collect(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("FULL 정식 수집 뒤 legacy 공식 웹 수집기를 다시 불렀습니다")

    monkeypatch.setattr(
        real,
        "collect_homepage_fragments",
        forbidden_legacy_collect,
    )
    monkeypatch.setattr(
        real,
        "collect_official_ir_fragments",
        forbidden_legacy_collect,
    )
    monkeypatch.setattr(
        real,
        "_typed_dart_collection_enabled",
        lambda _generation_mode: True,
    )
    monkeypatch.setattr(
        real,
        "_collect_typed_dart",
        forbidden_legacy_collect,
    )
    counter = engine.UsageCounter()
    financials, years = engine.fetch_financials(CORP_ID, counter)
    steps: list[dict[str, Any]] = []
    user_input, _card = _request()

    fragments, tables, filing_text = real._collect(
        engine,
        engine._client(),
        {
            "status": "000",
            "corp_code": CORP_ID,
            "corp_name": "가나다전자",
            "hm_url": "https://www.ganada.example",
        },
        user_input,
        counter,
        steps,
        financials=financials,
        fin_years=years,
        filing=None,
        generation_mode=real.engine_mode.EngineMode.V2,
        corp_code=CORP_ID,
        formal_official_evidence=official,
    )

    # typed 조각의 유일한 삽입 경계는 호출자 쪽 merge다. `_collect` 안에서는
    # 출처 현황만 남기고 같은 원문을 legacy 숫자 조각으로 복제하지 않는다.
    assert not any(
        RAW_EVIDENCE_SECTION_IDS_KEY in fragment
        for fragment in fragments.values()
    )
    assert tables == []
    assert filing_text == ""
    homepage_step = next(
        step for step in steps if step.get("step") == "6_수집_홈페이지"
    )
    assert homepage_step == {
        "step": "6_수집_홈페이지",
        "조각수": len(REQUIRED_EVIDENCE_SECTION_IDS),
        "후보범위완전": True,
    }
    assert next(step for step in steps if step.get("step") == "6_수집_공식IR") == {
        "step": "6_수집_공식IR",
        "없음": "정식 공식 IR 수집에서 사용할 근거를 찾지 못했습니다",
        "문서시도": 0,
        "PDF바이트": 0,
        "후보범위완전": False,
    }


def test_DART_식별번호는_ASCII_숫자만_허용한다() -> None:
    assert real._RCEPT_NO_RE.fullmatch("20260315000123") is not None
    assert real._RCEPT_NO_RE.fullmatch("２０２６０３１５０００１２３") is None
    assert real._official_company_registration_numbers(
        {
            "bizr_no": "123-45-67890",
            "jurir_no": "110111-1234567",
        }
    ) == ("1234567890", "1101111234567")
    assert real._official_company_registration_numbers(
        {
            "bizr_no": "123A45-67890",
            "jurir_no": "１１０１１１-１２３４５６７",
        }
    ) == ()


def test_packet_문서hash_충돌은_회사자료부족이_아닌_내부계약사유다() -> None:
    assert real._packet_document_preflight_final_gate_reason(
        FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID
    ) == FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT


def test_같은_DART와_재무라도_공식_snapshot이_바뀌면_coordinate와_cache_신원이_바뀐다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    first = _official_result(variant="첫자료")
    second = _official_result(variant="바뀐자료")
    collector = _Collector([first, second])
    calls = _wire_runtime(monkeypatch, engine=engine)

    first_run = _run(collector)
    second_run = _run(collector)

    assert first_run.outcome is Outcome.REPORT
    assert second_run.outcome is Outcome.REPORT
    assert first.source_snapshot_sha256 != second.source_snapshot_sha256
    coordinate_digests = [
        item["preflight_identity_digest"] for item in calls.coordinates
    ]
    cache_digests = [item["source_identity_digest"] for item in calls.cache_lookups]
    assert len(set(coordinate_digests)) == 2
    assert cache_digests == coordinate_digests
    assert [item["source_identity_digest"] for item in calls.composers] == (
        coordinate_digests
    )
    assert engine.posting_ai_calls == 0
    assert engine.generate_ai_calls == 0
    assert calls.paid_phase_count == 0


@pytest.mark.parametrize(
    ("official", "expected_reason"),
    (
        pytest.param(
            lambda: _official_result(first_state=CollectionState.FAILED),
            FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT,
            id="필수경로-실패",
        ),
    ),
)
def test_공식근거_부족과_일시장애는_서로_다른_사유로_AI전에_멈춘다(
    monkeypatch: pytest.MonkeyPatch,
    official: Any,
    expected_reason: str,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([official()])
    calls = _wire_runtime(monkeypatch, engine=engine)

    result = _run(collector)

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == expected_reason
    assert len(collector.requests) == 1
    assert calls.coordinates == []
    assert calls.cache_lookups == []
    assert calls.legacy_collects == []
    assert calls.composers == []
    assert calls.paid_phase_count == 0
    assert engine.posting_ai_calls == 0
    assert engine.generate_ai_calls == 0


def test_무분류_원문이_남은_부족은_실제자료부족으로_단정하지_않고_AI전에_멈춘다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    official = _official_result(
        first_state=CollectionState.MISSING,
        unclassified_evidence=UnclassifiedEvidenceObservation(
            company_id=CORP_ID,
            document_count=1,
            fragment_count=2,
            observation_sha256="b" * 64,
        ),
    )
    collector = _Collector([official])
    calls = _wire_runtime(monkeypatch, engine=engine)

    result = _run(collector)

    assert result.outcome is Outcome.GATE_STOPPED
    assert (
        result.final_gate_reason
        == FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED
    )
    assert "공식 자료는 읽었지만" in result.message
    assert result.sources[0].state == "failed"
    assert "자동으로 확인하지 못했습니다" in result.sources[0].detail
    assert calls.coordinates == []
    assert calls.cache_lookups == []
    assert calls.legacy_collects == []
    assert calls.composers == []
    assert calls.paid_phase_count == 0
    assert engine.posting_ai_calls == 0
    assert engine.generate_ai_calls == 0


def test_formal_문서수만으로는_뒤에_합쳐질_구조화근거를_과소평가하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([_official_result(document_count=1)])
    calls = _wire_runtime(monkeypatch, engine=engine)

    result = _run(collector)

    # 이 시험의 composer는 기록용 대역이다. 실제 합쳐진 packet의 문서 하한은
    # `_run_v2_composer` 안과 official_evidence_preflight 단위시험이 검증한다.
    assert result.outcome is Outcome.REPORT
    assert len(calls.coordinates) == 1
    assert len(calls.legacy_collects) == 1
    assert len(calls.composers) == 1


@pytest.mark.parametrize(
    "malformed",
    (
        pytest.param(object(), id="잘못된-결과형"),
        pytest.param(RuntimeError("원문을 싣지 않는 가짜 수집기 오류"), id="수집기-예외"),
        pytest.param(
            ValueError("알 수 없는 DART fetch 결과 상태"),
            id="수집기-state-계약오류",
        ),
        pytest.param(
            TypeError("download_document callback 시그니처 불일치"),
            id="callback-시그니처-불일치",
        ),
    ),
)
def test_collector_결과가_깨졌거나_예외면_자료탓이_아닌_내부계약오류로_멈춘다(
    monkeypatch: pytest.MonkeyPatch,
    malformed: object,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([malformed])
    calls = _wire_runtime(monkeypatch, engine=engine)

    result = _run(collector)

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
    assert len(collector.requests) == 1
    assert calls.coordinates == []
    assert calls.cache_lookups == []
    assert calls.legacy_collects == []
    assert calls.composers == []
    assert calls.paid_phase_count == 0
    assert engine.posting_ai_calls == 0
    assert engine.generate_ai_calls == 0


def test_FULL에_formal_collector_주입이_빠지면_legacy로_강등하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    calls = _wire_runtime(monkeypatch, engine=engine)

    result = _run(None)

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
    assert calls.coordinates == []
    assert calls.cache_lookups == []
    assert calls.legacy_collects == []
    assert calls.composers == []
    assert calls.paid_phase_count == 0
    assert engine.posting_ai_calls == 0
    assert engine.generate_ai_calls == 0


@pytest.mark.parametrize(
    ("mode", "release_mode"),
    (
        pytest.param(
            real.engine_mode.EngineMode.V2,
            ReleaseMode.SHADOW,
            id="v2-shadow",
        ),
        pytest.param(real.engine_mode.EngineMode.V1, ReleaseMode.FULL, id="v1-full-env"),
    ),
)
def test_SHADOW와_v1은_collector가_주입돼도_formal_수집을_열지_않는다(
    monkeypatch: pytest.MonkeyPatch,
    mode: real.engine_mode.EngineMode,
    release_mode: ReleaseMode,
) -> None:
    _freeze_runtime(monkeypatch, mode=mode, release_mode=release_mode)
    engine = FakeEngine()
    collector = _Collector([AssertionError("이 모드에서는 collector를 부르면 안 됩니다")])
    calls = _wire_runtime(monkeypatch, engine=engine, legacy_fragments={})

    result = _run(collector)

    assert collector.requests == []
    assert len(calls.coordinates) == 1
    assert len(calls.legacy_collects) == 1
    assert calls.legacy_collects[0]["formal_official_evidence"] is None
    assert result.outcome is Outcome.GATE_STOPPED
    assert engine.posting_ai_calls == 0
    assert engine.generate_ai_calls == 0


@pytest.mark.parametrize(
    ("dart_error_name", "expected_reason"),
    (
        (
            "DartAuthenticationError",
            FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION,
        ),
        ("DartLimitReached", FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT),
        ("DartTransportError", FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT),
        ("DartResponseError", FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT),
    ),
)
def test_정식공식수집_DART장애도_닫힌사유만_남기고_AI전에_멈춘다(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    dart_error_name: str,
    expected_reason: str,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    real._engine()  # noqa: SLF001 - 실제 예외 자료형만 적재, 네트워크 0회
    dart_client = sys.modules["core.dart_client"]
    secret = "https://secret.example/?api-key=정식수집기록금지"
    collector = _Collector([getattr(dart_client, dart_error_name)(secret)])
    engine = FakeEngine()
    calls = _wire_runtime(monkeypatch, engine=engine)

    result = _run(collector)

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == expected_reason
    assert secret not in result.message
    assert all(secret not in source.detail for source in result.sources)
    assert secret not in caplog.text
    assert len(collector.requests) == 1
    assert calls.coordinates == []
    assert calls.composers == []
    assert calls.paid_phase_count == 0
    assert engine.posting_ai_calls == 0
    assert engine.generate_ai_calls == 0


@pytest.mark.parametrize(
    ("dart_error_name", "expected_reason"),
    (
        ("DartLimitReached", FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT),
        (
            "DartAuthenticationError",
            FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION,
        ),
        ("DartTransportError", FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT),
        ("DartResponseError", FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT),
    ),
)
def test_공식양사비교_DART운영예외는_자료부족이나_내부오류로_접지_않는다(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    dart_error_name: str,
    expected_reason: str,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    # 실제 production DART 예외 계보를 로드하되 API·HTTP는 호출하지 않는다.
    real._engine()  # noqa: SLF001
    dart_client = sys.modules["core.dart_client"]
    secret = "https://secret.example/?api-key=절대기록금지"
    failure = getattr(dart_client, dart_error_name)(secret)
    engine = FakeEngine()
    collector = _Collector([_official_result()])
    calls = _wire_runtime(monkeypatch, engine=engine)

    def fail_comparison(**_kwargs: Any) -> object:
        raise failure

    monkeypatch.setattr(real, "_prepare_v2_comparison_result", fail_comparison)

    result = _run(collector)

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == expected_reason
    assert secret not in result.message
    assert all(secret not in source.detail for source in result.sources)
    assert secret not in caplog.text
    assert calls.coordinates == []
    assert calls.cache_lookups == []
    assert calls.composers == []
    assert calls.paid_phase_count == 0
    assert engine.posting_ai_calls == 0
    assert engine.generate_ai_calls == 0


def test_공식양사비교_내부계약오류는_DART일시장애로_위장하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([_official_result()])
    calls = _wire_runtime(monkeypatch, engine=engine)

    def fail_comparison(**_kwargs: Any) -> object:
        raise ValueError("깨진 bridge 계약")

    monkeypatch.setattr(real, "_prepare_v2_comparison_result", fail_comparison)

    result = _run(collector)

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.final_gate_reason == FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
    assert calls.coordinates == []
    assert calls.cache_lookups == []
    assert calls.composers == []
    assert calls.paid_phase_count == 0
    assert engine.posting_ai_calls == 0
    assert engine.generate_ai_calls == 0


def test_formal_DART문장은_실제_양사생산기와_typed_packet_bridge까지_이어진다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = FakeEngine()
    counter = engine.UsageCounter()
    profile = _comparison_profile(engine.get_json, counter)
    official = _official_result_with_dart_comparison_sentence(profile=profile)
    financials, _years = engine.fetch_financials(
        CORP_ID, counter, business_date=date(2026, 9, 4)
    )
    filing = engine.latest_report_rcept(
        CORP_ID,
        "상장사",
        counter,
        business_date=date(2026, 9, 4),
    )
    monkeypatch.setattr(
        real,
        "_company_catalog",
        lambda: (
            DartCompanyRecord(CORP_ID, "가나다전자"),
            DartCompanyRecord("00999999", "베타전자", stock_code="999999"),
        ),
    )

    comparison = real._prepare_v2_comparison_result(  # noqa: SLF001
        engine=engine,
        counter=counter,
        profile=profile,
        official_evidence=official,
        corp_code=CORP_ID,
        company_name="가나다전자",
        corp_type="상장사",
        financials=financials,
        filing=filing,
        business_date=date(2026, 9, 4),
    )
    raw, _added = merge_official_evidence_fragments({}, official)
    packets = real._full_section_evidence_packets(  # noqa: SLF001
        corp_id=CORP_ID,
        source_identity_digest=official.source_snapshot_sha256,
        frags=raw,
        filing_meta=None,
    )
    bridged = attach_comparison_program_evidence(packets, comparison)

    assert comparison.facts
    assert {source.publisher for source in comparison.sources} >= {
        "가나다전자",
        "베타전자",
    }
    assert comparison.candidates[0].document_content_sha256 == _sha(
        next(
            fragment.text
            for candidate in official.candidates
            if candidate.section_id == "competitive_position"
            for fragment in candidate.fragments
        )
    )
    competitive = next(
        packet
        for packet in bridged.packets
        if packet.section_id == "competitive_position"
    )
    assert competitive.program_evidence is not None
    program = competitive.program_evidence
    direct_source_ids = {
        fragment.bound_source.source_id for fragment in program.source_fragments
    }
    direct_attester_ids = {
        fragment.bound_source.domain_attestation_source_id
        for fragment in program.source_fragments
        if fragment.bound_source.domain_attestation_source_id
    }
    from src.shared.report_quality.comparison_basis import (
        comparison_basis_attester_source_ids,
    )

    # DART 후보는 규제기관 문서라 도메인 attester를 Source에 달지 않는다.
    # 다만 닫힌 comparison_basis가 직접 지목한 자사 기업개황 증명은 프로그램의
    # 의미 의존성이므로, 생산 helper가 계산한 exact closure에는 포함돼야 한다.
    basis_attester_ids = comparison_basis_attester_source_ids(program.facts)
    assert basis_attester_ids
    assert {source.source_id for source in program.registry_sources} == (
        direct_source_ids | direct_attester_ids | basis_attester_ids
    )
    assert {
        fact.claim_slot for fact in program.facts
    } == {
        "competitive_position:comparison_target",
        "competitive_position:comparison_metric",
        "competitive_position:comparison_basis",
        "competitive_position:comparison_judgment",
        "competitive_position:limitation",
    }
    candidate_fragments = tuple(
        fragment
        for fragment in competitive.program_evidence.source_fragments
        if fragment.document_content_sha256
        == comparison.candidates[0].document_content_sha256
    )
    assert len(candidate_fragments) == 1
    assert candidate_fragments[0].bound_source.kind.value == "공시"
    assert not candidate_fragments[0].bound_source.domain_attestation_source_id

    from src.features.composer.port import VerifiedProgramEvidence

    without_basis_attester = tuple(
        source
        for source in program.registry_sources
        if source.source_id not in basis_attester_ids
    )
    with pytest.raises(ValueError, match="attester가 직접 참조 의존성과 다릅니다"):
        VerifiedProgramEvidence(
            section_id=program.section_id,
            source_fragments=program.source_fragments,
            registry_sources=without_basis_attester,
            facts=program.facts,
            sentences=program.sentences,
        )
    assert engine.posting_ai_calls == 0
    assert engine.generate_ai_calls == 0


@pytest.mark.parametrize(
    ("failure_name", "expected_error"),
    (
        ("DartTransportError", ComparisonSourceTransientError),
        ("DartAuthenticationError", ComparisonSourceConfigurationError),
        ("ValueError", ComparisonSourceInternalError),
    ),
)
def test_비교사_공급실패는_실제_후보loop에서_복구종류를_보존한다(
    monkeypatch: pytest.MonkeyPatch,
    failure_name: str,
    expected_error: type[BaseException],
) -> None:
    # 실제 production 예외 클래스만 로드한다. 외부 API·HTTP는 호출하지 않는다.
    real._engine()  # noqa: SLF001
    failure_type = (
        ValueError
        if failure_name == "ValueError"
        else getattr(sys.modules["core.dart_client"], failure_name)
    )
    engine = FakeEngine()
    original_get_json = engine.get_json

    def fail_comparator(
        endpoint: str, params: dict[str, Any], counter: Any
    ) -> dict[str, Any]:
        if endpoint == "company.json" and params.get("corp_code") == "00999999":
            raise failure_type("https://secret.example/?api-key=기록금지")
        return original_get_json(endpoint, params, counter)

    monkeypatch.setattr(engine, "get_json", fail_comparator)
    monkeypatch.setattr(
        real,
        "_company_catalog",
        lambda: (
            DartCompanyRecord(CORP_ID, "가나다전자"),
            DartCompanyRecord("00999999", "베타전자", stock_code="999999"),
        ),
    )
    counter = engine.UsageCounter()
    profile = _comparison_profile(original_get_json, counter)
    official = _official_result_with_dart_comparison_sentence(profile=profile)
    financials, _years = engine.fetch_financials(CORP_ID, counter)
    filing = engine.latest_report_rcept(CORP_ID, "상장사", counter)

    with pytest.raises(expected_error):
        real._prepare_v2_comparison_result(  # noqa: SLF001
            engine=engine,
            counter=counter,
            profile=profile,
            official_evidence=official,
            corp_code=CORP_ID,
            company_name="가나다전자",
            corp_type="상장사",
            financials=financials,
            filing=filing,
            business_date=date(2026, 9, 4),
        )
    assert engine.posting_ai_calls == 0
    assert engine.generate_ai_calls == 0


@pytest.mark.parametrize("profile_corp_code", ["", "00888888"])
def test_비교사_기업개황_법인번호가_없거나_다르면_내부계약오류로_닫는다(
    monkeypatch: pytest.MonkeyPatch,
    profile_corp_code: str,
) -> None:
    engine = FakeEngine()
    original_get_json = engine.get_json

    def mismatched_comparator_profile(
        endpoint: str, params: dict[str, Any], counter: Any
    ) -> dict[str, Any]:
        response = original_get_json(endpoint, params, counter)
        if endpoint == "company.json" and params.get("corp_code") == "00999999":
            response = dict(response)
            response["corp_code"] = profile_corp_code
        return response

    monkeypatch.setattr(engine, "get_json", mismatched_comparator_profile)
    monkeypatch.setattr(
        real,
        "_company_catalog",
        lambda: (
            DartCompanyRecord(CORP_ID, "가나다전자"),
            DartCompanyRecord("00999999", "베타전자", stock_code="999999"),
        ),
    )
    counter = engine.UsageCounter()
    profile = _comparison_profile(original_get_json, counter)
    official = _official_result_with_dart_comparison_sentence(profile=profile)
    financials, _years = engine.fetch_financials(CORP_ID, counter)
    filing = engine.latest_report_rcept(CORP_ID, "상장사", counter)

    with pytest.raises(ComparisonSourceInternalError):
        real._prepare_v2_comparison_result(  # noqa: SLF001
            engine=engine,
            counter=counter,
            profile=profile,
            official_evidence=official,
            corp_code=CORP_ID,
            company_name="가나다전자",
            corp_type="상장사",
            financials=financials,
            filing=filing,
            business_date=date(2026, 9, 4),
        )

    assert engine.posting_ai_calls == 0
    assert engine.generate_ai_calls == 0


class _AuditOnlyEngine(FakeEngine):
    """감사보고서만 내는 비상장사 흉내 — DART 재무 API가 세 해 모두 «자료 없음(013)».

    2026-09-05 인이지 실측: ``fetch_financials``가 ``(None, [])``를 돌려
    ``financial_payload_digest``가 비었고, 운영은 이를 «공식 자료 snapshot을
    결속하지 못했습니다»(내부 계약 실패)로 읽어 AI 작성 전에 멈췄다.
    """

    def fetch_financials(
        self, corp_code: str, counter: Any, *, business_date: Any = None
    ) -> tuple[None, list[int]]:
        return None, []

    def latest_report_rcept(
        self, corp_code: str, corp_type: str, counter: Any, *, business_date: Any = None
    ) -> dict[str, Any]:
        return {
            "report_nm": "감사보고서 (2025.12)",
            "rcept_no": "20260406001240",
            "rcept_dt": "20260406",
            "reprt_code": "11011",
        }


def test_FULL은_재무API_자료없음_비상장사도_snapshot결속_실패로_멈추지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재무 도장이 없는 것은 회사 자료의 실제 상태다. 접수번호+공식 snapshot으로 생성한다."""

    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = _AuditOnlyEngine()
    official = _official_result()
    collector = _Collector([official])
    calls = _wire_runtime(monkeypatch, engine=engine)

    result = _run(collector)

    assert result.outcome is Outcome.REPORT, result.message
    assert "snapshot을 결속하지 못했습니다" not in " ".join(
        getattr(source, "detail", "") or "" for source in (result.sources or ())
    )

    filing = engine.latest_report_rcept(CORP_ID, "비상장 외감", engine.UsageCounter())
    identity = ReportSourceIdentity.capture(filing=filing, financial_payload=None)
    assert identity.cache_usable is False
    assert identity.cache_digest_with_official_snapshot(official.source_snapshot_sha256) == ""
    expected_digest = identity.generation_digest_without_financials(
        official.source_snapshot_sha256
    )
    assert expected_digest
    assert calls.coordinates[0]["preflight_identity_digest"] == expected_digest
    assert calls.composers[0]["source_identity_digest"] == expected_digest

    steps = calls.composers[0].get("steps") or []
    assert any(
        step.get("step") == "6_수집_생성신원_재무자료없음" for step in steps
    ), [step.get("step") for step in steps]
