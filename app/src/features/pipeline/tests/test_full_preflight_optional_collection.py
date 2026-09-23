"""FULL 사전검사가 선택 경로 미완료를 출고 차단으로 세지 않는지 두 겹 모두 잠근다.

★ 왜 이 시험이 있나 (ADR 0004)
  2026-09-22 상장사 평가 3회는 선택(OPTIONAL) 경로의 쪽 수 상한 잘림 2건과
  IR 실패 1건만으로 전부 «웹 일시 장애» 부분 보고서로 전환됐다. 사전검사가
  요구 수준을 가리지 않고 FAILED·TRUNCATED를 모두 «미완료»로 셌고, real.py가
  같은 값을 출고 모드 선택에 다시 OR했다. 한 겹만 고치면 다른 겹이 FULL을 끈다.

이 파일이 지키는 것:
  1) 사전검사의 요구 수준×상태 판정표(출고 차단·관측·진단 기록)
  2) 실측 시도 분포를 그대로 옮긴 픽스처의 전환 사유
  3) real.py 출고 모드 선택의 실제 호출 인자(작성기·생성 조정)
  4) 관측 플래그의 뜻 유지 — 캐시 조회 생략·캐시 부적격·부분 지문
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from src.core import evidence_reclassify_switch, news_intake_switch
from src.features.observability import run_diagnostics
from src.features.pipeline import real
from src.features.pipeline.collection_recovery import partial_generation_digest
from src.features.pipeline.constants import COLLECTION_RECOVERY_STEP
from src.features.pipeline.official_collection_diagnostic_constants import (
    ALLOWED_OFFICIAL_COLLECTION_REASON_CODES,
    NONBLOCKING_REASON_OPTIONAL_PATH,
    NONBLOCKING_REASON_REQUIRED_DESIGN_CAP,
    OPTIONAL_INCOMPLETE_DIAGNOSTICS_STEP,
    UNKNOWN_OFFICIAL_COLLECTION_VALUE,
)
from src.features.pipeline.official_collection_diagnostics import (
    official_collection_attempt_step,
    optional_incomplete_attempt_step,
)
from src.features.pipeline.official_evidence_preflight import (
    DART_PARTIAL_REASON_INSUFFICIENT_WITH_READY_SECTIONS,
    DART_PARTIAL_REASON_REQUIRED_COLLECTION_INCOMPLETE,
    DART_PARTIAL_REASON_TOO_FEW_DOCUMENTS_FOR_FULL,
    DART_PARTIAL_REASON_TRANSIENT_WEB_FAILURE,
    NonblockingIncompleteAttempt,
    assess_official_evidence,
)
from src.features.pipeline.port import Outcome, RunResult
from src.features.pipeline.tests.test_official_evidence_preflight import (
    _COMPANY_ID,
    _result,
    _with_dart_evidence,
    _without_section_evidence,
)
from src.features.pipeline.tests.test_official_evidence_runtime import (
    _AuditOnlyEngine,
    _Collector,
    _freeze_runtime,
    _official_result,
    _run,
    _wire_runtime,
)
from src.features.pipeline.tests.test_real_cache import CORP_ID, FakeEngine
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PREFLIGHT_DOCUMENT_SOURCES_INSUFFICIENT,
    FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INCOMPLETE,
    FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT,
)
from src.shared.report_evidence.constants import (
    COLLECTION_CAP_TRUNCATION_REASON_CODES,
    CollectionState,
    EvidenceReadiness,
    GenerationGateStatus,
    ReleaseMode,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
    SOURCE_KIND_OFFICIAL_IR_PDF,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
    SOURCE_KIND_ROBOTS_TXT,
    SourceRequirement,
)
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates,
    CollectionAttempt,
)
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.source_kind_policy import (
    attempt_slots_for_formal_source_kind,
)
from src.shared.report_source_identity import ReportSourceIdentity


_PARTIAL_SWITCH_STEP = "6_수집_DART부분보고서전환"
_PREFLIGHT_STEP = "6_수집_공식근거사전검사"
_PARTIAL_IDENTITY_STEP = "6_수집_생성신원_부분자료"
_NO_FINANCIALS_IDENTITY_STEP = "6_수집_생성신원_재무자료없음"
_TARGET_STEP = "5_대상판정"
_WIDE_WEB_PAGE_CAP = "truncated_page_cap"


# ── 공통 조립 ────────────────────────────────────────────────────────────


def _broad_slots(source_kind: str) -> tuple[str, ...]:
    """그 조회 종류가 주장할 수 있는 칸 전체 — 광역 시도가 실제로 거는 범위다."""

    return tuple(sorted(attempt_slots_for_formal_source_kind(source_kind)))


def _attempt(
    attempt_id: str,
    *,
    source_kind: str,
    requirement: SourceRequirement,
    state: CollectionState,
    reason_code: str,
    slot_ids: tuple[str, ...] | None = None,
) -> CollectionAttempt:
    return CollectionAttempt(
        company_id=_COMPANY_ID,
        attempt_id=attempt_id,
        source_kind=source_kind,
        requirement=requirement,
        state=state,
        slot_ids=slot_ids or _broad_slots(source_kind),
        reason_code=reason_code,
    )


def _with_attempts(
    result: OfficialEvidenceCollectionResult,
    attempts: tuple[CollectionAttempt, ...],
) -> OfficialEvidenceCollectionResult:
    """생산부처럼 칸이 겹치는 모든 장에 같은 시도를 복제해 붙인다.

    수집 생산부(chapter_evidence)는 한 시도를 칸이 겹치는 장마다 같은 식별자로
    싣는다. 광역 시도 한 건이 아홉 장에 나타나는 운영 모양을 그대로 만든다.
    """

    candidates: list[ChapterEvidenceCandidates] = []
    for candidate in result.candidates:
        section_slots = set(collector_slots_for(candidate.section_id))
        overlapping = tuple(
            attempt for attempt in attempts if section_slots & set(attempt.slot_ids)
        )
        candidates.append(
            replace(candidate, attempts=(*candidate.attempts, *overlapping))
        )
    return OfficialEvidenceCollectionResult(
        company_id=result.company_id,
        candidates=tuple(candidates),
        unclassified_evidence=result.unclassified_evidence,
    )


def _ready_base() -> OfficialEvidenceCollectionResult:
    """아홉 장 READY·독립 문서 9건·DART 근거 1건 — 시도만 바꿔 끼울 바탕."""

    return _with_dart_evidence(_result())


# ── 1) 요구 수준 × 상태 판정표 ───────────────────────────────────────────


_WEB = SOURCE_KIND_OFFICIAL_WEB_PAGE
_DART = SOURCE_KIND_DART_BUSINESS_REPORT
_REQUIRED = SourceRequirement.REQUIRED
_OPTIONAL = SourceRequirement.OPTIONAL
_FAILED = CollectionState.FAILED
_TRUNCATED = CollectionState.TRUNCATED
_MISSING = CollectionState.MISSING


#: 판정표의 «비차단 진단 기록» 칸 — 어느 목록에 실리는지(없으면 빈 값).
_AS_OPTIONAL = "optional"
_AS_DESIGN_CAP = "design_cap"


@pytest.mark.parametrize(
    (
        "source_kind",
        "requirement",
        "state",
        "reason_code",
        "expected_reason",
        "recorded_as",
    ),
    (
        pytest.param(
            _WEB, _OPTIONAL, _TRUNCATED, _WIDE_WEB_PAGE_CAP, "", _AS_OPTIONAL,
            id="선택-웹-쪽수상한-잘림",
        ),
        pytest.param(
            _WEB, _OPTIONAL, _FAILED, "network_failed", "", _AS_OPTIONAL,
            id="선택-웹-실패",
        ),
        pytest.param(
            SOURCE_KIND_OFFICIAL_IR_PDF, _OPTIONAL, _FAILED, "ir_pdf_failed", "",
            _AS_OPTIONAL,
            id="선택-IR-실패",
        ),
        pytest.param(
            SOURCE_KIND_ROBOTS_TXT, _OPTIONAL, _FAILED, "robots_unreachable", "",
            _AS_OPTIONAL,
            id="선택-robots-실패",
        ),
        pytest.param(
            SOURCE_KIND_DART_SEMIANNUAL_REPORT, _OPTIONAL, _TRUNCATED,
            "deadline_exceeded", "", _AS_OPTIONAL,
            id="선택-DART반기-잘림",
        ),
        pytest.param(
            _WEB, _REQUIRED, _FAILED, "network_failed",
            DART_PARTIAL_REASON_TRANSIENT_WEB_FAILURE, "",
            id="필수-웹-실패",
        ),
        pytest.param(
            _WEB, _REQUIRED, _TRUNCATED, _WIDE_WEB_PAGE_CAP, "", _AS_DESIGN_CAP,
            id="필수-웹-쪽수상한-잘림",
        ),
        pytest.param(
            _WEB, _REQUIRED, _TRUNCATED, "truncated_client_redirect_cap",
            DART_PARTIAL_REASON_TRANSIENT_WEB_FAILURE, "",
            id="필수-웹-상한밖사유-잘림",
        ),
        pytest.param(
            _DART, _REQUIRED, _TRUNCATED, "deadline_exceeded",
            DART_PARTIAL_REASON_REQUIRED_COLLECTION_INCOMPLETE, "",
            id="필수-DART-잘림",
        ),
        pytest.param(
            _DART, _REQUIRED, _TRUNCATED, _WIDE_WEB_PAGE_CAP,
            DART_PARTIAL_REASON_REQUIRED_COLLECTION_INCOMPLETE, "",
            id="필수-DART-상한사유여도-잘림",
        ),
        pytest.param(
            _DART, _REQUIRED, _FAILED, "document_fetch_failed",
            DART_PARTIAL_REASON_REQUIRED_COLLECTION_INCOMPLETE, "",
            id="필수-DART-실패",
        ),
        pytest.param(
            _WEB, _REQUIRED, _MISSING, "page_missing_404", "", "",
            id="필수-웹-정상부재는-미완료가-아니다",
        ),
    ),
)
def test_요구수준과_상태별로_출고차단과_관측을_따로_판정한다(
    source_kind: str,
    requirement: SourceRequirement,
    state: CollectionState,
    reason_code: str,
    expected_reason: str,
    recorded_as: str,
) -> None:
    observed = _with_attempts(
        _ready_base(),
        (
            _attempt(
                "matrix-0001",
                source_kind=source_kind,
                requirement=requirement,
                state=state,
                reason_code=reason_code,
            ),
        ),
    )

    preflight = assess_official_evidence(observed)

    incomplete = state in {_FAILED, _TRUNCATED}
    blocking = bool(expected_reason)
    # 바탕은 아홉 장이 모두 채워져 있어 시도 한 건이 장 준비도를 바꾸지 않는다.
    assert preflight.decision.status is GenerationGateStatus.READY_FOR_GENERATION
    assert preflight.can_call_ai is True
    assert preflight.collection_incomplete is incomplete
    assert preflight.release_blocking_incomplete is blocking
    assert preflight.dart_partial_fallback is blocking
    assert preflight.dart_partial_reason == expected_reason
    assert preflight.detail_code == (
        FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INCOMPLETE if blocking else ""
    )
    # 불변식: 출고 차단은 관측의 부분집합이다.
    assert not preflight.release_blocking_incomplete or preflight.collection_incomplete
    # 출고를 막지 않은 미완료만, 비차단 근거에 맞는 목록 하나에 실린다.
    observation = (NonblockingIncompleteAttempt(source_kind, state, reason_code),)
    assert preflight.optional_incomplete_attempts == (
        observation if recorded_as == _AS_OPTIONAL else ()
    )
    assert preflight.design_cap_exempt_attempts == (
        observation if recorded_as == _AS_DESIGN_CAP else ()
    )


@pytest.mark.parametrize("reason_code", sorted(COLLECTION_CAP_TRUNCATION_REASON_CODES))
def test_DART가_아닌_필수경로의_설계상한_잘림은_출고를_막지_않는다(
    reason_code: str,
) -> None:
    observed = _with_attempts(
        _ready_base(),
        (
            _attempt(
                "cap-0001",
                source_kind=_WEB,
                requirement=_REQUIRED,
                state=_TRUNCATED,
                reason_code=reason_code,
                slot_ids=collector_slots_for("culture"),
            ),
        ),
    )

    preflight = assess_official_evidence(observed)

    assert preflight.collection_incomplete is True
    assert preflight.release_blocking_incomplete is False
    assert preflight.dart_partial_fallback is False
    # 선택 경로 목록은 이름 그대로 OPTIONAL만 싣고, 이 잘림은 설계 상한 예외
    # 목록에 따로 남는다 — 사후에 «왜 안 막았나»를 가려 읽기 위해서다.
    assert preflight.optional_incomplete_attempts == ()
    assert preflight.design_cap_exempt_attempts == (
        NonblockingIncompleteAttempt(_WEB, _TRUNCATED, reason_code),
    )


def test_선택경로_미완료만_있어도_준비장_부족과_문서하한_갈래는_그대로다() -> None:
    page_cap = _attempt(
        "truncation-0001",
        source_kind=_WEB,
        requirement=_OPTIONAL,
        state=_TRUNCATED,
        reason_code=_WIDE_WEB_PAGE_CAP,
    )
    insufficient = assess_official_evidence(
        _with_attempts(
            _without_section_evidence(
                _ready_base(),
                ("portfolio", "culture"),
                identity_mismatch_section_id="portfolio",
            ),
            (page_cap,),
        )
    )
    too_few_documents = assess_official_evidence(
        _with_attempts(_with_dart_evidence(_result(document_count=4)), (page_cap,))
    )

    assert insufficient.decision.insufficient_section_ids == ("portfolio", "culture")
    assert insufficient.release_blocking_incomplete is False
    assert insufficient.collection_incomplete is True
    assert insufficient.dart_partial_fallback is True
    assert (
        insufficient.dart_partial_reason
        == DART_PARTIAL_REASON_INSUFFICIENT_WITH_READY_SECTIONS
    )
    assert (
        insufficient.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT
    )

    assert too_few_documents.independent_document_count == 4
    assert too_few_documents.release_blocking_incomplete is False
    assert too_few_documents.dart_partial_fallback is True
    assert (
        too_few_documents.dart_partial_reason
        == DART_PARTIAL_REASON_TOO_FEW_DOCUMENTS_FOR_FULL
    )
    assert (
        too_few_documents.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_DOCUMENT_SOURCES_INSUFFICIENT
    )


def test_출고차단이_섞이면_DART_웹_준비장부족_순으로_사유를_고른다() -> None:
    insufficient_base = _without_section_evidence(
        _ready_base(),
        ("portfolio", "culture"),
        identity_mismatch_section_id="portfolio",
    )
    web_failed = _attempt(
        "page-0001",
        source_kind=_WEB,
        requirement=_REQUIRED,
        state=_FAILED,
        reason_code="network_failed",
        slot_ids=collector_slots_for("identity"),
    )
    dart_failed = _attempt(
        "document:dart_business_report:20260330000001",
        source_kind=_DART,
        requirement=_REQUIRED,
        state=_FAILED,
        reason_code="document_fetch_failed",
    )

    web_first = assess_official_evidence(
        _with_attempts(insufficient_base, (web_failed,))
    )
    dart_first = assess_official_evidence(
        _with_attempts(insufficient_base, (web_failed, dart_failed))
    )

    assert web_first.dart_partial_reason == DART_PARTIAL_REASON_TRANSIENT_WEB_FAILURE
    assert (
        dart_first.dart_partial_reason
        == DART_PARTIAL_REASON_REQUIRED_COLLECTION_INCOMPLETE
    )
    assert web_first.release_blocking_incomplete is True
    assert dart_first.release_blocking_incomplete is True


def test_여러_장에_복제된_선택경로_시도는_진단에_한_번만_남는다() -> None:
    page_cap = _attempt(
        "truncation-0001",
        source_kind=_WEB,
        requirement=_OPTIONAL,
        state=_TRUNCATED,
        reason_code=_WIDE_WEB_PAGE_CAP,
    )
    observed = _with_attempts(_ready_base(), (page_cap,))
    carriers = sum(
        1
        for candidate in observed.candidates
        if any(attempt.attempt_id == page_cap.attempt_id for attempt in candidate.attempts)
    )
    assert carriers == len(REQUIRED_EVIDENCE_SECTION_IDS), "시험 전제 — 아홉 장에 복제"

    preflight = assess_official_evidence(observed)

    assert preflight.optional_incomplete_attempts == (
        NonblockingIncompleteAttempt(_WEB, _TRUNCATED, _WIDE_WEB_PAGE_CAP),
    )


# ── 2) 실측 모양 ─────────────────────────────────────────────────────────
#
# 2026-09-22 상장사 평가 실행 한 건의 «6_수집_공식자료원진단» 시도 분포를 그대로
# 옮겼다(그 진단은 원래 회사 식별자·URL을 싣지 않는다). 같은 날 같은 조건의 세
# 실행 모두 이 분포였고, 사전검사는 준비 6/9·독립 문서 5건·미달 3장·불명 0장인데도
# 전환 갈래를 transient_web_failure로 기록했다. 칸 이름(slot_ids)은 진단에 없어
# 생산 계약이 허용하는 범위로 채웠다. 미달 3장의 빈 칸은 그 실행의
# 차단사유코드(evidence_absent_after_check:<칸>)에서 옮겼다.
_RECORDED_ATTEMPT_HISTOGRAM: tuple[tuple[str, str, str, str, int], ...] = (
    ("dart_audit_report", "MISSING", "list_query_missing", "REQUIRED", 1),
    ("dart_business_report", "OK", "document_selection_compressed", "OPTIONAL", 1),
    ("dart_business_report", "OK", "document_selection_compressed", "REQUIRED", 1),
    ("dart_business_report", "OK", "list_query_ok", "OPTIONAL", 1),
    ("dart_business_report", "OK", "no_keyword_signal", "OPTIONAL", 1),
    ("dart_business_report", "OK", "no_keyword_signal", "REQUIRED", 1),
    ("dart_consolidated_audit_report", "MISSING", "list_query_missing", "OPTIONAL", 1),
    ("dart_quarterly_report", "OK", "document_selection_compressed", "OPTIONAL", 1),
    ("dart_quarterly_report", "OK", "list_query_ok", "OPTIONAL", 1),
    ("dart_quarterly_report", "OK", "no_keyword_signal", "OPTIONAL", 1),
    ("dart_semiannual_report", "OK", "document_selection_compressed", "OPTIONAL", 1),
    ("dart_semiannual_report", "OK", "list_query_ok", "OPTIONAL", 1),
    ("dart_semiannual_report", "OK", "no_keyword_signal", "OPTIONAL", 1),
    ("official_ir_pdf", "FAILED", "ir_pdf_failed", "OPTIONAL", 1),
    ("official_web_page", "OK", "duplicate_content_or_empty", "REQUIRED", 7),
    ("official_web_page", "OK", "page_ok", "REQUIRED", 1),
    ("official_web_page", "OK", "root_identity_name_only", "OPTIONAL", 1),
    ("official_web_page", "OK", "sitemap_ok", "OPTIONAL", 1),
    ("official_web_page", "TRUNCATED", "truncated_page_cap", "OPTIONAL", 2),
    ("robots_txt", "OK", "robots_ok", "OPTIONAL", 1),
)
_RECORDED_ATTEMPT_COUNT = 27
_RECORDED_READY_SECTION_COUNT = 6
_RECORDED_INDEPENDENT_DOCUMENT_COUNT = 5
_RECORDED_INSUFFICIENT_SECTION_IDS = (
    "future_strategy",
    "culture",
    "competitive_position",
)
_RECORDED_MISSING_SLOT_IDS = (
    "future_strategy:stated_plan",
    "culture:work_principle",
    "competitive_position:stated_differentiator",
)


def _recorded_attempts() -> tuple[CollectionAttempt, ...]:
    attempts: list[CollectionAttempt] = []
    typed_page_sections = iter(REQUIRED_EVIDENCE_SECTION_IDS * 2)
    for source_kind, state, reason_code, requirement, count in (
        _RECORDED_ATTEMPT_HISTOGRAM
    ):
        for _ in range(count):
            # 페이지 유형이 좁힌 필수 웹 시도는 한 장의 칸만 건다. 나머지(DART
            # 문서·광역 웹·robots)는 그 종류가 주장할 수 있는 칸 전체를 건다.
            slot_ids = (
                collector_slots_for(next(typed_page_sections))
                if source_kind == _WEB and requirement == _REQUIRED.value
                else None
            )
            attempts.append(
                _attempt(
                    f"recorded-{len(attempts) + 1:04d}",
                    source_kind=source_kind,
                    requirement=SourceRequirement(requirement),
                    state=CollectionState(state),
                    reason_code=reason_code,
                    slot_ids=slot_ids,
                )
            )
    return tuple(attempts)


def _recorded_shape_result() -> OfficialEvidenceCollectionResult:
    """준비 6장·미달 3장·독립 문서 5건에 실측 시도 27건을 붙인다."""

    base = _with_dart_evidence(
        _result(document_count=_RECORDED_INDEPENDENT_DOCUMENT_COUNT)
    )
    candidates: list[ChapterEvidenceCandidates] = []
    for candidate in base.candidates:
        section_slots = collector_slots_for(candidate.section_id)
        filled = tuple(
            slot for slot in section_slots if slot not in _RECORDED_MISSING_SLOT_IDS
        )
        if filled != section_slots:
            fragment = candidate.fragments[0]
            candidate = replace(
                candidate,
                fragments=(
                    replace(fragment, slot_id=filled[0], covered_slot_ids=filled),
                ),
                candidate_readiness=EvidenceReadiness.INSUFFICIENT,
            )
        candidates.append(candidate)
    return _with_attempts(
        OfficialEvidenceCollectionResult(
            company_id=base.company_id,
            candidates=tuple(candidates),
        ),
        _recorded_attempts(),
    )


def test_실측_시도분포_픽스처는_운영_진단과_같은_분포를_낸다() -> None:
    """픽스처가 실측을 «그대로» 옮겼는지 운영 진단 함수로 대조한다."""

    recorded = official_collection_attempt_step(_recorded_shape_result())

    assert recorded["attempt_count"] == _RECORDED_ATTEMPT_COUNT
    assert recorded["histogram"] == [
        {
            "source_kind": source_kind,
            "state": state,
            "reason_code": reason_code,
            "requirement": requirement,
            "count": count,
        }
        for source_kind, state, reason_code, requirement, count in (
            _RECORDED_ATTEMPT_HISTOGRAM
        )
    ]


def test_실측_모양은_일시장애가_아니라_준비장_부족으로_부분보고서가_된다() -> None:
    preflight = assess_official_evidence(_recorded_shape_result())

    # 운영 사전검사 단계에 남은 판정 모양과 같다.
    assert preflight.decision.status is GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE
    assert len(preflight.decision.ready_section_ids) == _RECORDED_READY_SECTION_COUNT
    assert (
        preflight.decision.insufficient_section_ids
        == _RECORDED_INSUFFICIENT_SECTION_IDS
    )
    assert preflight.decision.unknown_section_ids == ()
    assert preflight.independent_document_count == _RECORDED_INDEPENDENT_DOCUMENT_COUNT
    # 운영 기록의 전환 갈래는 transient_web_failure였다. 선택 경로 잘림·실패는
    # 이제 출고 차단이 아니므로 사유가 준비 장 부족으로 바뀐다. 6/9 준비라
    # 여전히 부분 보고서이고, 관측은 그대로 남는다.
    assert preflight.collection_incomplete is True
    assert preflight.release_blocking_incomplete is False
    assert preflight.dart_partial_fallback is True
    assert (
        preflight.dart_partial_reason
        == DART_PARTIAL_REASON_INSUFFICIENT_WITH_READY_SECTIONS
    )
    assert (
        preflight.detail_code
        == FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT
    )
    assert preflight.supplementary_research_allowed is True
    # 실측 분포에는 필수 경로 잘림이 없으므로 설계 상한 예외 목록은 비어 있다.
    assert preflight.design_cap_exempt_attempts == ()
    assert Counter(preflight.optional_incomplete_attempts) == Counter(
        {
            NonblockingIncompleteAttempt(_WEB, _TRUNCATED, _WIDE_WEB_PAGE_CAP): 2,
            NonblockingIncompleteAttempt(
                SOURCE_KIND_OFFICIAL_IR_PDF, _FAILED, "ir_pdf_failed"
            ): 1,
        }
    )


# ── 3) real.py 출고 모드 배선 ────────────────────────────────────────────


@pytest.fixture
def _optional_switches_off(monkeypatch: pytest.MonkeyPatch):
    """뉴스·재분류 스위치를 끈 «갓 켠 프로세스»로 고정한다.

    뉴스 갈래가 켜지면 검색 실패 기록이 따로 출고를 막아 이 시험이 보려는
    사전검사 겹과 섞인다. 앞 시험이 켠 값이 새지 않게 환경과 고정값을 비운다.
    """

    monkeypatch.delenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, raising=False)
    monkeypatch.delenv(
        evidence_reclassify_switch.EVIDENCE_RECLASSIFY_ENV_NAME, raising=False
    )
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    evidence_reclassify_switch._reset_process_evidence_reclassify_switch_for_tests()  # noqa: SLF001
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    evidence_reclassify_switch._reset_process_evidence_reclassify_switch_for_tests()  # noqa: SLF001


def _comparison_fold(source_identity_digest: str, _comparison: object) -> str:
    """비교 생산물을 생성 신원에 접는 자리 — 접혔는지 값으로 보이게 한다."""

    return hashlib.sha256(
        f"{source_identity_digest}:비교접기".encode("utf-8")
    ).hexdigest()


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    engine: FakeEngine,
) -> tuple[Any, list[dict[str, Any]]]:
    """운영 배선 대역 위에 «캐시로 굳혀도 된다»고 답하는 작성기를 끼운다.

    작성기가 참을 돌려줘도 관측 미완료가 캐시 자격을 막는지 보려는 것이다.
    """

    calls = _wire_runtime(monkeypatch, engine=engine)
    composed: list[dict[str, Any]] = []

    def compose(**kwargs: Any) -> RunResult:
        composed.append(kwargs)
        return RunResult(
            outcome=Outcome.REPORT,
            message="가짜 composer 완료",
            generation_cache_eligible=True,
        )

    monkeypatch.setattr(real, "_run_v2_composer", compose)
    monkeypatch.setattr(real, "_comparison_generation_digest", _comparison_fold)
    return calls, composed


def _complete_with_optional_failures() -> OfficialEvidenceCollectionResult:
    """아홉 장 READY·문서 9건에 선택 경로 잘림·실패만 섞는다(실측 종류 그대로)."""

    return _with_attempts(
        _official_result(),
        (
            _attempt(
                "truncation-0001",
                source_kind=_WEB,
                requirement=_OPTIONAL,
                state=_TRUNCATED,
                reason_code=_WIDE_WEB_PAGE_CAP,
            ),
            _attempt(
                "truncation-0002",
                source_kind=_WEB,
                requirement=_OPTIONAL,
                state=_TRUNCATED,
                reason_code=_WIDE_WEB_PAGE_CAP,
            ),
            _attempt(
                "ir-0003",
                source_kind=SOURCE_KIND_OFFICIAL_IR_PDF,
                requirement=_OPTIONAL,
                state=_FAILED,
                reason_code="ir_pdf_failed",
            ),
            _attempt(
                "sitemap-0004",
                source_kind=_WEB,
                requirement=_OPTIONAL,
                state=_FAILED,
                reason_code="sitemap_failed",
            ),
        ),
    )


def _step(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    matches = [step for step in steps if step.get("step") == name]
    assert len(matches) == 1, [step.get("step") for step in steps]
    return matches[0]


def _step_names(steps: list[dict[str, Any]]) -> list[str]:
    return [str(step.get("step") or "") for step in steps]


def test_선택경로_잘림과_실패만_있으면_FULL로_작성기까지_간다(
    monkeypatch: pytest.MonkeyPatch,
    _optional_switches_off: None,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    calls, composed = _wire(monkeypatch, engine=engine)

    result = _run(_Collector([_complete_with_optional_failures()]))

    assert result.outcome is Outcome.REPORT, result.message
    assert len(composed) == 1
    # 출고 모드 — 작성기와 생성 조정이 실제로 받은 값이다.
    assert composed[0]["release_mode_override"] is ReleaseMode.FULL
    assert [item["release_mode"] for item in calls.coordinates] == [
        ReleaseMode.FULL.value
    ]
    assert len(calls.comparisons) == 1, "FULL 전용 양사 비교 갈래가 돌지 않았습니다"
    steps = composed[0]["steps"]
    assert _PARTIAL_SWITCH_STEP not in _step_names(steps)
    assert not any(step.get("step") == COLLECTION_RECOVERY_STEP for step in steps)
    preflight_step = _step(steps, _PREFLIGHT_STEP)
    assert preflight_step["수집미완료"] is True
    assert preflight_step["DART부분보고서전환"] is False
    assert preflight_step["전환갈래"] == ""
    # 수집미완료가 참인데 전환하지 않은 이유를 기록에서 읽는다.
    expected_step = {
        "step": OPTIONAL_INCOMPLETE_DIAGNOSTICS_STEP,
        "attempt_count": 4,
        "histogram": [
            {
                "nonblocking_reason": NONBLOCKING_REASON_OPTIONAL_PATH,
                "source_kind": SOURCE_KIND_OFFICIAL_IR_PDF,
                "state": "FAILED",
                "reason_code": "ir_pdf_failed",
                "count": 1,
            },
            {
                "nonblocking_reason": NONBLOCKING_REASON_OPTIONAL_PATH,
                "source_kind": _WEB,
                "state": "FAILED",
                "reason_code": "sitemap_failed",
                "count": 1,
            },
            {
                "nonblocking_reason": NONBLOCKING_REASON_OPTIONAL_PATH,
                "source_kind": _WEB,
                "state": "TRUNCATED",
                "reason_code": _WIDE_WEB_PAGE_CAP,
                "count": 2,
            },
        ],
    }
    assert _step(steps, OPTIONAL_INCOMPLETE_DIAGNOSTICS_STEP) == expected_step
    # 실행 요약 로그 한 줄에도 같은 값이 실린다(요약 허용 목록 등록).
    summary = json.loads(run_diagnostics.summary_json(steps))
    assert expected_step in summary[run_diagnostics.KEY_STEPS]


def test_선택경로_미완료_FULL도_캐시와_자료확인완료의_관측_뜻은_그대로다(
    monkeypatch: pytest.MonkeyPatch,
    _optional_switches_off: None,
) -> None:
    """출고는 열되 불완전 수집을 캐시로 굳히거나 «완료»로 보이지 않는다."""

    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    calls, composed = _wire(monkeypatch, engine=engine)

    result = _run(_Collector([_complete_with_optional_failures()]))

    assert composed[0]["release_mode_override"] is ReleaseMode.FULL
    # 캐시 조회 생략과 캐시 부적격 — 작성기가 참을 돌려줘도 관측이 막는다.
    assert calls.cache_lookups == []
    assert result.generation_cache_eligible is False
    steps = composed[0]["steps"]
    assert _step(steps, _PARTIAL_IDENTITY_STEP) == {
        "step": _PARTIAL_IDENTITY_STEP,
        "수집미완료": True,
        "캐시재사용가능": False,
    }
    # 대상판정은 공식 근거 사전검사보다 앞 단계다. 이 값에는 사전검사 관측이
    # 섞인 적이 없고(수정 전후 같다), 초기 DART 조회 실패만 반영한다.
    assert _step(steps, _TARGET_STEP)["자료확인완료"] is True

    # 생성 신원은 정상 캐시 신원과 분리된 부분 지문 위에 비교 생산물을 접은
    # 값이다. 조정과 작성기가 같은 값을 받아야 FULL 출고 신원 대조가 맞는다.
    projected = calls.legacy_collects[0]["formal_official_evidence"]
    financials, _years = engine.fetch_financials(CORP_ID, engine.UsageCounter())
    filing = engine.latest_report_rcept(CORP_ID, "상장기업", engine.UsageCounter())
    identity = ReportSourceIdentity.capture(filing=filing, financial_payload=financials)
    partial_digest = partial_generation_digest(
        company_id=CORP_ID,
        official_snapshot=projected.source_snapshot_sha256,
        source_identity=identity,
        failure_steps=steps,
    )
    coordinated = calls.coordinates[0]["preflight_identity_digest"]
    assert coordinated == _comparison_fold(partial_digest, None)
    assert composed[0]["source_identity_digest"] == coordinated
    assert coordinated != identity.cache_digest_with_official_snapshot(
        projected.source_snapshot_sha256
    )


def test_재무API가_없는_회사도_선택경로_미완료만으로_FULL이_꺼지지_않는다(
    monkeypatch: pytest.MonkeyPatch,
    _optional_switches_off: None,
) -> None:
    """재무 도장 없는 생성 신원을 관측 값으로 막으면 이 회사군은 FULL이 없다."""

    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = _AuditOnlyEngine()
    calls, composed = _wire(monkeypatch, engine=engine)

    result = _run(_Collector([_complete_with_optional_failures()]))

    assert result.outcome is Outcome.REPORT, result.message
    assert composed[0]["release_mode_override"] is ReleaseMode.FULL
    assert [item["release_mode"] for item in calls.coordinates] == [
        ReleaseMode.FULL.value
    ]
    step_names = _step_names(composed[0]["steps"])
    assert _NO_FINANCIALS_IDENTITY_STEP in step_names
    assert _PARTIAL_IDENTITY_STEP in step_names
    assert _PARTIAL_SWITCH_STEP not in step_names
    assert result.generation_cache_eligible is False


def test_필수_DART_문서가_잘리면_지금처럼_SHADOW로_내린다(
    monkeypatch: pytest.MonkeyPatch,
    _optional_switches_off: None,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    calls, composed = _wire(monkeypatch, engine=engine)
    base = _official_result()
    first = base.candidates[0]
    official = OfficialEvidenceCollectionResult(
        company_id=base.company_id,
        candidates=(
            replace(
                first,
                attempts=(
                    _attempt(
                        "deadline:dart_business_report:20260330000001",
                        source_kind=_DART,
                        requirement=_REQUIRED,
                        state=_TRUNCATED,
                        reason_code="deadline_exceeded",
                        slot_ids=collector_slots_for(first.section_id),
                    ),
                ),
            ),
            *base.candidates[1:],
        ),
    )

    result = _run(_Collector([official]))

    assert result.outcome is Outcome.REPORT, result.message
    assert composed[0]["release_mode_override"] is ReleaseMode.SHADOW
    assert [item["release_mode"] for item in calls.coordinates] == [
        ReleaseMode.SHADOW.value
    ]
    assert calls.comparisons == []
    steps = composed[0]["steps"]
    assert {
        "step": _PARTIAL_SWITCH_STEP,
        "사유코드": DART_PARTIAL_REASON_REQUIRED_COLLECTION_INCOMPLETE,
    } in steps
    assert OPTIONAL_INCOMPLETE_DIAGNOSTICS_STEP not in _step_names(steps)
    assert calls.cache_lookups == []
    assert result.generation_cache_eligible is False


def test_설계상한_예외인_필수경로_잘림도_진단단계에_근거와_함께_실린다(
    monkeypatch: pytest.MonkeyPatch,
    _optional_switches_off: None,
) -> None:
    """출고를 막지 않은 필수 경로 잘림도 사후에 «왜 안 막았나»를 읽게 한다.

    지금 생산 코드에는 이 잘림을 만드는 자리가 없다. 생산자가 생겼을 때 이
    잘림이 선택 경로 미완료와 섞이거나 기록에서 빠지지 않게 배선째 고정한다.
    """

    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    calls, composed = _wire(monkeypatch, engine=engine)
    official = _with_attempts(
        _official_result(),
        (
            _attempt(
                "page-0001",
                source_kind=_WEB,
                requirement=_REQUIRED,
                state=_TRUNCATED,
                reason_code=_WIDE_WEB_PAGE_CAP,
                slot_ids=collector_slots_for("culture"),
            ),
            _attempt(
                "ir-0002",
                source_kind=SOURCE_KIND_OFFICIAL_IR_PDF,
                requirement=_OPTIONAL,
                state=_FAILED,
                reason_code="ir_pdf_failed",
            ),
        ),
    )

    result = _run(_Collector([official]))

    assert result.outcome is Outcome.REPORT, result.message
    assert composed[0]["release_mode_override"] is ReleaseMode.FULL
    assert [item["release_mode"] for item in calls.coordinates] == [
        ReleaseMode.FULL.value
    ]
    steps = composed[0]["steps"]
    assert _step(steps, OPTIONAL_INCOMPLETE_DIAGNOSTICS_STEP) == {
        "step": OPTIONAL_INCOMPLETE_DIAGNOSTICS_STEP,
        "attempt_count": 2,
        "histogram": [
            {
                "nonblocking_reason": NONBLOCKING_REASON_OPTIONAL_PATH,
                "source_kind": SOURCE_KIND_OFFICIAL_IR_PDF,
                "state": "FAILED",
                "reason_code": "ir_pdf_failed",
                "count": 1,
            },
            {
                "nonblocking_reason": NONBLOCKING_REASON_REQUIRED_DESIGN_CAP,
                "source_kind": _WEB,
                "state": "TRUNCATED",
                "reason_code": _WIDE_WEB_PAGE_CAP,
                "count": 1,
            },
        ],
    }
    assert _PARTIAL_SWITCH_STEP not in _step_names(steps)
    assert result.generation_cache_eligible is False


def test_실측_모양은_런타임에서도_준비장_부족_사유로_SHADOW가_된다(
    monkeypatch: pytest.MonkeyPatch,
    _optional_switches_off: None,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    calls, composed = _wire(monkeypatch, engine=engine)

    result = _run(_Collector([_recorded_shape_result()]))

    assert result.outcome is Outcome.REPORT, result.message
    assert composed[0]["release_mode_override"] is ReleaseMode.SHADOW
    steps = composed[0]["steps"]
    assert {
        "step": _PARTIAL_SWITCH_STEP,
        "사유코드": DART_PARTIAL_REASON_INSUFFICIENT_WITH_READY_SECTIONS,
    } in steps
    assert _step(steps, OPTIONAL_INCOMPLETE_DIAGNOSTICS_STEP)["attempt_count"] == 3
    assert calls.comparisons == []


def _fail(*_args: Any, **_kwargs: Any) -> None:
    raise RuntimeError("시험용 자료원 조회 실패")


@pytest.mark.parametrize(
    ("boundary", "target_confirmed"),
    (
        ("profile", False),
        ("audit", False),
        ("financials", False),
        ("registry", False),
        # 최신 공시목록은 대상판정 단계 «뒤»에 읽으므로 그 단계의 표시는 참이다.
        ("filing", True),
    ),
)
def test_초기_DART_조회_실패는_관측과_출고차단을_함께_세운다(
    monkeypatch: pytest.MonkeyPatch,
    _optional_switches_off: None,
    boundary: str,
    target_confirmed: bool,
) -> None:
    """자료원 실패는 선택 경로가 아니다 — 비교 갈래 전에 SHADOW로 내린다."""

    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    if boundary in {"profile", "audit"}:
        endpoint_to_fail = "company.json" if boundary == "profile" else "list.json"
        original_get_json = engine.get_json

        def get_json(endpoint: str, params: dict[str, Any], counter: Any) -> Any:
            if endpoint == endpoint_to_fail:
                return _fail()
            return original_get_json(endpoint, params, counter)

        monkeypatch.setattr(engine, "get_json", get_json)
    else:
        method = {
            "financials": "fetch_financials",
            "filing": "latest_report_rcept",
            "registry": "load_public_org_registry",
        }[boundary]
        monkeypatch.setattr(engine, method, _fail)
    calls, composed = _wire(monkeypatch, engine=engine)

    result = _run(_Collector([_official_result()]))

    assert result.outcome is Outcome.REPORT, result.message
    assert composed[0]["release_mode_override"] is ReleaseMode.SHADOW
    # 사전검사 뒤 첫 출고 모드 선택에서 이미 내려가 FULL 전용 갈래를 열지 않는다.
    assert calls.comparisons == []
    steps = composed[0]["steps"]
    assert _PARTIAL_SWITCH_STEP in _step_names(steps)
    assert _step(steps, _TARGET_STEP)["자료확인완료"] is target_confirmed
    assert calls.cache_lookups == []
    assert result.generation_cache_eligible is False


# ── 4) 닫힌 어휘와 생산자 대조 ───────────────────────────────────────────


def test_선택경로_미완료_진단은_닫힌_어휘와_개수만_싣는다() -> None:
    step = optional_incomplete_attempt_step(
        (
            NonblockingIncompleteAttempt(_WEB, _TRUNCATED, _WIDE_WEB_PAGE_CAP),
            NonblockingIncompleteAttempt(_WEB, _TRUNCATED, _WIDE_WEB_PAGE_CAP),
            NonblockingIncompleteAttempt("vendor_page", _FAILED, "vendor_timeout_code"),
        ),
        design_cap_exempt=(
            NonblockingIncompleteAttempt(_WEB, _TRUNCATED, _WIDE_WEB_PAGE_CAP),
        ),
    )

    # 같은 종류·상태·사유라도 비차단 근거가 다르면 다른 행이다.
    assert step == {
        "step": OPTIONAL_INCOMPLETE_DIAGNOSTICS_STEP,
        "attempt_count": 4,
        "histogram": [
            {
                "nonblocking_reason": NONBLOCKING_REASON_OPTIONAL_PATH,
                "source_kind": _WEB,
                "state": "TRUNCATED",
                "reason_code": _WIDE_WEB_PAGE_CAP,
                "count": 2,
            },
            {
                "nonblocking_reason": NONBLOCKING_REASON_OPTIONAL_PATH,
                "source_kind": UNKNOWN_OFFICIAL_COLLECTION_VALUE,
                "state": "FAILED",
                "reason_code": UNKNOWN_OFFICIAL_COLLECTION_VALUE,
                "count": 1,
            },
            {
                "nonblocking_reason": NONBLOCKING_REASON_REQUIRED_DESIGN_CAP,
                "source_kind": _WEB,
                "state": "TRUNCATED",
                "reason_code": _WIDE_WEB_PAGE_CAP,
                "count": 1,
            },
        ],
    }
    # 상한 사유가 진단 어휘 밖이면 모두 unknown으로 뭉개져 이유를 못 읽는다.
    assert COLLECTION_CAP_TRUNCATION_REASON_CODES <= ALLOWED_OFFICIAL_COLLECTION_REASON_CODES


def test_설계상한_잘림_사유는_광역_수집기가_실제로_남기는_글자다() -> None:
    """shared 정본과 생산자 글자가 갈라지면 예외가 조용히 죽는다.

    feature 간 import 없이 생산 코드의 글자만 읽어 대조한다.
    """

    producer = (
        Path(__file__).resolve().parents[2] / "homepage" / "wide_collect.py"
    ).read_text(encoding="utf-8")

    missing = [
        code
        for code in sorted(COLLECTION_CAP_TRUNCATION_REASON_CODES)
        if f'"{code}"' not in producer
    ]
    assert missing == []
