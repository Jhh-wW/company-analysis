"""여러 보고서 기능이 공유하는 독립 문서 신원 함수."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

from src.shared.report_evidence.constants import (
    FORMAL_DOCUMENT_SOURCE_KINDS,
    OFFICIAL_WEB_SOURCE_KINDS,
)


_KNOWN_DOCUMENT_HOSTS = frozenset(
    {"dart.fss.or.kr", "opendart.fss.or.kr", "kind.krx.co.kr"}
)
_TRACKING_QUERY_KEYS = frozenset({"fbclid", "gclid", "msclkid"})
_DART_RECEIPT_RE = re.compile(r"[0-9]{14}")
_DART_DOCUMENT_HOST = "dart.fss.or.kr"
_DART_DOCUMENT_PATH = "/dsaf001/main.do"


class DocumentIdentityInput(Protocol):
    """기존 Source가 호출 경계에서 만족하는 최소 문서 신원."""

    document_id: str
    host: str
    url: str


class FactIdentityInput(Protocol):
    """FactRecord의 대표 출처와 다중 출처 결속 열."""

    source_id: str
    source_document_id: str
    source_host: str
    source_url: str
    supporting_source_ids: Sequence[str]
    supporting_source_identities: Sequence[str]


def _normalized(value: object) -> str:
    return unicodedata.normalize("NFKC", str(value or "")).strip().casefold()


def canonical_url(value: str) -> str:
    """fragment·표시 차이를 없앤 문서 URL 신원을 만든다."""

    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urlsplit(raw)
        scheme = parsed.scheme.casefold()
        host = (
            (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii").casefold()
        )
        if (
            scheme not in {"http", "https"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
        ):
            return ""
        parsed_port = parsed.port
        port = (
            ""
            if parsed_port is None
            or (scheme == "https" and parsed_port == 443)
            or (scheme == "http" and parsed_port == 80)
            else f":{parsed_port}"
        )
        path = parsed.path.rstrip("/") or "/"
        query_items = [
            (key, item)
            for key, item in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.casefold().startswith("utm_")
            and key.casefold() not in _TRACKING_QUERY_KEYS
        ]
        query = urlencode(sorted(query_items))
        return urlunsplit((scheme, host + port, path, query, ""))
    except (TypeError, UnicodeError, ValueError):
        return ""


def document_identity(source: DocumentIdentityInput) -> str:
    """같은 문서의 여러 조각이 하나로 세어지는 안정 신원."""

    formal_source_kind = str(
        getattr(source, "formal_source_kind", "") or ""
    ).strip()
    if formal_source_kind:
        formal_identity = collected_document_identity(
            source_kind=formal_source_kind,
            document_id=source.document_id,
            url=source.url,
        )
        if formal_identity:
            return formal_identity
    return document_identity_from_parts(
        document_id=source.document_id,
        host=source.host,
        url=source.url,
    )


def bound_source_fragment_provenance(source: object) -> dict[str, str]:
    """봉인 Source가 program CollectedFragment에 투영해야 할 단일 정본.

    일반 수집 조각은 원래 수집 DTO의 publisher/document_id를 보존하지만,
    ``bound_source`` program 차선은 이미 공개 Source로 봉인된 값을 운반한다.
    생산자와 검증자가 이 필드표를 함께 써야 Source만 formal인데 조각은 legacy인
    반쪽 상태나 새 provenance 필드 한 개만 빠진 상태가 생기지 않는다.
    """

    formal_source_kind = str(
        getattr(source, "formal_source_kind", "") or ""
    ).strip()
    formal_only = bool(formal_source_kind)
    return {
        "source_url": str(getattr(source, "url", "") or ""),
        "document_title": str(
            getattr(source, "title", "")
            or getattr(source, "label", "")
            or ""
        ),
        "location": str(getattr(source, "location", "") or ""),
        "document_date": str(
            getattr(source, "published_at", "")
            or getattr(source, "disclosed_at", "")
            or getattr(source, "collected_at", "")
            or ""
        ),
        "document_identity": document_identity(source),
        "document_content_sha256": str(
            getattr(source, "document_content_sha256", "") or ""
        ),
        "formal_source_kind": formal_source_kind,
        "source_document_id": (
            str(getattr(source, "document_id", "") or "")
            if formal_only
            else ""
        ),
        "source_publisher": (
            str(getattr(source, "publisher", "") or "")
            if formal_only
            else ""
        ),
        "identity_binding": (
            str(getattr(source, "identity_binding", "") or "")
            if formal_only
            else ""
        ),
        "source_collected_on": (
            str(getattr(source, "collected_at", "") or "")
            if formal_only
            else ""
        ),
        "domain_attestation_source_id": (
            str(getattr(source, "domain_attestation_source_id", "") or "")
            if formal_only
            else ""
        ),
        "domain_attestation_evidence": (
            str(getattr(source, "domain_attestation_evidence", "") or "")
            if formal_only
            else ""
        ),
        "reporting_period": (
            str(getattr(source, "reporting_period", "") or "")
            if formal_only
            else ""
        ),
        "attachment_url": (
            str(getattr(source, "attachment_url", "") or "")
            if formal_only
            else ""
        ),
        "ir_metadata_verification": (
            str(getattr(source, "ir_metadata_verification", "") or "")
            if formal_only
            else ""
        ),
        "domain_redirect_verification": (
            str(getattr(source, "domain_redirect_verification", "") or "")
            if formal_only
            else ""
        ),
        "domain_redirect_from_host": (
            str(getattr(source, "domain_redirect_from_host", "") or "")
            if formal_only
            else ""
        ),
        "domain_redirect_to_host": (
            str(getattr(source, "domain_redirect_to_host", "") or "")
            if formal_only
            else ""
        ),
    }


def bound_source_fragment_provenance_mismatches(
    fragment: object,
    source: object,
) -> tuple[str, ...]:
    """program 조각이 위 Source 투영 정본과 다른 필드 이름을 돌려준다."""

    expected = bound_source_fragment_provenance(source)
    return tuple(
        field_name
        for field_name, expected_value in expected.items()
        if str(getattr(fragment, field_name, "") or "") != expected_value
    )


def fact_document_identity(fact: FactIdentityInput) -> str:
    """FactRecord가 Source에서 이미 결속한 대표 문서 신원을 되살린다.

    FULL fact는 Source를 만들 때 계산한 정확한 문서 identity를 다중 출처 결속
    첫 열에 보존한다. 공식 웹의 내부 ``document_id``를 다시 해석하면 URL 정본과
    다른 identity를 만들 수 있으므로 그 결속값을 우선한다. 결속 열이 일부만
    있거나 대표 ``source_id``와 어긋나면 임의 복구하지 않고 빈값으로 닫는다.
    두 열이 모두 없는 발급 전 레거시 fact만 예전 세 필드 규칙을 유지한다.
    """

    source_ids = tuple(
        str(value or "").strip()
        for value in getattr(fact, "supporting_source_ids", ())
    )
    identities = tuple(
        str(value or "").strip()
        for value in getattr(fact, "supporting_source_identities", ())
    )
    if source_ids or identities:
        if (
            not source_ids
            or not identities
            or len(source_ids) != len(identities)
            or source_ids[0] != str(fact.source_id or "").strip()
        ):
            return ""
        return identities[0]
    return document_identity_from_parts(
        document_id=str(fact.source_document_id or ""),
        host=str(fact.source_host or ""),
        url=str(fact.source_url or ""),
    )


def document_identity_from_parts(
    *,
    document_id: str = "",
    host: str = "",
    url: str = "",
) -> str:
    """기존 자료형을 import하지 않고 같은 독립 문서 신원을 만든다."""

    normalized_document_id = _normalized(document_id)
    normalized_host = _normalized(host).rstrip(".")
    normalized_url = canonical_url(url)
    if normalized_url and normalized_host:
        try:
            url_host = (urlsplit(normalized_url).hostname or "").casefold().rstrip(".")
        except ValueError:
            return ""
        if url_host != normalized_host:
            return ""
    if (
        normalized_document_id
        and normalized_host in _KNOWN_DOCUMENT_HOSTS
        and normalized_url
        and normalized_document_id not in unquote(normalized_url).casefold()
    ):
        return ""
    if normalized_document_id and normalized_host:
        return f"document:{normalized_host}:{normalized_document_id}"
    return f"url:{normalized_url}" if normalized_url else ""


def collected_document_identity(
    *,
    source_kind: str,
    document_id: str,
    url: str,
) -> str:
    """formal 수집 문서의 공개 재검산 가능한 identity를 한 규칙으로 만든다.

    공식 웹·채용·IR은 수집기 내부 ID가 바뀌어도 독자가 여는 canonical URL이
    문서 정본이다. DART 문서는 접수번호가 정본이며 URL 안의 접수번호와도
    일치해야 한다. 알 수 없는 ``source_kind``를 웹으로 간주하지 않는다.
    """

    normalized_kind = str(source_kind or "").strip()
    normalized_url = canonical_url(url)
    if normalized_kind not in FORMAL_DOCUMENT_SOURCE_KINDS or not normalized_url:
        return ""
    if normalized_kind in OFFICIAL_WEB_SOURCE_KINDS:
        return document_identity_from_parts(url=normalized_url)

    raw_document_id = _normalized(document_id)
    receipt_number = raw_document_id.rpartition(":")[2]
    if _DART_RECEIPT_RE.fullmatch(receipt_number) is None:
        return ""
    try:
        parsed = urlsplit(normalized_url)
        host = (parsed.hostname or "").casefold().rstrip(".")
        receipt_values = tuple(
            value
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if key.casefold() == "rcpno"
        )
    except ValueError:
        return ""
    if (
        host != _DART_DOCUMENT_HOST
        or parsed.path != _DART_DOCUMENT_PATH
        or receipt_values != (receipt_number,)
    ):
        return ""
    return document_identity_from_parts(
        document_id=receipt_number,
        host=host,
        url=normalized_url,
    )


def bind_declared_document_identity_to_url(
    declared_identity: str,
    url: str,
) -> str:
    """선언한 identity를 독자가 여는 URL에서 다시 만들 수 있을 때만 돌려준다."""

    declared = str(declared_identity or "").strip()
    normalized_url = canonical_url(url)
    if not declared or not normalized_url:
        return ""
    url_identity = document_identity_from_parts(url=normalized_url)
    if declared == url_identity:
        return declared
    if not declared.startswith("document:"):
        return ""
    parts = declared.split(":", 2)
    if len(parts) != 3:
        return ""
    _prefix, host, document_id = parts
    rebound = document_identity_from_parts(
        document_id=document_id,
        host=host,
        url=normalized_url,
    )
    return declared if rebound == declared else ""


def document_identity_components(value: str) -> tuple[str, str]:
    """검증된 ``document:host:id`` identity의 host와 문서 ID를 꺼낸다."""

    normalized = str(value or "").strip()
    if not normalized.startswith("document:"):
        return "", ""
    parts = normalized.split(":", 2)
    if len(parts) != 3 or not parts[1] or not parts[2]:
        return "", ""
    return parts[1], parts[2]
