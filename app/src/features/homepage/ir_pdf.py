"""회사 공식 홈페이지의 같은 호스트에서 IR PDF 근거를 안전하게 모은다.

이 수집기는 검색 결과·외부 뉴스·블로그를 입력으로 받지 않는다. DART 기업개황의
홈페이지 주소에서 시작해 정확히 같은 HTTPS 호스트 안의 IR 링크만 제한적으로
탐색하고, robots.txt가 막은 경로는 읽지 않는다. PDF는 바이트·문서·페이지·글자
상한을 모두 통과한 경우에만 페이지와 문단 경계를 보존한 조각으로 돌려준다.
"""

from __future__ import annotations

import hashlib
import http.client
import ipaddress
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Final
from urllib import robotparser

from src.features.homepage.constants import (
    IR_IDENTITY_CHECK_PAGES,
    IR_COLLECTION_TIMEOUT_SEC,
    IR_PDF_EXTRACTOR_VERSION,
    IR_PDF_PARSE_TIMEOUT_SEC,
    IR_PDF_WORKER_REAP_TIMEOUT_SEC,
    IR_PDF_WORKER_SLOT_TIMEOUT_SEC,
    MAX_IR_CHARS_PER_DOCUMENT,
    MAX_IR_CHARS_PER_FRAGMENT,
    MAX_IR_CHARS_PER_PAGE,
    MAX_IR_DECOMPRESSED_STREAM_BYTES,
    MAX_IR_DISCOVERY_LINKS,
    MAX_IR_DISCOVERY_PAGES,
    MAX_IR_DOCUMENT_TITLE_CHARS,
    MAX_IR_DOCUMENTS,
    MAX_IR_FRAGMENTS_PER_DOCUMENT,
    MAX_IR_PDF_BYTES,
    MAX_IR_PDF_PAGES,
    MAX_IR_RAW_CHARS_PER_PAGE,
    MAX_IR_ROOT_RECOVERY_OBJECTS,
    MAX_IR_TOTAL_CHARS,
    MAX_IR_TOTAL_PDF_BYTES,
    MAX_IR_WORKER_ADDRESS_SPACE_BYTES,
    MAX_IR_WORKER_CPU_SECONDS,
    MAX_IR_WORKER_OUTPUT_BYTES,
    MAX_CONCURRENT_IR_PDF_WORKERS,
    MIN_IR_FRAGMENT_CHARS,
    MULTI_LABEL_PUBLIC_SUFFIXES,
    TIMEOUT_SEC,
    USER_AGENT,
)
from src.features.homepage.robots_cache import RobotsDecision, cached_robots_decision
from src.features.homepage.safe_http import (
    HomepageResponseError,
    READ_CHUNK_BYTES,
    UnsafeHomepageUrlError,
    read_limited_text,
    request_deadline_scope,
    response_deadline,
    safe_urlopen,
    safe_urlopen_exact_https_host,
)
from src.shared.official_ir import (
    IR_ATTACHMENT_URL_FIELD,
    IR_DART_WWW_REDIRECT_FIELD,
    IR_DART_WWW_REDIRECT_FROM_FIELD,
    IR_DART_WWW_REDIRECT_TO_FIELD,
    IR_DART_WWW_REDIRECT_VALUE,
    IR_METADATA_VERIFICATION_FIELD,
    IR_METADATA_VERIFICATION_VALUE,
    IR_REPORTING_PERIOD_FIELD,
    dart_homepage_www_alias_url,
    extract_official_ir_anchor_metadata,
    safe_https_attachment_url,
)


OFFICIAL_IR_FRAGMENT_KIND: Final[str] = "공식 IR"
VERIFIED_FINAL_URL_FIELD: Final[str] = "후보출처검증"
VERIFIED_FINAL_URL_VALUE: Final[str] = "https_exact_dart_host"
PDF_CONTENT_TYPE: Final[str] = "application/pdf"
_RESOURCE_POLICY_STRICT: Final[str] = "strict"
_RESOURCE_POLICY_LOCAL_WINDOWS: Final[str] = "local-windows"
_RESOURCE_LIMITS_APPLIED: Final[str] = "applied"
_RESOURCE_LIMITS_LOCAL_WINDOWS_FALLBACK: Final[str] = "local-windows-fallback"
_RESOURCE_FAILURE_SETUP: Final[str] = "resource_limit_setup_failed"
_RESOURCE_FAILURE_EXCEEDED: Final[str] = "resource_limit_exceeded"
_WORKER_DEPLOYMENT_MODE_ENV: Final[str] = "IR_PDF_WORKER_DEPLOYMENT_MODE"
_PDF_WORKER_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_IR_PDF_WORKERS)

_IR_MARKERS: Final[tuple[str, ...]] = (
    "investor",
    "investors",
    "invest",
    "earnings",
    "earning",
    "results",
    "result",
    "presentation",
    "financial",
    "quarter",
    "실적",
    "투자정보",
    "투자자",
    "기업설명",
    "경영설명",
    "재무",
)
_SKIPPED_WEB_EXTENSIONS: Final[tuple[str, ...]] = (
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".hwp",
    ".zip",
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".svg",
    ".ico",
    ".css",
    ".js",
    ".mp4",
    ".mp3",
)
_DISALLOWED_CONTENT_MARKERS: Final[tuple[str, ...]] = (
    "/blog",
    "/search",
    "검색결과",
    "검색 결과",
)
_IDENTITY_LEGAL_WORDS: Final[frozenset[str]] = frozenset(
    {
        "주식회사",
        "유한회사",
        "corporation",
        "corp",
        "incorporated",
        "inc",
        "company",
        "co",
        "limited",
        "ltd",
    }
)
_GENERIC_IDENTITY_ALIASES: Final[frozenset[str]] = frozenset(
    {
        "company",
        "corporation",
        "holdings",
        "holding",
        "group",
        "global",
        "industry",
        "industries",
        "technology",
        "technologies",
        "market",
        "investor",
    }
)


class OfficialIrFetchError(Exception):
    """공식 IR 탐색·다운로드를 안전하게 완료하지 못했다."""


class OfficialIrRobotsUnavailable(OfficialIrFetchError):
    """robots.txt가 4xx로 명시적으로 존재하지 않거나 이용 불가하다."""


class OfficialIrRobotsUnreachable(OfficialIrFetchError):
    """서버·네트워크 오류로 robots.txt 정책 자체를 확인하지 못했다."""


@dataclass(frozen=True)
class FetchedIrHtml:
    """검증된 최종 URL과 HTML 본문."""

    html: str
    effective_url: str


@dataclass(frozen=True)
class FetchedIrPdf:
    """검증된 최종 URL, MIME, 상한 안의 PDF 바이트."""

    content: bytes
    effective_url: str
    content_type: str


UrlAllowPredicate = Callable[[str], bool]
IrHtmlFetcher = Callable[[str, str, UrlAllowPredicate | None], FetchedIrHtml]
IrPdfFetcher = Callable[[str, str, int, UrlAllowPredicate], FetchedIrPdf]
DartWwwRedirectProbe = Callable[[str, str], str]


@dataclass(frozen=True)
class OfficialIrCollectResult:
    """공식 IR PDF 수집 결과."""

    state: str
    fragments: list[dict[str, str]] = field(default_factory=list)
    detail: str = ""
    attempted_documents: int = 0
    downloaded_pdf_bytes: int = 0
    candidate_scope_complete: bool = False


@dataclass(frozen=True)
class _DiscoveredLink:
    url: str
    label: str
    context_url: str
    published_at: str = ""
    reporting_period: str = ""


@dataclass(frozen=True)
class _ParsedPdf:
    """격리 파서가 검증해 돌려준 페이지 글자와 추출기 정보."""

    pages: tuple[str, ...]
    extractor: str
    truncated_pages: frozenset[int]


