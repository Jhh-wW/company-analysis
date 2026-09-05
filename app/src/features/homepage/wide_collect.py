"""여러 공식 호스트에 흩어진 회사 공식 웹 문서를 결속 근거와 함께 모은다.

★ DART가 준 홈페이지 주소(root)에서 시작해 같은 등록 도메인의 하위 도메인만
  넓힌다. 공식 페이지가 일반 외부 사이트를 링크했다는 이유만으로 그 사이트를
  회사 공식 자료로 승격하지 않는다. 공식 HTML이 정확히 가리킨 외부 IR PDF는
  일반 웹 탐색과 분리된 낮은 신뢰 첨부 경로에서만 다룬다.
★ 본문 조회 전 항상 그 호스트의 robots.txt를 먼저 확인한다(fail-closed —
  robots 자체를 확인 못하면 그 호스트는 긁지 않는다, ``wide_fetch.py``).
★ 이 모듈은 «공식 확정»을 선언하지 않는다. root/하위도메인은 REQUIRED,
  exact 외부 IR 첨부는 OPTIONAL·낮은 출처 등급으로만 표시하며 필수 슬롯
  조각을 만들지 않는다.
★ 공식 IR PDF는 기존에 이미 검증된 ``ir_pdf.collect_official_ir_fragments``에
  호스트별로 위임한다 — 이 모듈이 PDF 파싱·격리 워커를 다시 구현하지 않는다
  (재사용 결정, 최종 보고서 참조).
"""

from __future__ import annotations

import hashlib
import time
import urllib.parse
from dataclasses import dataclass, field, replace
from typing import Callable

from src.features.homepage.constants import (
    PRIORITY_PATH_KEYWORDS,
    WIDE_COLLECTION_TIMEOUT_SEC,
    WIDE_COLLECTOR_VERSION,
    WIDE_MAX_HOSTS,
    WIDE_MAX_IDENTITY_CANDIDATES_PER_HOST,
    WIDE_MAX_IR_DOCUMENTS,
    WIDE_MAX_PAGES,
    WIDE_MAX_ROOT_IDENTITY_SUPPLEMENT_BYTES,
    WIDE_MAX_ROOT_IDENTITY_SUPPLEMENT_PAGES,
    WIDE_MAX_SITEMAP_BYTES,
    WIDE_MAX_SITEMAP_ENTRIES,
    WIDE_MAX_TOTAL_BYTES,
    WIDE_MAX_USABLE_RANGES_PER_DOCUMENT,
    WIDE_PARSER_VERSION,
    WIDE_PRIORITY_HOST_KEYWORDS,
    WIDE_REQUIRED_SLOT_IDS,
    WIDE_ROOT_IDENTITY_SUPPLEMENT_PATH_MARKERS,
    WIDE_SOURCE_KIND_IDENTITY_VERIFIED_WEB_PAGE,
    WIDE_SOURCE_KIND_IR_PDF,
    WIDE_SOURCE_KIND_RECRUIT_PAGE,
    WIDE_SOURCE_KIND_WEB_PAGE,
)
from src.features.homepage.ir_pdf import (
    IrHtmlFetcher,
    IrPdfFetcher,
    OfficialIrFetchError,
    collect_official_ir_fragments,
    default_ir_html_fetch,
    default_ir_pdf_fetch,
)
from src.features.homepage.official_identity import (
    OfficialCompanyIdentity,
    OfficialIdentityMatch,
    verify_dart_root_company_identity_pages,
    verify_official_company_identity,
    verify_official_company_identity_pages,
)
from src.shared.official_ir import (
    IR_ATTACHMENT_URL_FIELD,
    IR_DART_WWW_REDIRECT_FIELD,
    IR_DART_WWW_REDIRECT_FROM_FIELD,
    IR_DART_WWW_REDIRECT_TO_FIELD,
    IR_METADATA_VERIFICATION_FIELD,
    IR_REPORTING_PERIOD_FIELD,
    dart_homepage_exact_host,
    dart_www_redirect_is_valid,
    safe_https_attachment_url,
)
from src.shared.report_evidence.constants import SOURCE_KIND_ROBOTS_TXT
from src.shared.report_evidence.identity_verified_web import (
    build_verified_dart_filing_official_web_binding,
    build_verified_dart_filing_subdomain_binding,
    identity_binding_with_scope,
    parse_dart_filing_url_provenance,
    parse_verified_dart_filing_official_web_binding,
    provenance_digest,
)
from src.shared.report_evidence.profile_domain_attestation import (
    build_registered_subdomain_profile_attestation,
    parse_dart_profile_domain_attestation,
)
from src.shared.report_evidence.source_kind_policy import (
    formal_document_writer_ineligibility_reason,
)
from src.features.homepage.safe_http import HomepageResponseError, request_deadline_scope
from src.features.homepage.wide_domain import (
    BoundHost,
    OfficialOrigin,
    bind_registered_subdomain,
    bind_root_host,
    bind_www_apex_alternate,
    canonicalize_url,
    classify_official_page_url,
    is_excluded_linked_host,
    parse_official_origin,
)
from src.features.homepage.wide_extract import (
    extract_inline_spa_ranges,
    extract_json_ld_ranges,
    extract_links,
    extract_usable_ranges,
    parse_sitemap_urls,
)
from src.features.homepage.wide_fetch import (
    RawWideTransport,
    WideRawResponse,
    WideRobotsPolicy,
    WideTransportError,
    classify_general_outcome,
    default_wide_transport,
    fetch_sitemap,
    load_robots_policy,
)
from src.features.homepage.wide_types import (
    ATTEMPT_STATE_FAILED,
    ATTEMPT_STATE_MISSING,
    ATTEMPT_STATE_OK,
    ATTEMPT_STATE_TRUNCATED,
    REQUIREMENT_OPTIONAL,
    REQUIREMENT_REQUIRED,
    SOURCE_TIER_1_OFFICIAL,
    SOURCE_TIER_3_TRUSTED,
    WideCollectionAttempt,
    WideCollectionResult,
    WideDocumentIdentity,
)

_PRIORITY_KEYWORDS: tuple[str, ...] = WIDE_PRIORITY_HOST_KEYWORDS + PRIORITY_PATH_KEYWORDS

_DART_HOMEPAGE_DISCOVERY = "DART company.json hm_url"
_DART_IR_DISCOVERY = "DART company.json ir_url"
#: 법인명과 등록번호를 실제 본문에서 함께 확인한 기본 결속 라벨.
_DUAL_VERIFIED_IDENTITY_LABEL = "DART 법인명+등록번호 이중 검증 공식 웹"
#: DART가 기업개황에 직접 등록한 홈페이지 host라서 법인명만으로 결속한 라벨.
#: 등록번호를 홈페이지에 게시하지 않는 회사를 위한 좁은 예외이며, 라벨을
#: 따로 두어 운영 진단에서 두 결속을 구분한다.
_ROOT_NAME_ONLY_IDENTITY_LABEL = "DART hm_url host 법인명 검증 공식 웹(등록번호 미게시)"
#: : robots·sitemap·전체 truncation·IR처럼 «호스트/수집 전체」에 걸린
#: attempt이거나, 일반 페이지인데 URL로 페이지 유형을 못 알아낸 attempt에
#: 붙이는 fallback slot 집합. 앱 계약(CollectionAttempt)은 빈 slot_ids를
#: 생성 즉시 거절하므로(``WideCollectionAttempt.__post_init__``도 동일하게
#: 막는다) 특정 slot을 좁혀낼 수 없을 때도 항상 비어 있지 않은 집합을
#: 명시해야 한다 — 「이 결과 때문에 확인하지 못한(혹은 확인한) 모든 후보
#: slot」이라는 뜻으로 허용 어휘 17개 전체를 쓴다(``_CollectionState.add_attempt``
#: 참조 — 모든 attempt 생성이 이 한 곳을 거치므로 호출부마다 따로 챙기지 않아도
#: 절대 빈 slot_ids가 새 나가지 않는다).
#:
#: ★ 정정 1의 최종판(P0 — 결합 종단시험에서 실측):
#: 처음엔 「FAILED·TRUNCATED면 광역 REQUIRED가 정확하다」로 정정했으나,
#: 그건 **그 경로가 그 slot의 유일한 확인 경로일 때만** 참이다. 웹
#: 수집기는 17개 slot 전부의 유일한 경로가 아니다 — 공시 문서 수집이
#: 같은 17개 slot을 전부 훑고, 페이지 유형이 좁혀낸 REQUIRED 경로(예:
#: 채용 페이지 → culture 2슬롯)도 따로 있다. 그래서 IR PDF 조회 하나가
#: FAILED로 실패했을 뿐인데 REQUIRED+광역으로 나가면, 계약이 「그 17개
#: slot을 확인할 유일한 REQUIRED 경로가 실패했다」로 잘못 읽어 다른
#: 소스(공시·페이지 유형 좁힌 경로)가 채운 근거까지 UNKNOWN으로
#: 끌어내렸다(실측: IR FAILED attempt 하나 때문에 9개 장 중 8개가
#: UNKNOWN, 최종 게이트 STOP_TRANSIENT_FAILURE).
#:
#: **정확한 최종 규칙**: 광역(허용 어휘 17개 전체) slot 주장은 **상태와
#: 무관하게 절대 REQUIRED가 될 수 없다.** robots·sitemap·IR·유형 미상
#: 페이지처럼 특정 slot을 스스로 좁히지 못해 이 fallback을 쓰는 attempt는
#: OK·MISSING·FAILED·TRUNCATED 전부 OPTIONAL이다(``_BROAD_SLOT_REQUIREMENT``).
#: 실패 사실은 reason_code로 그대로 남으므로 정보를 잃지 않는다. 반대로
#: 페이지 유형에서 좁혀 낸(광역이 아닌) slot 집합은 그 페이지가 그
#: slot들의 실제 근거 경로이므로 지금처럼 REQUIRED를 유지해도 된다.
_ALL_SLOT_IDS_FALLBACK: tuple[str, ...] = WIDE_REQUIRED_SLOT_IDS

