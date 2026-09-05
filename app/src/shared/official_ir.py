"""검증된 회사 공식 IR PDF의 문서 시점 계약.

수집일을 PDF 발행일로 대신하지 않는다. 회사의 공식 IR 상세페이지·링크
라벨이나 PDF 표지에 들어 있던 발행일과 명시적 보고기간을 수집기가 함께
봉인한 경우만 Writer와 canonical 출처 후보로 쓴다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
import ipaddress
import json
import re
from typing import Any, Mapping
import urllib.parse


IR_METADATA_VERIFICATION_FIELD = "IR문서메타검증"
IR_METADATA_VERIFICATION_VALUE = "official_anchor_exact_date_period"
IR_METADATA_VERIFICATION_VALUE_COVER = "official_cover_exact_date_period"
IR_METADATA_VERIFICATION_VALUES = frozenset(
    {IR_METADATA_VERIFICATION_VALUE, IR_METADATA_VERIFICATION_VALUE_COVER}
)
IR_REPORTING_PERIOD_FIELD = "기준기간"
IR_ATTACHMENT_URL_FIELD = "첨부URL"
IR_COLLECTED_ON_FIELD = "IR수집기준일"
IR_DART_WWW_REDIRECT_FIELD = "DARTwww리다이렉트검증"
IR_DART_WWW_REDIRECT_VALUE = "https_apex_to_www_redirect"
IR_DART_WWW_REDIRECT_FROM_FIELD = "DARTwww원본host"
IR_DART_WWW_REDIRECT_TO_FIELD = "DARTwww최종host"
MAX_IR_PUBLICATION_LAG_DAYS = 190
IR_COVER_METADATA_MAX_PAGES = 2

_ISO_DATE = re.compile(r"20\d{2}-\d{2}-\d{2}")
_COVER_DATE_TOKEN = (
    r"20\d{2}\s*(?:년|[-./])\s*\d{1,2}\s*(?:월|[-./])\s*\d{1,2}\s*일?"
)
_COVER_DATE = re.compile(rf"(?<!\d)(?P<date>{_COVER_DATE_TOKEN})(?!\d)")
_COVER_PUBLICATION_DATE = (
    re.compile(
        rf"(?:발행|작성|공시|게시)\s*일?\s*[:：]?\s*"
        rf"(?P<date>{_COVER_DATE_TOKEN})(?!\d)"
    ),
    re.compile(
        rf"(?<!\d)(?P<date>{_COVER_DATE_TOKEN})\s*"
        rf"(?:발행|작성|공시|게시)\s*일?"
    ),
)
_COVER_REFERENCE_DATE = (
    re.compile(
        rf"기준\s*일?\s*[:：]?\s*(?P<date>{_COVER_DATE_TOKEN})(?!\d)"
    ),
    re.compile(
        rf"(?<!\d)(?P<date>{_COVER_DATE_TOKEN})\s*기준\s*일?"
    ),
)
_REPORTING_PERIOD = re.compile(
    r"(?:20\d{2}-(?:Q[1-4]|H[12]|FY)|"
    r"20\d{2}-\d{2}-\d{2}/20\d{2}-\d{2}-\d{2})"
)
_ANCHOR_QUARTER = (
    re.compile(r"(?<!\d)(?P<year>20\d{2}|\d{2})\s*년?\s*(?P<q>[1-4])\s*분기"),
    re.compile(
        r"(?<![A-Z0-9])FY\s*(?P<year>20\d{2}|\d{2})\s*Q\s*(?P<q>[1-4])(?!\d)",
        re.I,
    ),
    re.compile(r"(?<![A-Z0-9])(?P<q>[1-4])\s*Q\s*(?P<year>20\d{2}|\d{2})(?!\d)", re.I),
    re.compile(r"(?<!\d)(?P<year>20\d{2})\s*Q\s*(?P<q>[1-4])(?!\d)", re.I),
)
_ANCHOR_HALF = re.compile(
    r"(?<!\d)(?P<year>20\d{2}|\d{2})\s*년?\s*(?P<half>상|하)반기"
)
_ANCHOR_FY = re.compile(
    r"(?:(?<![A-Z0-9])FY\s*(?P<fy>20\d{2}|\d{2})(?!\d|\s*Q\s*[1-4])|"
    r"(?<!\d)(?P<year>20\d{2}|\d{2})\s*년\s*"
    r"(?:연간|연간실적|전체연도|사업연도))",
    re.I,
)
_ANCHOR_RANGE = re.compile(
    r"(?P<start>20\d{2}[-./]\d{1,2}[-./]\d{1,2})\s*"
    r"(?:~|∼|–|—|to)\s*"
    r"(?P<end>20\d{2}[-./]\d{1,2}[-./]\d{1,2})",
    re.I,
)


def _four_digit_year(raw: str) -> int:
    value = int(raw)
    return 2000 + value if value < 100 else value


def _iso_date(raw: str) -> str:
    value = re.sub(r"[./]", "-", str(raw or "").strip())
    parts = value.split("-")
    if len(parts) != 3:
        return ""
    try:
        parsed = date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return ""
    return parsed.isoformat()


def _cover_iso_date(raw: str) -> str:
    parts = re.findall(r"\d+", str(raw or ""))
    if len(parts) != 3:
        return ""
    try:
        parsed = date(int(parts[0]), int(parts[1]), int(parts[2]))
    except ValueError:
        return ""
    return parsed.isoformat()


def _reporting_period_candidates(text: str) -> set[str]:
    periods: set[str] = set()
    for match in _ANCHOR_QUARTER[0].finditer(text):
        periods.add(f"{_four_digit_year(match.group('year')):04d}-Q{match.group('q')}")
    for pattern in _ANCHOR_QUARTER[1:]:
        for match in pattern.finditer(text):
            periods.add(
                f"{_four_digit_year(match.group('year')):04d}-Q{match.group('q')}"
            )
    for match in _ANCHOR_HALF.finditer(text):
        half = "1" if match.group("half") == "상" else "2"
        periods.add(f"{_four_digit_year(match.group('year')):04d}-H{half}")
    for match in _ANCHOR_FY.finditer(text):
        year = match.group("fy") or match.group("year")
        periods.add(f"{_four_digit_year(year):04d}-FY")
    for match in _ANCHOR_RANGE.finditer(text):
        start = _iso_date(match.group("start"))
        end = _iso_date(match.group("end"))
        if start and end and start <= end:
            periods.add(f"{start}/{end}")
    return periods


def extract_official_ir_anchor_metadata(label: str) -> tuple[str, str]:
    """공식 anchor 문구의 단일 ISO 날짜와 단일 보고기간만 반환한다.

    날짜나 기간이 서로 다른 값으로 두 번 이상 나오면 임의로 고르지 않고
    빈 값을 돌려준다.
    """

    text = " ".join(str(label or "").split())
    dates = {_iso_date(value) for value in _ISO_DATE.findall(text)} - {""}
    periods = _reporting_period_candidates(text)
    return (
        next(iter(dates)) if len(dates) == 1 else "",
        next(iter(periods)) if len(periods) == 1 else "",
    )


def extract_official_ir_cover_metadata(pages: Sequence[str]) -> tuple[str, str]:
    """PDF 앞쪽 표지 글자의 단일 발행일과 단일 보고기간만 반환한다.

    발행 표지어가 붙은 날짜는 기준일·보고기간 범위 날짜와 구분한다. 그 밖의
    서로 다른 날짜나 보고기간이 둘 이상이면 임의로 고르지 않는다.
    """

    text = " ".join(
        " ".join(str(page or "").split())
        for page in tuple(pages)[:IR_COVER_METADATA_MAX_PAGES]
    ).strip()
    dates = {
        _cover_iso_date(match.group("date")) for match in _COVER_DATE.finditer(text)
    } - {""}
    publication_dates = {
        _cover_iso_date(match.group("date"))
        for pattern in _COVER_PUBLICATION_DATE
        for match in pattern.finditer(text)
    } - {""}
    reference_dates = {
        _cover_iso_date(match.group("date"))
        for pattern in _COVER_REFERENCE_DATE
        for match in pattern.finditer(text)
    } - {""}
    period_range_dates = {
        value
        for match in _ANCHOR_RANGE.finditer(text)
        for value in (_iso_date(match.group("start")), _iso_date(match.group("end")))
        if value
    }

    published_at = ""
    if len(publication_dates) == 1:
        candidate = next(iter(publication_dates))
        other_dates = dates - {candidate}
        if other_dates <= reference_dates | period_range_dates:
            published_at = candidate
    elif not publication_dates and len(dates) == 1 and not reference_dates:
        published_at = next(iter(dates))

    periods = _reporting_period_candidates(text)
    if not periods and len(reference_dates) == 1:
        reference_date = date.fromisoformat(next(iter(reference_dates)))
        if (reference_date.month, reference_date.day) == (12, 31):
            periods.add(f"{reference_date.year:04d}-FY")
    reporting_period = next(iter(periods)) if len(periods) == 1 else ""
    return published_at, reporting_period


def reporting_period_is_valid(value: str) -> bool:
    """임의 자유문자열이 아닌 닫힌 보고기간인지 확인한다."""

    raw = str(value or "").strip()
    if _REPORTING_PERIOD.fullmatch(raw) is None:
        return False
    if "/" not in raw:
        return True
    start_raw, end_raw = raw.split("/", 1)
    try:
        return date.fromisoformat(start_raw) <= date.fromisoformat(end_raw)
    except ValueError:
        return False


def _reporting_period_end(value: str) -> date | None:
    """닫힌 보고기간의 마지막 날을 돌려준다."""

    raw = str(value or "").strip()
    if not reporting_period_is_valid(raw):
        return None
    if "/" in raw:
        return date.fromisoformat(raw.split("/", 1)[1])
    year = int(raw[:4])
    suffix = raw[5:]
    month_and_day = {
        "Q1": (3, 31),
        "Q2": (6, 30),
        "Q3": (9, 30),
        "Q4": (12, 31),
        "H1": (6, 30),
        "H2": (12, 31),
        "FY": (12, 31),
    }.get(suffix)
    if month_and_day is None:
        return None
    return date(year, *month_and_day)


def official_ir_time_is_usable(
    *,
    published_at: str,
    reporting_period: str,
    reference_date: str,
    max_age_days: int = 400,
) -> bool:
    """기간 종료·발행·수집일의 순서와 현재성 상한을 함께 확인한다."""

    period_end = _reporting_period_end(reporting_period)
    if period_end is None:
        return False
    try:
        published = date.fromisoformat(str(published_at or "").strip())
        reference = date.fromisoformat(str(reference_date or "").strip())
    except ValueError:
        return False
    age_days = (reference - published).days
    publication_lag_days = (published - period_end).days
    return (
        0 <= age_days <= max_age_days
        and 0 <= publication_lag_days <= MAX_IR_PUBLICATION_LAG_DAYS
    )


def verified_official_ir_fragment_is_usable(
    fragment: Mapping[str, Any], *, reference_date: str
) -> bool:
    """수집기 메타데이터와 DART 법인·도메인 결속을 모두 요구한다."""

    if str(fragment.get("종류") or "").strip() != "공식 IR":
        return False
    if (
        str(fragment.get(IR_METADATA_VERIFICATION_FIELD) or "").strip()
        not in IR_METADATA_VERIFICATION_VALUES
        or not str(fragment.get("발행처") or "").strip()
        or not str(fragment.get("도메인근거SourceID") or "").strip()
        or not str(fragment.get("도메인근거원문") or "").strip()
        or not safe_https_attachment_url(
            str(fragment.get(IR_ATTACHMENT_URL_FIELD) or "")
        )
        or str(fragment.get(IR_COLLECTED_ON_FIELD) or "").strip()
        != str(reference_date or "").strip()
    ):
        return False
    try:
        profile = json.loads(str(fragment.get("도메인근거원문") or ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    if not isinstance(profile, dict):
        return False
    dart_host = dart_homepage_exact_host(str(profile.get("hm_url") or ""))
    source_url = safe_https_attachment_url(str(fragment.get("출처") or ""))
    try:
        source_host = (
            urllib.parse.urlsplit(source_url).hostname or ""
        ).casefold().rstrip(".")
    except ValueError:
        return False
    if not dart_host or not source_host:
        return False
    if source_host != dart_host and not dart_www_redirect_is_valid(
        verification=str(fragment.get(IR_DART_WWW_REDIRECT_FIELD) or ""),
        from_host=str(fragment.get(IR_DART_WWW_REDIRECT_FROM_FIELD) or ""),
        to_host=str(fragment.get(IR_DART_WWW_REDIRECT_TO_FIELD) or ""),
        dart_host=dart_host,
        source_host=source_host,
    ):
        return False
    return official_ir_time_is_usable(
        published_at=str(fragment.get("문서일") or ""),
        reporting_period=str(fragment.get(IR_REPORTING_PERIOD_FIELD) or ""),
        reference_date=reference_date,
    )


def safe_https_attachment_url(value: str) -> str:
    """공식 페이지가 직접 걸어 준 외부 PDF 첨부 URL의 최소 형식 검사."""

    raw = str(value or "").strip()
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii")
        port = parsed.port
    except (TypeError, ValueError, UnicodeError):
        return ""
    if (
        parsed.scheme.casefold() != "https"
        or not host
        or "." not in host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or "\\" in raw
        or any(ord(character) < 32 for character in raw)
    ):
        return ""
    path = urllib.parse.quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    return urllib.parse.urlunsplit(("https", host.casefold(), path, query, ""))


def dart_homepage_host_aliases(value: str) -> frozenset[str]:
    """DART host와 그 host의 제한된 ``www.`` 별칭만 반환한다.

    DART가 apex를 적었지만 공개 홈페이지가 ``www``에서만 열리는 경우를 위한
    닫힌 규칙이다. 임의 하위 도메인, IP, 포트, 자격증명은 허용하지 않는다.
    """

    raw = str(value or "").strip()
    if not raw or "\\" in raw or any(ord(character) < 32 for character in raw):
        return frozenset()
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii")
        port = parsed.port
    except (TypeError, ValueError, UnicodeError):
        return frozenset()
    normalized = host.casefold()
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not normalized
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or "." not in normalized
        or normalized == "localhost"
        or normalized.endswith(".localhost")
    ):
        return frozenset()
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        address = None
    if address is not None:
        return frozenset()
    aliases = {normalized}
    if not normalized.startswith("www."):
        aliases.add(f"www.{normalized}")
    return frozenset(aliases)


def dart_homepage_exact_host(value: str) -> str:
    """안전한 DART 홈페이지 값의 원래 host 하나만 반환한다."""

    raw = str(value or "").strip()
    if not raw or "\\" in raw or any(ord(character) < 32 for character in raw):
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urllib.parse.urlsplit(raw)
        host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii")
    except (TypeError, ValueError, UnicodeError):
        return ""
    normalized = host.casefold()
    return normalized if normalized in dart_homepage_host_aliases(raw) else ""


def dart_homepage_www_alias_url(value: str) -> str:
    """안전한 DART apex URL에 대해서만 같은 경로의 ``www`` HTTPS URL을 만든다."""

    raw = str(value or "").strip()
    if not raw or "\\" in raw or any(ord(character) < 32 for character in raw):
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urllib.parse.urlsplit(raw)
        current_host = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii")
    except (TypeError, ValueError, UnicodeError):
        return ""
    aliases = dart_homepage_host_aliases(raw)
    alias_host = f"www.{current_host.casefold()}"
    if alias_host not in aliases or current_host.casefold().startswith("www."):
        return ""
    path = urllib.parse.quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parsed.query, safe="/%?:@!$&'()*+,;=-._~")
    return urllib.parse.urlunsplit(("https", alias_host, path, query, ""))


def dart_www_redirect_is_valid(
    *,
    verification: str,
    from_host: str,
    to_host: str,
    dart_host: str,
    source_host: str,
) -> bool:
    """실제 HTTPS apex→www probe를 기록한 닫힌 marker인지 확인한다."""

    normalized_dart = str(dart_host or "").strip().casefold().rstrip(".")
    normalized_source = str(source_host or "").strip().casefold().rstrip(".")
    return bool(
        verification == IR_DART_WWW_REDIRECT_VALUE
        and normalized_dart
        and not normalized_dart.startswith("www.")
        and str(from_host or "").strip().casefold().rstrip(".") == normalized_dart
        and str(to_host or "").strip().casefold().rstrip(".")
        == f"www.{normalized_dart}"
        and normalized_source == f"www.{normalized_dart}"
    )
