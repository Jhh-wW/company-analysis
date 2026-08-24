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
import importlib
import importlib.util
import itertools
import logging
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Iterable, Optional

from src.core import paths
from src.core.clock import today_kst
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
    REVENUE_CITE,
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
    register_candidate_sentence_evidence,
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
from src.features.filingclean import extra as filing_extra
from src.features.filingclean import logic as filing_clean
from src.features.filingclean import relationships as filing_relationships
from src.features.newspick import constants as newspick
from src.features.newspick import logic as newspick_logic
from src.features.revenuemix import logic as revenuemix
from src.features.writer import constants as writer
from src.features.writer import logic as writer_logic
from src.features.writer import verify as writer_verify
from src.features.grading.logic import is_accounting_policy, is_table_dump
from src.features.cost_tracking.store import AiCostEvent
from src.features.pipeline.constants import ANTHROPIC_TIMEOUT_SEC, DART_SUCCESS_STATUS
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
)
from src.features.report_standard.constants import (
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
    FINAL_GATE_REASON_COMPARISON_BLOCKED,
    FINAL_GATE_REASON_MISSING_IDENTITY,
    FINAL_GATE_REASON_MISSING_IDENTITY_REVENUE,
    FINAL_GATE_REASON_MISSING_REVENUE,
    FINAL_GATE_REASON_OTHER_GATE,
    FINAL_GATE_REASON_PUBLISH_BLOCKED,
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
    FINAL_GATE_REASON_MISSING_IDENTITY: "회사 정체성 필수 사실 미확보",
    FINAL_GATE_REASON_MISSING_REVENUE: "수익 구조 필수 사실 미확보",
    FINAL_GATE_REASON_MISSING_IDENTITY_REVENUE: (
        "정체성·수익 구조 필수 사실 미확보"
    ),
    FINAL_GATE_REASON_OTHER_GATE: "출고 전 자동 검증",
}

#: 엔진 v2 스위치 — 환경변수 이름과 켜짐 값. 정확히 "1"일 때만 v2 경로다
#: (04장: 기본(미설정)은 v1 그대로 — 바이트 단위 무변).
ENGINE_V2_ENV_NAME: Final[str] = "ENGINE_V2"
ENGINE_V2_ENV_ON: Final[str] = "1"


def _engine_v2_enabled() -> bool:
    """지금 요청이 v2 경로로 가는가 — 판단을 한 곳에만 둔다.

    ★ 왜 함수로 빼는가 (실측 사고) — 1층 캐시 조회가 v2 분기«보다 앞»에 있어서
      ENGINE_V2=1을 켜도 그 회사의 v1 저장본이 살아 있으면 v1 보고서가 그대로
      반환됐다. 화면에는 「이전에 조사한 결과입니다」만 뜨므로 사용자는 v2가
      안 고쳐진 줄로 읽는다. 두 곳이 같은 답을 보게 묶어 둔다.
    """
    return os.environ.get(ENGINE_V2_ENV_NAME) == ENGINE_V2_ENV_ON

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
#: 공유한다(P-144). 요청마다 다른 이름으로 원본 파일을 실행해 module namespace 자체를
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


