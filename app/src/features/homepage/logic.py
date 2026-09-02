"""회사 홈페이지에서 「뭘 잘하나」 재료(조각)를 모은다.

이 파일이 다루는 것 — 2번 소스(회사 홈페이지)다.
「없다」와 「못 가져옴」은 다르다.

★ 네트워크 호출은 전부 `fetch` 인자로 주입받는다 (`Fetcher` 타입).
  기본값은 실제로 접속하는 `default_fetch`이지만, 시험에서는 가짜 함수를 넣어
  진짜 접속 없이 검증한다.
"""

from __future__ import annotations

import html
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import date
from html.parser import HTMLParser
from typing import Callable, Optional
from urllib import robotparser

from src.features.homepage.constants import (
    BRAND_PATH_EXCLUDED_TOKENS,
    BRAND_PATH_MAX_PREFIX_GAP,
    BRAND_PATH_MIN_TOKEN_CHARS,
    EXCLUDED_EXTENSIONS,
    FRAGMENT_KIND,
    HOMEPAGE_COLLECTION_TIMEOUT_SEC,
    HOSTNAME_MISMATCH_MARKER,
    MAX_CHARS_PER_PAGE,
    MAX_PAGES,
    MAX_TOTAL_CHARS,
    MIN_FRAGMENT_CHARS,
    MULTI_LABEL_PUBLIC_SUFFIXES,
    PRIORITY_PATH_KEYWORDS,
    SINGLE_LABEL_PUBLIC_SUFFIXES,
    TIMEOUT_SEC,
    USER_AGENT,
)
from src.features.homepage.robots_cache import (
    RobotsDecision,
    cached_robots_decision,
    robots_cache_key,
)
from src.features.homepage.safe_http import (
    HomepageResponseError,
    UnsafeHomepageUrlError,
    read_limited_text,
    request_deadline_scope,
    safe_urlopen,
)
from src.shared.official_ir import (
    IR_DART_WWW_REDIRECT_FIELD,
    IR_DART_WWW_REDIRECT_FROM_FIELD,
    IR_DART_WWW_REDIRECT_TO_FIELD,
    IR_DART_WWW_REDIRECT_VALUE,
    dart_homepage_exact_host,
    dart_homepage_www_alias_url,
)

#: <script>·<style>·<noscript> 안의 글자는 사람이 읽는 본문이 아니므로 뺀다.
_SKIP_TAGS = frozenset({"script", "style", "noscript"})
_BLOCK_TAGS = frozenset(
    {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "dd",
        "div",
        "dl",
        "dt",
        "figcaption",
        "figure",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "nav",
        "ol",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
        "ul",
    }
)


class HomepageFetchError(Exception):
    """홈페이지 접속 실패 — 시간초과·연결거부·HTTP 오류를 이 하나로 통일해서 던진다."""


class HomepageRobotsUnavailable(HomepageFetchError):
    """robots.txt가 HTTP 4xx로 명시적으로 존재하지 않거나 이용 불가하다."""


class HomepageRobotsUnreachable(HomepageFetchError):
    """robots.txt가 서버·네트워크 오류로 확인 불가하여 전체 금지로 봐야 한다."""


class HomepageSecurityPolicyError(HomepageFetchError):
    """SSRF·리다이렉트·응답 검증 정책 위반이라 HTTP downgrade하면 안 된다."""


class HomepageCertNameMismatchError(HomepageFetchError):
    """SSL 인증서에 적힌 이름이 접속 주소와 다를 때(hostname mismatch) 던진다.

    ★ C안(문제로그 P-46)의 시작점. 이 예외를 받으면 인증서의 진짜 이름이
      「같은 회사」로 보일 때만 검증을 켠 채 그 이름으로 1번 재시도한다
      (`_attempt_cert_fallback` 참조). 만료·자체서명 등 다른 인증서 오류는
      이 예외가 아니라 `HomepageFetchError`로 오므로 자동으로 재시도 대상에서
      빠진다.

    Attributes:
        host: 원래 접속하려던 호스트 이름(포트 제외, 소문자로 정규화하지 않음 —
            비교는 `_registrable_core_name`이 알아서 소문자로 맞춘다).
    """

    def __init__(self, message: str, *, host: str) -> None:
        super().__init__(message)
        self.host = host


@dataclass(frozen=True)
class FetchedPage:
    """본문과 안전 클라이언트가 실제로 도착한 최종 URL."""

    html: str
    effective_url: str


#: 시험 대역의 기존 문자열 반환도 일반 수집에는 허용한다. 다만 실제 최종 URL을
#: 증명하지 못하므로 그런 페이지는 경쟁 후보 공식 원문으로 승격되지 않는다.
Fetcher = Callable[[str], str | FetchedPage]

#: 인증서 이름 불일치가 났을 때, 그 서버 인증서에 적힌 유효 이름(SAN·CN) 목록을
#: 돌려주는 함수. ★ 인증서를 «들여다보는» 용도로만 쓴다 — 본문은 절대 읽지 않는다.
#: 조회에 실패해도 예외를 던지지 않고 빈 리스트를 돌려준다는 약속이다
#: (실패 = 「못 알아냄」 = 재시도 포기, `default_lookup_cert_names` 참조).
CertNameLookup = Callable[[str], list[str]]
DartWwwRedirectProbe = Callable[[str, str], str]


