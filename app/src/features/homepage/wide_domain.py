"""공식 도메인군 계산 — «어느 호스트를 결속 근거와 함께 수집할지» 판정.

★ ① DART root가 회사 전용 등록 도메인(예: ``x.com``·``www.x.com``)일 때만
  그 하위 도메인(``recruit.x.com``·``ir.x.com``)을 자동으로 도메인군에 넣는다.
★ ② 공식 페이지 안에서 «명시적으로 링크된» 다른 호스트는 «후보»로만 들어온다.
  결속 근거(어느 공식 페이지의 어느 링크에서 발견됐는지)가 없는 호스트는
  절대 수집하지 않는다. 이 모듈은 등급을 매길 뿐 «공식 확정»을 선언하지
  않는다 — 최종 확정은 다른 담당자의 몫이다.

``logic.py``의 인증서 이름 대체-host 판정(``_registrable_core_name``)과 같은
알고리즘을 쓰지만, 그 모듈의 비공개 함수에 결합하지 않기 위해 상수만
공유하고 알고리즘은 이 파일에서 독립적으로 유지한다(의도적 소규모 중복 —
최종 보고서에 설계 결정으로 남긴다).
"""

from __future__ import annotations

import posixpath
import urllib.parse
from dataclasses import dataclass

from src.features.homepage.constants import (
    MULTI_LABEL_PUBLIC_SUFFIXES,
    SINGLE_LABEL_PUBLIC_SUFFIXES,
    TEST_FIXTURE_ONLY_SINGLE_LABEL_SUFFIXES,
    WIDE_EXCLUDED_LINKED_HOST_SUFFIXES,
    WIDE_PRIORITY_HOST_KEYWORDS,
    WIDE_SLOT_KEYWORD_MAP,
)

#: 판정 시점에만 두 집합을 함께 본다 — 정본 SINGLE_LABEL_PUBLIC_SUFFIXES 자체는
#: 절대 합치지 않는다(팀 리드 정정 2). 오프라인 시험 픽스처(``.example`` 등)가
#: 실제 코드 경로를 그대로 지나가게 하되, 정본 상수를 읽는 다른 코드나 사람이
#: 「.example도 실제 커버리지 TLD」라고 오해하지 않게 분리해 둔다.
_SINGLE_LABEL_SUFFIXES_FOR_MATCHING: frozenset[str] = (
    SINGLE_LABEL_PUBLIC_SUFFIXES | TEST_FIXTURE_ONLY_SINGLE_LABEL_SUFFIXES
)

#: 하위 도메인 이름으로도 매칭해도 안전한 키워드만(예: recruit.company.example).
#: 「company」처럼 흔한 낱말은 회사 등록 도메인 자체에 우연히 들어 있을 수
#: 있어(예: company.example) 호스트 전체 문자열 대조에 넣지 않는다 — 이미
#: `constants.py`의 PRIORITY_PATH_KEYWORDS 주석에 실측으로 남긴 함정과 같다.
_HOST_SAFE_KEYWORDS: frozenset[str] = frozenset(WIDE_PRIORITY_HOST_KEYWORDS)

_DEFAULT_PORT_BY_SCHEME = {"http": 80, "https": 443}
_ALLOWED_WEB_PORTS = frozenset({80, 443, 8080, 8443})


def _normalized_path(raw_path: str) -> str:
    """URL 경로를 prefix 비교에 쓸 한 가지 절대경로 모양으로 만든다."""

    decoded = urllib.parse.unquote(raw_path or "/")
    if "\\" in decoded or any(ord(character) < 32 for character in decoded):
        raise ValueError("공식 홈페이지 경로가 안전하지 않습니다")
    normalized = posixpath.normpath("/" + decoded.lstrip("/"))
    if decoded.endswith("/") and normalized != "/":
        normalized += "/"
    return urllib.parse.quote(normalized, safe="/%:@!$&'()*+,;=-._~")


_KNOWN_PAGE_SUFFIXES = (".html", ".htm", ".php", ".asp", ".aspx", ".jsp", ".do")


