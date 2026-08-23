"""보고서에 실리는 출처 목록 — 문장 뒤 [번호], 맨 아래에 목록.

★ 왜 필요한가
  지금은 화면이 실행 기록(runs.jsonl)에서 출처를 «역산»한다. 기록 형식이
  바뀌면 조용히 틀어진다. 이 파일은 보고서 자체에 실을 수 있는 출처
  자료구조와, 그것을 마크다운으로 쓰고(직렬화) 다시 읽는(파싱) 함수를 담는다.

  **쓰기(render_sources)와 읽기(parse_sources)는 사람이 보는 표시 필드를 왕복한다.**
  canonical의 source_id·URL·발행처·원문 위치는 Markdown이 아니라 Report JSON
  등록부가 보존한다 — 시험(`tests/test_sources.py`, `storage/tests`)으로 각각 증명한다.

정본: 확정/07_출력/2_규칙/01_배치와근거표기.md
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import unicodedata
import urllib.parse
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import date
from enum import Enum

from src.features.provenance.constants import OTHER_DATE_PREFIX, SOURCES_HEADER
from src.shared.official_ir import (
    dart_www_redirect_is_valid,
    official_ir_time_is_usable,
    safe_https_attachment_url,
)


def evidence_text_hash(text: str) -> str:
    """원문 조각의 공백·대소문자 차이를 제거한 SHA-256 식별자."""

    normalized = " ".join(str(text or "").split()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def exact_evidence_text_hash(text: str) -> str:
    """원문 문자열을 바꾸지 않고 UTF-8 바이트 그대로 계산한 SHA-256."""

    raw = str(text or "")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


_KNOWN_FILING_HOSTS = frozenset(
    {"dart.fss.or.kr", "opendart.fss.or.kr", "kind.krx.co.kr"}
)
_PROVENANCE_SEAL_ENV = "PROVENANCE_SEAL_SECRET"
_configured_seal_key = os.environ.get(_PROVENANCE_SEAL_ENV, "").encode("utf-8")
# 배포에서는 재시작 뒤 저장 보고서도 검증되도록 환경 비밀을 고정한다. 비어 있거나
# 너무 짧으면 프로세스 한정 키를 써서 최소한 직렬화 payload의 사후 조작은 막고,
# 재시작 뒤 기존 보고서는 안전하게 fail-closed 된다.
_PROVENANCE_SEAL_KEY = (
    _configured_seal_key
    if len(_configured_seal_key) >= 32
    else secrets.token_bytes(32)
)


def seal_key_is_persistent() -> bool:
    """재시작 뒤에도 같은 출처 도장을 검증할 운영 키가 설정됐는가."""

    return len(_configured_seal_key) >= 32


def _host_key(value: str) -> str:
    return str(value or "").strip().casefold().rstrip(".")


def _publisher_key(value: str) -> str:
    """표시용 공백·대소문자만 무시한 발행 법인 비교 키."""

    return re.sub(r"\s+", "", str(value or "")).casefold()


_URL_IN_EVIDENCE = re.compile(
    r"(?i)(?:https?://|www\.)[^\s<>\"'()\[\]{}]+"
)
_DART_PROFILE_ATTESTATION_KEYS = frozenset({"corp_code", "corp_name", "hm_url"})


def _safe_profile_homepage_host(value: object) -> str:
    """DART ``hm_url`` 값에서 공개 HTTPS로 승격 가능한 host만 읽는다."""

    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if (
        not raw
        or len(raw) > 2_048
        or "\\" in raw
        or any(ord(char) < 32 for char in raw)
    ):
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urllib.parse.urlsplit(raw)
        hostname = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii")
        port = parsed.port
    except (UnicodeError, TypeError, ValueError):
        return ""
    normalized_host = hostname.casefold()
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not normalized_host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or parsed.fragment
        or "%" in parsed.netloc
        or normalized_host == "localhost"
        or normalized_host.endswith(".localhost")
        or "." not in normalized_host
    ):
        return ""
    try:
        address = ipaddress.ip_address(normalized_host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return ""
    return normalized_host


def _hosts_in_domain_attestation_evidence(evidence: str) -> set[str]:
    """공시 원문 조각에 실제 URL로 적힌 host만 꺼낸다.

    단순히 회사명이나 ``공식 홈페이지``라는 문구가 있다는 이유로 도메인을
    신뢰하지 않는다. URL 형태가 아닌 임의 문자열도 도메인 소유 근거로 쓰지
    않는다.
    """

    raw_evidence = str(evidence or "").strip()
    try:
        profile = json.loads(raw_evidence)
    except (json.JSONDecodeError, TypeError, ValueError):
        profile = None
    else:
        # OpenDART 기업개황은 hm_url에 scheme을 생략하거나 legacy http를 돌려줄
        # 수 있다. 닫힌 최소 subset 전체가 정확할 때만 그 필드를 별도 해석한다.
        if (
            not isinstance(profile, dict)
            or set(profile) != _DART_PROFILE_ATTESTATION_KEYS
            or not all(isinstance(profile.get(key), str) for key in profile)
            or re.fullmatch(r"\d{8}", profile["corp_code"].strip()) is None
            or not profile["corp_name"].strip()
        ):
            return set()
        profile_host = _safe_profile_homepage_host(profile["hm_url"])
        return {profile_host} if profile_host else set()

    hosts: set[str] = set()
    for token in _URL_IN_EVIDENCE.findall(raw_evidence):
        candidate = token.rstrip(".,;:!?。，、")
        if candidate.casefold().startswith("www."):
            candidate = f"https://{candidate}"
        try:
            parsed = urllib.parse.urlparse(candidate)
        except ValueError:
            continue
        if parsed.scheme in {"http", "https"} and parsed.hostname:
            hosts.add(_host_key(parsed.hostname))
    return hosts


def _url_identity_is_bound(source: "Source") -> bool:
    """선언 host/domain/document_id가 실제 URL의 신원과 맞는지 검사한다."""

    try:
        parsed = urllib.parse.urlparse(source.url.strip())
    except ValueError:
        return False
    hostname = _host_key(parsed.hostname or "")
    declared_host = _host_key(source.host)
    if parsed.scheme not in {"https", "http"} or not hostname or not declared_host:
        return False
    if declared_host != hostname:
        return False
    if source.kind is SourceKind.NEWS:
        domain = _host_key(source.domain)
        if not domain or domain != hostname:
            return False
    if source.kind is SourceKind.FILING:
        if hostname not in _KNOWN_FILING_HOSTS:
            return False
        document_id = source.document_id.strip().casefold()
        decoded_url = urllib.parse.unquote(source.url).casefold()
        if not document_id or document_id not in decoded_url:
            return False
    return True


def _valid_iso_date(value: str) -> bool:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "").strip()):
        return False
    try:
        date.fromisoformat(str(value).strip())
    except ValueError:
        return False
    return True


class SourceKind(str, Enum):
    """출처의 종류. 렌더링 형식이 종류마다 다르다."""

    #: 감사보고서·사업보고서 등 전자공시 원본 자료
    FILING = "공시"
    #: 언론 보도
    NEWS = "뉴스"
    #: 그 밖의 자료 (날짜 메타데이터 없이 이름만 있는 경우)
    OTHER = "기타"


# ``source_type``은 사용자에게도 보이는 한국어 분류라 저장 호환성을 위해 문자열로
# 유지한다. 다만 출고 게이트는 ``공식``이라는 부분 문자열을 믿지 않고 아래의 닫힌
# 목록만 인정한다. 예를 들어 뉴스가 스스로 ``공식 분석 기사``라고 적어도 핵심
# 사실의 원문으로 승격되지 않는다.
_OFFICIAL_SOURCE_TYPES_BY_KIND: dict[SourceKind, frozenset[str]] = {
    SourceKind.FILING: frozenset(
        {
            "공식 공시",
            "공식 재무 api",
            "공식 공시·재무 api",
            "공식 계획",
            "규제기관 공식 자료",
            "공식 규제기관 자료",
            "비교사 공식 공시",
            "비교사 공식 재무 api",
        }
    ),
    SourceKind.OTHER: frozenset(
        {
            "회사 공식 ir",
            "공식 ir",
            "회사 공식 웹",
            "공식 웹",
            "회사 공식 자료",
            "공식 파트너 자료",
            "파트너 공식 자료",
            "규제기관 공식 자료",
            "공식 규제기관 자료",
        }
    ),
    SourceKind.NEWS: frozenset(),
}
_PROVENANCE_ROLES = frozenset({"citation", "attestation_only"})
OFFICIAL_WEB_CURRENT_MAX_AGE_DAYS = 400
_HISTORICAL_WEB_MARKER = re.compile(
    r"^(?:news|newsroom|press|media|notice|release|archive|blog|search|results?|find)",
    re.IGNORECASE,
)
_DOCUMENT_WEB_KEY = re.compile(
    r"^(?:id|idx|seq|no|article|post|board|bbs|view|q|s|query|"
    r"keywords?|search|term)$",
    re.IGNORECASE,
)
_DOCUMENT_WEB_SEGMENT = re.compile(
    r"^(?:article|post|board|bbs|view)(?:[-_].*)?$",
    re.IGNORECASE,
)
_SEARCH_WEB_MARKER = re.compile(r"^(?:search|results?|find)", re.IGNORECASE)
_SEARCH_WEB_QUERY_KEY = re.compile(
    r"^(?:q|s|query|keywords?|search|term)$",
    re.IGNORECASE,
)
_WEB_YEAR_MARKER = re.compile(r"(?<!\d)20\d{2}(?!\d)")
_OFFICIAL_IR_SOURCE_TYPES = frozenset({"회사 공식 ir", "공식 ir"})
_OFFICIAL_WEB_SOURCE_TYPES = frozenset(
    {"회사 공식 웹", "공식 웹", "회사 공식 자료"}
)


def source_type_is_official_ir(source_type: str) -> bool:
    """표시 변형을 포함한 닫힌 공식 IR source_type 판정."""

    return " ".join(str(source_type or "").split()).casefold() in (
        _OFFICIAL_IR_SOURCE_TYPES
    )


def source_type_is_official_web(source_type: str) -> bool:
    """현재성 계약을 적용하는 공식 HTML source_type 판정."""

    return " ".join(str(source_type or "").split()).casefold() in (
        _OFFICIAL_WEB_SOURCE_TYPES
    )


def _decoded_web_component(value: str) -> str:
    decoded = str(value or "")
    for _ in range(3):
        candidate = urllib.parse.unquote(decoded)
        if candidate == decoded:
            break
        decoded = candidate
    return unicodedata.normalize("NFKC", decoded).casefold()


def _historical_web_token(value: str) -> bool:
    normalized = _decoded_web_component(value)
    tokens = tuple(
        token
        for token in re.split(r"[^0-9a-z가-힣_-]+", normalized)
        if token
    )
    return any(
        _HISTORICAL_WEB_MARKER.match(token)
        or _DOCUMENT_WEB_SEGMENT.fullmatch(token)
        or _WEB_YEAR_MARKER.search(token)
        for token in tokens
    )


def official_web_url_requires_document_date(url: str) -> bool:
    """보도·문서형 공식 웹 URL인지 host/path/query를 반복 decode해 판정한다."""

    try:
        parsed = urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return True
    host_labels = tuple(
        label for label in _decoded_web_component(parsed.hostname or "").split(".")
        if label
    )
    path = _decoded_web_component(parsed.path).replace("\\", "/")
    if any(_historical_web_token(label) for label in host_labels):
        return True
    if _historical_web_token(path):
        return True
    query = _decoded_web_component(parsed.query)
    query_pairs = urllib.parse.parse_qsl(query, keep_blank_values=True)
    if any(
        _DOCUMENT_WEB_KEY.fullmatch(_decoded_web_component(key))
        or _historical_web_token(key)
        or _historical_web_token(value)
        for key, value in query_pairs
    ):
        return True
    return bool(
        query
        and not query_pairs
        and _historical_web_token(query)
    )


def official_web_url_is_search_result(url: str) -> bool:
    """검색 결과·snippet URL은 문서일 유무와 무관하게 공개 사실에서 뺀다."""

    try:
        parsed = urllib.parse.urlsplit(str(url or "").strip())
    except ValueError:
        return True
    host_path_tokens = tuple(
        token
        for token in re.split(
            r"[^0-9a-z가-힣_-]+",
            _decoded_web_component(
                f"{parsed.hostname or ''}/{parsed.path}"
            ),
        )
        if token
    )
    if any(_SEARCH_WEB_MARKER.match(token) for token in host_path_tokens):
        return True
    query = _decoded_web_component(parsed.query)
    return any(
        _SEARCH_WEB_QUERY_KEY.fullmatch(_decoded_web_component(key))
        for key, _value in urllib.parse.parse_qsl(query, keep_blank_values=True)
    )


def official_web_currentness_is_usable(
    *,
    source_type: str,
    url: str,
    published_at: str = "",
    disclosed_at: str = "",
    collected_at: str = "",
    reference_date: str = "",
) -> bool:
    """안정 페이지는 수집일, 역사 문서 경로는 검증 문서일로 현재성을 닫는다."""

    if not source_type_is_official_web(source_type):
        return True
    if official_web_url_is_search_result(url):
        return False
    requires_document_date = official_web_url_requires_document_date(url)
    document_date = str(published_at or disclosed_at or "").strip()
    collected_date = str(collected_at or "").strip()
    if not document_date:
        if requires_document_date:
            return False
        document_date = collected_date
    try:
        published = date.fromisoformat(document_date)
        reference = date.fromisoformat(
            str(reference_date or collected_date).strip()
        )
    except ValueError:
        return False
    age_days = (reference - published).days
    return 0 <= age_days <= OFFICIAL_WEB_CURRENT_MAX_AGE_DAYS


@dataclass(frozen=True)
class Source:
    """출처 목록 한 줄 — 문장 뒤 `[번호]`가 가리키는 실제 출처.

    ★ `number`는 **AI가 고른 조각 번호를 그대로 쓴다.** 여기서 새로 매기지
      않는다 (정본 §근거 표기 — 번호).
    """

    number: int
    kind: SourceKind
    label: str
    #: 공시일 (예: "2024-03-15"). 공시 자료일 때만 쓴다.
    disclosed_at: str = ""
    #: 우리가 수집한 날짜 (예: "2026-08-13"). 공시 자료일 때만 쓴다.
    collected_at: str = ""
    #: 보도일. 뉴스일 때 반드시 있어야 한다.
    published_at: str = ""
    #: 언론사 도메인 (예: "mk.co.kr"). 뉴스일 때 반드시 있어야 한다.
    domain: str = ""
    #: canonical(v3) 주장 장부가 참조하는 안정적인 ID. 조각 번호와 분리한다.
    source_id: str = ""
    #: 원문 표지·페이지에 적힌 실제 문서 제목. 없으면 레거시 ``label``을 쓴다.
    title: str = ""
    #: 실제 발행 주체. DART·KIND 같은 호스팅 기관과 구분한다.
    publisher: str = ""
    #: 원문을 호스팅하는 기관·사이트.
    host: str = ""
    #: 사용자가 직접 열 수 있는 원문 URL.
    url: str = ""
    #: 공시 접수번호·보고서 ID 등 원문 식별자.
    document_id: str = ""
    #: PDF 페이지·표 또는 웹 섹션처럼 주장을 찾을 수 있는 위치.
    location: str = ""
    #: 공식 공시·공식 IR·공식 웹·외부 분석 등 자료 분류.
    source_type: str = ""
    #: 실제·잠정·계획·현재·외부 추정 등 이 자료에서 쓴 사실 상태.
    fact_status: str = ""
    #: 이 원문을 실제 사용한 semantic section ID.
    used_in: list[str] = field(default_factory=list)
    #: 수집 단계에서 보존한 원문 조각·표 행의 정규화 SHA-256 목록.
    #: canonical FactRecord.state_evidence는 반드시 이 목록 중 하나와 일치해야 한다.
    evidence_hashes: list[str] = field(default_factory=list)
    #: 대소문자·공백까지 같은 원문을 요구하는 계약용 UTF-8 SHA-256 목록.
    #: 비어 있으면 legacy Source이고, 값이 있을 때만 저장 JSON과 HMAC에 포함한다.
    exact_evidence_hashes: list[str] = field(default_factory=list)
    #: 회사 공식 웹·IR처럼 ``OTHER``인 원문의 도메인을 확인해 준 독립 공시
    #: Source ID. 자기 자신이나 또 다른 자기선언 웹 자료는 쓸 수 없다.
    domain_attestation_source_id: str = ""
    #: 위 공시 원문에서 회사 홈페이지 URL이 실제로 적힌 보존 조각.
    #: 이 문자열의 해시가 attestation Source.evidence_hashes에 있어야 한다.
    domain_attestation_evidence: str = ""
    #: 수집 경계가 Source 신원·원문 해시를 함께 잠근 서버 HMAC. 공개 보고서가
    #: evidence_hashes를 스스로 고쳐 쓰는 것을 막으며 렌더러에는 표시하지 않는다.
    provenance_seal: str = ""
    #: ``attestation_only``는 다른 공식 Source의 신원·도메인을 증명하는 내부
    #: provenance 의존성이다. 검증·저장에는 남지만 사용자 출처 부록에는 표시하지 않는다.
    provenance_role: str = "citation"
    #: 공식 IR에 표시된 닫힌 보고기간(YYYY-Qn/Hn/FY 또는 ISO 범위).
    #: 끝에 둔다. 이전 코드의 positional Source 생성자 순서를 바꾸지 않는다.
    reporting_period: str = ""
    #: 공식 상세페이지가 직접 건 PDF를 실제 다운로드한 최종 HTTPS URL.
    #: 끝에 둬 이전 positional Source 생성자 순서를 보존한다.
    attachment_url: str = ""
    #: DART apex에서 실제 ``www`` 최종 URL까지 제한 probe가 확인한 결속.
    domain_redirect_verification: str = ""
    domain_redirect_from_host: str = ""
    domain_redirect_to_host: str = ""

    @property
    def is_valid(self) -> bool:
        """뉴스는 보도일과 언론사 도메인이 반드시 있어야 한다 (정본 §근거 표기 — 뉴스)."""
        if self.number <= 0 or not self.label.strip():
            return False
        if self.kind is SourceKind.NEWS:
            return bool(self.published_at.strip()) and bool(self.domain.strip())
        return True

    @property
    def is_canonical_valid(self) -> bool:
        """v3 출처표의 필수 신원·위치·상태가 모두 있는가."""

        date = self.published_at or self.disclosed_at or self.collected_at
        return all(
            (
                self.number > 0,
                bool(self.source_id.strip()),
                bool((self.title or self.label).strip()),
                bool(self.publisher.strip()),
                _valid_iso_date(date),
                all(
                    not candidate.strip() or _valid_iso_date(candidate)
                    for candidate in (
                        self.published_at,
                        self.disclosed_at,
                        self.collected_at,
                    )
                ),
                bool(self.host.strip()),
                bool(self.document_id.strip()),
                bool(self.location.strip()),
                _url_identity_is_bound(self),
                bool(self.source_type.strip()),
                bool(self.fact_status.strip()),
                bool(self.evidence_hashes),
                self.provenance_role in _PROVENANCE_ROLES,
                all(re.fullmatch(r"[0-9a-f]{64}", item) for item in self.evidence_hashes),
                len(self.evidence_hashes) == len(set(self.evidence_hashes)),
                all(
                    re.fullmatch(r"[0-9a-f]{64}", item)
                    for item in self.exact_evidence_hashes
                ),
                len(self.exact_evidence_hashes)
                == len(set(self.exact_evidence_hashes)),
            )
        )

    @property
    def is_canonical_official(self) -> bool:
        """핵심 FactRecord의 단독 근거로 쓸 수 있는 공식 원문인가.

        canonical 메타데이터가 완전해야 하고, 자료 종류와 ``source_type``의 조합이
        닫힌 허용 목록에 정확히 들어야 한다. 외부 보도·증권사 분석은 검증 보조로만
        수집할 수 있으며 현재의 단일-source FactRecord에는 결속할 수 없다.
        """

        source_type = " ".join(self.source_type.split()).casefold()
        declared_official = (
            self.is_canonical_valid
            and source_type
            in _OFFICIAL_SOURCE_TYPES_BY_KIND.get(self.kind, frozenset())
        )
        if self.kind is SourceKind.OTHER:
            # Source 하나만 보고 OTHER 도메인의 소유자를 확정할 수는 없다. 다만
            # 독립 공시 결속 필드조차 없으면 후보로도 올리지 않아 조립 단계부터
            # fail-closed 한다. 실제 결속은 아래 registry 함수가 검증한다.
            return declared_official and bool(
                self.domain_attestation_source_id.strip()
                and self.domain_attestation_evidence.strip()
            )
        return declared_official


def _source_provenance_payload(source: Source) -> bytes:
    payload = {
        "number": source.number,
        "kind": source.kind.value,
        "label": source.label,
        "disclosed_at": source.disclosed_at,
        "collected_at": source.collected_at,
        "published_at": source.published_at,
        "domain": source.domain,
        "source_id": source.source_id,
        "title": source.title,
        "publisher": source.publisher,
        "host": source.host,
        "url": source.url,
        "document_id": source.document_id,
        "location": source.location,
        "source_type": source.source_type,
        "fact_status": source.fact_status,
        # ``used_in`` is a report-assembly projection, not collected provenance.
        # The assembler derives it from the final FactRecord set after collection,
        # so binding it here would invalidate an otherwise authentic source merely
        # because its section usage was calculated.  The publish gate validates the
        # derived usage independently against the facts.
        "evidence_hashes": sorted(source.evidence_hashes),
        "domain_attestation_source_id": source.domain_attestation_source_id,
        "domain_attestation_evidence": source.domain_attestation_evidence,
    }
    # 빈 exact 목록은 이 필드가 생기기 전 Source의 HMAC payload와 완전히 같다.
    # 새 exact provenance를 실제로 가진 Source만 선택적으로 seal 범위를 넓힌다.
    if source.exact_evidence_hashes:
        payload["exact_evidence_hashes"] = sorted(source.exact_evidence_hashes)
    # 기간 필드 도입 전에 저장된 Source의 HMAC payload는 그대로 유지한다.
    if source.reporting_period:
        payload["reporting_period"] = source.reporting_period
    # 공식 IR PDF의 실제 바이트 출처도 상세페이지 URL과 함께 seal에 결속한다.
    if source.attachment_url:
        payload["attachment_url"] = source.attachment_url
    if source.domain_redirect_verification:
        payload["domain_redirect_verification"] = source.domain_redirect_verification
    if source.domain_redirect_from_host:
        payload["domain_redirect_from_host"] = source.domain_redirect_from_host
    if source.domain_redirect_to_host:
        payload["domain_redirect_to_host"] = source.domain_redirect_to_host
    # 기존 저장 Source의 HMAC payload에는 role 필드가 없었다. 기본 citation은
    # byte-for-byte 호환을 유지하고, 새 내부 attester 역할일 때만 seal에 포함한다.
    if source.provenance_role != "citation":
        payload["provenance_role"] = source.provenance_role
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def seal_collected_source(source: Source) -> Source:
    """신뢰된 수집 경계가 완성 Source에 붙이는 사후변조 방지 seal."""

    digest = hmac.new(
        _PROVENANCE_SEAL_KEY,
        _source_provenance_payload(source),
        hashlib.sha256,
    ).hexdigest()
    return replace(source, provenance_seal=digest)


def has_valid_provenance_seal(source: Source) -> bool:
    """저장·전달 뒤 Source 신원이나 원문 hash가 바뀌지 않았는지 검증한다."""

    received = str(source.provenance_seal or "").strip()
    if not re.fullmatch(r"[0-9a-f]{64}", received):
        return False
    expected = hmac.new(
        _PROVENANCE_SEAL_KEY,
        _source_provenance_payload(source),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(received, expected)


def official_domain_attestation_problem(
    source: Source, sources: list[Source] | tuple[Source, ...]
) -> str:
    """OTHER 공식 원문의 도메인 소유 결속 오류를 반환한다.

    회사 웹 자료의 ``publisher/host/source_type`` 자기선언은 신뢰 경계가 아니다.
    알려진 DART/KIND 공시 원문이 같은 발행 법인의 홈페이지 URL을 실제 원문
    조각으로 보존했고 그 조각 해시까지 갖는 경우에만 공식 도메인으로 인정한다.
    특정 회사나 회사 도메인은 코드에 하드코딩하지 않는다.

    OTHER가 아닌 Source에는 적용할 계약이 없으므로 빈 문자열을 반환한다.
    """

    if source.kind is not SourceKind.OTHER:
        return ""
    if not source.is_canonical_official:
        return "공식 OTHER 원문의 필수 메타데이터 또는 독립 도메인 근거가 없습니다"

    attestation_id = source.domain_attestation_source_id.strip()
    matches = [item for item in sources if item.source_id.strip() == attestation_id]
    if len(matches) != 1:
        return "도메인 근거 source_id가 보고서의 단일 Source와 연결되지 않았습니다"
    attester = matches[0]
    if attester.source_id.strip() == source.source_id.strip():
        return "회사 웹 원문이 자기 자신의 공식 도메인을 증명할 수 없습니다"
    if attester.kind is not SourceKind.FILING or not attester.is_canonical_official:
        return "도메인 근거는 검증된 DART/KIND 공식 공시 원문이어야 합니다"
    if not has_valid_provenance_seal(attester):
        return "도메인 근거 공시의 수집 provenance seal이 없거나 변조됐습니다"
    if _publisher_key(attester.publisher) != _publisher_key(source.publisher):
        return "도메인 근거 공시의 발행 법인이 회사 웹 원문의 발행 법인과 다릅니다"

    evidence = source.domain_attestation_evidence.strip()
    if evidence_text_hash(evidence) not in attester.evidence_hashes:
        return "도메인 근거 원문 조각의 해시가 공시 Source에 보존되지 않았습니다"
    evidence_hosts = _hosts_in_domain_attestation_evidence(evidence)
    source_host = _host_key(source.host)
    if source_host not in evidence_hosts:
        dart_host = next(iter(evidence_hosts)) if len(evidence_hosts) == 1 else ""
        if not (
            source_type_is_official_ir(source.source_type)
            and dart_www_redirect_is_valid(
                verification=source.domain_redirect_verification,
                from_host=source.domain_redirect_from_host,
                to_host=source.domain_redirect_to_host,
                dart_host=dart_host,
                source_host=source_host,
            )
        ):
            return "공시 원문 조각에 회사 웹 원문의 정확한 host URL이 없습니다"
    return ""


def is_canonical_official_with_registry(
    source: Source, sources: list[Source] | tuple[Source, ...]
) -> bool:
    """보고서 Source 등록부까지 대조한 최종 공식 원문 판정."""

    return (
        source.is_canonical_official
        and has_valid_provenance_seal(source)
        and not official_domain_attestation_problem(source, sources)
    )


def official_ir_source_is_usable(source: Source, *, reference_date: str) -> bool:
    """IR 원문의 발행일·보고기간·DART 법인 결속을 함께 요구한다."""

    if not source_type_is_official_ir(source.source_type):
        return True
    evidence_hosts = _hosts_in_domain_attestation_evidence(
        source.domain_attestation_evidence
    )
    source_host = _host_key(source.host)
    if source_host not in evidence_hosts:
        dart_host = next(iter(evidence_hosts)) if len(evidence_hosts) == 1 else ""
        if not dart_www_redirect_is_valid(
            verification=source.domain_redirect_verification,
            from_host=source.domain_redirect_from_host,
            to_host=source.domain_redirect_to_host,
            dart_host=dart_host,
            source_host=source_host,
        ):
            return False
    return bool(
        source.domain_attestation_source_id.strip()
        and source.domain_attestation_evidence.strip()
        and safe_https_attachment_url(source.attachment_url)
        == source.attachment_url.strip()
        and official_ir_time_is_usable(
            published_at=source.published_at,
            reporting_period=source.reporting_period,
            reference_date=reference_date,
            max_age_days=OFFICIAL_WEB_CURRENT_MAX_AGE_DAYS,
        )
    )


# ══════════════════════════════════════════════════════════
# 쓰기 — 구조 → 마크다운
# ══════════════════════════════════════════════════════════


def _filing_meta_line(source: Source) -> str:
    """공시 자료의 두 번째 줄 — 「공시일 공시 · 수집 수집일」.

    둘 중 하나만 있어도 그것만 적는다. 있는 것까지 지우면 사실이 사라진다.
    """
    if source.disclosed_at and source.collected_at:
        return f"{source.disclosed_at} 공시 · 수집 {source.collected_at}"
    if source.disclosed_at:
        return f"{source.disclosed_at} 공시"
    if source.collected_at:
        return f"수집 {source.collected_at}"
    return ""


def render_sources(sources: list[Source]) -> str:
    """출처 목록을 화면·워드·노션이 그대로 쓸 마크다운 블록으로 만든다.

    세 형태(화면·워드·노션)가 같은 문자열을 쓰게 하려는 것이다 (P3) — 형태마다
    따로 그리면 한쪽만 고쳤을 때 내용이 갈린다.

    Args:
        sources: 보고서 하나에 실릴 출처 목록.

    Returns:
        `[출처]` 머리말부터 시작하는 마크다운 블록.
    """
    lines = [SOURCES_HEADER]
    for source in visible_citations(sources):
        if source.kind is SourceKind.OTHER and source.collected_at:
            # ★ 홈페이지 같은 「기타」 자료는 «확인»으로 적는다.
            #   공시의 「수집 …」과 글자가 같으면 다시 읽을 때 공시로 잘못 분류된다.
            #   그리고 홈페이지는 언제든 바뀌므로 «언제 본 것인지»가 특히 중요하다.
            lines.append(f" [{source.number}] {source.label}")
            if (
                source_type_is_official_ir(source.source_type)
                and source.published_at
                and source.reporting_period
            ):
                lines.append(
                    "     "
                    f"발행 {source.published_at} · 기준 {source.reporting_period} "
                    f"· {OTHER_DATE_PREFIX}{source.collected_at}"
                )
            else:
                lines.append(f"     {OTHER_DATE_PREFIX}{source.collected_at}")
            continue
        if source.kind is SourceKind.NEWS:
            date = f" {source.published_at}" if source.published_at else ""
            domain = f"  ({source.domain})" if source.domain else ""
            lines.append(f" [{source.number}] {source.label}{date}{domain}")
            continue
        lines.append(f" [{source.number}] {source.label}")
        meta = _filing_meta_line(source)
        if meta:
            lines.append(f"     {meta}")
    return "\n".join(lines)


def visible_citations(sources: Iterable[object]) -> list[Source]:
    """사용자 공개 채널에 내보낼 일반 citation만 고른다.

    ``attestation_only``는 출고 검증·감사 JSON·저장 재로딩에는 남기되,
    인증키 없는 내부 OpenDART endpoint를 사용자 출처처럼 표시하지 않는다.
    PDF·Notion·웹·마크다운이 모두 이 한 정책을 공유한다.
    """

    return [
        source
        for source in sources
        if isinstance(source, Source) and source.provenance_role == "citation"
    ]


# ══════════════════════════════════════════════════════════
# 읽기 — 마크다운 → 구조
# ══════════════════════════════════════════════════════════

#: `[2] 감사보고서 제16장 수익인식 주석` 같은 항목 첫 줄.
_ENTRY = re.compile(r"^\s*\[(?P<num>\d+)\]\s*(?P<rest>.+?)\s*$")
#: 뉴스 줄 끝의 `2025-03-12  (mk.co.kr)` 꼴.
_NEWS_SUFFIX = re.compile(
    r"^(?P<label>.+?)\s+(?P<date>\d{4}-\d{2}-\d{2})\s*\((?P<domain>[^)]+)\)\s*$"
)
#: 공시 자료의 두 번째 줄 — 셋 중 하나로 갈린다 (둘 다 / 공시일만 / 수집일만).
_FILING_BOTH = re.compile(
    r"^\s*(?P<disclosed>\d{4}-\d{2}-\d{2})\s*공시\s*·\s*수집\s*"
    r"(?P<collected>\d{4}-\d{2}-\d{2})\s*$"
)
_FILING_DISCLOSED_ONLY = re.compile(r"^\s*(?P<disclosed>\d{4}-\d{2}-\d{2})\s*공시\s*$")
_FILING_COLLECTED_ONLY = re.compile(r"^\s*수집\s*(?P<collected>\d{4}-\d{2}-\d{2})\s*$")
#: 기타(홈페이지 등)의 두 번째 줄 — 「확인 날짜」. 공시의 「수집 날짜」와 구분한다.
_OTHER_CONFIRMED = re.compile(
    r"^\s*" + OTHER_DATE_PREFIX.strip() + r"\s*(?P<collected>\d{4}-\d{2}-\d{2})\s*$"
)
_OTHER_IR_META = re.compile(
    r"^\s*발행\s*(?P<published>\d{4}-\d{2}-\d{2})\s*·\s*"
    r"기준\s*(?P<period>\S+)\s*·\s*"
    + OTHER_DATE_PREFIX.strip()
    + r"\s*(?P<collected>\d{4}-\d{2}-\d{2})\s*$"
)


def parse_sources(text: str) -> list[Source]:
    """마크다운 `[출처]` 블록을 다시 구조로 읽는다.

    ★ `render_sources()`가 쓴 형식만 읽는다 — 사람이 손으로 다르게 쓴 문서를
      복원하는 범용 파서가 아니다. 목적은 «왕복 보장»이다.

    Args:
        text: `render_sources()`가 만들었거나 그와 같은 모양인 마크다운.

    Returns:
        출처 목록. 항목을 하나도 못 찾으면 빈 목록.
    """
    lines = text.splitlines()
    sources: list[Source] = []
    idx = 0
    total = len(lines)

    while idx < total:
        entry = _ENTRY.match(lines[idx])
        if entry is None:
            idx += 1
            continue

        number = int(entry.group("num"))
        rest = entry.group("rest").strip()

        news = _NEWS_SUFFIX.match(rest)
        if news is not None:
            sources.append(
                Source(
                    number=number,
                    kind=SourceKind.NEWS,
                    label=news.group("label").strip(),
                    published_at=news.group("date"),
                    domain=news.group("domain").strip(),
                )
            )
            idx += 1
            continue

        disclosed_at = collected_at = published_at = reporting_period = source_type = ""
        kind = SourceKind.OTHER
        if idx + 1 < total:
            nxt = lines[idx + 1]
            both = _FILING_BOTH.match(nxt)
            disclosed_only = _FILING_DISCLOSED_ONLY.match(nxt)
            collected_only = _FILING_COLLECTED_ONLY.match(nxt)
            if both is not None:
                disclosed_at = both.group("disclosed")
                collected_at = both.group("collected")
                kind = SourceKind.FILING
                idx += 1
            elif disclosed_only is not None:
                disclosed_at = disclosed_only.group("disclosed")
                kind = SourceKind.FILING
                idx += 1
            elif collected_only is not None:
                collected_at = collected_only.group("collected")
                kind = SourceKind.FILING
                idx += 1
            else:
                ir_meta = _OTHER_IR_META.match(nxt)
                confirmed = _OTHER_CONFIRMED.match(nxt)
                if ir_meta is not None:
                    published_at = ir_meta.group("published")
                    reporting_period = ir_meta.group("period")
                    collected_at = ir_meta.group("collected")
                    source_type = "회사 공식 IR"
                    idx += 1
                elif confirmed is not None:
                    # 「확인 날짜」 = 기타(홈페이지 등). 종류를 공시로 바꾸지 않는다.
                    collected_at = confirmed.group("collected")
                    idx += 1

        sources.append(
            Source(
                number=number,
                kind=kind,
                label=rest,
                disclosed_at=disclosed_at,
                collected_at=collected_at,
                published_at=published_at,
                reporting_period=reporting_period,
                source_type=source_type,
            )
        )
        idx += 1

    return sources


def count_missing_dates(sources: list[Source]) -> int:
    """출처일·수집일이 하나라도 빠진 공시 자료 개수 (C3).

    맨 아래 목록에 날짜가 한곳에 모이는 «딸려 오는 효과»를 여기서 쓴다
    (정본 §근거 표기 — 딸려 오는 효과).

    ⚠️ 알려진 한계 — 마크다운으로 한 번 왕복(render → parse)하면, 날짜가
      하나도 없던 공시 항목은 겉모양이 '기타'와 똑같아져 더 이상 공시로
      구분되지 않는다 (표시 형식 자체에 「원래 공시였다」는 표식이 없다).
      그래서 이 함수는 **렌더링하기 전, 원본 in-memory 목록**에 대고 불러야
      정확하다.
    """
    return sum(
        1
        for s in sources
        if s.kind is SourceKind.FILING and not (s.disclosed_at and s.collected_at)
    )
