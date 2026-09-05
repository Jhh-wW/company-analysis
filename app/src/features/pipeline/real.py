"""실제 조사 엔진을 canonical(v4) 회사분석 파이프라인에 연결한다.

회사 확인 뒤 공식 공시·홈페이지와 검증용 외부 자료를 수집하고, 최대 두 번의
조건부 사실 선택, 작가·검토 분리, 원자 사실 장부, 출고 게이트를 거친다. 직무·
채용공고·급여·복지 정보는 회사분석 생성 경로에 넣지 않는다. 회사 정체성·사업·
제품·3개년 변화·성장 전략·운영 구조의 핵심 계약을 통과하지 못하면
``GATE_STOPPED``로 끝낸다. 당면 과제·문화·동종업계 비교처럼 공식 근거가 있을
때만 성립하는 장은 표준 사유를 붙인 ``PARTIAL`` 기본 보고서에서 생략한다.

실제 실행은 유료 API를 사용할 수 있다. ``PIPELINE=real``로만 켜며 필요한 키와
의존성은 ``analysis_engine/.env`` 및 앱 운영 문서를 따른다. 캐시는
``company-report-v4-canonical`` 보고서만 반환·저장한다.
"""

from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.util
import itertools
import logging
import os
import re
import sys
import threading
import time
import urllib.parse
from contextlib import contextmanager
from dataclasses import dataclass, replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Iterable, Optional

from src.core import paths, typed_collector_switch
from src.core.clock import subtract_years, today_kst
from src.core.provider_gateway import attempt_context, gateway
from src.core.provider_gateway.anthropic_adapter import AnthropicAdapter
from src.core.constants import (
    AUDIT_WINDOW_YEARS,
    CACHE_HIT_LAYER1,
    CACHE_HIT_MESSAGE,
    CACHE_HIT_UNKNOWN_DATE,
    CELL_LABELS,
    COMPANY_SOURCE_CELLS,
    EMPTY_REASON_HOMEPAGE,
    GENERATION_MODEL,
    MODEL_LABEL_SEPARATOR,
    EMPTY_REASON_JOINER,
    EMPTY_REASON_KIND_NONE,
    EMPTY_REASON_MATERIAL_UNUSED,
    EMPTY_REASON_NOT_COMPANY_SPECIFIC,
    EMPTY_REASON_NEWS_FAILED,
    EMPTY_REASON_NEWS_NONE,
    EMPTY_REASON_NO_MATERIAL,
    HOMEPAGE_GATE_CELLS,
    MAX_AI_CALLS_PER_REQUEST,
    SUBSTANCE_FAILED_REASON,
    TABLE_DUMP_REASON,
    VOTE_ROUNDS,
)
from src.core.pricing import detailed_usage_cost_krw
from src.core.citations import citation_number
from src.features.grading.constants import ACCOUNTING_POLICY_REASON
from src.features.budget import provider_budget
from src.features.company_performance.logic import build_three_year_table
from src.features.company_specificity.logic import (
    filter_prose_lines as filter_specific_prose,
)
from src.features.company_comparison import (
    ComparisonBlockedError,
    ComparisonSourceConfigurationError,
    ComparisonSourceInternalError,
    ComparisonSourceTransientError,
    OfficialCompanyBundle,
    build_competitive_position,
)
from src.features.company_comparison.logic import (
    comparison_candidate_preflight_possible,
    discover_official_source_candidates,
)
from src.features.company_comparison.official_sources import (
    OfficialCandidateSentence,
    bind_dart_profile_attestation,
    candidate_sentences_from_fragments,
    dart_profile_attestation_material,
    register_candidate_sentence_evidence,
)
from src.features.company_comparison.stated_differentiator import (
    STATED_DIFFERENTIATOR_CLAIM_TYPE,
    add_stated_differentiator_fragments,
    register_stated_differentiator_sentence_evidence,
)
from src.features.business_candidate.dart_identity import (
    DartCompanyRecord,
    build_dart_company_index,
    generate_dart_company_matches,
    parse_dart_company_records,
)
from src.features.grading.financial import parse_financial_table
from src.features.homepage import link as homepage_link
from src.features.homepage.constants import FRAGMENT_KIND as HOMEPAGE_FRAGMENT_KIND
from src.features.homepage.logic import collect_homepage_fragments
from src.features.homepage.ir_pdf import (
    OFFICIAL_IR_FRAGMENT_KIND,
    collect_official_ir_fragments,
)
from src.features.homepage.safe_http import collection_cache_scope
from src.features.provenance.citations import build_citations
from src.features.provenance.sources import (
    Source,
    official_web_currentness_is_usable,
)
from src.features.spanselect.constants import (
    NEWS_FRAGMENT_KIND,
    USAGE_MODEL_KEY,
)
from src.shared.official_ir import verified_official_ir_fragment_is_usable
from src.shared import engine_build_identity, generation_coordination
from src.shared.company_identity import normalize_korean_registration_number
from src.shared.generation_cache_identity import GenerationCacheNamespace
from src.shared.report_source_identity import ReportSourceIdentity
from src.shared.report_generation.constants import ENGINE_V2_SCHEMA_VERSION
from src.shared.report_evidence.constants import (
    CollectionState,
    ReleaseMode,
    SOURCE_KIND_OFFICIAL_IR_PDF,
    SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
)
from src.shared.report_evidence.date_normalization import (
    normalize_official_source_date,
)
from src.shared.report_evidence.runtime_port import (
    OfficialEvidenceCollectionRequest,
    OfficialEvidenceCollectionResult,
    OfficialEvidenceCollector,
)
from src.shared.report_evidence.legacy_fragment_kinds import (
    LEGACY_KIND_REVENUE_AND_ORDERS,
)
from src.shared.revenue_table_provenance import (
    revenue_row_evidence_matches,
    revenue_table_axis_matches,
    revenue_table_section_id_from_caption,
    revenue_table_source_excerpt,
)
from src.shared.report_evidence.release_mode import (
    REPORT_RELEASE_MODE_ENV_NAME,
    parse_release_mode,
)
from src.features.filingclean import extra as filing_extra
from src.features.filingclean import logic as filing_clean
from src.features.filingclean import relationships as filing_relationships
from src.features.newspick import constants as newspick
from src.features.newspick import logic as newspick_logic
# 회사 유형 표기의 «정본»은 이력 쪽에 있다. 여기서 값을 다시 적어 두면
# 언젠가 한쪽만 바뀌어 이번과 같은 결함이 되풀이된다.
from src.features.observability import constants as observability_constants
from src.features.revenuemix import logic as revenuemix
from src.features.writer import constants as writer
from src.features.writer import logic as writer_logic
from src.features.writer import verify as writer_verify
from src.features.grading.logic import is_accounting_policy, is_table_dump
from src.features.cost_tracking.store import AiCostEvent
from src.features.pipeline.constants import ANTHROPIC_TIMEOUT_SEC, DART_SUCCESS_STATUS
from src.features.pipeline import engine_mode
from src.features.pipeline.evidence_transport import (
    EvidenceTransportError,
    build_section_evidence_packet_set,
)
from src.features.pipeline.comparison_transport import (
    build_typed_comparison_candidate_inputs,
)
from src.features.pipeline.official_evidence_preflight import (
    OfficialEvidencePreflight,
    assess_official_evidence,
    assess_packet_document_sources,
)
from src.features.pipeline.official_evidence_transport_adapter import (
    merge_official_evidence_fragments,
)
from src.features.pipeline.port import (
    CompanyCard,
    CompanyLookupResult,
    Grade,
    Outcome,
    Report,
    ReportSection,
    ReportTable,
    RunResult,
    SourceStatus,
    StepReporter,
    UserInput,
    outcome_for,
)
from src.features.report_standard.constants import (
    CANONICAL_SCHEMA_VERSION,
    COMPARISON_SHORTFALL_REASON,
    CUSTOMER_MARKET_SHORTFALL_REASON,
    IDENTITY_SUMMARY_SHORTFALL_REASON,
    OPTIONAL_BASIC_SECTION_IDS,
    PAST_NARRATIVE_SHORTFALL_REASON,
    REQUIRED_SECTION_IDS,
)
from src.features.pipeline.canonical_report import (
    MINIMUM_WRITTEN_ROLE_IDENTITY,
    MINIMUM_WRITTEN_ROLE_REVENUE,
    PublishBlockedError,
    assemble_report_draft,
    basic_report_selection_is_complete,
    basic_report_selection_subset,
    combine_validated_picks,
    finalize_report,
    historical_performance_bases_are_complete,
    sections_from_picks as canonical_sections_from_picks,
    supplement_missing_minimum_claims_once,
    write_and_verify_sections,
)
from src.features.spanselect.canonical import (
    historical_performance_basis_options,
    select_canonical_spans,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_EVIDENCE_MANIFEST_BINDING_INVALID,
    FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID,
    FINAL_GATE_DETAIL_PUBLIC_MANIFEST_BINDING_INVALID,
    FINAL_GATE_REASON_COMPARISON_BLOCKED,
    FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED,
    FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
    FINAL_GATE_REASON_MISSING_IDENTITY,
    FINAL_GATE_REASON_MISSING_IDENTITY_REVENUE,
    FINAL_GATE_REASON_MISSING_REVENUE,
    FINAL_GATE_REASON_OTHER_GATE,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT,
    FINAL_GATE_REASON_PUBLISH_BLOCKED,
    FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
    FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED,
    classify_v2_validation_final_gate_reason,
)
from src.shared.span_selection_diagnostics import (
    MAJORITY_REASON_ALL_REJECTED,
    MAJORITY_REASON_NO_CONSENSUS,
    MAJORITY_REASON_OUTPUT_LIMIT,
    MAJORITY_REASON_PARSE_FAILURE,
    MAJORITY_REASON_PROVIDER_EMPTY,
    SELECTION_REASON_INSUFFICIENT_COVERAGE,
    SELECTION_REASON_PREFLIGHT_CANDIDATES,
    SELECTION_REASON_PREFLIGHT_PERFORMANCE,
    SpanSelectionRoundDiagnostic,
    round_diagnostic_from_steps,
    selection_result_reason,
)
from src.features.storage import cache as cache_store
from src.features.storage import db as storage_db

logger = logging.getLogger(__name__)

#: 중단 안내에 병기할 최종 게이트 사유 코드 → 한국어 표기.
#: 새 게이트가 아니다 — 이미 기록되는 닫힌 코드의 화면 표기 변환일 뿐이다.
_FINAL_GATE_REASON_KO: Final[dict[str, str]] = {
    FINAL_GATE_REASON_COMPARISON_BLOCKED: "동종업계 비교 검증 실패",
    FINAL_GATE_REASON_PUBLISH_BLOCKED: "출고 전 자동 검증 거절",
    FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR: "보고서 품질 최소 기준 미달",
    FINAL_GATE_REASON_MISSING_IDENTITY: "회사 정체성 필수 사실 미확보",
    FINAL_GATE_REASON_MISSING_REVENUE: "수익 구조 필수 사실 미확보",
    FINAL_GATE_REASON_MISSING_IDENTITY_REVENUE: (
        "정체성·수익 구조 필수 사실 미확보"
    ),
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT: "공식 자료의 필수 근거 부족",
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION: "공식 자료 접근 설정 오류",
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT: "공식 자료 확인 중 일시 장애",
    FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED: (
        "공식 자료 의미 자동 확인 불확정"
    ),
    FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT: "내부 근거 연결 오류",
    FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED: "이 조사에 배정된 AI 예산 소진",
    FINAL_GATE_REASON_OTHER_GATE: "출고 전 자동 검증",
}

#: 엔진 v2 스위치 — 환경변수 이름과 켜짐 값. 정확히 "1"일 때만 v2 경로다
#: (04장: 기본(미설정)은 v1 그대로 — 바이트 단위 무변).
ENGINE_V2_ENV_NAME: Final[str] = engine_mode.ENGINE_V2_ENV_NAME
ENGINE_V2_ENV_ON: Final[str] = engine_mode.ENGINE_V2_ENV_ON

#: 요청 전역 중단 예외에 실어 보내는 「그때까지 실제로 쓴 값」의 속성 이름.
#: 중단은 결과를 돌려주지 않고 예외로 나가므로, 이 값을 같이 싣지 않으면 이미
#: 나간 AI 원가가 원가 기록에서 0원으로 사라진다. 실행기가 같은 이름으로 읽는다.
STOPPED_RUN_USAGE_ATTR: Final[str] = "stopped_run_usage"


def _requested_release_mode(
    generation_mode: engine_mode.EngineMode,
) -> Optional[ReleaseMode]:
    """지금 요청이 «어떤 릴리스 모드로» 보고서를 만들려는지 (C6 재사용 판정용).

    ★ 「모르겠다」를 지어내지 않는다. v1 요청이거나 환경값이 없거나 계약 밖
      문자열이면 `None`을 돌려주고, 재사용 판정은 예전 동작을 그대로 쓴다.
      FULL 요청은 환경값이 반드시 있다 — 없으면 `_run_v2_composer`가 AI 호출
      전에 입력 계약으로 막으므로, `None`을 관대하게 처리해도 FULL이 새는
      구멍이 생기지 않는다.
    """
    if generation_mode is not engine_mode.EngineMode.V2:
        return None
    raw_release_mode = os.environ.get(REPORT_RELEASE_MODE_ENV_NAME)
    if not raw_release_mode:
        return None
    try:
        return parse_release_mode(raw_release_mode)
    except ValueError:
        return None


def _generation_cache_namespace(
    engine: Any,
    build_identity: Any,
    generation_mode: engine_mode.EngineMode,
    *,
    release_mode: Optional[ReleaseMode],
) -> GenerationCacheNamespace | None:
    """캐시와 single-flight가 함께 쓰는 사전 생성기 신원을 만든다.

    배포 revision·모델·출력 설정은 provider 호출 전에 모두 확정된다.
    pipeline과 delivery가 서로 다른 문자열 해시를 만들지 않고 shared의
    ``GenerationCacheNamespace`` 한 벌을 ContentSnapshot까지 운반한다.

    ★ release_mode도 신원의 일부다 (C6 · F-CACHE)
      릴리스 모드는 «무엇을 만드는가»를 바꾸는 입력이다. FULL은 봉인·생산
      증거·엄격 품질 게이트를 지난 산출물이고 SHADOW는 아니다. 모드가
      열쇠에 없으면 같은 배포에서 모드만 바뀔 때 SHADOW 저장본과 FULL
      저장본이 «같은 칸»을 놓고 다툰다 — FULL 요청이 SHADOW 결과를 물어
      오거나(거짓 표기), 새로 만든 FULL을 옛 SHADOW 항목 열쇠에 결속하려다
      `ImmutableRecordConflict`로 하드 실패한다(검토에서 재현).
      namespace_id는 `settings_sha256`을 포함해 계산되고, single-flight
      `LeaseKey`와 캐시 `CacheLookupKey`가 **둘 다** 이 namespace_id를
      운반한다. 그래서 여기 한 곳에 넣으면 두 열쇠가 함께 갈라진다.
      모드를 모르면(v1 요청·환경값 없음) 예전 열쇠 그대로 두어 기존
      저장본을 계속 재사용한다.

    Args:
        release_mode: 지금 요청이 만들려는 릴리스 모드. 모르면 ``None``.
    """

    model = str(getattr(engine, "MODEL", "") or GENERATION_MODEL).strip()
    if not model:
        return None
    build_identity = engine_build_identity.require_exact_engine_build_identity(
        build_identity
    )
    generation_mode = engine_mode.require_exact_engine_mode(generation_mode)
    if not build_identity.cache_usable:
        return None
    revision = build_identity.deployment_revision
    # v1은 운영 장애 때의 롤백 경로다. v2만 contract를 넣으면 롤백 순간
    # adapter와 namespace가 갈라져 보고서 출고가 실패하므로 두 경로 모두 같은
    # immutable deployment contract를 쓴다.
    image_digest = f"generator-build:{build_identity.build_id}"
    if generation_mode is engine_mode.EngineMode.V2:
        from src.features.composer.render import (  # noqa: PLC0415
            ENGINE_V2_SCHEMA_VERSION,
        )

        schema_version = ENGINE_V2_SCHEMA_VERSION
    else:
        schema_version = CANONICAL_SCHEMA_VERSION
    settings: dict[str, Any] = {"temperature": 0}
    if release_mode is not None:
        # 모르는 경우에만 키를 빼서 옛 저장본의 열쇠를 그대로 둔다. 값을
        # 지어내 넣으면 v1 요청까지 전부 미적중이 된다.
        settings["release_mode"] = release_mode.value
    return GenerationCacheNamespace.create(
        product="company-analysis",
        schema_version=schema_version,
        deployment_revision=revision,
        image_digest=image_digest,
        requested_models={"pipeline": model},
        output_settings=settings,
    )

#: v2 작가·검수 호출의 출력 token 상한. 작가는 장 하나(6~12문장 JSON)를 돌려준다.
#: 검수는 보고서 전체 «확인» 문장(50개+)의 판정 목록을 «한 번에» 돌려주므로
#: 절단 여유를 크게 둔다 — v1 파일럿 전멸 원인이 max_tokens 절단(3,000→6,000도
#: 부족)이었고, 검수 절단은 재요청 실패 시 확인 전원 해석 강등으로 이어진다.
#: 실제 비용은 실행기의 건당·일일 예상비용 상한이 계속 지킨다.
V2_WRITER_MAX_TOKENS: Final[int] = 4000
V2_REVIEWER_MAX_TOKENS: Final[int] = 8000

#: 도식 검수의 출력 상한. 응답은 «경로 줄마다 참/거짓» 한 줄씩이고 줄은 장당
#: 최대 5개다 — 실제로 200토큰이면 충분하다.
#:
#: ★ 왜 따로 두나 (적대 검토 실측) — 예산은 «출력 상한»으로 미리 잡는다
#:   (provider_budget.reserve_call). 검수 상한 8000을 그대로 쓰면 도식 검수
#:   한 번이 본조사 900원의 21.7%인 195원을 잡아 버린다. 실측 실행비가 이미
#:   584원(삼성전자)이라 여유가 8% 미만이고, 넘으면 «도식이 빠지는» 정도가
#:   아니라 ProviderBudgetExceeded로 «보고서 전체»가 실패한다.
V2_DIAGRAM_MAX_TOKENS: Final[int] = 512

#: other_gate일 때 세부를 보태는 span-selection 결과 사유 코드 → 한국어 표기.
_SPAN_RESULT_REASON_KO: Final[dict[str, str]] = {
    MAJORITY_REASON_OUTPUT_LIMIT: "AI 응답 길이 초과 의심",
    MAJORITY_REASON_PARSE_FAILURE: "AI 응답 해석 실패",
    MAJORITY_REASON_PROVIDER_EMPTY: "AI 사실 선택 결과 없음",
    MAJORITY_REASON_ALL_REJECTED: "선택 후보 전부 검증 거절",
    MAJORITY_REASON_NO_CONSENSUS: "선택 결과 합의 실패",
    SELECTION_REASON_INSUFFICIENT_COVERAGE: "기본 보고서 필수 항목 미충족",
    SELECTION_REASON_PREFLIGHT_PERFORMANCE: "3개년 완료 실적표 미확보",
    SELECTION_REASON_PREFLIGHT_CANDIDATES: "공식 원문 후보 없음",
}


def _stop_reason_note(
    final_gate_reason: str, span_result_reason: str = ""
) -> str:
    """중단 안내 끝에 붙일 「 (사유: …)」 한국어 병기를 만든다.

    옛 이름 "자료부족_중단"이 모든 게이트 중단을 덮어 진단을 왜곡했으므로,
    사용자 안내에도 실제 사유를 한국어로 함께 적는다. 코드가 매핑에 없으면
    아무것도 붙이지 않는다 (내부 코드 원문을 화면에 내보내지 않는다).
    """

    labels: list[str] = []
    gate_label = _FINAL_GATE_REASON_KO.get(final_gate_reason, "")
    if gate_label:
        labels.append(gate_label)
    if final_gate_reason == FINAL_GATE_REASON_OTHER_GATE:
        detail_label = _SPAN_RESULT_REASON_KO.get(span_result_reason, "")
        if detail_label:
            labels.append(detail_label)
    if not labels:
        return ""
    return " (사유: " + " · ".join(labels) + ")"


def _publish_gate_reason_for_missing_minimum_roles(
    missing_roles: Iterable[str],
) -> str:
    """원문 없이 저장 가능한 최소 역할 결손 코드 하나로 접는다."""

    missing = frozenset(str(value or "").strip() for value in missing_roles)
    identity = MINIMUM_WRITTEN_ROLE_IDENTITY in missing
    revenue = MINIMUM_WRITTEN_ROLE_REVENUE in missing
    if identity and revenue:
        return FINAL_GATE_REASON_MISSING_IDENTITY_REVENUE
    if identity:
        return FINAL_GATE_REASON_MISSING_IDENTITY
    if revenue:
        return FINAL_GATE_REASON_MISSING_REVENUE
    return FINAL_GATE_REASON_PUBLISH_BLOCKED


def _missing_basic_selection_roles(picks: list[Any]) -> tuple[str, ...]:
    """다음 선택 호출이 보완할 기본 보고서 역할만 닫힌 이름으로 돌려준다.

    원문이나 모델 출력 문구는 되먹이지 않는다. 이미 로컬 검증을 통과한
    ``claim_type``과 참조 관계만 보고 빠진 역할을 계산해, 두 번째 호출이 첫
    호출과 똑같은 전체 작업을 반복하지 않게 한다.
    """

    by_type: dict[str, list[Any]] = {}
    by_sid = {
        str(getattr(item, "sid", "") or ""): item
        for item in picks
        if str(getattr(item, "sid", "") or "")
    }
    for item in picks:
        by_type.setdefault(str(getattr(item, "claim_type", "") or ""), []).append(
            item
        )

    missing: list[str] = []
    for claim_type in (
        "identity_summary",
        "revenue_model",
        "customer_market",
        "current_issue",
        "completed_execution",
        "change_interpretation",
        "future_plan",
    ):
        if not by_type.get(claim_type):
            missing.append(claim_type)

    products = by_type.get("priority_product", [])
    if not any(
        getattr(
            by_sid.get(str(getattr(item, "revenue_model_sid", "") or "")),
            "claim_type",
            "",
        )
        == "revenue_model"
        for item in products
    ):
        missing.append("priority_product")

    if not (by_type.get("operating_core") or by_type.get("partner_role")):
        missing.extend(("operating_core", "partner_role"))
    return tuple(dict.fromkeys(missing))

# ``_company_catalog``의 공개 모양(고유번호, 이름)은 기존 식별 엔진과 시험이 함께
# 쓴다. stock_code·modify_date는 그 계약을 깨지 않고 고유번호별 보조 메타데이터로
# 보존해 fuzzy 후보 정렬에만 사용한다.
_COMPANY_CATALOG_METADATA: dict[str, tuple[str, str]] = {}
_COMPANY_CATALOG_ENGLISH_NAMES: dict[str, str] = {}
_COMPANY_CATALOG_RECORDS: tuple[DartCompanyRecord, ...] = ()
_COMPANY_CANDIDATE_INDEX_SOURCE: object | None = None
_COMPANY_CANDIDATE_INDEX = None
_COMPANY_CANDIDATE_INDEX_LOCK = threading.Lock()

# 후보 화면은 세 장으로 제한하지만, 로컬 이름 순위만 보고 같은 수의 DART
# 기업개황을 잘라 버리면 주소·상장 여부를 점수에 반영하기 전에 정답이 탈락한다.
# 표시 상한보다 두 건만 더 보강해 rank 4~5도 비교하되 요청 수와 deadline은
# 계속 작게 묶는다. 이 값은 자동 확정 임계값이 아니라 후보 재정렬 탐색 폭이다.
_DART_PROFILE_ENRICHMENT_LIMIT = 5


#: 1판은 모듈 전역 `_spent_usd`에 비용을 더한다. 보통 `import run_pilot`은
#: `sys.modules`의 같은 객체를 돌려주므로 서버가 살아 있는 내내 모든 요청이 그 값을
#: 공유한다. 요청마다 다른 이름으로 원본 파일을 실행해 module namespace 자체를
#: 갈라 놓는다. 잠금은 짧은 이름 발급에만 쓰며 조사 본체를 직렬화하지 않는다.
_ENGINE_INSTANCE_IDS = itertools.count(1)
_ENGINE_INSTANCE_ID_LOCK = threading.Lock()


