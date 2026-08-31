"""여러 공식 호스트에 흩어진 회사 공식 웹 문서를 결속 근거와 함께 모은다.

★ DART가 준 홈페이지 주소(root)에서 시작해 ①같은 등록 도메인의 하위 도메인
  ②공식 페이지 안에서 명시적으로 링크된 다른 호스트(«후보»)만 넓힌다.
  결속 근거 없는 호스트는 절대 수집하지 않는다(``wide_domain.py``).
★ 본문 조회 전 항상 그 호스트의 robots.txt를 먼저 확인한다(fail-closed —
  robots 자체를 확인 못하면 그 호스트는 긁지 않는다, ``wide_fetch.py``).
★ 이 모듈은 «공식 확정»을 선언하지 않는다 — root/하위도메인은 REQUIRED,
  링크로 발견된 후보 호스트는 OPTIONAL로만 표시한다. 최종 확정은 다음
  담당자(장별 근거 변환)의 몫이다.
★ 공식 IR PDF는 기존에 이미 검증된 ``ir_pdf.collect_official_ir_fragments``에
  호스트별로 위임한다 — 이 모듈이 PDF 파싱·격리 워커를 다시 구현하지 않는다
  (재사용 결정, 최종 보고서 참조).
"""

from __future__ import annotations

import hashlib
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Callable

from src.features.homepage.constants import (
    PRIORITY_PATH_KEYWORDS,
    WIDE_COLLECTION_TIMEOUT_SEC,
    WIDE_COLLECTOR_VERSION,
    WIDE_MAX_HOSTS,
    WIDE_MAX_IR_DOCUMENTS,
    WIDE_MAX_PAGES,
    WIDE_MAX_SITEMAP_BYTES,
    WIDE_MAX_SITEMAP_ENTRIES,
    WIDE_MAX_TOTAL_BYTES,
    WIDE_MAX_USABLE_RANGES_PER_DOCUMENT,
    WIDE_PARSER_VERSION,
    WIDE_PRIORITY_HOST_KEYWORDS,
    WIDE_REQUIRED_SLOT_IDS,
    WIDE_SOURCE_KIND_IR_PDF,
    WIDE_SOURCE_KIND_RECRUIT_PAGE,
    WIDE_SOURCE_KIND_WEB_PAGE,
)
from src.features.homepage.ir_pdf import (
    IrHtmlFetcher,
    IrPdfFetcher,
    collect_official_ir_fragments,
    default_ir_html_fetch,
    default_ir_pdf_fetch,
)
from src.features.homepage.safe_http import HomepageResponseError, request_deadline_scope
from src.features.homepage.wide_domain import (
    BoundHost,
    bind_linked_host,
    bind_registered_subdomain,
    bind_root_host,
    bind_www_apex_alternate,
    canonicalize_url,
    is_registered_subdomain,
    slot_ids_for_url,
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
    WideCollectionAttempt,
    WideCollectionResult,
    WideDocumentIdentity,
)

_PRIORITY_KEYWORDS: tuple[str, ...] = WIDE_PRIORITY_HOST_KEYWORDS + PRIORITY_PATH_KEYWORDS

#: url 안에 있으면 «채용 페이지」로 분류하는 키워드.
_RECRUIT_MARKERS: tuple[str, ...] = ("recruit", "career", "jobs", "채용")

#: P0-2: robots·sitemap·전체 truncation·IR처럼 «호스트/수집 전체」에 걸린
#: attempt이거나, 일반 페이지인데 URL로 페이지 유형을 못 알아낸 attempt에
#: 붙이는 fallback slot 집합. 앱 계약(CollectionAttempt)은 빈 slot_ids를
#: 생성 즉시 거절하므로(``WideCollectionAttempt.__post_init__``도 동일하게
#: 막는다) 특정 slot을 좁혀낼 수 없을 때도 항상 비어 있지 않은 집합을
#: 명시해야 한다 — 「이 결과 때문에 확인하지 못한(혹은 확인한) 모든 후보
#: slot」이라는 뜻으로 허용 어휘 17개 전체를 쓴다(``_CollectionState.add_attempt``
#: 참조 — 모든 attempt 생성이 이 한 곳을 거치므로 호출부마다 따로 챙기지 않아도
#: 절대 빈 slot_ids가 새 나가지 않는다).
#:
#: ★ 정정 1의 최종판(팀 리드 2026-08-31, P0 — 결합 종단시험에서 실측):
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
    documents: list[WideDocumentIdentity] = field(default_factory=list)
    attempts: list[WideCollectionAttempt] = field(default_factory=list)
    bound_hosts: dict[str, BoundHost] = field(default_factory=dict)
    robots_policies: dict[str, WideRobotsPolicy] = field(default_factory=dict)
    content_hashes: set[str] = field(default_factory=set)
    pages_fetched: int = 0
    total_bytes: int = 0
    attempt_counter: int = 0

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
                # P0-2: 빈 slot_ids는 절대 내보내지 않는다 — 좁혀낼 slot이 없으면
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


