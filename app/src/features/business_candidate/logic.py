"""회사명·주소 검색 결과를 사람이 확인할 작은 후보 목록으로 바꾼다.

검색 결과는 신뢰하지 않는다. HTML과 긴 snippet을 없앤 뒤 회사명, 주소, 공개
홈페이지 URL만 점수 계산에 쓴다. 점수가 높아도 여기서는 회사를 확정하지 않는다.
"""

from __future__ import annotations

import concurrent.futures
import base64
import contextvars
import hashlib
import hmac
import html
import ipaddress
import math
import re
import secrets
import threading
import logging
import time
import traceback
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from html.parser import HTMLParser
from typing import Protocol, Sequence
from urllib.parse import urlsplit, urlunsplit

from src.features.budget import logic as budget_logic
from src.features.business_candidate import ai_rerank, alias_resolution
from src.features.business_candidate.address_constants import ADDRESS_SCORE_BY_STRENGTH
from src.features.business_candidate.address_match import address_match_strength
from src.features.business_candidate.constants import (
    AI_ALIAS_TIMEOUT_SEC,
    AI_RERANK_TIMEOUT_SEC,
    CANDIDATE_ATTEMPT_TTL_SEC,
    MAX_ADDRESS_CHARS,
    MAX_CANDIDATES,
    MAX_CANDIDATE_SCORE_SUM,
    MAX_PROVIDER_TIMEOUT_SEC,
    MAX_RAW_CANDIDATES,
    MAX_NAME_CHARS,
    MAX_SOURCE_LABEL_CHARS,
    MAX_URL_CHARS,
    MIN_CANDIDATE_SCORE,
    PROVIDER_CALLS_PER_RESOLUTION,
    PROVIDER_TIMEOUT_SEC,
    PROVIDER_WORKER_SLOTS,
    RATE_MAX_SEARCHES,
    RATE_WINDOW_SEC,
)
from src.features.business_candidate.dart_identity import (
    LENGTH_TIEBREAK_KINDS,
    MATCH_KIND_PRIORITY,
    normalize_company_name,
)
from src.shared.company_identity import (
    latin_acronym_korean,
    normalized_latin_acronym,
)