@dataclass(frozen=True)
class _ExtractedDocument:
    """PDF 하나의 근거 조각과 상한 잘림 여부."""

    fragments: list[dict[str, str]]
    truncated: bool = False


@dataclass(frozen=True)
class _CompanyIdentityTerms:
    """DART 법인명에서 만든 필수 항과 충분히 식별적인 공식 별칭."""

    principal: frozenset[str]
    distinctive_aliases: frozenset[str]


def default_ir_html_fetch(
    url: str,
    expected_hostname: str,
    url_allowed: UrlAllowPredicate | None,
) -> FetchedIrHtml:
    """정확한 같은 HTTPS 호스트에서 제한된 HTML 한 장을 읽는다."""

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with safe_urlopen_exact_https_host(
            request,
            timeout=TIMEOUT_SEC,
            expected_hostname=expected_hostname,
            url_allowed=url_allowed,
        ) as response:
            effective_url = str(
                getattr(response, "geturl", lambda: url)() or ""
            ).strip()
            if not effective_url:
                raise HomepageResponseError("공식 IR HTML 최종 URL을 확인하지 못했습니다")
            return FetchedIrHtml(
                html=read_limited_text(response, timeout=TIMEOUT_SEC),
                effective_url=effective_url,
            )
    except urllib.error.HTTPError as exc:
        if urllib.parse.urlsplit(url).path == "/robots.txt":
            if 400 <= int(exc.code) <= 499:
                raise OfficialIrRobotsUnavailable(
                    f"HTTP {exc.code}: robots.txt 이용 불가"
                ) from exc
            raise OfficialIrRobotsUnreachable(
                f"HTTP {exc.code}: robots.txt 서버 오류"
            ) from exc
        raise OfficialIrFetchError(f"{type(exc).__name__}: {exc}") from exc
    except (
        UnsafeHomepageUrlError,
        HomepageResponseError,
        urllib.error.URLError,
        TimeoutError,
        OSError,
    ) as exc:
        if urllib.parse.urlsplit(url).path == "/robots.txt":
            raise OfficialIrRobotsUnreachable(
                f"{type(exc).__name__}: robots.txt 접근 실패"
            ) from exc
        raise OfficialIrFetchError(f"{type(exc).__name__}: {exc}") from exc


def default_ir_pdf_fetch(
    url: str,
    expected_hostname: str,
    max_bytes: int,
    url_allowed: UrlAllowPredicate,
) -> FetchedIrPdf:
    """정확한 같은 HTTPS 호스트에서 크기가 제한된 PDF 하나를 읽는다."""

    if max_bytes < 1 or max_bytes > MAX_IR_PDF_BYTES:
        raise OfficialIrFetchError("공식 IR PDF 요청 바이트 상한이 올바르지 않습니다")

    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": PDF_CONTENT_TYPE},
    )
    try:
        with safe_urlopen_exact_https_host(
            request,
            timeout=TIMEOUT_SEC,
            expected_hostname=expected_hostname,
            url_allowed=url_allowed,
        ) as response:
            effective_url = str(
                getattr(response, "geturl", lambda: url)() or ""
            ).strip()
            content_type = _response_content_type(response)
            content = _read_limited_pdf(
                response,
                timeout=TIMEOUT_SEC,
                max_bytes=max_bytes,
            )
            return FetchedIrPdf(
                content=content,
                effective_url=effective_url,
                content_type=content_type,
            )
    except (
        UnsafeHomepageUrlError,
        HomepageResponseError,
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
    ) as exc:
        raise OfficialIrFetchError(f"{type(exc).__name__}: {exc}") from exc


def collect_official_ir_fragments(
    homepage_url: str,
    *,
    company_name: str,
    company_aliases: tuple[str, ...] = (),
    html_fetch: IrHtmlFetcher = default_ir_html_fetch,
    pdf_fetch: IrPdfFetcher = default_ir_pdf_fetch,
    allow_dart_www_alias: bool = False,
    www_redirect_probe: DartWwwRedirectProbe | None = None,
) -> OfficialIrCollectResult:
    """공식 IR 수집 전체를 하나의 절대시간·DNS cache 경계에서 실행한다.

    ``allow_dart_www_alias``는 서비스 파이프라인만 켠다. DART가 apex를 적었지만
    공개 홈페이지가 ``www``에서만 열리는 경우, apex 수집이 성공하지 못했을 때
    동일 경로의 ``www`` 정확한 HTTPS host를 딱 한 번 더 확인한다.
    """

    try:
        with request_deadline_scope(IR_COLLECTION_TIMEOUT_SEC) as deadline:
            result = _collect_official_ir_fragments_impl(
                homepage_url,
                company_name=company_name,
                company_aliases=company_aliases,
                html_fetch=html_fetch,
                pdf_fetch=pdf_fetch,
            )
            alias_url = (
                dart_homepage_www_alias_url(homepage_url)
                if allow_dart_www_alias and result.state != "ok"
                else ""
            )
            probe = www_redirect_probe or default_dart_www_redirect_probe
            verified_alias_url = probe(homepage_url, alias_url) if alias_url else ""
            if alias_url and verified_alias_url == alias_url:
                alias_result = _collect_official_ir_fragments_impl(
                    verified_alias_url,
                    company_name=company_name,
                    company_aliases=company_aliases,
                    html_fetch=html_fetch,
                    pdf_fetch=pdf_fetch,
                )
                if alias_result.state == "ok" or (
                    result.state == "failed" and alias_result.state != "failed"
                ):
                    alias_note = "DART apex의 제한된 www 별칭에서 확인"
                    apex_host = (
                        urllib.parse.urlsplit(_normalize_root_url(homepage_url)[0]).hostname
                        or ""
                    ).casefold().rstrip(".")
                    alias_host = (
                        urllib.parse.urlsplit(verified_alias_url).hostname or ""
                    ).casefold().rstrip(".")
                    result = replace(
                        alias_result,
                        fragments=[
                            {
                                **fragment,
                                IR_DART_WWW_REDIRECT_FIELD: IR_DART_WWW_REDIRECT_VALUE,
                                IR_DART_WWW_REDIRECT_FROM_FIELD: apex_host,
                                IR_DART_WWW_REDIRECT_TO_FIELD: alias_host,
                            }
                            for fragment in alias_result.fragments
                        ],
                        detail=(
                            f"{alias_note}; {alias_result.detail}"
                            if alias_result.detail
                            else alias_note
                        ),
                    )
            deadline.remaining()
            return result
    except HomepageResponseError as exc:
        return OfficialIrCollectResult(
            state="failed",
            detail=f"공식 IR 수집 전체시간 초과: {exc}",
            candidate_scope_complete=False,
        )


def default_dart_www_redirect_probe(apex_url: str, alias_url: str) -> str:
    """공인 HTTPS apex가 정확한 ``www`` 별칭으로 리다이렉트하는지 1회 확인한다."""

    apex_root, apex_host = _normalize_root_url(apex_url)
    alias_root, alias_host = _normalize_root_url(alias_url)
    if (
        not apex_root
        or not alias_root
        or not apex_host
        or alias_host != f"www.{apex_host}"
    ):
        return ""

    allowed_hosts = frozenset({apex_host, alias_host})

    def url_allowed(value: str) -> bool:
        normalized = safe_https_attachment_url(value)
        if not normalized:
            return False
        try:
            host = (urllib.parse.urlsplit(normalized).hostname or "").casefold().rstrip(".")
        except ValueError:
            return False
        return host in allowed_hosts

    request = urllib.request.Request(apex_root, headers={"User-Agent": USER_AGENT})
    try:
        with safe_urlopen(
            request,
            timeout=TIMEOUT_SEC,
            url_allowed=url_allowed,
        ) as response:
            effective = str(
                getattr(response, "geturl", lambda: "")() or ""
            ).strip()
    except (
        UnsafeHomepageUrlError,
        HomepageResponseError,
        urllib.error.URLError,
        TimeoutError,
        OSError,
        http.client.HTTPException,
    ):
        return ""
    normalized_effective = safe_https_attachment_url(effective)
    if not normalized_effective or not url_allowed(normalized_effective):
        return ""
    try:
        final_host = (
            urllib.parse.urlsplit(normalized_effective).hostname or ""
        ).casefold().rstrip(".")
    except ValueError:
        return ""
    return alias_root if final_host == alias_host else ""