@dataclass(frozen=True)
class _QueueItem:
    """탐색 큐 항목 — 새 호스트를 만나면 결속 근거로 쓸 출처 페이지를 함께 든다."""

    url: str
    source_page_url: str


def collect_official_web_documents(
    *,
    company_id: str,
    company_name: str,
    root_homepage_url: str,
    collected_at: str,
    company_aliases: tuple[str, ...] = (),
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
        company_aliases: IR PDF 신원 대조에 함께 쓰는 공식 별칭.
        transport: 실제 네트워크 접속 함수. 시험에서는 가짜로 바꿔 끼운다.
        ir_html_fetch, ir_pdf_fetch: 공식 IR PDF 수집기(``ir_pdf.py``)에
            그대로 위임하는 접속 함수. 시험에서는 가짜로 바꿔 끼운다.
        clock: 시도 소요시간 측정에 쓰는 단조 시계. 시험에서 결정론적으로 고정 가능.

    Returns:
        문서·시도 기록을 담은 ``WideCollectionResult``. 상한 도달로 못 읽은
        부분은 문서를 지어내는 대신 ``TRUNCATED`` attempt로 남는다.
    """
    root_scheme, root_host = _normalize_root(root_homepage_url)
    state = _CollectionState(company_id=company_id, collected_at=collected_at, clock=clock)
    if not root_host:
        # 계약 gen=8 마지막 고리: 문서·attempt가 0건이어도 결과 자신은 항상
        # 대상 회사를 싣는다(documents에서 역산하지 않는다 — 역산하면 0건일 때
        # 정본을 잃는다).
        return WideCollectionResult(company_id=state.company_id, documents=(), attempts=())

    try:
        with request_deadline_scope(WIDE_COLLECTION_TIMEOUT_SEC) as deadline:
            _run_web_crawl(
                state,
                root_scheme=root_scheme,
                root_host=root_host,
                transport=transport,
                deadline=deadline,
            )
            _run_ir_pdf_phase(
                state,
                root_host=root_host,
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
    scheme: str,
    host: str,
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
    state.bound_hosts[host] = binding
    policy = _ensure_host_policy(state, scheme=scheme, host=host, transport=transport)
    if policy.blocked:
        return

    _discover_sitemap(
        state,
        scheme=scheme,
        host=host,
        transport=transport,
        policy=policy,
        queue=queue,
        seen_canonical=seen_canonical,
        root_host=root_host,
    )

    root_url = f"{scheme}://{host}/"
    canonical = canonicalize_url(root_url)
    if canonical in seen_canonical:
        return
    seen_canonical.add(canonical)
    queue.append(_QueueItem(url=root_url, source_page_url=root_url))


def _run_web_crawl(
    state: _CollectionState,
    *,
    root_scheme: str,
    root_host: str,
    transport: RawWideTransport,
    deadline: object,
) -> None:
    queue: list[_QueueItem] = []
    seen_canonical: set[str] = set()

    # APEX-WWW-OFFICIAL-ROOT-GAP(통합 담당 지시, 2026-08-31): DART가 준 호스트
    # («primary»)와 그 apex/www 짝을 각각 독립된 후보로 미리 심는다 — redirect를
    # 따라가는 방식이 아니라 «둘 다 직접 방문」하는 방식이라, 정확히 같은
    # host만 허용하는 redirect fail-closed 정책(앞서 고친 eTLD+1 결함 수정과
    # 같은 맥락, 여기서 완화하지 않는다)과 부딪히지 않는다. primary가 apex→www
    # (또는 반대) redirect 하나 때문에 robots·본문을 전혀 못 읽어도, 짝 호스트가
    # 각자 robots부터 따로 확인하며 독립적으로 시도되므로 수집이 0건이 되지
    # 않는다.
    _seed_host_root(
        state,
        scheme=root_scheme,
        host=root_host,
        binding=bind_root_host(root_host),
        transport=transport,
        queue=queue,
        seen_canonical=seen_canonical,
        root_host=root_host,
    )

    alternate_binding = bind_www_apex_alternate(root_host)
    if alternate_binding is not None:
        _seed_host_root(
            state,
            scheme=root_scheme,
            host=alternate_binding.host,
            binding=alternate_binding,
            transport=transport,
            queue=queue,
            seen_canonical=seen_canonical,
            root_host=root_host,
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
            root_host=root_host,
            transport=transport,
            queue=queue,
            seen_canonical=seen_canonical,
        )


def _visit_page(
    state: _CollectionState,
    *,
    item: _QueueItem,
    root_host: str,
    transport: RawWideTransport,
    queue: list[_QueueItem],
    seen_canonical: set[str],
) -> None:
    parsed = urllib.parse.urlsplit(item.url)
    host = (parsed.hostname or "").casefold()
    if not host:
        return

    binding = state.bound_hosts.get(host)
    if binding is None:
        binding = bind_registered_subdomain(root_host, host)
        if binding is None:
            if len(state.bound_hosts) >= WIDE_MAX_HOSTS:
                return
            binding = bind_linked_host(
                source_page_url=item.source_page_url,
                discovered_url=item.url,
                candidate_host=host,
            )
        if binding is None:
            return  # 제외 대상 호스트(소셜·광고 등) — 결속하지 않는다
        state.bound_hosts[host] = binding

    host_policy = state.robots_policies.get(host)
    if host_policy is None:
        scheme = parsed.scheme or "https"
        host_policy = _ensure_host_policy(state, scheme=scheme, host=host, transport=transport)
        if not host_policy.blocked:
            _discover_sitemap(
                state,
                scheme=scheme,
                host=host,
                transport=transport,
                policy=host_policy,
                queue=queue,
                seen_canonical=seen_canonical,
                root_host=root_host,
            )
    if host_policy.blocked or not host_policy.can_fetch(item.url):
        return  # robots 금지 — 절대 조회하지 않는다

    def url_allowed(candidate: str, expected_host: str = host, policy: WideRobotsPolicy = host_policy) -> bool:
        return _same_host(candidate, expected_host) and policy.can_fetch(candidate)

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
    source_kind = _source_kind_for(item.url)
    slot_ids = slot_ids_for_url(item.url)
    response_bytes = len((response.text if response else "").encode("utf-8", errors="ignore"))
    state.total_bytes += response_bytes

    documents_seen = 0
    if page_state == ATTEMPT_STATE_OK and response is not None:
        document = _build_web_document(
            state,
            response=response,
            source_kind=source_kind,
            requirement=requirement,
            binding=binding,
        )
        if document is not None:
            documents_seen = 1
            state.documents.append(document)
            for link in extract_links(response.text, response.effective_url):
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
    # 무관하게 항상 OPTIONAL이다 — FAILED도 예외가 아니다(팀 리드가 IR
    # FAILED 사례로 결합 종단시험에서 실측한 P0와 같은 원인, 2026-08-31).
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
    source_kind: str,
    requirement: str,
    binding: BoundHost,
) -> WideDocumentIdentity | None:
    body_ranges, title = extract_usable_ranges(response.text)
    ranges = body_ranges + extract_json_ld_ranges(response.text) + extract_inline_spa_ranges(response.text)
    ranges = tuple(dict.fromkeys(ranges))[:WIDE_MAX_USABLE_RANGES_PER_DOCUMENT]
    if not ranges:
        return None

    content_sha256 = hashlib.sha256("\n".join(ranges).encode("utf-8")).hexdigest()
    if content_sha256 in state.content_hashes:
        return None
    state.content_hashes.add(content_sha256)

    canonical = canonicalize_url(response.effective_url)
    host = (urllib.parse.urlsplit(canonical).hostname or "").casefold()
    document_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
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
        identity_binding=binding.identity_binding,
        usable_ranges=ranges,
        collector_version=WIDE_COLLECTOR_VERSION,
        parser_version=WIDE_PARSER_VERSION,
        requirement=requirement,
        #: 결속 확인된 공식 웹 문서 «후보» 등급. 최종 확정은 통합 담당의 몫이다.
        source_tier=SOURCE_TIER_1_OFFICIAL,
    )


def _ensure_host_policy(
    state: _CollectionState,
    *,
    scheme: str,
    host: str,
    transport: RawWideTransport,
) -> WideRobotsPolicy:
    cached = state.robots_policies.get(host)
    if cached is not None:
        return cached
    started = state.clock()
    policy = load_robots_policy(scheme=scheme, host=host, fetch=transport)
    elapsed_ms = int((state.clock() - started) * 1000)
    robots_state = ATTEMPT_STATE_FAILED if policy.blocked else ATTEMPT_STATE_OK
    # robots는 슬롯을 스스로 좁히지 못해 광역(17개 전체)을 쓴다 — 웹은 그
    # 17개 slot 전부의 유일한 확인 경로가 아니므로 상태와 무관하게 항상
    # OPTIONAL이다(_BROAD_SLOT_REQUIREMENT 참조).
    state.add_attempt(
        kind="robots",
        source_kind="robots_txt",
        requirement=_BROAD_SLOT_REQUIREMENT,
        state=robots_state,
        slot_ids=(),
        reason_code=policy.reason_code,
        elapsed_ms=elapsed_ms,
        bytes_downloaded=0,
        documents_seen=0,
    )
    state.robots_policies[host] = policy
    return policy


def _discover_sitemap(
    state: _CollectionState,
    *,
    scheme: str,
    host: str,
    transport: RawWideTransport,
    policy: WideRobotsPolicy,
    queue: list[_QueueItem],
    seen_canonical: set[str],
    root_host: str,
) -> None:
    started = state.clock()
    text, reason_code = fetch_sitemap(
        scheme=scheme,
        host=host,
        fetch=transport,
        robots=policy,
        max_bytes=WIDE_MAX_SITEMAP_BYTES,
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
        candidate_host = (urllib.parse.urlsplit(candidate).hostname or "").casefold()
        if candidate_host != host and not is_registered_subdomain(root_host, candidate_host):
            continue  # sitemap이 도메인군 밖 URL을 적어도 따라가지 않는다
        canonical_candidate = canonicalize_url(candidate)
        if canonical_candidate in seen_canonical:
            continue
        seen_canonical.add(canonical_candidate)
        queue.append(
            _QueueItem(url=candidate, source_page_url=f"{scheme}://{host}/sitemap.xml")
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
    root_host: str,
    company_name: str,
    company_aliases: tuple[str, ...],
    ir_html_fetch: IrHtmlFetcher,
    ir_pdf_fetch: IrPdfFetcher,
    deadline: object,
) -> None:
    if not company_name.strip():
        return

    candidate_hosts = sorted(
        (host for host, binding in state.bound_hosts.items() if binding.is_high_confidence),
        key=lambda host: (host != root_host, host),
    )
    total_ir_documents = 0
    for host in candidate_hosts:
        if total_ir_documents >= WIDE_MAX_IR_DOCUMENTS:
            return
        try:
            deadline.remaining()  # type: ignore[attr-defined]
        except HomepageResponseError:
            state.record_truncation(WIDE_SOURCE_KIND_IR_PDF, "truncated_time_cap")
            return

        started = state.clock()
        result = collect_official_ir_fragments(
            f"https://{host}/",
            company_name=company_name,
            company_aliases=company_aliases,
            html_fetch=ir_html_fetch,
            pdf_fetch=ir_pdf_fetch,
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
        for doc_key in order:
            if total_ir_documents >= WIDE_MAX_IR_DOCUMENTS:
                break
            document = _build_ir_document(state, host=host, fragments=grouped[doc_key])
            if document is None:
                continue
            state.documents.append(document)
            total_ir_documents += 1
            documents_added += 1

        state_map = {"ok": ATTEMPT_STATE_OK, "none": ATTEMPT_STATE_MISSING, "failed": ATTEMPT_STATE_FAILED}
        reason_map = {"ok": "ir_pdf_ok", "none": "ir_pdf_none", "failed": "ir_pdf_failed"}
        ir_attempt_state = state_map.get(result.state, ATTEMPT_STATE_FAILED)
        # IR PDF도 슬롯을 스스로 좁히지 못해 광역(17개 전체)을 쓴다(그
        # 조각화는 build_fragments가 문서별로 나중에 한다) — 웹은 그 17개
        # slot 전부의 유일한 확인 경로가 아니므로(공시 문서 수집·페이지
        # 유형이 좁힌 경로가 따로 있다) 상태와 무관하게 항상 OPTIONAL이다.
        # ★ 팀 리드가 결합 종단시험에서 실측한 P0(2026-08-31): IR FAILED
        # attempt 하나가 REQUIRED+광역으로 나가면, 다른 소스가 채운 근거
        # 까지 UNKNOWN으로 끌어내려 최종 게이트가 STOP_TRANSIENT_FAILURE로
        # 떨어졌다 — 이 상수(_BROAD_SLOT_REQUIREMENT)가 그걸 막는다.
        state.add_attempt(
            kind="ir",
            source_kind=WIDE_SOURCE_KIND_IR_PDF,
            requirement=_BROAD_SLOT_REQUIREMENT,
            state=ir_attempt_state,
            slot_ids=(),
            reason_code=reason_map.get(result.state, "ir_pdf_failed"),
            elapsed_ms=elapsed_ms,
            bytes_downloaded=result.downloaded_pdf_bytes,
            documents_seen=documents_added,
        )


def _build_ir_document(
    state: _CollectionState,
    *,
    host: str,
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
    if not source_url:
        return None
    canonical = canonicalize_url(source_url)
    content_sha256 = hashlib.sha256("\n".join(ranges).encode("utf-8")).hexdigest()
    if content_sha256 in state.content_hashes:
        return None
    state.content_hashes.add(content_sha256)

    document_id = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    title = str(first.get("문서명") or "").strip() or host
    published_on = str(first.get("문서일") or "").strip()
    binding = state.bound_hosts.get(host)
    identity_binding = (
        binding.identity_binding if binding is not None else f"도메인군 호스트: {host}"
    )
    return WideDocumentIdentity(
        company_id=state.company_id,
        document_id=document_id,
        canonical_url=canonical,
        source_kind=WIDE_SOURCE_KIND_IR_PDF,
        publisher=host,
        title=title,
        published_on=published_on,
        collected_at=state.collected_at,
        content_sha256=content_sha256,
        identity_binding=identity_binding,
        usable_ranges=ranges,
        collector_version=WIDE_COLLECTOR_VERSION,
        parser_version=WIDE_PARSER_VERSION,
        requirement=REQUIREMENT_REQUIRED,
        source_tier=SOURCE_TIER_1_OFFICIAL,
    )


# ══════════════════════════════════════════════════════════
# 작은 도우미
# ══════════════════════════════════════════════════════════


def _normalize_root(raw: str) -> tuple[str, str]:
    """DART hm_url을 (스킴, 소문자 호스트)로 정규화한다. 판정 불가면 ("", "")."""
    candidate = (raw or "").strip()
    if not candidate:
        return "", ""
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    try:
        parsed = urllib.parse.urlsplit(candidate)
    except ValueError:
        return "", ""
    if parsed.scheme and parsed.scheme.lower() not in ("http", "https"):
        return "", ""
    if not re.match(r"^https?://", candidate, re.IGNORECASE):
        candidate = f"https://{candidate}"
    try:
        parsed = urllib.parse.urlsplit(candidate)
    except ValueError:
        return "", ""
    host = (parsed.hostname or "").strip()
    if not host:
        return "", ""
    return parsed.scheme.lower(), host.casefold()


def _same_host(url: str, expected_host: str) -> bool:
    try:
        candidate_host = (urllib.parse.urlsplit(url).hostname or "").casefold()
    except ValueError:
        return False
    return candidate_host == expected_host


def _priority_key(url: str) -> tuple[int, str]:
    lowered = urllib.parse.unquote(url).lower()
    for rank, keyword in enumerate(_PRIORITY_KEYWORDS):
        if keyword in lowered:
            return (rank, url)
    return (len(_PRIORITY_KEYWORDS), url)


def _source_kind_for(url: str) -> str:
    lowered = urllib.parse.unquote(url).lower()
    if any(marker in lowered for marker in _RECRUIT_MARKERS):
        return WIDE_SOURCE_KIND_RECRUIT_PAGE
    return WIDE_SOURCE_KIND_WEB_PAGE
