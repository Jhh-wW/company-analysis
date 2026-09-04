"""DART 공시에서 발견한 공식 홈페이지 후보의 승격 계약.

공시 ZIP의 URL 한 줄, 홈페이지의 회사명 한 줄, 또는 출처 등급 문자열 하나만
보고 공식 사이트로 승격하면 제3자 디렉터리와 복사 페이지도 보고서 근거가 될 수
있다. 이 모듈은 다음 두 영수증을 한 가지 정본 형식으로 만든다.

* ``DartFilingUrlProvenance``: URL이 어느 회사의 어느 DART 접수 문서·첨부·
  원문 위치와 두 SHA-256에 결속되어 발견됐는지.
* ``VerifiedDartFilingOfficialWeb``: 위 후보의 HTTPS 소유 범위 안에서 실제
  landing 페이지를 읽고, DART 법인명과 정확한 등록번호를 모두 확인했는지.

수집기만 영수증을 만들고 장 선택기만 등급을 판정하는 식으로 규칙을 둘로
나누지 않는다. 수집·공식 source-kind 검증·Writer 선택이 모두 이 파일의 parser를
사용한다. 영수증은 전자서명이 아니라 내부 배선 계약이며, 최종 공개 출처에서는
기존 provenance seal이 별도로 변조를 막는다.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import re
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from typing import Final

from src.shared.company_identity import (
    exact_company_name_key,
    normalize_korean_registration_number,
)
from src.shared.registered_domain import (
    is_actual_registered_subdomain,
    registrable_domain,
)
from src.shared.report_evidence.constants import SourceRequirement, SourceTier


_DART_PROVENANCE_PREFIX: Final[str] = "dart-filing-url-provenance-v1:"
_VERIFIED_BINDING_PREFIX: Final[str] = "dart-filing-official-web-v1:"
_VERIFIED_SUBDOMAIN_BINDING_PREFIX: Final[str] = (
    "dart-filing-official-subdomain-v1:"
)
_DART_RECEIPT_RE = re.compile(r"^[0-9]{14}$")
_DART_LOCATION_RE = re.compile(r"^raw_xml_chars:([0-9]{1,10})-([0-9]{1,10})$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MAX_URL_CHARS: Final[int] = 2048
_MAX_MEMBER_NAME_CHARS: Final[int] = 512

# 회사가 운영하는 별도 채널로 승격하면 안 되는 공유·소셜·광고·분석 host.
# homepage.wide_domain과 공식 source-kind 검증이 같은 정본을 사용한다.
NON_COMPANY_IDENTITY_HOST_SUFFIXES: Final[tuple[str, ...]] = (
    "facebook.com",
    "instagram.com",
    "youtube.com",
    "youtu.be",
    "twitter.com",
    "x.com",
    "linkedin.com",
    "kakao.com",
    "pf.kakao.com",
    "band.us",
    "google.com",
    "googletagmanager.com",
    "google-analytics.com",
    "doubleclick.net",
    "naver.com",
    "channel.io",
    "dart.fss.or.kr",
    "opendart.fss.or.kr",
    "fss.or.kr",
)


def _is_sha256(value: object) -> bool:
    return type(value) is str and _SHA256_RE.fullmatch(value) is not None


def _safe_member_name(value: object) -> str:
    if type(value) is not str:
        return ""
    name = value.replace("\\", "/")
    if (
        not name
        or len(name) > _MAX_MEMBER_NAME_CHARS
        or name.startswith("/")
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or any(ord(character) < 32 for character in name)
    ):
        return ""
    return name


def _canonical_web_url(value: object, *, https_only: bool) -> str:
    """후보 영수증에 허용할 exact URL 모양을 만든다(추측·보정 없음)."""

    if (
        type(value) is not str
        or not value
        or len(value) > _MAX_URL_CHARS
        or "\\" in value
        or any(ord(character) < 32 for character in value)
    ):
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
        scheme = parsed.scheme.casefold()
        host = (
            (parsed.hostname or "")
            .rstrip(".")
            .encode("idna")
            .decode("ascii")
            .casefold()
        )
        port = parsed.port
    except (TypeError, ValueError, UnicodeError):
        return ""
    allowed_schemes = {"https"} if https_only else {"http", "https"}
    default_port = 80 if scheme == "http" else 443 if scheme == "https" else None
    if (
        scheme not in allowed_schemes
        or default_port is None
        or not host
        or "." not in host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, default_port)
        or parsed.fragment
    ):
        return ""
    try:
        # 회사 공식 웹의 소유 범위는 도메인으로 결속한다. 공인 IP 리터럴도
        # 재할당·공유 경계를 증명할 수 없으므로 후보로 받지 않는다.
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return ""
    normalized = urllib.parse.urlunsplit(
        (scheme, host, parsed.path or "/", parsed.query, "")
    )
    return normalized if normalized == value else ""


def is_disallowed_identity_host(host: object) -> bool:
    """공식 홈페이지 소유 host로 승격할 수 없는 공유/제3자 host인지 판정한다."""

    if type(host) is not str:
        return True
    normalized = host.casefold().rstrip(".")
    if not normalized:
        return True
    return any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in NON_COMPANY_IDENTITY_HOST_SUFFIXES
    )


def is_canonical_dart_candidate_url(value: object) -> bool:
    """typed DART Mapping과 proof builder가 공유하는 exact URL 형식 검사."""

    return bool(_canonical_web_url(value, https_only=False))


def canonical_identity_verified_web_url(value: object) -> str:
    """공식 신원 proof와 공개 Source가 공유하는 exact HTTPS URL 정본."""

    canonical = _canonical_web_url(value, https_only=True)
    if not canonical:
        return ""
    host = (urllib.parse.urlsplit(canonical).hostname or "").casefold()
    return "" if is_disallowed_identity_host(host) else canonical


@dataclass(frozen=True)
class DartFilingUrlProvenance:
    """typed DART 후보가 공시 문서·첨부 원문에 결속됐다는 내부 영수증."""

    company_id: str
    url: str
    source_document_id: str
    source_receipt_no: str
    source_member_name: str
    source_location: str
    source_document_sha256: str
    source_payload_sha256: str

    def __post_init__(self) -> None:
        if type(self.company_id) is not str or not self.company_id.strip():
            raise ValueError("DART URL 후보의 회사 식별자가 비어 있습니다")
        if _canonical_web_url(self.url, https_only=False) != self.url:
            raise ValueError("DART URL 후보가 exact 웹 URL 정본과 다릅니다")
        if _DART_RECEIPT_RE.fullmatch(self.source_receipt_no) is None:
            raise ValueError("DART URL 후보의 접수번호 형식이 올바르지 않습니다")
        if (
            type(self.source_document_id) is not str
            or not self.source_document_id.endswith(f":{self.source_receipt_no}")
        ):
            raise ValueError("DART URL 후보의 문서와 접수번호가 다릅니다")
        if _safe_member_name(self.source_member_name) != self.source_member_name:
            raise ValueError("DART URL 후보의 첨부 member 이름이 안전하지 않습니다")
        location_match = _DART_LOCATION_RE.fullmatch(self.source_location)
        if (
            location_match is None
            or int(location_match.group(2)) <= int(location_match.group(1))
        ):
            raise ValueError("DART URL 후보의 원문 위치 형식이 올바르지 않습니다")
        if not _is_sha256(self.source_document_sha256) or not _is_sha256(
            self.source_payload_sha256
        ):
            raise ValueError("DART URL 후보의 원문 SHA-256 형식이 올바르지 않습니다")


def _canonical_json(value: Mapping[str, object]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_dart_filing_url_provenance(**values: str) -> str:
    """Mapping 경계가 검증한 DART URL 후보를 유일한 문자열 영수증으로 만든다."""

    provenance = DartFilingUrlProvenance(**values)
    return _DART_PROVENANCE_PREFIX + _canonical_json(asdict(provenance))


def parse_dart_filing_url_provenance(
    value: object,
) -> DartFilingUrlProvenance | None:
    if type(value) is not str or not value.startswith(_DART_PROVENANCE_PREFIX):
        return None
    encoded = value[len(_DART_PROVENANCE_PREFIX) :]
    try:
        raw = json.loads(encoded)
        if type(raw) is not dict or set(raw) != set(DartFilingUrlProvenance.__annotations__):
            return None
        provenance = DartFilingUrlProvenance(**raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if build_dart_filing_url_provenance(**asdict(provenance)) != value:
        return None
    return provenance


@dataclass(frozen=True)
class VerifiedDartFilingOfficialWeb:
    """공시 계보와 실제 페이지 이중 신원 검증을 모두 통과한 공식 root."""

    provenance: DartFilingUrlProvenance
    candidate_url: str
    effective_urls: tuple[str, ...]
    scope_sha256: str
    identity_evidence_sha256: str
    matched_name_sha256: str
    registration_number_sha256: str


@dataclass(frozen=True)
class VerifiedDartFilingOfficialSubdomain:
    """검증된 DART root가 직접 발견한 실제 등록 하위도메인 영수증."""

    root_binding: str
    source_url: str
    scope_sha256: str


def _same_https_origin(left: str, right: str) -> bool:
    try:
        left_parsed = urllib.parse.urlsplit(left)
        right_parsed = urllib.parse.urlsplit(right)
        return (
            left_parsed.scheme.casefold() == right_parsed.scheme.casefold() == "https"
            and (left_parsed.hostname or "").casefold().rstrip(".")
            == (right_parsed.hostname or "").casefold().rstrip(".")
            and (left_parsed.port or 443) == (right_parsed.port or 443)
        )
    except ValueError:
        return False


def _https_candidate_from_provenance(url: str) -> str:
    """legacy HTTP는 같은 host/path/query의 기본 HTTPS 한 가지로만 올린다."""

    canonical = _canonical_web_url(url, https_only=False)
    if not canonical:
        return ""
    parsed = urllib.parse.urlsplit(canonical)
    if parsed.scheme.casefold() == "https":
        return canonical
    return urllib.parse.urlunsplit(
        ("https", parsed.hostname or "", parsed.path or "/", parsed.query, "")
    )


def _verified_binding_payload(
    proof: VerifiedDartFilingOfficialWeb,
) -> dict[str, object]:
    return {
        "candidate_url": proof.candidate_url,
        "effective_urls": list(proof.effective_urls),
        "identity_evidence_sha256": proof.identity_evidence_sha256,
        "matched_name_sha256": proof.matched_name_sha256,
        "provenance": asdict(proof.provenance),
        "registration_number_sha256": proof.registration_number_sha256,
        "scope_sha256": proof.scope_sha256,
    }


def build_verified_dart_filing_official_web_binding(
    *,
    provenance_value: object,
    company_id: str,
    company_name: str,
    company_registration_numbers: tuple[str, ...],
    candidate_url: str,
    effective_urls: tuple[str, ...],
    scope_sha256: str,
    scope_allows: Callable[[str], bool],
    identity_evidence_sha256: str,
    matched_name_sha256: str,
    registration_number_sha256: str,
) -> str:
    """모든 공식성 조건을 만족할 때만 TIER1 판정용 canonical binding을 만든다."""

    provenance = parse_dart_filing_url_provenance(provenance_value)
    candidate = _canonical_web_url(candidate_url, https_only=True)
    company_name_key = exact_company_name_key(company_name)
    expected_name_sha256 = (
        hashlib.sha256(company_name_key.encode("utf-8")).hexdigest()
        if company_name_key
        else ""
    )
    expected_registration_hashes = {
        hashlib.sha256(normalized.encode("ascii")).hexdigest()
        for raw in company_registration_numbers
        if (normalized := normalize_korean_registration_number(raw))
    }
    if (
        provenance is None
        or provenance.company_id != company_id
        or _https_candidate_from_provenance(provenance.url) != candidate
        or not candidate
        or matched_name_sha256 != expected_name_sha256
        or registration_number_sha256 not in expected_registration_hashes
        or not _is_sha256(scope_sha256)
        or not all(
            _is_sha256(value)
            for value in (
                identity_evidence_sha256,
                matched_name_sha256,
                registration_number_sha256,
            )
        )
    ):
        return ""
    candidate_host = (urllib.parse.urlsplit(candidate).hostname or "").casefold()
    if is_disallowed_identity_host(candidate_host) or not effective_urls:
        return ""
    normalized_effective: list[str] = []
    for value in effective_urls:
        normalized = _canonical_web_url(value, https_only=True)
        if (
            not normalized
            or is_disallowed_identity_host(
                (urllib.parse.urlsplit(normalized).hostname or "").casefold()
            )
            or not _same_https_origin(candidate, normalized)
            or not scope_allows(normalized)
        ):
            return ""
        normalized_effective.append(normalized)
    proof = VerifiedDartFilingOfficialWeb(
        provenance=provenance,
        candidate_url=candidate,
        effective_urls=tuple(normalized_effective),
        scope_sha256=scope_sha256,
        identity_evidence_sha256=identity_evidence_sha256,
        matched_name_sha256=matched_name_sha256,
        registration_number_sha256=registration_number_sha256,
    )
    return _VERIFIED_BINDING_PREFIX + _canonical_json(_verified_binding_payload(proof))


def parse_verified_dart_filing_official_web_binding(
    value: object,
) -> VerifiedDartFilingOfficialWeb | None:
    """canonical binding만 다시 열어 source-kind와 Writer가 같은 결론을 내린다."""

    if type(value) is not str or not value.startswith(_VERIFIED_BINDING_PREFIX):
        return None
    encoded = value[len(_VERIFIED_BINDING_PREFIX) :]
    try:
        raw = json.loads(encoded)
        if type(raw) is not dict or set(raw) != {
            "candidate_url",
            "effective_urls",
            "identity_evidence_sha256",
            "matched_name_sha256",
            "provenance",
            "registration_number_sha256",
            "scope_sha256",
        }:
            return None
        raw_provenance = raw["provenance"]
        if type(raw_provenance) is not dict:
            return None
        provenance_value = build_dart_filing_url_provenance(**raw_provenance)
        provenance = parse_dart_filing_url_provenance(provenance_value)
        effective_rows = raw["effective_urls"]
        if provenance is None or type(effective_rows) is not list:
            return None
        effective_urls = tuple(effective_rows)
        if not effective_urls or any(type(item) is not str for item in effective_urls):
            return None
        proof = VerifiedDartFilingOfficialWeb(
            provenance=provenance,
            candidate_url=raw["candidate_url"],
            effective_urls=effective_urls,
            scope_sha256=raw["scope_sha256"],
            identity_evidence_sha256=raw["identity_evidence_sha256"],
            matched_name_sha256=raw["matched_name_sha256"],
            registration_number_sha256=raw["registration_number_sha256"],
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not all(
        _is_sha256(value)
        for value in (
            proof.scope_sha256,
            proof.identity_evidence_sha256,
            proof.matched_name_sha256,
            proof.registration_number_sha256,
        )
    ):
        return None
    candidate = _canonical_web_url(proof.candidate_url, https_only=True)
    if (
        not candidate
        or _https_candidate_from_provenance(proof.provenance.url) != candidate
        or is_disallowed_identity_host(
            (urllib.parse.urlsplit(candidate).hostname or "").casefold()
        )
    ):
        return None
    for effective in proof.effective_urls:
        normalized = _canonical_web_url(effective, https_only=True)
        if not normalized or not _same_https_origin(candidate, normalized):
            return None
    if _VERIFIED_BINDING_PREFIX + _canonical_json(_verified_binding_payload(proof)) != value:
        return None
    return proof


def _registered_subdomain_source_is_allowed(
    root_proof: VerifiedDartFilingOfficialWeb,
    source_url: str,
) -> bool:
    """root apex/www와 실제 자손 host의 관계를 한 정본으로 확인한다."""

    source = canonical_identity_verified_web_url(source_url)
    if not source:
        return False
    try:
        root_host = (
            urllib.parse.urlsplit(root_proof.candidate_url).hostname or ""
        ).casefold().rstrip(".")
        source_host = (
            urllib.parse.urlsplit(source).hostname or ""
        ).casefold().rstrip(".")
    except ValueError:
        return False
    root_domain = registrable_domain(root_host)
    return bool(
        root_domain
        and root_host in {root_domain, f"www.{root_domain}"}
        and is_actual_registered_subdomain(root_domain, source_host)
        and not is_disallowed_identity_host(source_host)
    )


def build_verified_dart_filing_subdomain_binding(
    *,
    root_identity_binding: object,
    source_url: object,
    scope_sha256: object,
) -> str:
    """strict DART root proof를 실제 등록 하위도메인 URL에 다시 결속한다.

    설명 문자열을 덧붙이지 않는다. 원 DART 접수·첨부 hash·법인명·등록번호
    proof 전체와, root가 실제로 링크한 자손 URL 및 그 수집 범위를 하나의
    canonical JSON으로 운반한다. sibling·무관 도메인은 만들 수 없다.
    """

    root = parse_verified_dart_filing_official_web_binding(root_identity_binding)
    source = canonical_identity_verified_web_url(source_url)
    if (
        root is None
        or not source
        or not _is_sha256(scope_sha256)
        or not _registered_subdomain_source_is_allowed(root, source)
    ):
        return ""
    payload = {
        "root_binding": str(root_identity_binding),
        "scope_sha256": str(scope_sha256),
        "source_url": source,
    }
    return _VERIFIED_SUBDOMAIN_BINDING_PREFIX + _canonical_json(payload)


def parse_verified_dart_filing_subdomain_binding(
    value: object,
) -> VerifiedDartFilingOfficialSubdomain | None:
    """canonical 등록 하위도메인 proof만 다시 연다."""

    if type(value) is not str or not value.startswith(
        _VERIFIED_SUBDOMAIN_BINDING_PREFIX
    ):
        return None
    encoded = value[len(_VERIFIED_SUBDOMAIN_BINDING_PREFIX) :]
    try:
        raw = json.loads(encoded)
        if type(raw) is not dict or set(raw) != {
            "root_binding",
            "scope_sha256",
            "source_url",
        }:
            return None
        proof = VerifiedDartFilingOfficialSubdomain(
            root_binding=raw["root_binding"],
            source_url=raw["source_url"],
            scope_sha256=raw["scope_sha256"],
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    root = parse_verified_dart_filing_official_web_binding(proof.root_binding)
    if (
        root is None
        or not _is_sha256(proof.scope_sha256)
        or not _registered_subdomain_source_is_allowed(root, proof.source_url)
        or _VERIFIED_SUBDOMAIN_BINDING_PREFIX
        + _canonical_json(
            {
                "root_binding": proof.root_binding,
                "scope_sha256": proof.scope_sha256,
                "source_url": proof.source_url,
            }
        )
        != value
    ):
        return None
    return proof


def verified_dart_filing_binding_allows_public_source(
    identity_binding: object,
    *,
    company_name: object,
    source_url: object,
) -> bool:
    """봉인할 공개 Source가 검증된 회사명·origin과 실제로 같은지 확인한다.

    typed packet은 이 검사를 통과한 값을 다시 HMAC provenance seal에 넣는다.
    따라서 저장 뒤 URL·발행 법인·proof 중 하나만 바꾸어도 공식 원문 자격을
    잃는다. descendant 문서는 후보와 같은 HTTPS origin만 허용한다. 더 좁은
    path/query 범위는 proof를 만든 수집기의 ``scope_allows`` 검사가 이미
    확인하고 ``scope_sha256``으로 packet에 결속한다.
    """

    proof = parse_verified_dart_filing_official_web_binding(identity_binding)
    subdomain = parse_verified_dart_filing_subdomain_binding(identity_binding)
    name_key = exact_company_name_key(company_name)
    root_proof = proof or (
        parse_verified_dart_filing_official_web_binding(subdomain.root_binding)
        if subdomain is not None
        else None
    )
    if (
        root_proof is None
        or not name_key
        or not verified_dart_filing_binding_allows_url(
            identity_binding,
            source_url=source_url,
        )
    ):
        return False
    expected_name_hash = hashlib.sha256(name_key.encode("utf-8")).hexdigest()
    return expected_name_hash == root_proof.matched_name_sha256


def verified_dart_filing_binding_allows_url(
    identity_binding: object,
    *,
    source_url: object,
) -> bool:
    """typed transport에서 회사명 투영 전에도 proof↔URL 결속을 검사한다."""

    proof = parse_verified_dart_filing_official_web_binding(identity_binding)
    subdomain = parse_verified_dart_filing_subdomain_binding(identity_binding)
    canonical_url = canonical_identity_verified_web_url(source_url)
    if not canonical_url:
        return False
    if proof is not None:
        return _same_https_origin(proof.candidate_url, canonical_url)
    return bool(
        subdomain is not None
        and _same_https_origin(subdomain.source_url, canonical_url)
    )


def identity_verified_web_expected_trust(
    identity_binding: object,
) -> tuple[SourceTier, SourceRequirement]:
    """신원검증 웹의 유일한 등급 판정: 완전한 공시 proof만 TIER1이다."""

    if (
        parse_verified_dart_filing_official_web_binding(identity_binding) is not None
        or parse_verified_dart_filing_subdomain_binding(identity_binding) is not None
    ):
        return (SourceTier.TIER_1_OFFICIAL, SourceRequirement.REQUIRED)
    return (SourceTier.TIER_3_TRUSTED, SourceRequirement.OPTIONAL)


def identity_binding_with_scope(identity_binding: str, scope_sha256: str) -> str:
    """검증 binding은 이미 root scope를 포함하므로 중복 문자열을 붙이지 않는다."""

    proof = parse_verified_dart_filing_official_web_binding(identity_binding)
    if proof is not None and proof.scope_sha256 == scope_sha256:
        return identity_binding
    subdomain = parse_verified_dart_filing_subdomain_binding(identity_binding)
    if subdomain is not None and subdomain.scope_sha256 == scope_sha256:
        return identity_binding
    return f"{identity_binding}; scope_sha256={scope_sha256}"


def provenance_digest(value: object) -> str:
    """로그·문서 identity용으로 원문 개인정보 없이 proof 전체를 결속한다."""

    if type(value) is not str:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