def _prompt_cached_messages(messages: object) -> object:
    """Mark exact user text as an ephemeral cache block without changing it."""

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
        estimated_input = provider_budget.estimate_request_tokens(
            {"args": args, "kwargs": call_kwargs}
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
            response = self._messages.create(*args, **call_kwargs)
        except Exception as error:
            # Some provider failures still carry authoritative usage.  Preserve
            # that real cost as a failed-call event instead of flattening it to
            # zero.  If usage is absent, the existing billing-uncertain path
            # remains fail-closed.
            failure_usage = getattr(error, "usage", None)
            if failure_usage is None:
                failure_usage = getattr(
                    getattr(error, "response", None), "usage", None
                )
            try:
                failure_in = int(getattr(failure_usage, "input_tokens"))
                failure_out = int(getattr(failure_usage, "output_tokens"))
                failure_create = int(
                    getattr(failure_usage, "cache_creation_input_tokens", 0) or 0
                )
                failure_read = int(
                    getattr(failure_usage, "cache_read_input_tokens", 0) or 0
                )
                if min(
                    failure_in, failure_out, failure_create, failure_read
                ) < 0:
                    raise ValueError
            except (AttributeError, TypeError, ValueError, OverflowError):
                failure_usage = None
            if failure_usage is not None:
                failed_model = str(
                    getattr(error, "model", "") or call_kwargs.get("model", "")
                )
                failed_cost = detailed_usage_cost_krw(
                    failed_model,
                    input_tokens=failure_in,
                    output_tokens=failure_out,
                    cache_creation_tokens=failure_create,
                    cache_read_tokens=failure_read,
                    batch=False,
                )
                self._usages.append(
                    {
                        "in": failure_in,
                        "out": failure_out,
                        "cache_creation": failure_create,
                        "cache_read": failure_read,
                        "batch": False,
                        "stage": self._metered.current_stage,
                        "cost_krw": failed_cost,
                        "failed": True,
                        "cache_hit": failure_read > 0,
                        USAGE_MODEL_KEY: failed_model,
                    }
                )
                try:
                    provider_budget.current().settle_call(
                        call_reservation,
                        actual_krw=failed_cost,
                    )
                except provider_budget.ProviderCostInvariantError:
                    self._metered._billing_uncertain = True
                raise
            # timeout/API 예외는 서버가 요청을 처리했는지 알 수 없다. 응답이 없다고
            # 0원으로 마감하면 재시작 뒤 예산이 다시 열리므로 표식을 남길 신호를 보낸다.
            self._metered._billing_uncertain = True
            provider_budget.current().mark_unknown(call_reservation)
            raise
        usage = getattr(response, "usage", None)
        tokens_in = getattr(usage, "input_tokens", None) if usage is not None else None
        tokens_out = getattr(usage, "output_tokens", None) if usage is not None else None
        if tokens_in is None or tokens_out is None:
            # 응답은 왔지만 usage가 없으면 실제 비용을 확정할 수 없다. 0원으로
            # 적지 않고 같은 통장의 진행 중 표식을 남긴다.
            self._metered._billing_uncertain = True
            provider_budget.current().mark_unknown(call_reservation)
            return response
        try:
            clean_in, clean_out = int(tokens_in), int(tokens_out)
        except (TypeError, ValueError, OverflowError):
            self._metered._billing_uncertain = True
            provider_budget.current().mark_unknown(call_reservation)
            return response
        if clean_in < 0 or clean_out < 0:
            self._metered._billing_uncertain = True
            provider_budget.current().mark_unknown(call_reservation)
            return response
        used_model = str(
            getattr(response, "model", "") or call_kwargs.get("model", "")
        )
        try:
            cache_creation = int(
                getattr(usage, "cache_creation_input_tokens", 0) or 0
            )
            cache_read = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            self._metered._billing_uncertain = True
            provider_budget.current().mark_unknown(call_reservation)
            return response
        if cache_creation < 0 or cache_read < 0:
            self._metered._billing_uncertain = True
            provider_budget.current().mark_unknown(call_reservation)
            return response
        actual_cost = detailed_usage_cost_krw(
            used_model,
            input_tokens=clean_in,
            output_tokens=clean_out,
            cache_creation_tokens=cache_creation,
            cache_read_tokens=cache_read,
            batch=False,
        )
        self._usages.append(
            {
                "in": clean_in,
                "out": clean_out,
                "cache_creation": cache_creation,
                "cache_read": cache_read,
                "batch": False,
                "stage": self._metered.current_stage,
                "cost_krw": actual_cost,
                "failed": False,
                "cache_hit": cache_read > 0,
                USAGE_MODEL_KEY: used_model,
            }
        )
        try:
            provider_budget.current().settle_call(
                call_reservation,
                actual_krw=actual_cost,
            )
        except provider_budget.ProviderCostInvariantError:
            # usage는 먼저 보존했다. 이미 생긴 비용을 숨기지 않고 상위에서
            # billing-uncertain으로 phase를 닫게 한다.
            self._metered._billing_uncertain = True
            raise
        return response

    def __getattr__(self, name: str) -> Any:
        return getattr(self._messages, name)


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


def _engine() -> Any:
    """1판 엔진을 요청마다 독립 module namespace로 불러온다.

    ★ 파일 맨 위에서 부르지 않는다. 엔진은 `anthropic`·`presidio` 같은
      무거운 프로그램을 요구하는데, 그게 안 깔려 있어도 **데모 화면은 떠야 한다.**
    ★ 평범한 `import run_pilot`을 쓰지 않는다. 그 방식은 서버 수명 동안 같은 module을
      돌려줘 1판의 `_spent_usd`가 요청 사이에 누적된다(P-144).
    """
    root = paths.PROJECT_ROOT / "analysis_engine"
    for extra in (root / "src", root / "tools"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    return _load_isolated_engine_module(root / "tools" / "run_pilot.py")


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

    ★ 표 덩어리는 여기서 버린다 (D12 · 문제로그 P-29).
      엔진을 고치지 않고 «화면에 내보내기 직전»에 거른다.
    ★ 회계기준 설명 문구도 여기서 버린다 (문제로그 P-40).
    ★ 알맹이 검사(①-b) 결과도 여기서 반영한다 (문제로그 P-66).
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
        # ★ 재무·회계 수치는 «표 그대로» 낸다 (결정기록 D13). 버리지 않는다.
        table = parse_financial_table(item.sentence)
        if table is not None:
            tables.setdefault(item.block, []).append(table)
            continue
        if is_table_dump(item.sentence):
            dumped.add(item.block)
            continue
        # 회계기준 설명 문구는 회사 이름을 바꿔도 말이 된다 (문제로그 P-40).
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

    ★ 정본 03_수집/1_흐름/02_실패처리.md — 「⚠️ 못 가져옴 → ❌ 저장 안 함」.
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
    """빈칸 사유를 «실제 수집 결과»로 다시 쓴다 (문제로그 P-67).

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


class LocalDartProfileEnrichmentError(RuntimeError):
    """로컬 DART 후보의 선택 전 profile 보강만 실패했음을 표시한다."""

    local_profile_enrichment_failed = True


class RealPipeline:
    """1판 엔진을 `port.Pipeline` 약속에 맞춰 감싼 것.

    ★ 상태를 들고 있지 않는다. 확인 카드와 실행 사이에 필요한 것은
      `CompanyCard.ref`(전자공시 고유번호)로만 넘긴다.
      서버가 여러 대로 늘어나도 그대로 돈다.
    """

    # corpCode 로컬 색인과 무료 DART 기업개황만 쓰며 AI/Places 비용은 만들지 않는다.
    business_candidate_provider_costs_money = False

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
            homepage = homepage_link.workable_url(profile.get("hm_url", ""))
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
        if re.fullmatch(r"\d{8}", corp_code) is None:
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
                homepage_url=homepage_link.workable_url(profile.get("hm_url", "")),
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
                        homepage_url=homepage_link.workable_url(
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
        engine = _MeteredEngine(_engine())
        try:
            result = self._run_metered(user_input, card, on_step, engine=engine)
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
    ) -> RunResult:
        """본조사 본체. `_MeteredEngine`이 이 요청의 AI 사용량만 모은다.

        ★ 식별(2)은 다시 하지 않는다. `card.ref`에 이미 답이 있다.
          다시 하면 AI 5회가 통째로 또 나간다.
        """
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
                "bgn_de": end.replace(year=end.year - AUDIT_WINDOW_YEARS).strftime("%Y%m%d"),
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
        judgment = engine.decide(
            profile.get("corp_cls", ""),
            has_audit,
            profile.get("bizr_no"),
            lambda b: engine.match_public_org(b, registry),
        )
        if judgment.status != "대상":
            outcome = _OUTCOME_MAP.get(f"거부_{judgment.status}", Outcome.REJECT_NO_DISCLOSURE)
            return RunResult(
                outcome=outcome,
                message=_message(outcome),
                sources=(
                    [
                        SourceStatus(
                            "전자공시",
                            "none",
                            "최근 3년 안에 감사보고서 공시가 없습니다",
                        )
                    ]
                    if audit_no_data
                    else []
                ),
                corp_type=judgment.corp_type,
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
        financials, fin_years = engine.fetch_financials(corp_code, counter)
        filing = engine.latest_report_rcept(corp_code, judgment.corp_type, counter)
        current_fiscal_year = _current_fiscal_year(fin_years, filing)
        # ★ v2 경로는 1층 캐시를 «읽지 않는다». v2는 캐시에 저장하지도 않으므로
        #   여기서 적중하면 나오는 것은 반드시 옛 v1 보고서다. v2를 켠 요청에
        #   v1 보고서를 돌려주는 것은 조용한 거짓말이라 값을 아끼는 것보다 나쁘다.
        #   (v1 경로의 동작은 하나도 바뀌지 않는다 — 04장 «v1 무변» 원칙)
        cached = (
            None
            if _engine_v2_enabled()
            else _company_cache_lookup(
                corp_id=corp_code,
                current_fiscal_year=current_fiscal_year,
            )
        )
        if cached is not None:
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
                # 정본 00_공통/2_규칙/04_할당량.md — 캐시 반환은 0 차감·무제한.
                charged=False,
                corp_type=cached.corp_type or judgment.corp_type,
                # 이번 요청에서 실제로 쓴 돈 — 신선도 확인을 위한 조회분만 남는다.
                cost_krw=_request_spent_krw(engine),
                model=model,
                # 화면 배지와 대시보드 ⑤가 이 값을 읽는다. 안 실으면 캐시가
                # 돌아도 「재사용 0건」으로 보인다 (P-63과 같은 사고).
                cache_hit=CACHE_HIT_LAYER1,
            )

        # ── 6 수집 (AI 0회) ──────────────────────────────
        tell("collect")
        frags, revenue_tables, filing_text = _collect(
            engine, client, profile, user_input, counter, steps,
            financials=financials, fin_years=fin_years, filing=filing,
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
                corp_type=judgment.corp_type,
                fragments_collected=len(frags),
                cost_krw=_request_spent_krw(engine),
                model=model,
                final_gate_reason=FINAL_GATE_REASON_OTHER_GATE,
            )

        # ── 엔진 v2 분기 (유일한 분기 지점) ──────────────
        # 수집(6)·법인 판정(5)이 끝났고 실적표 재료(financials)가 확보된 지점이다.
        # ENGINE_V2=1일 때만 composer 경로로 간다. 미설정이면 아래 v1 경로 그대로다.
        if _engine_v2_enabled():
            tell("generate")
            return _run_v2_composer(
                engine=engine,
                client=client,
                company_name=company_name,
                corp_type=judgment.corp_type,
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
            )

        # 유료 span 선택 전에 후보 원문도 정식 provenance 경계에서 한 번 봉인한다.
        # DART 전체 원문과 exact-attested HTTPS 웹·IR만 사전검사에 넣으며, 수집
        # 실패·robots 차단·상한 잘림이 있으면 "후보 없음"으로 확정하지 않는다.
        comparison_preflight: bool | None = None
        try:
            preflight_fragments = register_candidate_sentence_evidence(frags)
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
                corp_type=judgment.corp_type,
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
                corp_type=judgment.corp_type,
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
                corp_type=judgment.corp_type,
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
        if revenue_tables:
            tables_by_section["business_model"] = [ReportTable(**table) for table in revenue_tables]

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
                corp_type=judgment.corp_type,
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
            if comparison_preflight is False:
                comparison_reasons = (
                    "사전검사에서 검증 가능한 동종업계 비교 후보를 찾지 못했습니다",
                )
            else:
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
                        "step": "12_경쟁사비교_조건부생략",
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
                    "step": "12_경쟁사비교_출고차단",
                    "사유": list(exc.reasons),
                }
            )
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                message=(
                    "양사 공식 원문을 같은 지표·기간·연결범위로 비교할 수 없어 "
                    "보고서를 내보내지 않았습니다. 경쟁우위가 없다는 뜻이 아니라, "
                    "현재 공개 근거로는 확인할 수 없다는 뜻입니다."
                    + _stop_reason_note(FINAL_GATE_REASON_COMPARISON_BLOCKED)
                ),
                sources=sources,
                corp_type=judgment.corp_type,
                fragments_collected=len(frags),
                sentences_made=sentences_made,
                sentences_passed=len(written_claims),
                cost_krw=_request_spent_krw(engine),
                model=model,
                span_selection_diagnostics=tuple(selection_diagnostics),
                span_selection_result_reason=selection_result_reason_code,
                final_gate_reason=FINAL_GATE_REASON_COMPARISON_BLOCKED,
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
                corp_type=judgment.corp_type,
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

        # ── 14 저장 — 1층 캐시 (정본 §3 저장 구간) ────────
        # 회사분석 전용 버전 키로 저장해 옛 직무·공고 보고서와 섞이지 않는다.
        # ★ 우리 쪽 수집 실패(⚠️)가 낀 결과는 «저장하지 않는다» —
        #   그날만 죽은 소스 때문에 그 회사가 「자료 없는 회사」로 굳는다.
        candidate_collection_incomplete = not _comparison_candidate_scope_complete(
            steps,
            filing=filing,
        )
        included_section_ids = {section.cell for section in report.sections}
        optional_basic_sections_missing = (
            OPTIONAL_BASIC_SECTION_IDS - included_section_ids
        )
        required_basic_sections_missing = REQUIRED_SECTION_IDS - included_section_ids
        content_shortfall_reasons = {
            IDENTITY_SUMMARY_SHORTFALL_REASON,
            CUSTOMER_MARKET_SHORTFALL_REASON,
            PAST_NARRATIVE_SHORTFALL_REASON,
        }.intersection(report.shortfall_reasons)
        if (
            _has_failed_source(sources)
            or candidate_collection_incomplete
            or optional_basic_sections_missing
            or required_basic_sections_missing
            or content_shortfall_reasons
        ):
            logger.info(
                "수집 실패·후보범위 불완전·기본 장/내용 결손이 껴 1층 캐시에 "
                "저장하지 않습니다 — corp_id=%s · 장누락=%s · 내용결손=%s",
                corp_code,
                sorted(optional_basic_sections_missing | required_basic_sections_missing),
                sorted(content_shortfall_reasons),
            )
        else:
            _company_cache_save(
                corp_id=corp_code,
                report=report,
                fiscal_year=current_fiscal_year,
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
            corp_type=judgment.corp_type,
            fragments_collected=len(frags),
            fragments_cited=len(report.citations),
            sentences_made=sentences_made,
            sentences_passed=len(written_claims),
            cost_krw=_request_spent_krw(engine),
            model=model,
            span_selection_diagnostics=tuple(selection_diagnostics),
            span_selection_result_reason=selection_result_reason_code,
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
) -> OfficialCompanyBundle | None:
    """DART 고유번호 하나의 기업개황·연간 원문·주요계정을 별도로 받는다."""

    profile = engine.get_json("company.json", {"corp_code": record.corp_code}, counter)
    if not isinstance(profile, dict) or profile.get("status") != DART_SUCCESS_STATUS:
        return None
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
    )
    financials, _years = engine.fetch_financials(record.corp_code, counter)
    official_text = ""
    if filing:
        path = engine.download_document(filing["rcept_no"], engine.RAW_DIR, counter)
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
    official_candidate_sentences: tuple[OfficialCandidateSentence, ...] = (),
    candidate_source_registry: tuple[Source, ...] = (),
) -> Report:
    """잠긴 1~8장 초안에 공식 양사 비교 9장을 붙여 내부 초안을 돌려준다."""

    records = _records_from_candidate_catalog(_company_catalog())
    self_bundle = OfficialCompanyBundle(
        corp_code=self_corp_code,
        company_name=self_company,
        financials=self_financials,
        filing=self_filing,
        official_text=self_official_text,
    )
    comparison = build_competitive_position(
        report,
        self_bundle=self_bundle,
        catalog=records,
        fetch_comparator=lambda record: _load_official_comparator_bundle(
            engine, counter, record
        ),
        collected_on=collected_on,
        official_candidate_sentences=official_candidate_sentences,
        candidate_source_registry=candidate_source_registry,
    )
    steps.append(
        {
            "step": "12_경쟁사비교",
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
        citations=[*report.citations, *comparison.sources],
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
      「작년 보고서가 계속 나가는」 구멍(정본 §2)을 막는 쪽으로 기운다.
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
) -> Optional[Report]:
    """회사분석 제품 namespace의 캐시만 조회한다."""
    if not corp_id:
        return None
    try:
        with storage_db.connect() as conn:
            hit = cache_store.get_company_report_hit(
                conn,
                corp_id=corp_id,
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


def _company_cache_save(
    *,
    corp_id: str,
    report: Report,
    fiscal_year: Optional[int],
) -> None:
    """신규 회사분석 보고서를 옛 직무 캐시와 격리해 저장한다."""
    if not corp_id:
        return
    try:
        with storage_db.connect() as conn:
            cache_store.save_company_report(
                conn,
                corp_id=corp_id,
                report=report,
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

    ★ 예산 소진·billing-uncertain 차단은 «이 요청 전역» 장애다 — composer가
      문장 단위 실패로 삼키면 실제 원인이 «출고 검증 실패»로 오표기된다
      (실측 결함). AskFatalError로 감싸 던져 composer의 삼킴 지점들이
      재전파하게 하고(_run_v2_composer가 다시 풀어 v1과 같은 FAILED로 끝낸다).
    """
    # composer는 v2 전용이라 지연 import한다 — v1 경로의 module 적재 비용을
    # 바꾸지 않기 위해서다.
    from src.features.composer.port import AskFatalError  # noqa: PLC0415

    def ask(prompt: str) -> str:
        try:
            with _meter_stage(engine, stage):
                response = client.messages.create(
                    model=getattr(engine, "MODEL", "") or GENERATION_MODEL,
                    max_tokens=max_tokens,
                    temperature=0,  # 원문 인용 충실도 우선 (1판 _ask와 동일)
                    messages=[{"role": "user", "content": prompt}],
                )
        except (
            provider_budget.ProviderBudgetExceeded,
            provider_budget.ProviderBudgetUnavailable,
        ) as error:
            raise AskFatalError(error) from error
        blocks = getattr(response, "content", None) or []
        return "".join(str(getattr(block, "text", "") or "") for block in blocks)

    return ask


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
) -> RunResult:
    """엔진 v2: composer 경로로 보고서를 만든다.

    v1 자산 재사용(04장 설계 개요): 수집 조각(frags)·법인 판정 결과·
    ``build_three_year_table`` 실적표를 그대로 받아 쓴다. 작가 ask와 검수 ask는
    «다른 클로저»로 주입한다 (Generator/Evaluator 분리).

    ★ v2 보고서는 1층 캐시에 저장하지 않는다 — 캐시는 canonical(v4)만
      반환·저장하는 계약이라 v2 스키마를 섞으면 v1 요청이 오염된다.
    """
    # composer는 v2 전용이라 지연 import한다 — v1 경로의 module 적재 비용·의존을
    # 바꾸지 않기 위해서다 (pipeline→composer 방향은 계획이 허용한 연결이다).
    from src.features.composer import pipeline as composer_pipeline  # noqa: PLC0415
    from src.features.composer.port import (  # noqa: PLC0415
        AskFatalError,
        composition_table_from_raw,
        filing_meta_from_raw,
        performance_table_from_report_table,
    )
    from src.features.composer.validate import V2ValidationError  # noqa: PLC0415

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
            filing_meta=filing_meta_from_raw(filing),
            composition_table=composition_table_from_raw(revenue_tables),
        )
    except AskFatalError as exc:
        # 예산 소진·billing-uncertain 같은 요청 전역 장애 — «출고 검증 실패»로
        # 오표기하지 않는다. 원인 예외를 그대로 다시 던져 v1과 같은 경로로
        # run()의 바깥 except가 FAILED로 정직하게 끝내게 한다.
        raise exc.cause from exc
    except V2ValidationError as exc:
        # v2 출고 3검사 실패 — 원문 없는 검증 사유만 운영 기록에 남긴다.
        logger.warning("엔진 v2 출고 검증 차단: %s", list(exc.problems))
        steps.append({"step": "v2_출고검증_차단", "사유": list(exc.problems)})
        return RunResult(
            outcome=Outcome.GATE_STOPPED,
            message=(
                "엔진 v2 출고 검증을 통과하지 못해 보고서를 내보내지 않았습니다. "
                "확인되지 않은 내용을 정상 보고서처럼 보여주지 않습니다."
                + _stop_reason_note(FINAL_GATE_REASON_PUBLISH_BLOCKED)
            ),
            sources=sources,
            corp_type=corp_type,
            fragments_collected=len(frags),
            cost_krw=_request_spent_krw(engine),
            model=model,
            final_gate_reason=FINAL_GATE_REASON_PUBLISH_BLOCKED,
        )

    report = output.report
    steps.append(
        {
            "step": "v2_composer_완료",
            "생성문장": output.composed_sentences,
            "생존문장": output.verified_sentences,
            "인용조각": len(report.citations),
        }
    )
    return RunResult(
        outcome=Outcome.REPORT,
        report=report,
        sources=sources,
        charged=True,  # 보고서가 나가면 1 차감 — v1과 같은 3분법
        corp_type=corp_type,
        fragments_collected=len(frags),
        fragments_cited=len(report.citations),
        sentences_made=output.composed_sentences,
        sentences_passed=output.verified_sentences,
        cost_krw=_request_spent_krw(engine),
        model=model,
    )