def _load_isolated_engine_module(engine_path: Path) -> Any:
    """1판 엔진 파일을 독립 module namespace로 한 번 실행한다.

    같은 파일을 읽더라도 반환 module마다 `_spent_usd`가 따로 생긴다. 원본 파일과
    `_ask()`는 한 줄도 바꾸지 않으므로 1판의 호출·거부·파싱 계약은 그대로다.

    ★ 실행하는 동안만 `sys.modules`에 넣고 반드시 뺀다. 일부 decorator는 실행 중
      자기 module을 되찾아야 하지만, 요청마다 고유 이름을 영구 등록하면 끝난 조사
      수만큼 엔진 module 객체가 쌓인다.
    """
    with _ENGINE_INSTANCE_ID_LOCK:
        instance_id = next(_ENGINE_INSTANCE_IDS)
    module_name = f"_app_run_pilot_request_{instance_id}"
    spec = importlib.util.spec_from_file_location(module_name, engine_path)
    if spec is None or spec.loader is None:
        raise ImportError(f"1판 엔진을 불러올 수 없습니다: {engine_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        # source 실행이 실패해도 반쯤 만들어진 module을 다음 요청이 집어 들면 안 된다.
        if sys.modules.get(module_name) is module:
            del sys.modules[module_name]
    return module


class _MeteredEngine:
    """1판 엔진을 고치지 않고 이 요청의 API 응답 사용량만 모으는 얇은 껍데기.

    ★ 이 껍데기가 받는 1판 module은 요청마다 독립 namespace다. 따라서 1판
      `_ask()`의 module 전역 `_spent_usd`와 `$8` 가드도 이 요청 안에서만 돈다.
      그래도 `_ask`만 감싸서는 usage를 전부 셀 수 없다 — `identify()` 같은 1판 함수는
      자기 module 전역 `_ask`를 직접 부른다. 그래서 더 아래 경계인 이 요청 client의
      `messages.create` 응답을 모은다. `MODEL`도 요청 로컬로 두고 provider 직전 경계에서
      덮어쓴다.
    """

    def __init__(self, engine: Any):
        object.__setattr__(self, "_engine", engine)
        object.__setattr__(self, "_usages", [])
        # ★ 1판 모듈의 MODEL을 직접 바꾸면 겹쳐 도는 다른 요청의 모델도 바뀐다.
        # 요청마다 자기 값을 들고 client 경계에서 덮어써야 Sonnet/Haiku가 섞이지 않는다.
        object.__setattr__(self, "_model", str(getattr(engine, "MODEL", "")))
        object.__setattr__(self, "_billing_uncertain", False)
        object.__setattr__(self, "_stage", "unspecified")
        object.__setattr__(self, "_prompt_cache", False)
        object.__setattr__(self, "_provider_call_count", 0)
        object.__setattr__(self, "_provider_call_lock", threading.Lock())

    def __getattr__(self, name: str) -> Any:
        if name == "MODEL":
            return self._model
        return getattr(self._engine, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in {
            "_engine",
            "_usages",
            "_model",
            "_billing_uncertain",
            "_stage",
            "_prompt_cache",
            "_provider_call_count",
            "_provider_call_lock",
        }:
            object.__setattr__(self, name, value)
        elif name == "MODEL":
            object.__setattr__(self, "_model", str(value))
        else:
            setattr(self._engine, name, value)

    def meter_client(self, client: Any) -> Any:
        """이 요청의 응답만 세는 client 껍데기를 만든다."""
        return _MeteredClient(client, self._usages, self)

    @property
    def usages(self) -> list[dict[str, Any]]:
        return list(self._usages)

    @property
    def billing_uncertain(self) -> bool:
        return bool(self._billing_uncertain)

    @property
    def current_stage(self) -> str:
        return str(self._stage)

    @property
    def prompt_cache_enabled(self) -> bool:
        return bool(self._prompt_cache)

    def set_stage(self, stage: str) -> None:
        clean = str(stage).strip()
        object.__setattr__(self, "_stage", clean or "unspecified")

    def reserve_provider_call(self) -> int:
        """요청 전체 AI 호출 상한을 실제 전송 경계에서 원자적으로 강제한다.

        성공 usage 목록의 길이를 세면 예외·usage 누락 호출이 빠진다. 호출을 보내기
        전에 별도 계수를 올려 실패도 포함하고, 16번째부터는 원장·네트워크 전에
        멈춘다.
        """

        with self._provider_call_lock:
            if self._provider_call_count >= MAX_AI_CALLS_PER_REQUEST:
                # ★ 돈이 아니라 «횟수»다 — 전용 타입으로 구분해 던진다.
                #   composer 의 «선택적 다듬기»는 이 구분을 보고 포기하고
                #   지금까지 만든 보고서로 끝낸다(실측 근거는
                #   composer/port.py::AskFatalError 주석 참조).
                raise provider_budget.RequestCallLimitReached(
                    "한 요청의 AI 호출 횟수 상한을 넘었습니다"
                )
            object.__setattr__(
                self,
                "_provider_call_count",
                self._provider_call_count + 1,
            )
            return int(self._provider_call_count)

    @contextmanager
    def stage_context(self, stage: str, *, prompt_cache: bool = False):
        previous_stage = self._stage
        previous_cache = self._prompt_cache
        self.set_stage(stage)
        object.__setattr__(self, "_prompt_cache", bool(prompt_cache))
        try:
            yield
        finally:
            object.__setattr__(self, "_stage", previous_stage)
            object.__setattr__(self, "_prompt_cache", previous_cache)


def _already_cache_marked_text_blocks(content: object) -> bool:
    """호출자가 «이미 나눠서» 캐시 경계를 찍어 보낸 text 블록 목록인지 본다.

    형식 검증은 str 경로와 같은 수준으로 유지한다 — dict이고 type이 text이며
    text가 문자열인 블록만 인정한다. 그중 하나라도 cache_control이 있어야
    «표식이 있다»로 친다.
    """

    if not isinstance(content, list) or not content:
        return False
    for block in content:
        if not isinstance(block, dict):
            return False
        if block.get("type") != "text" or not isinstance(block.get("text"), str):
            return False
    return any(block.get("cache_control") is not None for block in content)


def _prompt_cached_messages(messages: object) -> object:
    """Mark exact user text as an ephemeral cache block without changing it.

    ★ composer(v2)는 «공유 앞부분만» 캐시하려고 프롬프트를 두 블록으로 미리
      나눠 보낸다. 그런 요청까지 여기서 전체를 한 블록으로 다시 감싸면 장마다
      달라지는 뒷부분이 같은 블록에 섞여, 매 호출 cache write만 나고 read가
      0이 된다(= 캐시를 켠 값만 물고 아끼지는 못한다). 그래서 이미 경계가
      찍힌 블록 목록은 그대로 통과시킨다.
      str content(v1 span_selection)는 예전 동작 그대로 한 블록으로 감싼다.
    """

    if not isinstance(messages, list) or not messages:
        raise provider_budget.ProviderBudgetUnavailable(
            "prompt caching 대상 messages 형식이 올바르지 않습니다"
        )
    copied: list[object] = []
    marked = False
    for message in messages:
        if not isinstance(message, dict):
            raise provider_budget.ProviderBudgetUnavailable(
                "prompt caching 대상 message 형식이 올바르지 않습니다"
            )
        cloned = dict(message)
        content = cloned.get("content")
        if cloned.get("role") == "user" and isinstance(content, str):
            cloned["content"] = [
                {
                    "type": "text",
                    "text": content,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
            marked = True
        elif cloned.get("role") == "user" and _already_cache_marked_text_blocks(
            content
        ):
            marked = True
        copied.append(cloned)
    if not marked:
        raise provider_budget.ProviderBudgetUnavailable(
            "prompt caching할 user text block이 없습니다"
        )
    return copied


def _set_meter_stage(metered: _MeteredEngine, stage: str) -> None:
    """Keep local metering names out of the legacy engine API contract AST."""

    metered.set_stage(stage)


def _provider_output_config(value: Any) -> Any:
    """Normalize raw JSON schemas with the installed provider SDK before send.

    ``messages.create`` accepts a raw ``output_config`` mapping but does not run
    the SDK's schema transformer for that low-level form.  In particular,
    constraints such as ``uniqueItems`` are valid JSON Schema yet outside the
    provider's constrained-decoding subset.  Sending them unchanged produces a
    deterministic 400 before any model response.  Keep the source schema
    immutable and fail before reserving/calling the provider if normalization
    itself is unavailable.
    """

    if value is None:
        return None
    if not isinstance(value, dict):
        raise provider_budget.ProviderBudgetUnavailable(
            "provider structured-output 설정 형식이 올바르지 않습니다"
        )
    output_format = value.get("format")
    if output_format is None:
        return value
    if not isinstance(output_format, dict):
        raise provider_budget.ProviderBudgetUnavailable(
            "provider structured-output format 형식이 올바르지 않습니다"
        )
    if output_format.get("type") != "json_schema":
        return value
    schema = output_format.get("schema")
    if not isinstance(schema, dict):
        raise provider_budget.ProviderBudgetUnavailable(
            "provider structured-output schema 형식이 올바르지 않습니다"
        )
    try:
        transformer = getattr(importlib.import_module("anthropic"), "transform_schema")
        transformed = transformer(copy.deepcopy(schema))
    except Exception as exc:  # noqa: BLE001 - provider 호출 전 fail-closed
        raise provider_budget.ProviderBudgetUnavailable(
            "provider structured-output schema를 정규화할 수 없습니다"
        ) from exc
    if not isinstance(transformed, dict):
        raise provider_budget.ProviderBudgetUnavailable(
            "provider structured-output schema 정규화 결과가 올바르지 않습니다"
        )
    normalized_format = dict(output_format)
    normalized_format["schema"] = transformed
    normalized = dict(value)
    normalized["format"] = normalized_format
    return normalized


@contextmanager
def _meter_stage(
    metered: _MeteredEngine,
    stage: str,
    *,
    prompt_cache: bool = False,
):
    with metered.stage_context(stage, prompt_cache=prompt_cache):
        yield


def _anthropic_usage_event(
    value: object,
    *,
    fallback_model: str,
    stage: str,
    failed: bool,
) -> dict[str, Any] | None:
    """SDK 응답 또는 usage-bearing 예외를 같은 확정 비용 사건으로 바꾼다."""
    usage = getattr(value, "usage", None)
    if failed and usage is None:
        usage = getattr(getattr(value, "response", None), "usage", None)
    try:
        tokens_in = int(getattr(usage, "input_tokens"))
        tokens_out = int(getattr(usage, "output_tokens"))
        cache_creation = int(
            getattr(usage, "cache_creation_input_tokens", 0) or 0
        )
        cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        if min(tokens_in, tokens_out, cache_creation, cache_read) < 0:
            raise ValueError
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None
    used_model = str(getattr(value, "model", "") or fallback_model)
    actual_cost = detailed_usage_cost_krw(
        used_model,
        input_tokens=tokens_in,
        output_tokens=tokens_out,
        cache_creation_tokens=cache_creation,
        cache_read_tokens=cache_read,
        batch=False,
    )
    return {
        "in": tokens_in,
        "out": tokens_out,
        "cache_creation": cache_creation,
        "cache_read": cache_read,
        "batch": False,
        "stage": stage,
        "cost_krw": actual_cost,
        "failed": failed,
        "cache_hit": cache_read > 0,
        USAGE_MODEL_KEY: used_model,
    }


class _MeteredMessages:
    """Anthropic `messages.create`의 성공 응답 사용량을 요청별로 모은다."""

    def __init__(
        self,
        messages: Any,
        usages: list[dict[str, Any]],
        metered: _MeteredEngine,
    ):
        self._messages = messages
        self._usages = usages
        self._metered = metered

    def create(self, *args: Any, **kwargs: Any) -> Any:
        # Once a call has no authoritative usage, another call in the same paid
        # phase would stack a second unknown charge on top of the first.  Stop
        # locally before schema work, admission, or provider I/O; the outer
        # phase keeps the first unresolved reservation fail-closed.
        if self._metered.billing_uncertain:
            raise provider_budget.ProviderBudgetUnavailable(
                "미확정 provider 호출 뒤에는 같은 요청에서 다시 호출할 수 없습니다"
            )
        # ``MAX_AI_CALLS_PER_REQUEST``가 문서와 시험에만 있으면 실패 응답처럼
        # usages에 안 쌓이는 호출은 무한히 반복될 수 있다. 실제 전송보다 먼저
        # 요청 로컬 계수를 잡아 16번째 호출을 원장·네트워크 앞에서 닫는다.
        self._metered.reserve_provider_call()
        # 본조사는 DART snapshot과 single-flight owner가 확정된 뒤에만
        # phase를 연다. 이 호출은 누락된 새 provider 경로도 예산 문맥
        # 없이 밖으로 나가지 못하게 하는 마지막 방어선이다.
        generation_coordination.ensure_paid_phase()
        call_kwargs = dict(kwargs)
        # 1판 `_ask`는 모듈 전역 MODEL을 읽지만 그 값은 다른 요청과 공유된다.
        # provider에 나가는 마지막 경계에서 이 요청의 로컬 모델로 바로잡는다.
        if self._metered.MODEL:
            call_kwargs["model"] = self._metered.MODEL
        if "output_config" in call_kwargs:
            call_kwargs["output_config"] = _provider_output_config(
                call_kwargs["output_config"]
            )
        if self._metered.prompt_cache_enabled:
            call_kwargs["messages"] = _prompt_cached_messages(
                call_kwargs.get("messages")
            )
        model = str(call_kwargs.get("model", ""))
        max_tokens = call_kwargs.get("max_tokens")
        if not isinstance(max_tokens, int):
            raise provider_budget.ProviderBudgetUnavailable(
                "provider 출력 token 상한이 명시되지 않았습니다"
            )
        # 바이트 추정은 한글에서 글자당 3배로 부풀어 실제보다 훨씬 큰 예약액을
        # 잡는다 — 그 과대 예약이 «아직 쓸 돈이 남았는데» 요청을 죽였다(실측).
        # provider tokenizer에게 직접 물어 정확한 입력 계수를 쓰고, 못 얻으면
        # 예전 바이트 추정으로 그대로 되돌아간다(호출을 막지 않는다).
        exact_input_tokens = provider_budget.count_input_tokens(
            self._messages,
            model=model,
            messages=call_kwargs.get("messages") or [],
            system=call_kwargs.get("system"),
        )
        estimated_input = provider_budget.estimate_request_tokens_exact(
            {"args": args, "kwargs": call_kwargs},
            exact_input_tokens=exact_input_tokens,
        )
        if self._metered.prompt_cache_enabled:
            # A five-minute cache write is 1.25x normal input pricing.
            estimated_input = (estimated_input * 5 + 3) // 4
        call_reservation = provider_budget.current().reserve_call(
            model=model,
            input_tokens_upper=estimated_input,
            max_tokens=max_tokens,
        )
        try:
            callbacks = attempt_context.current()
            attempt_token = callbacks.begin_attempt(
                "anthropic",
                self._metered.current_stage,
                call_reservation.estimated_krw,
            )
        except Exception as error:
            # 영속 attempt를 열지 못했으므로 provider에는 아직 아무것도 보내지 않았다.
            provider_budget.current().cancel_before_dispatch(call_reservation)
            raise provider_budget.ProviderBudgetUnavailable(
                "provider 시도 원장을 시작할 수 없어 호출하지 않았습니다"
            ) from error

        fallback_model = str(call_kwargs.get("model", ""))
        stage = self._metered.current_stage

        def usage_cost(value: object, *, failed: bool) -> float | None:
            event = _anthropic_usage_event(
                value,
                fallback_model=fallback_model,
                stage=stage,
                failed=failed,
            )
            return None if event is None else float(event["cost_krw"])

        adapter = AnthropicAdapter(
            lambda value: usage_cost(value, failed=False),
            failure_cost_resolver=lambda value: usage_cost(value, failed=True),
        )

        def before_dispatch() -> None:
            callbacks.heartbeat(attempt_token)
            callbacks.mark_dispatch_intent(attempt_token)

        try:
            response = gateway.call_once(
                adapter=adapter,
                reserved_krw=call_reservation.estimated_krw,
                before_dispatch=before_dispatch,
                send=lambda: self._messages.create(*args, **call_kwargs),
                record_observation=lambda observation: callbacks.record_observation(
                    attempt_token, observation
                ),
            )
        except gateway.ProviderDispatchNotStarted as error:
            provider_budget.current().cancel_before_dispatch(call_reservation)
            raise provider_budget.ProviderBudgetUnavailable(
                "provider 전송 의도를 기록하지 못해 호출하지 않았습니다"
            ) from error
        except gateway.ProviderObservationRecordFailed as error:
            # 전송은 이미 일어났다. 결과를 DB에 못 썼으므로 예약을 반환하지 않고
            # lease 만료가 보수부채로 회수하도록 같은 요청도 여기서 멈춘다.
            _log_billing_uncertain(stage, "observation_record_failed", error)
            self._metered._billing_uncertain = True
            provider_budget.current().mark_unknown(call_reservation)
            raise provider_budget.ProviderBudgetUnavailable(
                "provider 호출 결과를 비용 원장에 기록하지 못했습니다"
            ) from error
        except gateway.ProviderCallFailed as wrapped:
            error = wrapped.__cause__
            if not isinstance(error, Exception):
                raise provider_budget.ProviderBudgetUnavailable(
                    "provider 실패 원인을 확인할 수 없습니다"
                ) from wrapped
            failure_event = _anthropic_usage_event(
                error,
                fallback_model=fallback_model,
                stage=stage,
                failed=True,
            )
            if failure_event is not None:
                self._usages.append(failure_event)
                try:
                    provider_budget.current().settle_call(
                        call_reservation,
                        actual_krw=float(failure_event["cost_krw"]),
                    )
                except provider_budget.ProviderCostInvariantError as invariant:
                    _log_billing_uncertain(stage, "settle_invariant_on_failure", invariant)
                    self._metered._billing_uncertain = True
                raise error
            # ★ 여기서 «모든» 실패를 미확정으로 접으면, 타임아웃 한 번에
            #   보고서 전체가 날아간다(실측: 현대카드 본조사가 1초 만에 죽었다).
            #   provider 가 요청을 «받아들이지 않은» 것이 확실한 거절(400·401·403·404)은
            #   토큰을 만들지 않았으므로 0원으로 «확정» 마감한다. 그러면 같은 요청의
            #   다음 호출을 막을 이유가 없다.
            if _is_determinate_zero_cost(error):
                logger.warning(
                    "provider 가 요청을 거절했습니다(0원 확정) stage=%s kind=%s status=%s",
                    stage or "unknown",
                    type(error).__name__,
                    getattr(error, "status_code", None)
                    or getattr(getattr(error, "response", None), "status_code", None),
                )
                provider_budget.current().settle_call(call_reservation, actual_krw=0.0)
                raise error
            # 나머지(타임아웃·연결끊김·429·5xx)는 «서버가 받았는지» 알 수 없다.
            # 0원으로 마감하지 않고 adapter가 기록한 보수부채와 예약을 함께 유지한다.
            _log_billing_uncertain(stage, "sdk_error_without_usage", error)
            self._metered._billing_uncertain = True
            provider_budget.current().mark_unknown(call_reservation)
            raise error

        usage_event = _anthropic_usage_event(
            response,
            fallback_model=fallback_model,
            stage=stage,
            failed=False,
        )
        if usage_event is None:
            # 응답은 왔지만 usage가 없으면 adapter도 같은 예약액을 부채로 남겼다.
            _log_billing_uncertain(stage, "response_without_usage", None)
            self._metered._billing_uncertain = True
            provider_budget.current().mark_unknown(call_reservation)
            return response
        self._usages.append(usage_event)
        try:
            provider_budget.current().settle_call(
                call_reservation,
                actual_krw=float(usage_event["cost_krw"]),
            )
        except provider_budget.ProviderCostInvariantError as invariant:
            # usage는 먼저 보존했다. 이미 생긴 비용을 숨기지 않고 상위에서
            # billing-uncertain으로 phase를 닫게 한다.
            _log_billing_uncertain(stage, "settle_invariant_on_success", invariant)
            self._metered._billing_uncertain = True
            raise
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._messages, name)


#: provider 가 «요청을 받아들이지 않아» 토큰을 만들지 않은 것이 확실한 HTTP 상태.
#: 이때는 0원으로 «확정» 마감한다 — 모호하지 않으므로 요청을 막을 이유가 없다.
#:
#: ⚠️ 좁게 잡는다. 애매하면 «모름»에 남긴다 — 돈을 적게 세는 쪽으로 기울면 안 된다.
#:   429(한도)·5xx(서버 오류)는 요청이 서버까지 갔다 거절된 것이라 여전히 모호하다.
#:   408·409 등도 넣지 않는다.
#: ⚠️ 이 판정은 «스트리밍이 아닌» messages.create 에만 맞다. 스트리밍을 도입하면
#:   중간에 400 이 날 수 있어 이미 만들어진 토큰이 생긴다 — 그때는 이 목록을 비워라.
_DETERMINATE_ZERO_COST_STATUSES: Final[frozenset[int]] = frozenset({400, 401, 403, 404})


def _is_determinate_zero_cost(error: BaseException) -> bool:
    """토큰을 만들지 않은 것이 «확실한» 거절인가.

    anthropic 을 import 하지 않고 status_code 속성만 본다 — SDK 버전이 바뀌어도
    깨지지 않고, 다른 provider 로 바뀌어도 같은 규약이면 그대로 동작한다.
    """
    status = getattr(error, "status_code", None)
    if not isinstance(status, int):
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)
    return isinstance(status, int) and status in _DETERMINATE_ZERO_COST_STATUSES


def _log_billing_uncertain(stage: str, reason: str, error: BaseException | None) -> None:
    """미확정으로 «왜» 접었는지 남긴다.

    ★ 이전에는 이 자리에 로그가 «하나도» 없어서, 조사가 통째로 죽어도
      서버 로그에 흔적이 없었다(27장 결함 D).
    ⚠️ 예외 «메시지»는 남기지 않는다 — provider 응답 본문이 섞일 수 있다.
      클래스 이름과 상태코드만 남긴다.
    """
    status = getattr(error, "status_code", None)
    if not isinstance(status, int):
        status = getattr(getattr(error, "response", None), "status_code", None)
    logger.warning(
        "비용 미확정으로 접었습니다 stage=%s reason=%s kind=%s status=%s",
        stage or "unknown",
        reason,
        type(error).__name__ if error is not None else "none",
        status if isinstance(status, int) else "none",
    )


class _MeteredClient:
    """원본 client의 나머지 기능은 그대로 두고 messages만 감싼다."""

    def __init__(
        self,
        client: Any,
        usages: list[dict[str, Any]],
        metered: _MeteredEngine,
    ):
        self._client = client
        # messages 경계가 없으면 유료 호출을 0원으로 통과시킬 수 있으므로 여기서
        # 즉시 실패시킨다. 시험 가짜도 실제 계약을 갖춰야 이 구멍을 숨기지 않는다.
        self.messages = _MeteredMessages(client.messages, usages, metered)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)

# ── 1판 엔진 종료 코드 → 화면 종료 종류 ─────────────────
# 왼쪽은 `run_pilot.py`의 `fin(...)` 인자다. 데모와 같은 표를 쓰지 않는 이유는
# 데모는 «저장된 기록»을, 여기는 «지금 도는 엔진»을 읽기 때문이다.
_OUTCOME_MAP: dict[str, Outcome] = {
    "완료": Outcome.REPORT,
    "중단_식별실패": Outcome.NOT_FOUND,
    "중단_기업개황실패": Outcome.NOT_FOUND,
    "중단_게이트미달": Outcome.GATE_STOPPED,
    "중단_요구역량없음": Outcome.POSTING_DISCARDED,
    "중단_생성실패": Outcome.FAILED,
    "거부_거부A": Outcome.REJECT_PUBLIC,
    "거부_거부B": Outcome.REJECT_NO_DISCLOSURE,
    "폐기": Outcome.POSTING_DISCARDED,
}


def _reject_outcome(status: str) -> Outcome:
    """판정 status 를 «화면 종류»로 옮긴다 — 데모와 «같은 규칙»(앞부분 맞추기).

    ★ 왜 정확일치가 아닌가 (실측 — 운영 결함이었다)
      `_OUTCOME_MAP` 의 열쇠는 run_pilot 의 `fin(...)` 이름인 「거부_거부A」인데,
      판정이 내놓는 값은 「거부A_공공기관」이라 열쇠가 「거부_거부A_공공기관」이 된다.
      정확일치로 찾으면 **둘 다 표에 없어** 기본값으로 떨어졌고, 그래서
      **공공기관(거부A)이 「공개된 재무 자료가 없습니다」 화면**을 봤다.
      거부B 는 기본값이 우연히 맞아 티가 안 났다.
      데모 쪽(`demo._outcome_of`)은 처음부터 앞부분 맞추기라 멀쩡했다 —
      **같은 뜻을 두 곳이 다른 방법으로 옮기고 있었던 것**이 진짜 원인이다.

    ★ 못 찾으면 「실패」다. 「자료 없음」으로 접지 않는다 — 모르는 것을
      아는 것처럼 말하는 화면이 바로 이 결함의 정체였다.
    """
    return outcome_for(f"거부_{status}", _OUTCOME_MAP)


def _canonical_corp_type(value: Optional[str]) -> str:
    """회사 유형 표기를 이력 정본(`CORP_TYPE_VALUES`)에 맞춘다.

    ★ 왜 필요한가 (2026-09-05 운영 실측)
      엔진 판정은 「비상장외감」(띄어쓰기 없음)을 냈는데 이력 정본은
      「비상장 외감」이라, **비상장 회사(현대카드·우리은행)의 이력 1행이
      허용값 검사에 걸려 전부 거부됐다.** 기록 실패는 조용히 삼켜지므로
      보고서는 정상으로 나갔지만 실행 상태가 「진행 중」으로 남고 대시보드·
      하루 집계·게이트 진단이 통째로 빠졌다. 상장사는 글자가 같아 멀쩡했다.
      → 이 값을 싣는 곳이 스무 곳이 넘는다. 각자 고치지 말고 **들어오는 자리**
        에서 한 번만 맞춘다.

    Args:
        value: 판정 또는 저장본이 들고 있는 회사 유형. `None`일 수 있다.

    Returns:
        아는 표기면 정본 글자, 모르는 표기면 **받은 값 그대로**.

    ★ 모르는 값을 빈칸으로 뭉개지 않는 이유 — 빈칸은 「02_판정에 이르지 못하고
      끝났다」는 «다른 뜻»이다. 판정까지 갔는데 빈칸으로 적으면 이력이 거짓말을
      한다. 모르는 값은 그대로 흘려보내 허용값 검사에서 소리 나게 둔다.
    """
    text = (value or "").strip()
    if text in observability_constants.CORP_TYPE_VALUES:
        return text
    # 띄어쓰기만 다른 표기를 같은 것으로 본다 (「비상장외감」 → 「비상장 외감」).
    squeezed = "".join(text.split())
    for canonical in observability_constants.CORP_TYPE_VALUES:
        if squeezed and squeezed == "".join(canonical.split()):
            return canonical
    if text:
        logger.warning("이력 정본에 없는 회사 유형입니다: %r", text)
    return text


def _engine() -> Any:
    """1판 엔진을 요청마다 독립 module namespace로 불러온다.

    ★ 파일 맨 위에서 부르지 않는다. 엔진은 `anthropic`·`presidio` 같은
      무거운 프로그램을 요구하는데, 그게 안 깔려 있어도 **데모 화면은 떠야 한다.**
    ★ 평범한 `import run_pilot`을 쓰지 않는다. 그 방식은 서버 수명 동안 같은 module을
      돌려줘 1판의 `_spent_usd`가 요청 사이에 누적된다.
    """
    root = paths.PROJECT_ROOT / "analysis_engine"
    for extra in (root / "src", root / "tools"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    return _load_isolated_engine_module(root / "tools" / "run_pilot.py")


def _exception_chain(error: BaseException) -> tuple[BaseException, ...]:
    """예외 문자열을 읽지 않고 cause/context 체인을 한 번만 순회한다."""

    chain: list[BaseException] = []
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        visited.add(id(current))
        chain.append(current)
        current = current.__cause__ or current.__context__
    return tuple(chain)


def _has_dart_error_type(error: BaseException, *class_names: str) -> bool:
    """분석 엔진의 닫힌 DART 예외 자료형만 이름·모듈 신원으로 확인한다."""

    expected = frozenset(class_names)
    return any(
        type(item).__module__ == "core.dart_client"
        and type(item).__name__ in expected
        for item in _exception_chain(error)
    )


def _comparison_source_failure_is_configuration(error: BaseException) -> bool:
    """운영자가 인증키·권한을 고쳐야 하는 실패인지 판정한다."""

    return any(
        isinstance(item, ComparisonSourceConfigurationError)
        for item in _exception_chain(error)
    ) or _has_dart_error_type(error, "DartAuthenticationError")


def _comparison_source_failure_is_transient(error: BaseException) -> bool:
    """재시도로 회복 가능한 DART 운영 장애만 분리한다.

    분석 엔진은 의존성 지연 로딩 때문에 앱 모듈에서 정적으로 import할 수 없다.
    그래서 예외 문구(바뀔 수 있고 URL·응답을 포함할 수도 있음)가 아니라, 이미
    로드된 실제 닫힌 예외 자료형을 모듈·클래스 신원으로 확인한다. 인증 실패는
    설정 오류이고 일반 ``DartClientError`` 파생은 미등록 내부 오류이므로 이
    함수가 참으로 만들지 않는다.
    """

    return any(
        isinstance(item, ComparisonSourceTransientError)
        for item in _exception_chain(error)
    ) or _has_dart_error_type(
        error,
        "DartLimitReached",
        "DartTransportError",
        "DartResponseError",
    )


@lru_cache(maxsize=1)
def _company_catalog() -> tuple[tuple[str, str], ...]:
    """전자공시 전체 법인의 기존 (고유번호, 표시명) 호환 목록.

    실제 정본은 같은 XML에서 읽은 immutable 5-field record이며, 이 2-tuple은
    기존 1판 exact-name index 계약에만 남긴다.
    """
    engine = _engine()
    # 기존 real 개발 실행은 analysis_engine/.env bootstrap에 의존할 수 있다.
    # 실시간 평가 launcher는 ANALYSIS_ENGINE_DISABLE_DOTENV=1이라 이 호출 자체가
    # no-op이고, 명시 전달된 환경변수만 사용한다.
    engine.load_env()
    xml = engine.download_corpcode(engine.CORPCODE_DIR, engine.UsageCounter())
    records = parse_dart_company_records(xml)
    global _COMPANY_CATALOG_RECORDS
    _COMPANY_CATALOG_RECORDS = records
    _COMPANY_CATALOG_METADATA.clear()
    _COMPANY_CATALOG_METADATA.update(
        {
            record.corp_code: (record.stock_code, record.modify_date)
            for record in records
        }
    )
    _COMPANY_CATALOG_ENGLISH_NAMES.clear()
    _COMPANY_CATALOG_ENGLISH_NAMES.update(
        {record.corp_code: record.corp_eng_name for record in records}
    )
    return tuple((record.corp_code, record.corp_name) for record in records)


def _records_from_candidate_catalog(
    catalog: tuple[object, ...],
) -> tuple[DartCompanyRecord, ...]:
    """Accept the legacy fixture tuple shapes at the local-index boundary."""
    records: list[DartCompanyRecord] = []
    for raw_entry in catalog:
        if isinstance(raw_entry, DartCompanyRecord):
            records.append(raw_entry)
            continue
        if not isinstance(raw_entry, (tuple, list)) or len(raw_entry) < 2:
            continue
        entry = tuple(raw_entry)
        corp_code, corp_name = str(entry[0]), str(entry[1])
        english_name = _COMPANY_CATALOG_ENGLISH_NAMES.get(corp_code, "")
        stock_code, modify_date = _COMPANY_CATALOG_METADATA.get(corp_code, ("", ""))
        if len(entry) >= 5:
            english_name = str(entry[2] or "").strip()
            stock_code = str(entry[3] or "").strip()
            modify_date = str(entry[4] or "").strip()
        elif len(entry) >= 4:
            stock_code = str(entry[2] or "").strip()
            modify_date = str(entry[3] or "").strip()
        elif len(entry) >= 3:
            stock_code = str(entry[2] or "").strip()
        records.append(
            DartCompanyRecord(
                corp_code=corp_code,
                corp_name=corp_name,
                corp_eng_name=english_name,
                stock_code=stock_code,
                modify_date=modify_date,
            )
        )
    return tuple(records)


def _company_candidate_index():
    """Cache normalized aliases against the cached catalog tuple identity."""
    catalog = _company_catalog()
    global _COMPANY_CANDIDATE_INDEX_SOURCE, _COMPANY_CANDIDATE_INDEX
    with _COMPANY_CANDIDATE_INDEX_LOCK:
        if _COMPANY_CANDIDATE_INDEX_SOURCE is not catalog:
            _COMPANY_CANDIDATE_INDEX = build_dart_company_index(
                _records_from_candidate_catalog(catalog)
            )
            _COMPANY_CANDIDATE_INDEX_SOURCE = catalog
        return _COMPANY_CANDIDATE_INDEX


@lru_cache(maxsize=1)
def _company_index() -> dict[str, list[str]]:
    """전자공시 전체 법인 이름 색인.

    ★ 한 번만 만든다. 10만 곳 넘는 XML을 파싱하므로 요청마다 하면 화면이 멈춘다.
    """
    engine = _engine()
    return engine.build_index(list(_company_catalog()))


# ══════════════════════════════════════════════════════════
# 보고서 만들기 — 엔진의 결과를 화면이 아는 모양으로 옮긴다
# ══════════════════════════════════════════════════════════

def _sections_from(
    kept: list[Any],
    frags: dict[int, dict[str, str]],
    engine: Any,
    engine_cells: Optional[dict[str, bool]] = None,
) -> tuple[list[ReportSection], list[str]]:
    """엔진이 고른 문장들을 항목별로 담는다.

    ★ 표 덩어리는 여기서 버린다.
      엔진을 고치지 않고 «화면에 내보내기 직전»에 거른다.
    ★ 회계기준 설명 문구도 여기서 버린다.
    ★ 알맹이 검사(①-b) 결과도 여기서 반영한다.
      전에는 3회 다수결까지 내고 **결과를 통째로 버렸다.**

    Args:
        engine_cells: 칸 → 알맹이 검사 통과 여부. None이면 검사 결과 없이 담는다.
    """
    by_cell: dict[str, list[tuple[str, str]]] = {}
    tables: dict[str, list[ReportTable]] = {}
    requirements: list[str] = []
    dumped: set[str] = set()
    policy_dropped: set[str] = set()

    for item in kept:
        cite = (
            f"조각 {item.fragment_id}·{frags[item.fragment_id]['종류']}"
            if item.fragment_id in frags
            else "공고"
        )
        if cite == "공고":
            requirements.append(item.sentence)
            continue
        # ★ 재무·회계 수치는 «표 그대로» 낸다. 버리지 않는다.
        table = parse_financial_table(item.sentence)
        if table is not None:
            tables.setdefault(item.block, []).append(table)
            continue
        if is_table_dump(item.sentence):
            dumped.add(item.block)
            continue
        # 회계기준 설명 문구는 회사 이름을 바꿔도 말이 된다.
        if is_accounting_policy(item.sentence):
            policy_dropped.add(item.block)
            continue
        by_cell.setdefault(item.block, []).append((item.sentence, cite))

    empty_reasons = getattr(engine, "EMPTY_REASONS", {})
    cell_substance = engine_cells or {}
    sections: list[ReportSection] = []
    for cell in COMPANY_SOURCE_CELLS:
        lines = by_cell.get(cell, [])
        cell_tables = tables.get(cell, [])
        # ★★ 알맹이 검사(①-b) 반영 — 문장이 «실제로 남아 있는» 칸에만 건다.
        #   ⚠️ ①-b의 False는 「내용이 나쁘다」가 아니라 «그 칸이 비어 있었다»인
        #     경우가 많다 — 엔진 프롬프트가 「문장이 없는 칸은 false」로 지시한다.
        #     빈 칸에까지 걸면 나중에 새로 채워진 문장이 통째로 다시 숨겨진다.
        if lines and cell_substance.get(cell, True) is False:
            lines = []
        reason = ""
        if not lines and not cell_tables:
            if by_cell.get(cell) and cell_substance.get(cell, True) is False:
                reason = SUBSTANCE_FAILED_REASON
            elif cell in dumped:
                reason = TABLE_DUMP_REASON
            elif cell in policy_dropped:
                reason = ACCOUNTING_POLICY_REASON
            else:
                reason = empty_reasons.get(cell, "수집 자료에 해당 재료 없음")
        sections.append(
            ReportSection(
                cell=cell,
                title=CELL_LABELS.get(cell, cell),
                lines=lines,
                empty_reason=reason,
                tables=cell_tables,
            )
        )
    return sections, requirements


#: 1판 대응표에 칸이 없을 때 `_sections_from`이 붙이는 사유. 이것도 갈아 끼울 대상이다.
_ENGINE_FALLBACK_REASON = "수집 자료에 해당 재료 없음"

#: 「⚠️ 못 가져옴 = 우리 쪽 실패」를 가리키는 `SourceStatus.state` 값 (port.py 정의).
_SOURCE_STATE_FAILED = "failed"


def _has_failed_source(sources: list[SourceStatus]) -> bool:
    """소스 중 하나라도 «우리 쪽 실패»(⚠️)가 있는가.

    ★ 「⚠️ 못 가져옴 → ❌ 저장 안 함」.
      「홈페이지가 그날만 죽었을 수 있다. 캐시하면 다음 사람도, 그다음 사람도
      영영 X를 본다. 그 회사가 「자료 없는 회사」로 굳어버린다.」
      ❌ 없음(회사의 사실)은 저장해도 된다 — 실패와 섞지 않는다.
    """
    return any(source.state == _SOURCE_STATE_FAILED for source in sources)


def _homepage_reason(homepage_state: str, homepage_detail: str) -> str:
    """홈페이지가 «실제로» 어떻게 됐는지 한 줄."""
    text = EMPTY_REASON_HOMEPAGE.get(homepage_state, "")
    if text and homepage_state == "failed" and homepage_detail:
        return f"{text} — {homepage_detail}"
    return text


def _source_details(
    kinds_for_cell: tuple[str, ...],
    collected_kinds: set[str],
    news_step: dict[str, Any],
    homepage_reason: str,
    uses_homepage: bool,
) -> list[str]:
    """이 칸이 쓰는 소스마다 «지금 실제 상태»를 한 줄씩 만든다."""
    details: list[str] = []
    for kind in kinds_for_cell:
        if kind in collected_kinds:
            continue
        if kind == NEWS_FRAGMENT_KIND:
            details.append(
                EMPTY_REASON_NEWS_FAILED
                if news_step.get("오류")
                else EMPTY_REASON_NEWS_NONE.format(found=news_step.get("검색결과", 0))
            )
        else:
            details.append(EMPTY_REASON_KIND_NONE.format(kind=kind))
    if uses_homepage and HOMEPAGE_FRAGMENT_KIND not in collected_kinds and homepage_reason:
        details.append(homepage_reason)
    return details


def _refresh_empty_reasons(
    sections: list[ReportSection],
    homepage_state: str,
    homepage_detail: str,
    *,
    engine: Any,
    collected_kinds: set[str],
    news_step: dict[str, Any],
    specificity_rejected_cells: set[str] | None = None,
) -> list[ReportSection]:
    """빈칸 사유를 «실제 수집 결과»로 다시 쓴다.

    ★ 1판 엔진은 칸마다 고정 문구를 **조건 없이** 붙인다. 그래서 뉴스를 6건
      모아 놓고도 「채택된 기사 없음」이라고 말했다. 있는 것을 없다고 하면
      사용자는 멀쩡한 회사를 포기한다 — 절대 규칙 5를 반대 방향으로 어긴 것이다.

    ★ 「재료가 없다」와 「재료는 있는데 안 뽑혔다」를 **반드시 갈라 말한다.**
      사용자가 할 일이 다르다 — 앞은 회사를 바꿔야 하고, 뒤는 다시 돌리면 된다.

    ⚠️ 앱이 «직접» 붙인 사유(표 덩어리·회계 문구·알맹이 미달)는 이미 사실이므로
      건드리지 않는다. 1판이 붙인 고정 문구만 갈아 끼운다.
    """
    engine_reasons = getattr(engine, "EMPTY_REASONS", {})
    replaceable = set(engine_reasons.values()) | {_ENGINE_FALLBACK_REASON}
    homepage_reason = _homepage_reason(homepage_state, homepage_detail)

    fixed: list[ReportSection] = []
    rejected_cells = specificity_rejected_cells or set()
    for section in sections:
        if section.lines or section.tables or section.empty_reason not in replaceable:
            fixed.append(section)
            continue

        if section.cell in rejected_cells:
            fixed.append(replace(section, empty_reason=EMPTY_REASON_NOT_COMPANY_SPECIFIC))
            continue

        kinds_for_cell = tuple(getattr(engine, "CELL_SOURCES", {}).get(section.cell, ()))
        uses_homepage = section.cell in HOMEPAGE_GATE_CELLS
        gathered = [k for k in kinds_for_cell if k in collected_kinds]
        if uses_homepage and HOMEPAGE_FRAGMENT_KIND in collected_kinds:
            gathered.append(HOMEPAGE_FRAGMENT_KIND)

        if gathered:
            reason = EMPTY_REASON_MATERIAL_UNUSED.format(
                materials=EMPTY_REASON_JOINER.join(gathered)
            )
        else:
            details = _source_details(
                kinds_for_cell, collected_kinds, news_step, homepage_reason, uses_homepage
            )
            reason = (
                EMPTY_REASON_NO_MATERIAL.format(
                    details=EMPTY_REASON_JOINER.join(details)
                )
                if details
                else section.empty_reason
            )

        fixed.append(
            ReportSection(
                cell=section.cell,
                title=section.title,
                lines=section.lines,
                empty_reason=reason,
                tables=section.tables,
            )
        )
    return fixed


def _step_usage_spent_krw(
    steps: list[dict[str, Any]], *, model: str
) -> float:
    """이 단계 목록에 직접 달린 AI 사용량만 원화로 센다.

    ★ 엔진 전역 `_spent_usd`의 전후 차이를 쓰면 동시에 본조사가 돌 때 남의 비용까지
      섞인다. client 응답에서 요청별로 모은 입력·출력 토큰만 다시 계산한다.
    """
    spent_krw = 0.0
    for step in steps:
        usage = step.get("usage")
        if not isinstance(usage, dict):
            continue
        tokens_in, tokens_out = usage.get("in"), usage.get("out")
        if not isinstance(tokens_in, (int, float)) or not isinstance(
            tokens_out, (int, float)
        ):
            continue
        used_model = str(usage.get(USAGE_MODEL_KEY) or model)
        if isinstance(usage.get("cost_krw"), (int, float)):
            spent_krw += float(usage["cost_krw"])
            continue
        spent_krw += detailed_usage_cost_krw(
            used_model,
            input_tokens=tokens_in,
            output_tokens=tokens_out,
            cache_creation_tokens=usage.get("cache_creation", 0),
            cache_read_tokens=usage.get("cache_read", 0),
            batch=usage.get("batch", False) is True,
        )
    return round(spent_krw, 2)


def _metered_client(metered: _MeteredEngine, client: Any) -> Any:
    """계약 검사(AST)가 1판 이름과 앱 껍데기 이름을 섞지 않게 감싼다."""
    # SDK 내부 retry는 이 경계를 다시 지나지 않아 재호출 예상비용을 따로 잡을 수
    # 없다. 자동 retry를 끄고 명시적인 재호출만 매번 새 예약을 받게 한다.
    with_options = getattr(client, "with_options", None)
    if callable(with_options):
        client = with_options(max_retries=0, timeout=ANTHROPIC_TIMEOUT_SEC)
    return metered.meter_client(client)


def _request_spent_krw(metered: _MeteredEngine) -> float:
    """계량 껍데기가 이 요청에서 직접 받은 모든 AI 응답 비용."""
    steps = [{"usage": usage} for usage in metered.usages]
    return _step_usage_spent_krw(
        steps,
        model=str(getattr(metered, "MODEL", "")),
    )


def _request_cost_events(metered: _MeteredEngine) -> tuple[AiCostEvent, ...]:
    """Return only non-sensitive stage/model/token/cost usage metadata."""

    events: list[AiCostEvent] = []
    for usage in metered.usages:
        model = str(usage.get(USAGE_MODEL_KEY, "")).strip()
        if not model:
            continue
        events.append(
            AiCostEvent(
                stage=str(usage.get("stage") or "unspecified"),
                model_id=model,
                input_tokens=int(usage.get("in", 0)),
                output_tokens=int(usage.get("out", 0)),
                cache_creation_tokens=int(usage.get("cache_creation", 0)),
                cache_read_tokens=int(usage.get("cache_read", 0)),
                batch_applied=usage.get("batch") is True,
                cost_krw=float(usage.get("cost_krw", 0.0)),
                failed_call=usage.get("failed") is True,
                cache_hit=usage.get("cache_hit") is True,
            )
        )
    return tuple(events)


def _request_model_label(metered: _MeteredEngine) -> str:
    """기본값을 지어내지 않고 실제 응답한 모델만 호출 순서대로 남긴다."""
    models = tuple(
        dict.fromkeys(
            str(usage.get(USAGE_MODEL_KEY, "")).strip()
            for usage in metered.usages
            if str(usage.get(USAGE_MODEL_KEY, "")).strip()
        )
    )
    return MODEL_LABEL_SEPARATOR.join(models)


def _request_billing_uncertain(metered: _MeteredEngine) -> bool:
    """provider 예외를 받은 요청인지 감춘 껍데기에서 읽는다."""
    return metered.billing_uncertain


def _first_fragment_cite(
    frags: dict[int, dict[str, str]],
    *,
    kind: str,
    text_prefix: str = "",
) -> str:
    """표가 실제 수집 조각 번호를 가리키게 한다. 못 찾으면 빈 문자열이다."""

    for number, fragment in sorted(frags.items()):
        if fragment.get("종류") != kind:
            continue
        if text_prefix and not str(fragment.get("원문") or "").startswith(text_prefix):
            continue
        return f"조각 {number}·{kind}"
    return ""


class RevenueTableEvidenceBindingError(ValueError):
    """매출 구성표와 그 표가 나온 공시 원문을 하나로 묶지 못했다."""


def _bind_revenue_table_evidence_fragments(
    frags: dict[int, dict[str, object]],
    revenue_tables: list[dict[str, Any]],
    *,
    filing: Optional[dict[str, Any]],
    filing_text: str,
) -> tuple[dict[int, dict[str, object]], list[dict[str, Any]]]:
    """표마다 정확한 원문 표 전체를 전용 숫자 인용 조각으로 붙인다.

    ``revenuemix``가 행별로 보존한 원문 범위가 정본이다. 기존 ``매출수주``
    조각의 첫 번호를 모든 표에 재사용하면 그 조각 안에 실제 표 행이 없을 수
    있으므로, 제품별·지역별 표마다 excerpt를 자르거나 정규화하지 않고 별도
    조각으로 보존한다. 어느 표라도 행 근거·문서 신원을 잃으면 조용히 표만
    버리지 않고 작성기 호출 전에 내부 계약 오류로 닫는다.
    """

    merged: dict[int, dict[str, object]] = {
        int(number): dict(raw) for number, raw in frags.items()
    }
    if not revenue_tables:
        return merged, []

    if type(filing_text) is not str or not filing_text:
        raise RevenueTableEvidenceBindingError(
            "매출 구성표를 실제 공시 원문에 다시 결속할 수 없습니다"
        )

    filing_raw = filing or {}
    document_id = str(filing_raw.get("rcept_no") or "").strip()
    if _RCEPT_NO_RE.fullmatch(document_id) is None:
        raise RevenueTableEvidenceBindingError(
            "매출 구성표의 공시 문서 식별자를 확인할 수 없습니다"
        )
    raw_document_date = str(filing_raw.get("rcept_dt") or "").strip()
    document_date = (
        f"{raw_document_date[:4]}-{raw_document_date[4:6]}-{raw_document_date[6:8]}"
        if re.fullmatch(r"\d{8}", raw_document_date)
        else ""
    )
    document_title = str(filing_raw.get("report_nm") or "").strip()

    bound_tables: list[dict[str, Any]] = []
    for table_index, raw_table in enumerate(revenue_tables, start=1):
        if not isinstance(raw_table, dict):
            raise RevenueTableEvidenceBindingError(
                "매출 구성표 transport가 Mapping이 아닙니다"
            )
        raw_evidence_rows = raw_table.get("evidence_rows")
        if not isinstance(raw_evidence_rows, (list, tuple)) or not raw_evidence_rows:
            raise RevenueTableEvidenceBindingError(
                "매출 구성표의 행별 원문 근거가 비었습니다"
            )
        evidence_rows = tuple(raw_evidence_rows)
        if any(type(value) is not str for value in evidence_rows):
            raise RevenueTableEvidenceBindingError(
                "매출 구성표의 행별 원문 근거 형식이 올바르지 않습니다"
            )
        excerpt = revenue_table_source_excerpt(evidence_rows)
        if not excerpt or excerpt != excerpt.strip():
            raise RevenueTableEvidenceBindingError(
                "매출 구성표의 원문 범위와 행 근거가 일치하지 않습니다"
            )
        if not revenue_table_axis_matches(
            axis=raw_table.get("axis"),
            caption=raw_table.get("caption"),
            evidence_rows=evidence_rows,
            cited_source_text=excerpt,
        ):
            raise RevenueTableEvidenceBindingError(
                "매출 구성표의 구분축·표제·원문 범위가 일치하지 않습니다"
            )
        headers = raw_table.get("headers")
        rows = raw_table.get("rows")
        raw_rows = raw_table.get("raw_rows")
        if (
            not isinstance(headers, (list, tuple))
            or not isinstance(rows, (list, tuple))
            or not isinstance(raw_rows, (list, tuple))
            or len(rows) != len(raw_rows)
            or len(rows) != len(evidence_rows)
            or len(rows) < 3
        ):
            raise RevenueTableEvidenceBindingError(
                "매출 구성표의 공개 행과 원문 행 수가 일치하지 않습니다"
            )
        expected_row_count = len(rows) - 1
        if any(
            not revenue_row_evidence_matches(
                evidence,
                cited_source_text=excerpt,
                filing_text=filing_text,
                headers=headers,
                public_row=rows[row_index],
                raw_row=raw_rows[row_index],
                expected_selected_index=row_index,
                expected_row_count=expected_row_count,
            )
            for row_index, evidence in enumerate(evidence_rows)
        ):
            raise RevenueTableEvidenceBindingError(
                "매출 구성표의 공개 값·원문 행·공시 범위가 일치하지 않습니다"
            )

        fragment_number = max(merged, default=0) + 1
        caption = str(raw_table.get("caption") or "").strip()
        merged[fragment_number] = {
            "종류": LEGACY_KIND_REVENUE_AND_ORDERS,
            "원문": excerpt,
            "문서ID": document_id,
            "문서명": document_title,
            "문서일": document_date,
            "원문위치": f"매출 구성 원문 표 {table_index} · {caption}".rstrip(" ·"),
        }
        table = copy.deepcopy(raw_table)
        # ``axis``는 원문 근거를 검증하기 위한 transport 전용 값이다. 검증 뒤
        # V1 공개 ReportTable 스키마에는 넘기지 않아 기존 출력 계약을 지킨다.
        table.pop("axis", None)
        table["cite"] = (
            f"조각 {fragment_number}·{LEGACY_KIND_REVENUE_AND_ORDERS}"
        )
        bound_tables.append(table)

    return merged, bound_tables


def _used_citation_numbers(sections: list[ReportSection]) -> set[int]:
    """최종 화면에서 실제로 가리킨 조각 번호만 모은다."""

    used: set[int] = set()
    for section in sections:
        cites = [cite for _text, cite in section.lines]
        cites += [cite for _text, cite in section.prose_lines]
        cites += [table.cite for table in section.tables]
        for cite in cites:
            number = citation_number(cite)
            if number:
                used.add(int(number))
    return used


def _sources_from(steps: list[dict[str, Any]]) -> list[SourceStatus]:
    """엔진이 남긴 단계 기록에서 소스별 수집 현황을 뽑는다.

    ⭕ 찾음 / ❌ 없음 / ⚠️ 못 가져옴 — 셋을 섞으면 오거부가 된다.
    """
    def step(name: str) -> Optional[dict[str, Any]]:
        return next((s for s in steps if s.get("step") == name), None)

    sources: list[SourceStatus] = []

    fail = step("6_수집_원문")           # 엔진은 «실패했을 때만» 이 단계를 남긴다
    collect = step("6_수집")
    if fail is not None:
        sources.append(SourceStatus("전자공시", "failed", "공시 원문을 가져오지 못했습니다"))
    elif collect and collect.get("원문"):
        sources.append(
            SourceStatus(
                "전자공시",
                "ok",
                f"{collect['원문']} · 조각 "
                f"{collect.get('전자공시조각수', collect.get('조각수', 0))}개",
            )
        )
    else:
        sources.append(SourceStatus("전자공시", "none", "최근 3년 안에 낸 보고서가 없습니다"))

    news = step("6_수집_뉴스")
    if news is None:
        sources.append(SourceStatus("뉴스", "none", "여기까지 오지 못함"))
    elif news.get("생략"):
        sources.append(SourceStatus("뉴스", "none", str(news["생략"])))
    elif news.get("오류"):
        sources.append(SourceStatus("뉴스", "failed", "뉴스 검색에 실패했습니다"))
    else:
        taken, found = news.get("채택", 0), news.get("검색결과", 0)
        sources.append(
            SourceStatus("뉴스", "ok", f"검색 {found}건 중 {taken}건 채택")
            if taken
            else SourceStatus("뉴스", "none", f"검색 {found}건 · 채택 조건 통과 0건")
        )

    home = step("6_수집_홈페이지")
    if home is None:
        sources.append(SourceStatus("회사 홈페이지", "none", "여기까지 오지 못함"))
    elif home.get("오류"):
        # ⚠️ 우리 쪽 실패다. ❌(회사에 자료가 없음)와 섞으면 오거부가 된다.
        sources.append(SourceStatus("회사 홈페이지", "failed", str(home["오류"])))
    elif home.get("조각수"):
        sources.append(
            SourceStatus("회사 홈페이지", "ok", f"페이지에서 조각 {home['조각수']}개")
        )
    else:
        sources.append(SourceStatus("회사 홈페이지", "none", str(home.get("없음", "자료 없음"))))

    ir = step("6_수집_공식IR")
    if ir is None:
        sources.append(SourceStatus("회사 공식 IR", "none", "여기까지 오지 못함"))
    elif ir.get("오류"):
        sources.append(SourceStatus("회사 공식 IR", "failed", str(ir["오류"])))
    elif ir.get("조각수"):
        detail = f"PDF 조각 {ir['조각수']}개"
        if ir.get("문서시도") is not None:
            detail += f" · 문서 {ir['문서시도']}개 시도"
        if ir.get("상세"):
            detail += f" · {ir['상세']}"
        sources.append(SourceStatus("회사 공식 IR", "ok", detail))
    else:
        sources.append(
            SourceStatus("회사 공식 IR", "none", str(ir.get("없음", "자료 없음")))
        )
    return sources


def _comparison_candidate_scope_complete(
    steps: list[dict[str, Any]],
    *,
    filing: Optional[dict[str, Any]],
) -> bool:
    """공식 후보 부재를 확정할 만큼 모든 허용 수집 경계가 완전한가."""

    if filing and any(step.get("step") == "6_수집_원문" for step in steps):
        return False
    for name in ("6_수집_홈페이지", "6_수집_공식IR"):
        step = next((item for item in steps if item.get("step") == name), None)
        if (
            step is None
            or step.get("오류")
            or step.get("후보범위완전") is not True
        ):
            return False
    return True


def _generation_cache_eligibility(
    report: Report,
    *,
    sources: list[SourceStatus],
    steps: list[dict[str, Any]],
    filing: Optional[dict[str, Any]],
) -> tuple[bool, set[str], set[str]]:
    """현재 결과가 이후 새 조사에 재사용 가능한지 한 정본으로 판정한다.

    링크·최초 PDF 저장은 이 판정과 무관하다. 일시적 수집 실패나 후보 범위
    불완전은 현재 사용자에게 결과를 주더라도 다음 조사까지 60일 고정하지 않는다.
    v1·v2·불변 Delivery 캐시가 반드시 이 같은 판정을 공유한다.
    """

    included_section_ids = {section.cell for section in report.sections}
    missing_sections = (
        OPTIONAL_BASIC_SECTION_IDS | REQUIRED_SECTION_IDS
    ) - included_section_ids
    content_shortfall_reasons = {
        IDENTITY_SUMMARY_SHORTFALL_REASON,
        CUSTOMER_MARKET_SHORTFALL_REASON,
        PAST_NARRATIVE_SHORTFALL_REASON,
    }.intersection(report.shortfall_reasons)
    eligible = not (
        _has_failed_source(sources)
        or not _comparison_candidate_scope_complete(steps, filing=filing)
        or missing_sections
        or content_shortfall_reasons
    )
    return eligible, missing_sections, content_shortfall_reasons


def _official_company_aliases(profile: dict[str, Any]) -> tuple[str, ...]:
    """DART 기업개황의 공식 영문명·종목명만 순서대로 중복 제거한다."""

    return tuple(
        dict.fromkeys(
            value
            for value in (
                str(profile.get("corp_name_eng") or "").strip(),
                str(profile.get("corp_eng_name") or "").strip(),
                str(profile.get("stock_name") or "").strip(),
            )
            if value
        )
    )


def _official_company_registration_numbers(profile: dict[str, Any]) -> tuple[str, ...]:
    """같은 company.json 응답의 사업자·법인등록번호만 정규화한다.

    공식 웹 adapter가 company.json을 다시 호출하지 않게, 이미 회사 확인에
    사용한 한 snapshot에서 식별정보를 함께 운반한다. DART가 빈 값이나 예상
    밖 형식을 주면 낡아 재할당됐을 수 있는 ``hm_url``까지 포함해 공식 웹
    승격을 포기한다. 이 경우 DART 공시 근거만 남기고 품질 사전검사가
    충분성 여부를 결정한다.
    """

    numbers: list[str] = []
    for field_name in ("bizr_no", "jurir_no"):
        normalized = normalize_korean_registration_number(
            profile.get(field_name)
        )
        if normalized and normalized not in numbers:
            numbers.append(normalized)
    return tuple(numbers)


def _official_candidate_urls(profile: dict[str, Any]) -> tuple[str, ...]:
    """DART profile이 별도 필드로 확인한 exact 공식 URL 후보만 돌려준다.

    사용자 자유입력·검색 snippet·회사명으로 만든 추측 URL은 넣지 않는다.
    ``hm_url``은 기존 root 필드가 운반하며, 여기서는 DART의 별도 ``ir_url``만
    보조 후보로 둔다. 후보도 수집기에서 법인명+등록번호를 다시 확인해야 한다.
    """

    return tuple(
        dict.fromkeys(
            value
            for value in (str(profile.get("ir_url") or "").strip(),)
            if value
        )
    )


def _official_evidence_stop_source(
    preflight: OfficialEvidencePreflight,
    *,
    gate_reason: str,
) -> SourceStatus:
    """원문·URL·예외문 없이 공식 자료 사전검사 결과를 사용자에게 보인다."""

    if gate_reason == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT:
        return SourceStatus(
            "공식 근거 사전검사",
            "failed",
            "필수 공식 자료 경로를 끝까지 확인하지 못했습니다",
        )
    if gate_reason == FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED:
        return SourceStatus(
            "공식 근거 사전검사",
            "failed",
            "공식 자료는 읽었지만 필요한 내용인지 자동으로 확인하지 못했습니다",
        )
    return SourceStatus(
        "공식 근거 사전검사",
        "none",
        (
            f"독립 문서 {preflight.independent_document_count}건을 확인했지만 "
            "완성 보고서의 최소 근거가 부족합니다"
        ),
    )


class LocalDartProfileEnrichmentError(RuntimeError):
    """로컬 DART 후보의 선택 전 profile 보강만 실패했음을 표시한다."""

    local_profile_enrichment_failed = True


class RealPipeline:
    """1판 엔진을 `port.Pipeline` 약속에 맞춰 감싼 것.

    ★ 상태를 들고 있지 않는다. 확인 카드와 실행 사이에 필요한 것은
      `CompanyCard.ref`(전자공시 고유번호)로만 넘긴다.
      서버가 여러 대로 늘어나도 그대로 돈다.
    """

    # 웹 실행기가 DART preflight 전에 본조사 phase를 미리 예약하지
    # 않아도 된다는 명시적 capability. ``_run_metered``가 완전한 source
    # 지문→single-flight owner→phase 순서를 직접 지킨다.
    supports_deferred_paid_phase = True

    # 현재 기업분석 전용 엔진은 직무·공고를 보고서 입력으로 사용하지 않는다.
    # UI 표시와 별개로 서버가 OCR provider를 열지 않게 하는 capability 정본이다.
    supports_posting_image_input = False

    # corpCode 로컬 색인과 무료 DART 기업개황만 쓰며 AI/Places 비용은 만들지 않는다.
    business_candidate_provider_costs_money = False

    def __init__(
        self,
        *,
        official_evidence_collector: OfficialEvidenceCollector | None = None,
    ) -> None:
        # web 조립부는 production adapter를 주입한다. None은 v1·SHADOW와
        # 외부 I/O를 쓰지 않는 기존 단위시험의 호환 경로다. 요청별 자료는
        # 이 인스턴스에 저장하지 않아 여러 worker에서도 상태를 공유하지 않는다.
        self._official_evidence_collector = official_evidence_collector

    def search_business_candidates(
        self, *, company: str, address_hint: str, limit: int, timeout_sec: float
    ) -> list[dict[str, object]]:
        """공식 DART 색인 후보를 top-k 기업개황으로 보강해 반환한다.

        exact·영문명·약어·token·긴 오타 후보를 먼저 로컬에서 합치고 ``corp_code``로
        중복 제거한다. 점수는 화면 순서일 뿐 어느 후보도 자동 확정하지 않는다.
        """
        from src.features.business_candidate.logic import (  # noqa: PLC0415
            score_business_candidate,
        )

        # company.json은 후보마다 외부 DART 요청 한 번이다. 화면 표시 수와 profile
        # 보강 수를 분리한다. 로컬 rank 4~5까지 주소·상장 여부를 비교해야 짧은 공식
        # 약어의 동명 후보가 앞에 몰려도 관련 법인을 후보 카드에 남길 수 있다.
        # 전체 resolver timeout 뒤 취소할 수 없는 thread가 계속 호출하지 않도록
        # 보강 폭은 다섯 건으로 고정하고, 매 호출 전에 deadline을 다시 확인한다.
        display_cap = max(1, min(int(limit), 3))
        profile_cap = max(display_cap, _DART_PROFILE_ENRICHMENT_LIMIT)
        deadline = time.monotonic() + max(0.1, float(timeout_sec))
        all_matches = list(
            generate_dart_company_matches(
                _company_candidate_index(), company, limit=max(15, profile_cap * 3)
            )
        )
        # 이름 근거의 다양성만으로 낮은 local rank를 먼저 끌어올리면 더 관련성 높은
        # 같은-kind 후보가 profile 조회 전에 잘릴 수 있다. matcher의 결정론적 순위를
        # 그대로 보강한 뒤, 실제 profile 근거로 최종 표시 후보만 다시 정렬한다.
        matched = all_matches[:profile_cap]
        if not matched:
            return []

        engine = _MeteredEngine(_engine())
        engine.load_env()
        counter = engine.UsageCounter()
        ranked_out: list[tuple[float, int, str, str, dict[str, object]]] = []
        for match in matched:
            if time.monotonic() >= deadline:
                break
            record = match.record
            corp_code = record.corp_code
            profile = engine.get_json("company.json", {"corp_code": corp_code}, counter)
            if not isinstance(profile, dict) or profile.get("status") != DART_SUCCESS_STATUS:
                raise LocalDartProfileEnrichmentError(
                    "DART 기업개황 후보 조회가 정상 상태가 아닙니다"
                )
            candidate_name = str(profile.get("corp_name") or record.corp_name)
            address = str(profile.get("adres") or "")
            homepage = _homepage_url_for_display(profile.get("hm_url", ""))
            score, _evidence = score_business_candidate(
                query=company,
                address_hint=address_hint,
                candidate_name=candidate_name,
                address=address,
                homepage=homepage,
                stock_code=record.stock_code,
                modify_date=record.modify_date,
                english_name=record.corp_eng_name,
                name_match_kind=match.match_kind,
                name_similarity=match.similarity,
            )
            ranked_out.append(
                (
                    score,
                    int(bool(record.stock_code)),
                    record.modify_date,
                    corp_code,
                    {
                        "candidate_name": candidate_name,
                        "address": address,
                        "homepage": homepage,
                        "source_label": "전자공시(DART) 기업개황",
                        "source_url": "https://opendart.fss.or.kr/",
                        "provider_name": "DART",
                        "candidate_ref": corp_code,
                        "stock_code": record.stock_code,
                        "modify_date": record.modify_date,
                        "english_name": record.corp_eng_name,
                        "name_match_kind": match.match_kind,
                        "name_similarity": match.similarity,
                    },
                )
            )
        ranked_out.sort(
            key=lambda item: (
                -item[0],
                -item[1],
                -(int(item[2]) if re.fullmatch(r"\d{8}", item[2]) else 0),
                item[3],
            )
        )
        return [item[4] for item in ranked_out[:display_cap]]

    def find_company(self, user_input: UserInput) -> Optional[CompanyCard]:
        """2 식별 → 3 확인 카드까지만 한다. 여기서 멈추고 사람에게 보여준다."""
        return self.find_company_metered(user_input).card

    def find_company_by_ref_metered(
        self, user_input: UserInput, candidate_ref: str
    ) -> CompanyLookupResult:
        """사람이 고른 DART 고유번호를 이름 재식별 없이 다시 확인한다.

        후보 점수는 회사를 확정하지 않는다. 후보 화면에서 사용자가 고른 뒤 서명 검증을
        통과한 고유번호만 이 경로로 들어오며, DART 기업개황을 다시 읽어 확인 카드를
        만든다. 이름 식별 AI는 호출하지 않는다.
        """
        corp_code = str(candidate_ref or "").strip()
        if re.fullmatch(r"[0-9]{8}", corp_code) is None:
            return CompanyLookupResult(card=None, failed=True)

        engine = _MeteredEngine(_engine())
        engine.load_env()
        counter = engine.UsageCounter()
        try:
            profile = engine.get_json(
                "company.json", {"corp_code": corp_code}, counter
            )
            if not isinstance(profile, dict) or profile.get("status") != DART_SUCCESS_STATUS:
                return CompanyLookupResult(card=None, failed=True)
            address = str(profile.get("adres") or "").strip()
            region = user_input.region.strip() or "모름"
            warning = ""
            if region != "모름" and not _region_matches(region, address):
                warning = (
                    f"입력하신 지역({region})과 본사 주소가 다릅니다. "
                    "지사·공장이거나 최근 이전했을 수 있습니다."
                )
            card = CompanyCard(
                legal_name=str(profile.get("corp_name") or user_input.company),
                typed_name=user_input.company,
                address=address,
                ceo=str(profile.get("ceo_nm") or ""),
                founded=str(profile.get("est_dt") or ""),
                homepage=str(profile.get("hm_url") or ""),
                homepage_url=_homepage_url_for_display(profile.get("hm_url", "")),
                region_warning=warning,
                ref=corp_code,
            )
        except Exception:  # noqa: BLE001 — DART 기술 실패를 회사 없음으로 바꾸지 않는다
            logger.exception("선택한 DART 회사 후보를 다시 확인하지 못했습니다")
            return CompanyLookupResult(card=None, failed=True)
        return CompanyLookupResult(card=card)

    def find_company_metered(self, user_input: UserInput) -> CompanyLookupResult:
        """회사 확인 카드와 식별 AI 비용을 한 번에 돌려준다.

        ★ 비용을 브라우저의 숨은 입력칸으로 보내지 않는다. 웹 서버가 이 결과를
          곧바로 원장에 적고, 확인 카드와 본조사를 같은 요청 번호로 잇는다.
        """
        engine = _MeteredEngine(_engine())
        engine.load_env()

        client = _metered_client(engine, engine._client())
        counter = engine.UsageCounter()
        index = _company_index()
        steps: list[dict[str, Any]] = []
        card: Optional[CompanyCard] = None
        failed = False

        try:
            region = user_input.region.strip() or "모름"
            _set_meter_stage(engine, "company_identification")
            corp_code = engine.identify(
                client, user_input.company, region, index, counter, steps
            )
            if corp_code is not None:
                profile = engine.get_json(
                    "company.json", {"corp_code": corp_code}, counter
                )
                if profile.get("status") == DART_SUCCESS_STATUS:
                    address = (profile.get("adres") or "").strip()
                    same_name = max(
                        (s.get("후보수", 1) for s in steps if s.get("후보수")),
                        default=1,
                    )
                    warning = ""
                    if region != "모름" and not _region_matches(region, address):
                        warning = (
                            f"입력하신 지역({region})과 본사 주소가 다릅니다. "
                            "지사·공장이거나 최근 이전했을 수 있습니다."
                        )
                    card = CompanyCard(
                        legal_name=profile.get("corp_name", user_input.company),
                        typed_name=user_input.company,
                        address=address,
                        ceo=profile.get("ceo_nm", ""),
                        founded=profile.get("est_dt", ""),
                        homepage=profile.get("hm_url", ""),
                        homepage_url=_homepage_url_for_display(
                            profile.get("hm_url", "")
                        ),
                        region_warning=warning,
                        same_name_count=int(same_name or 1),
                        ref=corp_code,
                    )
                else:
                    # 법인 코드는 찾았는데 DART 회사 응답이 실패한 것이다. 이를
                    # 「그 회사가 없음」으로 기록하면 반대 방향의 거짓말이 된다.
                    failed = True
        except Exception:  # noqa: BLE001 — 앞에서 쓴 비용을 버리지 않고 실패로 돌려준다
            logger.exception("회사 식별 중 실패했습니다")
            failed = True

        return CompanyLookupResult(
            card=card,
            cost_krw=_request_spent_krw(engine),
            model=_request_model_label(engine),
            failed=failed or _request_billing_uncertain(engine),
            billing_uncertain=_request_billing_uncertain(engine),
            ai_cost_events=_request_cost_events(engine),
        )

    def run(
        self,
        user_input: UserInput,
        card: CompanyCard,
        on_step: Optional[StepReporter] = None,
    ) -> RunResult:
        """5 판정부터 13 출력까지 돌리고 예외 때도 이미 쓴 비용을 보존한다."""
        # lifespan을 거치지 않는 CLI·단위시험도 요청을 시작하는 이 자리에서
        # exact 모드를 한 번만 동결한다. 아래 캐시·분기에는 이 값만 운반한다.
        generation_mode = engine_mode.process_engine_mode()
        engine = _MeteredEngine(_engine())
        frozen_identity = generation_coordination.frozen_engine_build_identity()
        if frozen_identity is None:
            build_identity = engine_build_identity.process_engine_build_identity()
        else:
            try:
                build_identity = engine_build_identity.require_exact_engine_build_identity(
                    frozen_identity
                )
            except (TypeError, ValueError) as exc:
                raise generation_coordination.GenerationCoordinationError(
                    "웹 Job이 고정한 엔진 빌드 신원 형식이 올바르지 않습니다"
                ) from exc
        engine_build_identity.assert_engine_build_identity_current(build_identity)
        if not build_identity.cache_usable:
            raise generation_coordination.GenerationCoordinationError(
                "유료 생성을 시작하려면 정상 배포 epoch 영수증이 필요합니다"
            )
        try:
            result = self._run_metered(
                user_input,
                card,
                on_step,
                engine=engine,
                build_identity=build_identity,
                generation_mode=generation_mode,
            )
        except generation_coordination.GenerationCoordinationError as stopped:
            if type(stopped) is generation_coordination.GenerationCoordinationError:
                # 이 pipeline 안에서 직접 올리는 기본형 예외는 재사용 저장본 계약
                # 위반 같은 fail-closed 조건이다. 요청 전역 중단(초대 링크 닫힘·
                # lease 상실·대기 취소 — 전부 하위 클래스)과 달리 실행기에 넘길
                # 전용 분기가 없으므로, 예전 계약대로 실패 결과로 닫는다. 비용은
                # 아래 단일 출구가 싣는다(여기서 따로 return 하지 않는다).
                logger.warning(
                    "본조사를 계약 위반으로 닫습니다 — %s", stopped, exc_info=True
                )
                result = RunResult(
                    outcome=Outcome.FAILED,
                    message=_message(Outcome.FAILED),
                )
            else:
                # 초대 링크 중단·lease 상실·대기 취소는 조사가 «못 한» 것이지
                # 「품질이 모자란」 것이 아니다. 여기서 FAILED 결과로 바꾸면 실행기의
                # 전용 중단 분기에 닿지 못해 이력 사유와 화면 문구가 뒤바뀐다.
                # 그때까지 실제로 쓴 값만 예외에 실어 그대로 다시 던진다.
                logger.info(
                    "본조사를 요청 전역 사유로 중단했습니다 — %s",
                    type(stopped).__name__,
                )
                setattr(
                    stopped,
                    STOPPED_RUN_USAGE_ATTR,
                    RunResult(
                        outcome=Outcome.FAILED,
                        cost_krw=_request_spent_krw(engine),
                        model=_request_model_label(engine),
                        billing_uncertain=_request_billing_uncertain(engine),
                        ai_cost_events=_request_cost_events(engine),
                    ),
                )
                raise
        except Exception:  # noqa: BLE001 — AI 뒤 후속 코드가 터져도 쓴 돈은 0원이 아니다
            logger.exception("본조사 중 예기치 않은 실패가 발생했습니다")
            result = RunResult(
                outcome=Outcome.FAILED,
                message=_message(Outcome.FAILED),
            )
        # 어느 조기 종료로 나왔든 비용·모델은 한 요청의 client 응답을 정본으로 삼는다.
        # 단계별 return에 따로 적으면 새 종료가 추가될 때 한 곳은 반드시 빠진다.
        return replace(
            result,
            cost_krw=_request_spent_krw(engine),
            model=_request_model_label(engine),
            billing_uncertain=_request_billing_uncertain(engine),
            ai_cost_events=_request_cost_events(engine),
        )

    def _run_metered(
        self,
        user_input: UserInput,
        card: CompanyCard,
        on_step: Optional[StepReporter],
        *,
        engine: _MeteredEngine,
        build_identity: Any,
        generation_mode: engine_mode.EngineMode,
    ) -> RunResult:
        """본조사 본체. `_MeteredEngine`이 이 요청의 AI 사용량만 모은다.

        ★ 식별(2)은 다시 하지 않는다. `card.ref`에 이미 답이 있다.
          다시 하면 AI 5회가 통째로 또 나간다.
        """
        generation_mode = engine_mode.assert_engine_mode_current(generation_mode)

        def tell(key: str) -> None:
            _set_meter_stage(engine, key)
            if on_step is not None:
                on_step(key)

        engine.load_env()
        client = _metered_client(engine, engine._client())
        counter = engine.UsageCounter()
        steps: list[dict[str, Any]] = []
        model = getattr(engine, "MODEL", "")

        tell("identify")   # 이미 끝났다 — 화면에는 지나간 단계로 표시된다
        corp_code = card.ref
        if not corp_code:
            return RunResult(outcome=Outcome.NOT_FOUND, message=_message(Outcome.NOT_FOUND))
        profile = engine.get_json("company.json", {"corp_code": corp_code}, counter)
        if not isinstance(profile, dict) or profile.get("status") != DART_SUCCESS_STATUS:
            # 법인 코드를 사람이 확인한 뒤에도 DART가 한도·인증 오류를 돌려줄 수 있다.
            # 빈 회사정보로 계속 가면 기술 실패를 실제 기업 성격으로 오판한다.
            raise RuntimeError("DART 회사정보 응답이 정상 상태가 아닙니다")
        company_name = (
            card.legal_name.strip()
            or str(profile.get("corp_name") or "").strip()
            or user_input.company.strip()
        )

        # ── 5 판정 (전부 코드 · AI 0회) ──────────────────
        tell("judge")
        business_date = today_kst()
        end = business_date
        audit = engine.get_json(
            "list.json",
            {
                "corp_code": corp_code,
                "bgn_de": subtract_years(end, AUDIT_WINDOW_YEARS).strftime("%Y%m%d"),
                "end_de": end.strftime("%Y%m%d"),
                "pblntf_ty": "F",
                "page_count": "100",
            },
            counter,
        )
        if not isinstance(audit, dict):
            raise RuntimeError("DART 공시목록 응답 모양이 올바르지 않습니다")
        audit_status = audit.get("status")
        if not isinstance(audit_status, str):
            raise RuntimeError("DART 공시목록 상태값 모양이 올바르지 않습니다")
        audit_rows = audit.get("list")
        audit_no_data = audit_status == "013"
        if audit_status == "013":
            # DART의 013만 「조회 범위에 자료 없음」이라는 정상 빈 결과다. 모순된
            # 013+비빈 목록은 공급자 응답 이상이므로 거부로 조용히 접지 않는다.
            if audit_rows not in (None, []):
                raise RuntimeError("DART 공시목록 013 응답에 비어 있지 않은 목록이 있습니다")
            audit_rows = []
        elif audit_status != DART_SUCCESS_STATUS:
            # 오류 응답에는 목록이 없지만 그것은 「감사보고서가 없음」의 증거가 아니다.
            # 빈 목록으로 접으면 비상장 회사를 거부B로 거짓 분류하므로 즉시 실패한다.
            raise RuntimeError("DART 공시목록 응답이 정상 상태가 아닙니다")
        if not isinstance(audit_rows, list) or not all(
            isinstance(row, dict) for row in audit_rows
        ):
            raise RuntimeError("DART 공시목록 성공 응답의 목록 모양이 올바르지 않습니다")
        registry = engine.load_public_org_registry(engine.PUBLIC_ORG_REGISTRY)
        has_audit = any(
            "감사보고서" in (row.get("report_nm") or "") for row in audit_rows
        )
        # ── 조건 2-b: 공개된 재무제표가 «실제로» 있나 ─────
        #
        # ★ 왜 판정 «전»으로 올렸나 — 「감사보고서라는 이름의 공시가 없다」는
        #   「분석할 자료가 없다」가 아니다. 사업보고서를 내는 회사는 감사보고서를
        #   그 «안에» 첨부하므로 별도 공시가 안 생긴다(외부감사법 23조① 단서 —
        #   첨부해 내면 감사인이 제출한 것으로 «본다»).
        #   실측: 현대카드·우리은행·현대캐피탈·SC제일은행·토스·야놀자가
        #   그래서 거부됐는데, 재무 API 는 20~38개 계정을 정상으로 준다.
        #   이름난 비상장사 13곳을 재보니 7곳이 이 갈래로 되살아난다.
        #   → 물어야 할 것은 「감사보고서가 있나」가 아니라
        #     **「분석할 재무 자료가 실제로 있나」**다. 이 제품이 거부하는 이유가 그것이다.
        #
        # ★ 「감사보고서」 갈래를 «안» 없앤 이유 — 없애면 회귀한다(실측).
        #   삼성디스플레이·쿠팡·우아한형제들은 감사보고서는 있는데 재무 API 는
        #   자료가 없다. 두 갈래는 서로 다른 회사를 살린다. 대체가 아니라 «추가»다.
        #
        # ★ 값은 아래에서 그대로 재사용한다 — 같은 것을 두 번 받지 않는다.
        #   전자공시 조회일 뿐 **AI 는 안 부른다**(0원).
        # ★ 여기서 오류가 나면 «거부»가 아니라 실패로 터진다 — 위 공시목록과 같은
        #   원칙이다. 기술 실패를 「자료가 없음」으로 접으면 거짓 분류가 된다.
        financials, fin_years = engine.fetch_financials(
            corp_code,
            counter,
            business_date=business_date,
        )
        judgment = engine.decide(
            profile.get("corp_cls", ""),
            has_audit,
            profile.get("bizr_no"),
            lambda b: engine.match_public_org(b, registry),
            has_financial_statements=bool(fin_years),
        )
        # 엔진 표기를 이력 정본 표기로 «여기서 한 번만» 맞춘다. 아래에서 이 값을
        # 싣는 자리가 스무 곳이 넘어서, 각자 고치면 반드시 한 곳이 빠진다.
        corp_type = _canonical_corp_type(judgment.corp_type)
        if judgment.status != "대상":
            outcome = _reject_outcome(judgment.status)
            return RunResult(
                outcome=outcome,
                message=_message(outcome),
                sources=(
                    [
                        SourceStatus(
                            "전자공시",
                            "none",
                            "최근 3년 안에 공개된 재무 자료가 없습니다",
                        )
                    ]
                    if audit_no_data
                    else []
                ),
                corp_type=corp_type,
                cost_krw=_request_spent_krw(engine),
                model=model,
            )

        # ── 회사분석 전용 캐시 확인 ──────────────────────
        # 직무·공고를 읽거나 OCR하지 않는다. 저장소 스키마는 옛 payload를 읽기
        # 위해 유지하되, 전용 제품/스키마 버전 키로 기존 채용 보고서와 격리한다.
        # ★ 재무 API·최신 공시를 여기서 미리 부르는 이유 — 신선도(O9)가
        #   「저장 당시 사업연도 == 지금 최신 사업연도」를 보기 때문이다.
        #   둘 다 전자공시 조회일 뿐 **AI는 안 부른다**(0원). 미적중이면
        #   6 수집에 그대로 넘겨 같은 것을 두 번 받지 않는다.
        # financials·fin_years 는 위 「조건 2-b」에서 이미 받아 두었다 (두 번 안 받는다).
        filing = engine.latest_report_rcept(
            corp_code,
            corp_type,
            counter,
            business_date=business_date,
        )
        # FULL의 formal collector·비교 생산기·legacy 매출표가 같은
        # 접수번호 원문을 각자 다운로드하던 경로를 요청 단위 정본
        # artifact로 합친다. 엔진 내부 디스크 캐시에 숨지 않고,
        # ``download_document`` 호출 자체가 접수번호당 1회만 발생한다.
        downloaded_document_artifacts: dict[str, Any] = {}

        def download_document_once(
            receipt_number: str,
            directory: Any,
            request_counter: Any,
            *,
            require_official_url_sidecar: bool = False,
        ) -> Any:
            receipt = str(receipt_number or "").strip()
            if receipt not in downloaded_document_artifacts:
                # FULL은 첫 요청이 비교/legacy의 약한 호출이어도
                # 항상 sidecar 검증까지 한 강한 artifact를 만든다. 그렇지
                # 않으면 non-strict path를 나중의 strict 수집에 재사용해
                # 공식 URL 출처 검사를 통과한 척하게 된다.
                downloaded_document_artifacts[receipt] = engine.download_document(
                    receipt,
                    directory,
                    request_counter,
                    require_official_url_sidecar=True,
                )
            return downloaded_document_artifacts[receipt]

        source_identity = ReportSourceIdentity.capture(
            filing=filing,
            financial_payload=financials,
        )
        current_fiscal_year = _current_fiscal_year(fin_years, filing)
        # 재사용 판정에 쓸 «지금 요청의 릴리스 모드». 두 재사용 겹(아래
        # coordination과 옛 1층 캐시)이 같은 값을 봐야 한 겹만 막히는 일이
        # 없다(C6).
        requested_release_mode = _requested_release_mode(generation_mode)
        official_evidence: OfficialEvidenceCollectionResult | None = None
        v2_comparison_result: Any = None
        generation_source_identity_digest = source_identity.cache_digest
        if (
            generation_mode is engine_mode.EngineMode.V2
            and requested_release_mode is ReleaseMode.FULL
        ):
            if self._official_evidence_collector is None:
                # FULL의 의미가 조립 실수 하나로 옛 legacy 경로로 강등되면
                # 같은 환경값인데도 호출 위치에 따라 안전 계약이 달라진다.
                # 정식 collector가 없는 FULL은 cache/coordination/provider보다
                # 먼저 닫고 회사 자료 부족으로 오표기하지 않는다.
                return RunResult(
                    outcome=Outcome.GATE_STOPPED,
                    message=(
                        "엄격 보고서에 필요한 공식 자료 수집기가 연결되지 않아 "
                        "AI 작성 전에 멈췄습니다."
                        + _stop_reason_note(
                            FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
                        )
                    ),
                    sources=[
                        SourceStatus(
                            "공식 근거 사전검사",
                            "failed",
                            "내부 공식 자료 수집 연결을 확인하지 못했습니다",
                        )
                    ],
                    corp_type=corp_type,
                    cost_krw=_request_spent_krw(engine),
                    model=model,
                    final_gate_reason=FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
                    dart_receipt_numbers=source_identity.dart_receipt_numbers,
                    financial_payload_digest=source_identity.financial_payload_digest,
                )
            # 캐시보다 먼저 실제 공식 자료 snapshot을 확인한다. 그렇지 않으면
            # 홈페이지·IR이 바뀌어도 공시 접수번호와 재무값만 같은 동안 옛
            # 보고서를 새 결과처럼 재사용한다. 아직 provider/유료 phase는 0회다.
            tell("collect")
            try:
                (
                    profile_attestation_source_id,
                    profile_attestation_evidence,
                ) = dart_profile_attestation_material(
                    profile=profile,
                    corp_code=corp_code,
                    company_name=company_name,
                )
                official_evidence = self._official_evidence_collector.collect(
                    OfficialEvidenceCollectionRequest(
                        company_id=corp_code,
                        company_name=company_name,
                        company_aliases=_official_company_aliases(profile),
                        root_homepage_url=str(profile.get("hm_url") or ""),
                        company_registration_numbers=(
                            _official_company_registration_numbers(profile)
                        ),
                        official_candidate_urls=_official_candidate_urls(profile),
                        as_of_date=business_date,
                        dart_document_cache_dir=Path(engine.RAW_DIR),
                        dart_counter=counter,
                        dart_get_json=engine.get_json,
                        dart_download_document=download_document_once,
                        domain_attestation_source_id=(
                            profile_attestation_source_id
                        ),
                        domain_attestation_evidence=profile_attestation_evidence,
                    )
                )
                official_evidence = add_stated_differentiator_fragments(
                    official_evidence,
                    company_name=company_name,
                    company_aliases=_official_company_aliases(profile),
                )
                official_preflight = assess_official_evidence(official_evidence)
            except Exception as error:  # noqa: BLE001 - 닫힌 타입으로만 아래서 분류
                # traceback에는 provider URL·응답 원문·인증 설명이 섞일 수 있다.
                # 로그·영속 결과에는 닫힌 타입명과 안전 사유만 남긴다.
                if _comparison_source_failure_is_configuration(error):
                    failure_reason = FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION
                    logger.error(
                        "공식 근거 수집 DART 접근 설정 오류 kind=%s",
                        type(error).__name__,
                    )
                    failure_message = (
                        "공식 자료 접근 설정을 확인하지 못해 AI 작성 전에 "
                        "멈췄습니다."
                    )
                    failure_detail = "운영자의 DART 접근 설정 확인이 필요합니다"
                elif _comparison_source_failure_is_transient(error):
                    failure_reason = FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT
                    logger.warning(
                        "공식 근거 수집 DART 일시 장애 kind=%s",
                        type(error).__name__,
                    )
                    failure_message = (
                        "공식 자료 제공처의 일시 장애로 확인을 끝내지 못해 "
                        "AI 작성 전에 멈췄습니다."
                    )
                    failure_detail = "DART 공식 자료 확인을 지금 완료하지 못했습니다"
                else:
                    failure_reason = FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
                    logger.error(
                        "공식 근거 수집·계약 결속 내부 오류 kind=%s",
                        type(error).__name__,
                    )
                    failure_message = (
                        "공식 자료를 보고서에 연결하는 내부 검사를 통과하지 못해 "
                        "AI 작성 전에 멈췄습니다."
                    )
                    failure_detail = "내부 근거 연결을 확인하지 못했습니다"
                return RunResult(
                    outcome=Outcome.GATE_STOPPED,
                    message=failure_message + _stop_reason_note(failure_reason),
                    sources=[
                        SourceStatus(
                            "공식 근거 사전검사",
                            "failed",
                            failure_detail,
                        )
                    ],
                    corp_type=corp_type,
                    cost_krw=_request_spent_krw(engine),
                    model=model,
                    final_gate_reason=failure_reason,
                    dart_receipt_numbers=source_identity.dart_receipt_numbers,
                    financial_payload_digest=source_identity.financial_payload_digest,
                )

            steps.append(
                {
                    "step": "6_수집_공식근거사전검사",
                    "후보장": len(official_evidence.candidates),
                    "준비장": len(official_preflight.decision.ready_section_ids),
                    "독립문서수": official_preflight.independent_document_count,
                    "판정": official_preflight.decision.status.value,
                    "사유코드": official_preflight.detail_code,
                    "DART부분보고서전환": (
                        official_preflight.dart_partial_fallback
                    ),
                }
            )
            if not official_preflight.can_call_ai:
                gate_reason = classify_v2_validation_final_gate_reason(
                    (official_preflight.detail_code,)
                )
                transient = gate_reason == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT
                classification_undetermined = (
                    gate_reason
                    == FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED
                )
                return RunResult(
                    outcome=Outcome.GATE_STOPPED,
                    message=(
                        (
                            "공식 자료의 필수 확인 경로를 끝까지 확인하지 못해 "
                            if transient
                            else (
                                "공식 자료는 읽었지만 필요한 내용인지 자동으로 "
                                "확인하지 못해 "
                                if classification_undetermined
                                else "공식 자료를 모두 확인했지만 완성 보고서의 "
                                "최소 근거가 부족해 "
                            )
                        )
                        + "확인되지 않은 내용을 만들지 않고 AI 작성 전에 멈췄습니다."
                        + _stop_reason_note(gate_reason)
                    ),
                    sources=[
                        _official_evidence_stop_source(
                            official_preflight,
                            gate_reason=gate_reason,
                        )
                    ],
                    corp_type=corp_type,
                    fragments_collected=sum(
                        len(candidate.fragments)
                        for candidate in official_evidence.candidates
                    ),
                    cost_krw=_request_spent_krw(engine),
                    model=model,
                    final_gate_reason=gate_reason,
                    dart_receipt_numbers=source_identity.dart_receipt_numbers,
                    financial_payload_digest=source_identity.financial_payload_digest,
                )

            if official_preflight.dart_partial_fallback:
                # FULL은 아홉 장·독립 문서 8건을 모두 요구한다. 사전검사가 부분
                # 보고서 갈래(웹 경로 일시 장애 / 자료 일부 부족 / 정식 문서 하한
                # 도달 불가)로 열어 준 경우에는 이미 존재하는 SHADOW의 안전한
                # PARTIAL 출고 계약으로만 전환한다. 근거 없는 장은 최종 검증에서
                # 제외되고, FULL 표시나 FULL 생산 영수증을 가장하지 않는다.
                # ★ 사유는 사전검사가 고른 닫힌 코드를 그대로 싣는다 — 여기에 문구를
                #   박아 두면 갈래가 늘 때마다 운영 기록이 거짓말을 한다(2026-09-05
                #   우리은행: 웹 장애가 아닌데 «웹 경로 일시 장애»로 찍혔을 뻔했다).
                requested_release_mode = ReleaseMode.SHADOW
                steps.append(
                    {
                        "step": "6_수집_DART부분보고서전환",
                        "사유코드": official_preflight.dart_partial_reason,
                    }
                )

            generation_source_identity_digest = (
                source_identity.cache_digest_with_official_snapshot(
                    official_evidence.source_snapshot_sha256
                )
            )
            if not generation_source_identity_digest:
                # ★ 감사보고서만 내는 비상장사처럼 DART 재무 API가 세 사업연도 모두
                #   «자료 없음(013)»을 답한 회사는 재무 도장이 비어 캐시 신원이 서지
                #   않는다(2026-09-05 인이지 실측: financial_payload_digest=''). 그건
                #   내부 계약 실패가 아니라 회사 자료의 실제 상태다. 공식 접수번호와
                #   공식 자료 snapshot만으로 생성 신원을 만들고, 캐시 재사용은 그대로
                #   막는다(build_identity.cache_usable=False가 캐시 열쇠를 거절한다).
                #   run_pilot.fetch_financials는 013이 아닌 오류를 예외로 터뜨리므로
                #   여기서 None은 «못 물어봄»이 아니라 «없음»이다.
                generation_source_identity_digest = (
                    source_identity.generation_digest_without_financials(
                        official_evidence.source_snapshot_sha256
                    )
                )
                if generation_source_identity_digest:
                    steps.append(
                        {
                            "step": "6_수집_생성신원_재무자료없음",
                            "설명": (
                                "DART 재무 API가 자료 없음을 답해 재무 도장 없이 "
                                "생성 신원을 만들었습니다. 캐시 재사용은 하지 않습니다."
                            ),
                        }
                    )
            if not generation_source_identity_digest:
                return RunResult(
                    outcome=Outcome.GATE_STOPPED,
                    message=(
                        "공식 자료의 생성 신원을 완전히 확인하지 못해 AI 작성 전에 "
                        "멈췄습니다."
                        + _stop_reason_note(
                            FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
                        )
                    ),
                    sources=[
                        SourceStatus(
                            "공식 근거 사전검사",
                            "failed",
                            "공식 자료 snapshot을 결속하지 못했습니다",
                        )
                    ],
                    corp_type=corp_type,
                    final_gate_reason=FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
                )
        if (
            generation_mode is engine_mode.EngineMode.V2
            and requested_release_mode is ReleaseMode.FULL
        ):
            # 9장도 나머지 장과 같은 생산 입력이다. 캐시 뒤·AI 뒤에 붙이면
            # 비교 근거가 없어도 돈을 쓴 뒤 실패하고, 비교사 공시가 바뀌어도
            # 자사 cache key가 그대로 남는다. 실제 공식 비교를 여기서 먼저
            # 만들고 그 snapshot을 생성 신원에 포함한다.
            assert official_evidence is not None
            try:
                v2_comparison_result = _prepare_v2_comparison_result(
                    engine=engine,
                    counter=counter,
                    profile=profile,
                    official_evidence=official_evidence,
                    corp_code=corp_code,
                    company_name=company_name,
                    corp_type=corp_type,
                    financials=financials,
                    filing=filing,
                    business_date=business_date,
                    dart_download_document=download_document_once,
                )
                generation_source_identity_digest = _comparison_generation_digest(
                    generation_source_identity_digest,
                    v2_comparison_result,
                )
            except ComparisonBlockedError:
                logger.info("엔진 v2 회사 차별점 사전검사 차단", exc_info=True)
                steps.append(
                    {
                        "step": "v2_FULL_회사차별점사전검사_차단",
                        "사유코드": FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
                    }
                )
                return RunResult(
                    outcome=Outcome.GATE_STOPPED,
                    message=(
                        "회사 공식 자료에서 자기 선언형 차별점을 확인하지 못해 "
                        "AI 작성 전에 멈췄습니다."
                        + _stop_reason_note(
                            FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT
                        )
                    ),
                    sources=[
                        SourceStatus(
                            "회사 공식 차별점",
                            "none",
                            "회사 주어와 선언 표지가 있는 공식 원문이 부족합니다",
                        )
                    ],
                    corp_type=corp_type,
                    cost_krw=_request_spent_krw(engine),
                    model=model,
                    final_gate_reason=FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
                    dart_receipt_numbers=source_identity.dart_receipt_numbers,
                    financial_payload_digest=source_identity.financial_payload_digest,
                )
            except Exception as error:  # noqa: BLE001 - 아래서 외부 장애를 제한 분류
                if _comparison_source_failure_is_configuration(error):
                    # 인증키·권한은 사용자의 회사나 일시 네트워크 문제가 아니다.
                    # 원문 예외문은 로그·화면·영속 사유 어디에도 복사하지 않는다.
                    logger.error(
                        "엔진 v2 공식 양사 비교 DART 접근 설정 오류 kind=%s",
                        type(error).__name__,
                    )
                    steps.append(
                        {
                            "step": "v2_FULL_공식비교접근설정_차단",
                            "사유코드": (
                                FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION
                            ),
                        }
                    )
                    return RunResult(
                        outcome=Outcome.GATE_STOPPED,
                        message=(
                            "공식 양사 자료의 접근 설정을 확인하지 못해 "
                            "AI 작성 전에 멈췄습니다."
                            + _stop_reason_note(
                                FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION
                            )
                        ),
                        sources=[
                            SourceStatus(
                                "공식 양사 비교",
                                "failed",
                                "운영자의 DART 접근 설정 확인이 필요합니다",
                            )
                        ],
                        corp_type=corp_type,
                        cost_krw=_request_spent_krw(engine),
                        model=model,
                        final_gate_reason=(
                            FINAL_GATE_REASON_OFFICIAL_EVIDENCE_CONFIGURATION
                        ),
                        dart_receipt_numbers=source_identity.dart_receipt_numbers,
                        financial_payload_digest=(
                            source_identity.financial_payload_digest
                        ),
                    )
                if _comparison_source_failure_is_transient(error):
                    # 예외 문자열·URL·응답 원문은 로그·결과에 싣지 않는다.
                    logger.warning(
                        "엔진 v2 공식 양사 비교 DART 일시 장애 kind=%s",
                        type(error).__name__,
                    )
                    steps.append(
                        {
                            "step": "v2_FULL_공식비교일시장애_차단",
                            "사유코드": FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT,
                        }
                    )
                    return RunResult(
                        outcome=Outcome.GATE_STOPPED,
                        message=(
                            "공식 양사 자료를 확인하는 중 일시 장애가 발생해 "
                            "AI 작성 전에 멈췄습니다."
                            + _stop_reason_note(
                                FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT
                            )
                        ),
                        sources=[
                            SourceStatus(
                                "공식 양사 비교",
                                "failed",
                                "DART 공식 자료 확인을 지금 완료하지 못했습니다",
                            )
                        ],
                        corp_type=corp_type,
                        cost_krw=_request_spent_krw(engine),
                        model=model,
                        final_gate_reason=(
                            FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT
                        ),
                        dart_receipt_numbers=source_identity.dart_receipt_numbers,
                        financial_payload_digest=(
                            source_identity.financial_payload_digest
                        ),
                    )
                # 내부 계약 오류도 traceback을 남기지 않는다. 원인이 가진 외부
                # 원문이나 URL이 예외 체인에 섞였을 수 있기 때문이다.
                logger.error(
                    "엔진 v2 공식 양사 비교 내부 연결 오류 kind=%s",
                    type(error).__name__,
                )
                steps.append(
                    {
                        "step": "v2_FULL_공식비교transport_차단",
                        "사유코드": FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
                    }
                )
                return RunResult(
                    outcome=Outcome.GATE_STOPPED,
                    message=(
                        "공식 양사 자료를 보고서 근거에 연결하는 내부 검사를 "
                        "통과하지 못해 AI 작성 전에 멈췄습니다."
                        + _stop_reason_note(
                            FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
                        )
                    ),
                    sources=[
                        SourceStatus(
                            "공식 양사 비교",
                            "failed",
                            "내부 비교 근거 연결을 확인하지 못했습니다",
                        )
                    ],
                    corp_type=corp_type,
                    cost_krw=_request_spent_krw(engine),
                    model=model,
                    final_gate_reason=FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
                    dart_receipt_numbers=source_identity.dart_receipt_numbers,
                    financial_payload_digest=source_identity.financial_payload_digest,
                )
        # 불변 content+PDF 캐시는 옛 layer1보다 먼저 본다. 새 계약의 hit이면
        # 최초 원본 ID를 그대로 운반하고, miss면 같은 열쇠로 owner lease를
        # 먼저 얻는다. 옛 layer1에는 생성 당시 배포·모델·설정 신원이 없으므로
        # 실제 paid 경로에서 현재 namespace 결과로 «승격»하지 않는다. 그렇게
        # 하면 옛 본문을 현재 코드가 만든 것처럼 거짓 표기하게 된다. 한 번
        # 명시적 miss로 새로 만들고, 그때부터 정확한 불변 원본을 재사용한다.
        generation_namespace = _generation_cache_namespace(
            engine,
            build_identity,
            generation_mode,
            release_mode=requested_release_mode,
        )
        reused_generation = generation_coordination.coordinate(
            corp_id=corp_code,
            cache_namespace=generation_namespace,
            preflight_identity_digest=generation_source_identity_digest,
        )
        reused_release_mode = str(
            getattr(getattr(reused_generation, "report", None), "release_mode", "")
            or ""
        )
        if reused_generation is not None and not (
            cache_store.reusable_for_requested_release_mode(
                reused_release_mode, requested_release_mode
            )
        ):
            # ★ 여기까지 오면 안 된다 (C6). 조정자는 요청 모드와 다른 저장본을
            #   히트로 돌려주지 않고 미적중으로 닫아 owner 선정으로 내려간다
            #   (`web/generation_singleflight.py`의 coordinate). 그런데도 여기
            #   걸렸다면 조정자와 이 판정이 갈라진 것이다.
            # ★ 값만 버리면 안 된다 — 조정자는 이미 「캐시 재사용」 상태로
            #   굳었고, 그 상태에서는 유료 단계를 열 수 없어 요청이 뒤늦게
            #   통째로 실패한다. 조용히 버리는 대신 닫고 끝낸다.
            raise generation_coordination.GenerationCoordinationError(
                "재사용 보고서가 이번 요청의 공개 기준으로 만들어지지 않았습니다"
            )
        if reused_generation is not None:
            reused_report = reused_generation.report
            if not isinstance(reused_report, Report):
                raise generation_coordination.GenerationCoordinationError(
                    "재사용 보고서가 pipeline 계약이 아닙니다"
                )
            expected_schema = (
                generation_namespace.schema_version
                if generation_namespace is not None
                else ""
            )
            if not expected_schema or reused_report.schema_version != expected_schema:
                raise generation_coordination.GenerationCoordinationError(
                    "재사용 보고서의 생성기 schema가 현재 요청과 다릅니다"
                )
            metrics = reused_report.generation_metrics
            if expected_schema == ENGINE_V2_SCHEMA_VERSION and metrics is None:
                raise generation_coordination.GenerationCoordinationError(
                    "재사용 보고서에 생성 당시의 실제 지표가 없습니다"
                )
            if (
                reused_report.release_mode
                in {
                    ReleaseMode.ENFORCE_NO_PARTIAL.value,
                    ReleaseMode.FULL.value,
                }
                and reused_report.quality_observation is None
            ):
                raise generation_coordination.GenerationCoordinationError(
                    "엄격 재사용 보고서에 생성 당시의 품질 관측이 없습니다"
                )
            tell("output")
            return RunResult(
                outcome=Outcome.REPORT,
                report=reused_report,
                message=CACHE_HIT_MESSAGE.format(
                    generated_at=(
                        reused_report.generated_at or CACHE_HIT_UNKNOWN_DATE
                    )
                ),
                sources=list(reused_report.sources),
                charged=False,
                corp_type=_canonical_corp_type(reused_report.corp_type) or corp_type,
                fragments_collected=(
                    metrics.fragments_collected if metrics is not None else 0
                ),
                fragments_cited=(metrics.fragments_cited if metrics is not None else 0),
                sentences_made=(metrics.sentences_made if metrics is not None else 0),
                sentences_passed=(
                    metrics.sentences_passed if metrics is not None else 0
                ),
                cost_krw=_request_spent_krw(engine),
                model=(
                    MODEL_LABEL_SEPARATOR.join(reused_generation.actual_models)
                    or model
                ),
                cache_hit=CACHE_HIT_LAYER1,
                dart_receipt_numbers=source_identity.dart_receipt_numbers,
                financial_payload_digest=source_identity.financial_payload_digest,
                reused_content_snapshot_id=reused_generation.content_snapshot_id,
                reused_artifact_id=reused_generation.artifact_id,
                generation_cache_eligible=(
                    reused_generation.generation_cache_eligible
                ),
                generation_evidence=reused_report.generation_evidence,
                generation_metrics=metrics,
                quality_observation=reused_report.quality_observation,
            )
        # ★ v1 캐시와 v2 캐시는 «열쇠가 다르다» — 서로의 보고서를 못 꺼낸다.
        #   v2를 켠 요청에 v1 보고서를 돌려주는 것은 조용한 거짓말이고,
        #   그 반대도 마찬가지다.
        # ★ v2 캐시 열쇠에는 «지금 코드의 지문»이 들어간다. 코드가 그대로면
        #   적중해 900원을 아끼고, 한 글자라도 바뀌면 저절로 불일치라
        #   옛 결과가 절대 안 나온다 — 「고쳤는데 화면이 그대로」를 막는다.
        cached = None
        if not generation_coordination.is_active():
            # demo·순수 pipeline 단위 경로는 delivery 원본을 발급하지 않으므로
            # 기존 Report 캐시 호환을 유지한다. 실제 웹은 위 새 계약만 쓴다.
            cached = (
                _v2_cache_lookup(
                    corp_id=corp_code,
                    current_fiscal_year=current_fiscal_year,
                    source_identity_digest=generation_source_identity_digest,
                    build_identity=build_identity,
                    release_mode=requested_release_mode,
                )
                if generation_mode is engine_mode.EngineMode.V2
                else _company_cache_lookup(
                    corp_id=corp_code,
                    current_fiscal_year=current_fiscal_year,
                    source_identity_digest=generation_source_identity_digest,
                    build_identity=build_identity,
                )
            )
        if cached is not None:
            if (
                generation_mode is engine_mode.EngineMode.V2
                and cached.generation_metrics is None
            ):
                logger.warning(
                    "생성 지표가 없는 옛 v2 cache는 0으로 꾸미지 않고 다시 생성합니다"
                )
                cached = None
            elif (
                generation_mode is engine_mode.EngineMode.V2
                and cached.release_mode
                in {
                    ReleaseMode.ENFORCE_NO_PARTIAL.value,
                    ReleaseMode.FULL.value,
                }
                and cached.quality_observation is None
            ):
                logger.warning(
                    "품질 관측이 없는 엄격 v2 cache는 다시 생성합니다"
                )
                cached = None
            elif not cache_store.reusable_for_requested_release_mode(
                str(cached.release_mode or ""), requested_release_mode
            ):
                logger.warning(
                    "FULL 요청에는 FULL로 만든 저장본만 재사용합니다 — 새로 만듭니다"
                )
                cached = None
        if cached is not None:
            metrics = cached.generation_metrics
            tell("output")   # 6~10을 통째로 건너뛴다
            return RunResult(
                outcome=Outcome.REPORT,
                report=cached,
                # ★ 「방금 조사한 것」처럼 보이면 안 된다. 언제 만든 것인지 밝힌다.
                message=CACHE_HIT_MESSAGE.format(
                    generated_at=cached.generated_at or CACHE_HIT_UNKNOWN_DATE
                ),
                # 수집 현황은 «그때 실제로 모은 것»을 그대로 보여준다.
                sources=list(cached.sources),
                # 캐시 반환은 0 차감·무제한.
                charged=False,
                corp_type=_canonical_corp_type(cached.corp_type) or corp_type,
                fragments_collected=(
                    metrics.fragments_collected if metrics is not None else 0
                ),
                fragments_cited=(metrics.fragments_cited if metrics is not None else 0),
                sentences_made=(metrics.sentences_made if metrics is not None else 0),
                sentences_passed=(
                    metrics.sentences_passed if metrics is not None else 0
                ),
                # 이번 요청에서 실제로 쓴 돈 — 신선도 확인을 위한 조회분만 남는다.
                cost_krw=_request_spent_krw(engine),
                model=model,
                # 화면 배지와 대시보드 ⑤가 이 값을 읽는다. 안 실으면 캐시가
                # 돌아도 「재사용 0건」으로 보인다 — 화면이 옛말을 하는 사고다.
                cache_hit=CACHE_HIT_LAYER1,
                dart_receipt_numbers=source_identity.dart_receipt_numbers,
                financial_payload_digest=source_identity.financial_payload_digest,
                # Report-only layer1에는 재사용할 Content/PDF ID가 없다. 웹의
                # 장기 생성 캐시 권위로 승격하지 않는다.
                generation_cache_eligible=False,
                generation_evidence=cached.generation_evidence,
                generation_metrics=metrics,
                quality_observation=cached.quality_observation,
            )

        # ── 6 수집 (AI 0회) ──────────────────────────────
        tell("collect")
        try:
            frags, revenue_tables, filing_text = _collect(
                engine, client, profile, user_input, counter, steps,
                financials=financials, fin_years=fin_years, filing=filing,
                # formal collector가 이미 typed DART를 수집했다면 legacy typed
                # adapter를 다시 돌리지 않는다. 같은 DART 요청·원문을 두 번
                # 가져오고 section/slot을 coarse kind로 잃는 옛 경로를 피한다.
                generation_mode=(
                    None if official_evidence is not None else generation_mode
                ),
                corp_code=corp_code,
                formal_official_evidence=official_evidence,
                dart_download_document=(
                    download_document_once
                    if official_evidence is not None
                    else None
                ),
            )
        except RevenueTableEvidenceBindingError:
            logger.exception("매출 구성표와 공시 원문을 결속하지 못했습니다")
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                message=(
                    "매출 구성표를 공시 원문에 연결하는 내부 검사를 통과하지 "
                    "못해 AI 작성 전에 멈췄습니다."
                    + _stop_reason_note(FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT)
                ),
                sources=_sources_from(steps),
                corp_type=corp_type,
                cost_krw=_request_spent_krw(engine),
                model=model,
                final_gate_reason=FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
            )
        if official_evidence is not None:
            try:
                frags, added_official_fragments = merge_official_evidence_fragments(
                    frags,
                    official_evidence,
                )
            except (TypeError, ValueError):
                logger.exception("공식 근거를 숫자 인용 transport로 옮기지 못했습니다")
                return RunResult(
                    outcome=Outcome.GATE_STOPPED,
                    message=(
                        "공식 자료를 보고서 인용에 연결하는 내부 검사를 통과하지 "
                        "못해 AI 작성 전에 멈췄습니다."
                        + _stop_reason_note(
                            FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
                        )
                    ),
                    sources=_sources_from(steps),
                    corp_type=corp_type,
                    fragments_collected=len(frags),
                    final_gate_reason=FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
                )
            steps.append(
                {
                    "step": "6_수집_공식근거생성입력",
                    "추가조각수": added_official_fragments,
                    "전체조각수": len(frags),
                }
            )
        sources = _sources_from(steps)

        # ── 7 사전 게이트 — 원문 자체가 없으면 생성 전에 멈춘다 ──
        tell("gate")
        if not frags:
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                message=(
                    "이번 조사에서는 공식 자료에서 분석에 쓸 회사 사실을 찾지 못했습니다. "
                    "확인되지 않은 내용을 채우지 않고 여기서 멈췄습니다."
                    + _stop_reason_note(FINAL_GATE_REASON_OTHER_GATE)
                ),
                sources=sources,
                corp_type=corp_type,
                fragments_collected=len(frags),
                cost_krw=_request_spent_krw(engine),
                model=model,
                final_gate_reason=FINAL_GATE_REASON_OTHER_GATE,
            )

        # ── 엔진 v2 분기 (유일한 분기 지점) ──────────────
        # 수집(6)·법인 판정(5)이 끝났고 실적표 재료(financials)가 확보된 지점이다.
        # ENGINE_V2=1일 때만 composer 경로로 간다. 미설정이면 아래 v1 경로 그대로다.
        if generation_mode is engine_mode.EngineMode.V2:
            # packet·표·문서 수처럼 코드만으로 판정할 수 있는 검사는 모두
            # 유료 phase 밖에서 끝낸다. 실제 첫 provider 전송 경계인
            # `_MeteredMessages.create`가 `ensure_paid_phase()`를 호출하므로,
            # 여기서 미리 열면 AI 0회 사전검사 실패도 유료 단계로 기록된다.
            tell("generate")
            v2_result = _run_v2_composer(
                engine=engine,
                client=client,
                company_name=company_name,
                corp_type=corp_type,
                frags=frags,
                financials=financials,
                filing=filing,
                # v1이 2장에 붙이는 매출 구성표를 v2도 받는다. 안 넘기면
                # 표도 «구성 도식»도 통째로 사라진다 (실측 결함).
                revenue_tables=revenue_tables,
                sources=sources,
                business_date=business_date,
                model=model,
                steps=steps,
                corp_id=corp_code,
                current_fiscal_year=current_fiscal_year,
                source_identity_digest=generation_source_identity_digest,
                build_identity=build_identity,
                generation_mode=generation_mode,
                comparison_result=v2_comparison_result,
                release_mode_override=requested_release_mode,
            )
            return replace(
                v2_result,
                dart_receipt_numbers=source_identity.dart_receipt_numbers,
                financial_payload_digest=source_identity.financial_payload_digest,
            )

        # v1도 같은 소유권·비용 순서를 지킨다. 이후 간접 함수가
        # messages.create를 부르더라도 외곽에서 설치한 요청 문맥을 물려받는다.
        generation_coordination.ensure_paid_phase()

        # 유료 span 선택 전에 후보 원문도 정식 provenance 경계에서 한 번 봉인한다.
        # DART 전체 원문과 exact-attested HTTPS 웹·IR만 사전검사에 넣으며, 수집
        # 실패·robots 차단·상한 잘림이 있으면 "후보 없음"으로 확정하지 않는다.
        comparison_preflight: bool | None = None
        try:
            preflight_fragments = register_candidate_sentence_evidence(frags)
            preflight_fragments = register_stated_differentiator_sentence_evidence(
                preflight_fragments,
                company_name=company_name,
                company_aliases=_official_company_aliases(profile),
            )
            preflight_attestation = bind_dart_profile_attestation(
                preflight_fragments,
                profile=profile,
                corp_code=corp_code,
                company_name=company_name,
                collected_on=business_date.isoformat(),
            )
            preflight_fragments = preflight_attestation.fragments
            preflight_sources = build_citations(
                preflight_fragments,
                filing=filing,
                collected_on=business_date,
                company_publisher=company_name,
                confirmed_corp_code=corp_code,
            )
            if preflight_attestation.attester is not None:
                preflight_sources.append(preflight_attestation.attester)
            preflight_sentences = candidate_sentences_from_fragments(
                preflight_fragments,
                preflight_sources,
            )
            candidate_catalog = _records_from_candidate_catalog(_company_catalog())
            sealed_candidates = discover_official_source_candidates(
                preflight_sentences,
                preflight_sources,
                candidate_catalog,
                self_corp_code=corp_code,
                self_company=company_name,
                as_of_date=business_date.isoformat(),
            )
            eligible_preflight_texts = [
                filing_text,
                *(
                    str(fragment.get("원문") or "")
                    for fragment in preflight_fragments.values()
                    if str(fragment.get("종류") or "") == HOMEPAGE_FRAGMENT_KIND
                    and str(fragment.get("도메인근거SourceID") or "").strip()
                    and official_web_currentness_is_usable(
                        source_type="회사 공식 웹",
                        url=str(fragment.get("출처") or ""),
                        published_at=str(
                            fragment.get("문서일")
                            or fragment.get("published_at")
                            or ""
                        ),
                        collected_at=business_date.isoformat(),
                    )
                ),
            ]
            raw_preflight = comparison_candidate_preflight_possible(
                eligible_preflight_texts,
                candidate_catalog,
                self_corp_code=corp_code,
                self_company=company_name,
            )
            scope_complete = _comparison_candidate_scope_complete(
                steps,
                filing=filing,
            )
            if (
                scope_complete
                and raw_preflight is None
                and not any(text.strip() for text in eligible_preflight_texts)
            ):
                # 수집일뿐인 IR PDF는 현재 경쟁관계의 기준일이 아니다. 다른 적격
                # 공식 원문이 전혀 없고 수집 범위가 완결됐으면 유료 호출 전에 닫는다.
                raw_preflight = False
            comparison_preflight = (
                True
                if sealed_candidates
                else False
                if scope_complete and raw_preflight is False
                else None
            )
            steps.append(
                {
                    "step": "7_경쟁사후보_사전검사",
                    "결과": (
                        "possible"
                        if comparison_preflight is True
                        else "none"
                        if comparison_preflight is False
                        else "unknown"
                    ),
                    "봉인후보수": len(sealed_candidates),
                    "후보범위완전": scope_complete,
                }
            )
        except Exception as exc:  # noqa: BLE001 - 기술 실패는 fail-open(unknown)
            comparison_preflight = None
            steps.append(
                {
                    "step": "7_경쟁사후보_사전검사",
                    "결과": "unknown",
                    "오류": type(exc).__name__,
                }
            )
        if comparison_preflight is False:
            steps.append(
                {
                    "step": "7_경쟁사후보_후속생략",
                    "사유": (
                        "자사 공식 가능 원문에 경쟁 표지와 고유 DART 법인명이 "
                        "같은 문장으로 확인되지 않았습니다"
                    ),
                }
            )

        # ── 8 사실 배치 + 9 원문 대조 — 1회 완결이면 종료, 부족할 때만 1회 재선택 ──
        tell("generate")
        generation_attestation = bind_dart_profile_attestation(
            frags,
            profile=profile,
            corp_code=corp_code,
            company_name=company_name,
            collected_on=business_date.isoformat(),
        )
        generation_frags = {
            number: fragment
            for number, fragment in generation_attestation.fragments.items()
            if str(fragment.get("원문") or "").strip()
            and (
                (
                    str(fragment.get("종류") or "")
                    == OFFICIAL_IR_FRAGMENT_KIND
                    and verified_official_ir_fragment_is_usable(
                        fragment,
                        reference_date=business_date.isoformat(),
                    )
                )
                or str(fragment.get("종류") or "")
                not in {HOMEPAGE_FRAGMENT_KIND, OFFICIAL_IR_FRAGMENT_KIND}
                or (
                    str(fragment.get("종류") or "")
                    == HOMEPAGE_FRAGMENT_KIND
                    and official_web_currentness_is_usable(
                    source_type="회사 공식 웹",
                    url=str(fragment.get("출처") or ""),
                    published_at=str(
                        fragment.get("문서일")
                        or fragment.get("published_at")
                        or ""
                    ),
                        collected_at=business_date.isoformat(),
                    )
                )
            )
        }
        selection_diagnostics: list[SpanSelectionRoundDiagnostic] = []
        validated_selection_rounds: list[list[Any]] = []
        kept = []
        minimum_subset = []
        sentences_made = 0
        focus_missing_claim_roles: tuple[str, ...] = ()
        focus_rejection_codes: tuple[str, ...] = ()
        focus_verified_sids: tuple[str, ...] = ()
        # 프로그램이 DART 원수치로 만든 완료 FY 행에는 내부 fact_id 대신
        # 일회성 선택 참조를 붙인다. 모델은 이 참조만 basis_sids로 고르고,
        # 조립기가 검증된 표 FactRecord의 실제 ID로 치환한다.
        financial_cite = _first_fragment_cite(
            frags, kind="재무", text_prefix="주요계정(DART API):"
        )
        performance_table = (
            build_three_year_table(financials, cite=financial_cite)
            if financial_cite
            else None
        )
        performance_bases = historical_performance_basis_options(
            [performance_table] if performance_table is not None else []
        )
        company_identity = " ".join(
            str(value or "").strip()
            for value in (
                company_name,
                profile.get("corp_name"),
                profile.get("corp_name_eng"),
                profile.get("corp_eng_name"),
            )
            if str(value or "").strip()
        )
        if not historical_performance_bases_are_complete(performance_bases):
            steps.append(
                {
                    "step": "8_사실선택_사전중단",
                    "사유": "연속 3개 완료 사업연도 실적표 없음",
                    "AI호출": 0,
                }
            )
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                message=(
                    "연속 3개 완료 사업연도의 공식 실적표를 확보하지 못해 "
                    "기본 보고서를 안전하게 만들 수 없습니다. AI를 반복 호출해도 "
                    "고칠 수 없는 조건이라 비용을 쓰기 전에 멈췄습니다."
                    + _stop_reason_note(
                        FINAL_GATE_REASON_OTHER_GATE,
                        SELECTION_REASON_PREFLIGHT_PERFORMANCE,
                    )
                ),
                sources=sources,
                corp_type=corp_type,
                fragments_collected=len(frags),
                sentences_made=0,
                cost_krw=_request_spent_krw(engine),
                model=model,
                span_selection_diagnostics=(),
                span_selection_result_reason=(
                    SELECTION_REASON_PREFLIGHT_PERFORMANCE
                ),
                final_gate_reason=FINAL_GATE_REASON_OTHER_GATE,
            )
        if not generation_frags:
            steps.append(
                {
                    "step": "8_사실선택_사전중단",
                    "사유": "선택 가능한 공식 원문 후보 없음",
                    "AI호출": 0,
                }
            )
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                message=(
                    "선택할 수 있는 공식 원문 후보가 없어 기본 보고서를 안전하게 "
                    "만들 수 없습니다. 빈 입력으로 AI를 부르지 않고 멈췄습니다."
                    + _stop_reason_note(
                        FINAL_GATE_REASON_OTHER_GATE,
                        SELECTION_REASON_PREFLIGHT_CANDIDATES,
                    )
                ),
                sources=sources,
                corp_type=corp_type,
                fragments_collected=len(frags),
                sentences_made=0,
                cost_krw=_request_spent_krw(engine),
                model=model,
                span_selection_diagnostics=(),
                span_selection_result_reason=SELECTION_REASON_PREFLIGHT_CANDIDATES,
                final_gate_reason=FINAL_GATE_REASON_OTHER_GATE,
            )
        for round_index in range(VOTE_ROUNDS):
            round_step_start = len(steps)
            # 1회차는 뒤에 같은 프롬프트를 다시 쓸 보장이 없고, 2회차는 첫 회의
            # 누락 역할·거절 코드·검증 SID가 들어가 정확한 user text가 달라진다.
            # 전체 user text 한 블록에 cache_control을 붙이면 두 호출 모두 cache
            # write만 생기므로 이 단계는 일반 입력 요금으로 호출한다.
            with _meter_stage(engine, "span_selection"):
                picked, rejected = select_canonical_spans(
                    client,
                    generation_frags,
                    steps,
                    engine=engine, model=GENERATION_MODEL,
                    company=company_identity,
                    historical_performance_bases=performance_bases,
                    focus_missing_claim_roles=focus_missing_claim_roles,
                    focus_rejection_codes=focus_rejection_codes,
                    focus_verified_sids=focus_verified_sids,
                )
            sentences_made += len(picked) + len(rejected)
            diagnostic = round_diagnostic_from_steps(
                steps[round_step_start:],
                round_number=round_index + 1,
            )
            selection_diagnostics.append(diagnostic)
            if not diagnostic.output_limit_reached and not diagnostic.parse_failed:
                validated_selection_rounds.append(picked)
            cumulative_kept = combine_validated_picks(validated_selection_rounds)
            round_subset = (
                basic_report_selection_subset(
                    cumulative_kept,
                    historical_performance_bases=performance_bases,
                )
                if validated_selection_rounds
                else []
            )
            # 마지막 유효 누적 결과를 그대로 따른다. 뒤 회차에서 같은 SID가
            # 다른 원문과 충돌해 제거됐다면, 앞 회차의 오래된 부분집합을 되살려
            # 출고해서는 안 된다.
            minimum_subset = round_subset
            if round_subset and basic_report_selection_is_complete(
                cumulative_kept,
                historical_performance_bases=performance_bases,
            ):
                # 전체 계약이 성립하면 추가 선택 비용을 쓰지 않는다. Writer와
                # 이후 출고 게이트가 실제로 보는 항목도 같은 안전 부분집합이다.
                kept = round_subset
                steps.append(
                    {
                        "step": "8_사실선택_완결",
                        "선택호출": round_index + 1,
                        "추가호출생략": round_index + 1 < VOTE_ROUNDS,
                    }
                )
                break
            # 첫 호출의 검증 통과 사실은 보존하고, 다음 호출에는 원문·자유형
            # 실패 문구 대신 닫힌 누락 역할과 거절 코드만 전달한다. 두 번째
            # 결과도 동일한 원문·구조·회사특이성 검사를 다시 통과해야 누적된다.
            focus_missing_claim_roles = _missing_basic_selection_roles(
                cumulative_kept
            )
            focus_rejection_codes = tuple(
                reason
                for reason, _count in diagnostic.validation_rejection_reason_counts
            )
            focus_verified_sids = tuple(
                sorted(
                    {
                        str(getattr(item, "sid", "") or "")
                        for item in cumulative_kept
                        if str(getattr(item, "sid", "") or "")
                    }
                )
            )
        if not kept and minimum_subset:
            # 두 번의 선택 결과를 합쳐도 전체 장이 성립하지 않으면, 확인된 사실을
            # 폐기하지 않고 최소 계약의 부분 보고서로 넘긴다. 여기서 선택되지 않은
            # 장은 출고 단계가 표준 미확보 사유와 함께 명시적으로 생략한다.
            kept = minimum_subset
            steps.append(
                {
                    "step": "8_사실선택_부분완결",
                    "선택호출": len(selection_diagnostics),
                    "사유": "전체 계약 미충족·검증된 최소 계약 출고",
                }
            )
        selection_result_reason_code = selection_result_reason(
            selection_diagnostics,
            selection_kept=len(kept),
        )
        if not kept:
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                message=(
                    "이번에 수집한 공식 자료에서 핵심 기본 보고서(기업 정체성·"
                    "사업·제품·3개년 변화·성장 전략·운영 구조)에 필요한 회사 "
                    "사실과 연결관계를 모두 확보하지 못했습니다. 확인되지 않은 "
                    "내용을 보고서처럼 보여주지 않고 여기서 멈췄습니다."
                    + _stop_reason_note(
                        FINAL_GATE_REASON_OTHER_GATE,
                        selection_result_reason_code,
                    )
                ),
                sources=sources,
                corp_type=corp_type,
                fragments_collected=len(frags),
                sentences_made=sentences_made,
                cost_krw=_request_spent_krw(engine),
                model=model,
                span_selection_diagnostics=tuple(selection_diagnostics),
                span_selection_result_reason=selection_result_reason_code,
                final_gate_reason=FINAL_GATE_REASON_OTHER_GATE,
            )

        tell("verify")
        # 구조화 표는 해당 장이 단독 소유한다. 같은 수치를 요약·다른 장에 복제하지 않는다.
        tables_by_section: dict[str, list[ReportTable]] = {}
        for table in revenue_tables:
            section_id = revenue_table_section_id_from_caption(table.get("caption"))
            tables_by_section.setdefault(section_id, []).append(ReportTable(**table))

        if performance_table is not None:
            tables_by_section["past_changes"] = [performance_table]

        sections = canonical_sections_from_picks(
            kept,
            generation_frags,
            tables_by_section=tables_by_section,
        )
        sections, written_claims = write_and_verify_sections(
            engine=engine,
            client=client,
            company=company_name,
            sections=sections,
            fragments=generation_frags,
            picks=kept,
            steps=steps,
            model=model,
        )
        # 선택·원문 검증을 이미 통과한 정체성/수익 span만 대상으로 Writer와
        # 독립 Reviewer를 한 번 더 호출한다. 새 수집·재선택·미검수 원문 복사는
        # 하지 않으며, 이 한 번에도 남은 결손은 닫힌 코드로 최종 기록한다.
        sections, written_claims, missing_minimum_roles_after_verify = (
            supplement_missing_minimum_claims_once(
                engine=engine,
                client=client,
                company=company_name,
                sections=sections,
                fragments=generation_frags,
                picks=kept,
                written_claims=written_claims,
                steps=steps,
                model=model,
            )
        )
        provenance_fragments = {
            number: dict(fragment) for number, fragment in frags.items()
        }
        provenance_fragments = register_candidate_sentence_evidence(
            provenance_fragments
        )
        provenance_fragments = register_stated_differentiator_sentence_evidence(
            provenance_fragments,
            company_name=company_name,
            company_aliases=_official_company_aliases(profile),
        )
        profile_attestation = bind_dart_profile_attestation(
            provenance_fragments,
            profile=profile,
            corp_code=corp_code,
            company_name=company_name,
            collected_on=business_date.isoformat(),
        )
        provenance_fragments = profile_attestation.fragments
        # 실적표의 숨은 근거는 공개 행을 다시 이어 붙인 문자열이 아니라 위에서
        # build_three_year_table이 실제 DART API 응답으로 만든 payload다.
        # provenance 생성 단계에만 전달하고 공개 fragment 원문은 바꾸지 않는다.
        if performance_table is not None and performance_table.evidence_rows:
            financial_number = citation_number(performance_table.cite)
            if financial_number:
                number = int(financial_number)
                if number in provenance_fragments:
                    provenance_fragments[number]["근거원문"] = list(
                        dict.fromkeys(performance_table.evidence_rows)
                    )
        selected_evidence: dict[int, list[str]] = {}
        for claim in written_claims:
            selected_evidence.setdefault(claim.fragment_id, []).append(claim.evidence)
        all_citations = build_citations(
            provenance_fragments,
            filing=filing,
            collected_on=business_date,
            company_publisher=company_name,
            confirmed_corp_code=corp_code,
            selected_evidence_by_fragment=selected_evidence,
        )
        if profile_attestation.attester is not None:
            all_citations.append(profile_attestation.attester)
        official_candidate_sentences = candidate_sentences_from_fragments(
            provenance_fragments,
            all_citations,
        )

        def summary_ask(prompt: str, schema: dict[str, Any], max_tokens: int):
            previous = getattr(engine, "MODEL", "")
            try:
                if model:
                    engine.MODEL = model
                payload, usage = engine._ask(client, prompt, schema, max_tokens=max_tokens)
            finally:
                if model:
                    engine.MODEL = previous
            if isinstance(usage, dict):
                usage = {**usage, USAGE_MODEL_KEY: model or previous}
            return payload, usage

        analysis_period, latest_performance_period = _performance_period_labels(
            performance_table,
            filing,
        )
        try:
            report = assemble_report_draft(
                company=company_name,
                corp_type=corp_type,
                sections=sections,
                written_claims=written_claims,
                sources=all_citations,
                steps=steps,
                as_of_date=business_date.isoformat(),
                analysis_period=analysis_period,
                latest_performance_period=latest_performance_period,
            )
            # 1~8장이 사실 장부로 잠긴 뒤에만 비교사를 고른다. 자사 공시 문장에
            # 경쟁 관계가 직접 적힌 DART 법인 1~3곳의 공식 원문을 별도로 받고,
            # 동일 지표·기간·연결범위인 수치가 있을 때만 9장을 붙인다.
            comparison_reasons: tuple[str, ...] = ()
            try:
                report = _attach_competitive_position(
                    report,
                    engine=engine,
                    counter=counter,
                    self_corp_code=corp_code,
                    self_company=company_name,
                    self_financials=financials,
                    self_filing=filing,
                    self_official_text=filing_text,
                    steps=steps,
                    collected_on=business_date.isoformat(),
                    business_date=business_date,
                    official_candidate_sentences=official_candidate_sentences,
                    candidate_source_registry=tuple(all_citations),
                )
            except ComparisonBlockedError as exc:
                comparison_reasons = tuple(exc.reasons)
            if comparison_reasons:
                # 비교 실패는 경쟁우위가 없다는 판정이 아니다. 검증된 1~8장은
                # 기본 보고서로 유지하고, 빈 9장이나 추정 비교 문장은 만들지 않는다.
                steps.append(
                    {
                        "step": "12_회사차별점_조건부생략",
                        "사유": list(comparison_reasons),
                    }
                )
                report = replace(
                    report,
                    grade=Grade.PARTIAL,
                    shortfall_reasons=[COMPARISON_SHORTFALL_REASON],
                )
            # 요약은 필수 1~8장과, 성립한 경우의 9장이 잠긴 뒤 기존 fact_id의
            # 최소 부분집합만으로 생성한다. finalize_report가 최종 출고 게이트와
            # 공개본 생성을 정확히 한 번 수행한다.
            report = finalize_report(
                report,
                summary_ask=summary_ask,
                steps=steps,
            )
        except ComparisonBlockedError as exc:
            steps.append(
                {
                    "step": "12_회사차별점_출고차단",
                    "사유": list(exc.reasons),
                }
            )
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                message=(
                    "회사 공식 자료에서 자기 선언형 차별점을 확인하지 못해 "
                    "보고서를 내보내지 않았습니다. 차별점이 없다는 뜻이 아니라, "
                    "현재 공개 근거로는 확인할 수 없다는 뜻입니다."
                    + _stop_reason_note(FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT)
                ),
                sources=sources,
                corp_type=corp_type,
                fragments_collected=len(frags),
                sentences_made=sentences_made,
                sentences_passed=len(written_claims),
                cost_krw=_request_spent_krw(engine),
                model=model,
                span_selection_diagnostics=tuple(selection_diagnostics),
                span_selection_result_reason=selection_result_reason_code,
                final_gate_reason=FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
            )
        except PublishBlockedError as exc:
            # 출고 차단은 사용자 화면에서는 닫힌 문구로만 보이지만, 운영자가 같은
            # 유료 실행을 추측으로 반복하지 않도록 원문·비밀값 없는 검증 사유는 남긴다.
            logger.warning("canonical 출고 차단: %s", list(exc.validation.reasons))
            steps.append(
                {
                    "step": "13_정본_출고차단",
                    "사유": list(exc.validation.reasons),
                }
            )
            publish_gate_reason = _publish_gate_reason_for_missing_minimum_roles(
                missing_minimum_roles_after_verify
            )
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                message=(
                    "필수 회사 사실과 검증 근거가 충분하지 않아 보고서를 "
                    "내보내지 않았습니다. 확인되지 않은 내용을 정상 보고서처럼 "
                    "보여주지 않습니다."
                    + _stop_reason_note(publish_gate_reason)
                ),
                sources=sources,
                corp_type=corp_type,
                fragments_collected=len(frags),
                sentences_made=sentences_made,
                sentences_passed=len(written_claims),
                cost_krw=_request_spent_krw(engine),
                model=model,
                span_selection_diagnostics=tuple(selection_diagnostics),
                span_selection_result_reason=selection_result_reason_code,
                final_gate_reason=publish_gate_reason,
            )

        # ── 13 출력 ──────────────────────────────────────
        tell("output")

        # ── 14 저장 — 1층 캐시 (저장 구간) ────────
        # 회사분석 전용 버전 키로 저장해 옛 직무·공고 보고서와 섞이지 않는다.
        # ★ 우리 쪽 수집 실패(⚠️)가 낀 결과는 «저장하지 않는다» —
        #   그날만 죽은 소스 때문에 그 회사가 「자료 없는 회사」로 굳는다.
        content_eligible, missing_sections, content_shortfall_reasons = (
            _generation_cache_eligibility(
                report,
                sources=sources,
                steps=steps,
                filing=filing,
            )
        )
        cache_eligible = bool(build_identity.cache_usable and content_eligible)
        if not cache_eligible:
            logger.info(
                "수집 실패·후보범위 불완전·기본 장/내용 결손이 껴 1층 캐시에 "
                "저장하지 않습니다 — corp_id=%s · 장누락=%s · 내용결손=%s",
                corp_code,
                sorted(missing_sections),
                sorted(content_shortfall_reasons),
            )
        elif generation_coordination.is_active():
            # 유료 웹의 정본은 승인된 Content+PDF delivery 캐시 한 벌이다.
            # Report-only layer1은 웹이 읽지 않으므로 두 번째 완료 권위를
            # 만들지 않고, 아래 eligibility만 출고 transaction에 운반한다.
            logger.info(
                "유료 웹은 Report-only 1층 저장을 건너뜁니다 — corp_id=%s",
                corp_code,
            )
        else:
            if generation_mode is not engine_mode.EngineMode.V1:
                raise generation_coordination.GenerationCoordinationError(
                    "v1 캐시 저장에 v1 엔진 모드 영수증이 필요합니다"
                )
            _company_cache_save(
                corp_id=corp_code,
                report=report,
                fiscal_year=current_fiscal_year,
                source_identity_digest=generation_source_identity_digest,
                build_identity=build_identity,
            )

        return RunResult(
            outcome=Outcome.REPORT,
            report=report,
            message=(
                " ".join(report.shortfall_reasons)
                if report.grade is Grade.PARTIAL
                else ""
            ),
            sources=sources,
            charged=True,  # 보고서가 나가면 1 차감 (3분법 · D5 — 부분 보고서도 1)
            corp_type=corp_type,
            fragments_collected=len(frags),
            fragments_cited=len(report.citations),
            sentences_made=sentences_made,
            sentences_passed=len(written_claims),
            cost_krw=_request_spent_krw(engine),
            model=model,
            span_selection_diagnostics=tuple(selection_diagnostics),
            span_selection_result_reason=selection_result_reason_code,
            dart_receipt_numbers=source_identity.dart_receipt_numbers,
            financial_payload_digest=source_identity.financial_payload_digest,
            generation_cache_eligible=cache_eligible,
        )