#: 광역 slot 주장(위 fallback을 쓰는 attempt) 전용 requirement — 항상
#: OPTIONAL이다. 상태(OK/MISSING/FAILED/TRUNCATED)나 호스트 신뢰도와
#: 무관하다(위 docstring 참조). 이름을 상수로 남겨 「이 값은 절대
#: REQUIRED가 될 수 없다」는 불변식을 코드에서도 명시적으로 드러낸다.
_BROAD_SLOT_REQUIREMENT: str = REQUIREMENT_OPTIONAL


@dataclass
class _CollectionState:
    """수집 한 번의 누적 상태 — 문서·시도 기록·중복 판정 자료를 들고 다닌다."""

    company_id: str
    collected_at: str
    clock: Callable[[], float]
    domain_attestation_source_id: str = ""
    domain_attestation_evidence: str = ""
    documents: list[WideDocumentIdentity] = field(default_factory=list)
    attempts: list[WideCollectionAttempt] = field(default_factory=list)
    bound_hosts: dict[str, BoundHost] = field(default_factory=dict)
    bound_origins: dict[str, OfficialOrigin] = field(default_factory=dict)
    robots_policies: dict[str, WideRobotsPolicy] = field(default_factory=dict)
    content_hashes: set[str] = field(default_factory=set)
    pages_fetched: int = 0
    total_bytes: int = 0
    attempt_counter: int = 0
    # DART root(또는 그 고신뢰 하위호스트)의 실제 HTML이 직접 가리킨
    # 다른 등록 도메인 exact URL. 링크 사실만으로는 절대 문서가 되지 않고,
    # 수집 후 official_identity의 법인명+등록번호 이중 검증을 다시 거친다.
    cross_domain_candidates: dict[str, str] = field(default_factory=dict)
    # 외부 exact 링크의 계보를 주장할 수 있는 실제 고신뢰 페이지 URL.
    # DART root 신원 검증을 통과한 origin에서 이번 실행 중 성공적으로 읽은
    # 페이지만 들어간다. 호출자가 넘긴 URL 문자열만으로는 채우지 않는다.
    official_link_source_urls: set[str] = field(default_factory=set)

    def next_attempt_id(self, kind: str) -> str:
        self.attempt_counter += 1
        return f"{kind}-{self.attempt_counter:04d}"

    def add_attempt(
        self,
        *,
        kind: str,
        source_kind: str,
        requirement: str,
        state: str,
        slot_ids: tuple[str, ...],
        reason_code: str,
        elapsed_ms: int,
        bytes_downloaded: int,
        documents_seen: int,
    ) -> None:
        self.attempts.append(
            WideCollectionAttempt(
                # 계약 generation=8: 이 상태 객체가 이 수집 실행 시작부터
                # 들고 있는 실제 대상 회사 값을 그 자리에서 직접 싣는다 —
                # document의 company_id로 나중에 채워 넣지 않는다.
                company_id=self.company_id,
                attempt_id=self.next_attempt_id(kind),
                source_kind=source_kind,
                requirement=requirement,
                state=state,
                # : 빈 slot_ids는 절대 내보내지 않는다 — 좁혀낼 slot이 없으면
                # 허용 어휘 17개 전체로 대체한다(_ALL_SLOT_IDS_FALLBACK).
                slot_ids=slot_ids or _ALL_SLOT_IDS_FALLBACK,
                reason_code=reason_code,
                elapsed_ms=max(0, elapsed_ms),
                bytes_downloaded=max(0, bytes_downloaded),
                documents_seen=max(0, documents_seen),
            )
        )

    def record_truncation(self, source_kind: str, reason_code: str) -> None:
        self.add_attempt(
            kind="truncation",
            source_kind=source_kind,
            requirement=REQUIREMENT_OPTIONAL,
            state=ATTEMPT_STATE_TRUNCATED,
            slot_ids=(),
            reason_code=reason_code,
            elapsed_ms=0,
            bytes_downloaded=0,
            documents_seen=0,
        )

    def add_cross_domain_candidate(self, *, url: str, source_page_url: str) -> None:
        """host와 exact URL 상한 안에서 신원검증 전 후보를 보관한다.

        같은 host의 첫 랜딩 페이지에 등록번호가 없더라도 공시가 직접 적은
        회사소개 URL에는 있을 수 있다. 그래서 host 전체를 첫 실패 하나로
        닫지는 않되, host별 후보 수도 제한한다.
        """

        normalized_url = _identity_candidate_https_url(url)
        if not normalized_url:
            return
        host = (
            urllib.parse.urlsplit(normalized_url).hostname or ""
        ).casefold().rstrip(".")
        if not host or is_excluded_linked_host(host):
            return
        existing_hosts = {
            (urllib.parse.urlsplit(candidate).hostname or "").casefold().rstrip(".")
            for candidate in self.cross_domain_candidates
        }
        if normalized_url in self.cross_domain_candidates:
            # 같은 URL을 profile 후보가 먼저 넣고 DART 원문 provenance가 뒤에
            # 도착할 수 있다. 기존 출처가 비어 있을 때만 더 강한 발견 영수증을
            # 보존하며, 서로 다른 출처끼리 임의로 덮어쓰지는 않는다.
            if (
                not self.cross_domain_candidates[normalized_url]
                and str(source_page_url or "").strip()
            ):
                self.cross_domain_candidates[normalized_url] = str(
                    source_page_url
                ).strip()
            return
        if host not in existing_hosts and len(existing_hosts) >= WIDE_MAX_HOSTS:
            return
        same_host_count = sum(
            1
            for candidate in self.cross_domain_candidates
            if (urllib.parse.urlsplit(candidate).hostname or "").casefold().rstrip(".")
            == host
        )
        if same_host_count >= WIDE_MAX_IDENTITY_CANDIDATES_PER_HOST:
            return
        self.cross_domain_candidates[normalized_url] = source_page_url


@dataclass(frozen=True)
class _QueueItem:
    """탐색 큐 항목 — 새 호스트를 만나면 결속 근거로 쓸 출처 페이지를 함께 든다."""

    url: str
    source_page_url: str