def _path_prefix(start_path: str, host: str) -> str:
    if start_path == "/" or start_path.endswith("/"):
        return start_path
    last_segment = start_path.rsplit("/", 1)[-1]
    if last_segment.casefold().endswith(_KNOWN_PAGE_SUFFIXES):
        parent = start_path.rsplit("/", 1)[0]
        parent_prefix = f"{parent}/" if parent else "/"
        core = registrable_core_name(host)
        dedicated_root = host in (core, f"www.{core}") if core else False
        # 공유 host의 루트 파일(/acme.html)은 부모 /로 넓히면 다른 입주자까지
        # 허용한다. 하위 디렉터리(/acme/index.html)는 /acme/까지만 허용한다.
        if parent_prefix == "/" and not dedicated_root:
            return f"{start_path}/"
        return parent_prefix
    return f"{start_path}/"


@dataclass(frozen=True)
class OfficialOrigin:
    """DART 공식 주소의 origin과 회사 소유 경로를 함께 봉인한 값.

    hostname만 보존하면 ``https://shared.example/acme``가
    ``https://shared.example/``로 바뀌어 다른 입주자 자료를 자사 공식자료로
    승격할 수 있다. 이 값은 scheme·host·effective port와 DART 시작 경로를
    끝까지 함께 들고 다니며, 본문 redirect가 그 경계를 벗어나면 거절한다.
    """

    scheme: str
    host: str
    port: int
    start_path: str
    path_prefix: str
    start_query: str = ""

    @property
    def authority(self) -> str:
        default_port = _DEFAULT_PORT_BY_SCHEME[self.scheme]
        display_host = f"[{self.host}]" if ":" in self.host else self.host
        return display_host if self.port == default_port else f"{display_host}:{self.port}"

    @property
    def key(self) -> str:
        return f"{self.scheme}://{self.host}:{self.port}{self.path_prefix}"

    @property
    def root_url(self) -> str:
        return urllib.parse.urlunsplit(
            (self.scheme, self.authority, self.start_path, self.start_query, "")
        )

    @property
    def robots_url(self) -> str:
        return urllib.parse.urlunsplit(
            (self.scheme, self.authority, "/robots.txt", "", "")
        )

    @property
    def sitemap_url(self) -> str:
        return urllib.parse.urlunsplit(
            (self.scheme, self.authority, "/sitemap.xml", "", "")
        )

    def _parsed_same_origin(self, value: str) -> urllib.parse.SplitResult | None:
        try:
            parsed = urllib.parse.urlsplit(value)
            host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").casefold()
            port = (
                parsed.port
                if parsed.port is not None
                else _DEFAULT_PORT_BY_SCHEME.get(parsed.scheme.casefold())
            )
        except (TypeError, ValueError, UnicodeError):
            return None
        if (
            parsed.scheme.casefold() != self.scheme
            or host != self.host
            or port != self.port
            or parsed.username is not None
            or parsed.password is not None
        ):
            return None
        return parsed

    def allows_content_url(self, value: str) -> bool:
        parsed = self._parsed_same_origin(value)
        if parsed is None:
            return False
        try:
            path = _normalized_path(parsed.path)
        except ValueError:
            return False
        if path == self.start_path:
            return True
        # 공유 host의 루트 파일은 디렉터리가 아니다. ``/acme.html/other``를
        # 자손 경로로 인정하면 일부 서버의 path-info 처리에서 다른 콘텐츠로
        # 넓어질 수 있으므로 정확히 그 파일 하나만 허용한다.
        exact_file_scope = (
            self.path_prefix == f"{self.start_path}/"
            and self.start_path.casefold().endswith(_KNOWN_PAGE_SUFFIXES)
        )
        return not exact_file_scope and path.startswith(self.path_prefix)

    def allows_infrastructure_url(self, value: str) -> bool:
        parsed = self._parsed_same_origin(value)
        if parsed is None:
            return False
        try:
            path = _normalized_path(parsed.path)
        except ValueError:
            return False
        return path in ("/robots.txt", "/sitemap.xml")

    def with_host(self, host: str) -> "OfficialOrigin":
        normalized_host = host.rstrip(".").encode("idna").decode("ascii").casefold()
        return OfficialOrigin(
            scheme=self.scheme,
            host=normalized_host,
            port=self.port,
            start_path=self.start_path,
            path_prefix=self.path_prefix,
            start_query=self.start_query,
        )