def _collect_official_ir_fragments_impl(
    homepage_url: str,
    *,
    company_name: str,
    company_aliases: tuple[str, ...] = (),
    html_fetch: IrHtmlFetcher = default_ir_html_fetch,
    pdf_fetch: IrPdfFetcher = default_ir_pdf_fetch,
) -> OfficialIrCollectResult:
    """DART 홈페이지 주소와 같은 HTTPS 호스트의 IR PDF 조각을 모은다."""

    if not str(homepage_url or "").strip():
        return OfficialIrCollectResult(
            state="none",
            detail="DART 기업개황에 공식 홈페이지 주소 없음",
            candidate_scope_complete=True,
        )
    root_url, exact_hostname = _normalize_root_url(homepage_url)
    if not root_url or not exact_hostname:
        return OfficialIrCollectResult(
            state="none", detail="공식 IR 탐색에 쓸 HTTPS 홈페이지 주소 없음"
        )
    identity_terms = _company_identity_terms(
        company_name,
        company_aliases,
        homepage_hostname=exact_hostname,
    )
    if not identity_terms.principal:
        return OfficialIrCollectResult(
            state="none", detail="공식 IR PDF와 대조할 DART 법인명 없음"
        )

    try:
        robots = _load_robots(root_url, exact_hostname, html_fetch)
    except OfficialIrRobotsUnreachable as exc:
        return OfficialIrCollectResult(
            state="failed",
            detail=f"robots.txt 정책을 확인하지 못함: {exc}",
            candidate_scope_complete=False,
        )
    url_allowed = lambda candidate: robots.can_fetch(USER_AGENT, candidate)
    if not url_allowed(root_url):
        return OfficialIrCollectResult(
            state="failed",
            detail="일부 실패 1개(robots.txt가 홈페이지 시작 경로 수집을 차단)",
        )

    html_queue = [root_url]
    queued_html = {root_url}
    inherited_document_metadata: dict[str, tuple[str, str]] = {}
    visited_html: set[str] = set()
    pdf_candidates: list[_DiscoveredLink] = []
    queued_pdf: set[str] = set()
    root_failed = False
    discovery_truncated = False
    robots_blocked_paths = 0
    html_page_failures = 0

    while html_queue and len(visited_html) < MAX_IR_DISCOVERY_PAGES:
        page_url = html_queue.pop(0)
        queued_html.discard(page_url)
        if page_url in visited_html:
            continue
        if not robots.can_fetch(USER_AGENT, page_url):
            robots_blocked_paths += 1
            continue
        visited_html.add(page_url)
        try:
            page = html_fetch(page_url, exact_hostname, url_allowed)
            effective_url = _validated_effective_url(
                page.effective_url, exact_hostname
            )
            if (
                not isinstance(page.html, str)
                or not effective_url
                or not url_allowed(effective_url)
            ):
                raise OfficialIrFetchError("공식 IR HTML 응답 검증 실패")
        except (OfficialIrFetchError, AttributeError, TypeError, ValueError):
            html_page_failures += 1
            if page_url == root_url:
                root_failed = True
            continue

        inherited_date, inherited_period = inherited_document_metadata.get(
            page_url, ("", "")
        )
        links = _extract_links(
            page.html,
            effective_url,
            exact_hostname,
            allow_external_pdf=effective_url != root_url,
        )
        links.sort(key=_link_priority)
        for link in links:
            if len(queued_html) + len(visited_html) + len(queued_pdf) >= MAX_IR_DISCOVERY_LINKS:
                discovery_truncated = True
                break
            if _is_pdf_candidate_link(link):
                if link.url in queued_pdf or not _looks_like_ir_link(link):
                    continue
                if not (link.published_at and link.reporting_period) and (
                    inherited_date and inherited_period
                ):
                    link = replace(
                        link,
                        published_at=inherited_date,
                        reporting_period=inherited_period,
                    )
                queued_pdf.add(link.url)
                link_host = (
                    urllib.parse.urlsplit(link.url).hostname or ""
                ).casefold().rstrip(".")
                if link_host != exact_hostname or robots.can_fetch(USER_AGENT, link.url):
                    pdf_candidates.append(link)
                else:
                    robots_blocked_paths += 1
                continue
            if (
                link.url not in visited_html
                and link.url not in queued_html
                and _looks_like_ir_link(link)
                and not _has_skipped_extension(link.url)
            ):
                html_queue.append(link.url)
                queued_html.add(link.url)
                if link.published_at and link.reporting_period:
                    inherited_document_metadata[link.url] = (
                        link.published_at,
                        link.reporting_period,
                    )
        html_queue.sort(key=lambda value: _text_ir_priority(value))

    if html_queue:
        discovery_truncated = True

    if root_failed and not visited_html - {root_url}:
        return OfficialIrCollectResult(
            state="failed", detail="공식 IR 링크를 찾을 홈페이지에 접속하지 못함"
        )

    fragments: list[dict[str, str]] = []
    total_chars = 0
    attempted_documents = 0
    downloaded_pdf_bytes = 0
    valid_documents = 0
    failed_documents = 0
    duplicate_documents = 0
    truncated_documents = 0
    seen_document_hashes: set[str] = set()
    for candidate in pdf_candidates:
        if (
            attempted_documents >= MAX_IR_DOCUMENTS
            or total_chars >= MAX_IR_TOTAL_CHARS
            or MAX_IR_TOTAL_CHARS - total_chars < MIN_IR_FRAGMENT_CHARS
            or downloaded_pdf_bytes >= MAX_IR_TOTAL_PDF_BYTES
        ):
            break
        attempted_documents += 1
        remaining_pdf_bytes = MAX_IR_TOTAL_PDF_BYTES - downloaded_pdf_bytes
        allowed_pdf_bytes = min(MAX_IR_PDF_BYTES, remaining_pdf_bytes)
        try:
            attachment_hostname = (
                urllib.parse.urlsplit(candidate.url).hostname or ""
            ).casefold().rstrip(".")
            attachment_allowed = (
                url_allowed
                if attachment_hostname == exact_hostname
                else lambda value, expected=candidate.url: value == expected
            )
            fetched = pdf_fetch(
                candidate.url,
                attachment_hostname,
                allowed_pdf_bytes,
                attachment_allowed,
            )
            if not isinstance(fetched.content, bytes):
                raise OfficialIrFetchError("공식 IR PDF 응답 바이트 검증 실패")
            downloaded_pdf_bytes += len(fetched.content)
            if len(fetched.content) > allowed_pdf_bytes:
                raise OfficialIrFetchError("공식 IR PDF 합계 바이트 상한 위반")
            effective_url = _validated_effective_url(
                fetched.effective_url, attachment_hostname
            )
            if not effective_url:
                raise OfficialIrFetchError("공식 IR PDF 최종 URL 검증 실패")
            if not attachment_allowed(effective_url):
                raise OfficialIrFetchError(
                    "공식 IR PDF 리다이렉트가 robots.txt 차단 경로에 도착했습니다"
                )
            document_hash = hashlib.sha256(fetched.content).hexdigest()
            if document_hash in seen_document_hashes:
                duplicate_documents += 1
                continue
            seen_document_hashes.add(document_hash)
            extracted = _extract_pdf_fragments(
                fetched,
                source_url=(
                    candidate.context_url
                    if attachment_hostname != exact_hostname
                    else effective_url
                ),
                attachment_url=effective_url,
                document_title=_document_title(candidate),
                published_at=candidate.published_at,
                reporting_period=candidate.reporting_period,
                remaining_total_chars=MAX_IR_TOTAL_CHARS - total_chars,
                identity_terms=identity_terms,
            )
        except (
            OfficialIrFetchError,
            HomepageResponseError,
            UnsafeHomepageUrlError,
            urllib.error.URLError,
            TimeoutError,
            OSError,
            http.client.HTTPException,
        ):
            failed_documents += 1
            continue
        valid_documents += 1
        fragments.extend(extracted.fragments)
        total_chars += sum(len(item["원문"]) for item in extracted.fragments)
        if extracted.truncated:
            truncated_documents += 1

    detail_parts: list[str] = []
    if discovery_truncated:
        detail_parts.append("탐색 상한 잘림")
    if html_page_failures:
        detail_parts.append(f"일부 실패 {html_page_failures}개(HTML 탐색)")
    if robots_blocked_paths:
        detail_parts.append(
            f"일부 실패 {robots_blocked_paths}개(robots.txt 차단 경로 미확인)"
        )
    if len(pdf_candidates) > attempted_documents:
        detail_parts.append(
            f"문서·바이트·AI입력 상한으로 {len(pdf_candidates) - attempted_documents}개 후보 미시도"
        )
    if failed_documents:
        detail_parts.append(
            f"일부 실패 {failed_documents}개(다운로드·검증·파서)"
        )
    if duplicate_documents:
        detail_parts.append(f"같은 콘텐츠 SHA-256 중복 {duplicate_documents}개 제외")
    if truncated_documents:
        detail_parts.append(f"페이지·글자 상한 잘림 {truncated_documents}개")
    detail = "; ".join(detail_parts)
    candidate_scope_complete = not any(
        (
            discovery_truncated,
            html_page_failures,
            robots_blocked_paths,
            len(pdf_candidates) > attempted_documents,
            failed_documents,
            truncated_documents,
        )
    )
    if not fragments:
        discovery_incomplete = bool(
            discovery_truncated or html_page_failures or robots_blocked_paths
        )
        if (
            discovery_incomplete
            or (pdf_candidates and failed_documents and not valid_documents)
        ):
            return OfficialIrCollectResult(
                state="failed",
                detail=detail or "공식 IR PDF 다운로드·검증·파서 실패",
                attempted_documents=attempted_documents,
                downloaded_pdf_bytes=downloaded_pdf_bytes,
                candidate_scope_complete=False,
            )
        return OfficialIrCollectResult(
            state="none",
            detail=detail
            or (
                "유효한 공식 IR PDF에 추출 가능한 글자가 없음"
                if valid_documents
                else "같은 HTTPS 호스트에서 공식 IR PDF 링크를 찾지 못함"
            ),
            attempted_documents=attempted_documents,
            downloaded_pdf_bytes=downloaded_pdf_bytes,
            candidate_scope_complete=candidate_scope_complete,
        )
    return OfficialIrCollectResult(
        state="ok",
        fragments=fragments,
        detail=detail,
        attempted_documents=attempted_documents,
        downloaded_pdf_bytes=downloaded_pdf_bytes,
        candidate_scope_complete=candidate_scope_complete,
    )