def _write_prose(
    engine: Any,
    client: Any,
    user_input: UserInput,
    sections: list[ReportSection],
    steps: list[dict[str, Any]],
    model: str,
) -> tuple[list[ReportSection], set[str]]:
    """11 작성 — 근거를 «하나의 글»로 잇는다 (P-110). AI 2회.

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

    # ★ 검증 뒤에도 문장별 근거를 버리지 않는다(P-118). 문자열 하나로 합치면
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
    """6 수집 — 뉴스. **AI가 번호를 고르고 프로그램이 원문을 복사한다** (P-108).

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
) -> tuple[dict[int, dict[str, str]], list[dict], str]:
    """6 수집 — 공시 원문 + 재무 API + 홈페이지를 조각으로 만든다. AI 0회.

    Args:
        financials: 재무 API 응답. ★ 여기서 다시 부르지 않는다 — 캐시 신선도를
            보려고 `run()`이 «이미» 불렀다. 두 번 부르면 DART 일일 한도만 깎는다.
        fin_years: 그때 실제로 자료가 있던 사업연도 목록 (단계 기록용).
        filing: 최신 공시 1건(보고서 이름·접수번호). 이것도 `run()`이 이미 받았다.
            **출처 목록을 만들 때 쓴다** (P-24). 공시를 못 찾았으면 None.

    Returns:
        조각 목록, 구조화 표, 실제로 내려받은 자사 공식 원문. 마지막 값은
        9장 비교에서 한쪽 자료만 있는 비교를 막는 데 다시 쓴다.
    """
    filing_text = ""
    if filing:
        try:
            path = engine.download_document(filing["rcept_no"], engine.RAW_DIR, counter)
            filing_text = engine.read_filing_text(path)
        except (RuntimeError, OSError) as exc:
            # 못 가져온 사실을 남긴다 — 조용히 넘어가면 「회사에 자료가 없다」로 잘못 읽힌다.
            steps.append({"step": "6_수집_원문", "오류": str(exc)[:120]})

    frags = engine.make_fragments(filing_text, financials)
    # ★ 1판은 절 표제의 «첫 출현»만 본다. 그런데 사업보고서 첫 장이 «목차»라,
    #   「사업의 내용」의 첫 출현이 목차 줄이고 거기서 1,200자를 뜨면 통째로 목차가 된다.
    #   실측 — 하이브 조각 9개 중 3개가 목차였고, 그래서 1·3·4번 칸이 비었다 (P-99).
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

    # ★ 1판이 «안 뜨는» 절을 더 모은다 (P-105) — 신규사업 전망·시장 특성·소송.
    #   사용자 지적(「그냥 DART 뜯어온 거라 이럴 거면 DART를 직접 보지」)의 핵심 원인이
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

    # 회사 홈페이지 — 2번(뭘 잘하나)이 만성적으로 비는 원인이었다 (문제로그 P-35 · D14-7).
    # ★ 실패를 「없음」과 반드시 구분한다. 섞으면 「이 회사는 자료가 없다」로 잘못 읽힌다.
    homepage = collect_homepage_fragments(
        profile.get("hm_url", ""),
        allow_dart_www_alias=True,
    )
    if homepage.state == "ok":
        for frag in homepage.fragments:
            # 최종 URL 검증 표식·문서 위치 등 수집기가 만든 provenance 메타데이터를
            # 버리지 않는다. build_citations가 닫힌 Source 필드만 골라 쓴다.
            frags[max(frags, default=0) + 1] = dict(frag)
        steps.append(
            {
                "step": "6_수집_홈페이지",
                "조각수": len(homepage.fragments),
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
        official_ir = collect_official_ir_fragments(
            str(profile.get("hm_url") or ""),
            company_name=str(profile.get("corp_name") or "").strip(),
            company_aliases=company_aliases,
            allow_dart_www_alias=True,
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
            steps.append(
                {
                    "step": "6_수집_공식IR",
                    "조각수": len(official_ir.fragments),
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

    # ★ 매출 구성 비중 표 (P-112) — 사용자가 리포트 11건에서 고른 항목 ①.
    #   **11건이 «전부» 실은 유일한 만장일치 항목**이다.
    #   ⚠️ 지어낼 자리가 없다 — 공시가 비중을 이미 계산해 놓았고 우리는 베낄 뿐이다.
    revenue_cite = _first_fragment_cite(frags, kind="매출수주") or REVENUE_CITE
    revenue_tables = revenuemix.build(filing_text, cite=revenue_cite)
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

    「강원 강릉시」와 「강원도 강릉시 …」는 같은 곳이다 (문제로그 P-26).
    데모와 같은 규칙이라 그쪽 함수를 그대로 쓴다 — 두 벌로 나뉘면 반드시 어긋난다.
    """
    from src.features.pipeline.demo import _region_matches as shared  # noqa: PLC0415

    return shared(typed, address)


def _message(outcome: Outcome) -> str:
    """실패했을 때 사용자에게 보여줄 말. 데모와 같은 문장을 쓴다."""
    from src.features.pipeline.demo import _OUTCOME_MESSAGE  # noqa: PLC0415

    return _OUTCOME_MESSAGE.get(outcome, "")