def parse_official_origin(raw: str) -> OfficialOrigin | None:
    """DART URL을 회사 소유 origin+경로로 정규화한다. 추측 불가면 None."""

    candidate = str(raw or "").strip()
    if not candidate:
        return None
    if candidate.startswith("//"):
        candidate = f"https:{candidate}"
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    try:
        parsed = urllib.parse.urlsplit(candidate)
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").casefold()
        port = (
            parsed.port
            if parsed.port is not None
            else _DEFAULT_PORT_BY_SCHEME.get(scheme)
        )
        start_path = _normalized_path(parsed.path)
    except (TypeError, ValueError, UnicodeError):
        return None
    if (
        scheme not in _DEFAULT_PORT_BY_SCHEME
        or not host
        or port not in _ALLOWED_WEB_PORTS
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    query = urllib.parse.quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    return OfficialOrigin(
        scheme=scheme,
        host=host,
        port=int(port),
        start_path=start_path,
        path_prefix=_path_prefix(start_path, host),
        start_query=query,
    )


def registrable_core_name(host: str) -> str:
    """호스트의 전체 등록 도메인(eTLD+1 — 공개 접미사 + 그 바로 앞 한 칸)을 돌려준다.

    ★ P0-1 수정(2026-08-31): 예전 구현은 접미사를 뗀 뒤 «핵심 이름 한 칸만»
      돌려줘서 ``company.com``·``company.net``·``company.co.kr``이 전부 같은
      값(``"company"``)이 되어 서로 다른 등록 도메인이 같다고 오판했다(남의
      도메인이 REQUIRED 고신뢰 문서로 자동 승격됨). 이제 접미사를 **포함해서**
      돌려주므로(``"company.com"``·``"company.co.kr"`` 등) TLD가 다르면 값도
      달라진다.

    Args:
        host: 포트·스킴이 없는 순수 호스트 이름.

    Returns:
        eTLD+1 전체 문자열(소문자). 판정 불가(빈 문자열 등) 또는 공개 접미사
        목록 밖의 접미사면 ``""``(fail-closed — 등록 도메인 경계를 모르는
        채로 «같은 도메인」이라고 주장하지 않는다).
    """
    labels = [label for label in (host or "").lower().rstrip(".").split(".") if label]
    if len(labels) <= 1:
        return ".".join(labels)
    if len(labels) >= 3 and ".".join(labels[-2:]) in MULTI_LABEL_PUBLIC_SUFFIXES:
        suffix_labels = 2
    elif labels[-1] in _SINGLE_LABEL_SUFFIXES_FOR_MATCHING:
        suffix_labels = 1
    else:
        # 목록에 없는 접미사 — fail-closed. 경계를 모르는 채로 같다고
        # 주장하지 않는다(예전의 「한 칸만 접미사로 보는 보수적 기본값」은
        # 서로 다른 미지 TLD를 같다고 오판할 수 있어 폐기했다).
        return ""
    remainder = labels[: len(labels) - suffix_labels]
    if not remainder:
        return ""
    return ".".join(labels[-(suffix_labels + 1) :])


def is_registered_subdomain(root_host: str, candidate_host: str) -> bool:
    """candidate_host가 root_host와 같은 등록 도메인의 (하위)도메인인가."""
    root_core = registrable_core_name(root_host)
    if not root_core:
        return False
    candidate_core = registrable_core_name(candidate_host)
    return bool(candidate_core) and candidate_core == root_core


def is_excluded_linked_host(host: str) -> bool:
    """소셜·광고·분석 등 «회사의 다른 공식 채널」로 보지 않는 호스트인가."""
    normalized = (host or "").lower().rstrip(".")
    if not normalized:
        return True
    return any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in WIDE_EXCLUDED_LINKED_HOST_SUFFIXES
    )


@dataclass(frozen=True)
class BoundHost:
    """도메인군에 들어온 호스트 하나와 그 결속 근거·요구도."""

    host: str
    identity_binding: str
    #: True면 REQUIRED(고 결속) 문서, False면 OPTIONAL(후보) 문서로 만든다.
    is_high_confidence: bool