_LEGAL_TOKENS = (
    "주식회사",
    "유한회사",
    "합자회사",
    "합명회사",
    "(주)",
    "㈜",
    "(유)",
    "corporation",
    "corp",
    "incorporated",
    "inc",
    "company",
    "co",
    "limited",
    "ltd",
)
_TOKEN_RE = re.compile(r"[0-9a-zA-Z가-힣]+")
_ADMIN_SUFFIXES = ("특별자치도", "특별자치시", "광역시", "특별시", "도", "시", "군", "구")
_TAG_OR_ENTITY_RE = re.compile(r"<|>|&(?:#\d+|#x[0-9a-f]+|[a-z]+);", re.I)
_RATE_SECRET = secrets.token_bytes(32)
_SELECTION_SECRET = secrets.token_bytes(32)
_RATE_HISTORY = budget_logic.RateHistory()
_RATE_LOCK = threading.Lock()
_PROVIDER_WORKER_SLOTS = threading.BoundedSemaphore(PROVIDER_WORKER_SLOTS)
class _TextExtractor(HTMLParser):
    """태그 속성·script 내용을 버리고 보이는 글자만 모은다."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.hidden_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        if tag.lower() in {"script", "style", "template", "noscript"}:
            self.hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "template", "noscript"}:
            self.hidden_depth = max(0, self.hidden_depth - 1)

    def handle_data(self, data: str) -> None:
        if not self.hidden_depth:
            self.parts.append(data)


class ResolutionStatus(str, Enum):
    OK = "ok"
    NO_MATCHES = "no_matches"
    UNCONFIGURED = "unconfigured"
    RATE_LIMITED = "rate_limited"
    TIMED_OUT = "timed_out"
    FAILED = "failed"


class ProviderRateLimited(RuntimeError):
    """공급자 429/quota. 응답 원문을 포함하지 않는다."""


logger = logging.getLogger(__name__)


class ProviderWorkerUnavailable(ProviderRateLimited):
    """내부 worker slot을 얻지 못해 공급자를 아직 호출하지 않은 상태."""


class ProviderTimedOut(RuntimeError):
    """공급자의 연결·읽기 timeout."""


@dataclass(frozen=True)
class RawBusinessCandidate:
    """공급자 경계가 돌려주는 최소 필드. 검색 snippet은 계약에 없다."""

    # 장소 검색의 displayName은 DART가 확인한 법인명이 아니다. 오해를 막기 위해
    # 후보 단계에서는 끝까지 중립적인 이름을 쓴다.
    candidate_name: str
    address: str = ""
    homepage: str = ""
    source_label: str = ""
    source_url: str = ""
    provider_name: str = ""
    attributions: tuple[tuple[str, str], ...] = ()
    # DART-local 후보만 채운다. 이름으로 다시 식별하면 사용자가 고른 법인과
    # 동명이거나 과거 법인인 다른 고유번호로 바뀔 수 있으므로 선택 서명에 묶는다.
    candidate_ref: str = ""
    stock_code: str = ""
    modify_date: str = ""
    english_name: str = ""
    name_match_kind: str = ""
    name_similarity: float = 0.0
    #: AI가 옮긴 정식명으로 찾아온 후보면 ``"ai"``. 결정적 경로는 빈 문자열이다.
    #: 어느 값도 회사를 확정하지 않으며, 사람이 고르는 절차는 두 경로가 같다.
    alias_source: str = ""
    #: 그때 AI가 준 정식 법인명. 점수 계산과 화면 구분의 근거라 함께 나른다.
    alias_name: str = ""


@dataclass(frozen=True)
class BusinessCandidate:
    candidate_name: str
    address: str
    homepage: str
    source_label: str
    source_url: str
    provider_name: str
    attributions: tuple[tuple[str, str], ...]
    score: float
    evidence: tuple[str, ...]
    candidate_ref: str = ""
    stock_code: str = ""
    modify_date: str = ""
    english_name: str = ""
    name_match_kind: str = ""
    name_similarity: float = 0.0
    #: `RawBusinessCandidate`와 같은 뜻. 화면 출처 구분과 시험이 읽는다.
    alias_source: str = ""
    alias_name: str = ""


@dataclass(frozen=True)
class CandidateResolution:
    status: ResolutionStatus
    candidates: tuple[BusinessCandidate, ...] = ()
    #: rate-limit/미설정은 False. timeout·HTTP 오류는 요청이 나갔을 수 있어 True다.
    provider_called: bool = False
    #: 후보가 없을 때 화면이 실제로 조회한 공급자를 정확히 설명하기 위한 표시명.
    provider_name: str = ""
    #: DART 로컬 후보의 profile 보강만 실패한 좁은 관측 표식. 일반 공급자 장애·
    #: timeout·rate limit에는 절대 쓰지 않으며 운영 점검 심각도를 낮추지 않는다.
    local_profile_enrichment_failed: bool = False
    #: AI 보조 재정렬 결과(`ai_rerank.RERANK_STATUS_*`). 부르지 않았으면 빈 문자열이다.
    #: 후보 집합은 이 값과 무관하게 같고, 어떤 값도 회사를 확정하지 않는다.
    rerank_status: str = ""
    #: AI 정식명 번역 결과(`alias_resolution.ALIAS_STATUS_*`). 열지 않았으면 빈
    #: 문자열이다. 이 값은 후보를 «더할» 수 있을 뿐 어떤 회사도 확정하지 않는다.
    alias_status: str = ""


class BusinessCandidateProvider(Protocol):
    """무료·읽기 전용 사업자 후보 공급자 계약.

    구현체는 한 호출 안에서 자체 재시도하면 안 되며, 과금형 공급자는 이 계약에 붙이지
    않는다. 비용이 드는 공급자는 기존 paid phase에 별도로 설계해야 한다.

    ★ 선택 확장: 클래스 속성 ``accepts_alias_ask = True``를 붙인 구현체만
      ``search(..., alias_ask=...)``를 추가로 받는다. 표시가 없는 구현체에는 그
      인자를 넘기지 않으므로 이 최소 계약은 그대로다.
    """

    costs_money: bool

    def search(
        self, *, company: str, address_hint: str, limit: int, timeout_sec: float
    ) -> Sequence[RawBusinessCandidate]: ...


def _plain_text(value: object, limit: int) -> str:
    raw = unicodedata.normalize("NFKC", str(value or ""))[: limit * 4]
    if _TAG_OR_ENTITY_RE.search(raw):
        parser = _TextExtractor()
        try:
            parser.feed(raw)
            raw = " ".join(parser.parts)
        except Exception:  # noqa: BLE001 — 깨진 HTML도 원문으로 되돌리지 않는다
            raw = ""
    raw = html.unescape(raw).replace("\x00", " ")
    raw = "".join(ch if ch.isprintable() else " " for ch in raw)
    return re.sub(r"\s+", " ", raw).strip()[:limit]


def _public_http_url(value: object) -> str:
    raw = _plain_text(value, MAX_URL_CHARS)
    if not raw or "\\" in raw:
        return ""
    try:
        parsed = urlsplit(raw)
        host = (parsed.hostname or "").strip(".").lower()
        if (
            parsed.scheme.lower() not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or host == "localhost"
            or host.endswith((".localhost", ".local"))
        ):
            return ""
        try:
            if not ipaddress.ip_address(host).is_global:
                return ""
        except ValueError:
            pass
        port = parsed.port
    except (ValueError, UnicodeError):
        return ""
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", parsed.query, ""))


def _company_key(value: str) -> str:
    return normalize_company_name(value)


def _normalized_latin_acronym(value: str) -> str:
    """대소문자·전각이 섞인 2~5자 ASCII 영문 약어를 대문자로 정규화한다.

    NFKC 뒤에도 ASCII 영문자만 남아야 한다. 숫자·공백·한글·키릴 문자 같은
    혼합 문자열은 비슷하게 보여도 약어로 인정하지 않아 오탐 범위를 넓히지 않는다.
    """
    return normalized_latin_acronym(value)


def _latin_acronym_korean(value: str) -> str:
    """XYZ/xyz 같은 2~5자 영문 약어만 ‘엑스와이지’로 펼친다.

    일반 영문명·혼합 문자열을 억지 음역하지 않는다. 이 좁은 규칙만 결정론적으로 쓴다.
    """
    return latin_acronym_korean(value)


def _latin_acronym_token_match(query: str, candidate_name: str) -> bool:
    """영문 법인명 안의 독립된 약어 토큰을 찾는다.

    ``XYZ`` → ``XYZ Ent.``는 허용하지만 ``SM`` → ``Smart Media``처럼 단어
    앞글자만 우연히 같은 경우는 허용하지 않는다. 대문자 2~5자 입력과 완전히 같은
    영문 토큰이 법인명에 실제로 적혀 있을 때만 참이다.
    """
    acronym = _normalized_latin_acronym(query)
    if not acronym:
        return False
    return any(
        token.casefold() == acronym.casefold()
        for token in re.findall(r"[A-Za-z]+", unicodedata.normalize("NFKC", candidate_name))
    )


def deterministic_name_affinity(query: str, candidate_name: str) -> int:
    """로컬 DART 후보를 외부 호출 전에 정렬할 이름 근접도.

    값은 후보끼리의 정렬에만 쓰며 자동 확정 임계값이 아니다. 영문 법인명에 실제
    약어 토큰이 있는 경우를 한글 letter-name 확장보다 먼저 둔다.
    """
    direct = _company_key(query)
    candidate = _company_key(candidate_name)
    if not direct or not candidate or direct == candidate:
        return 0
    if _latin_acronym_token_match(query, candidate_name):
        return 5
    expanded = _company_key(_latin_acronym_korean(query))
    if expanded and expanded == candidate:
        return 4
    if expanded and expanded in candidate:
        return 3
    if len(direct) >= 4 and len(candidate) >= 4 and (
        direct in candidate or candidate in direct
    ):
        return 2
    return 0


def is_deterministic_fuzzy_name_match(query: str, candidate_name: str) -> bool:
    """DART 로컬 색인에서 보여줄 좁은 별칭/포함 후보인지 판정한다.

    완전일치는 기존 DART 식별이 더 정확하고 싸게 처리하므로 여기서는 제외한다.
    2~5자 ASCII 영문 약어의 한글 letter-name 또는 정규화 뒤 4자 이상인 문자열의
    prefix/containment만 허용해 `SM` 같은 짧은 일반 토큰의 무차별 일치를 막는다.
    """
    return deterministic_name_affinity(query, candidate_name) > 0


def _tokens(value: str) -> set[str]:
    text = unicodedata.normalize("NFKC", value).casefold()
    return {token for token in _TOKEN_RE.findall(text) if len(token) >= 2}


def _address_tokens(value: str) -> set[str]:
    out: set[str] = set()
    for token in _tokens(value):
        out.add(token)
        for suffix in _ADMIN_SUFFIXES:
            if token.endswith(suffix) and len(token) - len(suffix) >= 2:
                out.add(token[: -len(suffix)])
                break
    return out


def _domain_key(url: str) -> str:
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    labels = [label for label in host.split(".") if label and label != "www"]
    return "".join(labels[:-1] or labels)


def _ai_alias_evidence(alias_name: str) -> str:
    """AI 추정 정식명으로 찾은 후보의 이름 근거 한 줄.

    사용자가 적은 말이 맞은 것이 아니라 «AI가 추정한 이름»이 맞았다는 사실과,
    그래서 사람이 확인해야 한다는 것만 짧게 말한다.
    """
    shown = _plain_text(alias_name, MAX_NAME_CHARS)
    if shown:
        return f"AI가 추정한 정식명({shown})과 일치합니다 · 사람이 확인해야 합니다"
    return "AI가 추정한 정식명과 일치합니다 · 사람이 확인해야 합니다"


def _score(
    *,
    query: str,
    address_hint: str,
    candidate_name: str,
    address: str,
    homepage: str,
    stock_code: str = "",
    modify_date: str = "",
    english_name: str = "",
    name_match_kind: str = "",
    name_similarity: float = 0.0,
    alias_source: str = "",
    alias_name: str = "",
) -> tuple[float, tuple[str, ...]]:
    score = 0.0
    evidence: list[str] = []
    query_key = _company_key(query)
    expanded_query_key = _company_key(_latin_acronym_korean(query))
    candidate_key = _company_key(candidate_name)
    english_key = _company_key(english_name)
    match_kind = name_match_kind if name_match_kind in MATCH_KIND_PRIORITY else ""
    similarity = (
        min(1.0, max(0.0, float(name_similarity)))
        if isinstance(name_similarity, (int, float)) and math.isfinite(name_similarity)
        else 0.0
    )
    if match_kind == "exact_id":
        score += 0.64
        evidence.append("입력한 DART 고유번호 또는 종목코드가 정확히 일치합니다")
    elif match_kind == "exact_name" or (query_key and query_key == candidate_key):
        score += 0.62
        evidence.append(
            "입력한 회사명이 DART 영문 정식명칭과 일치합니다"
            if english_key and query_key == english_key
            else "입력한 회사명과 DART 정식명칭이 일치합니다"
        )
    elif match_kind == "legal_suffix":
        score += 0.54
        evidence.append("영문 법인격 접미사를 제외한 DART 정식명칭이 일치합니다")
    elif match_kind == "spacing":
        score += 0.60
        evidence.append("공백을 정리한 회사명이 DART 정식명칭과 일치합니다")
    elif match_kind == "prefix":
        # 등록명이 입력한 이름으로 «시작»한다(당근->당근마켓). 법인 접미사·계열
        # 표기를 생략한 입력의 주 경로다. 분기가 없으면 아래 낙하 분기(0.44)로
        # 떨어져 오타(trigram)보다도 뒤로 밀린다 — 그때 화면 순서가
        # `dart_identity.MATCH_KIND_PRIORITY`와 정반대가 된다.
        #
        # 자리는 legal_suffix(0.54) «바로 아래»다. 이 값을 더 올려 약어 종류
        # (acronym_token 0.56) 위로 두면, 종목코드 가점(+0.08)이 붙은 부분 일치
        # 후보가 가점 없는 «완전 일치» 후보를 앞질러 버린다(2026-09-10 실측:
        # 0.58로 두자 "넥슨" 1위가 exact_name 넥슨 -> prefix 넥슨게임즈로 뒤집혔다).
        # acronym_token/acronym_reading이 legal_suffix보다 높은 것은 이 변경 전부터
        # 있던 어긋남이라 여기서 건드리지 않는다.
        score += 0.53
        evidence.append("DART 정식명칭이 입력한 회사명으로 시작합니다")
    elif match_kind == "substring":
        # 등록명 «안쪽»에 입력한 이름이 들어 있다(올리브영->씨제이올리브영).
        # 시작 위치가 아니라 우연한 겹침일 여지가 prefix보다 크므로 한 칸 아래에
        # 두되, 오타 유사도(trigram 최대 0.52)보다는 위에 둔다.
        score += 0.525
        evidence.append("DART 정식명칭 안에 입력한 회사명이 들어 있습니다")
    elif match_kind == "acronym_token":
        score += 0.56
        evidence.append("DART 영문 정식명칭에 입력한 약어가 독립된 이름으로 적혀 있습니다")
    elif match_kind == "acronym_reading":
        score += 0.56
        evidence.append("입력한 영문 약어의 한글 읽기와 DART 정식명칭이 일치합니다")
    elif match_kind == "acronym_cross_script":
        # 공식 원문에 실제로 적힌 대문자 약어의 역별칭이다. 직접 법인명
        # 일치보다 낮게 두어 정확한 동명 입력의 의도를 덮어쓰지 않는다.
        score += 0.52
        evidence.append("DART 영문 정식명칭의 공식 약어가 입력한 이름 일부와 일치합니다")
        if len(_tokens(query)) >= 2:
            evidence.append(
                "나머지 입력어까지 같은 법인명이라는 뜻은 아니므로 "
                "주소·홈페이지를 확인하세요"
            )
    elif match_kind == "token":
        score += 0.48
        evidence.append("입력한 회사명 토큰이 DART 정식명칭과 일치합니다")
    elif match_kind == "trigram":
        score += 0.34 + 0.18 * similarity
        evidence.append("긴 회사명의 철자 유사도가 높습니다(오타 가능성)")
    elif query_key and query_key in {candidate_key, english_key}:
        score += 0.62
        evidence.append("입력한 회사명과 후보명이 일치합니다")
    elif _latin_acronym_token_match(query, candidate_name) or _latin_acronym_token_match(
        query, english_name
    ):
        score += 0.56
        evidence.append("영문 법인명에 입력한 약어가 독립된 이름으로 적혀 있습니다")
    elif expanded_query_key and expanded_query_key in {candidate_key, english_key}:
        score += 0.56
        evidence.append("영문 약어의 한글 읽기와 후보명이 일치합니다")
    elif query_key and candidate_key and (
        query_key in candidate_key or candidate_key in query_key
    ):
        score += 0.44
        evidence.append("입력한 회사명이 후보명에 포함됩니다")
    elif expanded_query_key and expanded_query_key in candidate_key:
        score += 0.44
        evidence.append("영문 약어의 한글 읽기가 후보명에 포함됩니다")
    else:
        query_tokens = _tokens(query)
        candidate_tokens = _tokens(candidate_name)
        overlap = query_tokens & candidate_tokens
        if overlap:
            score += min(0.32, 0.16 * len(overlap))
            evidence.append("회사명 토큰이 겹칩니다: " + ", ".join(sorted(overlap)[:3]))

    if alias_source == ALIAS_SOURCE_AI and evidence:
        # ★ 여기까지 쌓인 근거는 «이름 비교» 한 줄뿐이고, 그 비교에 쓴 이름은
        #   사용자가 적은 말이 아니라 AI가 추정한 정식명이다(호출부가 query에
        #   alias_name을 넣는다). 그대로 두면 「입력한 회사명과 …일치합니다」가
        #   되어 사용자가 적지도 않은 이름의 일치를 확신 근거로 보게 된다.
        #   점수는 바꾸지 않는다 — 바꾸는 것은 «무엇이 맞았는지»의 설명뿐이다.
        evidence[0] = _ai_alias_evidence(alias_name)

    domain_key = _domain_key(homepage)
    ascii_query = "".join(ch for ch in query_key if ch.isascii() and ch.isalnum())
    if ascii_query and len(ascii_query) >= 2 and ascii_query in domain_key:
        score += 0.12
        evidence.append("공개 홈페이지 도메인에 입력한 영문명이 포함됩니다")
    elif homepage:
        score += 0.02
        evidence.append("공개 홈페이지 주소가 제공되었습니다")

    address_strength = address_match_strength(address_hint, address)
    if address_strength:
        score += ADDRESS_SCORE_BY_STRENGTH[address_strength]
        address_overlap = _address_tokens(address_hint) & _address_tokens(address)
        evidence.append("주소가 겹칩니다: " + ", ".join(sorted(address_overlap)[:3]))

    if re.fullmatch(r"\d{6}", stock_code):
        score += 0.08
        evidence.append(f"DART 종목코드 {stock_code}가 있는 상장 법인입니다")
    if re.fullmatch(r"\d{8}", modify_date):
        # 갱신일 자체가 현재 법인임을 보장하지는 않는다. 다만 같은 이름 후보끼리
        # 비교할 때 오래된 폐업·변경 전 레코드보다 최근 정비된 레코드를 앞에 둔다.
        year = int(modify_date[:4])
        score += max(0.0, min(0.03, (year - 2000) * 0.0012))
        evidence.append(
            f"DART 법인목록 정보가 {modify_date[:4]}-{modify_date[4:6]}-"
            f"{modify_date[6:]}에 갱신되었습니다"
        )
    return min(1.0, score / MAX_CANDIDATE_SCORE_SUM), tuple(evidence)


def score_business_candidate(
    *,
    query: str,
    address_hint: str,
    candidate_name: str,
    address: str,
    homepage: str,
    stock_code: str = "",
    modify_date: str = "",
    english_name: str = "",
    name_match_kind: str = "",
    name_similarity: float = 0.0,
    alias_source: str = "",
    alias_name: str = "",
) -> tuple[float, tuple[str, ...]]:
    """공급자와 resolver가 공유하는 후보 점수·표시 근거 계약.

    ``alias_source``/``alias_name``은 «AI가 추정한 정식명으로 찾아온 후보인가»를
    표시 근거에만 반영한다. 점수는 두 값과 무관하게 같다. 기본값은 결정적
    경로(빈 문자열)라, 값을 넘기지 않는 호출부는 예전과 똑같이 동작한다.
    """
    return _score(
        query=query,
        address_hint=address_hint,
        candidate_name=candidate_name,
        address=address,
        homepage=homepage,
        stock_code=stock_code,
        modify_date=modify_date,
        english_name=english_name,
        name_match_kind=name_match_kind,
        name_similarity=name_similarity,
        alias_source=alias_source,
        alias_name=alias_name,
    )


# ── 화면 전용 일치 근거 요약 칩 ──────────────────────────
# 후보 수집·점수 로직을 바꾸지 않는 표시용 매핑이다. 계산한 사실만 요약하고,
# 계산하지 않았거나 비교 재료가 없는 항목은 '불확실'로 정직하게 말한다.
CHIP_TONE_OK = "ok"
CHIP_TONE_PART = "part"
CHIP_TONE_NO = "no"
CHIP_TONE_UNKNOWN = "unknown"

_CHIP_ID_EXACT = "식별번호 일치"
_CHIP_NAME_EXACT = "법인명 일치"
_CHIP_NAME_PARTIAL = "법인명 부분 일치"
#: 사용자가 적은 이름이 아니라 «AI가 추정한 정식명»이 맞았다는 뜻. 초록(확신)
#: 칩을 주지 않는다 — 이 화면의 존재 이유가 사람이 확인하는 것이기 때문이다.
_CHIP_NAME_AI_ALIAS = "AI 추정명 일치"
_CHIP_NAME_SIMILAR = "법인명 유사"
_CHIP_NAME_UNKNOWN = "법인명 불확실"
_CHIP_ADDRESS_MATCH = "주소 일치"
_CHIP_ADDRESS_MISMATCH = "주소 불일치"
_CHIP_ADDRESS_UNKNOWN = "주소 불확실"

_PARTIAL_MATCH_KINDS = frozenset(
    {
        "prefix",
        "substring",
        "acronym_token",
        "acronym_reading",
        "acronym_cross_script",
        "token",
    }
)


@dataclass(frozen=True)
class CandidateDisplayChip:
    """후보 목록 행에 보여줄 일치 근거 요약 한 개."""

    tone: str
    label: str


def _name_chip(candidate: BusinessCandidate, query: str) -> CandidateDisplayChip:
    """이름 비교 결과를 _score와 같은 결정 규칙으로 칩 한 개로 요약한다."""
    match_kind = (
        candidate.name_match_kind
        if candidate.name_match_kind in MATCH_KIND_PRIORITY
        else ""
    )
    query_key = _company_key(query)
    candidate_key = _company_key(candidate.candidate_name)
    english_key = _company_key(candidate.english_name)
    expanded_query_key = _company_key(_latin_acronym_korean(query))

    if candidate.alias_source == ALIAS_SOURCE_AI:
        # ★ 이 후보는 사용자가 적은 말이 아니라 «AI가 추정한 정식명»으로 찾았다.
        #   여기서 초록 「법인명 일치」를 주면 사용자가 적지도 않은 이름의 일치를
        #   가장 강한 확신 신호로 보게 되고, 「AI는 회사를 확정하지 않는다」는
        #   설계가 화면에서 무너진다. 종류가 무엇이든 확신 칩을 주지 않는다.
        return CandidateDisplayChip(CHIP_TONE_PART, _CHIP_NAME_AI_ALIAS)
    if match_kind == "exact_id":
        return CandidateDisplayChip(CHIP_TONE_OK, _CHIP_ID_EXACT)
    if match_kind in {"exact_name", "legal_suffix"} or (
        query_key and query_key in {candidate_key, english_key}
    ):
        return CandidateDisplayChip(CHIP_TONE_OK, _CHIP_NAME_EXACT)
    if match_kind == "trigram":
        return CandidateDisplayChip(CHIP_TONE_PART, _CHIP_NAME_SIMILAR)
    if match_kind in _PARTIAL_MATCH_KINDS:
        return CandidateDisplayChip(CHIP_TONE_PART, _CHIP_NAME_PARTIAL)
    if _latin_acronym_token_match(query, candidate.candidate_name) or (
        candidate.english_name
        and _latin_acronym_token_match(query, candidate.english_name)
    ):
        return CandidateDisplayChip(CHIP_TONE_PART, _CHIP_NAME_PARTIAL)
    if expanded_query_key and expanded_query_key in {candidate_key, english_key}:
        return CandidateDisplayChip(CHIP_TONE_PART, _CHIP_NAME_PARTIAL)
    if query_key and candidate_key and (
        query_key in candidate_key or candidate_key in query_key
    ):
        return CandidateDisplayChip(CHIP_TONE_PART, _CHIP_NAME_PARTIAL)
    if expanded_query_key and candidate_key and expanded_query_key in candidate_key:
        return CandidateDisplayChip(CHIP_TONE_PART, _CHIP_NAME_PARTIAL)
    if _tokens(query) & _tokens(candidate.candidate_name):
        return CandidateDisplayChip(CHIP_TONE_PART, _CHIP_NAME_PARTIAL)
    return CandidateDisplayChip(CHIP_TONE_UNKNOWN, _CHIP_NAME_UNKNOWN)


def screen_sort_key(
    item: BusinessCandidate,
) -> tuple[float, int, bool, int, str, str]:
    """사용자가 보는 후보 순서를 정하는 유일한 키.

    ``dart_identity._match_sort_key``가 «어떤 15건이 살아남는지»를 정한다면 이
    키는 «그중 어느 3장이 보이는지»를 정한다. 두 키가 어긋나면 matcher가 정한
    의도가 화면에 도달하지 않으므로, matcher의 길이 동점 규칙을 여기서도 쓴다.
    """
    return (
        -float(item.score),
        # prefix/substring 은 정의상 같은 종류로 여러 후보가 묶인다("아시아나"가
        # 아시아나항공·아시아나IDT·아시아나에어포트를 함께 침). matcher와 같은
        # 규칙으로 «등록명이 짧은 = 입력에 더 가까운» 쪽을 앞세운다. 다른 종류는
        # 0을 반환해 기존 동점 처리 순서를 그대로 둔다.
        len(item.candidate_name) if item.name_match_kind in LENGTH_TIEBREAK_KINDS else 0,
        not bool(item.stock_code),
        -(int(item.modify_date) if item.modify_date else 0),
        item.candidate_name,
        item.address,
    )


def _address_chip(candidate: BusinessCandidate, address_hint: str) -> CandidateDisplayChip:
    """주소 토큰 비교를 칩 한 개로 요약한다. 비교 재료가 없으면 '불확실'이다."""
    hint_tokens = _address_tokens(address_hint)
    candidate_tokens = _address_tokens(candidate.address)
    if not hint_tokens or not candidate_tokens:
        return CandidateDisplayChip(CHIP_TONE_UNKNOWN, _CHIP_ADDRESS_UNKNOWN)
    if hint_tokens & candidate_tokens:
        return CandidateDisplayChip(CHIP_TONE_OK, _CHIP_ADDRESS_MATCH)
    return CandidateDisplayChip(CHIP_TONE_NO, _CHIP_ADDRESS_MISMATCH)


def candidate_match_chips(
    candidate: BusinessCandidate, *, query: str, address_hint: str
) -> tuple[CandidateDisplayChip, ...]:
    """후보 목록 화면 전용 일치 근거 칩. 수집·점수·순서에는 영향을 주지 않는다."""
    return (_name_chip(candidate, query), _address_chip(candidate, address_hint))


def anonymous_rate_key(*parts: str) -> str:
    """IP·User-Agent 원문을 남기지 않는 프로세스 한정 rate-limit 열쇠."""
    material = "\x1f".join(str(part or "")[:512] for part in parts).encode(
        "utf-8", errors="replace"
    )
    return hmac.new(_RATE_SECRET, material, hashlib.sha256).hexdigest()


def canonical_candidate_name(value: object) -> str:
    """후보 선택 wire 값과 DART 재조회에 공통으로 쓰는 유일한 이름 직렬화."""
    return _plain_text(value, MAX_NAME_CHARS)


def canonical_provider_name(value: object) -> str:
    """후보 공급자 wire 값의 유일한 직렬화."""
    return _plain_text(value, MAX_SOURCE_LABEL_CHARS)


def canonical_candidate_ref(value: object) -> str:
    """후보 공급자의 불투명 식별자를 선택 서명에 넣을 안전한 문자열로 만든다."""
    return _plain_text(value, MAX_SOURCE_LABEL_CHARS)


def candidate_selection_token(
    *,
    binding: str,
    original_company: str,
    job: str,
    address_hint: str,
    candidate_name: str,
    provider_name: str,
    candidate_ref: str = "",
    now: int | None = None,
) -> str:
    """후보 선택 hidden field를 짧게 서명한다.

    후보 원문은 서버에 저장하지 않는다. 대신 현재 프로세스 비밀과 요청 권한 binding에
    묶인 5분 토큰을 만들어, 사용자가 후보명을 임의 회사로 바꿔 공유 범위를 우회하지
    못하게 한다. 서버가 재시작되면 안전하게 무효가 된다.
    """
    issued = int(time.time() if now is None else now)
    payload = "\x1f".join(
        (
            str(issued),
            str(binding or "")[:512],
            _plain_text(original_company, MAX_NAME_CHARS),
            _plain_text(job, MAX_NAME_CHARS),
            _plain_text(address_hint, MAX_ADDRESS_CHARS),
            _plain_text(candidate_name, MAX_NAME_CHARS),
            _plain_text(provider_name, MAX_SOURCE_LABEL_CHARS),
            canonical_candidate_ref(candidate_ref),
        )
    ).encode("utf-8", errors="strict")
    signature = hmac.new(_SELECTION_SECRET, payload, hashlib.sha256).digest()
    return f"{issued:x}.{base64.urlsafe_b64encode(signature).decode('ascii').rstrip('=')}"


def valid_candidate_selection_token(
    token: str,
    *,
    binding: str,
    original_company: str,
    job: str,
    address_hint: str,
    candidate_name: str,
    provider_name: str,
    candidate_ref: str = "",
    now: int | None = None,
    max_age_sec: int = CANDIDATE_ATTEMPT_TTL_SEC,
) -> bool:
    """서명·binding·내용·짧은 수명을 모두 만족할 때만 후보 선택을 인정한다."""
    try:
        issued_hex, _signature = str(token or "").split(".", 1)
        issued = int(issued_hex, 16)
    except (TypeError, ValueError):
        return False
    current = int(time.time() if now is None else now)
    if issued > current + 30 or current - issued > max_age_sec:
        return False
    expected = candidate_selection_token(
        binding=binding,
        original_company=original_company,
        job=job,
        address_hint=address_hint,
        candidate_name=candidate_name,
        provider_name=provider_name,
        candidate_ref=candidate_ref,
        now=issued,
    )
    return hmac.compare_digest(expected, str(token or ""))


def _claim_rate(rate_key: str, now: float) -> bool:
    with _RATE_LOCK:
        if not budget_logic.rate_ok(
            _RATE_HISTORY,
            rate_key,
            now,
            window_sec=RATE_WINDOW_SEC,
            max_runs=RATE_MAX_SEARCHES,
        ):
            return False
        budget_logic.record_start(_RATE_HISTORY, rate_key, now)
        return True


def _call_once(
    provider: BusinessCandidateProvider,
    company: str,
    address_hint: str,
    alias_ask: Callable[[str], str] | None = None,
) -> Sequence[RawBusinessCandidate]:
    # Thread timeout은 응답을 기다리는 상한이다. 공급자 구현도 전달받은 timeout을
    # 네트워크 연결·읽기 양쪽에 적용해야 한다.
    provider_limit = max(
        1,
        min(
            MAX_RAW_CANDIDATES,
            int(getattr(provider, "max_results", MAX_CANDIDATES)),
        ),
    )
    provider_timeout_sec = PROVIDER_TIMEOUT_SEC
    try:
        requested_timeout = float(
            getattr(provider, "resolution_timeout_sec", PROVIDER_TIMEOUT_SEC)
        )
        if math.isfinite(requested_timeout) and requested_timeout > 0:
            provider_timeout_sec = min(
                requested_timeout, MAX_PROVIDER_TIMEOUT_SEC
            )
    except (TypeError, ValueError, OverflowError):
        provider_timeout_sec = PROVIDER_TIMEOUT_SEC

    # 콜백은 «지금 전역 이름이 가리키는 세마포어»가 아니라 실제로 획득한
    # 인스턴스에 자리를 돌려줘야 한다. 시험 격리나 런타임 재설정 중 전역이
    # 교체돼도 엉뚱한 세마포어를 풀어 상한을 훼손하지 않는다.
    worker_slots = _PROVIDER_WORKER_SLOTS
    if not worker_slots.acquire(blocking=False):
        raise ProviderWorkerUnavailable("회사 후보 worker가 모두 사용 중입니다")
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

    def invoke() -> Sequence[RawBusinessCandidate]:
        search_kwargs: dict[str, object] = {
            "company": company,
            "address_hint": address_hint,
            "limit": provider_limit,
            "timeout_sec": provider_timeout_sec,
        }
        # ★ 번역 ask는 «받겠다고 표시한» 어댑터에만 넘긴다. 계약에 없는 공급자에
        #   억지로 밀어 넣으면 TypeError가 나서 후보 검색 자체가 실패한다.
        if alias_ask is not None and bool(
            getattr(provider, "accepts_alias_ask", False)
        ):
            search_kwargs["alias_ask"] = alias_ask
        return provider.search(**search_kwargs)

    try:
        # paid worker 안에서 다시 만든 thread에도 요청 로컬 attempt/예산 문맥을
        # 명시적으로 복사한다. ThreadPoolExecutor는 contextvars를 자동 전파하지
        # 않으므로 그대로 submit하면 provider 직전 gateway가 문맥 없음으로 막힌다.
        request_context = contextvars.copy_context()
        future = executor.submit(request_context.run, invoke)
        # 완료·예외·시작 전 취소 모두 callback을 정확히 한 번 부른다. timeout 뒤
        # underlying thread가 계속 돌면 완료 때까지 슬롯을 붙잡아 thread 폭증을 막는다.
        future.add_done_callback(lambda _future: worker_slots.release())
        return future.result(timeout=provider_timeout_sec)
    except BaseException:
        # submit 자체가 실패했다면 invoke의 finally가 슬롯을 돌려줄 수 없다.
        if "future" not in locals():
            worker_slots.release()
        raise
    finally:
        # timeout 뒤 공급자 구현이 멈추지 않더라도 HTTP 응답을 붙잡지 않는다. Python
        # thread를 강제 종료할 수는 없으므로 실제 어댑터도 전달받은 timeout을 지켜야 한다.
        executor.shutdown(wait=False, cancel_futures=True)


def _timeboxed_rerank_ask(ask: ai_rerank.RerankAsk) -> ai_rerank.RerankAsk:
    """AI 재정렬 응답을 공급자 호출과 «같은» worker·시간 상한 안에 가둔다.

    재정렬은 공급자 호출이 끝난 뒤에 돌아 같은 슬롯을 다시 빌린다. 슬롯이 없으면
    기다리지 않고 즉시 포기해, 보조 단계가 후보 화면을 붙잡지 않게 한다.
    """

    def bounded(prompt: str) -> str:
        worker_slots = _PROVIDER_WORKER_SLOTS
        if not worker_slots.acquire(blocking=False):
            raise ProviderWorkerUnavailable("회사 후보 재정렬 worker가 모두 사용 중입니다")
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        def invoke() -> str:
            return str(ask(prompt) or "")

        try:
            # ThreadPoolExecutor는 contextvars를 자동 전파하지 않는다. 요청 로컬
            # attempt/예산 문맥을 명시적으로 복사해야 하위 gateway가 막지 않는다.
            request_context = contextvars.copy_context()
            future = executor.submit(request_context.run, invoke)
            future.add_done_callback(lambda _future: worker_slots.release())
            return future.result(timeout=AI_RERANK_TIMEOUT_SEC)
        except BaseException:
            if "future" not in locals():
                worker_slots.release()
            raise
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

    return bounded


#: AI가 옮긴 정식명으로 찾아온 후보의 표식. 결정적 경로는 빈 문자열이다.
ALIAS_SOURCE_AI: str = "ai"
#: 후보가 실을 수 있는 «어떻게 들어왔는지» 값. 그 밖의 값은 빈 문자열로 낮춘다.
ALIAS_SOURCE_VALUES: frozenset[str] = frozenset({"", ALIAS_SOURCE_AI})


def _alias_status(channel: "_AliasAskChannel | None") -> str:
    """번역 단계를 열었을 때만 그 결과를 옮긴다. 열지 않았으면 빈 문자열이다."""

    return "" if channel is None else channel.status


class _AliasAskChannel:
    """번역 ask를 시간 상한 안에 가두고, 어댑터가 알려 준 결과를 받아 둔다.

    이 호출은 공급자 검색 «안에서» 일어나므로 이미 worker 자리를 하나 쥐고 있다.
    여기서 자리를 또 빌리면 겹쳐 도는 다른 검색의 자리를 잠식하므로 빌리지 않고,
    시간 상한만 다시 건다.
    """

    def __init__(self, ask: Callable[[str], str]) -> None:
        self._ask = ask
        self.status = ""

    def report(self, status: object) -> None:
        """어댑터가 번역 단계 결과를 되돌려 주는 자리. 응답 원문은 받지 않는다."""
        reported = _plain_text(status, MAX_SOURCE_LABEL_CHARS)
        # 어댑터가 아는 사유 코드만 관측 칸에 들인다. 자유 문자열을 그대로 받으면
        # 공급자 응답 조각이 로그·화면 경계로 새어 나갈 수 있다.
        self.status = (
            reported if reported in alias_resolution.ALIAS_STATUS_VALUES else ""
        )
        inner = getattr(self._ask, "report", None)
        if callable(inner):
            inner(self.status)

    def __call__(self, prompt: str) -> str:
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)

        def invoke() -> str:
            return str(self._ask(prompt) or "")

        try:
            # ThreadPoolExecutor는 contextvars를 자동 전파하지 않는다. 요청 로컬
            # attempt/예산 문맥을 명시적으로 복사해야 하위 gateway가 막지 않는다.
            request_context = contextvars.copy_context()
            future = executor.submit(request_context.run, invoke)
            return future.result(timeout=AI_ALIAS_TIMEOUT_SEC)
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


def resolve_candidates(
    provider: BusinessCandidateProvider | None,
    *,
    company: str,
    address_hint: str,
    rate_key: str,
    now: float | None = None,
    allow_paid_provider: bool = False,
    rerank_ask: Callable[[str], str] | None = None,
    alias_ask: Callable[[str], str] | None = None,
) -> CandidateResolution:
    """공급자를 최대 한 번 호출하고, 안전한 상위 후보만 돌려준다.

    반환 후보는 어디까지나 사용자가 선택할 목록이다. 이 함수에는 자동 확정 경로가 없다.
    ``rerank_ask``를 주면 결정적 순위가 갈리지 않을 때만 AI에 순서를 한 번 물어본다.
    ``alias_ask``를 주면 이름이 겹치는 후보를 한 건도 못 찾았을 때만 AI에 정식
    법인명을 한 번 물어 그 이름으로 공급자가 다시 찾는다. 공급자 호출 수는 그대로 1회다.
    """
    if provider is None:
        return CandidateResolution(ResolutionStatus.UNCONFIGURED)
    provider_name = canonical_provider_name(getattr(provider, "provider_name", ""))
    if bool(getattr(provider, "costs_money", True)) and not allow_paid_provider:
        # 새 공급자 비용을 기존 DART/AI 예산 밖에서 몰래 쓰지 않는다.
        return CandidateResolution(
            ResolutionStatus.UNCONFIGURED, provider_name=provider_name
        )
    if PROVIDER_CALLS_PER_RESOLUTION != 1:
        raise RuntimeError("후보 공급자 호출 상한은 1이어야 합니다")
    if not _claim_rate(rate_key, time.monotonic() if now is None else now):
        return CandidateResolution(
            ResolutionStatus.RATE_LIMITED, provider_name=provider_name
        )

    safe_company = _plain_text(company, MAX_NAME_CHARS)
    safe_address_hint = _plain_text(address_hint, MAX_ADDRESS_CHARS)
    alias_channel = None if alias_ask is None else _AliasAskChannel(alias_ask)
    try:
        raw_candidates = _call_once(
            provider, safe_company, safe_address_hint, alias_ask=alias_channel
        )
    except (concurrent.futures.TimeoutError, ProviderTimedOut):
        logger.warning("회사 후보 검색 시간초과 provider=%s", provider_name)
        return CandidateResolution(
            ResolutionStatus.TIMED_OUT,
            provider_called=True,
            provider_name=provider_name,
            alias_status=_alias_status(alias_channel),
        )
    except ProviderWorkerUnavailable:
        logger.warning("회사 후보 검색 worker 부족 provider=%s", provider_name)
        return CandidateResolution(
            ResolutionStatus.RATE_LIMITED,
            provider_called=False,
            provider_name=provider_name,
        )
    except ProviderRateLimited:
        return CandidateResolution(
            ResolutionStatus.RATE_LIMITED,
            provider_called=True,
            provider_name=provider_name,
            alias_status=_alias_status(alias_channel),
        )
    except Exception as error:  # noqa: BLE001 — 공급자 원문/예외 본문은 로그에 남기지 않는다
        # ★ 예외를 통째로 삼켜서 화면도 로그도 원인을 못 말했다.
        #   «클래스 이름과 발생 위치»만 남긴다. 응답 본문·예외 메시지는 남기지 않는다
        #   (그 안에 공급자 원문이 섞일 수 있다 — 원래 주석의 의도를 지킨다).
        _마지막 = traceback.extract_tb(error.__traceback__)[-1:] or None
        logger.warning(
            "회사 후보 검색 실패 provider=%s kind=%s at=%s",
            provider_name,
            type(error).__name__,
            f"{_마지막[0].filename.rsplit('/', 1)[-1]}:{_마지막[0].lineno}" if _마지막 else "unknown",
        )
        return CandidateResolution(
            ResolutionStatus.FAILED,
            provider_called=True,
            provider_name=provider_name,
            local_profile_enrichment_failed=bool(
                getattr(error, "local_profile_enrichment_failed", False)
            ),
            alias_status=_alias_status(alias_channel),
        )

    ranked: list[BusinessCandidate] = []
    seen: set[tuple[str, str]] = set()
    for raw in list(raw_candidates or ())[:MAX_RAW_CANDIDATES]:
        if not isinstance(raw, RawBusinessCandidate):
            continue
        candidate_name = _plain_text(raw.candidate_name, MAX_NAME_CHARS + 1)
        if len(candidate_name) > MAX_NAME_CHARS:
            # 잘라 낸 다른 법인명을 선택하게 하지 않고 후보 하나만 버린다.
            continue
        address = _plain_text(raw.address, MAX_ADDRESS_CHARS)
        homepage = _public_http_url(raw.homepage)
        source_url = _public_http_url(raw.source_url)
        source_label = _plain_text(raw.source_label, MAX_SOURCE_LABEL_CHARS)
        provider_name = _plain_text(raw.provider_name, MAX_SOURCE_LABEL_CHARS)
        candidate_ref = canonical_candidate_ref(raw.candidate_ref)
        stock_code = _plain_text(raw.stock_code, 7)
        modify_date = _plain_text(raw.modify_date, 9)
        english_name = _plain_text(raw.english_name, MAX_NAME_CHARS)
        name_match_kind = _plain_text(raw.name_match_kind, 32)
        if name_match_kind not in MATCH_KIND_PRIORITY:
            name_match_kind = ""
        try:
            name_similarity = float(raw.name_similarity)
        except (TypeError, ValueError, OverflowError):
            name_similarity = 0.0
        if not math.isfinite(name_similarity):
            name_similarity = 0.0
        name_similarity = min(1.0, max(0.0, name_similarity))
        alias_name = _plain_text(raw.alias_name, MAX_NAME_CHARS)
        alias_source = _plain_text(raw.alias_source, MAX_SOURCE_LABEL_CHARS)
        if alias_source not in ALIAS_SOURCE_VALUES:
            # 공급자가 모르는 값을 적어도 화면 표식으로 승격시키지 않는다.
            alias_source = ""
        if not alias_source:
            alias_name = ""
        if re.fullmatch(r"\d{6}", stock_code) is None:
            stock_code = ""
        if re.fullmatch(r"\d{8}", modify_date) is None:
            modify_date = ""
        if provider_name == "DART" and re.fullmatch(r"\d{8}", candidate_ref) is None:
            # A DART name without its official key is not a selectable identity.
            continue
        attributions: list[tuple[str, str]] = []
        for pair in list(raw.attributions or ())[:MAX_CANDIDATES]:
            if not isinstance(pair, (tuple, list)) or len(pair) != 2:
                continue
            label = _plain_text(pair[0], MAX_SOURCE_LABEL_CHARS)
            url = _public_http_url(pair[1])
            if label:
                attributions.append((label, url))
        if not candidate_name:
            continue
        dedupe_key = (
            ("DART", candidate_ref)
            if provider_name == "DART"
            else (_company_key(candidate_name), _company_key(address))
        )
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        score, evidence = score_business_candidate(
            # AI가 옮긴 정식명으로 찾아온 후보는 그 이름으로 잰다(공급자 쪽과 같은
            # 규칙). ⚠️ 종류에 따라 두 질의의 점수가 «다르다» — 2026-09-10 실측:
            # exact_name 은 0.5275로 같지만 spacing 은 0.5275 대 0.5112,
            # legal_suffix 는 0.5275 대 0.4624로 갈린다. 두 종류 모두
            # `ALIAS_STRONG_MATCH_KINDS`에 있어 이 경로로 실제로 들어온다.
            query=alias_name or safe_company,
            address_hint=safe_address_hint,
            candidate_name=candidate_name,
            address=address,
            homepage=homepage,
            stock_code=stock_code,
            modify_date=modify_date,
            english_name=english_name,
            name_match_kind=name_match_kind,
            name_similarity=name_similarity,
            # 점수에는 영향이 없고 표시 근거만 갈린다. 이 두 값이 없으면 화면이
            # 「입력한 회사명과 …일치합니다」로 사용자가 적지도 않은 이름을 근거로
            # 내세운다.
            alias_source=alias_source,
            alias_name=alias_name,
        )
        if score < MIN_CANDIDATE_SCORE:
            continue
        ranked.append(
            BusinessCandidate(
                candidate_name=candidate_name,
                address=address,
                homepage=homepage,
                source_label=source_label,
                source_url=source_url,
                provider_name=provider_name,
                attributions=tuple(attributions),
                score=score,
                evidence=evidence,
                candidate_ref=candidate_ref,
                stock_code=stock_code,
                modify_date=modify_date,
                english_name=english_name,
                name_match_kind=name_match_kind,
                name_similarity=name_similarity,
                alias_source=alias_source,
                alias_name=alias_name,
            )
        )

    ranked.sort(key=screen_sort_key)
    rerank_status = ""
    if rerank_ask is not None:
        # 상위 3개를 자르기 «전»에 순서를 다시 잡아야 밀려 있던 정답이 화면에 든다.
        ranked, rerank_status = ai_rerank.rerank_candidates(
            ranked,
            query=safe_company,
            address_hint=safe_address_hint,
            ask=_timeboxed_rerank_ask(rerank_ask),
        )

    selected = tuple(ranked[:MAX_CANDIDATES])
    return CandidateResolution(
        ResolutionStatus.OK if selected else ResolutionStatus.NO_MATCHES,
        selected,
        provider_called=True,
        provider_name=provider_name,
        rerank_status=rerank_status,
        alias_status=_alias_status(alias_channel),
    )