@dataclass(frozen=True)
class HomepageCollectResult:
    """홈페이지 수집 결과 하나.

    ★ 「자료 없음」과 「못 가져옴」을 반드시 구분한다 (정본 §「없다」와 「못 가져옴」은 다르다).
      state가 "failed"인 결과는 캐시하면 안 된다 — 그날만 사이트가 죽었을 수 있다.
    """

    #: "ok"(조각을 찾음) | "none"(접속은 됐는데 쓸 글자가 없음) | "failed"(접속 자체가 실패)
    state: str
    #: state가 "ok"일 때만 채워진다. 모양: {"종류": "홈페이지", "원문": str, "출처": url}
    fragments: list[dict[str, str]] = field(default_factory=list)
    #: 사람이 읽는 사유 한 줄 (실패·없음일 때). ok일 때도, 인증서 이름 불일치를
    #: 우회해 원래 주소가 아닌 다른 주소로 접속했다면 그 사실이 여기 남는다
    #: (C안 — 어디로 갔는지 사용자가 알 수 있어야 한다).
    detail: str = ""
    #: 경쟁 후보를 찾는 데 허용된 HTTPS 원문 범위를 끝까지 확인했는가.
    #: 페이지 실패·robots 차단·페이지/글자 상한·HTTP/대체 host가 하나라도 있으면
    #: ``False``다. 이 값이 거짓인 결과를 "경쟁사 언급 없음"으로 확정하면 안 된다.
    candidate_scope_complete: bool = True


def default_fetch(
    url: str,
    *,
    url_allowed: Callable[[str], bool] | None = None,
) -> FetchedPage:
    """실제로 접속하는 기본 구현. urllib만 쓴다 (`naver_client.py`와 같은 방식).

    Args:
        url: 가져올 주소.

    Returns:
        디코딩한 HTML 원문.

    Raises:
        HomepageCertNameMismatchError: SSL 인증서의 이름이 주소와 다를 때
            (그 외 인증서 오류는 아래 `HomepageFetchError`로 던진다).
        HomepageFetchError: 연결 실패·시간초과·HTTP 오류(4xx/5xx) 전부 이걸로 던진다.
    """
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with safe_urlopen(
            req,
            timeout=TIMEOUT_SEC,
            url_allowed=url_allowed,
        ) as res:
            effective_url = str(getattr(res, "geturl", lambda: url)() or "").strip()
            if not effective_url:
                raise HomepageResponseError("홈페이지 최종 URL을 확인하지 못했습니다")
            if not _same_origin(url, effective_url):
                raise HomepageResponseError(
                    "홈페이지 리다이렉트가 최초 origin을 벗어났습니다"
                )
            return FetchedPage(
                html=read_limited_text(res, timeout=TIMEOUT_SEC),
                effective_url=effective_url,
            )
    except urllib.error.HTTPError as exc:
        if _is_robots_url(url):
            if 400 <= int(exc.code) <= 499:
                raise HomepageRobotsUnavailable(f"HTTP {exc.code}") from exc
            raise HomepageRobotsUnreachable(f"HTTP {exc.code}") from exc
        raise HomepageFetchError(f"{type(exc).__name__}: {exc}") from exc
    except (UnsafeHomepageUrlError, HomepageResponseError) as exc:
        if _is_robots_url(url):
            raise HomepageRobotsUnreachable(
                f"{type(exc).__name__}: {exc}"
            ) from exc
        raise HomepageSecurityPolicyError(
            f"{type(exc).__name__}: {exc}"
        ) from exc
    except urllib.error.URLError as exc:
        if _is_hostname_mismatch(exc):
            host = urllib.parse.urlparse(url).hostname or ""
            raise HomepageCertNameMismatchError(
                f"인증서 이름 불일치: {exc.reason}", host=host
            ) from exc
        if _is_robots_url(url):
            raise HomepageRobotsUnreachable(
                f"{type(exc).__name__}: {exc}"
            ) from exc
        raise HomepageFetchError(f"{type(exc).__name__}: {exc}") from exc
    except (TimeoutError, OSError) as exc:
        if _is_robots_url(url):
            raise HomepageRobotsUnreachable(
                f"{type(exc).__name__}: {exc}"
            ) from exc
        raise HomepageFetchError(f"{type(exc).__name__}: {exc}") from exc


def default_lookup_cert_names(url: str) -> list[str]:
    """인증서 대체-host 자동 탐색은 공식 후보 수집에서 사용하지 않는다.

    hostname 검증을 끈 별도 TLS probe는 DNS·연결·handshake 마감을 다시 만들고
    대체 host의 법인 결속도 약하다. 기본 운영 경로는 빈 목록으로 fail-closed하며,
    명시적으로 주입한 시험/관리자 정책만 기존 fallback 흐름을 검증할 수 있다.
    """

    del url
    return []


def strip_html(raw_html: str) -> str:
    """HTML 태그를 지우고 사람이 읽는 글자만 남긴다.

    Args:
        raw_html: 원본 HTML.

    Returns:
        태그·스크립트·엔티티(&amp; 등)를 정리한 한 줄짜리 본문 글자.
    """
    parser = _TextExtractor()
    parser.feed(raw_html)
    text = html.unescape(parser.get_text())
    lines = [
        re.sub(r"[^\S\r\n]+", " ", line).strip()
        for line in text.splitlines()
    ]
    return "\n".join(line for line in lines if line)