def bind_root_host(root_host: str) -> BoundHost:
    """DART가 준 홈페이지 호스트 — 가장 높은 신뢰도."""
    return BoundHost(
        host=root_host.casefold(),
        identity_binding="DART 기업개황 홈페이지 주소(root)",
        is_high_confidence=True,
    )


def bind_registered_subdomain(root_host: str, candidate_host: str) -> BoundHost | None:
    """회사 전용 registrable root에서만 같은 도메인 하위호스트를 자동 결속한다.

    ``sites.google.com/acme``처럼 DART 주소 자체가 공유 플랫폼 하위호스트인
    경우, ``drive.google.com``까지 같은 google.com이라는 이유로 회사
    공식 REQUIRED가 되어서는 안 된다. root가 eTLD+1 자체 또는 정확한 www
    짝일 때만 회사가 그 등록 도메인을 소유한다고 볼 수 있다. 그 밖의 링크는
    ``bind_linked_host``의 OPTIONAL 후보 경로를 거친다.
    """

    normalized_root = (root_host or "").casefold().rstrip(".")
    root_core = registrable_core_name(normalized_root)
    if not root_core or normalized_root not in (root_core, f"www.{root_core}"):
        return None
    if not is_registered_subdomain(root_host, candidate_host):
        return None
    return BoundHost(
        host=candidate_host.casefold(),
        identity_binding=(
            f"root({root_host})와 같은 등록 도메인의 하위 도메인"
        ),
        is_high_confidence=True,
    )


def www_apex_alternate(host: str) -> str | None:
    """host의 apex/www 짝 하나를 계산한다 — ``www.`` 접두사 유무만 다룬다.

    ``www.company.com`` ↔ ``company.com`` 같은 정확히 한 짝만 다룬다.
    등록 도메인(eTLD+1) 자체를 바꾸지 않는 변형이라 서로 같은 회사를
    가리킬 가능성이 매우 높다 — 그 밖의 하위 도메인 변형(예:
    ``recruit.company.com``)은 여기서 다루지 않는다(공식 페이지에서
    링크로 발견되는 등 다른 경로로만 결속한다, ``bind_linked_host``).

    Args:
        host: 포트·스킴이 없는 순수 호스트 이름.

    Returns:
        계산된 대안 호스트(소문자). ``host``가 빈 문자열이면 ``None``.
    """
    normalized = (host or "").lower().rstrip(".")
    if not normalized:
        return None
    core = registrable_core_name(normalized)
    if not core:
        return None
    if normalized == core:
        return f"www.{core}"
    if normalized == f"www.{core}":
        return core
    # recruit.company.com → www.recruit.company.com 같은 것은 apex/www 짝이
    # 아니다. DART가 준 임의 하위도메인을 고신뢰 후보로 자동 확장하지 않는다.
    return None


def bind_www_apex_alternate(root_host: str) -> BoundHost | None:
    """root_host의 apex/www 짝을 고신뢰(REQUIRED) 후보로 결속한다.

    ★ APEX-WWW-OFFICIAL-ROOT-GAP(통합 담당 지시, 2026-08-31): DART가 준
      호스트 하나(예: ``company.com``)가 실제로는 다른 짝
      (``www.company.com``)으로 운영되는 경우가 흔하다. redirect
      판정은 정확히 같은 host만 허용하므로(SSRF 방어이자 eTLD+1
      결함 수정과 같은 맥락 — 여기서 완화하지 않는다), root 페이지
      자체가 apex↔www redirect라면 그 redirect 자체가 막혀 수집이
      0건이 될 수 있었다. 그래서 redirect를 따라가는 대신 **apex·www
      짝을 각각 독립된 후보로 미리 결속**해, 호출자가 두 호스트를
      각자 robots부터 따로 확인하며 직접 방문하게 한다.
    ★ 등록 도메인(eTLD+1)이 실제로 같은지 ``is_registered_subdomain``으로
      다시 확인한다 — root_host의 접미사가 공개 접미사 목록 밖(fail-closed)
      이면 ``registrable_core_name``이 ``""``을 돌려주므로 여기서도
      자동으로 판정 불가(``None``)가 된다. 등록 도메인 전체를 폭넓게
      허용하는 방향이 아니라, www. 접두사 유무라는 좁은 변형 하나만
      다룬다는 뜻을 이 재확인으로 코드에도 남긴다.

    Returns:
        결속 정보, 또는 판정 불가면 ``None``.
    """
    alternate = www_apex_alternate(root_host)
    if alternate is None or not is_registered_subdomain(root_host, alternate):
        return None
    return BoundHost(
        host=alternate.casefold(),
        identity_binding=f"DART root({root_host})의 apex/www 짝: {alternate}",
        is_high_confidence=True,
    )


