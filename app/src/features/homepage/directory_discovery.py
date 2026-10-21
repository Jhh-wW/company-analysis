"""DART URL이 없는 회사의 기관 게시 링크를 제한된 공개 GET으로 발견한다."""
from __future__ import annotations

import http.client
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from collections.abc import Callable
from dataclasses import dataclass

from src.features.homepage.constants import DIRECTORY_DISCOVERY_MAX_REQUESTS, DIRECTORY_DISCOVERY_TIMEOUT_SEC, USER_AGENT
from src.features.homepage.safe_http import (
    HomepageResponseError, UnsafeHomepageUrlError, read_limited_body,
    request_deadline_scope, safe_urlopen_exact_https_host,
)
from src.shared.report_evidence.institution_directory_attestation import (
    build_institution_directory_provenance, directory_search_record, directory_search_url,
    parse_institution_directory_provenance,
)
from src.shared.report_evidence.institution_directory_constants import DIRECTORY_MAX_PAGE_BYTES, DIRECTORY_ORIGIN


@dataclass(frozen=True)
class DirectoryPage:
    status: int
    url: str
    body: bytes
    content_type: str = "text/html"


DirectoryFetch = Callable[[str], DirectoryPage]


@dataclass(frozen=True)
class DirectoryDiscovery:
    candidates: tuple[tuple[str, str], ...] = ()
    state: str = "MISSING"
    reason_code: str = "institution_directory_no_exact_record"
    bytes_downloaded: int = 0
    requests: int = 0


def fetch_directory_page(url: str) -> DirectoryPage:
    """기관의 exact 요청 URL만 연다. 다른 경로·query로의 redirect도 거절한다."""
    host = urllib.parse.urlsplit(DIRECTORY_ORIGIN).hostname or ""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with safe_urlopen_exact_https_host(request, timeout=DIRECTORY_DISCOVERY_TIMEOUT_SEC,
                expected_hostname=host, url_allowed=lambda target: target == url) as response:
            body = read_limited_body(response, timeout=DIRECTORY_DISCOVERY_TIMEOUT_SEC)
            if len(body) > DIRECTORY_MAX_PAGE_BYTES:
                raise HomepageResponseError("기관 원문 바이트 상한을 넘었습니다")
            return DirectoryPage(response.status, response.geturl(), body, response.headers.get_content_type())
    except urllib.error.HTTPError as error:
        return DirectoryPage(error.code, error.geturl(), b"", "")


def discover_institution_homepage(*, company_id: str, company_name: str, collected_at: str,
                                  fetch: DirectoryFetch = fetch_directory_page) -> DirectoryDiscovery:
    """검색 원문에서 발견한 유일한 상세 레코드만 읽고 후보 영수증을 반환한다."""
    byte_count = request_count = 0

    def read(url: str) -> DirectoryPage:
        nonlocal byte_count, request_count
        if request_count >= DIRECTORY_DISCOVERY_MAX_REQUESTS:
            raise ValueError("기관 조회 횟수 상한을 넘었습니다")
        request_count += 1
        page = fetch(url)
        if page.url != url or type(page.body) is not bytes or len(page.body) > DIRECTORY_MAX_PAGE_BYTES:
            raise ValueError("기관 응답 URL·원문 크기·형식이 다릅니다")
        byte_count += len(page.body)
        return page

    def result(state: str, reason: str, candidates=()) -> DirectoryDiscovery:
        return DirectoryDiscovery(candidates, state, reason, byte_count, request_count)

    try:
        with request_deadline_scope(DIRECTORY_DISCOVERY_TIMEOUT_SEC):
            search_url = directory_search_url(company_name)
            robots = read(DIRECTORY_ORIGIN + "/robots.txt")
            # 선택적 새 발견 경로는 robots를 확인하지 못하면 추정해서 열지 않는다.
            if robots.status != 200 or robots.content_type not in {"text/plain", "text/html"}:
                return result("FAILED", "institution_directory_robots_unavailable")
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(robots.body.decode("utf-8").splitlines())
            if not parser.can_fetch(USER_AGENT, search_url):
                return result("MISSING", "institution_directory_robots_disallowed")
            search = read(search_url)
            if search.status != 200 or search.content_type not in {"text/html", "application/xhtml+xml"}:
                return result("FAILED", "institution_directory_search_failed")
            record_url, _location, _row_hash = directory_search_record(search.body, company_name=company_name)
            if not record_url:
                return result("MISSING", "institution_directory_no_exact_record")
            if not parser.can_fetch(USER_AGENT, record_url):
                return result("MISSING", "institution_directory_robots_disallowed")
            record = read(record_url)
            if record.status != 200 or record.content_type not in {"text/html", "application/xhtml+xml"}:
                return result("FAILED", "institution_directory_record_failed")
            proof = build_institution_directory_provenance(company_id=company_id, company_name=company_name,
                search_url=search_url, search_body=search.body, record_url=record_url,
                record_body=record.body, fetched_on=collected_at[:10])
            parsed = parse_institution_directory_provenance(proof, reference_date=collected_at)
            if parsed is None:
                return result("FAILED", "institution_directory_proof_invalid")
            return result("OK", "institution_directory_candidate_found", ((parsed.url, proof),))
    except (ValueError, TypeError, UnicodeError):
        return result("FAILED", "institution_directory_record_ambiguous_or_invalid")
    except (HomepageResponseError, UnsafeHomepageUrlError, urllib.error.URLError,
            OSError, TimeoutError, http.client.HTTPException):
        return result("FAILED", "institution_directory_network_failed")