class _AnchorExtractor(HTMLParser):
    """링크 주소와 사람이 보는 앵커 글자를 함께 보존한다."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href = ""
        self._text: list[str] = []
        self._anchor_depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        if tag == "a" and self._anchor_depth == 0:
            self._href = str(dict(attrs).get("href") or "").strip()
            self._text = []
            self._anchor_depth = 1
        elif self._anchor_depth:
            self._anchor_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if not self._anchor_depth:
            return
        self._anchor_depth -= 1
        if tag == "a" or self._anchor_depth == 0:
            if self._href:
                self.links.append((self._href, _clean_inline(" ".join(self._text))))
            self._href = ""
            self._text = []
            self._anchor_depth = 0

    def handle_data(self, data: str) -> None:
        if self._anchor_depth and data.strip():
            self._text.append(data.strip())


def _normalize_root_url(raw: str) -> tuple[str, str]:
    """스킴 없는 DART 홈페이지 주소는 HTTPS로만 정규화한다."""

    candidate = str(raw or "").strip()
    if not candidate:
        return "", ""
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    elif candidate.casefold().startswith("http://"):
        # DART 기업개황은 오래된 http 스킴을 보존한 경우가 있다. 신뢰 대상은
        # host이고 실제 수집은 반드시 같은 host의 HTTPS로만 강제한다.
        candidate = f"https://{candidate[len('http://'):]}"
    try:
        parsed = urllib.parse.urlsplit(candidate)
        hostname = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii")
        port = parsed.port
    except (TypeError, ValueError, UnicodeError):
        return "", ""
    if (
        parsed.scheme.casefold() != "https"
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or hostname.casefold() == "localhost"
        or hostname.casefold().endswith(".localhost")
        or "." not in hostname
        or "\\" in candidate
        or any(ord(character) < 32 for character in candidate)
    ):
        return "", ""
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return "", ""
    path = urllib.parse.quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    normalized = urllib.parse.urlunsplit(
        ("https", hostname.casefold(), path, query, "")
    )
    return normalized, hostname.casefold()


def _validated_effective_url(raw: str, exact_hostname: str) -> str:
    """최종 URL이 정확한 호스트의 기본 HTTPS 주소일 때만 정규화한다."""

    candidate = str(raw or "").strip()
    try:
        parsed = urllib.parse.urlsplit(candidate)
        hostname = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii")
        port = parsed.port
    except (TypeError, ValueError, UnicodeError):
        return ""
    if (
        parsed.scheme.casefold() != "https"
        or hostname.casefold() != exact_hostname.casefold()
        or port not in (None, 443)
        or parsed.username is not None
        or parsed.password is not None
        or "\\" in candidate
        or any(ord(character) < 32 for character in candidate)
    ):
        return ""
    path = urllib.parse.quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    return urllib.parse.urlunsplit(
        ("https", hostname.casefold(), path, query, "")
    )


def _load_robots(
    root_url: str,
    exact_hostname: str,
    html_fetch: IrHtmlFetcher,
) -> robotparser.RobotFileParser:
    """같은 호스트의 robots.txt를 읽고, 없으면 빈 규칙으로 처리한다.

    ★ 같은 조사(scope) 안에서 이미 다른 수집기(홈페이지·광역 웹)가 같은
      host의 robots.txt를 확인했으면 새 네트워크 요청 없이 그 판정을
      재사용한다(``robots_cache.cached_robots_decision`` — 티켓 B2: 최대 4회
      중복 요청 실측). 특히 광역 웹 수집의 IR 위임(``wide_collect.
      _run_ir_pdf_phase``)에서는 이 재사용이 그 host의 광역(더 엄격한
      RFC 9309) 판정을 그대로 물려받는다 — 의도한 동작이다.
    """

    robots_url = urllib.parse.urlunsplit(
        ("https", exact_hostname, "/robots.txt", "", "")
    )
    host = exact_hostname.casefold()

    def loader() -> RobotsDecision:
        parser = robotparser.RobotFileParser()
        try:
            # robots.txt 자체를 받아야 규칙을 알 수 있으므로 부트스트랩 요청만 예외다.
            page = html_fetch(robots_url, exact_hostname, None)
            if not _validated_effective_url(page.effective_url, exact_hostname):
                raise OfficialIrFetchError("robots.txt 최종 URL 검증 실패")
            text = page.html if isinstance(page.html, str) else ""
        except OfficialIrRobotsUnavailable:
            text = ""
        except (OfficialIrFetchError, AttributeError, TypeError, ValueError):
            return RobotsDecision(
                host=host, parser=parser, blocked=True, reason_code="robots_unreachable"
            )
        parser.set_url(robots_url)
        parser.parse(text.splitlines())
        return RobotsDecision(host=host, parser=parser, blocked=False, reason_code="robots_ok")

    decision = cached_robots_decision(host, loader)
    if decision.blocked:
        raise OfficialIrRobotsUnreachable(
            f"robots.txt 서버·네트워크 상태를 확인할 수 없습니다 (reason={decision.reason_code})"
        )
    return decision.parser


def _extract_links(
    raw_html: str,
    base_url: str,
    exact_hostname: str,
    *,
    allow_external_pdf: bool = False,
) -> list[_DiscoveredLink]:
    """같은 정확한 HTTPS 호스트의 링크만 정규화해 뽑는다."""

    parser = _AnchorExtractor()
    try:
        parser.feed(raw_html)
    except (ValueError, TypeError):
        return []
    links: list[_DiscoveredLink] = []
    seen: set[str] = set()
    for href, label in parser.links:
        absolute = urllib.parse.urljoin(base_url, href).split("#", 1)[0]
        normalized = _validated_effective_url(absolute, exact_hostname)
        if not normalized and allow_external_pdf:
            external = safe_https_attachment_url(absolute)
            provisional = _DiscoveredLink(
                url=external,
                label=label,
                context_url=base_url,
            )
            if external and _is_pdf_candidate_link(provisional):
                normalized = external
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        published_at, reporting_period = extract_official_ir_anchor_metadata(label)
        links.append(
            _DiscoveredLink(
                url=normalized,
                label=label,
                context_url=base_url,
                published_at=published_at,
                reporting_period=reporting_period,
            )
        )
    return links


def _is_pdf_url(url: str) -> bool:
    try:
        path = urllib.parse.unquote(urllib.parse.urlsplit(url).path).casefold()
    except (TypeError, ValueError):
        return False
    return path.endswith(".pdf")


def _is_pdf_candidate_link(link: _DiscoveredLink) -> bool:
    """확장자 또는 명시적 PDF 다운로드 표지가 있는 링크인지 판정한다."""

    if _is_pdf_url(link.url):
        return True
    direct_text = " ".join((link.url, link.label)).casefold()
    try:
        parsed = urllib.parse.urlsplit(link.url)
    except (TypeError, ValueError):
        return False
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    query_marks_pdf = any(
        "pdf" in str(item).casefold()
        for key, values in query.items()
        for item in (key, *values)
    )
    endpoint_marks_download = any(
        marker in parsed.path.casefold()
        for marker in ("download", "attachment", "filedown", "file_down")
    )
    return "pdf" in direct_text and (query_marks_pdf or endpoint_marks_download)


def _has_skipped_extension(url: str) -> bool:
    try:
        path = urllib.parse.unquote(urllib.parse.urlsplit(url).path).casefold()
    except (TypeError, ValueError):
        return True
    return path.endswith(_SKIPPED_WEB_EXTENSIONS)


def _looks_like_ir_link(link: _DiscoveredLink) -> bool:
    text = " ".join((link.url, link.label, link.context_url)).casefold()
    direct_text = " ".join((link.url, link.label)).casefold()
    if any(marker in direct_text for marker in _DISALLOWED_CONTENT_MARKERS):
        return False
    if re.search(r"\bir\b", text) or re.search(
        r"(?:^|[/_.?=&\-])ir(?:$|[/_.?=&\-])", text
    ):
        return True
    return any(marker in text for marker in _IR_MARKERS)


def _text_ir_priority(value: str) -> int:
    lowered = value.casefold()
    # IR 자료·실적 목록과 주가·재무 일반 페이지가 모두 `/IR/`을
    # 포함하는 사이트에서 URL 정렬순이 5쪽 탐색 상한을 먼저 먹지 않게
    # 자료실·발표·실적 표지를 닫힌 우선순위로 올린다.
    if any(
        marker in lowered
        for marker in (
            "ir-data",
            "ir_data",
            "irdata",
            "ir 자료",
            "ir자료",
            "earnings",
            "results",
            "presentation",
            "실적발표",
            "기업설명",
        )
    ):
        return 0
    if any(
        marker in lowered
        for marker in ("/stock", "stock-price", "share-price", "주가정보")
    ):
        return 100
    if re.search(r"\bir\b", lowered) or re.search(
        r"(?:^|[/_.?=&\-])ir(?:$|[/_.?=&\-])", lowered
    ):
        return 20
    for index, marker in enumerate(_IR_MARKERS, start=1):
        if marker in lowered:
            return index
    return len(_IR_MARKERS) + 1


def _link_priority(link: _DiscoveredLink) -> tuple[int, str]:
    return (_text_ir_priority(" ".join((link.label, link.url))), link.url)


def _document_title(candidate: _DiscoveredLink) -> str:
    title = _clean_inline(candidate.label)
    if not title:
        path = urllib.parse.unquote(urllib.parse.urlsplit(candidate.url).path)
        title = path.rsplit("/", 1)[-1]
    title = re.sub(r"\.pdf$", "", title, flags=re.IGNORECASE).strip(" ._-")
    return (title or "공식 IR 자료")[:MAX_IR_DOCUMENT_TITLE_CHARS]


def _extract_pdf_fragments(
    fetched: FetchedIrPdf,
    *,
    source_url: str,
    attachment_url: str = "",
    document_title: str,
    published_at: str = "",
    reporting_period: str = "",
    remaining_total_chars: int,
    identity_terms: _CompanyIdentityTerms | frozenset[str],
) -> _ExtractedDocument:
    """PDF 하나를 페이지·문단별 근거 조각으로 바꾼다."""

    content = fetched.content
    content_type = str(fetched.content_type or "").split(";", 1)[0].strip().casefold()
    if (
        not isinstance(content, bytes)
        or not content
        or len(content) > MAX_IR_PDF_BYTES
        or content_type != PDF_CONTENT_TYPE
        or not content.startswith(b"%PDF-")
        or remaining_total_chars < MIN_IR_FRAGMENT_CHARS
    ):
        raise OfficialIrFetchError("공식 IR PDF 형식 또는 크기 검증 실패")

    parsed = _parse_pdf_with_timeout(content)
    if not any(_clean_inline(page) for page in parsed.pages):
        raise OfficialIrFetchError(
            "공식 IR PDF에 OCR 없이 추출 가능한 글자가 없습니다"
        )
    if not _identity_matches(
        parsed.pages[:IR_IDENTITY_CHECK_PAGES], identity_terms
    ):
        raise OfficialIrFetchError(
            "공식 IR PDF 앞쪽 페이지에서 대상 법인명·별칭을 확인하지 못했습니다"
        )

    document_id = hashlib.sha256(content).hexdigest()
    document_limit = min(MAX_IR_CHARS_PER_DOCUMENT, remaining_total_chars)
    fragments: list[dict[str, str]] = []
    extracted_chars = 0
    truncated = bool(parsed.truncated_pages)
    for page_number, raw_text in enumerate(parsed.pages, start=1):
        if (
            extracted_chars >= document_limit
            or len(fragments) >= MAX_IR_FRAGMENTS_PER_DOCUMENT
        ):
            truncated = True
            break
        page_chars = 0
        paragraphs = _paragraphs(raw_text)
        for paragraph_number, paragraph in enumerate(paragraphs, start=1):
            parts = _bounded_paragraph_parts(paragraph)
            for part_number, part in enumerate(parts, start=1):
                if len(fragments) >= MAX_IR_FRAGMENTS_PER_DOCUMENT:
                    truncated = True
                    break
                remaining_document = document_limit - extracted_chars
                remaining_page = MAX_IR_CHARS_PER_PAGE - page_chars
                remaining = min(remaining_document, remaining_page)
                if remaining < MIN_IR_FRAGMENT_CHARS:
                    truncated = True
                    break
                kept = part[:remaining].strip()
                if len(part) > len(kept):
                    truncated = True
                if len(kept) < MIN_IR_FRAGMENT_CHARS:
                    continue
                location = f"PDF p.{page_number} {paragraph_number}문단"
                if len(parts) > 1:
                    location = f"{location} {part_number}부분"
                location = f"{location} · {parsed.extractor}"
                fragment = {
                        "종류": OFFICIAL_IR_FRAGMENT_KIND,
                        "원문": kept,
                        "출처": source_url,
                        "문서ID": document_id,
                        "문서명": document_title,
                        "원문위치": location,
                        "후보출처검증": VERIFIED_FINAL_URL_VALUE,
                    }
                if attachment_url:
                    fragment[IR_ATTACHMENT_URL_FIELD] = attachment_url
                if published_at and reporting_period:
                    fragment.update(
                        {
                            "문서일": published_at,
                            IR_REPORTING_PERIOD_FIELD: reporting_period,
                            IR_METADATA_VERIFICATION_FIELD: (
                                IR_METADATA_VERIFICATION_VALUE
                            ),
                        }
                    )
                fragments.append(fragment)
                kept_chars = len(kept)
                extracted_chars += kept_chars
                page_chars += kept_chars
            if (
                page_chars >= MAX_IR_CHARS_PER_PAGE
                or extracted_chars >= document_limit
                or len(fragments) >= MAX_IR_FRAGMENTS_PER_DOCUMENT
            ):
                if paragraph_number < len(paragraphs):
                    truncated = True
                break
    if not fragments:
        raise OfficialIrFetchError(
            "공식 IR PDF의 모든 추출 문단이 최소 글자 상한보다 짧습니다"
        )
    return _ExtractedDocument(fragments=fragments, truncated=truncated)


def _kill_worker_process(process: subprocess.Popen[bytes]) -> None:
    """아직 실행 중인 워커만 강제 종료한다."""

    if process.poll() is not None:
        return
    try:
        process.kill()
    except OSError as exc:
        # 종료와 kill 사이의 경쟁만 정상이다. 여전히 살아 있으면 실패를 숨기지 않는다.
        if process.poll() is None:
            raise OSError("PDF 워커를 강제 종료하지 못했습니다") from exc


def _reap_worker_process(process: subprocess.Popen[bytes]) -> None:
    """강제 종료한 워커를 짧은 상한 안에서 회수한다."""

    try:
        process.wait(timeout=IR_PDF_WORKER_REAP_TIMEOUT_SEC)
    except subprocess.TimeoutExpired as exc:
        raise OSError("PDF 워커를 제한 시간 안에 회수하지 못했습니다") from exc


def _run_pdf_worker_bounded(
    command: list[str],
    content: bytes,
    *,
    timeout: float,
    max_output_bytes: int,
    creation_flags: int,
) -> subprocess.CompletedProcess[bytes]:
    """stdin 파일과 제한 reader로 워커 stdout을 상한 안에서만 받는다.

    ``communicate``와 ``subprocess.run(..., stdout=PIPE)``는 자식이 끝날 때까지
    stdout 전체를 메모리에 모은다. 입력은 임시 파일로 넘겨 pipe 교착을 없애고,
    reader는 상한보다 딱 한 바이트만 더 읽은 즉시 실행 중인 워커를 죽인다.
    """

    if max_output_bytes < 1:
        raise ValueError("PDF 워커 출력 상한은 양수여야 합니다")

    with tempfile.TemporaryDirectory(prefix="official-ir-worker-") as worker_dir:
        with tempfile.TemporaryFile(mode="w+b", dir=worker_dir) as input_file:
            input_file.write(content)
            input_file.flush()
            input_file.seek(0)
            process = subprocess.Popen(
                command,
                stdin=input_file,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                cwd=worker_dir,
                creationflags=creation_flags,
                env=_pdf_worker_environment(worker_dir),
                close_fds=True,
            )
            stdout_pipe = process.stdout
            if stdout_pipe is None:
                _kill_worker_process(process)
                _reap_worker_process(process)
                raise OSError("PDF 워커 stdout pipe를 만들지 못했습니다")

            output: list[bytes] = []
            reader_errors: list[Exception] = []

            def read_stdout() -> None:
                try:
                    value = stdout_pipe.read(max_output_bytes + 1)
                    if not isinstance(value, bytes):
                        raise TypeError("PDF 워커 stdout이 bytes가 아닙니다")
                    output.append(value)
                    if len(value) > max_output_bytes:
                        _kill_worker_process(process)
                except Exception as exc:  # noqa: BLE001 - thread 오류를 부모로 전달
                    reader_errors.append(exc)
                    _kill_worker_process(process)

            reader: threading.Thread | None = None
            try:
                reader = threading.Thread(
                    target=read_stdout,
                    name="official-ir-worker-stdout",
                    daemon=True,
                )
                try:
                    reader.start()
                except RuntimeError as exc:
                    raise OSError("PDF 워커 stdout reader를 시작하지 못했습니다") from exc
                timeout_error: subprocess.TimeoutExpired | None = None
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    timeout_error = exc
                    _kill_worker_process(process)
                    _reap_worker_process(process)

                # kill+wait 뒤에는 pipe writer가 닫혀 reader도 반드시 끝나야 한다.
                reader.join(timeout=IR_PDF_WORKER_REAP_TIMEOUT_SEC)
                if reader.is_alive():
                    raise OSError("PDF 워커 stdout reader를 회수하지 못했습니다")
                if timeout_error is not None:
                    raise timeout_error
                if reader_errors:
                    raise OSError("PDF 워커 stdout을 읽지 못했습니다") from reader_errors[0]
                return subprocess.CompletedProcess(
                    args=command,
                    returncode=int(process.returncode or 0),
                    stdout=output[0] if output else b"",
                    stderr=None,
                )
            finally:
                cleanup_error: OSError | None = None
                if process.poll() is None:
                    try:
                        _kill_worker_process(process)
                        _reap_worker_process(process)
                    except OSError as exc:
                        cleanup_error = exc
                # 살아 있는 자식이 pipe writer를 잡은 상태에서는 다른 스레드의
                # BufferedReader.close도 내부 락에서 멈출 수 있다. OS 상한이 자식을
                # 끝내면 daemon reader가 EOF로 빠져나오므로 여기서는 동기 close/join을
                # 하지 않고 부모 요청의 시간 상한을 지킨다.
                if process.poll() is not None and (
                    reader is None or not reader.is_alive()
                ):
                    if not stdout_pipe.closed:
                        stdout_pipe.close()
                if cleanup_error is not None:
                    raise cleanup_error


def _parse_pdf_with_timeout(content: bytes) -> _ParsedPdf:
    """PDF 파싱을 별도 프로세스에서 실행하고 10초 뒤 강제로 끝낸다."""

    worker = Path(__file__).with_name("_ir_pdf_worker.py")
    expected_version = IR_PDF_EXTRACTOR_VERSION.removeprefix("pypdf ")
    resource_policy = _pdf_worker_resource_policy()
    command = [
        sys.executable,
        "-I",
        "-B",
        str(worker),
        "--max-bytes",
        str(MAX_IR_PDF_BYTES),
        "--max-pages",
        str(MAX_IR_PDF_PAGES),
        "--max-raw-chars",
        str(MAX_IR_RAW_CHARS_PER_PAGE),
        "--max-root-recovery",
        str(MAX_IR_ROOT_RECOVERY_OBJECTS),
        "--max-stream-bytes",
        str(MAX_IR_DECOMPRESSED_STREAM_BYTES),
        "--max-address-space-bytes",
        str(MAX_IR_WORKER_ADDRESS_SPACE_BYTES),
        "--max-cpu-seconds",
        str(MAX_IR_WORKER_CPU_SECONDS),
        "--max-output-bytes",
        str(MAX_IR_WORKER_OUTPUT_BYTES),
        "--resource-policy",
        resource_policy,
        "--expected-version",
        expected_version,
    ]
    creation_flags = (
        getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    )
    if not _PDF_WORKER_SLOTS.acquire(timeout=IR_PDF_WORKER_SLOT_TIMEOUT_SEC):
        raise OfficialIrFetchError("공식 IR PDF 격리 파서 동시 실행 상한 대기 초과")
    try:
        try:
            completed = _run_pdf_worker_bounded(
                command,
                content,
                timeout=IR_PDF_PARSE_TIMEOUT_SEC,
                max_output_bytes=MAX_IR_WORKER_OUTPUT_BYTES,
                creation_flags=creation_flags,
            )
        except subprocess.TimeoutExpired as exc:
            raise OfficialIrFetchError("공식 IR PDF 파싱·추출 10초 상한 초과") from exc
        except (OSError, ValueError) as exc:
            raise OfficialIrFetchError("공식 IR PDF 격리 파서를 시작하지 못했습니다") from exc
    finally:
        _PDF_WORKER_SLOTS.release()

    # 출력 초과 때문에 부모가 kill한 경우를 OS 메모리 초과로 잘못 분류하지 않는다.
    if len(completed.stdout) > MAX_IR_WORKER_OUTPUT_BYTES:
        raise OfficialIrFetchError("공식 IR PDF 격리 파서 실행 실패")
    if _worker_exit_was_resource_limit(completed.returncode):
        raise OfficialIrFetchError("공식 IR PDF 워커 OS 자원 상한 초과")
    if completed.returncode != 0:
        raise OfficialIrFetchError("공식 IR PDF 격리 파서 실행 실패")
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OfficialIrFetchError("공식 IR PDF 격리 파서 응답 검증 실패") from exc
    if isinstance(payload, dict) and payload.get("state") == "failed":
        failure_kind = payload.get("failure_kind")
        if failure_kind == _RESOURCE_FAILURE_SETUP:
            raise OfficialIrFetchError("공식 IR PDF 워커 OS 자원 상한 설정 실패")
        if failure_kind == _RESOURCE_FAILURE_EXCEEDED:
            raise OfficialIrFetchError("공식 IR PDF 워커 OS 자원 상한 초과")
    if not isinstance(payload, dict) or payload.get("state") != "ok":
        raise OfficialIrFetchError("공식 IR PDF 파싱 또는 글자 추출 실패")

    pages = payload.get("pages")
    extractor = payload.get("extractor")
    truncated_pages = payload.get("truncated_pages")
    resource_limits = payload.get("resource_limits")
    allowed_resource_states = (
        {_RESOURCE_LIMITS_APPLIED}
        if resource_policy == _RESOURCE_POLICY_STRICT
        else {
            _RESOURCE_LIMITS_APPLIED,
            _RESOURCE_LIMITS_LOCAL_WINDOWS_FALLBACK,
        }
    )
    if (
        not isinstance(pages, list)
        or not 1 <= len(pages) <= MAX_IR_PDF_PAGES
        or any(
            not isinstance(page, str) or len(page) > MAX_IR_RAW_CHARS_PER_PAGE
            for page in pages
        )
        or extractor != IR_PDF_EXTRACTOR_VERSION
        or resource_limits not in allowed_resource_states
        or not isinstance(truncated_pages, list)
        or any(
            type(page_number) is not int
            or not 1 <= page_number <= len(pages)
            for page_number in truncated_pages
        )
    ):
        raise OfficialIrFetchError("공식 IR PDF 격리 파서 계약 검증 실패")
    return _ParsedPdf(
        pages=tuple(pages),
        extractor=extractor,
        truncated_pages=frozenset(truncated_pages),
    )


def _pdf_worker_resource_policy(
    *,
    platform_name: str | None = None,
    deployment_mode: str | None = None,
) -> str:
    """Render/Linux는 강제, Windows만 명시적 로컬 모드에서 fallback을 허용한다."""

    platform = platform_name or os.name
    configured = (
        os.environ.get(_WORKER_DEPLOYMENT_MODE_ENV, "")
        if deployment_mode is None
        else deployment_mode
    ).strip().casefold()
    if configured not in {"", "local", "production"}:
        raise OfficialIrFetchError("공식 IR PDF 워커 배포 모드 설정이 올바르지 않습니다")
    if platform == "nt" and configured == "local":
        return _RESOURCE_POLICY_LOCAL_WINDOWS
    return _RESOURCE_POLICY_STRICT


def _worker_exit_was_resource_limit(
    returncode: int,
    *,
    platform_name: str | None = None,
) -> bool:
    """OS가 CPU/메모리 hard limit로 끝낸 대표 종료코드를 분류한다."""

    platform = platform_name or os.name
    if platform == "posix":
        return returncode in {
            -getattr(signal, "SIGXCPU", 24),
            -getattr(signal, "SIGKILL", 9),
        }
    if platform == "nt":
        unsigned = returncode & 0xFFFFFFFF
        return unsigned in {
            0xC0000017,  # STATUS_NO_MEMORY
            0xC000009A,  # STATUS_INSUFFICIENT_RESOURCES
            0xC000012D,  # STATUS_COMMITMENT_LIMIT
        }
    return False


def _pdf_worker_environment(worker_dir: str) -> dict[str, str]:
    """격리 파서에 API 키를 넘기지 않는 최소 환경을 만든다."""

    environment = {
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
        "TEMP": worker_dir,
        "TMP": worker_dir,
    }
    for name in ("PATH", "SYSTEMROOT", "WINDIR"):
        value = os.environ.get(name)
        if value:
            environment[name] = value
    return environment


def _company_identity_terms(
    company_name: str,
    aliases: tuple[str, ...],
    *,
    homepage_hostname: str = "",
) -> _CompanyIdentityTerms:
    """DART 공식명과 충분히 식별적인 registry 별칭만 분리한다."""

    original_normalized_name = unicodedata.normalize(
        "NFKC", str(company_name or "")
    )
    normalized_name = original_normalized_name.casefold()
    original_name_words = re.findall(
        r"[^\W_]+", original_normalized_name, flags=re.UNICODE
    )
    name_words = re.findall(r"[^\W_]+", normalized_name, flags=re.UNICODE)
    official_token = _identity_token(company_name)
    principal: set[str] = set()
    if (
        official_token
        and official_token not in _GENERIC_IDENTITY_ALIASES
        and not (official_token.isascii() and len(official_token) <= 3)
    ):
        principal.add(official_token)
    legal_core = "".join(
        word for word in name_words if word not in _IDENTITY_LEGAL_WORDS
    )
    # ``주식회사 AI``의 legal core인 ``ai``처럼 짧거나 일반적인 토큰은
    # 같은 공식 host 안의 무관한 시장 자료에도 흔하다. 전체 공식명도 짧은
    # ASCII 일반어라면 버리고, 법적 접미사를 뺀 core는 독립 식별력이
    # 충분할 때만 보조 principal로 인정한다.
    if len(legal_core) >= 4 and legal_core not in _GENERIC_IDENTITY_ALIASES:
        principal.add(legal_core)

    # JYP의 실제 IR PDF는 공식명이 ``JYP Ent.``여도 앞쪽 표지에는 ``JYP``만
    # 글자로 추출된다. 짧은 영문 약자를 무조건 허용하면 SK·AI 같은 일반 글자와
    # 충돌하므로, DART가 확인한 공식 홈페이지 도메인의 브랜드 라벨과 0~2글자
    # 차이로 맞는 첫 단어일 때만 보조 principal로 인정한다.
    hostname_labels = [
        label
        for label in str(homepage_hostname or "").casefold().rstrip(".").split(".")
        if label
    ]
    public_suffix = ".".join(hostname_labels[-2:])
    brand_label_index = -3 if public_suffix in MULTI_LABEL_PUBLIC_SUFFIXES else -2
    brand_label = (
        hostname_labels[brand_label_index]
        if len(hostname_labels) >= abs(brand_label_index)
        else ""
    )
    original_leading_word = original_name_words[0] if original_name_words else ""
    leading_word = _identity_token(name_words[0]) if name_words else ""
    if (
        re.fullmatch(r"[A-Z]{3,5}", original_leading_word) is not None
        and leading_word.isascii()
        and leading_word not in _GENERIC_IDENTITY_ALIASES
        and (
            brand_label.startswith(leading_word)
            or leading_word.startswith(brand_label)
        )
        and abs(len(brand_label) - len(leading_word)) <= 2
    ):
        principal.add(leading_word)
    principal.discard("")

    distinctive_aliases: set[str] = set()
    for alias in aliases:
        normalized_alias = unicodedata.normalize(
            "NFKC", str(alias or "")
        ).casefold()
        alias_words = re.findall(
            r"[^\W_]+", normalized_alias, flags=re.UNICODE
        )
        for token in (
            _identity_token(alias),
            "".join(
                word
                for word in alias_words
                if word not in _IDENTITY_LEGAL_WORDS
            ),
        ):
            if len(token) >= 4 and token not in _GENERIC_IDENTITY_ALIASES:
                distinctive_aliases.add(token)
    return _CompanyIdentityTerms(
        principal=frozenset(principal),
        distinctive_aliases=frozenset(distinctive_aliases),
    )


def _identity_token(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _identity_matches(
    pages: tuple[str, ...],
    identity_terms: _CompanyIdentityTerms | frozenset[str],
) -> bool:
    """DART 공식명 또는 충분히 식별적인 공식 별칭만 인정한다."""

    normalized = unicodedata.normalize("NFKC", " ".join(pages)).casefold()
    compact = _identity_token(normalized)
    words = set(re.findall(r"[^\W_]+", normalized, flags=re.UNICODE))

    def term_matches(term: str) -> bool:
        if term.isascii() and len(term) <= 3:
            return term in words
        return term in compact

    if isinstance(identity_terms, _CompanyIdentityTerms):
        return bool(
            any(term_matches(term) for term in identity_terms.principal)
            or any(
                term_matches(term) for term in identity_terms.distinctive_aliases
            )
        )
    # 내부 parser 단위시험의 명시적 legacy 입력만 유지한다. 실제 수집 경로는
    # 위 구조화 계약을 거쳐 임의 사용자 별칭을 받을 수 없다.
    return any(term_matches(term) for term in identity_terms)


def _paragraphs(raw_text: str) -> list[str]:
    """PDF 파서가 준 빈 줄 경계를 유지한 채 각 문단 안 공백만 정리한다."""

    normalized = str(raw_text or "").replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n[ \t]*\n+", normalized)
    paragraphs: list[str] = []
    for block in blocks:
        lines = [_clean_inline(line) for line in block.split("\n")]
        paragraph = " ".join(line for line in lines if line).strip()
        if paragraph:
            paragraphs.append(paragraph)
    return paragraphs


def _bounded_paragraph_parts(paragraph: str) -> list[str]:
    """한 문단을 다른 문단과 섞지 않고 글자 상한 안의 연속 부분으로 나눈다."""

    remaining = paragraph.strip()
    parts: list[str] = []
    while remaining:
        if len(remaining) <= MAX_IR_CHARS_PER_FRAGMENT:
            parts.append(remaining)
            break
        split_at = remaining.rfind(" ", 0, MAX_IR_CHARS_PER_FRAGMENT + 1)
        if split_at < MIN_IR_FRAGMENT_CHARS:
            split_at = MAX_IR_CHARS_PER_FRAGMENT
        parts.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return [part for part in parts if part]


def _read_limited_pdf(
    response: object,
    *,
    timeout: float,
    max_bytes: int,
    clock: Callable[[], float] = time.monotonic,
) -> bytes:
    """MIME·바이트·전체 경과시간 상한 안에서 PDF 응답을 읽는다."""

    if max_bytes < 1 or max_bytes > MAX_IR_PDF_BYTES:
        raise ValueError("max_bytes는 PDF 파일 상한 안의 양수여야 합니다")
    if _response_content_type(response) != PDF_CONTENT_TYPE:
        raise HomepageResponseError("공식 IR 응답 MIME이 application/pdf가 아닙니다")
    content_length = _response_header(response, "Content-Length")
    if content_length:
        try:
            declared = int(content_length)
        except (TypeError, ValueError) as exc:
            raise HomepageResponseError("공식 IR Content-Length가 올바르지 않습니다") from exc
        if declared < 0 or declared > max_bytes:
            raise HomepageResponseError("공식 IR PDF 응답이 너무 큽니다")

    reader = getattr(response, "read1", None)
    if not callable(reader):
        reader = getattr(response, "read", None)
    if not callable(reader):
        raise HomepageResponseError("공식 IR PDF 응답을 읽을 수 없습니다")

    deadline, effective_clock = response_deadline(
        response,
        timeout=timeout,
        clock=clock,
    )
    body = bytearray()
    while True:
        remaining_seconds = deadline - effective_clock()
        if remaining_seconds <= 0:
            raise HomepageResponseError("공식 IR PDF 응답 시간이 초과됐습니다")
        _set_socket_timeout(response, remaining_seconds)
        requested = min(
            READ_CHUNK_BYTES,
            max_bytes + 1 - len(body),
        )
        try:
            chunk = reader(requested)
        except (TimeoutError, OSError, http.client.HTTPException) as exc:
            raise HomepageResponseError("공식 IR PDF 응답을 읽지 못했습니다") from exc
        if effective_clock() >= deadline:
            raise HomepageResponseError("공식 IR PDF 응답 시간이 초과됐습니다")
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise HomepageResponseError("공식 IR PDF 응답 데이터가 올바르지 않습니다")
        if not chunk:
            content = bytes(body)
            if not content.startswith(b"%PDF-"):
                raise HomepageResponseError("공식 IR 응답에 PDF 매직 바이트가 없습니다")
            return content
        body.extend(chunk)
        if len(body) > max_bytes:
            raise HomepageResponseError("공식 IR PDF 응답이 너무 큽니다")


def _response_header(response: object, name: str) -> str:
    headers = getattr(response, "headers", None)
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return ""
    value = getter(name)
    return str(value).strip() if value is not None else ""


def _response_content_type(response: object) -> str:
    return _response_header(response, "Content-Type").split(";", 1)[0].strip().casefold()


def _set_socket_timeout(response: object, seconds: float) -> None:
    fp = getattr(response, "fp", None)
    raw = getattr(fp, "raw", None)
    socket_object = getattr(raw, "_sock", None)
    setter = getattr(socket_object, "settimeout", None)
    if not callable(setter):
        return
    try:
        setter(seconds)
    except (OSError, TypeError, ValueError):
        return


def _clean_inline(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()