#: 전자공시 보고서 이름 끝에 붙는 결산 기간 — 「감사보고서 (2025.12)」·
#: 「사업보고서 (2025.12)」. 1판이 실제로 받아 온 116건 전부 이 모양이었다
#: (`analysis_engine/data/pilot/runs*.jsonl` 실측 · 예외 0건).
_FILING_PERIOD_PATTERN = re.compile(r"\((\d{4})\.(\d{2})\)")


def _latest_filing_label(filing: Optional[dict[str, Any]]) -> str:
    """공시 제목의 보고기간과 보고서 종류를 잃지 않고 표시한다."""

    report_name = str((filing or {}).get("report_nm") or "").strip()
    matched = _FILING_PERIOD_PATTERN.search(report_name)
    if matched is None:
        return report_name or "최신 공식 공시 기준"
    year, month = matched.groups()
    if "반기보고서" in report_name:
        period = "반기"
    elif "분기보고서" in report_name:
        period = {"03": "1분기", "09": "3분기"}.get(month, f"{int(month)}월 분기")
    elif "사업보고서" in report_name or "감사보고서" in report_name:
        period = "연간"
    else:
        period = f"{int(month)}월"
    return f"{year}년 {period} 공식 공시"


def _performance_period_labels(
    table: Optional[ReportTable],
    filing: Optional[dict[str, Any]],
) -> tuple[str, str]:
    """실제 표·공시 제목에 적힌 기간만 보고서 메타데이터로 옮긴다."""

    years: list[int] = []
    if table is not None:
        # canonical 실적표는 행=사업연도, 열=지표다. 연도를 헤더에서 읽으면
        # 구형 전치 표를 조용히 허용하게 되므로 첫 번째 열만 기준으로 삼는다.
        for row in table.rows:
            first_cell = row[0] if row else ""
            matched = re.fullmatch(r"\s*(20\d{2})\s*", str(first_cell or ""))
            if matched:
                years.append(int(matched.group(1)))
    latest = _latest_filing_label(filing)
    if years:
        low, high = min(years), max(years)
        analysis = f"{low}~{high} 완료 회계연도"
        return analysis, latest

    return "기준일 전 36개월", latest


