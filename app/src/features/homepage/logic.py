"""회사 홈페이지에서 「뭘 잘하나」 재료(조각)를 모은다.

정본: 확정/03_수집/2_규칙/01_소스정책.md (2번 소스 — 회사 홈페이지)
      확정/05_생성/2_규칙/02_유형별소스.md (2·4-2·4-3 칸의 홈페이지 소스)
      확정/03_수집/1_흐름/02_실패처리.md (「없다」와 「못 가져옴」은 다르다)

★ 네트워크 호출은 전부 `fetch` 인자로 주입받는다 (`Fetcher` 타입).
  기본값은 실제로 접속하는 `default_fetch`이지만, 시험에서는 가짜 함수를 넣어
  진짜 접속 없이 검증한다.
"""

from __future__ import annotations

import html
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Callable, Optional
from urllib import robotparser

from src.features.homepage.constants import (
    EXCLUDED_EXTENSIONS,
    FRAGMENT_KIND,
    HOSTNAME_MISMATCH_MARKER,
    HTTPS_DEFAULT_PORT,
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

#: <script>·<style>·<noscript> 안의 글자는 사람이 읽는 본문이 아니므로 뺀다.
_SKIP_TAGS = frozenset({"script", "style", "noscript"})


class HomepageFetchError(Exception):
    """홈페이지 접속 실패 — 시간초과·연결거부·HTTP 오류를 이 하나로 통일해서 던진다."""


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


#: 주소 하나를 받아 HTML 원문을 돌려주는 함수. 접속에 실패하면 예외로 던진다
#: (조용히 빈 문자열을 돌려주면 「내용이 없다」와 구분이 안 된다).
Fetcher = Callable[[str], str]

#: 인증서 이름 불일치가 났을 때, 그 서버 인증서에 적힌 유효 이름(SAN·CN) 목록을
#: 돌려주는 함수. ★ 인증서를 «들여다보는» 용도로만 쓴다 — 본문은 절대 읽지 않는다.
#: 조회에 실패해도 예외를 던지지 않고 빈 리스트를 돌려준다는 약속이다
#: (실패 = 「못 알아냄」 = 재시도 포기, `default_lookup_cert_names` 참조).
CertNameLookup = Callable[[str], list[str]]


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


def default_fetch(url: str) -> str:
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
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as res:  # noqa: S310
            raw = res.read()
    except urllib.error.URLError as exc:
        if _is_hostname_mismatch(exc):
            host = urllib.parse.urlparse(url).hostname or ""
            raise HomepageCertNameMismatchError(
                f"인증서 이름 불일치: {exc.reason}", host=host
            ) from exc
        raise HomepageFetchError(f"{type(exc).__name__}: {exc}") from exc
    except (TimeoutError, OSError) as exc:
        raise HomepageFetchError(f"{type(exc).__name__}: {exc}") from exc
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp949", errors="replace")  # 국내 구형 사이트 대응


def default_lookup_cert_names(url: str) -> list[str]:
    """실제 서버에 접속해 인증서에 적힌 SAN·CN 이름 목록을 읽어온다.

    ★ 이름만 «들여다보는» 용도다. HTTP 요청은 하지 않으므로 본문은 절대
      읽지 않는다 (지시 1번 — 본문을 읽을 때는 반드시 완전 검증해야 하므로,
      이 함수는 그 「완전 검증」 접속과는 별개의, 이름 확인 전용 접속이다).

    호스트 이름 검증(`check_hostname`)만 끄고 인증서 체인·만료 검증
    (`verify_mode=CERT_REQUIRED`, 기본값)은 그대로 켠 채로 접속한다 — 아무
    인증서나 믿는 게 아니라 「신뢰할 수 있는 CA가 발급했고 만료도 안 됐지만
    이름만 다른」 경우만 통과시킨다.

    Args:
        url: 인증서 이름 불일치가 났던 원래 주소.

    Returns:
        인증서의 SAN(DNS 항목)·CN 이름 목록(소문자). 조회 자체가 실패하면
        예외를 던지지 않고 빈 리스트를 돌려준다 — 못 알아내면 그냥 포기다.
    """
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        return []  # http 주소는 애초에 인증서가 없다
    host = parsed.hostname
    port = parsed.port or HTTPS_DEFAULT_PORT

    context = ssl.create_default_context()
    context.check_hostname = False  # 이름 비교는 우리가 직접 한다 (아래 판정 로직)
    try:
        with socket.create_connection((host, port), timeout=TIMEOUT_SEC) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls_sock:
                cert = tls_sock.getpeercert()
    except (OSError, ssl.SSLError, ValueError):
        return []  # 접속·핸드셰이크 실패 — 이름을 못 읽었으니 재시도도 포기한다

    if not cert:
        return []
    names: list[str] = []
    for entry_type, value in cert.get("subjectAltName", ()):
        if entry_type == "DNS":
            names.append(value.lower())
    for rdn in cert.get("subject", ()):
        for key, value in rdn:
            if key == "commonName":
                names.append(value.lower())
    return names


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
    return re.sub(r"\s+", " ", text).strip()


def collect_homepage_fragments(
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
    try:
        root_html = fetch(url)
        final_url = url
    except HomepageCertNameMismatchError as exc:
        fallback = _attempt_cert_fallback(url, exc, fetch, lookup_cert_names)
        if fallback is None:
            return HomepageCollectResult(
                state="failed",
                detail=f"홈페이지 접속 실패(인증서 이름 불일치, 재시도 대상 없음): {exc}",
            )
        final_url, root_html = fallback
        origin_note = f"원래 주소({url})의 인증서 이름이 달라 {final_url} 로 재시도해 접속함"
    except HomepageFetchError as exc:
        # https가 아예 안 되는 사이트가 있다 (국내 중소기업 홈페이지에 흔하다).
        # ★ 암호화 없이 읽게 되므로 «그 사실을 반드시 남긴다». 조용히 내려가지 않는다.
        plain = _http_variant(url)
        if not plain:
            return HomepageCollectResult(
                state="failed", detail=f"홈페이지 접속 실패: {exc}"
            )
        try:
            root_html = fetch(plain)
        except HomepageFetchError as plain_exc:
            return HomepageCollectResult(
                state="failed", detail=f"홈페이지 접속 실패: {plain_exc}"
            )
        final_url = plain
        origin_note = (
            f"{url} 는 접속되지 않아 {plain} (암호화되지 않은 연결)로 읽었습니다"
        )

    parsed_root = urllib.parse.urlparse(final_url)
    # 한 번만 가져와 재사용 — 경로마다 robots.txt를 다시 접속하지 않는다.
    # ★ 인증서 우회로 도메인이 바뀌었으면 실제로 읽는 도메인(final_url) 기준으로 조회한다.
    robots = _load_robots(final_url, fetch)
    fragments: list[dict[str, str]] = []
    seen_text: set[str] = set()
    seen_urls: set[str] = {final_url}
    total_chars = 0
    pages_fetched = 1  # 루트 페이지도 1페이지로 센다

    if robots.can_fetch(USER_AGENT, final_url):
        total_chars = _collect_page(final_url, root_html, fragments, seen_text, total_chars)

    candidates = sorted(
        _extract_links(root_html, final_url, parsed_root.netloc), key=_link_priority
    )
    for link in candidates:
        if pages_fetched >= MAX_PAGES or total_chars >= MAX_TOTAL_CHARS:
            break  # 상한 도달 — 더 읽지 않는다 (무한 크롤링 금지)
        if link in seen_urls:
            continue
        seen_urls.add(link)
        if not robots.can_fetch(USER_AGENT, link):
            continue
        pages_fetched += 1  # 실패해도 시도 자체는 상한에 넣는다
        try:
            page_html = fetch(link)
        except HomepageFetchError:
            continue  # 낱장 페이지 실패는 건너뛴다 — 루트는 이미 접속됐으니 전체 실패가 아니다
        total_chars = _collect_page(link, page_html, fragments, seen_text, total_chars)

    if not fragments:
        detail = "홈페이지에서 쓸 만한 글자를 찾지 못함"
        if origin_note:
            detail = f"{origin_note}; {detail}"
        return HomepageCollectResult(state="none", detail=detail)
    return HomepageCollectResult(state="ok", fragments=fragments, detail=origin_note)


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

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0 and data.strip():
            self._chunks.append(data.strip())

    def get_text(self) -> str:
        return " ".join(self._chunks)


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
) -> Optional[tuple[str, str]]:
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
        html_text = fetch(retry_url)  # ★ 재시도도 완전 검증(fetch 그대로) — 이름만 바꿨다
    except HomepageFetchError:
        return None
    return retry_url, html_text


def _load_robots(base_url: str, fetch: Fetcher) -> robotparser.RobotFileParser:
    """robots.txt를 한 번만 가져와 파서로 만든다. 경로마다 다시 접속하지 않는다.

    ★ robots.txt 자체를 못 가져오면 «허용»으로 본다 — 못 가져온 것을 금지로
      해석하면 robots.txt가 없는 대부분의 사이트가 통째로 막힌다.
    """
    parser = robotparser.RobotFileParser()
    robots_url = urllib.parse.urljoin(base_url, "/robots.txt")
    try:
        text = fetch(robots_url)
    except HomepageFetchError:
        # ★ 주의: `RobotFileParser`는 `.parse()`를 한 번도 안 부르면 «미확인» 상태로
        #   보고 `can_fetch()`가 오히려 «전부 금지»를 돌려준다 (표준 라이브러리 함정 —
        #   read()로 확인하기 전 false positive를 막으려는 설계). 그래서 빈 규칙으로
        #   명시적으로 «확인 끝, 규칙 없음»을 만들어야 전부 허용이 된다.
        text = ""
    parser.parse(text.splitlines())  # 네트워크 접속 없이 주어진 글자만 해석한다
    return parser


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
    """회사·기술 소개 페이지가 앞에 오게 하는 순위. 낮을수록 먼저 읽는다."""
    lowered = url.lower()
    for rank, keyword in enumerate(PRIORITY_PATH_KEYWORDS):
        if keyword in lowered:
            return rank
    return len(PRIORITY_PATH_KEYWORDS)


def _collect_page(
    page_url: str,
    raw_html: str,
    fragments: list[dict[str, str]],
    seen_text: set[str],
    total_chars: int,
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
    fragments.append({"종류": FRAGMENT_KIND, "원문": kept, "출처": page_url})
    return total_chars + len(kept)