def bind_linked_host(
    *, source_page_url: str, discovered_url: str, candidate_host: str
) -> BoundHost | None:
    """공식 페이지 안에서 명시적으로 링크된 다른 호스트 — «후보»로만 결속한다.

    소셜·광고·분석 호스트는 결속하지 않는다(``is_excluded_linked_host``).

    Returns:
        결속 정보, 또는 제외 대상이면 ``None``.
    """
    if is_excluded_linked_host(candidate_host):
        return None
    return BoundHost(
        host=candidate_host.casefold(),
        identity_binding=(
            f"공식 페이지 링크 후보 — 출처 페이지: {source_page_url}, "
            f"발견된 링크: {discovered_url}"
        ),
        is_high_confidence=False,
    )


_TRACKING_PARAM_PREFIXES: tuple[str, ...] = ("utm_",)
_TRACKING_PARAM_EXACT: frozenset[str] = frozenset(
    {"gclid", "fbclid", "mc_cid", "mc_eid", "igshid", "ref", "ref_src", "spm", "yclid"}
)


def _is_tracking_param(key: str) -> bool:
    lowered = key.casefold()
    return lowered.startswith(_TRACKING_PARAM_PREFIXES) or lowered in _TRACKING_PARAM_EXACT


def canonicalize_url(url: str) -> str:
    """fragment를 없애고 추적 파라미터를 뺀 정규화 URL을 만든다.

    스킴·호스트는 소문자로, 쿼리는 키 순서로 정렬해 같은 문서가 파라미터
    순서차이만으로 다른 URL이 되지 않게 한다.
    """
    parsed = urllib.parse.urlsplit(url)
    query_pairs = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not _is_tracking_param(key)
    ]
    query = urllib.parse.urlencode(sorted(query_pairs))
    path = parsed.path or "/"
    return urllib.parse.urlunsplit(
        (parsed.scheme.casefold(), parsed.netloc.casefold(), path, query, "")
    )


def slot_ids_for_url(url: str) -> tuple[str, ...]:
    """URL 안의 페이지 유형 키워드로 후보 슬롯 집합을 고른다(첫 일치 우선).

    경로(+쿼리)는 모든 키워드로 대조하지만, 호스트 이름은 «하위 도메인으로
    써도 안전한» 키워드(``_HOST_SAFE_KEYWORDS``, recruit·ir·news 등)만
    라벨 단위로 정확히 대조한다. 그래야 recruit.company.example 같은
    도메인은 잡아내면서, company.example처럼 회사 도메인 자체에 우연히
    들어 있는 낱말(«company» 등)이 모든 페이지를 잘못 분류하지 않는다.

    `wide_collect.py`(attempt.slot_ids)와 `wide_fragments.py`(조각 슬롯 태깅)가
    같은 표(`constants.WIDE_SLOT_KEYWORD_MAP`)를 이 함수 하나로 공유한다 —
    두 곳에 각각 다른 매핑을 두지 않는다.
    """
    parsed = urllib.parse.urlsplit(url)
    host_labels = frozenset((parsed.hostname or "").lower().split("."))
    path_and_query = urllib.parse.unquote(parsed.path).lower()
    if parsed.query:
        path_and_query = f"{path_and_query}?{urllib.parse.unquote(parsed.query).lower()}"

    for keywords, slots in WIDE_SLOT_KEYWORD_MAP:
        if any(keyword in path_and_query for keyword in keywords):
            return slots
        if any(
            keyword in host_labels for keyword in keywords if keyword in _HOST_SAFE_KEYWORDS
        ):
            return slots
    return ()
