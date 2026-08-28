"""여러 보고서 기능이 공유하는 독립 문서 신원 함수."""

from __future__ import annotations

import unicodedata
from typing import Protocol
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit


_KNOWN_DOCUMENT_HOSTS = frozenset(
    {"dart.fss.or.kr", "opendart.fss.or.kr", "kind.krx.co.kr"}
)
_TRACKING_QUERY_KEYS = frozenset({"fbclid", "gclid", "msclkid"})


class DocumentIdentityInput(Protocol):
    """기존 Source가 호출 경계에서 만족하는 최소 문서 신원."""

    document_id: str
    host: str
    url: str


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

    return document_identity_from_parts(
        document_id=source.document_id,
        host=source.host,
        url=source.url,
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