def _load_official_comparator_bundle(
    engine: Any,
    counter: Any,
    record: DartCompanyRecord,
    *,
    business_date: Any,
    dart_download_document: Any = None,
) -> OfficialCompanyBundle | None:
    """DART 고유번호 하나의 기업개황·연간 원문·주요계정을 별도로 받는다."""

    profile = engine.get_json("company.json", {"corp_code": record.corp_code}, counter)
    if not isinstance(profile, dict) or profile.get("status") != DART_SUCCESS_STATUS:
        return None
    profile_corp_code = str(profile.get("corp_code") or "").strip()
    if profile_corp_code != record.corp_code:
        # 비교 후보 이름은 catalog, 원문·재무는 요청 corp_code, 표시 발행자는
        # company.json에서 온다. 이 셋의 법인 신원이 갈라진 채 계속하면 다른
        # 회사 이름으로 공식 수치를 봉인할 수 있다. 성공 응답의 corp_code가
        # 없거나 다르면 「비교 자료가 없음」이 아니라 공급자/배선 계약 오류다.
        raise ValueError("비교사 DART 기업개황의 법인 식별자가 요청과 다릅니다")
    official_name = str(profile.get("corp_name") or record.corp_name or "").strip()
    if not official_name:
        return None
    listed = bool(
        str(record.stock_code or "").strip()
        or str(profile.get("stock_code") or "").strip()
        or str(profile.get("corp_cls") or "").strip().upper() in {"Y", "K", "N"}
    )
    filing = engine.latest_report_rcept(
        record.corp_code,
        "상장사" if listed else "비상장 외감",
        counter,
        business_date=business_date,
    )
    financials, _years = engine.fetch_financials(
        record.corp_code,
        counter,
        business_date=business_date,
    )
    official_text = ""
    if filing:
        downloader = dart_download_document or engine.download_document
        path = downloader(filing["rcept_no"], engine.RAW_DIR, counter)
        official_text = engine.read_filing_text(path)
    return OfficialCompanyBundle(
        corp_code=record.corp_code,
        company_name=official_name,
        financials=financials,
        filing=filing,
        official_text=str(official_text or ""),
    )