def collect_homepage_fragments(
    homepage_url: str,
    fetch: Fetcher = default_fetch,
    lookup_cert_names: CertNameLookup = default_lookup_cert_names,
    *,
    allow_dart_www_alias: bool = False,
    www_redirect_probe: DartWwwRedirectProbe | None = None,
) -> HomepageCollectResult:
    """홈페이지 수집 전체를 하나의 절대시간·DNS cache 경계에서 실행한다.

    ``allow_dart_www_alias``는 DART 기업개황을 그대로 받은 운영 파이프라인만
    켠다. apex 수집이 성공하지 못했고 실제 HTTPS apex→정확한 ``www`` 이동을
    IR 수집기와 같은 probe가 증명한 경우에만 ``www``에서 딱 한 번 재수집한다.
    """

    try:
        with request_deadline_scope(HOMEPAGE_COLLECTION_TIMEOUT_SEC) as deadline:
            result = _collect_homepage_fragments_impl(
                homepage_url,
                fetch=fetch,
                lookup_cert_names=lookup_cert_names,
            )
            alias_url = (
                dart_homepage_www_alias_url(homepage_url)
                if allow_dart_www_alias and result.state != "ok"
                else ""
            )
            verified_alias_url = ""
            if alias_url:
                probe = www_redirect_probe
                if probe is None:
                    # IR 수집기와 같은 네트워크 probe 계약을 재사용한다. 지연 import로
                    # 일반 홈페이지 모듈을 불러올 때 PDF 수집기까지 초기화하지 않는다.
                    from src.features.homepage.ir_pdf import (
                        default_dart_www_redirect_probe,
                    )

                    probe = default_dart_www_redirect_probe
                verified_alias_url = probe(homepage_url, alias_url)
            if alias_url and verified_alias_url == alias_url:
                alias_result = _collect_homepage_fragments_impl(
                    verified_alias_url,
                    fetch=fetch,
                    lookup_cert_names=lookup_cert_names,
                )
                if alias_result.state == "ok" or (
                    result.state == "failed" and alias_result.state != "failed"
                ):
                    apex_host = dart_homepage_exact_host(homepage_url)
                    alias_host = (
                        urllib.parse.urlsplit(verified_alias_url).hostname or ""
                    ).casefold().rstrip(".")
                    alias_note = "DART apex의 검증된 www 별칭에서 홈페이지 확인"
                    result = replace(
                        alias_result,
                        fragments=[
                            {
                                **fragment,
                                IR_DART_WWW_REDIRECT_FIELD: (
                                    IR_DART_WWW_REDIRECT_VALUE
                                ),
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
        return HomepageCollectResult(
            state="failed",
            detail=f"홈페이지 수집 전체시간 초과: {exc}",
            candidate_scope_complete=False,
        )


def _collect_homepage_fragments_impl(
    homepage_url: str,
    fetch: Fetcher = default_fetch,
    lookup_cert_names: CertNameLookup = default_lookup_cert_names,
) -> HomepageCollectResult:
    """홈페이지 주소 하나에서 조각 목록을 모은다.

    회사·기술 소개 페이지(`PRIORITY_PATH_KEYWORDS`)를 우선해서 읽고,
    robots.txt로 금지된 경로는 건너뛰며, 페이지 수·글자 수 상한을 지킨다.

    ★ 인증서 이름 불일치(C안, 문제로그 P-46): 루트 접속에서
      `HomepageCertNameMismatchError`가 나면, 인증서에 적힌 이름 중 원래
      주소와 «같은 회사」로 보이는 것이 있을 때만 그 이름으로 검증을 켠 채
      1번 재시도한다 (`_attempt_cert_fallback`). 다르면 그대로 실패로 남긴다.

    Args:
        homepage_url: 전자공시 기업개황 API가 주는 `hm_url`.
        fetch: 주소를 받아 HTML을 돌려주는 함수. 실패하면 `HomepageFetchError`
            (이름 불일치면 그 서브클래스인 `HomepageCertNameMismatchError`)를
            던져야 한다. 시험에서는 가짜로 바꿔 끼운다.
        lookup_cert_names: 인증서 이름 불일치가 났을 때만 불리는, 인증서의
            SAN·CN 이름을 읽어오는 함수. 시험에서는 가짜로 바꿔 끼운다.

    Returns:
        수집 결과. 루트 페이지 접속 자체가 실패하면 state="failed",
        접속은 됐는데 쓸 글자가 하나도 없으면 state="none". 인증서 이름
        불일치를 우회해 다른 주소로 접속했다면 `detail`에 원래 주소와
        실제로 읽은 주소를 함께 남긴다(성공·실패와 무관하게).
    """
    url = _normalize_url(homepage_url)
    if not url:
        return HomepageCollectResult(state="none", detail="홈페이지 주소 없음")

    origin_note = ""
    candidate_scope_complete = True
    try:
        root_html, final_url, root_url_verified, robots = _load_origin_root(
            url,
            fetch,
        )
    except HomepageCertNameMismatchError as exc:
        fallback = _attempt_cert_fallback(url, exc, fetch, lookup_cert_names)
        if fallback is None:
            return HomepageCollectResult(
                state="failed",
                detail=f"홈페이지 접속 실패(인증서 이름 불일치, 재시도 대상 없음): {exc}",
                candidate_scope_complete=False,
            )
        final_url, root_html, root_url_verified, robots = fallback
        origin_note = f"원래 주소({url})의 인증서 이름이 달라 {final_url} 로 재시도해 접속함"
        candidate_scope_complete = False
    except HomepageRobotsUnreachable as exc:
        return HomepageCollectResult(
            state="failed",
            detail=f"홈페이지 robots.txt 확인 실패: {exc}",
            candidate_scope_complete=False,
        )
    except HomepageSecurityPolicyError as exc:
        return HomepageCollectResult(
            state="failed",
            detail=f"홈페이지 안전 정책 위반: {exc}",
            candidate_scope_complete=False,
        )
    except HomepageFetchError as exc:
        # https가 아예 안 되는 사이트가 있다 (국내 중소기업 홈페이지에 흔하다).
        # ★ 암호화 없이 읽게 되므로 «그 사실을 반드시 남긴다». 조용히 내려가지 않는다.
        plain = _http_variant(url)
        if not plain:
            return HomepageCollectResult(
                state="failed",
                detail=f"홈페이지 접속 실패: {exc}",
                candidate_scope_complete=False,
            )
        try:
            root_html, final_url, root_url_verified, robots = _load_origin_root(
                plain,
                fetch,
            )
        except HomepageFetchError as plain_exc:
            return HomepageCollectResult(
                state="failed",
                detail=f"홈페이지 접속 실패: {plain_exc}",
                candidate_scope_complete=False,
            )
        origin_note = (
            f"{url} 는 접속되지 않아 {plain} (암호화되지 않은 연결)로 읽었습니다"
        )
        candidate_scope_complete = False

    parsed_root = urllib.parse.urlparse(final_url)
    # 본문보다 먼저 확인한 같은 origin의 규칙을 경로마다 재접속하지 않고 재사용한다.
    fragments: list[dict[str, str]] = []
    seen_text: set[str] = set()
    seen_urls: set[str] = {final_url}
    total_chars = 0
    pages_fetched = 1  # 루트 페이지도 1페이지로 센다

    if not root_url_verified:
        candidate_scope_complete = False
    if robots.can_fetch(USER_AGENT, final_url):
        candidate_scope_complete = (
            candidate_scope_complete
            and _page_candidate_scope_is_complete(root_html, total_chars)
        )
        total_chars = _collect_page(
            final_url,
            root_html,
            fragments,
            seen_text,
            total_chars,
            final_url_verified=root_url_verified,
        )
    else:
        candidate_scope_complete = False

    # 우선순위 큐를 쓰는 bounded depth 탐색. 예전에는 루트 링크만 한 번 읽어서
    # ``홈 → IR 자료실 → 최신 분기 상세``의 두 번째 링크를 영원히 못 봤다.
    # 매 페이지에서 같은 도메인 링크를 더하되 기존 6쪽/글자 상한은 그대로다.
    candidates = sorted(
        _extract_links(root_html, final_url, parsed_root.netloc),
        key=lambda candidate: (_link_priority(candidate), candidate),
    )
    queued_urls = set(candidates)
    while candidates:
        if pages_fetched >= MAX_PAGES or total_chars >= MAX_TOTAL_CHARS:
            candidate_scope_complete = False
            break  # 상한 도달 — 더 읽지 않는다 (무한 크롤링 금지)
        link = candidates.pop(0)
        queued_urls.discard(link)
        if link in seen_urls:
            continue
        seen_urls.add(link)
        if not robots.can_fetch(USER_AGENT, link):
            candidate_scope_complete = False
            continue
        pages_fetched += 1  # 실패해도 시도 자체는 상한에 넣는다
        try:
            page_html, effective_link, link_verified = _fetch_page(
                fetch,
                link,
                url_allowed=lambda candidate, source=link: (
                    _same_origin(source, candidate)
                    and robots.can_fetch(USER_AGENT, candidate)
                ),
            )
        except HomepageFetchError:
            candidate_scope_complete = False
            continue  # 낱장 페이지 실패는 건너뛴다 — 루트는 이미 접속됐으니 전체 실패가 아니다
        candidate_scope_complete = (
            candidate_scope_complete
            and link_verified
            and _page_candidate_scope_is_complete(page_html, total_chars)
        )
        total_chars = _collect_page(
            effective_link,
            page_html,
            fragments,
            seen_text,
            total_chars,
            final_url_verified=link_verified,
        )
        for discovered in _extract_links(page_html, effective_link, parsed_root.netloc):
            if discovered in seen_urls or discovered in queued_urls:
                continue
            candidates.append(discovered)
            queued_urls.add(discovered)
        candidates.sort(key=lambda candidate: (_link_priority(candidate), candidate))

    if not fragments:
        detail = "홈페이지에서 쓸 만한 글자를 찾지 못함"
        if origin_note:
            detail = f"{origin_note}; {detail}"
        return HomepageCollectResult(
            state="none",
            detail=detail,
            candidate_scope_complete=candidate_scope_complete,
        )
    return HomepageCollectResult(
        state="ok",
        fragments=fragments,
        detail=origin_note,
        candidate_scope_complete=candidate_scope_complete,
    )


# ══════════════════════════════════════════════════════════
# 안쪽 도우미 — 밖에서 부르지 않는다
# ══════════════════════════════════════════════════════════


class _TextExtractor(HTMLParser):
    """<script>·<style>·<noscript>를 건너뛰고 화면에 보이는 글자만 모은다."""

    def __init__(self) -> None:
        super().__init__()
        self._skip_depth = 0
        self._chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        elif self._skip_depth == 0 and tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        elif self._skip_depth == 0 and tag in _BLOCK_TAGS:
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._chunks.append(data.strip())

    def get_text(self) -> str:
        return "".join(
            chunk if chunk == "\n" else f"{chunk} " for chunk in self._chunks
        )


class _LinkExtractor(HTMLParser):
    """<a href="…"> 값만 모은다."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.hrefs.append(href)


_PUBLISHED_DATE_META_KEYS = frozenset(
    {
        "article:published_time",
        "datepublished",
        "date_published",
        "publishdate",
        "publish_date",
        "published",
    }
)
_ISO_DATE_PREFIX = re.compile(r"^\s*(20\d{2}-\d{2}-\d{2})(?:[Tt ]|$)")


class _PublishedDateExtractor(HTMLParser):
    """명시적 machine-readable 발행일만 모은다."""

    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, Optional[str]]],
    ) -> None:
        values = {str(key).casefold(): str(value or "") for key, value in attrs}
        markers = {
            values.get(key, "").strip().casefold()
            for key in ("property", "name", "itemprop")
            if values.get(key, "").strip()
        }
        raw = ""
        if tag.casefold() == "meta" and markers.intersection(
            _PUBLISHED_DATE_META_KEYS
        ):
            raw = values.get("content", "")
        elif (
            tag.casefold() == "time"
            and "datepublished" in markers
        ):
            raw = values.get("datetime", "")
        match = _ISO_DATE_PREFIX.match(raw)
        if match is None:
            return
        candidate = match.group(1)
        try:
            date.fromisoformat(candidate)
        except ValueError:
            return
        self.values.append(candidate)


def _published_date_from_html(raw_html: str) -> str:
    """서로 충돌하지 않는 단일 ISO 발행일만 반환한다."""

    parser = _PublishedDateExtractor()
    parser.feed(raw_html)
    unique = tuple(dict.fromkeys(parser.values))
    return unique[0] if len(unique) == 1 else ""


def _normalize_url(raw: str) -> str:
    """`hm_url`이 스킴 없이 올 때(예: "www.foo.com")를 대비해 채워 넣는다.

    ★ **`https://`를 먼저 붙인다.** 전자공시의 `hm_url`은 대부분 스킴이 없다.
      `http://`를 붙이면 **암호화 없이** 읽게 되고, 그러면 중간에서 내용을
      바꿔치기해도 알 수 없다 — 그 거짓이 그대로 보고서에 「사실」로 들어간다.
      (문제로그 P-52)
    """
    candidate = raw.strip()
    if not candidate:
        return ""
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    try:
        parsed = urllib.parse.urlsplit(candidate)
    except ValueError:
        return ""
    if parsed.scheme and parsed.scheme.lower() not in ("http", "https"):
        return ""
    if not re.match(r"^https?://", candidate, re.IGNORECASE):
        candidate = f"https://{candidate}"
    return candidate


def _http_variant(url: str) -> str:
    """같은 주소의 «암호화 없는» 형태. https가 아예 안 될 때만 쓴다."""
    return "http://" + url[len("https://"):] if url.startswith("https://") else ""


def _is_hostname_mismatch(exc: urllib.error.URLError) -> bool:
    """SSL 인증서 오류 중 «이름이 다름»(hostname mismatch)만 골라낸다.

    ★ 만료·자체서명 같은 다른 인증서 오류는 `ssl.SSLCertVerificationError`로
      똑같이 오지만 메시지에 `HOSTNAME_MISMATCH_MARKER` 글자가 없으므로
      여기서 걸러진다 — 그런 오류는 따라가지 않는다(지시 3번).
    """
    reason = getattr(exc, "reason", None)
    if not isinstance(reason, ssl.SSLCertVerificationError):
        return False
    return HOSTNAME_MISMATCH_MARKER in str(reason).lower()


def _registrable_core_name(host: str) -> str:
    """호스트 이름에서 공개 접미사(.co.kr·.com 등)를 떼고 핵심 이름 한 칸만 남긴다.

    「공개 접미사 바로 앞 칸」이 실제 등록된 회사 도메인이라는 성질을 이용한다.
    `www.` 같은 흔한 하위 도메인 접두사나 인증서의 와일드카드(`*.`)는 공개
    접미사보다 앞쪽(더 왼쪽)에 있으므로, 항상 «접미사를 뗀 나머지 중 가장
    오른쪽 한 칸»만 보면 자동으로 무시된다 — 별도 처리가 필요 없다.

    예: "robostar.co.kr" → "robostar" / "www.robostar.com" → "robostar"
        "*.robostar.com" → "robostar" (와일드카드도 동일하게 처리됨)

    Args:
        host: 포트·스킴이 없는 순수 호스트 이름.

    Returns:
        핵심 이름 한 칸(소문자). 판정 불가(빈 문자열 등)면 "".
    """
    labels = [label for label in host.lower().rstrip(".").split(".") if label]
    if len(labels) <= 1:
        return ".".join(labels)
    if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_LABEL_PUBLIC_SUFFIXES:
        remainder = labels[:-2]
    elif labels[-1] in SINGLE_LABEL_PUBLIC_SUFFIXES:
        remainder = labels[:-1]
    else:
        # ★ 목록에 없는 접미사(알 수 없는 TLD) — 마지막 한 칸만 접미사로 보는
        #   보수적인 기본값으로 넘어간다. 완전한 Public Suffix List가 아니므로
        #   생기는 한계이며, 틀려도 「불일치로 판정 → 재시도 안 함」쪽으로만
        #   기운다(§ 알려진 한계 — 최종 보고 참조).
        remainder = labels[:-1]
    return remainder[-1] if remainder else ""


def _is_same_organization(original_host: str, candidate_host: str) -> bool:
    """두 호스트 이름이 «같은 회사」로 보이는지 판정한다 (핵심 이름 비교).

    ★ 가장 중요한 안전장치. 이게 없으면 하이브(hiveoil.co.kr)처럼 호스팅
      업체의 기본 인증서(realserver2.com)를 회사 홈페이지로 착각해 따라간다.
    """
    original_core = _registrable_core_name(original_host)
    if not original_core:
        return False  # 원래 이름에서 핵심 이름을 못 뽑으면 안전하게 거부한다
    return original_core == _registrable_core_name(candidate_host)


def _swap_host(url: str, new_host: str) -> str:
    """주소의 스킴·경로·쿼리는 그대로 두고 호스트 이름만 바꾼다."""
    parsed = urllib.parse.urlparse(url)
    netloc = f"{new_host}:{parsed.port}" if parsed.port else new_host
    # `_replace`는 밑줄로 시작하지만 namedtuple(ParseResult)의 정식 공개 API다
    # (필드 이름과 충돌을 피하려고 밑줄을 붙인 것일 뿐, private 메서드가 아니다).
    return urllib.parse.urlunparse(parsed._replace(netloc=netloc))


def _attempt_cert_fallback(
    original_url: str,
    exc: HomepageCertNameMismatchError,
    fetch: Fetcher,
    lookup_cert_names: CertNameLookup,
) -> Optional[tuple[str, str, bool, robotparser.RobotFileParser]]:
    """인증서 이름 불일치를 「같은 회사의 다른 도메인」으로 판단되면 1번만 재시도한다 (C안).

    ★ 재시도는 정확히 1번뿐이다 — 후보 중 첫 번째로 «같은 회사」로 판정된
      이름 하나에만 `fetch`를 다시 부른다. 그 재시도가 또 실패해도 더는
      다른 후보로 넘어가지 않는다(무한 연쇄 금지, 지시 2번).

    Args:
        original_url: 처음 시도했던(정규화된) 주소.
        exc: 루트 접속에서 난 인증서 이름 불일치 예외.
        fetch: 재시도에도 그대로 쓰는, «완전 검증»하는 접속 함수.
        lookup_cert_names: 인증서의 SAN·CN 이름을 읽어오는 함수.

    Returns:
        (실제로 읽은 주소, HTML) — 따라갈 이름이 없거나 재시도도 실패하면 None.
    """
    candidate_hosts = lookup_cert_names(original_url)
    matched_host = next(
        (host for host in candidate_hosts if _is_same_organization(exc.host, host)),
        None,
    )
    if matched_host is None:
        return None  # 같은 회사로 볼 만한 이름이 없다 — 포기한다 (하이브 사례)

    retry_url = _swap_host(original_url, matched_host)
    try:
        html_text, effective_url, verified, robots = _load_origin_root(
            retry_url,
            fetch,
        )  # ★ 새 origin도 robots를 먼저 확인한 뒤 완전 검증한다.
    except HomepageFetchError:
        return None
    return effective_url, html_text, verified, robots


def _load_robots(base_url: str, fetch: Fetcher) -> robotparser.RobotFileParser:
    """RFC 9309 실패 의미론으로 robots.txt를 확인한다.

    HTTP 4xx는 robots가 이용 불가한 것으로 보아 빈 규칙으로 진행한다. 서버 오류,
    DNS·timeout·응답 검증 실패는 규칙을 확인할 수 없는 상태이므로 전면 허용으로
    바꾸지 않고 호출자까지 실패를 전달한다.

    ★ 같은 조사(scope) 안에서 이미 다른 수집기(공식 IR PDF·광역 웹)가 같은
      **origin**(scheme+host+port)의 robots.txt를 확인했으면 새 네트워크
      요청 없이 그 판정을 재사용한다(``robots_cache.cached_robots_decision``
      — 최대 4회 중복 요청 실측). 캐시 키는 host가 아니라 origin
      이다 — HTTPS 전면 실패 뒤 `_http_variant`로 HTTP 재시도할 때, HTTPS
      robots 판정을 그대로 물려받으면 실제 HTTP robots.txt(예: 전면 차단)를
      다시 확인하지 않아 차단된 사이트를 평문으로 읽어버린다(독립 검토
      P0 실측 — ``robots_cache.py`` 모듈 docstring 참조).
    """
    robots_url = urllib.parse.urljoin(base_url, "/robots.txt")
    host = (urllib.parse.urlsplit(robots_url).hostname or "").casefold()
    cache_key = robots_cache_key(robots_url)

    def loader() -> RobotsDecision:
        parser = robotparser.RobotFileParser()
        try:
            text, effective_url, _verified = _fetch_page(fetch, robots_url)
            if not _same_origin(robots_url, effective_url):
                raise HomepageRobotsUnreachable(
                    "robots.txt가 다른 origin으로 이동했습니다"
                )
        except HomepageCertNameMismatchError:
            # ★ robots 판정이 아니라 「같은 회사의 다른 도메인」 재시도 신호다
            #   (`_retry_alternate_host` 참조) — 캐시하지 않고 그대로 던진다.
            raise
        except HomepageRobotsUnavailable:
            # ★ 주의: `RobotFileParser`는 `.parse()`를 한 번도 안 부르면 «미확인»
            #   상태로 보고 `can_fetch()`가 오히려 «전부 금지»를 돌려준다
            #   (표준 라이브러리 함정) — HTTP 4xx로 부재가 확인된 경우에만
            #   빈 규칙으로 «확인 끝»을 표시한다.
            text = ""
        except (HomepageRobotsUnreachable, HomepageFetchError) as exc:
            return RobotsDecision(
                host=host,
                parser=parser,
                blocked=True,
                reason_code="robots_unreachable",
                detail=str(exc),
            )
        parser.parse(text.splitlines())  # 네트워크 접속 없이 주어진 글자만 해석한다
        return RobotsDecision(host=host, parser=parser, blocked=False, reason_code="robots_ok")

    decision = cached_robots_decision(cache_key, loader)
    if decision.blocked:
        detail_suffix = f": {decision.detail}" if decision.detail else ""
        raise HomepageRobotsUnreachable(
            f"robots.txt를 확인하지 못했습니다 (reason={decision.reason_code}){detail_suffix}"
        )
    return decision.parser


def _load_origin_root(
    url: str,
    fetch: Fetcher,
) -> tuple[str, str, bool, robotparser.RobotFileParser]:
    """한 origin의 robots를 본문보다 먼저 확인하고 허용된 루트만 읽는다."""

    robots = _load_robots(url, fetch)
    if not robots.can_fetch(USER_AGENT, url):
        raise HomepageRobotsUnreachable("robots.txt가 홈페이지 루트를 차단했습니다")
    policy = lambda candidate: (
        _same_origin(url, candidate)
        and robots.can_fetch(USER_AGENT, candidate)
    )
    html_text, effective_url, verified = _fetch_page(
        fetch,
        url,
        url_allowed=policy,
    )
    return html_text, effective_url, verified, robots


def _extract_links(raw_html: str, base_url: str, same_netloc: str) -> list[str]:
    """홈페이지 안의 링크 중 같은 도메인·글자 페이지만 뽑는다.

    외부 사이트나 문서·이미지 파일은 따라가지 않는다.
    """
    parser = _LinkExtractor()
    parser.feed(raw_html)
    links: list[str] = []
    for href in parser.hrefs:
        absolute = urllib.parse.urljoin(base_url, href).split("#", 1)[0]
        parsed = urllib.parse.urlparse(absolute)
        if parsed.scheme not in ("http", "https") or parsed.netloc != same_netloc:
            continue
        if any(absolute.lower().endswith(ext) for ext in EXCLUDED_EXTENSIONS):
            continue
        if absolute not in links:
            links.append(absolute)
    return links


def _link_priority(url: str) -> int:
    """핵심 소개·사업·채용 HTML이 IR 목록보다 앞에 오게 한다.

    공식 IR 문서 수집은 별도 기능이 맡는다. 이 일반 HTML 수집기의 6쪽 예산은
    사용자가 회사를 이해하는 데 필요한 소개·사업·비전 페이지부터 배정한다.
    호스트 이름은 평가하지 않는다. 예를 들어 ``company.example`` 때문에 모든
    하위 링크가 ``company`` 페이지처럼 취급되면 실제 경로 우선순위가 사라진다.
    """
    parsed = urllib.parse.urlparse(url)
    if _is_brand_landing_path(parsed):
        return 0
    path_and_query = parsed.path
    if parsed.query:
        path_and_query = f"{path_and_query}?{parsed.query}"
    lowered = urllib.parse.unquote(path_and_query).lower()
    for rank, keyword in enumerate(PRIORITY_PATH_KEYWORDS):
        if keyword in lowered:
            return rank
    return len(PRIORITY_PATH_KEYWORDS)


def _is_brand_landing_path(parsed: urllib.parse.ParseResult) -> bool:
    """브랜드명이 경로 끝에 있는 회사소개 landing page인지 판정한다.

    JYP처럼 회사소개가 ``/ko/JYP``인 사이트를 위한 작은 보완이다. 등록
    도메인의 핵심 라벨과 마지막 경로 조각만 비교하므로 ``company.example``
    호스트의 모든 링크가 ``company`` 우선순위를 받던 문제는 되살리지 않는다.
    또한 ``/ko/JYP/History`` 같은 하위 namespace 전체를 올리지 않아 5쪽 예산을
    같은 회사소개 메뉴가 독점하지 않는다.
    """
    core = re.sub(
        r"[^a-z0-9]",
        "",
        _registrable_core_name(parsed.hostname or ""),
    )
    segments = [
        re.sub(r"[^a-z0-9]", "", segment.lower())
        for segment in urllib.parse.unquote(parsed.path).split("/")
        if segment
    ]
    if not segments:
        return False
    brand = segments[-1]
    if (
        len(core) < BRAND_PATH_MIN_TOKEN_CHARS
        or len(brand) < BRAND_PATH_MIN_TOKEN_CHARS
        or core in BRAND_PATH_EXCLUDED_TOKENS
        or brand in BRAND_PATH_EXCLUDED_TOKENS
    ):
        return False
    shorter, longer = sorted((core, brand), key=len)
    return (
        longer.startswith(shorter)
        and len(longer) - len(shorter) <= BRAND_PATH_MAX_PREFIX_GAP
    )


def _page_candidate_scope_is_complete(raw_html: str, total_chars: int) -> bool:
    """현재 페이지를 후보 원문 상한 때문에 잘라내지 않는지 판정한다."""

    text = strip_html(raw_html)
    if len(text) > MAX_CHARS_PER_PAGE:
        return False
    if len(text) < MIN_FRAGMENT_CHARS:
        return True
    return total_chars + len(text) <= MAX_TOTAL_CHARS


def _collect_page(
    page_url: str,
    raw_html: str,
    fragments: list[dict[str, str]],
    seen_text: set[str],
    total_chars: int,
    *,
    final_url_verified: bool = False,
) -> int:
    """페이지 하나를 조각으로 만들어 `fragments`에 더한다.

    Returns:
        갱신된 누적 글자 수.
    """
    text = strip_html(raw_html)[:MAX_CHARS_PER_PAGE]
    if len(text) < MIN_FRAGMENT_CHARS or text in seen_text:
        return total_chars  # 빈 페이지이거나 이미 넣은 것과 같은 내용
    seen_text.add(text)
    remaining = MAX_TOTAL_CHARS - total_chars
    if remaining < MIN_FRAGMENT_CHARS:
        return total_chars  # 전체 상한에 다 찼다
    kept = text[:remaining]
    fragment = {"종류": FRAGMENT_KIND, "원문": kept, "출처": page_url}
    published_at = _published_date_from_html(raw_html)
    if published_at:
        fragment["문서일"] = published_at
    if final_url_verified and urllib.parse.urlsplit(page_url).scheme.casefold() == "https":
        fragment["후보출처검증"] = "https_exact_dart_host"
    fragments.append(fragment)
    return total_chars + len(kept)


def _fetch_page(
    fetch: Fetcher,
    url: str,
    *,
    url_allowed: Callable[[str], bool] | None = None,
) -> tuple[str, str, bool]:
    """구형 시험 fetch와 최종 URL을 보존하는 실제 fetch를 함께 다룬다."""

    if fetch is default_fetch:
        result = default_fetch(url, url_allowed=url_allowed)
    else:
        result = fetch(url)
    if isinstance(result, FetchedPage):
        effective = str(result.effective_url or "").strip()
        if not effective:
            raise HomepageFetchError("홈페이지 최종 URL을 확인하지 못했습니다")
        if not _same_origin(url, effective):
            raise HomepageSecurityPolicyError(
                "홈페이지 리다이렉트가 최초 origin을 벗어났습니다"
            )
        if url_allowed is not None and url_allowed(effective) is not True:
            raise HomepageSecurityPolicyError(
                "홈페이지 리다이렉트 경로를 robots.txt가 차단했습니다"
            )
        return result.html, effective, True
    if not isinstance(result, str):
        raise HomepageFetchError("홈페이지 응답 형식이 올바르지 않습니다")
    return result, url, False


def _same_origin(left: str, right: str) -> bool:
    """스킴·정규화 host·유효 포트가 모두 같은 origin인지 판정한다."""

    try:
        left_parts = urllib.parse.urlsplit(left)
        right_parts = urllib.parse.urlsplit(right)
        left_scheme = left_parts.scheme.casefold()
        right_scheme = right_parts.scheme.casefold()
        left_host = (left_parts.hostname or "").rstrip(".").encode("idna").decode()
        right_host = (right_parts.hostname or "").rstrip(".").encode("idna").decode()
        default_port = {"http": 80, "https": 443}
        left_port = left_parts.port or default_port.get(left_scheme)
        right_port = right_parts.port or default_port.get(right_scheme)
    except (TypeError, ValueError, UnicodeError):
        return False
    return (
        left_scheme in default_port
        and left_scheme == right_scheme
        and left_host.casefold() == right_host.casefold()
        and left_port == right_port
    )


def _is_robots_url(url: str) -> bool:
    try:
        return urllib.parse.urlsplit(url).path.rstrip("/").casefold() == "/robots.txt"
    except (TypeError, ValueError):
        return False