def _identity_candidate_https_url(raw: str) -> str:
    """공식 후보의 host·path·query를 보존해 HTTPS exact URL로 만든다.

    DART에는 오래전에 등록한 ``http://`` URL이 남아 있을 수 있다. 그 문자열을
    그대로 버리면 강한 회사 신원값이 있어도 실제 공식 페이지를 한 번도 확인할
    수 없다. HTTP 응답을 읽는 대신 같은 host·path·query의 HTTPS(기본 443)만
    시도한다. 사용자정보·비표준 포트·다른 프로토콜은 추측하지 않고 거절하며,
    이후 redirect도 ``OfficialOrigin.allows_content_url``의 같은 origin 경계를
    그대로 통과해야 한다.
    """

    candidate = str(raw or "").strip()
    if not candidate:
        return ""
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    elif "://" not in candidate:
        candidate = f"https://{candidate}"
    try:
        parsed = urllib.parse.urlsplit(candidate)
        scheme = parsed.scheme.casefold()
        port = parsed.port
    except (TypeError, ValueError, UnicodeError):
        return ""
    if (
        scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    if scheme == "http":
        if port not in (None, 80):
            return ""
        display_host = (
            f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname
        )
        candidate = urllib.parse.urlunsplit(
            ("https", display_host, parsed.path, parsed.query, "")
        )
    origin = parse_official_origin(candidate)
    if origin is None or origin.scheme != "https" or origin.port != 443:
        return ""
    return origin.root_url


def _scoped_canonical_url(url: str, origin: OfficialOrigin) -> str:
    """이미 scope 검사를 통과한 URL에서 시작 query key를 잃지 않는다."""

    return canonicalize_url(url, preserve_query_keys=origin.scope_query_keys)


def _has_official_discovery_lineage(
    state: _CollectionState,
    *,
    candidate_url: str,
    source_page_url: str,
    promote_verified_root: bool,
) -> bool:
    """신원 복사만으로 외부 페이지가 공식 경로가 되지 않게 계보를 확인한다."""

    if promote_verified_root:
        return True
    source = str(source_page_url or "").strip()
    if source == _DART_IR_DISCOVERY:
        return True
    provenance = parse_dart_filing_url_provenance(source)
    if provenance is not None:
        return bool(
            provenance.company_id == state.company_id
            and _identity_candidate_https_url(provenance.url) == candidate_url
        )
    try:
        canonical_source = canonicalize_url(source)
    except (TypeError, ValueError):
        return False
    return bool(
        canonical_source and canonical_source in state.official_link_source_urls
    )


def _wide_document_id(canonical_url: str, origin: OfficialOrigin) -> str:
    """scope와 산출 모양 버전을 함께 봉인해 따뜻한 캐시 충돌을 막는다."""

    material = "\0".join(
        (
            canonical_url,
            origin.scope_digest,
            WIDE_COLLECTOR_VERSION,
            WIDE_PARSER_VERSION,
        )
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _profile_attestation_for_url(
    state: _CollectionState,
    source_url: str,
) -> tuple[str, str, str, str, str]:
    """현재 URL에 정확히 결속되는 DART 기업개황 provenance만 돌려준다."""

    source_id = state.domain_attestation_source_id.strip()
    evidence = state.domain_attestation_evidence.strip()
    if not source_id or not evidence:
        return "", "", "", "", ""
    profile = parse_dart_profile_domain_attestation(evidence)
    if (
        profile is None
        or profile.is_registered_subdomain
        or profile.corp_code != state.company_id
        or source_id != f"dart-company-profile-{state.company_id}"
    ):
        return "", "", "", "", ""
    dart_host = profile.root_host
    try:
        source_host = (
            urllib.parse.urlsplit(str(source_url or "")).hostname or ""
        ).casefold().rstrip(".")
    except ValueError:
        return "", "", "", "", ""
    if not dart_host or not source_host:
        return "", "", "", "", ""
    if source_host == dart_host:
        return source_id, evidence, "", "", ""
    subdomain_evidence = build_registered_subdomain_profile_attestation(
        evidence,
        source_url=source_url,
    )
    if subdomain_evidence:
        return source_id, subdomain_evidence, "", "", ""
    verification = "https_apex_to_www_redirect"
    if not dart_www_redirect_is_valid(
        verification=verification,
        from_host=dart_host,
        to_host=source_host,
        dart_host=dart_host,
        source_host=source_host,
    ):
        return "", "", "", "", ""
    return source_id, evidence, verification, dart_host, source_host


def collect_official_web_documents(
    *,
    company_id: str,
    company_name: str,
    root_homepage_url: str,
    collected_at: str,
    company_aliases: tuple[str, ...] = (),
    company_registration_numbers: tuple[str, ...] = (),
    official_candidate_urls: tuple[str, ...] = (),
    official_candidate_provenance: tuple[tuple[str, str], ...] = (),
    domain_attestation_source_id: str = "",
    domain_attestation_evidence: str = "",
    root_identity_verification_required: bool = True,
    transport: RawWideTransport = default_wide_transport,
    ir_html_fetch: IrHtmlFetcher = default_ir_html_fetch,
    ir_pdf_fetch: IrPdfFetcher = default_ir_pdf_fetch,
    clock: Callable[[], float] = time.monotonic,
) -> WideCollectionResult:
    """DART 홈페이지 주소 하나에서 넓은 공식 웹 문서 수집을 실행한다.

    Args:
        company_id: 문서 identity에 봉인할 회사 식별자.
        company_name: 공식 IR PDF 신원 대조에 쓰는 DART 법인명.
        root_homepage_url: DART 기업개황의 홈페이지 주소(``hm_url``).
        collected_at: 이 수집 실행의 ISO 시각. 호출자가 명시적으로 넘긴다
            (이 모듈은 벽시계를 직접 읽지 않는다 — 결정론적 시험을 위해).
        company_aliases: IR PDF·교차 도메인 신원 대조에 함께 쓰는 DART 공식 별칭.
        company_registration_numbers: DART 기업개황의 사업자등록번호·법인등록번호.
            다른 등록 도메인은 이 안정 식별번호와 법인명이 실제 HTML에 함께
            확인될 때만 수집한다. 정식 운영 경로에서 번호가 없으면 공식 웹
            신원을 확인할 수 없으므로 fail-closed한다.
        official_candidate_urls: 상위의 출처 있는 무료 발견 경로가 확인한 exact
            공식 URL 후보(DART ``ir_url`` 등). 사용자 자유입력이나 검색 snippet을
            넣는 자리가 아니다. DART ``hm_url``이 비어 있어도 후보 본문에서 위
            이중 신원 검증을 통과하면 수집할 수 있다.
        official_candidate_provenance: ``(후보 URL, 발견 provenance)`` 묶음.
            DART 공시 전문에서 찾은 URL처럼 receipt/location/hash를 보존해야
            하는 후보용이다. provenance는 최종 identity_binding에 함께 봉인된다.
        root_identity_verification_required: DART ``hm_url``도 재할당된 낡은
            도메인일 수 있으므로, 정식 운영 경로에서는 법인명+등록번호를 실제
            HTML에서 확인한 뒤에만 REQUIRED 공식 root로 승격한다. 등록번호가
            없으면 웹은 fail-closed하고 DART 공시만 남긴다. ``False``는 기존
            단위 호환 시험용이며 production adapter는 항상 ``True``를 쓴다.
        transport: 실제 네트워크 접속 함수. 시험에서는 가짜로 바꿔 끼운다.
        ir_html_fetch, ir_pdf_fetch: 공식 IR PDF 수집기(``ir_pdf.py``)에
            그대로 위임하는 접속 함수. 시험에서는 가짜로 바꿔 끼운다.
        clock: 시도 소요시간 측정에 쓰는 단조 시계. 시험에서 결정론적으로 고정 가능.

    Returns:
        문서·시도 기록을 담은 ``WideCollectionResult``. 상한 도달로 못 읽은
        부분은 문서를 지어내는 대신 ``TRUNCATED`` attempt로 남는다.
    """
    root_origin = parse_official_origin(root_homepage_url)
    state = _CollectionState(
        company_id=company_id,
        collected_at=collected_at,
        clock=clock,
        domain_attestation_source_id=str(domain_attestation_source_id or "").strip(),
        domain_attestation_evidence=str(domain_attestation_evidence or "").strip(),
    )
    identity = (
        OfficialCompanyIdentity(
            legal_name=company_name,
            aliases=company_aliases,
            registration_numbers=company_registration_numbers,
        )
        if str(company_name or "").strip()
        else None
    )
    candidate_urls = tuple(
        dict.fromkeys(
            clean
            for value in official_candidate_urls
            if (clean := str(value or "").strip())
        )
    )
    verified_root_urls: list[str] = []
    if root_origin is not None and root_identity_verification_required:
        for candidate_origin in (
            root_origin,
            (
                root_origin.with_host(alternate.host)
                if (alternate := bind_www_apex_alternate(root_origin.host)) is not None
                else None
            ),
        ):
            if candidate_origin is None:
                continue
            normalized = _identity_candidate_https_url(candidate_origin.root_url)
            if normalized and normalized not in verified_root_urls:
                verified_root_urls.append(normalized)

    # DART hm_url은 가장 먼저 자리를 잡는다. ir_url 등 출처가 이미 있는 exact
    # 후보를 그다음, 일반 HTML의 외부 링크보다 먼저 넣는다. 광고·협력사 링크가
    # 상한을 먼저 차지해 공식 후보가 아예 검증되지 못하는 순서 의존을 막는다.
    if (
        verified_root_urls
        and identity is not None
        and identity.can_verify_cross_domain
    ):
        for root_candidate_url in verified_root_urls:
            state.add_cross_domain_candidate(
                url=root_candidate_url,
                source_page_url=_DART_HOMEPAGE_DISCOVERY,
            )
    if identity is not None and identity.can_verify_cross_domain:
        for candidate_url in candidate_urls:
            state.add_cross_domain_candidate(
                url=candidate_url,
                source_page_url=_DART_IR_DISCOVERY,
            )
        for candidate in official_candidate_provenance:
            if not isinstance(candidate, tuple) or len(candidate) != 2:
                continue
            candidate_url, provenance = candidate
            if not str(provenance or "").strip():
                continue
            state.add_cross_domain_candidate(
                url=str(candidate_url or ""),
                source_page_url=str(provenance).strip(),
            )
    if root_origin is None and not (
        identity is not None
        and identity.can_verify_cross_domain
        and (candidate_urls or official_candidate_provenance)
    ):
        # 계약 gen=8 마지막 고리: 문서·attempt가 0건이어도 결과 자신은 항상
        # 대상 회사를 싣는다(documents에서 역산하지 않는다 — 역산하면 0건일 때
        # 정본을 잃는다).
        return WideCollectionResult(company_id=state.company_id, documents=(), attempts=())

    if (
        root_origin is not None
        and root_identity_verification_required
        and not (
            verified_root_urls
            and identity is not None
            and identity.can_verify_cross_domain
        )
    ):
        # 등록번호가 없으면 낡은 hm_url을 다른 회사의 REQUIRED 자료로 믿는
        # 것보다 웹 근거를 쓰지 않는 편이 안전하다. 네트워크도 0회다.
        state.add_attempt(
            kind="root-identity",
            source_kind=WIDE_SOURCE_KIND_WEB_PAGE,
            requirement=REQUIREMENT_OPTIONAL,
            state=ATTEMPT_STATE_MISSING,
            slot_ids=(),
            reason_code="root_identity_unverifiable",
            elapsed_ms=0,
            bytes_downloaded=0,
            documents_seen=0,
        )

    try:
        with request_deadline_scope(WIDE_COLLECTION_TIMEOUT_SEC) as deadline:
            if root_origin is not None and not root_identity_verification_required:
                _run_web_crawl(
                    state,
                    root_origin=root_origin,
                    transport=transport,
                    deadline=deadline,
                )
            if identity is not None and identity.can_verify_cross_domain:
                _run_cross_domain_identity_phase(
                    state,
                    identity=identity,
                    explicit_candidate_urls=candidate_urls,
                    verified_root_urls=tuple(verified_root_urls),
                    transport=transport,
                    deadline=deadline,
                )
            if root_origin is not None and root_identity_verification_required:
                verified_ir_origin = next(
                    (
                        state.bound_origins.get(
                            (urllib.parse.urlsplit(candidate).hostname or "").casefold()
                        )
                        for candidate in verified_root_urls
                        if state.bound_origins.get(
                            (urllib.parse.urlsplit(candidate).hostname or "").casefold()
                        )
                        is not None
                    ),
                    None,
                )
            else:
                verified_ir_origin = root_origin
            if verified_ir_origin is not None:
                _run_ir_pdf_phase(
                    state,
                    root_origin=verified_ir_origin,
                    company_name=company_name,
                    company_aliases=company_aliases,
                    ir_html_fetch=ir_html_fetch,
                    ir_pdf_fetch=ir_pdf_fetch,
                    deadline=deadline,
                )
    except HomepageResponseError:
        state.record_truncation(WIDE_SOURCE_KIND_WEB_PAGE, "truncated_time_cap")

    return WideCollectionResult(
        company_id=state.company_id,
        documents=tuple(state.documents),
        attempts=tuple(state.attempts),
    )


# ══════════════════════════════════════════════════════════
# 일반 웹 페이지 탐색
# ══════════════════════════════════════════════════════════


def _seed_host_root(
    state: _CollectionState,
    *,
    origin: OfficialOrigin,
    binding: BoundHost,
    transport: RawWideTransport,
    queue: list[_QueueItem],
    seen_canonical: set[str],
    root_host: str,
) -> None:
    """호스트 하나를 결속하고, 그 호스트 전용 robots·sitemap을 확인한 뒤
    루트 URL을 초기 큐에 심는다.

    robots가 이 호스트를 막으면 이 호스트의 루트 URL은 큐에 들어가지
    않지만(``policy.blocked`` — attempt 자체는 ``_ensure_host_policy``가
    이미 남겼다), 그렇다고 전체 크롤을 중단하지 않는다 — 호출자가 다른
    호스트(예: apex/www 짝)를 이어서 독립적으로 시도할 수 있다.
    """
    if origin.host not in state.bound_hosts and len(state.bound_hosts) >= WIDE_MAX_HOSTS:
        return
    state.bound_hosts[origin.host] = binding
    state.bound_origins[origin.host] = origin
    policy = _ensure_host_policy(state, origin=origin, transport=transport)
    if policy.blocked:
        return

    _discover_sitemap(
        state,
        origin=origin,
        transport=transport,
        policy=policy,
        queue=queue,
        seen_canonical=seen_canonical,
        root_host=root_host,
    )

    root_url = origin.root_url
    canonical = _scoped_canonical_url(root_url, origin)
    if canonical in seen_canonical:
        return
    seen_canonical.add(canonical)
    queue.append(_QueueItem(url=root_url, source_page_url=root_url))


def _run_web_crawl(
    state: _CollectionState,
    *,
    root_origin: OfficialOrigin,
    transport: RawWideTransport,
    deadline: object,
) -> None:
    queue: list[_QueueItem] = []
    seen_canonical: set[str] = set()

    # apex·www 짝 결속: DART가 준 호스트
    # («primary»)와 그 apex/www 짝을 각각 독립된 후보로 미리 심는다 — redirect를
    # 따라가는 방식이 아니라 «둘 다 직접 방문」하는 방식이라, 정확히 같은
    # host만 허용하는 redirect fail-closed 정책(앞서 고친 eTLD+1 결함 수정과
    # 같은 맥락, 여기서 완화하지 않는다)과 부딪히지 않는다. primary가 apex→www
    # (또는 반대) redirect 하나 때문에 robots·본문을 전혀 못 읽어도, 짝 호스트가
    # 각자 robots부터 따로 확인하며 독립적으로 시도되므로 수집이 0건이 되지
    # 않는다.
    _seed_host_root(
        state,
        origin=root_origin,
        binding=bind_root_host(root_origin.host),
        transport=transport,
        queue=queue,
        seen_canonical=seen_canonical,
        root_host=root_origin.host,
    )

    alternate_binding = bind_www_apex_alternate(root_origin.host)
    if alternate_binding is not None:
        _seed_host_root(
            state,
            origin=root_origin.with_host(alternate_binding.host),
            binding=alternate_binding,
            transport=transport,
            queue=queue,
            seen_canonical=seen_canonical,
            root_host=root_origin.host,
        )

    while queue:
        try:
            deadline.remaining()  # type: ignore[attr-defined]
        except HomepageResponseError:
            state.record_truncation(WIDE_SOURCE_KIND_WEB_PAGE, "truncated_time_cap")
            return
        if state.pages_fetched >= WIDE_MAX_PAGES:
            state.record_truncation(WIDE_SOURCE_KIND_WEB_PAGE, "truncated_page_cap")
            return
        if state.total_bytes >= WIDE_MAX_TOTAL_BYTES:
            state.record_truncation(WIDE_SOURCE_KIND_WEB_PAGE, "truncated_byte_cap")
            return

        queue.sort(key=lambda queued: _priority_key(queued.url))
        item = queue.pop(0)
        _visit_page(
            state,
            item=item,
            root_origin=root_origin,
            root_host=root_origin.host,
            transport=transport,
            queue=queue,
            seen_canonical=seen_canonical,
        )


# ══════════════════════════════════════════════════════════
# 다른 등록 도메인 — DART 법인명+등록번호 이중 검증
# ══════════════════════════════════════════════════════════


def _run_cross_domain_identity_phase(
    state: _CollectionState,
    *,
    identity: OfficialCompanyIdentity,
    explicit_candidate_urls: tuple[str, ...],
    verified_root_urls: tuple[str, ...],
    transport: RawWideTransport,
    deadline: object,
) -> None:
    """출처 있는 exact 후보를 격리 조회하고 신원 일치 때만 수집한다.

    일반 외부 링크를 곧바로 공식 도메인군에 넣지 않는다. 먼저 exact URL
    한 건만 robots·SSRF 경계 안에서 읽고, 실제 HTML footer/JSON-LD에서
    DART 법인명과 등록번호를 함께 확인한다. 확인된 호스트도 보조
    ``OPTIONAL`` 경로다. 이 보조 경로의 일시 장애가 공시 등 다른 근거까지
    막지 않으며, 성공한 본문 조각은 부족한 장을 채울 수 있다.
    """

    # 명시 후보는 collect 진입 직후 먼저 state에 넣어 두었다. 이 인자를
    # 여기서도 받아 계약상 같은 후보 묶음을 처리 중임을 드러내고, 호출자가
    # 실수로 다른 state를 넘긴 경우에만 보완한다.
    for candidate_url in explicit_candidate_urls:
        if candidate_url not in state.cross_domain_candidates:
            state.add_cross_domain_candidate(url=candidate_url, source_page_url="")

    processed_urls: set[str] = set()
    while True:
        candidates = sorted(
            (
                item
                for item in state.cross_domain_candidates.items()
                if item[0] not in processed_urls
            ),
            key=lambda item: (
                (
                    verified_root_urls.index(item[0])
                    if item[0] in verified_root_urls
                    else len(verified_root_urls)
                ),
                _priority_key(item[0]),
            ),
        )
        if not candidates:
            break
        candidate_url, source_page_url = candidates[0]
        processed_urls.add(candidate_url)
        try:
            deadline.remaining()  # type: ignore[attr-defined]
        except HomepageResponseError:
            state.record_truncation(WIDE_SOURCE_KIND_WEB_PAGE, "truncated_time_cap")
            return
        if state.pages_fetched >= WIDE_MAX_PAGES:
            state.record_truncation(WIDE_SOURCE_KIND_WEB_PAGE, "truncated_page_cap")
            return
        if state.total_bytes >= WIDE_MAX_TOTAL_BYTES:
            state.record_truncation(WIDE_SOURCE_KIND_WEB_PAGE, "truncated_byte_cap")
            return
        _collect_identity_verified_candidate(
            state,
            candidate_url=candidate_url,
            source_page_url=source_page_url,
            identity=identity,
            promote_verified_root=candidate_url in verified_root_urls,
            transport=transport,
            deadline=deadline,
        )


def _root_identity_supplement_urls(
    response: WideRawResponse,
    *,
    origin: OfficialOrigin,
    policy: WideRobotsPolicy,
) -> tuple[str, ...]:
    """첫 화면 exact 링크 중 닫힌 신원 경로만 same-origin 후보로 고른다."""

    ranked: list[tuple[int, str]] = []
    seen: set[str] = set()
    for link in extract_links(response.text, response.effective_url):
        if not origin.allows_content_url(link) or not policy.can_fetch(link):
            continue
        try:
            parsed = urllib.parse.urlsplit(link)
            decoded_path = urllib.parse.unquote(parsed.path).casefold()
        except (TypeError, ValueError, UnicodeError):
            continue
        ranks = [
            index
            for index, marker in enumerate(
                WIDE_ROOT_IDENTITY_SUPPLEMENT_PATH_MARKERS
            )
            if marker.casefold() in decoded_path
        ]
        if not ranks:
            continue
        canonical = _scoped_canonical_url(link, origin)
        if not canonical or canonical in seen:
            continue
        seen.add(canonical)
        ranked.append((min(ranks), canonical))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return tuple(
        url
        for _rank, url in ranked[:WIDE_MAX_ROOT_IDENTITY_SUPPLEMENT_PAGES]
    )


def _fetch_root_identity_supplements(
    state: _CollectionState,
    *,
    root_response: WideRawResponse,
    origin: OfficialOrigin,
    policy: WideRobotsPolicy,
    transport: RawWideTransport,
    deadline: object,
) -> tuple[tuple[WideRawResponse, ...], int, bool]:
    """root 신원 분리 확인용 same-origin 페이지를 비재귀·bounded 조회한다."""

    responses: list[WideRawResponse] = []
    total_bytes = 0
    had_failure = False
    for candidate_url in _root_identity_supplement_urls(
        root_response,
        origin=origin,
        policy=policy,
    ):
        deadline.remaining()  # type: ignore[attr-defined]
        if (
            state.pages_fetched >= WIDE_MAX_PAGES
            or state.total_bytes >= WIDE_MAX_TOTAL_BYTES
            or total_bytes >= WIDE_MAX_ROOT_IDENTITY_SUPPLEMENT_BYTES
        ):
            state.record_truncation(
                WIDE_SOURCE_KIND_WEB_PAGE,
                "truncated_root_identity_supplement_cap",
            )
            break

        def url_allowed(candidate: str) -> bool:
            return origin.allows_content_url(candidate) and policy.can_fetch(candidate)

        response: WideRawResponse | None = None
        error: WideTransportError | None = None
        try:
            response = transport(candidate_url, url_allowed)
        except WideTransportError as exc:
            error = exc
        state.pages_fetched += 1
        page_state, _reason_code = classify_general_outcome(response, error)
        response_bytes = len(
            (response.text if response else "").encode("utf-8", errors="ignore")
        )
        state.total_bytes += response_bytes
        total_bytes += response_bytes
        if total_bytes > WIDE_MAX_ROOT_IDENTITY_SUPPLEMENT_BYTES:
            state.record_truncation(
                WIDE_SOURCE_KIND_WEB_PAGE,
                "truncated_root_identity_supplement_bytes",
            )
            break
        if page_state != ATTEMPT_STATE_OK or response is None:
            had_failure = True
            continue
        if (
            not origin.allows_content_url(response.effective_url)
            or not policy.can_fetch(response.effective_url)
        ):
            had_failure = True
            continue
        responses.append(response)
    return tuple(responses), total_bytes, had_failure


def _enqueue_verified_identity_link(
    state: _CollectionState,
    *,
    link: str,
    source_page_url: str,
    origin: OfficialOrigin,
    promote_verified_root: bool,
    queue: list[_QueueItem],
    seen_canonical: set[str],
) -> None:
    """신원이 확인된 페이지 링크를 같은-origin 큐/외부 격리 후보로 나눈다."""

    try:
        link_host = (
            urllib.parse.urlsplit(link).hostname or ""
        ).casefold().rstrip(".")
    except (TypeError, ValueError):
        return
    if link_host == origin.host:
        if not origin.allows_content_url(link):
            return
        canonical_link = _scoped_canonical_url(link, origin)
    elif bind_registered_subdomain(origin.host, link_host) is not None:
        canonical_link = canonicalize_url(link)
    else:
        if promote_verified_root:
            state.add_cross_domain_candidate(
                url=link,
                source_page_url=source_page_url,
            )
        return
    if canonical_link in seen_canonical:
        return
    seen_canonical.add(canonical_link)
    queue.append(_QueueItem(url=link, source_page_url=source_page_url))


def _collect_identity_verified_candidate(
    state: _CollectionState,
    *,
    candidate_url: str,
    source_page_url: str,
    identity: OfficialCompanyIdentity,
    promote_verified_root: bool,
    transport: RawWideTransport,
    deadline: object,
) -> None:
    """교차 도메인 exact 페이지 하나를 검증하고 같은 host 내부만 넓힌다."""

    origin = parse_official_origin(candidate_url)
    if origin is None or origin.scheme != "https":
        return
    host = origin.host
    if not _has_official_discovery_lineage(
        state,
        candidate_url=candidate_url,
        source_page_url=source_page_url,
        promote_verified_root=promote_verified_root,
    ):
        # 법인명·번호는 공개 정보라 디렉터리/광고 페이지가 그대로 복사할 수
        # 있다. DART profile·공시 또는 이번 실행의 검증된 root exact 링크
        # 계보가 없으면 네트워크도 호출하지 않고 관측 실패만 남긴다.
        page_classification = classify_official_page_url(candidate_url)
        state.add_attempt(
            kind="cross-domain-lineage",
            source_kind=WIDE_SOURCE_KIND_IDENTITY_VERIFIED_WEB_PAGE,
            requirement=REQUIREMENT_OPTIONAL,
            state=ATTEMPT_STATE_MISSING,
            slot_ids=page_classification.slot_ids,
            reason_code="cross_domain_official_lineage_missing",
            elapsed_ms=0,
            bytes_downloaded=0,
            documents_seen=0,
        )
        return
    if is_excluded_linked_host(host) or host in state.bound_hosts:
        return
    if len(state.bound_hosts) >= WIDE_MAX_HOSTS:
        return

    policy = _ensure_host_policy(state, origin=origin, transport=transport)
    if policy.blocked or not policy.can_fetch(origin.root_url):
        return

    started = state.clock()
    response: WideRawResponse | None = None
    error: WideTransportError | None = None
    try:
        response = transport(origin.root_url, origin.allows_content_url)
    except WideTransportError as exc:
        error = exc
    elapsed_ms = int((state.clock() - started) * 1000)
    state.pages_fetched += 1

    page_state, reason_code = classify_general_outcome(response, error)
    response_bytes = len(
        (response.text if response else "").encode("utf-8", errors="ignore")
    )
    state.total_bytes += response_bytes
    documents_seen = 0
    binding: BoundHost | None = None

    match: OfficialIdentityMatch | None = None
    # DART hm_url host의 root 묶음을 «법인명만» 확인해 결속했는지 여부.
    name_only_root = False
    identity_responses: tuple[WideRawResponse, ...] = ()
    if page_state == ATTEMPT_STATE_OK and response is not None:
        # 실제 전송 구현은 매 redirect마다 같은 origin/path/query를 검사한다.
        # 그래도 이 조립 경계가 transport의 선행 검사를 믿기만 하면, 시험 대역
        # 또는 향후 구현이 predicate를 빠뜨린 순간 범위 밖 본문에 복사된 회사명·
        # 등록번호가 원래 host를 공식으로 결속한다. 최종 URL을 신원 확인보다
        # 먼저 독립 재검사한다.
        if not origin.allows_content_url(response.effective_url):
            page_state = ATTEMPT_STATE_FAILED
            reason_code = "redirect_scope_mismatch"
        else:
            identity_responses = (response,)
            match = verify_official_company_identity(response.text, identity)
            if match is None and promote_verified_root:
                supplements, supplement_bytes, supplement_failed = (
                    _fetch_root_identity_supplements(
                        state,
                        root_response=response,
                        origin=origin,
                        policy=policy,
                        transport=transport,
                        deadline=deadline,
                    )
                )
                response_bytes += supplement_bytes
                identity_responses = (response, *supplements)
                if supplements:
                    match = verify_official_company_identity_pages(
                        tuple(item.text for item in identity_responses),
                        identity,
                    )
                if match is None and supplement_failed:
                    page_state = ATTEMPT_STATE_FAILED
                    reason_code = "root_identity_supplement_failed"
                elif match is None:
                    # 등록번호를 홈페이지 어디에도 적지 않는 회사가 있다
                    # ((주)인이지 2026-09-05 실측: root·회사소개·개인정보·
                    # 약관 4쪽 모두 0건). 이 host는 DART 기업개황이 직접
                    # 등록한 hm_url이므로, 사용자 결정에 따라 법인명 일치만
                    # 으로 결속한다. hm_url이 낡아 다른 사이트를 가리킬
                    # 위험은 남지만 법인명 토큰이 그대로 이어져야 한다는
                    # 조건이 있어 낮다. 보강 조회가 하나라도 실패했으면
                    # (위 갈래) 자료를 다 못 본 것이므로 열지 않는다.
                    match = verify_dart_root_company_identity_pages(
                        tuple(item.text for item in identity_responses),
                        identity,
                    )
                    name_only_root = match is not None
        if page_state == ATTEMPT_STATE_OK and match is None:
            page_state = ATTEMPT_STATE_MISSING
            reason_code = (
                "root_identity_mismatch"
                if promote_verified_root
                else "cross_domain_identity_mismatch"
            )

    if match is not None and response is not None:
        filing_provenance = parse_dart_filing_url_provenance(source_page_url)
        verified_filing_binding = build_verified_dart_filing_official_web_binding(
            provenance_value=source_page_url,
            company_id=state.company_id,
            company_name=identity.legal_name,
            company_registration_numbers=identity.registration_numbers,
            candidate_url=candidate_url,
            effective_urls=tuple(item.effective_url for item in identity_responses),
            scope_sha256=origin.scope_digest,
            scope_allows=origin.allows_content_url,
            identity_evidence_sha256=match.evidence_sha256,
            matched_name_sha256=match.matched_name_sha256,
            registration_number_sha256=match.registration_number_sha256,
        )
        source_binding = (
            _DART_HOMEPAGE_DISCOVERY
            if promote_verified_root
            else (
                "DART 공시 URL 후보"
                if filing_provenance is not None
                else (
                    "DART company.json ir_url"
                    if source_page_url == _DART_IR_DISCOVERY
                    else "검증된 DART root 문서의 exact 링크"
                )
            )
        )
        source_digest = provenance_digest(source_page_url or candidate_url)
        # 이름-단독 결속도 DART가 보증한 host라는 계보는 같으므로 root의
        # 기존 신뢰 등급을 그대로 쓴다. 대신 라벨과 사유 코드로 구분한다.
        is_high_confidence = bool(promote_verified_root or verified_filing_binding)
        identity_label = (
            _ROOT_NAME_ONLY_IDENTITY_LABEL
            if name_only_root
            else _DUAL_VERIFIED_IDENTITY_LABEL
        )
        binding = BoundHost(
            host=host,
            identity_binding=(
                verified_filing_binding
                or (
                    f"{identity_label}; "
                    f"discovery={source_binding}; "
                    f"discovery_sha256={source_digest}; "
                    f"identity_evidence_sha256={match.evidence_sha256}"
                )
            ),
            # DART hm_url 및 exact 공시문서+첨부 hash 계보와 실제 법인명+
            # 등록번호·landing scope를 모두 확인한 root만 REQUIRED가 된다.
            is_high_confidence=is_high_confidence,
        )
        state.bound_hosts[host] = binding
        state.bound_origins[host] = origin
        if binding.is_high_confidence:
            state.official_link_source_urls.update(
                _scoped_canonical_url(item.effective_url, origin)
                for item in identity_responses
            )
        for identity_response in identity_responses:
            response_classification = classify_official_page_url(
                identity_response.effective_url
            )
            document = _build_web_document(
                state,
                response=identity_response,
                origin=origin,
                source_kind=(
                    response_classification.source_kind
                    if promote_verified_root
                    else WIDE_SOURCE_KIND_IDENTITY_VERIFIED_WEB_PAGE
                ),
                requirement=(
                    REQUIREMENT_REQUIRED
                    if binding.is_high_confidence
                    else REQUIREMENT_OPTIONAL
                ),
                binding=binding,
            )
            if document is not None:
                state.documents.append(document)
                documents_seen += 1
        reason_code = (
            ("root_identity_name_only" if name_only_root else "root_identity_verified")
            if promote_verified_root
            else (
                "dart_filing_identity_verified"
                if verified_filing_binding
                else "cross_domain_identity_verified"
            )
        )

        # 신원이 확인된 exact origin 안에서만 일반 페이지를 더 읽는다. 다른
        # 등록 도메인과 하위호스트는 이 보조 후보에서 연쇄 승격하지 않는다.
        queue: list[_QueueItem] = []
        seen_canonical = {
            _scoped_canonical_url(item.effective_url, origin)
            for item in identity_responses
        }
        if binding.is_high_confidence:
            _discover_sitemap(
                state,
                origin=origin,
                transport=transport,
                policy=policy,
                queue=queue,
                seen_canonical=seen_canonical,
                root_host=origin.host,
            )

        for source_response in identity_responses:
            for link in extract_links(
                source_response.text,
                source_response.effective_url,
            ):
                _enqueue_verified_identity_link(
                    state,
                    link=link,
                    source_page_url=source_response.effective_url,
                    origin=origin,
                    promote_verified_root=binding.is_high_confidence,
                    queue=queue,
                    seen_canonical=seen_canonical,
                )

        while queue:
            try:
                deadline.remaining()  # type: ignore[attr-defined]
            except HomepageResponseError:
                state.record_truncation(
                    WIDE_SOURCE_KIND_WEB_PAGE, "truncated_time_cap"
                )
                break
            if state.pages_fetched >= WIDE_MAX_PAGES:
                state.record_truncation(
                    WIDE_SOURCE_KIND_WEB_PAGE, "truncated_page_cap"
                )
                break
            if state.total_bytes >= WIDE_MAX_TOTAL_BYTES:
                state.record_truncation(
                    WIDE_SOURCE_KIND_WEB_PAGE, "truncated_byte_cap"
                )
                break
            queue.sort(key=lambda queued: _priority_key(queued.url))
            item = queue.pop(0)
            _visit_page(
                state,
                item=item,
                root_origin=origin,
                root_host=origin.host,
                transport=transport,
                queue=queue,
                seen_canonical=seen_canonical,
            )

    # 성공한 문서·attempt·조각은 모두 실제 landing URL 하나로 분류한다.
    # 실패했을 때만 최종 URL을 믿을 수 없으므로 요청 URL로 진단한다.
    attempt_classification = classify_official_page_url(
        response.effective_url
        if page_state == ATTEMPT_STATE_OK and response is not None
        else candidate_url
    )
    source_kind = (
        attempt_classification.source_kind
        if promote_verified_root
        else WIDE_SOURCE_KIND_IDENTITY_VERIFIED_WEB_PAGE
    )
    slot_ids = attempt_classification.slot_ids
    attempt_requirement = (
        REQUIREMENT_REQUIRED
        if binding is not None and binding.is_high_confidence and slot_ids
        else REQUIREMENT_OPTIONAL
    )
    state.add_attempt(
        kind="cross-domain-page",
        source_kind=source_kind,
        requirement=attempt_requirement,
        state=page_state,
        slot_ids=slot_ids,
        reason_code=reason_code,
        elapsed_ms=elapsed_ms,
        bytes_downloaded=response_bytes,
        documents_seen=documents_seen,
    )


def _visit_page(
    state: _CollectionState,
    *,
    item: _QueueItem,
    root_origin: OfficialOrigin,
    root_host: str,
    transport: RawWideTransport,
    queue: list[_QueueItem],
    seen_canonical: set[str],
) -> None:
    # 회사 query scope는 host가 바뀌어도 최초 DART 시작 URL이 정본이다.
    # 이 검사는 candidate canonicalize·robots·본문 transport보다 먼저 한다.
    if not root_origin.allows_query_scope(item.url):
        return
    candidate_origin = parse_official_origin(item.url)
    if candidate_origin is None:
        return
    candidate_origin = candidate_origin.with_query_scope(root_origin.start_query)
    host = candidate_origin.host
    binding = state.bound_hosts.get(host)
    origin = state.bound_origins.get(host)
    if binding is None:
        # 모든 결속 경로에 같은 상한을 먼저 적용한다. 같은 등록 도메인의
        # 하위호스트도 robots/DNS/본문 비용을 소비하므로 예외가 아니다.
        if len(state.bound_hosts) >= WIDE_MAX_HOSTS:
            return
        binding = bind_registered_subdomain(root_host, host)
        if binding is None:
            # 공식 페이지가 링크했다는 사실은 그 외부 host 전체가 회사
            # 소유라는 증거가 아니다. vendor·언론·관계사 일반 페이지는
            # 여기서 0회 호출한다. 외부 IR PDF exact-link는 아래 전용
            # PDF 경로만 허용한다.
            return
        root_binding = state.bound_hosts.get(root_host)
        strict_root_proof = (
            parse_verified_dart_filing_official_web_binding(
                root_binding.identity_binding
            )
            if root_binding is not None
            else None
        )
        if strict_root_proof is not None:
            derived_binding = build_verified_dart_filing_subdomain_binding(
                root_identity_binding=root_binding.identity_binding,
                source_url=item.url,
                scope_sha256=candidate_origin.scope_digest,
            )
            if not derived_binding:
                # strict root의 접수·첨부·이중신원 proof를 자손 URL에 정확히
                # 재결속할 수 없으면 설명 문자열로 고신뢰를 가장하지 않는다.
                return
            binding = BoundHost(
                host=binding.host,
                identity_binding=derived_binding,
                is_high_confidence=True,
            )
        elif root_binding is not None and not root_binding.is_high_confidence:
            # 법인명+등록번호로 확인한 교차 도메인은 OPTIONAL 보조 경로다.
            # 그 하위호스트가 같은 eTLD+1이라는 이유만으로 다시 REQUIRED로
            # 강해지면 보조 채용/제품 host 장애가 전체 조사를 막게 된다.
            binding = BoundHost(
                host=binding.host,
                identity_binding=(
                    f"{root_binding.identity_binding}; "
                    f"같은 등록 도메인의 하위 도메인({host})"
                ),
                is_high_confidence=False,
            )
        state.bound_hosts[host] = binding
        state.bound_origins[host] = candidate_origin
        origin = candidate_origin
    elif origin is None or not origin.allows_content_url(item.url):
        # 이미 결속한 host라도 DART/최초 링크의 회사 소유 path-prefix 밖으로
        # 넓히지 않는다. 같은 hostname이라는 이유만으로 다른 입주자 경로를
        # 공식자료로 승격해서는 안 된다.
        return

    host_policy = state.robots_policies.get(host)
    if host_policy is None:
        assert origin is not None
        host_policy = _ensure_host_policy(state, origin=origin, transport=transport)
        if not host_policy.blocked:
            _discover_sitemap(
                state,
                origin=origin,
                transport=transport,
                policy=host_policy,
                queue=queue,
                seen_canonical=seen_canonical,
                root_host=root_host,
            )
    if host_policy.blocked or not host_policy.can_fetch(item.url):
        return  # robots 금지 — 절대 조회하지 않는다

    assert origin is not None

    def url_allowed(
        candidate: str,
        expected_origin: OfficialOrigin = origin,
        policy: WideRobotsPolicy = host_policy,
    ) -> bool:
        return expected_origin.allows_content_url(candidate) and policy.can_fetch(candidate)

    started = state.clock()
    response: WideRawResponse | None = None
    error: WideTransportError | None = None
    try:
        response = transport(item.url, url_allowed)
    except WideTransportError as exc:
        error = exc
    elapsed_ms = int((state.clock() - started) * 1000)
    state.pages_fetched += 1

    page_state, reason_code = classify_general_outcome(response, error)
    requirement = REQUIREMENT_REQUIRED if binding.is_high_confidence else REQUIREMENT_OPTIONAL
    # 성공 응답은 요청 주소가 아니라 redirect 뒤 실제 문서 주소 하나로
    # 종류·슬롯을 함께 판정한다. 오류에는 effective URL이 없으므로 그때만
    # 요청 주소를 진단용으로 쓴다.
    classification_url = (
        response.effective_url
        if page_state == ATTEMPT_STATE_OK and response is not None
        else item.url
    )
    page_classification = classify_official_page_url(classification_url)
    source_kind = (
        page_classification.source_kind
        if binding.is_high_confidence
        else WIDE_SOURCE_KIND_IDENTITY_VERIFIED_WEB_PAGE
    )
    slot_ids = page_classification.slot_ids
    response_bytes = len((response.text if response else "").encode("utf-8", errors="ignore"))
    state.total_bytes += response_bytes

    documents_seen = 0
    if page_state == ATTEMPT_STATE_OK and response is not None:
        if (
            binding.is_high_confidence
            and origin.allows_content_url(response.effective_url)
            and host_policy.can_fetch(response.effective_url)
        ):
            state.official_link_source_urls.add(
                _scoped_canonical_url(response.effective_url, origin)
            )
        document = _build_web_document(
            state,
            response=response,
            origin=origin,
            source_kind=source_kind,
            requirement=requirement,
            binding=binding,
        )
        if document is not None:
            documents_seen = 1
            state.documents.append(document)
            for link in extract_links(response.text, response.effective_url):
                if not root_origin.allows_query_scope(link):
                    continue
                link_host = (
                    urllib.parse.urlsplit(link).hostname or ""
                ).casefold()
                if link_host == origin.host:
                    # scope 판정은 tracking 제거나 query 정렬보다 먼저 한다.
                    # 거절 URL은 queue에도 넣지 않아 transport 0회를 보장한다.
                    if not origin.allows_content_url(link):
                        continue
                    canonical_link = _scoped_canonical_url(link, origin)
                else:
                    # 같은 등록 도메인이 아닌 exact 링크는 일반 queue에 넣어
                    # 나중에 조용히 버리지 않는다. 다만 현재 문서 자체가 DART
                    # root 계열 고신뢰 문서일 때만 «검증 전 후보»로 기록한다.
                    # 실제 호출·승격은 별도 단계에서 법인명+등록번호를 모두
                    # 확인한 뒤에만 일어난다. OPTIONAL로 검증돼 들어온 외부
                    # 도메인이 다시 다른 도메인을 연쇄 추천할 수는 없다.
                    if (
                        bind_registered_subdomain(root_host, link_host) is None
                        and binding.is_high_confidence
                    ):
                        state.add_cross_domain_candidate(
                            url=link,
                            source_page_url=response.effective_url,
                        )
                        continue
                    canonical_link = canonicalize_url(link)
                if canonical_link in seen_canonical:
                    continue
                seen_canonical.add(canonical_link)
                queue.append(_QueueItem(url=link, source_page_url=response.effective_url))
        else:
            reason_code = "duplicate_content_or_empty"

    # URL로 페이지 유형을 못 알아낸(slot_ids가 원래 비어 있던) 페이지는
    # 광역(17개 전체) slot fallback을 쓴다 — build_fragments도 같은 URL
    # 판정 함수(slot_ids_for_url)를 쓰므로 이 문서는 조각을 하나도 만들지
    # 않는다는 뜻이라, 이 attempt는 어떤 slot의 실제 근거 경로도 아니다.
    # 웹은 17개 slot 전부의 유일한 확인 경로가 아니므로(공시 문서 수집·
    # 페이지 유형이 좁힌 다른 경로가 따로 있다) 상태(OK/MISSING/FAILED)와
    # 무관하게 항상 OPTIONAL이다 — FAILED도 예외가 아니다(IR FAILED 사례로
    # 결합 종단시험에서 실측한 P0와 같은 원인).
    # document 자체의 requirement(등록 하위도메인 여부)는 건드리지 않는다
    # — attempt 전용 판단이라 별도 변수로 둔다.
    attempt_requirement = requirement
    if not slot_ids:
        attempt_requirement = _BROAD_SLOT_REQUIREMENT

    state.add_attempt(
        kind="page",
        source_kind=source_kind,
        requirement=attempt_requirement,
        state=page_state,
        slot_ids=slot_ids,
        reason_code=reason_code,
        elapsed_ms=elapsed_ms,
        bytes_downloaded=response_bytes,
        documents_seen=documents_seen,
    )


def _build_web_document(
    state: _CollectionState,
    *,
    response: WideRawResponse,
    origin: OfficialOrigin,
    source_kind: str,
    requirement: str,
    binding: BoundHost,
) -> WideDocumentIdentity | None:
    if not origin.allows_content_url(response.effective_url):
        return None
    body_ranges, title = extract_usable_ranges(response.text)
    ranges = body_ranges + extract_json_ld_ranges(response.text) + extract_inline_spa_ranges(response.text)
    ranges = tuple(dict.fromkeys(ranges))[:WIDE_MAX_USABLE_RANGES_PER_DOCUMENT]
    if not ranges:
        return None

    content_sha256 = hashlib.sha256("\n".join(ranges).encode("utf-8")).hexdigest()
    if content_sha256 in state.content_hashes:
        return None
    state.content_hashes.add(content_sha256)

    canonical = _scoped_canonical_url(response.effective_url, origin)
    host = (urllib.parse.urlsplit(canonical).hostname or "").casefold()
    document_id = _wide_document_id(canonical, origin)
    (
        attestation_source_id,
        attestation_evidence,
        redirect_verification,
        redirect_from_host,
        redirect_to_host,
    ) = _profile_attestation_for_url(state, canonical)
    return WideDocumentIdentity(
        company_id=state.company_id,
        document_id=document_id,
        canonical_url=canonical,
        source_kind=source_kind,
        publisher=host or "unknown",
        title=title or host or canonical,
        published_on="",
        collected_at=state.collected_at,
        content_sha256=content_sha256,
        identity_binding=identity_binding_with_scope(
            binding.identity_binding,
            origin.scope_digest,
        ),
        usable_ranges=ranges,
        collector_version=WIDE_COLLECTOR_VERSION,
        parser_version=WIDE_PARSER_VERSION,
        requirement=requirement,
        # DART hm_url 또는 canonical 공시 proof와 실제 이중 신원을 모두
        # 확인한 root는 TIER1이다. 그 밖의 교차 도메인은 TIER3에 머문다.
        source_tier=(
            SOURCE_TIER_1_OFFICIAL
            if binding.is_high_confidence
            else SOURCE_TIER_3_TRUSTED
        ),
        domain_attestation_source_id=attestation_source_id,
        domain_attestation_evidence=attestation_evidence,
        domain_redirect_verification=redirect_verification,
        domain_redirect_from_host=redirect_from_host,
        domain_redirect_to_host=redirect_to_host,
    )


def _ensure_host_policy(
    state: _CollectionState,
    *,
    origin: OfficialOrigin,
    transport: RawWideTransport,
) -> WideRobotsPolicy:
    cached = state.robots_policies.get(origin.host)
    if cached is not None:
        return cached
    started = state.clock()
    policy = load_robots_policy(
        robots_url=origin.robots_url,
        host=origin.host,
        fetch=transport,
        url_allowed=origin.allows_infrastructure_url,
    )
    elapsed_ms = int((state.clock() - started) * 1000)
    robots_state = ATTEMPT_STATE_FAILED if policy.blocked else ATTEMPT_STATE_OK
    # robots는 슬롯을 스스로 좁히지 못해 광역(17개 전체)을 쓴다 — 웹은 그
    # 17개 slot 전부의 유일한 확인 경로가 아니므로 상태와 무관하게 항상
    # OPTIONAL이다(_BROAD_SLOT_REQUIREMENT 참조).
    state.add_attempt(
        kind="robots",
        source_kind=SOURCE_KIND_ROBOTS_TXT,
        requirement=_BROAD_SLOT_REQUIREMENT,
        state=robots_state,
        slot_ids=(),
        reason_code=policy.reason_code,
        elapsed_ms=elapsed_ms,
        bytes_downloaded=0,
        documents_seen=0,
    )
    state.robots_policies[origin.host] = policy
    return policy


def _discover_sitemap(
    state: _CollectionState,
    *,
    origin: OfficialOrigin,
    transport: RawWideTransport,
    policy: WideRobotsPolicy,
    queue: list[_QueueItem],
    seen_canonical: set[str],
    root_host: str,
) -> None:
    started = state.clock()
    text, reason_code = fetch_sitemap(
        sitemap_url=origin.sitemap_url,
        fetch=transport,
        robots=policy,
        max_bytes=WIDE_MAX_SITEMAP_BYTES,
        url_allowed=origin.allows_infrastructure_url,
    )
    elapsed_ms = int((state.clock() - started) * 1000)
    urls = parse_sitemap_urls(text) if text else ()

    if reason_code == "sitemap_ok":
        outcome_state = (
            ATTEMPT_STATE_TRUNCATED if len(urls) >= WIDE_MAX_SITEMAP_ENTRIES else ATTEMPT_STATE_OK
        )
    elif reason_code.startswith("sitemap_missing"):
        outcome_state = ATTEMPT_STATE_MISSING
    else:  # sitemap_failed · robots_disallowed
        outcome_state = ATTEMPT_STATE_FAILED

    # sitemap도 슬롯을 스스로 좁히지 못해 광역(17개 전체)을 쓴다 — 웹은
    # 그 17개 slot 전부의 유일한 확인 경로가 아니므로 상태와 무관하게
    # 항상 OPTIONAL이다(_BROAD_SLOT_REQUIREMENT 참조).
    for candidate in urls:
        if not origin.allows_query_scope(candidate):
            continue
        candidate_host = (urllib.parse.urlsplit(candidate).hostname or "").casefold()
        if candidate_host == origin.host:
            if not origin.allows_content_url(candidate):
                continue
        elif bind_registered_subdomain(root_host, candidate_host) is None:
            continue  # sitemap이 도메인군 밖 URL을 적어도 따라가지 않는다
        canonical_candidate = (
            _scoped_canonical_url(candidate, origin)
            if candidate_host == origin.host
            else canonicalize_url(candidate)
        )
        if canonical_candidate in seen_canonical:
            continue
        seen_canonical.add(canonical_candidate)
        queue.append(
            _QueueItem(url=candidate, source_page_url=origin.sitemap_url)
        )

    state.add_attempt(
        kind="sitemap",
        source_kind=WIDE_SOURCE_KIND_WEB_PAGE,
        requirement=_BROAD_SLOT_REQUIREMENT,
        state=outcome_state,
        slot_ids=(),
        reason_code=reason_code,
        elapsed_ms=elapsed_ms,
        bytes_downloaded=len(text.encode("utf-8", errors="ignore")) if text else 0,
        documents_seen=0,
    )


# ══════════════════════════════════════════════════════════
# 공식 IR PDF — 기존 ir_pdf.collect_official_ir_fragments에 위임
# ══════════════════════════════════════════════════════════


def _run_ir_pdf_phase(
    state: _CollectionState,
    *,
    root_origin: OfficialOrigin,
    company_name: str,
    company_aliases: tuple[str, ...],
    ir_html_fetch: IrHtmlFetcher,
    ir_pdf_fetch: IrPdfFetcher,
    deadline: object,
) -> None:
    if not company_name.strip():
        return

    candidate_origins = sorted(
        (
            (state.bound_origins[host], binding)
            for host, binding in state.bound_hosts.items()
            if binding.is_high_confidence and host in state.bound_origins
        ),
        key=lambda item: (item[0].host != root_origin.host, item[0].host),
    )
    total_ir_documents = 0
    for origin, binding in candidate_origins:
        if total_ir_documents >= WIDE_MAX_IR_DOCUMENTS:
            return
        try:
            deadline.remaining()  # type: ignore[attr-defined]
        except HomepageResponseError:
            state.record_truncation(WIDE_SOURCE_KIND_IR_PDF, "truncated_time_cap")
            return

        # ★ 광역 웹 수집(_ensure_host_policy)이 이미 이 host의
        #   robots.txt를 확인 못했거나 명시적으로 거부됐다고 판정했으면 IR PDF
        #   시도 자체를 하지 않는다(요청 0) — ir_pdf._load_robots도 같은
        #   scope 캐시를 재사용해 새 네트워크 요청은 어차피 나가지 않지만,
        #   여기서 먼저 걸러야 그 host에 대한 불필요한 「ir」 attempt까지
        #   남기지 않는다. 판정 자체는 이미 「robots」 attempt로 기록돼 있다.
        cached_policy = state.robots_policies.get(origin.host)
        if cached_policy is not None and cached_policy.blocked:
            continue

        # 기존 IR 파서는 HTTPS exact-host 경계다. HTTP나 비기본 포트를
        # HTTPS:443으로 바꿔 접속하면 DART origin을 잃으므로, 지원하지 않는
        # origin에서는 네트워크를 0회로 두고 정직하게 TRUNCATED로 남긴다.
        if origin.scheme != "https" or origin.port != 443:
            state.add_attempt(
                kind="ir",
                source_kind=WIDE_SOURCE_KIND_IR_PDF,
                requirement=_BROAD_SLOT_REQUIREMENT,
                state=ATTEMPT_STATE_TRUNCATED,
                slot_ids=(),
                reason_code="ir_origin_unsupported",
                elapsed_ms=0,
                bytes_downloaded=0,
                documents_seen=0,
            )
            continue

        started = state.clock()
        result = collect_official_ir_fragments(
            origin.root_url,
            company_name=company_name,
            company_aliases=company_aliases,
            html_fetch=_origin_checked_ir_html_fetch(origin, ir_html_fetch),
            pdf_fetch=_origin_checked_ir_pdf_fetch(origin, ir_pdf_fetch),
        )
        elapsed_ms = int((state.clock() - started) * 1000)

        grouped: dict[str, list[dict[str, str]]] = {}
        order: list[str] = []
        for fragment in result.fragments:
            doc_key = str(fragment.get("문서ID") or fragment.get("출처") or "").strip()
            if not doc_key:
                continue
            if doc_key not in grouped:
                grouped[doc_key] = []
                order.append(doc_key)
            grouped[doc_key].append(fragment)

        documents_added = 0
        writer_ineligibility_reasons: set[str] = set()
        for doc_key in order:
            if total_ir_documents >= WIDE_MAX_IR_DOCUMENTS:
                break
            document = _build_ir_document(
                state,
                origin=origin,
                binding=binding,
                fragments=grouped[doc_key],
            )
            if document is None:
                continue
            state.documents.append(document)
            total_ir_documents += 1
            documents_added += 1
            reason = formal_document_writer_ineligibility_reason(document)
            if reason:
                writer_ineligibility_reasons.add(reason)

        state_map = {"ok": ATTEMPT_STATE_OK, "none": ATTEMPT_STATE_MISSING, "failed": ATTEMPT_STATE_FAILED}
        reason_map = {"ok": "ir_pdf_ok", "none": "ir_pdf_none", "failed": "ir_pdf_failed"}
        ir_attempt_state = state_map.get(result.state, ATTEMPT_STATE_FAILED)
        # IR PDF도 슬롯을 스스로 좁히지 못해 광역(17개 전체)을 쓴다(그
        # 조각화는 build_fragments가 문서별로 나중에 한다) — 웹은 그 17개
        # slot 전부의 유일한 확인 경로가 아니므로(공시 문서 수집·페이지
        # 유형이 좁힌 경로가 따로 있다) 상태와 무관하게 항상 OPTIONAL이다.
        # ★ 결합 종단시험에서 실측한 P0: IR FAILED
        # attempt 하나가 REQUIRED+광역으로 나가면, 다른 소스가 채운 근거
        # 까지 UNKNOWN으로 끌어내려 최종 게이트가 STOP_TRANSIENT_FAILURE로
        # 떨어졌다 — 이 상수(_BROAD_SLOT_REQUIREMENT)가 그걸 막는다.
        attempt_reason = reason_map.get(result.state, "ir_pdf_failed")
        if (
            result.state == "ok"
            and "official_ir_writer_metadata_incomplete"
            in writer_ineligibility_reasons
        ):
            attempt_reason = "official_ir_writer_metadata_incomplete"
        state.add_attempt(
            kind="ir",
            source_kind=WIDE_SOURCE_KIND_IR_PDF,
            requirement=_BROAD_SLOT_REQUIREMENT,
            state=ir_attempt_state,
            slot_ids=(),
            reason_code=attempt_reason,
            elapsed_ms=elapsed_ms,
            bytes_downloaded=result.downloaded_pdf_bytes,
            documents_seen=documents_added,
        )


def _origin_checked_ir_html_fetch(
    origin: OfficialOrigin,
    delegate: IrHtmlFetcher,
) -> IrHtmlFetcher:
    """IR HTML 요청·redirect를 DART origin과 허용 경로 안에 가둔다."""

    def checked(url: str, expected_hostname: str, url_allowed):
        is_infrastructure = urllib.parse.urlsplit(url).path == "/robots.txt"
        boundary = (
            origin.allows_infrastructure_url
            if is_infrastructure
            else origin.allows_content_url
        )
        if expected_hostname.casefold() != origin.host or not boundary(url):
            raise OfficialIrFetchError("공식 IR HTML이 DART origin·경로 밖입니다")

        def combined(candidate: str) -> bool:
            return boundary(candidate) and (
                url_allowed is None or url_allowed(candidate)
            )

        fetched = delegate(url, expected_hostname, combined)
        if not boundary(fetched.effective_url):
            raise OfficialIrFetchError("공식 IR HTML redirect가 DART origin·경로 밖입니다")
        return fetched

    return checked


def _origin_checked_ir_pdf_fetch(
    origin: OfficialOrigin,
    delegate: IrPdfFetcher,
) -> IrPdfFetcher:
    """동일 origin PDF 또는 공식 HTML의 exact 외부 첨부 한 건만 허용한다.

    외부 첨부는 host를 결속하거나 그 host의 다른 경로를 탐색하지 않는다.
    발견된 HTTPS URL 하나만 요청하고 redirect도 허용하지 않는다.
    """

    def checked(url: str, expected_hostname: str, max_bytes: int, url_allowed):
        normalized_url = safe_https_attachment_url(url)
        if not normalized_url:
            raise OfficialIrFetchError("공식 IR PDF URL 형식이 안전하지 않습니다")
        requested_host = (
            urllib.parse.urlsplit(normalized_url).hostname or ""
        ).casefold()
        if expected_hostname.casefold() != requested_host:
            raise OfficialIrFetchError("공식 IR PDF 요청 host 결속이 다릅니다")

        same_origin = requested_host == origin.host
        if same_origin and not origin.allows_content_url(normalized_url):
            raise OfficialIrFetchError("공식 IR PDF가 DART origin·경로 밖입니다")

        def combined(candidate: str) -> bool:
            normalized_candidate = safe_https_attachment_url(candidate)
            if not normalized_candidate:
                return False
            boundary_ok = (
                origin.allows_content_url(normalized_candidate)
                if same_origin
                else normalized_candidate == normalized_url
            )
            return boundary_ok and url_allowed(normalized_candidate)

        fetched = delegate(normalized_url, requested_host, max_bytes, combined)
        normalized_effective = safe_https_attachment_url(fetched.effective_url)
        if not normalized_effective:
            raise OfficialIrFetchError("공식 IR PDF 최종 URL 형식이 안전하지 않습니다")
        if same_origin:
            if not origin.allows_content_url(normalized_effective):
                raise OfficialIrFetchError("공식 IR PDF redirect가 DART origin·경로 밖입니다")
        elif normalized_effective != normalized_url:
            raise OfficialIrFetchError("외부 IR PDF는 exact URL redirect만 허용합니다")
        return fetched

    return checked


def _build_ir_document(
    state: _CollectionState,
    *,
    origin: OfficialOrigin,
    binding: BoundHost,
    fragments: list[dict[str, str]],
) -> WideDocumentIdentity | None:
    ranges = tuple(
        dict.fromkeys(
            str(fragment.get("원문") or "").strip()
            for fragment in fragments
            if str(fragment.get("원문") or "").strip()
        )
    )[:WIDE_MAX_USABLE_RANGES_PER_DOCUMENT]
    if not ranges:
        return None

    first = fragments[0]
    source_url = str(first.get("출처") or "").strip()
    if not source_url or not origin.allows_content_url(source_url):
        return None
    attachment_url = safe_https_attachment_url(
        str(first.get(IR_ATTACHMENT_URL_FIELD) or "")
    )
    attachment_host = (
        (urllib.parse.urlsplit(attachment_url).hostname or "").casefold()
        if attachment_url
        else ""
    )
    is_external_attachment = bool(attachment_host and attachment_host != origin.host)
    canonical = (
        canonicalize_url(attachment_url)
        if is_external_attachment
        else _scoped_canonical_url(source_url, origin)
    )
    content_sha256 = hashlib.sha256("\n".join(ranges).encode("utf-8")).hexdigest()
    if content_sha256 in state.content_hashes:
        return None
    state.content_hashes.add(content_sha256)

    document_id = _wide_document_id(canonical, origin)
    title = str(first.get("문서명") or "").strip() or origin.host
    published_on = str(first.get("문서일") or "").strip()
    (
        attestation_source_id,
        attestation_evidence,
        redirect_verification,
        redirect_from_host,
        redirect_to_host,
    ) = _profile_attestation_for_url(state, canonical)
    scoped_identity_binding = identity_binding_with_scope(
        binding.identity_binding,
        origin.scope_digest,
    )
    document = WideDocumentIdentity(
        company_id=state.company_id,
        document_id=document_id,
        canonical_url=canonical,
        source_kind=WIDE_SOURCE_KIND_IR_PDF,
        publisher=origin.host,
        title=title,
        published_on=published_on,
        collected_at=state.collected_at,
        content_sha256=content_sha256,
        identity_binding=(
            f"{scoped_identity_binding}; "
            f"공식 HTML exact-link 외부 IR 첨부: {attachment_url}"
            if is_external_attachment
            else scoped_identity_binding
        ),
        usable_ranges=ranges,
        collector_version=WIDE_COLLECTOR_VERSION,
        parser_version=WIDE_PARSER_VERSION,
        # 외부 CDN 파일은 공식 HTML이 그 exact URL을 가리켰다는 provenance만
        # 확인했다. CDN host 전체나 파일 발행자를 회사 공식이라고 승격하지
        # 않으며 필수 슬롯을 채울 수 없는 낮은 신뢰 후보로 보존한다.
        requirement=(
            REQUIREMENT_OPTIONAL if is_external_attachment else REQUIREMENT_REQUIRED
        ),
        source_tier=(
            SOURCE_TIER_3_TRUSTED if is_external_attachment else SOURCE_TIER_1_OFFICIAL
        ),
        domain_attestation_source_id=attestation_source_id,
        domain_attestation_evidence=attestation_evidence,
        reporting_period=str(first.get(IR_REPORTING_PERIOD_FIELD) or "").strip(),
        attachment_url=attachment_url,
        ir_metadata_verification=str(
            first.get(IR_METADATA_VERIFICATION_FIELD) or ""
        ).strip(),
        domain_redirect_verification=(
            str(first.get(IR_DART_WWW_REDIRECT_FIELD) or "").strip()
            or redirect_verification
        ),
        domain_redirect_from_host=(
            str(first.get(IR_DART_WWW_REDIRECT_FROM_FIELD) or "").strip()
            or redirect_from_host
        ),
        domain_redirect_to_host=(
            str(first.get(IR_DART_WWW_REDIRECT_TO_FIELD) or "").strip()
            or redirect_to_host
        ),
    )
    # 같은 공식 host의 PDF여도 anchor에서 발행일·보고기간을 exact로 확인하지
    # 못했다면 Writer로 보내지 않는다. 수집 문서 자체는 OPTIONAL provenance로
    # 남겨 자료 부족과 내부 배선 오류를 구분할 수 있게 한다.
    if (
        not is_external_attachment
        and formal_document_writer_ineligibility_reason(document)
        == "official_ir_writer_metadata_incomplete"
    ):
        document = replace(
            document,
            requirement=REQUIREMENT_OPTIONAL,
            source_tier=SOURCE_TIER_3_TRUSTED,
        )
    return document


def _priority_key(url: str) -> tuple[int, str]:
    lowered = urllib.parse.unquote(url).lower()
    for rank, keyword in enumerate(_PRIORITY_KEYWORDS):
        if keyword in lowered:
            return (rank, url)
    return (len(_PRIORITY_KEYWORDS), url)