def _attach_competitive_position(
    report: Report,
    *,
    engine: Any,
    counter: Any,
    self_corp_code: str,
    self_company: str,
    self_financials: Optional[dict[str, Any]],
    self_filing: Optional[dict[str, Any]],
    self_official_text: str,
    steps: list[dict[str, Any]],
    collected_on: str,
    business_date: Any,
    official_candidate_sentences: tuple[OfficialCandidateSentence, ...] = (),
    candidate_source_registry: tuple[Source, ...] = (),
) -> Report:
    """잠긴 1~8장 초안에 공식 양사 비교 9장을 붙여 내부 초안을 돌려준다."""

    comparison = _build_competitive_position_result(
        report,
        engine=engine,
        counter=counter,
        self_corp_code=self_corp_code,
        self_company=self_company,
        self_financials=self_financials,
        self_filing=self_filing,
        self_official_text=self_official_text,
        collected_on=collected_on,
        business_date=business_date,
        official_candidate_sentences=official_candidate_sentences,
        candidate_source_registry=candidate_source_registry,
    )
    if not any(
        fact.claim_type == STATED_DIFFERENTIATOR_CLAIM_TYPE
        for fact in comparison.facts
    ):
        raise ComparisonBlockedError(
            "회사 공식 자료에서 자기 선언형 차별점을 확인하지 못했습니다"
        )
    steps.append(
        {
            "step": "12_회사차별점",
            "후보": [item.record.corp_name for item in comparison.candidates],
            "확정사실": len(comparison.facts),
            "공식출처": len(comparison.sources),
        }
    )
    draft = replace(
        report,
        sections=[
            section
            for section in report.sections
            if section.cell != "competitive_position"
        ]
        + [comparison.section],
        citations=list(
            {
                source.source_id: source
                for source in (*report.citations, *comparison.sources)
                if isinstance(source, Source)
            }.values()
        ),
        fact_records=[
            fact
            for fact in report.fact_records
            if fact.section_owner != "competitive_position"
        ]
        + list(comparison.facts),
        cells={**report.cells, "competitive_position": True},
    )
    # 이 객체는 아직 요약도 최종 gate도 거치지 않은 내부 초안이다. 호출자가
    # 1~9장 전체로 finalize_report를 실행하기 전에는 저장·렌더링하면 안 된다.
    return draft


