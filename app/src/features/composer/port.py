"""composer 데이터 계약 (엔진 v2 생성 단계의 고정 계약).

★ 이 파일의 세 타입(ComposedSentence·ComposedSection·ComposedReport)은
  단계3 모든 소단계가 공유하는 «고정 계약»이다. 필드 변경·삭제 금지,
  꼭 필요하면 필드 «추가»만 허용한다.
★ composer는 pipeline·report_standard를 import 하지 않는다.
  파이프라인 쪽 실측 구조(조각 dict, ReportTable)는 아래 얇은 어댑터로 받는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final, Optional

from src.features.composer.constants import RCEPT_DT_LENGTH, SECTION_IDS
from src.features.provenance.sources import (
    evidence_text_hash,
    exact_evidence_text_hash,
    source_has_evidence_text,
)
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.official_ir import IR_METADATA_VERIFICATION_VALUE
from src.shared.report_evidence.constants import (
    FORMAL_DOCUMENT_SOURCE_KINDS,
    SOURCE_KIND_OFFICIAL_IR_PDF,
)
from src.shared.report_quality.comparison_claims import (
    comparison_context_claim_problems,
    comparison_program_problems,
    comparison_target_source_problems,
)
from src.shared.report_quality.comparison_basis import (
    comparison_basis_attester_source_ids,
    comparison_basis_v2_problems,
)
from src.shared.report_quality.comparison_numeric import comparison_numeric_problems
from src.shared.report_quality.constants import (
    COMPETITIVE_COMPARISON_CLAIM_TYPE,
    COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
)
from src.shared.report_quality.evidence_support import (
    MIN_PROSE_EVIDENCE_SUPPORT_TERMS,
    PROSE_CLAIM_TYPES,
    evidence_support_term_mismatches,
    normalized_support_terms,
)
from src.shared.revenue_table_provenance import is_revenue_total_name


class AskFatalError(Exception):
    """AI 호출이 «문장 내용 문제»가 아니라 «요청 전역 장애»로 죽었을 때만 쓴다.

    ★ 예산 소진(ProviderBudgetExceeded)·billing-uncertain 차단(요청 전체가
      더 못 부르는 상태)은 문장 하나의 실패가 아니라 이 요청 전체의 장애다.
      composer의 다른 모든 예외는 문장 단위로 삼켜 안내문·강등으로 바꾸지만,
      이 타입만은 어디서 잡히든 재전파해 pipeline.run_v2까지 뚫고 나가야
      한다 — 그래야 real.py가 v1과 같은 FAILED로 정직하게 끝낼 수 있다
      (전역 장애를 «검증 실패»로 오표기하지 않기 위함).

    ★ 예외의 예외 — «호출 «횟수» 상한»만은 다르다 (실측).
      돈이 떨어진 것이 아니라 «이 요청에 허락된 AI 호출 수»를 다 쓴 것이다.
      그때는 이미 만들어 둔 장·문장이 멀쩡히 손에 있는데도 보고서 전체가
      버려졌다(현대카드·우리은행 실측 — 완성된 9개 장이 통째로 사라지고
      화면에는 「보고서를 만들다 오류가 났습니다」만 남았다).
      선택적 다듬기(거짓 문장 «재작성»)에서 이 한도를 만나면, 다듬기를
      포기하고 «지금까지 만든 것»으로 끝내는 편이 정직하고 안전하다 —
      다듬지 못한 문장은 재작성 대신 «제거»되므로 검증은 오히려 더 보수적이다.
      `call_limit=True` 가 그 구분을 나른다. 돈·계정 장애는 여전히 False 다.

    ★ 예외의 예외 ② — «이 요청 하나에 미리 잡아 둔 예약액» 소진도 같다 (실측).
      2026-09-05 본조사에서 요청 로컬 예약액(단계 예약 잔액)이 다음 호출
      예상액을 감당하지 못하자 같은 결말이 났다 — 완성돼 가던 장이 통째로
      버려지고 화면에는 사유 없는 「보고서를 만들다 오류가 났습니다」만 남았다.
      이것은 «돈이 없다»가 아니라 «이 요청에 허락된 몫을 다 썼다»는 뜻이라
      횟수 상한과 성질이 같다. `request_budget=True` 가 그 구분을 나른다.
      일일·수명 상한과 계정 장애(ProviderBudgetUnavailable 등)는 요청을
      멈추는 게 맞으므로 두 깃발 모두 False 로 남는다 — 안전선 불변.
      선택적 단계의 강등 판정은 두 깃발을 합친 `degradable` 하나만 본다.
    """

    def __init__(
        self,
        cause: BaseException,
        *,
        call_limit: bool = False,
        request_budget: bool = False,
    ) -> None:
        self.cause = cause
        #: 호출 «횟수» 상한이라 선택적 단계를 포기하고 이어가도 되는가.
        self.call_limit = bool(call_limit)
        #: 요청 로컬 «예약액» 소진이라 선택적 단계를 포기하고 이어가도 되는가.
        self.request_budget = bool(request_budget)
        super().__init__(str(cause))

    @property
    def degradable(self) -> bool:
        """선택적 단계를 건너뛰고 «지금까지 만든 것»으로 끝내도 되는가.

        두 깃발을 여기서 한 번만 합친다 — 강등 지점이 네 곳이라 각자
        조건을 적으면 한 곳만 빠뜨려도 그 단계에서만 보고서가 버려진다.
        """
        return self.call_limit or self.request_budget


@dataclass(frozen=True)
class ComposedSentence:
    """작가 AI가 쓴 문장 하나."""

    #: 문장 본문 (한국어 산문)
    text: str
    #: 근거로 인용한 수집 조각 id들. 순수 «해석» 문장이면 빈 튜플 허용.
    citations: tuple[str, ...]
    #: "확인"(인용 원문에 직접 근거가 있는 사실) | "해석"(공식 자료 기반 분석·의미 부여)
    grade: str
    #: 생성 계획에서 작가가 선택한 원자 claim 자리. 누락·계약 밖 값은 빈칸이다.
    planned_claim_slot: str = ""
    #: 작가가 아니라 독립 검증기가 확정하는 상태. 기본은 절대 verified가 아니다.
    verification_state: str = "unverified"
    #: 프로그램이 구조화 원자료로 만든 claim만 갖는 손실 없는 결속 DTO.
    structured_claim: Optional["StructuredClaim"] = None
    #: 렌더가 만든 검증 FactRecord를 본문에서 글자 그대로 고른 요약에만
    #: 프로그램이 붙이는 ID. 작가 응답에서는 이 값을 읽지 않는다.
    verified_fact_id: str = ""


@dataclass(frozen=True)
class StructuredClaim:
    """텍스트 역추출 없이 공개 문장과 FactRecord를 1:1로 잇는 계약."""

    fact_id: str
    claim_slot: str
    section_owner: str
    source_fragment_id: str
    source_identity: str
    verification_state: str
    state_evidence: str
    subject_scope: str = ""
    metric: str = ""
    period_start: str = ""
    period_end: str = ""
    sign: str = ""
    unit: str = ""
    unit_dimension: str = ""
    formula: str = ""
    raw_value: str = ""
    calculation: str = ""
    display_value: str = ""
    rounding_rule: str = ""
    numeric_checks: tuple[str, ...] = ()


@dataclass(frozen=True)
class FlowRow:
    """사업 경로 한 줄 — «무엇으로 시작 / 회사가 하는 일 / 누구에게 닿나».

    ★ 한 줄이 한 «경로»다. 고객이 다르면 다른 줄이다. 이 규칙이 도식 결함
      세 가지(주 경로 누락·고객 혼동·지원 관계를 판매 경로에 놓기)를 구조적으로
      막는다 — 기존 flow 렌더러가 「표의 한 행 = 왼쪽→오른쪽 한 흐름」으로
      그리기 때문이다.
    """

    cells: tuple[str, ...]
    #: 이 줄의 근거 조각 id. 비면 근거 없는 줄이라 싣지 않는다.
    citations: tuple[str, ...] = ()


@dataclass(frozen=True)
class ComposedSection:
    """장 하나. 장 삭제 금지 — 자료가 부족해도 안내문으로 남긴다."""

    #: 기존 v3 정본 장 id 재사용 (identity … competitive_position)
    section_id: str
    #: 이 장의 문장들. 생성 실패 시 빈 튜플.
    sentences: tuple[ComposedSentence, ...]
    #: 자료 부족·생성 실패의 정직한 안내문. 문제없으면 "".
    notice: str = ""
    #: 7장 운영 경로표. 근거가 없으면 빈 튜플 — 빈 도식을 만들지 않는다.
    flow_rows: tuple[FlowRow, ...] = ()


@dataclass(frozen=True)
class ComposedReport:
    """v2 보고서 전체. summary는 소단계 3-3이 채운다 (그전까지 빈 튜플)."""

    sections: tuple[ComposedSection, ...]
    summary: tuple[ComposedSentence, ...] = ()


# ══════════════════════════════════════════════════════════
# 입력 어댑터 — 파이프라인 실측 구조를 얇게 감싼다
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CollectedFragment:
    """수집 조각 하나 — real.py의 `frags: dict[int, dict[str, str]]`를 감싼 것.

    실측 필드 대응: fragment_id ← dict 키(int), kind ← "종류", text ← "원문",
    source_url ← "출처"(홈페이지·공식 IR만), document_title ← "문서명"(공식 IR),
    location ← "원문위치"(공식 IR). 없는 필드는 빈 문자열.
    """

    fragment_id: str
    kind: str
    text: str
    source_url: str = ""
    document_title: str = ""
    location: str = ""
    #: packet raw Mapping이 가진 문서 기준일. legacy ``fragments_from_raw``는
    #: byte 호환을 위해 채우지 않고 packet 준비 경계에서만 보존한다.
    document_date: str = ""
    #: FULL typed packet이 수집 문서에서 확정한 독립 문서 신원. 임의 embedded
    #: fallback은 허용하지 않으며 SHADOW legacy 조각만 빈 값을 유지한다.
    document_identity: str = ""
    #: typed 공식 수집기가 봉인한 문서 전체 원문의 SHA-256. 같은 원문을
    #: URL·ID만 바꿔 여러 독립 문서로 세는 것을 유료 전 게이트가 막는다.
    #: legacy 조각은 문서 전체 바이트를 잃은 옛 모양이라 빈 문자열이다.
    document_content_sha256: str = ""
    #: typed 수집기가 이 정확한 원문 조각에 결속한 claim slot. legacy는
    #: 종류→장 범위밖에 모르므로 빈 튜플을 유지하며 의미 칸을 추측하지 않는다.
    supported_claim_slots: tuple[str, ...] = ()
    #: typed 수집 문서가 선언한 닫힌 source_kind. ``kind``는 transport 지문이라
    #: 사용자용 출처 종류를 복원할 수 없으므로 별도 필드로 끝까지 봉인한다.
    formal_source_kind: str = ""
    #: 수집 문서 내부 ID. 공식 웹은 URL identity를 쓰더라도 공개 부록이 원본
    #: 문서 ID를 잃지 않게 별도로 보존한다.
    source_document_id: str = ""
    #: typed 문서가 선언한 원자료 발행자와 회사 결속 proof. 공개 발행 법인명은
    #: 보고서 대상 회사로 다시 검산하되, 이 두 값은 packet·Source 도장까지 간다.
    source_publisher: str = ""
    identity_binding: str = ""
    source_collected_on: str = ""
    domain_attestation_source_id: str = ""
    domain_attestation_evidence: str = ""
    reporting_period: str = ""
    attachment_url: str = ""
    ir_metadata_verification: str = ""
    domain_redirect_verification: str = ""
    domain_redirect_from_host: str = ""
    domain_redirect_to_host: str = ""
    #: 프로그램 검증기가 이미 봉인한 Source. 일반 수집 조각은 ``None``이다.
    #: 경쟁사 비교처럼 분석 대상과 발행 법인이 다른 원문은 URL·제목만으로
    #: Source를 다시 만들면 비교사 발행자가 자사로 바뀌므로 원본 Source를
    #: packet 안에서 그대로 운반한다.
    bound_source: object | None = field(default=None, repr=False, compare=True)


@dataclass(frozen=True)
class VerifiedProgramEvidence:
    """AI가 쓰지 않은 검증 사실·출처·공개 문장을 한 장에 원자적으로 묶는다.

    현재 첫 생산자는 공식 양사 DART 비교기다. ``facts``만 넘기면 숨은 사실로
    품질 점수를 부풀릴 수 있으므로, 공개 문장과 인용 원문 조각을 정확히 1:1로
    함께 요구한다. ``registry_sources``에는 직접 인용 Source뿐 아니라 공식 웹
    소유권을 증명하는 ``attestation_only`` Source도 보존한다.
    """

    section_id: str
    source_fragments: tuple[CollectedFragment, ...]
    registry_sources: tuple[object, ...]
    facts: tuple[object, ...]
    sentences: tuple[ComposedSentence, ...]
    evidence_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        # 지연 import로 composer port의 기본 자료형 경계를 가볍게 유지한다.
        from src.features.pipeline.port import FactRecord  # noqa: PLC0415
        from src.features.provenance.sources import (  # noqa: PLC0415
            Source,
            SourceKind,
            full_typed_source_registry_problem,
            has_valid_provenance_seal,
            is_canonical_official_with_registry,
        )
        from src.shared.report_generation.models import canonical_value  # noqa: PLC0415
        from src.shared.report_quality.fact_binding import (  # noqa: PLC0415
            fact_evidence_binding,
            fact_primary_source_metadata_mismatches,
        )
        from src.shared.report_quality.source_identity import (  # noqa: PLC0415
            bound_source_fragment_provenance_mismatches,
            document_identity,
        )

        if self.section_id not in SECTION_IDS:
            raise ValueError("프로그램 근거의 장 식별자가 올바르지 않습니다")
        if not self.source_fragments or any(
            type(fragment) is not CollectedFragment
            for fragment in self.source_fragments
        ):
            raise TypeError("프로그램 근거에는 정확한 원문 조각 tuple이 필요합니다")
        if not self.registry_sources or any(
            type(source) is not Source for source in self.registry_sources
        ):
            raise TypeError("프로그램 근거에는 정확한 Source 등록부가 필요합니다")
        if any(
            not has_valid_provenance_seal(source)
            for source in self.registry_sources
        ):
            raise ValueError("프로그램 Source 등록부의 provenance 도장이 손상됐습니다")
        if not self.facts or any(type(fact) is not FactRecord for fact in self.facts):
            raise TypeError("프로그램 근거에는 정확한 FactRecord tuple이 필요합니다")
        if not self.sentences or any(
            type(sentence) is not ComposedSentence for sentence in self.sentences
        ):
            raise TypeError("프로그램 근거에는 정확한 공개 문장 tuple이 필요합니다")

        sources_by_id = {source.source_id: source for source in self.registry_sources}
        if (
            any(not source_id for source_id in sources_by_id)
            or len(sources_by_id) != len(self.registry_sources)
        ):
            raise ValueError("프로그램 Source ID가 비었거나 중복됐습니다")
        fragments_by_id = {
            fragment.fragment_id: fragment for fragment in self.source_fragments
        }
        if len(fragments_by_id) != len(self.source_fragments):
            raise ValueError("프로그램 원문 조각 ID가 중복됐습니다")
        source_id_by_fragment: dict[str, str] = {}
        for fragment in self.source_fragments:
            source = fragment.bound_source
            if type(source) is not Source:
                raise TypeError("프로그램 원문 조각의 봉인 Source가 누락됐습니다")
            registered_source = sources_by_id.get(source.source_id)
            if registered_source is None or source != registered_source:
                raise ValueError("프로그램 조각 Source가 등록부의 봉인 값과 다릅니다")
            if str(source.number) != fragment.fragment_id:
                raise ValueError("프로그램 조각 번호와 Source 번호가 다릅니다")
            provenance_mismatches = bound_source_fragment_provenance_mismatches(
                fragment,
                source,
            )
            if provenance_mismatches:
                raise ValueError(
                    "프로그램 원문 조각과 Source provenance가 다릅니다: "
                    + ",".join(provenance_mismatches)
                )
            exact_hash = exact_evidence_text_hash(fragment.text)
            if not exact_hash or exact_hash not in source.exact_evidence_hashes:
                raise ValueError("프로그램 원문 바이트가 Source 해시에 결속되지 않았습니다")
            source_document_identity = document_identity(source)
            if (
                not source_document_identity
                or fragment.document_identity != source_document_identity
            ):
                raise ValueError(
                    "프로그램 원문 조각과 Source의 독립 문서 신원이 다릅니다"
                )
            source_content_sha256 = source.document_content_sha256.strip()
            fragment_content_sha256 = fragment.document_content_sha256.strip()
            if source.formal_source_kind.strip() and (
                _SHA256_RE.fullmatch(source_content_sha256) is None
                or fragment_content_sha256 != source_content_sha256
            ):
                raise ValueError(
                    "프로그램 typed Source와 원문 조각의 문서 전체 지문이 다릅니다"
                )
            source_id_by_fragment[fragment.fragment_id] = source.source_id

        direct_source_ids = set(source_id_by_fragment.values())
        citation_source_ids = {
            source.source_id
            for source in self.registry_sources
            if source.provenance_role == "citation"
        }
        attester_source_ids = {
            source.source_id
            for source in self.registry_sources
            if source.provenance_role == "attestation_only"
        }
        if any(
            source.provenance_role not in {"citation", "attestation_only"}
            for source in self.registry_sources
        ):
            raise ValueError("프로그램 Source 등록부에 알 수 없는 역할이 있습니다")
        required_attester_ids = {
            sources_by_id[source_id].domain_attestation_source_id.strip()
            for source_id in direct_source_ids
            if sources_by_id[source_id].domain_attestation_source_id.strip()
        }
        required_attester_ids.update(
            comparison_basis_attester_source_ids(self.facts)
        )
        if any(
            sources_by_id[source_id].domain_attestation_source_id.strip()
            == source_id
            for source_id in direct_source_ids
        ):
            raise ValueError("프로그램 citation Source가 자기 자신을 증명할 수 없습니다")
        if citation_source_ids != direct_source_ids:
            raise ValueError(
                "프로그램 Source 등록부의 citation이 실제 인용 조각과 정확히 닫히지 않았습니다"
            )
        if attester_source_ids != required_attester_ids:
            raise ValueError(
                "프로그램 Source 등록부의 attester가 직접 참조 의존성과 다릅니다"
            )
        if any(
            sources_by_id[source_id].domain_attestation_source_id.strip()
            for source_id in attester_source_ids
        ):
            raise ValueError("프로그램 attester는 다른 attester를 연쇄 참조할 수 없습니다")
        registry_tuple = tuple(self.registry_sources)
        for source in self.registry_sources:
            if source.provenance_role == "citation" and not (
                is_canonical_official_with_registry(source, registry_tuple)
            ):
                raise ValueError(
                    "프로그램 citation Source가 공식 출처 등록부 계약을 "
                    "통과하지 못했습니다"
                )
            if source.provenance_role == "attestation_only" and (
                source.kind is not SourceKind.FILING
                or not is_canonical_official_with_registry(source, registry_tuple)
            ):
                raise ValueError(
                    "프로그램 attester Source가 공식 공시 증명 계약을 "
                    "통과하지 못했습니다"
                )
            if not source.formal_source_kind.strip():
                continue
            reference_date = (
                source.collected_at
                or source.published_at
                or source.disclosed_at
            )
            problem = full_typed_source_registry_problem(
                source,
                registry_tuple,
                reference_date=reference_date,
            )
            if problem:
                raise ValueError(
                    "프로그램 typed Source 등록부 계약이 손상됐습니다: " + problem
                )

        facts_by_id = {fact.fact_id: fact for fact in self.facts}
        if any(not fact_id for fact_id in facts_by_id) or len(facts_by_id) != len(
            self.facts
        ):
            raise ValueError("프로그램 FactRecord ID가 비었거나 중복됐습니다")
        semantic_fact_keys = tuple(
            (
                fact.section_owner,
                " ".join(fact.claim.split()).casefold(),
                fact.claim_slot,
                fact.claim_type,
                tuple(
                    zip(
                        fact.supporting_source_ids,
                        fact.supporting_source_identities,
                        fact.supporting_evidence_hashes,
                    )
                ),
            )
            for fact in self.facts
        )
        if len(semantic_fact_keys) != len(set(semantic_fact_keys)):
            raise ValueError(
                "프로그램 FactRecord가 ID만 바꾼 같은 의미 사실을 중복했습니다"
            )
        for fact in self.facts:
            if (
                fact.section_owner != self.section_id
                or fact.status != "verified"
                or fact.verification_status != "verified"
                or fact.claim_slot not in CLAIM_SLOTS_BY_SECTION[self.section_id]
                or not fact.evidence_binding
                or fact.evidence_binding != fact_evidence_binding(fact)
            ):
                raise ValueError(
                    "프로그램 FactRecord의 장·검증·claim slot 결속이 깨졌습니다: "
                    f"{fact.fact_id}"
                )
            primary_source = sources_by_id.get(fact.source_id)
            metadata_mismatches = (
                ()
                if primary_source is None
                else fact_primary_source_metadata_mismatches(fact, primary_source)
            )
            if (
                primary_source is None
                or fact.source_id not in direct_source_ids
                or metadata_mismatches
            ):
                raise ValueError(
                    "프로그램 FactRecord 대표 출처 메타데이터가 Source와 다릅니다: "
                    + ",".join(metadata_mismatches)
                )
            if not source_has_evidence_text(primary_source, fact.state_evidence):
                raise ValueError(
                    "프로그램 FactRecord의 state_evidence가 Source 원문 등록부에 "
                    "없습니다"
                )
            if fact.claim_type in {
                COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
                COMPETITIVE_COMPARISON_CLAIM_TYPE,
            }:
                comparator_source = sources_by_id.get(fact.comparator_source_id)
                target_source_problems = comparison_target_source_problems(
                    fact,
                    comparator_source,
                )
                if (
                    comparator_source is None
                    or comparator_source.source_id not in direct_source_ids
                    or target_source_problems
                ):
                    raise ValueError(
                        "프로그램 비교 대상과 비교사 공식 Source가 다릅니다: "
                        + "; ".join(target_source_problems)
                    )
                if not source_has_evidence_text(
                    comparator_source,
                    fact.comparator_state_evidence,
                ):
                    raise ValueError(
                        "프로그램 비교사 state_evidence가 비교사 Source 원문 "
                        "등록부에 없습니다"
                    )
            support_terms = normalized_support_terms(fact.evidence_support_terms)
            if fact.claim_type == COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE:
                context_problems = comparison_context_claim_problems(fact)
                if context_problems:
                    raise ValueError(
                        "프로그램 FactRecord의 claim·근거어·원문 결속이 다릅니다: "
                        + "; ".join(context_problems)
                    )
            else:
                unsupported_terms = evidence_support_term_mismatches(
                    fact.claim,
                    fact.state_evidence,
                    support_terms,
                )
                requires_prose_terms = fact.claim_type in PROSE_CLAIM_TYPES
                if unsupported_terms or (
                    requires_prose_terms
                    and len(support_terms) < MIN_PROSE_EVIDENCE_SUPPORT_TERMS
                ):
                    raise ValueError(
                        "프로그램 FactRecord의 claim·근거어·원문 결속이 다릅니다: "
                        + ",".join(unsupported_terms)
                    )
            if fact.claim_type == COMPETITIVE_COMPARISON_CLAIM_TYPE:
                comparison_problems = comparison_numeric_problems(fact)
                if comparison_problems:
                    raise ValueError(
                        "프로그램 비교 수치 문장이 재계산 정본과 다릅니다: "
                        + "; ".join(comparison_problems)
                    )
            supporting_source_ids = tuple(fact.supporting_source_ids)
            supporting_identities = tuple(fact.supporting_source_identities)
            supporting_hashes = tuple(fact.supporting_evidence_hashes)
            triples = tuple(
                zip(
                    supporting_source_ids,
                    supporting_identities,
                    supporting_hashes,
                )
            )
            if (
                not triples
                or len(supporting_source_ids)
                != len(supporting_identities)
                or len(supporting_source_ids) != len(supporting_hashes)
                or supporting_source_ids[0] != fact.source_id
                or len({item[0] for item in triples}) != len(triples)
            ):
                raise ValueError("프로그램 FactRecord의 다중 출처 결속이 비었습니다")
            for source_id, identity, evidence_hash in triples:
                source = sources_by_id.get(source_id)
                if (
                    source is None
                    or document_identity(source) != identity
                    or evidence_hash not in source.exact_evidence_hashes
                ):
                    raise ValueError("프로그램 FactRecord와 Source 등록부가 다릅니다")
            if fact.comparator_source_id in supporting_source_ids:
                comparator_terms = normalized_support_terms(
                    fact.comparator_evidence_support_terms
                )
                comparator_term_mismatches = evidence_support_term_mismatches(
                    fact.claim,
                    fact.comparator_state_evidence,
                    comparator_terms,
                )
                if (
                    len(comparator_terms) < MIN_PROSE_EVIDENCE_SUPPORT_TERMS
                    or comparator_term_mismatches
                ):
                    raise ValueError(
                        "프로그램 비교사 원문의 근거어가 claim과 원문에 "
                        "결속되지 않았습니다: "
                        + ",".join(comparator_term_mismatches)
                    )

        sentence_fact_ids = tuple(sentence.verified_fact_id for sentence in self.sentences)
        if (
            any(not fact_id for fact_id in sentence_fact_ids)
            or len(sentence_fact_ids) != len(set(sentence_fact_ids))
            or set(sentence_fact_ids) != set(facts_by_id)
        ):
            raise ValueError("프로그램 공개 문장과 FactRecord가 1:1이 아닙니다")
        visible_sentence_keys = tuple(
            (
                " ".join(sentence.text.split()).casefold(),
                sentence.planned_claim_slot,
                tuple(sentence.citations),
            )
            for sentence in self.sentences
        )
        if len(visible_sentence_keys) != len(set(visible_sentence_keys)):
            raise ValueError(
                "프로그램 공개 문장이 fact_id만 바꾼 같은 내용으로 중복됐습니다"
            )
        cited_fragment_ids: set[str] = set()
        for sentence in self.sentences:
            fact = facts_by_id[sentence.verified_fact_id]
            cited_fragments = tuple(
                fragments_by_id.get(fragment_id)
                for fragment_id in sentence.citations
            )
            cited_triples = tuple(
                (
                    source_id_by_fragment.get(fragment.fragment_id, ""),
                    fragment.document_identity,
                    exact_evidence_text_hash(fragment.text),
                )
                for fragment in cited_fragments
                if fragment is not None
            )
            fact_triples = tuple(
                zip(
                    fact.supporting_source_ids,
                    fact.supporting_source_identities,
                    fact.supporting_evidence_hashes,
                )
            )
            primary_cited_fragments = tuple(
                fragment
                for fragment in cited_fragments
                if fragment is not None
                and source_id_by_fragment.get(fragment.fragment_id, "")
                == fact.source_id
            )
            comparator_cited_fragments = tuple(
                fragment
                for fragment in cited_fragments
                if fragment is not None
                and source_id_by_fragment.get(fragment.fragment_id, "")
                == fact.comparator_source_id
            )
            comparator_is_supporting = (
                fact.comparator_source_id in fact.supporting_source_ids
            )
            if (
                sentence.verification_state != "verified"
                or sentence.text != fact.claim
                or sentence.planned_claim_slot != fact.claim_slot
                or len(cited_fragments) != len(sentence.citations)
                or any(fragment is None for fragment in cited_fragments)
                or not cited_triples
                or any(not all(value for value in triple) for triple in cited_triples)
                or cited_triples != fact_triples
                or len(primary_cited_fragments) != 1
                or evidence_text_hash(primary_cited_fragments[0].text)
                != evidence_text_hash(fact.state_evidence)
                or (
                    comparator_is_supporting
                    and (
                        len(comparator_cited_fragments) != 1
                        or evidence_text_hash(comparator_cited_fragments[0].text)
                        != evidence_text_hash(fact.comparator_state_evidence)
                    )
                )
                or any(
                    fact.claim_slot not in fragment.supported_claim_slots
                    for fragment in cited_fragments
                    if fragment is not None
                )
            ):
                raise ValueError("프로그램 공개 문장과 사실·인용 결속이 다릅니다")
            cited_fragment_ids.update(sentence.citations)
        if cited_fragment_ids != set(fragments_by_id):
            raise ValueError(
                "프로그램 원문 조각이 공개 Fact·문장 인용과 정확히 닫히지 않았습니다"
            )
        program_comparison_problems = comparison_program_problems(self.facts)
        if program_comparison_problems:
            raise ValueError(
                "프로그램 비교 사실 집합이 하나의 장부로 닫히지 않았습니다: "
                + "; ".join(program_comparison_problems)
            )
        comparison_facts = tuple(
            fact
            for fact in self.facts
            if fact.claim_type
            in {
                COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
                COMPETITIVE_COMPARISON_CLAIM_TYPE,
            }
        )
        if comparison_facts:
            basis_problems = comparison_basis_v2_problems(
                comparison_facts[0],
                sources_by_id,
            )
            if basis_problems:
                raise ValueError(
                    "프로그램 비교 후보 basis가 Source 등록부와 다릅니다: "
                    + "; ".join(basis_problems)
                )

        payload = {
            "version": 1,
            "section_id": self.section_id,
            "source_fragments": [
                {
                    "fragment_id": fragment.fragment_id,
                    "text_sha256": exact_evidence_text_hash(fragment.text),
                    "document_identity": fragment.document_identity,
                    "document_content_sha256": fragment.document_content_sha256,
                    "source": canonical_value(fragment.bound_source),
                }
                for fragment in self.source_fragments
            ],
            "registry_sources": [
                canonical_value(source) for source in self.registry_sources
            ],
            "facts": [canonical_value(fact) for fact in self.facts],
            "sentences": [canonical_value(sentence) for sentence in self.sentences],
        }
        object.__setattr__(
            self,
            "evidence_sha256",
            hashlib.sha256(_packet_json(payload).encode("utf-8")).hexdigest(),
        )


_GEN8_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{8}")
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")


def _packet_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


@dataclass(frozen=True)
class SectionEvidencePacket:
    """한 회사·한 수집 generation·한 장에 고정된 FULL 작성 입력."""

    company_id: str
    evidence_generation_sha256: str
    section_id: str
    fragments: tuple[CollectedFragment, ...]
    program_evidence: VerifiedProgramEvidence | None = None
    packet_sha256: str = field(init=False)

    def __post_init__(self) -> None:
        if (
            type(self.company_id) is not str
            or self.company_id != self.company_id.strip()
            or type(self.evidence_generation_sha256) is not str
            or self.evidence_generation_sha256
            != self.evidence_generation_sha256.strip()
        ):
            raise ValueError("section packet 회사·generation 형식이 손상됐습니다")
        company_id = self.company_id
        generation = self.evidence_generation_sha256
        if _GEN8_RE.fullmatch(company_id) is None:
            raise ValueError("section packet의 company_id는 gen8이어야 합니다")
        if _SHA256_RE.fullmatch(generation) is None:
            raise ValueError("section packet의 evidence generation은 SHA-256이어야 합니다")
        if type(self.section_id) is not str or self.section_id not in SECTION_IDS:
            raise ValueError(f"알 수 없는 section packet 장입니다: {self.section_id!r}")
        if type(self.fragments) is not tuple or any(
            type(fragment) is not CollectedFragment for fragment in self.fragments
        ):
            raise TypeError("section packet 조각은 정확한 CollectedFragment tuple이어야 합니다")
        if not self.fragments:
            raise ValueError("FULL section packet은 빈 조각 묶음일 수 없습니다")
        fragment_ids = tuple(fragment.fragment_id for fragment in self.fragments)
        if len(fragment_ids) != len(set(fragment_ids)):
            raise ValueError("section packet의 fragment_id가 중복됐습니다")
        for fragment in self.fragments:
            if any(
                type(value) is not str
                for value in (
                    fragment.fragment_id,
                    fragment.kind,
                    fragment.text,
                    fragment.source_url,
                    fragment.document_title,
                    fragment.location,
                    fragment.document_date,
                    fragment.document_identity,
                    fragment.document_content_sha256,
                    fragment.formal_source_kind,
                    fragment.source_document_id,
                    fragment.source_publisher,
                    fragment.identity_binding,
                    fragment.source_collected_on,
                    fragment.domain_attestation_source_id,
                    fragment.domain_attestation_evidence,
                    fragment.reporting_period,
                    fragment.attachment_url,
                    fragment.ir_metadata_verification,
                    fragment.domain_redirect_verification,
                    fragment.domain_redirect_from_host,
                    fragment.domain_redirect_to_host,
                )
            ):
                raise TypeError("section packet 조각 필드는 문자열이어야 합니다")
            if type(fragment.supported_claim_slots) is not tuple or any(
                type(slot_id) is not str
                or not slot_id
                or slot_id != slot_id.strip()
                for slot_id in fragment.supported_claim_slots
            ):
                raise TypeError("section packet 지원 claim slot은 문자열 tuple이어야 합니다")
            if len(fragment.supported_claim_slots) != len(
                set(fragment.supported_claim_slots)
            ):
                raise ValueError("section packet 지원 claim slot이 중복됐습니다")
            known_claim_slots = {
                slot_id
                for section_slots in CLAIM_SLOTS_BY_SECTION.values()
                for slot_id in section_slots
            }
            if set(fragment.supported_claim_slots) - known_claim_slots:
                raise ValueError("section packet에 알 수 없는 지원 claim slot이 있습니다")
            if fragment.supported_claim_slots and not (
                set(fragment.supported_claim_slots)
                & set(CLAIM_SLOTS_BY_SECTION[self.section_id])
            ):
                raise ValueError(
                    "section packet 조각이 현재 장의 claim slot을 하나도 지원하지 않습니다"
                )
            if not fragment.fragment_id.strip() or not fragment.text.strip():
                raise ValueError("section packet 조각의 id·원문은 비울 수 없습니다")
            identity = fragment.document_identity.strip()
            if not identity or identity.startswith("embedded:"):
                raise ValueError(
                    "FULL section packet에는 검증된 비-embedded 문서 신원이 필요합니다"
                )
            content_sha256 = fragment.document_content_sha256.strip()
            if content_sha256 and _SHA256_RE.fullmatch(content_sha256) is None:
                raise ValueError("section packet 문서 원문 SHA-256 형식이 올바르지 않습니다")
            formal_kind = fragment.formal_source_kind.strip()
            typed_only_extras = (
                fragment.source_publisher.strip(),
                fragment.identity_binding.strip(),
                fragment.source_collected_on.strip(),
            )
            if formal_kind:
                if (
                    formal_kind not in FORMAL_DOCUMENT_SOURCE_KINDS
                    or not content_sha256
                    or not fragment.source_document_id.strip()
                    or not all(typed_only_extras)
                ):
                    raise ValueError(
                        "FULL typed 조각의 자료종류·문서지문·발행자·회사 결속이 불완전합니다"
                    )
            elif any(typed_only_extras):
                raise ValueError(
                    "legacy 조각에 typed 출처 메타데이터를 일부만 넣을 수 없습니다"
                )
            attestation_parts = (
                fragment.domain_attestation_source_id.strip(),
                fragment.domain_attestation_evidence.strip(),
            )
            if bool(attestation_parts[0]) != bool(attestation_parts[1]):
                raise ValueError("도메인 attestation Source ID와 exact 원문이 갈렸습니다")
            if formal_kind == SOURCE_KIND_OFFICIAL_IR_PDF and (
                not fragment.document_date.strip()
                or not fragment.reporting_period.strip()
                or not fragment.attachment_url.strip()
                or fragment.ir_metadata_verification.strip()
                != IR_METADATA_VERIFICATION_VALUE
                or not all(attestation_parts)
            ):
                raise ValueError("FULL typed 공식 IR의 시점·첨부·도메인 근거가 불완전합니다")
        bound_fragments = {
            fragment.fragment_id: fragment
            for fragment in self.fragments
            if fragment.bound_source is not None
        }
        if self.program_evidence is not None:
            if type(self.program_evidence) is not VerifiedProgramEvidence:
                raise TypeError("section packet 프로그램 근거 형식이 올바르지 않습니다")
            if self.program_evidence.section_id != self.section_id:
                raise ValueError("section packet과 프로그램 근거의 장이 다릅니다")
            program_fragments = {
                fragment.fragment_id: fragment
                for fragment in self.program_evidence.source_fragments
            }
            if (
                bound_fragments.keys() != program_fragments.keys()
                or any(
                    bound_fragments[fragment_id] != program_fragment
                    for fragment_id, program_fragment in program_fragments.items()
                )
            ):
                raise ValueError(
                    "section packet의 봉인 Source 조각과 프로그램 원문 조각이 다릅니다"
                )
        elif bound_fragments:
            raise ValueError(
                "봉인 Source 조각은 같은 section packet의 프로그램 근거에 결속돼야 합니다"
            )
        payload = {
            # 문서 전체 hash와 지원 claim slot이 봉인 대상에 추가된 두 번째
            # 계약이다. 필드를 바꾸고 이전 version을 유지하면 같은 버전 이름이
            # 배포 시점마다 다른 바이트를 뜻하게 된다.
            "version": 4,
            "company_id": company_id,
            "evidence_generation_sha256": generation,
            "section_id": self.section_id,
            "fragments": [
                {
                    "fragment_id": fragment.fragment_id,
                    "kind": fragment.kind,
                    "text_sha256": exact_evidence_text_hash(fragment.text),
                    "source_url": fragment.source_url,
                    "document_title": fragment.document_title,
                    "location": fragment.location,
                    "document_date": fragment.document_date,
                    "document_identity": fragment.document_identity,
                    "document_content_sha256": fragment.document_content_sha256,
                    "supported_claim_slots": fragment.supported_claim_slots,
                    "formal_source_kind": fragment.formal_source_kind,
                    "source_document_id": fragment.source_document_id,
                    "source_publisher": fragment.source_publisher,
                    "identity_binding": fragment.identity_binding,
                    "source_collected_on": fragment.source_collected_on,
                    "domain_attestation_source_id": (
                        fragment.domain_attestation_source_id
                    ),
                    "domain_attestation_evidence": (
                        fragment.domain_attestation_evidence
                    ),
                    "reporting_period": fragment.reporting_period,
                    "attachment_url": fragment.attachment_url,
                    "ir_metadata_verification": fragment.ir_metadata_verification,
                    "domain_redirect_verification": (
                        fragment.domain_redirect_verification
                    ),
                    "domain_redirect_from_host": fragment.domain_redirect_from_host,
                    "domain_redirect_to_host": fragment.domain_redirect_to_host,
                }
                for fragment in self.fragments
            ],
            "program_evidence_sha256": (
                self.program_evidence.evidence_sha256
                if self.program_evidence is not None
                else ""
            ),
        }
        object.__setattr__(self, "company_id", company_id)
        object.__setattr__(self, "evidence_generation_sha256", generation)
        object.__setattr__(
            self,
            "packet_sha256",
            hashlib.sha256(_packet_json(payload).encode("utf-8")).hexdigest(),
        )


@dataclass(frozen=True)
class SectionEvidencePacketSet:
    """정책 순서의 typed 아홉 장 packet을 한 회사 generation에 묶는다."""

    company_id: str
    evidence_generation_sha256: str
    packets: tuple[SectionEvidencePacket, ...]

    def __post_init__(self) -> None:
        if (
            type(self.company_id) is not str
            or self.company_id != self.company_id.strip()
            or type(self.evidence_generation_sha256) is not str
            or self.evidence_generation_sha256
            != self.evidence_generation_sha256.strip()
        ):
            raise ValueError("packet set 회사·generation 형식이 손상됐습니다")
        company_id = self.company_id
        generation = self.evidence_generation_sha256
        if _GEN8_RE.fullmatch(company_id) is None:
            raise ValueError("packet set의 company_id는 gen8이어야 합니다")
        if _SHA256_RE.fullmatch(generation) is None:
            raise ValueError("packet set의 evidence generation은 SHA-256이어야 합니다")
        if type(self.packets) is not tuple or any(
            type(packet) is not SectionEvidencePacket for packet in self.packets
        ):
            raise TypeError("packet set에는 정확한 SectionEvidencePacket tuple이 필요합니다")
        if tuple(packet.section_id for packet in self.packets) != SECTION_IDS:
            raise ValueError("packet set에는 정책 순서의 typed 아홉 장이 필요합니다")
        if any(packet.company_id != company_id for packet in self.packets):
            raise ValueError("다른 회사의 section packet을 섞을 수 없습니다")
        if any(
            packet.evidence_generation_sha256 != generation
            for packet in self.packets
        ):
            raise ValueError("다른 evidence generation의 section packet을 섞을 수 없습니다")
        object.__setattr__(self, "company_id", company_id)
        object.__setattr__(self, "evidence_generation_sha256", generation)

    @property
    def packet_sha256s(self) -> tuple[tuple[str, str], ...]:
        return tuple(
            (packet.section_id, packet.packet_sha256) for packet in self.packets
        )


def fragments_from_raw(
    raw: Mapping[int, Mapping[str, Any]]
) -> tuple[CollectedFragment, ...]:
    """파이프라인 조각 dict를 CollectedFragment 튜플로 바꾼다.

    ★ 원문이 빈 조각은 뺀다 — 인용해도 대조할 원문이 없어 근거가 못 되기 때문이다.
      (내용을 보고 거르는 게 아니라 «비어 있는가»만 본다.)
    """
    out: list[CollectedFragment] = []
    for number in sorted(raw):
        item = raw[number]
        text = str(item.get("원문") or "").strip()
        if not text:
            continue
        out.append(
            CollectedFragment(
                fragment_id=str(number),
                kind=str(item.get("종류") or "").strip(),
                text=text,
                source_url=str(item.get("출처") or "").strip(),
                document_title=str(item.get("문서명") or "").strip(),
                location=str(item.get("원문위치") or "").strip(),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class PerformanceTable:
    """프로그램이 만든 3개년 실적표 — 작가 AI에게 근거로 주는 표.

    파이프라인 `ReportTable`(canonical_report.py의 table_facts 원천)을
    composer가 직접 import 하지 않으려고 얇게 복사한 모양이다.
    """

    caption: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    unit: str = ""
    cite: str = ""
    raw_rows: tuple[tuple[str, ...], ...] = ()
    scale_divisor: str = ""
    scale_places: int = 0
    evidence_rows: tuple[str, ...] = ()
    entity_scope: str = ""
    raw_unit: str = ""
    unit_dimension: str = ""
    #: 행이 원문 직접 결속 대신 이미 검증된 프로그램 사실을 주입할 때 쓰는 ID.
    #: 있으면 rows와 같은 길이여야 하며 manifest canonicalizer가 검증한다.
    row_fact_ids: tuple[str, ...] = ()


def performance_table_from_report_table(table: Any) -> PerformanceTable:
    """파이프라인 ReportTable을 덕 타이핑으로 감싼다 (직접 import 회피)."""
    return PerformanceTable(
        caption=str(getattr(table, "caption", "") or ""),
        headers=tuple(str(h) for h in (getattr(table, "headers", None) or ())),
        rows=tuple(
            tuple(str(cell) for cell in row)
            for row in (getattr(table, "rows", None) or ())
        ),
        unit=str(getattr(table, "display_unit", "") or ""),
        cite=str(getattr(table, "cite", "") or ""),
        raw_rows=tuple(
            tuple(str(cell) for cell in row)
            for row in (getattr(table, "raw_rows", None) or ())
        ),
        scale_divisor=str(getattr(table, "scale_divisor", "") or ""),
        scale_places=int(getattr(table, "scale_places", 0) or 0),
        evidence_rows=tuple(
            str(value) for value in (getattr(table, "evidence_rows", None) or ())
        ),
        entity_scope=str(getattr(table, "entity_scope", "") or ""),
        raw_unit=str(getattr(table, "raw_unit", "") or ""),
        unit_dimension=str(getattr(table, "unit_dimension", "") or ""),
        row_fact_ids=tuple(
            str(value) for value in (getattr(table, "row_fact_ids", None) or ())
        ),
    )


# ══════════════════════════════════════════════════════════
# 공시 신원 — 부록 출처에 «원문 주소»를 싣기 위한 어댑터
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FilingMeta:
    """이번 조사가 실제로 내려받은 공시 1건의 신원.

    ★ 왜 이 타입이 필요한가 (실측 결함) — 전자공시 절 조각(사업내용·MD&A 등)에는
      조각 자체에 주소가 없다. 주소를 가진 것은 조각이 아니라 «그 조각을 떠 온
      문서»다. 그런데 v2는 그 문서 신원을 render까지 넘기지 않아, 현대자동차
      실측에서 부록 출처 12건 중 11건이 「주소 없음」으로 나갔다.
      독자가 원문을 열 수 없으면 근거 표기는 장식일 뿐이다.
    ★ v1은 같은 정보를 provenance/citations.py에서 이미 쓰고 있다. v1 경로는
      건드리지 않고(v1 경로는 그대로 둔다), v2가 같은 재료를 받아 쓰게만 한다.
    """

    #: 공시 접수번호 (`rcept_no`). 이것이 있어야 원문 주소를 만들 수 있다.
    document_id: str = ""
    #: 보고서 이름 (`report_nm`). 예: "반기보고서 (2026.06)".
    title: str = ""
    #: 공시일 `YYYY-MM-DD`. 원래 모양이 아니면 비운다 (지어내지 않는다).
    disclosed_at: str = ""


def _format_disclosed_at(raw: str) -> str:
    """DART 공시일(`YYYYMMDD`) → `"YYYY-MM-DD"`. 모양이 안 맞으면 빈 문자열.

    ★ 날짜를 지어내지 않는다 — 틀린 공시일은 없는 공시일보다 나쁘다.
      v1 `provenance/citations.py._format_rcept_dt`와 같은 규칙이다.
    """
    digits = raw.strip()
    if len(digits) != RCEPT_DT_LENGTH or not digits.isdigit():
        return ""
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def filing_meta_from_raw(filing: Any) -> Optional[FilingMeta]:
    """real.py의 공시 dict(`rcept_no`·`report_nm`·`rcept_dt`)를 FilingMeta로.

    접수번호가 없으면 주소를 만들 수 없으므로 ``None``을 돌려준다 — 이때는
    부록이 예전처럼 주소 없이 나가며, 그 사실이 화면에 그대로 보인다.
    """
    if not isinstance(filing, Mapping):
        return None
    document_id = str(filing.get("rcept_no") or filing.get("rceptNo") or "").strip()
    if not document_id:
        return None
    return FilingMeta(
        document_id=document_id,
        title=str(filing.get("report_nm") or "").strip(),
        disclosed_at=_format_disclosed_at(str(filing.get("rcept_dt") or "")),
    )


#: 비중 열을 알아보는 말.
_RATIO_HEADER_HINTS: Final[tuple[str, ...]] = ("비중", "%")


def _composition_shape(
    headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]
) -> tuple[tuple[str, ...], tuple[tuple[str, ...], ...]]:
    """구성 도식이 그려질 수 있는 «항목 + 비중» 두 열 모양으로 줄인다.

    ★ 왜 필요한가 (실측) — `revenuemix`가 만드는 표는 「구분 · 금액 · 비중」
      3열이고 «합계» 행이 붙는다. 그런데 도식 판정기는 「정확히 2열 · 합계 행
      없음 · 3~5행」일 때만 100% 누적 막대를 그린다. 그래서 진영 실측에서
      **표는 붙었는데 도식은 안 그려졌다.**
    ★ v1이 쓰는 `revenuemix`를 고치지 않는다(v1 무변). 여기서 «도식용 모양»만
      만든다.
    ★ 줄이는 것뿐이고 값을 바꾸거나 만들지 않는다. 비중 열을 못 찾거나 항목
      수가 안 맞으면 원래 모양을 그대로 돌려준다 — 그러면 표로만 나간다.
      억지로 도식을 만들지 않는다.
    """
    shaped_headers, shaped_rows, _, _ = _composition_projection(headers, rows)
    return shaped_headers, shaped_rows


def _composition_projection(
    headers: tuple[str, ...], rows: tuple[tuple[str, ...], ...]
) -> tuple[
    tuple[str, ...],
    tuple[tuple[str, ...], ...],
    tuple[int, ...],
    tuple[int, ...],
]:
    """구성표 변환과 근거 변환이 공유하는 행·열 index 계획."""

    identity_rows = tuple(range(len(rows)))
    identity_columns = tuple(range(len(headers)))
    if len(headers) <= 2:
        return headers, rows, identity_rows, identity_columns
    ratio_indices = tuple(
        index
        for index in range(1, len(headers))
        if any(hint in headers[index] for hint in _RATIO_HEADER_HINTS)
    )
    year_ratio_indices = tuple(
        (int(match.group(0)), index)
        for index in ratio_indices
        if (match := re.search(r"(?<!\d)20\d{2}(?!\d)", headers[index]))
        is not None
    )
    # 연도가 명시된 표는 열 순서가 아니라 가장 큰 연도를 고른다. 연도가 없는
    # 기존 표는 오른쪽 비중 열을 고르던 순서를 그대로 유지한다.
    ratio_index = (
        max(year_ratio_indices)[1]
        if year_ratio_indices
        else ratio_indices[-1]
        if ratio_indices
        else None
    )
    if ratio_index is None:
        return headers, rows, identity_rows, identity_columns
    kept_indices = tuple(
        index
        for index, row in enumerate(rows)
        if len(row) > ratio_index
        and not (
            is_revenue_total_name(row[0])
            or re.sub(r"\s+", "", str(row[0])) == "소계"
        )
    )
    if len(kept_indices) < 3:
        # 도식 판정기의 하한(3행)에 못 미친다 — 원표를 그대로 두는 편이 낫다.
        return headers, rows, identity_rows, identity_columns
    column_indices = (0, ratio_index)
    trimmed = tuple(
        tuple(rows[row_index][column_index] for column_index in column_indices)
        for row_index in kept_indices
    )
    return (
        (headers[0], headers[ratio_index]),
        trimmed,
        kept_indices,
        column_indices,
    )


def _project_rows(
    rows: tuple[tuple[str, ...], ...],
    *,
    row_indices: tuple[int, ...],
    column_indices: tuple[int, ...],
) -> tuple[tuple[str, ...], ...]:
    """공개 행과 같은 index 계획으로 원자료 행을 줄인다."""

    if not rows:
        return ()
    try:
        return tuple(
            tuple(rows[row_index][column_index] for column_index in column_indices)
            for row_index in row_indices
        )
    except IndexError:
        # 깨진 입력을 조용히 고쳐 쓰지 않는다. 뒤의 manifest가 불일치로 거절한다.
        return rows


def _project_parallel_values(
    values: tuple[str, ...], *, row_indices: tuple[int, ...]
) -> tuple[str, ...]:
    if not values:
        return ()
    try:
        return tuple(values[index] for index in row_indices)
    except IndexError:
        return values


def composition_tables_from_raw(tables: Any) -> tuple[PerformanceTable, ...]:
    """`revenuemix.build()`가 돌려준 표 목록을 «전부» 구성표로 바꾼다.

    ★ 왜 필요한가 (실측 결함 ①) — v1은 이 표를 만들어 2장에 붙이는데
      (`pipeline/real.py`의 tables_by_section["business_model"]),
      v2 호출부가 넘기지 않아 «표도 도식도» 통째로 빠져 있었다.
      9개 장 중 4장 하나만 표를 받는 상태였다.
    ★ 왜 여러 개인가 (실측 결함 ②, 설계 변경) — 예전에는 «첫 표만»
      썼다. revenuemix는 제품별·지역별 두 표를 낼 수 있는데, 첫 표만 쓰면
      지역별 표가 통째로 사라진다. v1은 이미 둘 다 2장에 붙이고
      (`ReportTable(**table) for table in revenue_tables`), 정본
      §4 소유권 표(2장 = 「수익 구조·고객 유형·고객·지역·채널 우선순위」)에도
      지역 우선순위가 명시돼 있다 — 첫 표만 쓰는 건 v2만의 축소였다. 다른
      장으로 옮기지 않고 «2장에 표 여러 개»를 그대로 허용한다(제품 결정).
      «같은 매출을 두 번 보여 준다»는 예전 우려는 성립하지 않는다 —
      제품별·지역별은 같은 매출을 «다른 축»으로 나눈 것이라 정본 규칙의
      「같은 수치의 반복」이 아니다.
    ★ 표마다 «구성 도식 모양»으로 따로 줄인다(_composition_shape) — 표 하나가
      도식 하한(3행)에 못 미쳐도 다른 표에는 영향을 주지 않는다.
    """
    if not tables:
        return ()
    items = tables if isinstance(tables, (list, tuple)) else (tables,)
    out: list[PerformanceTable] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        headers = tuple(str(head) for head in (item.get("headers") or ()))
        rows = tuple(
            tuple(str(cell) for cell in row) for row in (item.get("rows") or ())
        )
        if not rows:
            continue
        raw_rows = tuple(
            tuple(str(cell) for cell in row)
            for row in (item.get("raw_rows") or ())
        )
        evidence_rows = tuple(str(value) for value in (item.get("evidence_rows") or ()))
        row_fact_ids = tuple(str(value) for value in (item.get("row_fact_ids") or ()))
        headers, rows, kept_indices, column_indices = _composition_projection(
            headers, rows
        )
        if not rows:
            continue
        caption = str(item.get("caption") or "")
        if len(headers) == 2:
            selected_year = re.search(r"(?<!\d)(20\d{2})(?!\d)", headers[1])
            if selected_year is not None:
                caption = f"{caption} ({selected_year.group(1)}년 비중)"
        out.append(
            PerformanceTable(
                caption=caption,
                headers=headers,
                rows=rows,
                unit=str(item.get("display_unit") or ""),
                cite=str(item.get("cite") or ""),
                raw_rows=_project_rows(
                    raw_rows,
                    row_indices=kept_indices,
                    column_indices=column_indices,
                ),
                scale_divisor=str(item.get("scale_divisor") or ""),
                scale_places=int(item.get("scale_places") or 0),
                evidence_rows=_project_parallel_values(
                    evidence_rows,
                    row_indices=kept_indices,
                ),
                entity_scope=str(item.get("entity_scope") or ""),
                raw_unit=str(item.get("raw_unit") or ""),
                unit_dimension=str(item.get("unit_dimension") or ""),
                row_fact_ids=_project_parallel_values(
                    row_fact_ids,
                    row_indices=kept_indices,
                ),
            )
        )
    return tuple(out)