def _build_competitive_position_result(
    report: Report,
    *,
    engine: Any,
    counter: Any,
    self_corp_code: str,
    self_company: str,
    self_financials: Optional[dict[str, Any]],
    self_filing: Optional[dict[str, Any]],
    self_official_text: str,
    collected_on: str,
    business_date: Any,
    official_candidate_sentences: tuple[OfficialCandidateSentence, ...] = (),
    candidate_source_registry: tuple[Source, ...] = (),
    dart_download_document: Any = None,
) -> Any:
    """V1 Report 부착과 분리한 공용 공식 비교 생산기."""

    records = _records_from_candidate_catalog(_company_catalog())
    self_bundle = OfficialCompanyBundle(
        corp_code=self_corp_code,
        company_name=self_company,
        financials=self_financials,
        filing=self_filing,
        official_text=self_official_text,
    )
    def fetch_comparator(record: DartCompanyRecord) -> OfficialCompanyBundle | None:
        try:
            return _load_official_comparator_bundle(
                engine,
                counter,
                record,
                business_date=business_date,
                dart_download_document=dart_download_document,
            )
        except Exception as error:  # noqa: BLE001 - 실제 DART 계보를 아래서 제한
            if _comparison_source_failure_is_configuration(error):
                # 잘못된 키·권한은 후보 하나의 자료 없음도 일시 장애도 아니다.
                raise ComparisonSourceConfigurationError() from error
            if _comparison_source_failure_is_transient(error):
                # 원문·URL을 새 메시지에 복사하지 않고 타입 경계만 보존한다.
                raise ComparisonSourceTransientError() from error
            # ValueError·미등록 DartClientError 등은 builder가 후보별 실패로
            # 삼키지 못하도록 내부 계약 표지로 올린다.
            raise ComparisonSourceInternalError() from error

    return build_competitive_position(
        report,
        self_bundle=self_bundle,
        catalog=records,
        fetch_comparator=fetch_comparator,
        collected_on=collected_on,
        official_candidate_sentences=official_candidate_sentences,
        candidate_source_registry=candidate_source_registry,
    )


def _filing_fiscal_year(filing: Optional[dict[str, Any]]) -> Optional[int]:
    """공시 보고서 이름에서 사업연도를 읽는다. 「감사보고서 (2025.12)」 → 2025.

    ★ 접수일(`rcept_dt`)을 쓰지 않는다. 접수일은 «언제 냈나»라 결산 연도와
      다르고, 매년 올라가기만 해서 「사업연도가 바뀌었다」를 못 가른다.
    """
    match = _FILING_PERIOD_PATTERN.search((filing or {}).get("report_nm") or "")
    return int(match.group(1)) if match else None


def _current_fiscal_year(
    fiscal_years: list[int], filing: Optional[dict[str, Any]]
) -> Optional[int]:
    """지금 기준 「최신 사업연도」 — 신선도(O9)의 비교 기준값.

    두 군데서 읽어 «더 최신»을 택한다.
      ① 재무 API가 실제로 자료를 준 사업연도들
      ② 최신 공시(사업보고서·감사보고서) 이름 안의 결산 연도

    ★ 왜 둘 다 보나 — 재무 API(단일회사 주요계정)는 **비상장 외감에서 자주
      빈손이다.** 1판 실측 28건 중 13건(전부 비상장 외감)이 그랬다. ①만 쓰면
      그 회사들은 「사업연도를 모름」이 되어 **캐시가 영영 적중하지 않는다.**
    ★ 왜 «더 최신»인가 — 한쪽이 옛 연도에 멈춰 있어도(실측: 재무 API가
      2023에 멈춘 회사) 다른 쪽이 새 자료를 알아채면 캐시가 만료된다.
      「작년 보고서가 계속 나가는」 구멍(신선도 규칙)을 막는 쪽으로 기운다.
    ★ `dt.date.today().year - 1`로 **추측하지 않는다.** 사업보고서는 결산 뒤
      몇 달 지나야 올라오므로 1~3월에는 작년 자료가 없는 것이 정상이다.
      추측하면 그 기간 내내 오판해 캐시가 통째로 무효가 된다.

    Returns:
        최신 사업연도. 두 군데 다 모르면 `None` — 그때는 캐시가
        «신선하다고 우기지 않는다» (`cache.get_layer1_hit` 참고).
    """
    candidates = [year for year in fiscal_years if year]
    from_filing = _filing_fiscal_year(filing)
    if from_filing is not None:
        candidates.append(from_filing)
    return max(candidates) if candidates else None


def _company_cache_lookup(
    *,
    corp_id: str,
    current_fiscal_year: Optional[int],
    source_identity_digest: str,
    build_identity: Any,
) -> Optional[Report]:
    """회사분석 제품 namespace의 캐시만 조회한다."""
    if not corp_id:
        return None
    try:
        with storage_db.connect() as conn:
            hit = cache_store.get_company_report_hit(
                conn,
                corp_id=corp_id,
                build_identity=build_identity,
                source_identity_digest=source_identity_digest,
                current_fiscal_year=current_fiscal_year,
            )
    except Exception:  # noqa: BLE001 — 캐시 실패가 조사를 막으면 안 된다
        logger.exception("회사분석 캐시 조회 실패 — 새로 조사합니다 (corp_id=%s)", corp_id)
        return None

    if hit is None:
        logger.info(
            "회사분석 캐시 미적중 — 새로 만듭니다 (corp_id=%s · 최신사업연도=%s)",
            corp_id,
            current_fiscal_year,
        )
    else:
        logger.info(
            "회사분석 캐시 적중 — 생성·검증 AI를 건너뜁니다 "
            "(corp_id=%s · 조사일=%s)",
            corp_id,
            hit.generated_at,
        )
    return hit


def _v2_cache_lookup(
    *,
    corp_id: str,
    current_fiscal_year: Optional[int],
    source_identity_digest: str,
    build_identity: Any,
    release_mode: Optional[ReleaseMode] = None,
) -> Optional[Report]:
    """엔진 v2 보고서 캐시를 조회한다 (지금 코드 지문이 같을 때만)."""
    if not corp_id:
        return None
    build_id = build_identity.build_id
    try:
        with storage_db.connect() as conn:
            hit = cache_store.get_v2_report_hit(
                conn,
                corp_id=corp_id,
                build_identity=build_identity,
                source_identity_digest=source_identity_digest,
                release_mode=release_mode,
                current_fiscal_year=current_fiscal_year,
            )
    except Exception:  # noqa: BLE001 — 캐시 실패가 조사를 막으면 안 된다
        logger.exception("v2 캐시 조회 실패 — 새로 조사합니다 (corp_id=%s)", corp_id)
        return None

    if hit is None:
        logger.info(
            "v2 캐시 미적중 — 새로 만듭니다 (corp_id=%s · 코드지문=%s)",
            corp_id,
            build_id,
        )
    else:
        logger.info(
            "v2 캐시 적중 — 생성·검증 AI를 건너뜁니다 (corp_id=%s · 조사일=%s)",
            corp_id,
            hit.generated_at,
        )
    return hit


def _v2_cache_save(
    *,
    corp_id: str,
    report: Report,
    fiscal_year: Optional[int],
    source_identity_digest: str,
    build_identity: Any,
    release_mode: Optional[ReleaseMode] = None,
) -> None:
    """v2 보고서를 «그 코드 지문»과 함께 저장한다.

    ★ 저장 실패가 사용자를 막지 않는다. 보고서는 이미 만들어졌다.
    """
    if not corp_id:
        return
    try:
        with storage_db.connect_explicit_commit() as conn:
            cache_store.save_v2_report(
                conn,
                corp_id=corp_id,
                report=report,
                build_identity=build_identity,
                source_identity_digest=source_identity_digest,
                release_mode=release_mode,
                fiscal_year=fiscal_year,
            )
    except Exception:  # noqa: BLE001 — 저장 실패가 사용자를 막으면 안 된다
        logger.exception("v2 캐시 저장 실패 (보고서는 정상) — corp_id=%s", corp_id)


def _company_cache_save(
    *,
    corp_id: str,
    report: Report,
    fiscal_year: Optional[int],
    source_identity_digest: str,
    build_identity: Any,
) -> None:
    """신규 회사분석 보고서를 옛 직무 캐시와 격리해 저장한다."""
    if not corp_id:
        return
    try:
        with storage_db.connect_explicit_commit() as conn:
            cache_store.save_company_report(
                conn,
                corp_id=corp_id,
                report=report,
                build_identity=build_identity,
                source_identity_digest=source_identity_digest,
                fiscal_year=fiscal_year,
            )
    except Exception:  # noqa: BLE001 — 저장 실패가 사용자를 막으면 안 된다
        logger.exception("회사분석 캐시 저장 실패 (보고서는 정상) — corp_id=%s", corp_id)


# ══════════════════════════════════════════════════════════
# 엔진 v2 (composer) 연결 — ENGINE_V2=1일 때만 탄다 (04장 3-1 항목3·3-4절)
# ══════════════════════════════════════════════════════════


def _v2_ask_via_provider(
    engine: _MeteredEngine,
    client: Any,
    *,
    stage: str,
    max_tokens: int,
):
    """composer의 AskFn(프롬프트→응답 문자열)을 기존 provider 포트로 감싼다.

    writer 경로와 같은 계량 client 경계를 지난다 — 비용 계량·예산 가드·요청별
    모델 고정이 전부 그 경계에서 적용된다. 구조화 출력(output_config)은 쓰지
    않는다: composer가 응답 문자열에서 직접 JSON을 관용 파싱하기 때문이다.

    ★ 프롬프트가 «공유 앞부분» 길이를 실어 오면(composer의 CacheablePrompt)
      그 경계로 두 블록을 만들어 앞부분만 캐시한다 — 아래 ask 주석 참조.

    ★ 예산 소진·billing-uncertain 차단은 «이 요청 전역» 장애다 — composer가
      문장 단위 실패로 삼키면 실제 원인이 «출고 검증 실패»로 오표기된다
      (실측 결함). AskFatalError로 감싸 던져 composer의 삼킴 지점들이
      재전파하게 하고(_run_v2_composer가 다시 풀어 실행기의 전용 중단 분기로 보낸다).
    """
    # composer는 v2 전용이라 지연 import한다 — v1 경로의 module 적재 비용을
    # 바꾸지 않기 위해서다.
    from src.features.composer.port import AskFatalError  # noqa: PLC0415

    def ask(prompt: str) -> str:
        # composer가 «아홉 장이 공유하는 앞부분»(회사 머리말 + 조각 전체)의
        # 길이를 프롬프트에 실어 보내면(composer.logic.CacheablePrompt), 그
        # 경계에서 두 블록으로 나눠 앞부분에만 캐시 표식을 찍는다. 프롬프트
        # 캐시는 앞부분이 바이트 단위로 같을 때만 맞으므로, 장마다 달라지는
        # 뒷부분을 같은 블록에 섞으면 매번 새로 써야 한다(실측: 호출 6번까지
        # cache_read 0, 685원 소진).
        # 표식이 없으면(재시도로 이어 붙인 평범한 str 등) 예전처럼 통짜로 보낸다.
        text = str(prompt)
        raw_prefix = getattr(prompt, "cache_prefix_chars", 0)
        prefix_chars = raw_prefix if type(raw_prefix) is int and raw_prefix > 0 else 0
        head, rest = text[:prefix_chars], text[prefix_chars:]
        # 뒷부분이 비면(이론상 불가) 나눌 이유가 없다 — 통짜 str로 되돌린다.
        use_prompt_cache = bool(head) and bool(rest)
        content: Any = (
            [
                {
                    "type": "text",
                    "text": head,
                    "cache_control": {"type": "ephemeral"},
                },
                {"type": "text", "text": rest},
            ]
            if use_prompt_cache
            else text
        )
        try:
            with _meter_stage(engine, stage, prompt_cache=use_prompt_cache):
                response = client.messages.create(
                    model=getattr(engine, "MODEL", "") or GENERATION_MODEL,
                    max_tokens=max_tokens,
                    temperature=0,  # 원문 인용 충실도 우선 (1판 _ask와 동일)
                    messages=[{"role": "user", "content": content}],
                )
        except (
            provider_budget.ProviderBudgetExceeded,
            provider_budget.ProviderBudgetUnavailable,
        ) as error:
            # 요청 로컬 한도는 «돈이 없다»가 아니라 «이 요청 몫을 다 썼다»다.
            # 횟수 상한(RequestCallLimitReached)과 예약액 소진
            # (ProviderBudgetExceeded)을 각각 다른 깃발로 나르되, 둘 다
            # composer의 선택적 단계에서는 강등 대상이다.
            # ProviderBudgetUnavailable(일일·수명 상한·계정 장애·원장 실패)은
            # 두 깃발 모두 False 로 남아 요청 전체를 멈춘다 — 안전선 불변.
            call_limited = isinstance(
                error, provider_budget.RequestCallLimitReached
            )
            raise AskFatalError(
                error,
                call_limit=call_limited,
                request_budget=(
                    isinstance(error, provider_budget.ProviderBudgetExceeded)
                    and not call_limited
                ),
            ) from error
        except generation_coordination.GenerationCoordinationError as error:
            # 초대 링크 중단·lease 상실·대기 취소·유료 단계 예약 실패는 «이
            # 요청 전역» 중단이다. 여기서 감싸지 않으면 composer의 문장 단위
            # 삼킴이 이를 「이 장을 못 썼다」로 바꿔, 조사는 남은 장과 확인 단계를
            # 돌고 사유가 「품질 미달」로 뒤바뀐다(실측 결함).
            # 호출 «횟수» 상한이 아니라 요청 자체를 더 진행할 수 없는 상태이므로
            # 선택적 단계를 건너뛰고 이어가지 않는다.
            raise AskFatalError(error, call_limit=False) from error
        blocks = getattr(response, "content", None) or []
        return "".join(str(getattr(block, "text", "") or "") for block in blocks)

    return ask


def _full_section_evidence_packets(
    *,
    corp_id: str,
    source_identity_digest: str,
    frags: dict[int, dict[str, object]],
    filing_meta: Any,
) -> Any:
    """legacy·typed 수집물을 단 하나의 fail-closed packet 경계로 옮긴다."""

    return build_section_evidence_packet_set(
        corp_id=corp_id,
        source_generation_sha256=source_identity_digest,
        frags=frags,
        filing_meta=filing_meta,
    )


def _packet_document_preflight_final_gate_reason(detail_code: str) -> str:
    """문서 하한과 packet 결속 손상을 서로 다른 운영 사유로 바꾼n다."""

    if detail_code == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID:
        return FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
    return FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT


def _prepare_v2_comparison_result(
    *,
    engine: Any,
    counter: Any,
    profile: dict[str, Any],
    official_evidence: OfficialEvidenceCollectionResult,
    corp_code: str,
    company_name: str,
    corp_type: str,
    financials: Optional[dict[str, Any]],
    filing: Optional[dict[str, Any]],
    business_date: Any,
    dart_download_document: Any = None,
) -> Any:
    """FULL 캐시·provider 전에 공식 양사 비교를 실제 생산한다."""

    candidate_fragments, _added = merge_official_evidence_fragments(
        {}, official_evidence
    )
    candidate_fragments = register_candidate_sentence_evidence(
        candidate_fragments
    )
    candidate_fragments = register_stated_differentiator_sentence_evidence(
        candidate_fragments,
        company_name=company_name,
        company_aliases=_official_company_aliases(profile),
    )
    candidate_sources, candidate_sentences = build_typed_comparison_candidate_inputs(
        candidate_fragments,
        result=official_evidence,
        profile=profile,
        corp_code=corp_code,
        company_name=company_name,
        collected_on=business_date.isoformat(),
    )
    self_official_text = ""
    if filing and str(filing.get("rcept_no") or "").strip():
        downloader = dart_download_document or engine.download_document
        self_path = downloader(
            str(filing["rcept_no"]), engine.RAW_DIR, counter
        )
        self_official_text = str(engine.read_filing_text(self_path) or "")
    comparison = _build_competitive_position_result(
        Report(
            company=company_name,
            job="",
            corp_type=corp_type,
            grade=Grade.PARTIAL,
            sections=[],
            # 후보 registry는 아래 전용 인자로만 넘긴다. Report에도 미리 넣으면
            # 공용 생산기가 「이미 상위 보고서에 있는 Source」로 보고 결과에서
            # 빼는데, V2 bridge에는 그 상위 Report가 없어서 후보·attester가
            # typed packet 직전에 사라진다.
            citations=[],
        ),
        engine=engine,
        counter=counter,
        self_corp_code=corp_code,
        self_company=company_name,
        self_financials=financials,
        self_filing=filing,
        self_official_text=self_official_text,
        collected_on=business_date.isoformat(),
        business_date=business_date,
        official_candidate_sentences=candidate_sentences,
        candidate_source_registry=candidate_sources,
        dart_download_document=dart_download_document,
    )
    if not any(
        fact.claim_type == STATED_DIFFERENTIATOR_CLAIM_TYPE
        for fact in comparison.facts
    ):
        raise ComparisonBlockedError(
            "회사 공식 자료에서 자기 선언형 차별점을 확인하지 못했습니다"
        )
    return comparison


def _comparison_generation_digest(
    source_identity_digest: str, comparison: Any
) -> str:
    """비교사 원문·수치 변경도 FULL 캐시 신원에 포함한다."""

    from src.shared.report_generation.models import canonical_sha256  # noqa: PLC0415

    comparison_digest = canonical_sha256(comparison)
    return hashlib.sha256(
        f"{source_identity_digest}\x1f{comparison_digest}".encode("utf-8")
    ).hexdigest()


def _run_v2_composer(
    *,
    engine: _MeteredEngine,
    client: Any,
    company_name: str,
    corp_type: str,
    frags: dict[int, dict[str, str]],
    financials: Any,
    filing: Optional[dict[str, Any]],
    revenue_tables: list[dict[str, Any]],
    sources: list[SourceStatus],
    business_date: Any,
    model: str,
    steps: list[dict[str, Any]],
    # ★ 캐시 저장에 필요한 두 값. 조회할 때 쓴 것과 «같은» 값이어야 다음
    #   조사에서 적중한다 — 여기서 다시 계산하면 두 곳이 어긋난다.
    corp_id: str = "",
    current_fiscal_year: Optional[int] = None,
    source_identity_digest: str = "",
    build_identity: engine_build_identity.EngineBuildIdentity,
    generation_mode: engine_mode.EngineMode,
    comparison_result: Any = None,
    release_mode_override: Optional[ReleaseMode] = None,
) -> RunResult:
    """엔진 v2: composer 경로로 보고서를 만든다.

    v1 자산 재사용(04장 설계 개요): 수집 조각(frags)·법인 판정 결과·
    ``build_three_year_table`` 실적표를 그대로 받아 쓴다. 작가 ask와 검수 ask는
    «다른 클로저»로 주입한다 (Generator/Evaluator 분리).

    ★ v2 보고서는 v1과 열쇠가 다른 전용 1층 캐시에만 저장한다. 배포 revision과
      생성기 지문이 달라지면 자동 미적중이라 옛 결과가 새 코드 결과로 나오지 않는다.
    """
    build_identity = engine_build_identity.require_exact_engine_build_identity(
        build_identity
    )
    generation_mode = engine_mode.assert_engine_mode_current(generation_mode)
    if generation_mode is not engine_mode.EngineMode.V2:
        raise generation_coordination.GenerationCoordinationError(
            "v2 composer에는 v2 엔진 모드 영수증이 필요합니다"
        )
    engine_build_identity.assert_engine_build_identity_current(build_identity)
    if not build_identity.cache_usable:
        raise generation_coordination.GenerationCoordinationError(
            "v2 provider에는 정상 배포 epoch 영수증이 필요합니다"
        )
    # composer는 v2 전용이라 지연 import한다 — v1 경로의 module 적재 비용·의존을
    # 바꾸지 않기 위해서다 (pipeline→composer 방향은 계획이 허용한 연결이다).
    from src.features.composer import pipeline as composer_pipeline  # noqa: PLC0415
    from src.features.composer.port import (  # noqa: PLC0415
        AskFatalError,
        composition_tables_from_raw,
        filing_meta_from_raw,
        performance_table_from_report_table,
    )
    from src.features.composer.validate import V2ValidationError  # noqa: PLC0415
    from src.features.company_comparison.v2_bridge import (  # noqa: PLC0415
        attach_comparison_program_evidence,
    )
    from src.shared.report_generation.canonical import (  # noqa: PLC0415
        PublicManifestError,
    )

    filing_identity = filing_meta_from_raw(filing)
    try:
        if release_mode_override is not None:
            release_mode = release_mode_override
        else:
            raw_release_mode = os.environ.get(REPORT_RELEASE_MODE_ENV_NAME)
            if raw_release_mode is None or raw_release_mode == "":
                raise ValueError(
                    "엔진 v2 운영 경로는 보고서 release mode를 명시해야 합니다"
                )
            release_mode = parse_release_mode(raw_release_mode)
        section_evidence_packets = None
        build_identity_sha256 = ""
        if release_mode is ReleaseMode.FULL:
            frozen_build_identity = (
                engine_build_identity.require_exact_engine_build_identity(
                    build_identity
                )
            )
            if not frozen_build_identity.cache_usable:
                raise ValueError("FULL 생성 build identity를 확정할 수 없습니다")
            build_identity_sha256 = frozen_build_identity.epoch_digest
            section_evidence_packets = _full_section_evidence_packets(
                corp_id=corp_id,
                source_identity_digest=source_identity_digest,
                frags=frags,
                filing_meta=filing_identity,
            )
            if comparison_result is None:
                raise ValueError("FULL 생성에 공식 양사 비교 생산물이 없습니다")
            section_evidence_packets = attach_comparison_program_evidence(
                section_evidence_packets,
                comparison_result,
            )
    except EvidenceTransportError as exc:
        gate_reason = classify_v2_validation_final_gate_reason((exc.detail_code,))
        logger.warning(
            "엔진 v2 FULL 근거 transport 차단: %s",
            exc.detail_code,
        )
        steps.append(
            {
                "step": "v2_FULL_근거transport_차단",
                "사유코드": exc.detail_code,
            }
        )
        return RunResult(
            outcome=Outcome.GATE_STOPPED,
            message=(
                "회사 공식 자료를 보고서 장에 연결하는 내부 검사를 통과하지 "
                "못해 AI 작성 전에 멈췄습니다."
                + _stop_reason_note(gate_reason)
            ),
            sources=sources,
            corp_type=corp_type,
            fragments_collected=len(frags),
            cost_krw=_request_spent_krw(engine),
            model=model,
            final_gate_reason=gate_reason,
        )
    except ValueError:
        # release mode 파싱·FULL build 설정·양사 비교 packet 결속은 모두
        # 회사 자료의 품질이 아니라 우리 실행 경계의 입력/배선 계약이다.
        # 이 ValueError를 publish_blocked로 접으면 운영 화면은 회사 자료가
        # 부족한 것처럼 보이고 같은 내부 결함을 찾기 어려워진다. 사람용
        # 예외문은 저장하지 않고 닫힌 내부 사유만 남긴다.
        logger.warning("엔진 v2 FULL 입력 계약 차단", exc_info=True)
        steps.append(
            {
                "step": "v2_FULL_입력계약_차단",
                "사유코드": FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
            }
        )
        return RunResult(
            outcome=Outcome.GATE_STOPPED,
            message=(
                "엄격 생성에 필요한 회사별 근거 묶음을 완전히 확인하지 못해 "
                "AI 작성 전에 멈췄습니다."
                + _stop_reason_note(
                    FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
                )
            ),
            sources=sources,
            corp_type=corp_type,
            fragments_collected=len(frags),
            cost_krw=_request_spent_krw(engine),
            model=model,
            final_gate_reason=FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
        )

    if release_mode is ReleaseMode.FULL:
        assert section_evidence_packets is not None
        document_preflight = assess_packet_document_sources(
            section_evidence_packets
        )
        if not document_preflight.can_call_ai:
            packet_contract_invalid = (
                document_preflight.detail_code
                == FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID
            )
            gate_reason = _packet_document_preflight_final_gate_reason(
                document_preflight.detail_code
            )
            steps.append(
                {
                    "step": "v2_FULL_독립문서사전검사_차단",
                    "독립문서수": document_preflight.independent_document_count,
                    "사유코드": document_preflight.detail_code,
                }
            )
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                message=(
                    (
                        "수집한 문서의 신원과 원문 지문을 하나로 "
                        "결속하는 내부 검사를 통과하지 못해 AI 작성 전에 "
                        "멈췄습니다."
                        if packet_contract_invalid
                        else (
                            "수집한 공식 자료를 모두 합쳐도 완성 보고서의 "
                            "독립 문서 최소 기준을 채울 수 없어 AI 작성 "
                            "전에 멈췄습니다."
                        )
                    )
                    + _stop_reason_note(gate_reason)
                ),
                sources=sources,
                corp_type=corp_type,
                fragments_collected=len(frags),
                cost_krw=_request_spent_krw(engine),
                model=model,
                final_gate_reason=gate_reason,
            )

    # 실적표 — v1과 같은 생성부(재사용). 없으면 None으로 계속 간다 (차단 아님).
    financial_cite = _first_fragment_cite(
        frags, kind="재무", text_prefix="주요계정(DART API):"
    )
    report_table = (
        build_three_year_table(financials, cite=financial_cite)
        if financial_cite
        else None
    )
    performance_table = (
        performance_table_from_report_table(report_table)
        if report_table is not None
        else None
    )
    try:
        composition_tables = composition_tables_from_raw(revenue_tables)
        if len(composition_tables) != len(revenue_tables):
            # 변환기는 과거 호환을 위해 빈 행·잘못된 Mapping을 건너뛸 수 있다.
            # 생산 경로에서는 표 하나라도 조용히 사라지면 근거 없는 축소
            # 보고서가 되므로, 입력과 출력의 1:1 보존을 별도로 강제한다.
            raise ValueError("매출 구성표 변환 과정에서 표가 누락됐습니다")
    except (TypeError, ValueError):
        # 매출표의 공개 행·원문 행·행별 근거가 한 index로 이동하지 못하면
        # 회사 자료 부족이 아니라 내부 transport 결함이다. provider closure를
        # 만들기 전에 닫아 실제 AI 호출과 정상 차감을 모두 막는다.
        logger.warning("엔진 v2 매출표 근거 transport 차단", exc_info=True)
        steps.append(
            {
                "step": "v2_매출표근거transport_차단",
                "사유코드": FINAL_GATE_DETAIL_PUBLIC_MANIFEST_BINDING_INVALID,
            }
        )
        return RunResult(
            outcome=Outcome.GATE_STOPPED,
            message=(
                "매출 구성표의 공개 값과 원문 근거를 같은 순서로 연결하지 "
                "못해 AI 작성 전에 멈췄습니다."
                + _stop_reason_note(FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT)
            ),
            sources=sources,
            corp_type=corp_type,
            fragments_collected=len(frags),
            cost_krw=_request_spent_krw(engine),
            model=model,
            final_gate_reason=FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
        )
    analysis_period, latest_performance_period = _performance_period_labels(
        report_table, filing
    )

    writer_ask = _v2_ask_via_provider(
        engine, client, stage="v2_compose", max_tokens=V2_WRITER_MAX_TOKENS
    )
    reviewer_ask = _v2_ask_via_provider(
        engine, client, stage="v2_review", max_tokens=V2_REVIEWER_MAX_TOKENS
    )
    diagram_ask = _v2_ask_via_provider(
        engine, client, stage="v2_diagram", max_tokens=V2_DIAGRAM_MAX_TOKENS
    )
    try:
        output = composer_pipeline.run_v2(
            company_name,
            frags,
            performance_table,
            writer_ask=writer_ask,
            reviewer_ask=reviewer_ask,
            diagram_ask=diagram_ask,
            corp_type=corp_type,
            generated_at=business_date.isoformat(),
            as_of_date=business_date.isoformat(),
            analysis_period=analysis_period,
            latest_performance_period=latest_performance_period,
            table_presentation=(
                str(getattr(report_table, "presentation", "") or "") or "table"
            ),
            # 전자공시 조각에는 조각 자체에 주소가 없다. 주소를 가진 것은
            # «떠 온 문서»이므로 그 신원을 함께 넘겨 부록에 원문 주소를 싣는다.
            filing_meta=filing_identity,
            composition_tables=composition_tables,
            release_mode=release_mode,
            section_evidence_packets=section_evidence_packets,
            # 회사 신원은 릴리스 정책과 무관한 사실이다 — 이미 확인한 고유번호를
            # 모드 때문에 버리지 않는다. 이 값이 비면 초대 링크에
            # 보고서를 다시 묶을 때 회사 일치 검증이 이름 비교만 남아, 이름이
            # 같고 고유번호가 다른 회사의 보고서가 그대로 묶인다
            # (`web/routers/admin.py`의 `_link_company_id`가 묶인 보고서에서
            # 이 값을 읽는다). corp_id를 확인하지 못했으면 예전처럼 빈 값이다.
            company_id=corp_id,
            build_identity_sha256=build_identity_sha256,
        )
        # 도식 검증이 뺀 줄의 사유를 실행 기록에도 남긴다. 3장 카드 0건 실측에서
        # 「작가가 안 냈다」와 「우리가 걸렀다」를 가를 표식이 서버 로그에만 있어
        # 저장된 실행 기록으로는 진단할 수 없었다.
        dropped_diagram_reasons = tuple(getattr(output, "diagram_drop_reasons", ()))
        if dropped_diagram_reasons:
            steps.append(
                {
                    "step": "8_도식_검증_제외",
                    "사유": list(dropped_diagram_reasons),
                }
            )
        if release_mode is not ReleaseMode.SHADOW:
            from src.shared.report_generation.models import (  # noqa: PLC0415
                GenerationProducerEvidence,
                GenerationRunMetrics,
                assert_canonical_producer_evidence,
            )
            from src.shared.report_quality.generation import (  # noqa: PLC0415
                GenerationQualityObservation,
                assert_observation_matches_assessment,
            )

            if (
                type(output.generation_metrics) is not GenerationRunMetrics
                or output.report.generation_metrics is not output.generation_metrics
                or type(output.quality_observation)
                is not GenerationQualityObservation
                or output.report.quality_observation
                is not output.quality_observation
                or output.report.release_mode != release_mode.value
            ):
                raise V2ValidationError(
                    ("엄격 생성 결과의 실제 지표·품질 관측 transport가 누락됐습니다",)
                )
            if release_mode is ReleaseMode.FULL:
                from src.shared.report_generation.canonical import (  # noqa: PLC0415
                    assert_report_matches_generation_evidence,
                    report_verification_payload,
                )

                evidence = output.generation_evidence
                if (
                    type(evidence) is not GenerationProducerEvidence
                    or output.report.generation_evidence is not evidence
                    or output.report.company_id != corp_id
                ):
                    raise V2ValidationError(
                        ("FULL 생성 결과의 생산 증거 transport가 누락됐습니다",)
                    )
                try:
                    assert_canonical_producer_evidence(evidence)
                    assert_observation_matches_assessment(
                        output.quality_observation,
                        evidence.assessment,
                    )
                    assert_report_matches_generation_evidence(
                        report_verification_payload(output.report),
                        evidence,
                        manifest_bytes=(
                            output.report.public_structure_manifest.encode("utf-8")
                        ),
                    )
                except PublicManifestError:
                    # 아래 공용 except가 같은 manifest 오류를 닫힌 내부 사유로
                    # 분류한다. 사람 문구 하나짜리 V2ValidationError로 바꾸면
                    # 발생 위치에 따라 publish_blocked로 오표기된다.
                    raise
                except (TypeError, ValueError) as error:
                    raise V2ValidationError(
                        ("FULL 생성 생산 증거와 최종 보고서 결속이 깨졌습니다",),
                        problem_codes=(
                            FINAL_GATE_DETAIL_EVIDENCE_MANIFEST_BINDING_INVALID,
                        ),
                    ) from error
    except AskFatalError as exc:
        # ★ 요청 로컬 예약액 소진(ProviderBudgetExceeded — 횟수 상한 포함)만은
        #   «사유 없는 실패»로 끝내지 않는다. 예외로 나가면 run() 바깥 except가
        #   Outcome.FAILED로 접어 화면에 「보고서를 만들다 오류가 났습니다」만
        #   남는다(2026-09-05 실측). 회사 자료 문제도 우리 코드 결함도 아닌
        #   운영 한도 문제이므로 닫힌 사유를 실어 GATE_STOPPED로 멈춘다.
        if isinstance(exc.cause, provider_budget.ProviderBudgetExceeded):
            spent = _request_spent_krw(engine)
            logger.warning("엔진 v2 요청 예산 소진으로 중단", exc_info=True)
            steps.append(
                {
                    "step": "v2_요청예산_소진",
                    "사유코드": FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED,
                    "지출원": round(spent),
                }
            )
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                message=(
                    "이 조사에 배정된 AI 예산을 다 써서 보고서를 "
                    "완성하지 못했습니다."
                    + _stop_reason_note(FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED)
                ),
                sources=sources,
                corp_type=corp_type,
                fragments_collected=len(frags),
                cost_krw=spent,
                model=model,
                final_gate_reason=FINAL_GATE_REASON_REQUEST_BUDGET_EXHAUSTED,
            )
        # 그 밖의 요청 전역 장애(계정·원장·조정 실패)는 «출고 검증 실패»로
        # 오표기하지 않는다. 원인 예외를 그대로 다시 던져 v1과 같은 경로로
        # run()의 바깥 경계를 지나 실행기가 예외 종류에 맞는 중단 결과로 분류하게 한다.
        raise exc.cause from exc
    except PublicManifestError:
        # 행 원문·표 변형·공개 manifest 결속이 깨진 것은 회사 자료 부족이
        # 아니라 우리 내부 배선 오류다. 예외문이나 원문은 영속 진단에 싣지 않는다.
        gate_reason = classify_v2_validation_final_gate_reason(
            (FINAL_GATE_DETAIL_PUBLIC_MANIFEST_BINDING_INVALID,)
        )
        logger.warning("엔진 v2 공개 manifest 결속 차단", exc_info=True)
        steps.append(
            {
                "step": "v2_공개manifest_결속차단",
                "사유코드": FINAL_GATE_DETAIL_PUBLIC_MANIFEST_BINDING_INVALID,
            }
        )
        return RunResult(
            outcome=Outcome.GATE_STOPPED,
            message=(
                "보고서 표와 원문을 연결하는 내부 검사를 통과하지 못해 "
                "보고서를 내보내지 않았습니다."
                + _stop_reason_note(gate_reason)
            ),
            sources=sources,
            corp_type=corp_type,
            fragments_collected=len(frags),
            cost_krw=_request_spent_krw(engine),
            model=model,
            final_gate_reason=gate_reason,
        )
    except V2ValidationError as exc:
        # v2 출고 3검사 실패 — 원문 없는 검증 사유만 운영 기록에 남긴다.
        # ★ 품질 하한(40건 실질 claim·8건 독립 문서·50% 검증 비율) 미달일
        #   때만 별도 사유로 구분한다 — 그 외 출고 검증 실패는 여전히
        #   publish_blocked로 남는다(뭉뚱그림 금지). 분류 자체는
        #   src/shared/final_gate_diagnostics.py의 순수 함수 한 곳이
        #   권위다(shadow 진단 하네스와 공유).
        # composer 사전검사는 하위 호환 때문에 기계 코드를 ``problems``에
        # 싣던 기간이 있었다. 닫힌 분류기는 완전 일치만 받으므로 두 통로를
        # 합쳐도 사람 문구·URL이 사유로 오인되지 않는다.
        gate_reason = classify_v2_validation_final_gate_reason(
            (*exc.problem_codes, *exc.problems)
        )
        logger.warning("엔진 v2 출고 검증 차단: %s", list(exc.problems))
        steps.append({"step": "v2_출고검증_차단", "사유": list(exc.problems)})
        return RunResult(
            outcome=Outcome.GATE_STOPPED,
            message=(
                "엔진 v2 출고 검증을 통과하지 못해 보고서를 내보내지 않았습니다. "
                "확인되지 않은 내용을 정상 보고서처럼 보여주지 않습니다."
                + _stop_reason_note(gate_reason)
            ),
            sources=sources,
            corp_type=corp_type,
            fragments_collected=len(frags),
            cost_krw=_request_spent_krw(engine),
            model=model,
            final_gate_reason=gate_reason,
        )

    # composer는 본문·인용을 만들지만 수집 단계의 3상태(ok/none/failed)는
    # 알지 못한다. RunResult에만 두면 최초 worker가 사라진 뒤 캐시·재시작
    # 조회에서 수집 신원이 빈 목록으로 바뀌므로 불변 Report에도 함께 봉인한다.
    report = replace(output.report, sources=list(sources))
    steps.append(
        {
            "step": "v2_composer_완료",
            "생성문장": output.composed_sentences,
            "생존문장": output.verified_sentences,
            "인용조각": len(report.citations),
        }
    )
    # ★ 출고 검증(validate_v2)을 이미 통과한 보고서만 여기 온다. 그것을
    #   «지금 코드 지문»과 함께 저장해 두면, 코드가 그대로일 때 같은 회사를
    #   다시 조사해도 900원이 안 나간다. 코드가 바뀌면 지문이 달라져 저절로
    #   미적중이므로 옛 결과가 새 결과인 척 나올 수 없다.
    # ★ 저장 실패는 삼킨다 — 보고서는 이미 만들어졌고, 저장이 안 됐다고
    #   사용자에게 실패를 돌려주면 돈만 쓰고 결과를 못 받는다.
    content_eligible, missing_sections, content_shortfall_reasons = (
        _generation_cache_eligibility(
            report,
            sources=sources,
            steps=steps,
            filing=filing,
        )
    )
    cache_eligible = bool(build_identity.cache_usable and content_eligible)
    if cache_eligible and not generation_coordination.is_active():
        _v2_cache_save(
            corp_id=corp_id,
            report=report,
            fiscal_year=current_fiscal_year,
            source_identity_digest=source_identity_digest,
            build_identity=build_identity,
            # 조회와 «같은 열쇠»로 저장해야 다음 조사에서 적중한다(C6).
            release_mode=release_mode,
        )
    elif not cache_eligible:
        logger.info(
            "수집 실패·후보범위 불완전·기본 장/내용 결손이 껴 v2 캐시에 "
            "저장하지 않습니다 — corp_id=%s · 장누락=%s · 내용결손=%s",
            corp_id,
            sorted(missing_sections),
            sorted(content_shortfall_reasons),
        )
    else:
        logger.info(
            "유료 웹은 Report-only v2 1층 저장을 건너뜁니다 — corp_id=%s",
            corp_id,
        )
    return RunResult(
        outcome=Outcome.REPORT,
        report=report,
        sources=sources,
        charged=True,  # 보고서가 나가면 1 차감 — v1과 같은 3분법
        corp_type=corp_type,
        # composer의 최종 provenance 등록부에는 직접 인용되지 않는 소유권
        # attester도 남는다. raw frags/부록 길이를 다시 세지 않고, 프로그램
        # 비교 조각까지 포함해 같은 번호 정본으로 계산한 생성 지표를 운반한다.
        fragments_collected=(
            output.generation_metrics.fragments_collected
            if output.generation_metrics is not None
            else len(frags)
        ),
        fragments_cited=(
            output.generation_metrics.fragments_cited
            if output.generation_metrics is not None
            else len(report.citations)
        ),
        sentences_made=output.composed_sentences,
        sentences_passed=output.verified_sentences,
        cost_krw=_request_spent_krw(engine),
        model=model,
        generation_cache_eligible=cache_eligible,
        generation_evidence=output.generation_evidence,
        generation_metrics=output.generation_metrics,
        quality_observation=output.quality_observation,
    )


def _write_prose(
    engine: Any,
    client: Any,
    user_input: UserInput,
    sections: list[ReportSection],
    steps: list[dict[str, Any]],
    model: str,
) -> tuple[list[ReportSection], set[str]]:
    """11 작성 — 근거를 «하나의 글»로 잇는다. AI 2회.

    Args:
        engine: 1판 엔진 (`_ask`를 빌려 쓴다).
        client: Anthropic 클라이언트.
        user_input: 회사·직무.
        sections: 원문 문장이 담긴 보고서 칸들.
        steps: 단계 기록.
        model: 이 요청에서 쓰는 모델.

    Returns:
        (글이 붙은 칸들, 작가가 «실제로 쓴» 칸 번호들).

    ★★ **작가와 검증은 한 벌이다.** 검증이 죽거나 통과가 0이면 그 칸은
      **원문 나열 그대로** 둔다 — 검사 없이 새 글을 내보내지 않는다.
    ★ 원문(`lines`)을 지우지 않는다. 화면이 「원문 보기」로 같이 낸다.
    ⚠️ 여기서 터져도 보고서 전체가 죽으면 안 된다 — 글은 «덤»이고 원문이 본체다.
    """
    lines_by_cell = {s.cell: list(s.lines) for s in sections if s.lines}
    evidence = writer_logic.collect_evidence(lines_by_cell)
    if not evidence:
        return sections, set()

    def ask(prompt: str, schema: dict[str, Any], max_tokens: int):
        engine_model = getattr(engine, "MODEL", "")
        try:
            if model:
                engine.MODEL = model
            payload, usage = engine._ask(client, prompt, schema, max_tokens=max_tokens)
        finally:
            if model:
                engine.MODEL = engine_model
        # ★ 단계 기록에도 어느 모델인지 남긴다. 비용 정본은 동시성에 안전한 client
        #   응답 계량이고, 이 값은 나중에 단계별 결과를 재현할 때 쓴다.
        if isinstance(usage, dict):
            usage = {**usage, writer.USAGE_MODEL_KEY: model or engine_model}
        return payload, usage

    try:
        written, write_step = writer_logic.write_with_ai(
            lambda p, s: ask(p, s, writer.WRITE_MAX_TOKENS),
            company=user_input.company,
            job=user_input.job,
            evidence=evidence,
        )
        steps.append({"step": writer.WRITE_STEP, **write_step})
        if not written:
            return sections, set()

        # ★ 다른 호출·다른 지시문이다 (정본 Generator/Evaluator 분리) —
        #   같은 대화에서 이어 물으면 «자기가 쓴 것»을 감싸게 된다.
        passed, verify_step = writer_verify.verify_with_ai(
            lambda p, s: ask(p, s, writer_verify.VERIFY_MAX_TOKENS),
            written=written,
            evidence=evidence,
        )
        steps.append({"step": writer_verify.VERIFY_STEP, **verify_step})
    except Exception as exc:  # noqa: BLE001 — 글은 «덤»이다. 원문 보고서를 죽이면 안 된다
        steps.append({"step": writer.WRITE_STEP, "오류": f"{type(exc).__name__}: {str(exc)[:80]}"})
        return sections, set()

    # ★ 검증 뒤에도 문장별 근거를 버리지 않는다. 문자열 하나로 합치면
    #   내부 sid와 실제 출처의 연결이 끊겨 화면에서 근거 번호를 못 붙인다.
    prose_lines_by_cell = {}
    for cell, sentences in passed.items():
        cited = writer_logic.to_cited_lines(sentences, evidence.get(cell, []))
        cited = filter_specific_prose(
            cell,
            cited,
            lines_by_cell.get(cell, []),
            company=user_input.company,
        )
        if cited:
            prose_lines_by_cell[cell] = cited
    출처연결버림 = sum(
        len(sents) - len(prose_lines_by_cell.get(cell, []))
        for cell, sents in passed.items()
    )
    if 출처연결버림:
        # 조용히 지우면 출처 연결 결함이 다시 생겨도 실측에서 알 수 없다.
        steps.append({"step": writer.CITE_LINK_STEP, "버림": 출처연결버림})
    prose_lines_by_cell = {c: lines for c, lines in prose_lines_by_cell.items() if lines}
    if not prose_lines_by_cell:
        return sections, set()
    out = [
        replace(s, prose_lines=prose_lines_by_cell[s.cell])
        if s.cell in prose_lines_by_cell else s
        for s in sections
    ]
    return out, set(prose_lines_by_cell)


def _collect_news(
    engine: Any,
    client: Any,
    company: str,
    profile: dict[str, Any],
    steps: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """6 수집 — 뉴스. **AI가 번호를 고르고 프로그램이 원문을 복사한다**.

    Args:
        engine: 1판 엔진 (`search_news`·`_ask`를 빌려 쓴다).
        client: 1판이 만든 Anthropic 클라이언트.
        company: 회사 이름.
        profile: 기업개황 — «동명 타사»를 가르는 단서가 여기서 나온다.
        steps: 단계 기록 (여기에 결과를 남긴다).

    Returns:
        뉴스 조각 목록. 실패하면 빈 목록.

    ★ 단계 이름과 열쇠(`검색결과`·`채택`·`오류`)를 **1판과 똑같이** 남긴다 —
      출처 목록과 빈칸 사유가 이 열쇠들을 읽는다. 바꾸면 조용히 어긋난다.
    ★ 모델을 갈아 끼우지 «않는다» — 엔진 기본값(하이쿠)을 그대로 쓴다.
      번호만 고르는 일이라 충분하고, 단가가 어긋나지 않아 비용이 정확하다.
    """
    step: dict[str, Any] = {"step": "6_수집_뉴스"}
    # ★ 주제별로 나눠 검색한다 — 회사 이름만 최신순으로 찾으면 «오늘 기사»만 잡힌다.
    #   실측(하이브): 30건 전부 그날 나온 걸그룹 차트 기사였다. 취준생에게 쓸모가 없다.
    groups: list[list[Any]] = []
    검색별: dict[str, int] = {}
    첫오류 = ""
    for 검색어, 정렬, 개수 in newspick_logic.search_terms(
        company, profile, newspick.SEARCH_QUERIES
    ):
        try:
            found = engine.search_news(검색어, display=개수, sort=정렬)
        except Exception as exc:  # noqa: BLE001 — 한도·인증·네트워크. 나머지 검색은 계속한다
            첫오류 = 첫오류 or f"{type(exc).__name__}: {str(exc)[:60]}"
            검색별[검색어] = 0
            # 인증 거부·한도 소진·응답 계약 파손은 검색어를 바꿔도
            # 회복되지 않는다. 같은 요청에서 다시 불러 계수기와
            # provider 사용량만 늘리지 않도록 어댑터의 종료 신호를 따른다.
            if getattr(exc, "stop_further_requests", False) is True:
                break
            continue
        groups.append(found)
        검색별[검색어] = len(found)

    items = newspick_logic.interleave(groups, newspick.MAX_CANDIDATES * 2)
    if not items:
        # ★ 「검색이 막혔다(⚠️)」와 「기사가 없다(❌)」를 구분한다 — 섞으면 오거부가 된다.
        step.update({"검색결과": 0, "채택": 0, "검색별": 검색별})
        if 첫오류:
            step["오류"] = 첫오류
        steps.append(step)
        return []

    candidates, dropped = newspick_logic.prefilter(
        items, company=company, today=today_kst()
    )
    step.update({"검색결과": len(items), "검색별": 검색별, "사전거름": dropped})
    if 첫오류:
        step["일부검색실패"] = 첫오류

    try:
        picked, 기록 = newspick_logic.pick_with_ai(
            lambda prompt, schema: engine._ask(
                client, prompt, schema, max_tokens=newspick.PICK_MAX_TOKENS
            ),
            company=company,
            profile=profile,
            candidates=candidates,
        )
    except Exception as exc:  # noqa: BLE001 — AI가 막혀도 보고서 전체가 멈추면 안 된다
        step.update({"채택": 0, "오류": f"{type(exc).__name__}: {str(exc)[:80]}"})
        steps.append(step)
        return []

    step.update(기록)
    step.setdefault("채택", len(picked))
    steps.append(step)
    return newspick_logic.to_fragments(picked)


def _homepage_compare_host(url: str) -> str:
    """주소에서 «비교용 host»만 뽑는다. 못 뽑으면 빈 문자열.

    ★ 왜 `browser_url()`을 먼저 통과시키나 — 세 가지를 한 번에 해결하기 때문이다.
      1) **스킴이 없을 수 있다.** DART `hm_url`은 대부분 "www.foo.co.kr" 모양이다.
         `urlsplit("www.hanjin.com:443/kor")`은 host가 아니라 **scheme을
         "www.hanjin.com"으로 읽는다**(실측 확인). `browser_url()`은 "://"가
         없으면 https를 먼저 붙이므로 이 함정을 지나간다.
      2) **대소문자·끝점·한글 도메인.** host는 DNS 규칙상 대소문자를 안 가리고
         (RFC 4343), 끝의 "."은 root label 표기일 뿐 같은 이름이다. 한글 도메인은
         punycode로 적으면 같은 이름이다. `browser_url()`이 셋 다 정규화한다.
      3) **위험한 주소는 빈 문자열.** 그러면 아래에서 «다름»으로 떨어져
         원래 주소를 그대로 쓴다 — fail-closed다.

    ⚠️ **포트는 일부러 뺀다.** `hostname`은 ":443"을 포함하지 않는다.
      실측에서 www.hanjin.co.kr → https://www.hanjin.com:443/... 처럼 포트가 붙어
      돌아오는데, 포트는 «어느 회사냐»가 아니라 «어떻게 연결하냐»다. 포트까지
      비교하면 같은 사이트의 "https://foo.co.kr" 과 "https://foo.co.kr:443" 을
      다른 회사로 오판한다. 포트 안전성은 `browser_url`의 ALLOWED_PORTS가 이미 본다.
    """
    normalized = homepage_link.browser_url(url)
    if not normalized:
        return ""
    try:
        return urllib.parse.urlsplit(normalized).hostname or ""
    except ValueError:
        return ""


def _homepage_url_same_host_only(raw: str) -> str:
    """홈페이지 주소를 고른다 — **같은 회사일 때만** 바꾼다.

    ★ 이름에 「수집기」가 없는 이유 — 조각 수집만 잠그면 소용이 없다. 화면(후보 목록·
      회사 확인 카드)도 같은 `workable_url()`을 부르므로, 한쪽만 잠그면 **사용자가
      보는 화면에 남의 회사 주소가 인쇄된 채로** 회사를 고르게 된다
      (적대 검수에서 실제로 발견됐다). 네 곳이 이 하나를 공유한다.

    `workable_url()`은 실제로 열어 보고 «열리는 주소»를 준다. 자체서명 인증서
    때문에 https가 통째로 죽은 회사((주)진영 실측)에서 조각이 0개가 되는 것을
    막아 준다. 그런데 **리다이렉트를 따라가므로 다른 회사 host가 올 수 있다.**

    ★ 실측(대기업 표본에서 3건) —
        www.hyundai.co.kr → https://www.hyundaimotorgroup.com/ko/main/mainRecommend
        www.hyosung.co.kr → https://www.hyosung.com/kr/index
        www.hanjin.co.kr  → https://www.hanjin.com:443/kor/Main.do
      이걸 그대로 넘기면 **남의 회사 사이트가 「이 회사 공식 웹」으로** 들어오고,
      게다가 조각에 후보출처검증="https_exact_dart_host"(= DART host와 정확히
      같음) 도장까지 찍힌다 — 거짓말이 근거로 박힌다.

    ★ 그래서 규칙은 하나 — **host가 같을 때만 바꾼다. 다르면 원래 주소 그대로.**
      "다르면 그대로"는 오늘과 완전히 같은 동작이라 새 예외를 만들지 않는다.

    ⚠️ **"www." 접두어는 지우지 않는다** — 즉 apex와 www는 «다른 host»로 본다.
      DART가 apex를 적었는데 실제로는 www인 경우는 `collect_homepage_fragments`의
      `allow_dart_www_alias` 경로가 이미 다룬다. 그 경로는 별도 probe로 이동을
      증명하고 조각에 이동 흔적(IR_DART_WWW_REDIRECT_*)을 남긴다. 여기서 www를
      말없이 같은 것으로 쳐 버리면 **그 흔적이 사라진 채로** 통과한다.

    Args:
        raw: DART 기업개황이 준 `hm_url` (스킴이 없을 수도 있다).

    Returns:
        열리는 주소(host가 같을 때) 또는 `raw` 그대로.
    """
    raw_host = _homepage_compare_host(raw)
    if not raw_host:
        # 주소가 비었거나 링크로 만들 수 없는 모양이다. 접속을 시도할 이유가 없다.
        return raw
    # `workable_url`은 `@lru_cache(maxsize=256)`이라(homepage/link.py:140) 회사 확인
    # 화면에서 이미 부른 주소면 «보통은» 재접속하지 않는다.
    # ⚠️ 「안 한다」가 아니라 「보통은 안 한다」다 — ①캐시가 256곳을 넘기면 밀려나고
    #   ②확인 화면을 거치지 않고 바로 본조사가 도는 경로가 있는지는 확정하지 못했다
    #   (검수). 빗나가도 접속 1회가 더 늘 뿐 안전성 문제는 아니다.
    candidate = homepage_link.workable_url(raw)
    if not candidate or _homepage_compare_host(candidate) != raw_host:
        return raw
    return candidate


def _homepage_url_for_display(raw: str) -> str:
    """화면 링크(후보 목록·회사 확인 카드)에 걸 주소를 고른다.

    ★ 왜 잠금만으로는 부족한가 — `_homepage_url_same_host_only()`는 «다른 회사»라고
      판정하면 DART 원본 글자를 그대로 돌려준다. 그런데 그 글자는 대개
      "www.foo.co.kr"처럼 **앞머리(스킴)가 없다.** 스킴 없는 글자를 HTML의 href에
      그대로 넣으면 브라우저는 그걸 **우리 사이트 안의 상대 경로**로 읽는다.
      그래서 `browser_url()`을 한 번 더 통과시켜 «링크로 걸 수 있는 모양», 또는
      걸면 안 되는 주소면 빈 문자열로 만든다. 빈 문자열이면 화면은 링크 대신
      글자만 보여 준다(company_candidates.html·confirm.html이 이미 그렇게 한다).

    ⚠️ 이 값은 화면에만 쓰이지 않는다 — 후보 점수
      `score_business_candidate(homepage=...)`에도 그대로 들어간다. 리다이렉트된
      남의 host가 들어가면 `_domain_key`가 **남의 도메인으로 +0.12**를 줘 후보
      순서가 흔들린다 (`features/business_candidate/logic.py`의 도메인 가점).

    Args:
        raw: DART 기업개황이 준 `hm_url` (스킴이 없을 수도 있다).

    Returns:
        `https://…`/`http://…`, 또는 링크로 만들 수 없으면 `""`.
    """
    return homepage_link.browser_url(_homepage_url_same_host_only(raw))


#: typed 수집기의 장(section_id) → v1 수집 조각 «종류».
#: ★ 값은 v1이 이미 쓰는 종류만 골랐다. 새 이름을 만들면
#:   `_full_section_evidence_packets`의 장 배정 표에도, composer 어휘에도
#:   걸리지 않아 조각이 조용히 사라진다(작가가 못 본다).
_TYPED_DART_SECTION_FRAGMENT_KIND: Final[dict[str, str]] = {
    "identity": "사업내용",
    "business_model": "사업내용",
    "portfolio": "사업내용",
    "past_changes": "MD&A",
    "current_challenges": "MD&A",
    "future_strategy": "신규사업전망",
    "operations_partners": "사업내용",
    "culture": "사업내용",
    "competitive_position": "사업내용",
}

#: DART 접수번호(rcept_no)의 모양 — 14자리 숫자.
#: typed 수집기의 문서ID는 `f"{source_kind}:{rcept_no}"`라 뒷부분만 접수번호다.
_RCEPT_NO_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{14}")

#: typed 공식 근거 수집 결과를 남기는 단계 이름.
#: ★ `_sources_from`이 읽는 이름들과 겹치지 않게 새로 만든다 — 겹치면 화면의
#:   소스별 현황이 typed 결과로 덮인다.
TYPED_DART_COLLECT_STEP: Final[str] = "6_수집_typed공시"


def _typed_dart_collection_enabled(
    generation_mode: Optional[engine_mode.EngineMode],
) -> bool:
    """typed 공식 근거 수집을 이번 요청에서 돌릴 것인가.

    ★ 검사 «순서»가 계약이다.
      1) v1이면 첫 줄에서 나간다 — v1 경로는 release mode도
         `TYPED_DART_COLLECTOR`도 **읽지 않는다**(읽지 않으면 스위치가 동결도
         되지 않아 「안 봤다」를 시험이 기계적으로 확인할 수 있다).
      2) FULL이 아니면 나간다 — SHADOW·ENFORCE는 사용자 결과가 불변이어야
         한다. release mode가 없거나 모르는 값이면 «FULL로 치지 않는다»;
         그 계약 위반은 v2 composer가 GATE_STOPPED로 다룬다.
      3) 그다음에야 kill switch를 본다.
    """

    if generation_mode is not engine_mode.EngineMode.V2:
        return False
    raw_release_mode = os.environ.get(REPORT_RELEASE_MODE_ENV_NAME)
    if not raw_release_mode:
        return False
    try:
        release_mode = parse_release_mode(raw_release_mode)
    except ValueError:
        return False
    if release_mode is not ReleaseMode.FULL:
        return False
    return typed_collector_switch.typed_dart_collector_enabled()


def _typed_dart_collector_modules() -> tuple[Any, Any, Any]:
    """엔진 패키지의 typed 수집기 module 셋을 불러온다.

    typed 수집기는 app이 아니라 ``analysis_engine/src`` 아래에 산다. `_engine()`이
    이미 하는 것과 같은 방식으로 검색 경로를 얹되, **실제로 그 트리에서 온
    module인지 파일 경로로 확인한다** — app의 `features` 패키지가 이름을 가리면
    조용히 다른 코드를 부르는 대신 소리 나게 실패해야 한다(부르는 쪽이 강등으로
    흡수한다).
    """

    engine_src = (paths.PROJECT_ROOT / "analysis_engine" / "src").resolve()
    if str(engine_src) not in sys.path:
        sys.path.insert(0, str(engine_src))
    modules = tuple(
        importlib.import_module(f"features.evidence_collection.{name}")
        for name in ("collect", "dart_fetcher", "serialize")
    )
    for module in modules:
        module_path = Path(getattr(module, "__file__", "") or "").resolve()
        if engine_src not in module_path.parents:
            raise ImportError(
                "typed 수집기가 엔진 트리 밖에서 잡혔습니다: "
                f"{module.__name__} -> {module_path}"
            )
    return modules


def _typed_dart_harvest_mapping(
    engine: Any,
    counter: Any,
    corp_code: str,
    *,
    collected_at: str,
) -> dict[str, Any]:
    """typed 공식 근거 수집을 실행하고 계약 Mapping으로 돌려준다.

    DART 조회는 이 요청의 1판 엔진 함수(`get_json`·`download_document`)를 그대로
    쓴다 — 별도 HTTP 경로를 새로 열지 않아 일일 한도·사용량 계수가 한 곳에 모인다.
    """

    collect_module, fetcher_module, serialize_module = _typed_dart_collector_modules()
    fetcher = fetcher_module.DartRuntimeFetcher(
        document_cache_dir=Path(str(engine.RAW_DIR)),
        counter=counter,
        get_json_fn=engine.get_json,
        download_document_fn=engine.download_document,
    )
    harvest = collect_module.collect_dart_evidence(
        fetcher, corp_code, now=collected_at
    )
    return serialize_module.harvest_to_mapping(harvest)


def _collect_typed_dart(
    engine: Any,
    counter: Any,
    corp_code: str,
    frags: dict[int, dict[str, str]],
    steps: list[dict[str, Any]],
    *,
    collected_at: str,
) -> dict[int, dict[str, str]]:
    """typed 공식 근거를 모아 v1 조각 묶음에 더한다. 실패는 강등으로 흡수한다.

    ★ 예외 경계는 공식 IR 호출부(아래 `collect_official_ir_fragments` 감싸기)를
      그대로 복사했다 — 미검증(LIVE_COLLECTION_UNVERIFIED) 수집기의 결함이
      보고서를 강등 없이 `Outcome.FAILED`로 떨어뜨리면 안 되기 때문이다.
      «자료 없음»이 아니라 «오류»로 적어 화면에서 둘이 섞이지 않게 한다.

    ⚠️ 알려진 절충 — DART 일일 한도(`DartLimitReached`)도 여기서 흡수된다.
      legacy `_collect`가 공시 원문 다운로드 실패를 이미 같은 방식으로 강등하고
      있어(같은 함수 위쪽 `except (RuntimeError, OSError)`) 계약을 맞춘 것이다.
      이 지점 뒤로 새 DART 호출은 없다.
    """

    try:
        mapping = _typed_dart_harvest_mapping(
            engine, counter, corp_code, collected_at=collected_at
        )
        merged, added = _merge_typed_dart_fragments(frags, mapping)
    except Exception as exc:  # noqa: BLE001 - 수집기 결함도 자료 부재로 오인하지 않는다
        steps.append(
            {
                "step": TYPED_DART_COLLECT_STEP,
                "오류": f"{type(exc).__name__}: {str(exc)[:120]}",
            }
        )
        return frags
    steps.append(
        {
            "step": TYPED_DART_COLLECT_STEP,
            "조각수": added,
            "문서수": len(mapping.get("documents") or []),
            "조회기록": len(mapping.get("attempts") or []),
        }
    )
    return merged


def _normalized_fragment_text_key(text: str) -> str:
    """조각 원문의 중복 판정 열쇠 — 공백·개행을 정규화한 뒤 SHA-256.

    ★ 왜 `(원문, 문서ID)`가 아닌가 (P1-A) — v1 공시 조각(`make_fragments`)에는
      `문서ID` 키 «자체»가 없다. 그 조합을 열쇠로 쓰면 legacy 쪽이 항상
      `(원문, "")`이 되어 어떤 typed 문서ID와도 일치하지 못하고, **같은 공시
      문단이 두 번 실린다.** 원문만으로 판정하면 그 구멍이 닫힌다.
    """

    return hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()


def _typed_dart_document_index(
    mapping: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """typed 수집 Mapping의 문서 목록을 document_id로 찾을 수 있게 만든다."""

    documents = mapping.get("documents")
    if not isinstance(documents, list):
        return {}
    return {
        str(document.get("document_id") or ""): document
        for document in documents
        if isinstance(document, dict)
    }


def _typed_dart_legacy_fragments(mapping: dict[str, Any]) -> list[dict[str, str]]:
    """typed 수집 Mapping → v1 수집 조각 모양(`{"종류","원문",…}`) 목록.

    ★ 문서 신원을 못 만드는 조각은 «버린다». 접수번호를 확인하지 못한 채
      넣으면 `_full_section_evidence_packets`가 최신 공시 1건의 문서ID를
      빌려 주어, 다른 공시에서 온 조각이 엉뚱한 문서를 근거로 가리킨다.
    """

    fragments = mapping.get("fragments")
    if not isinstance(fragments, list):
        return []
    documents = _typed_dart_document_index(mapping)
    made: list[dict[str, str]] = []
    for raw in fragments:
        if not isinstance(raw, dict):
            continue
        text = str(raw.get("text") or "").strip()
        kind = _TYPED_DART_SECTION_FRAGMENT_KIND.get(
            str(raw.get("section_id") or "")
        )
        if not text or not kind:
            continue
        document_id = str(raw.get("document_id") or "")
        rcept_no = document_id.rpartition(":")[2]
        if not _RCEPT_NO_RE.fullmatch(rcept_no):
            logger.warning(
                "typed 공시 조각의 접수번호를 확인할 수 없어 버립니다: %s",
                document_id[:64],
            )
            continue
        document = documents.get(document_id, {})
        published_on = normalize_official_source_date(
            document.get("published_on")
        )
        made.append(
            {
                "종류": kind,
                "원문": text,
                "문서ID": rcept_no,
                "문서명": str(document.get("title") or ""),
                "원문위치": str(raw.get("location") or ""),
                "문서일": published_on,
            }
        )
    return made


def _merge_typed_dart_fragments(
    frags: dict[int, dict[str, str]],
    mapping: dict[str, Any],
) -> tuple[dict[int, dict[str, str]], int]:
    """typed 공시 조각을 v1 조각 묶음 «뒤에» 더한다. 기존 번호는 안 건드린다.

    Args:
        frags: v1 수집이 만든 조각 묶음. 번호는 작가가 인용하는 주소라
            덮어쓰지 않는다.
        mapping: `serialize.harvest_to_mapping()` 산출.

    Returns:
        합친 조각 묶음과 실제로 더한 개수.
    """

    seen = {
        _normalized_fragment_text_key(str(fragment.get("원문") or ""))
        for fragment in frags.values()
    }
    merged = dict(frags)
    added = 0
    for fragment in _typed_dart_legacy_fragments(mapping):
        key = _normalized_fragment_text_key(fragment["원문"])
        if key in seen:
            continue
        seen.add(key)
        merged[max(merged, default=0) + 1] = fragment
        added += 1
    return merged, added


@dataclass(frozen=True)
class _FormalOfficialSourceSummary:
    """정식 수집 결과를 옛 출처 현황 형식으로만 보여 주는 읽기 전용 요약."""

    state: str
    detail: str
    candidate_scope_complete: bool
    fragment_count: int
    attempted_documents: int = 0
    downloaded_pdf_bytes: int = 0
    fragments: tuple[dict[str, str], ...] = ()


def _formal_official_web_summaries(
    result: OfficialEvidenceCollectionResult,
) -> tuple[_FormalOfficialSourceSummary, _FormalOfficialSourceSummary]:
    """한 번 끝낸 정식 웹 수집을 홈페이지·IR 상태로만 투영한다.

    이 요약의 ``fragments``는 의도적으로 항상 비어 있다. 실제 typed 조각은
    뒤의 ``merge_official_evidence_fragments`` 한 경계에서만 숫자 인용으로
    옮겨야 한다. 여기서 옛 조각처럼 다시 넣으면 동일 원문이 두 번호로 생기고,
    캐시 snapshot에 없는 입력이 Writer에 섞인다.
    """

    documents: dict[str, Any] = {}
    fragments: dict[str, Any] = {}
    attempts: dict[str, Any] = {}
    for candidate in result.candidates:
        for document in candidate.documents:
            documents.setdefault(document.document_id, document)
        for fragment in candidate.fragments:
            fragments.setdefault(fragment.fragment_id, fragment)
        for attempt in candidate.attempts:
            attempts.setdefault(attempt.attempt_id, attempt)

    def summarize(
        source_kinds: frozenset[str],
        *,
        missing_detail: str,
        failed_detail: str,
    ) -> _FormalOfficialSourceSummary:
        matching_documents = {
            document_id: document
            for document_id, document in documents.items()
            if document.source_kind in source_kinds
        }
        matching_fragments = {
            fragment_id: fragment
            for fragment_id, fragment in fragments.items()
            if fragment.document_id in matching_documents
        }
        matching_attempts = {
            attempt_id: attempt
            for attempt_id, attempt in attempts.items()
            if attempt.source_kind in source_kinds
        }
        failed = any(
            attempt.state in {CollectionState.FAILED, CollectionState.TRUNCATED}
            for attempt in matching_attempts.values()
        )
        # 시도도 문서도 없으면 공식 URL 자체를 열지 못한 경우까지 "끝까지
        # 확인했다"고 단정할 수 없다. 성공/없음/실패를 캐시 판정에서 섞지 않는다.
        scope_complete = bool(matching_attempts or matching_documents) and not failed
        if failed:
            state = "failed"
            detail = failed_detail
        elif matching_fragments:
            state = "ok"
            detail = "정식 공식 자료 수집 결과를 사용했습니다"
        else:
            state = "none"
            detail = missing_detail
        return _FormalOfficialSourceSummary(
            state=state,
            detail=detail,
            candidate_scope_complete=scope_complete,
            fragment_count=len(matching_fragments),
            attempted_documents=sum(
                attempt.documents_seen for attempt in matching_attempts.values()
            ),
            downloaded_pdf_bytes=sum(
                attempt.bytes_downloaded for attempt in matching_attempts.values()
            ),
        )

    homepage = summarize(
        frozenset(
            {
                SOURCE_KIND_OFFICIAL_WEB_PAGE,
                SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
            }
        ),
        missing_detail="정식 공식 웹 수집에서 사용할 근거를 찾지 못했습니다",
        failed_detail="정식 공식 웹 자료 확인을 끝까지 마치지 못했습니다",
    )
    official_ir = summarize(
        frozenset({SOURCE_KIND_OFFICIAL_IR_PDF}),
        missing_detail="정식 공식 IR 수집에서 사용할 근거를 찾지 못했습니다",
        failed_detail="정식 공식 IR 자료 확인을 끝까지 마치지 못했습니다",
    )
    return homepage, official_ir


def _collect(
    engine: Any,
    client: Any,
    profile: dict[str, Any],
    user_input: UserInput,
    counter: Any,
    steps: list[dict[str, Any]],
    *,
    financials: Optional[dict[str, Any]],
    fin_years: list[int],
    filing: Optional[dict[str, Any]],
    generation_mode: Optional[engine_mode.EngineMode] = None,
    corp_code: str = "",
    formal_official_evidence: Optional[OfficialEvidenceCollectionResult] = None,
    dart_download_document: Any = None,
) -> tuple[dict[int, dict[str, str]], list[dict], str]:
    """6 수집 — 공시 원문 + 재무 API + 홈페이지를 조각으로 만든다. AI 0회.

    Args:
        financials: 재무 API 응답. ★ 여기서 다시 부르지 않는다 — 캐시 신선도를
            보려고 `run()`이 «이미» 불렀다. 두 번 부르면 DART 일일 한도만 깎는다.
        fin_years: 그때 실제로 자료가 있던 사업연도 목록 (단계 기록용).
        filing: 최신 공시 1건(보고서 이름·접수번호). 이것도 `run()`이 이미 받았다.
            **출처 목록을 만들 때 쓴다**. 공시를 못 찾았으면 None.
        generation_mode: 이 요청이 운반한 엔진 모드. typed 공식 근거 수집을
            켤지 정하는 데만 쓴다. 기본값(None)이면 v1과 똑같이 동작한다 —
            기존 호출자·시험은 한 줄도 달라지지 않는다.
        corp_code: 사용자가 확정한 8자리 DART 법인코드(`card.ref`). typed 수집은
            회사별이라 이 값이 없으면 시작하지 않는다. `profile`에서 다시 읽지
            않는다 — 회사 식별자는 한 곳에서만 온다.
        formal_official_evidence: FULL 정식 수집기가 이미 만든 결과. 있으면 같은
            홈페이지·IR을 legacy 수집기로 다시 열거나 옛 조각으로 다시 넣지
            않고, 출처 현황용 안전 요약만 남긴다. 기본값은 기존 경로와 같다.

    Returns:
        조각 목록, 구조화 표, 실제로 내려받은 자사 공식 원문. 마지막 값은
        9장 비교에서 한쪽 자료만 있는 비교를 막는 데 다시 쓴다.
    """
    filing_text = ""
    if filing:
        try:
            downloader = dart_download_document or engine.download_document
            path = downloader(filing["rcept_no"], engine.RAW_DIR, counter)
            filing_text = engine.read_filing_text(path)
        except (RuntimeError, OSError) as exc:
            # 못 가져온 사실을 남긴다 — 조용히 넘어가면 「회사에 자료가 없다」로 잘못 읽힌다.
            steps.append({"step": "6_수집_원문", "오류": str(exc)[:120]})

    frags = engine.make_fragments(filing_text, financials)
    # ★ 1판은 절 표제의 «첫 출현»만 본다. 그런데 사업보고서 첫 장이 «목차»라,
    #   「사업의 내용」의 첫 출현이 목차 줄이고 거기서 1,200자를 뜨면 통째로 목차가 된다.
    #   실측 — 하이브 조각 9개 중 3개가 목차였고, 그래서 1·3·4번 칸이 비었다.
    #   1판은 안 고치고, 나온 조각 중 목차인 것만 «다음 출현»으로 다시 뜬다.
    # ⚠️ `getattr`로 받는다 — 1판이 이름을 바꾸거나 시험용 가짜 엔진을 끼울 때
    #   여기서 터지면 **조사 전체가 멈춘다.** 못 받으면 «고치지 않고» 넘어갈 뿐이다.
    frags, repaired = filing_clean.repair(
        frags,
        filing_text,
        getattr(engine, "SECTION_HEADS", {}),
        getattr(engine, "FRAG_CHARS", 0),
    )
    if repaired:
        steps.append({"step": "6_수집_목차보정", "고친조각": repaired})

    # ★ 1판이 «안 뜨는» 절을 더 모은다 — 신규사업 전망·시장 특성·소송.
    #   결과가 전자공시 원문을 그대로 옮긴 것처럼 보이던 핵심 원인이
    #   **정작 필요한 절을 안 뜨던 것**이었다. 1판은 0줄 고치지 않고 «더할» 뿐이다.
    frags, added = filing_extra.add_to(
        frags, filing_text, getattr(engine, "FRAG_CHARS", filing_extra.DEFAULT_FRAG_CHARS)
    )
    if added:
        steps.append({"step": "6_수집_추가절", "더한조각": added})

    # 절 첫머리 밖에 있는 실명 파트너·계약 근거를 최대 3문장만 보충한다.
    # 전체 절 길이를 늘리지 않아 표·업계 일반론이 후보를 압도하는 일을 막는다.
    frags, relationship_added = filing_relationships.add_to(frags, filing_text)
    if relationship_added:
        steps.append(
            {"step": "6_수집_파트너관계", "더한조각": relationship_added}
        )

    # ── typed 공식 근거 수집 (FULL + kill switch 둘 다 켜졌을 때만) ──
    # ★ 아래 한 줄이 kill switch다. 꺼져 있거나 v1·비FULL이면 여기서 그대로
    #   빠져나가고, 이 함수의 나머지 legacy 경로는 바이트 하나 달라지지 않는다.
    # FULL의 정식 collector가 typed DART를 이미 받았으면 옛 kill-switch
    # 경로를 두 번 타지 않는다. 같은 공시를 재다운로드하고
    # 중복 조각을 넣는 것은 DART 한도·packet 신원 모두를 손상한다.
    # formal이 없는 옛 direct/SHADOW 호출은 기존 kill-switch를 그대로 유지한다.
    if (
        formal_official_evidence is None
        and _typed_dart_collection_enabled(generation_mode)
        and corp_code.strip()
    ):
        frags = _collect_typed_dart(
            engine,
            counter,
            corp_code.strip(),
            frags,
            steps,
            collected_at=today_kst().isoformat(),
        )

    # 홈페이지를 붙이기 전 개수다. 출처 현황에서 이 값을 쓰지 않으면
    # "전자공시 조각 21개"에 홈페이지까지 섞여 소스별 상태가 틀린다.
    dart_fragment_count = len(frags)

    # canonical 공개 사실은 뉴스 조각을 항상 폐기했고 별도 모순 검사도 없었다.
    # 효과 없이 Haiku 1회와 Writer 입력 토큰만 늘리므로 공식 경로에서는 검색부터
    # 생략한다. `_collect_news`는 과거 실행 재현과 단위시험 호환용으로만 남긴다.
    steps.append(
        {
            "step": "6_수집_뉴스",
            "검색결과": 0,
            "채택": 0,
            "생략": "공식 근거 보고서에서는 뉴스를 사용하지 않습니다",
        }
    )

    # 회사 홈페이지 — 2번(뭘 잘하나)이 만성적으로 비는 원인이었다.
    # ★ 실패를 「없음」과 반드시 구분한다. 섞으면 「이 회사는 자료가 없다」로 잘못 읽힌다.
    formal_homepage = None
    formal_official_ir = None
    if formal_official_evidence is not None:
        formal_homepage, formal_official_ir = _formal_official_web_summaries(
            formal_official_evidence
        )
    with collection_cache_scope():
        homepage = (
            formal_homepage
            if formal_homepage is not None
            else collect_homepage_fragments(
                _homepage_url_same_host_only(profile.get("hm_url", "")),
                allow_dart_www_alias=True,
            )
        )
        if homepage.state == "ok":
            for frag in homepage.fragments:
                # 최종 URL 검증 표식·문서 위치 등 수집기가 만든 provenance 메타데이터를
                # 버리지 않는다. build_citations가 닫힌 Source 필드만 골라 쓴다.
                frags[max(frags, default=0) + 1] = dict(frag)
            homepage_fragment_count = int(
                getattr(homepage, "fragment_count", len(homepage.fragments))
            )
            steps.append(
                {
                    "step": "6_수집_홈페이지",
                    "조각수": homepage_fragment_count,
                    "후보범위완전": homepage.candidate_scope_complete,
                }
            )
        elif homepage.state == "failed":
            steps.append(
                {
                    "step": "6_수집_홈페이지",
                    "오류": homepage.detail,
                    "후보범위완전": False,
                }
            )
        else:
            steps.append(
                {
                    "step": "6_수집_홈페이지",
                    "없음": homepage.detail,
                    "후보범위완전": homepage.candidate_scope_complete,
                }
            )

        # DART 기업개황의 홈페이지와 정확히 같은 HTTPS host 안에서만 공식 IR
        # PDF를 찾는다. PDF 파싱은 별도 프로세스·바이트/페이지/글자 상한 안에서
        # 수행하며, 실패·상한 잘림은 "경쟁사 언급 없음"과 분리한다.
        company_aliases = tuple(
            dict.fromkeys(
                value
                for value in (
                    str(profile.get("corp_name_eng") or "").strip(),
                    str(profile.get("corp_eng_name") or "").strip(),
                    str(profile.get("stock_name") or "").strip(),
                )
                if value
            )
        )
        try:
            official_ir = (
                formal_official_ir
                if formal_official_ir is not None
                else collect_official_ir_fragments(
                    str(profile.get("hm_url") or ""),
                    company_name=str(profile.get("corp_name") or "").strip(),
                    company_aliases=company_aliases,
                    allow_dart_www_alias=True,
                )
            )
        except Exception as exc:  # noqa: BLE001 - 수집기 결함도 자료 부재로 오인하지 않는다
            steps.append(
                {
                    "step": "6_수집_공식IR",
                    "오류": f"{type(exc).__name__}: {str(exc)[:120]}",
                    "후보범위완전": False,
                }
            )
        else:
            ir_scope_complete = bool(
                getattr(official_ir, "candidate_scope_complete", False)
            )
            if official_ir.state == "ok":
                for fragment in official_ir.fragments:
                    frags[max(frags, default=0) + 1] = dict(fragment)
                ir_fragment_count = int(
                    getattr(official_ir, "fragment_count", len(official_ir.fragments))
                )
                steps.append(
                    {
                        "step": "6_수집_공식IR",
                        "조각수": ir_fragment_count,
                        "문서시도": official_ir.attempted_documents,
                        "PDF바이트": official_ir.downloaded_pdf_bytes,
                        "상세": official_ir.detail,
                        "후보범위완전": ir_scope_complete,
                    }
                )
            elif official_ir.state == "failed":
                steps.append(
                    {
                        "step": "6_수집_공식IR",
                        "오류": official_ir.detail,
                        "문서시도": official_ir.attempted_documents,
                        "PDF바이트": official_ir.downloaded_pdf_bytes,
                        "후보범위완전": False,
                    }
                )
            else:
                steps.append(
                    {
                        "step": "6_수집_공식IR",
                        "없음": official_ir.detail,
                        "문서시도": official_ir.attempted_documents,
                        "PDF바이트": official_ir.downloaded_pdf_bytes,
                        "후보범위완전": ir_scope_complete,
                    }
                )

    # ★ 매출 구성 비중 표 — 사용자가 리포트 11건에서 고른 항목 ①.
    #   **11건이 «전부» 실은 유일한 만장일치 항목**이다.
    #   ⚠️ 지어낼 자리가 없다 — 공시가 비중을 이미 계산해 놓았고 우리는 베낄 뿐이다.
    revenue_tables = revenuemix.build(filing_text)
    frags, revenue_tables = _bind_revenue_table_evidence_fragments(
        frags,
        revenue_tables,
        filing=filing,
        filing_text=filing_text,
    )
    dart_fragment_count += len(revenue_tables)
    if revenue_tables:
        steps.append({"step": "6_수집_매출구성", "표": len(revenue_tables)})

    steps.append(
        {
            "step": "6_수집",
            "원문": (filing or {}).get("report_nm"),
            "조각수": len(frags),
            "전자공시조각수": dart_fragment_count,
            "재무API연도": fin_years,
        }
    )
    return frags, revenue_tables, filing_text


def _region_matches(typed: str, address: str) -> bool:
    """입력 지역이 주소와 같은 곳인가.

    「강원 강릉시」와 「강원도 강릉시 …」는 같은 곳이다.
    데모와 같은 규칙이라 그쪽 함수를 그대로 쓴다 — 두 벌로 나뉘면 반드시 어긋난다.
    """
    from src.features.pipeline.demo import _region_matches as shared  # noqa: PLC0415

    return shared(typed, address)


def _message(outcome: Outcome) -> str:
    """실패했을 때 사용자에게 보여줄 말. 데모와 같은 문장을 쓴다."""
    from src.features.pipeline.demo import _OUTCOME_MESSAGE  # noqa: PLC0415

    return _OUTCOME_MESSAGE.get(outcome, "")
