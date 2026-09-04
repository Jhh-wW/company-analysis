"""OpenDART 클라이언트 — 사용량 계수기 내장 (착수 순서 3번의 「계수기 v1 필수」).

키는 환경변수 DART_API_KEY로만 받는다(하드코딩 금지). .env는 프로그램만 읽는다.
일일 한도: 키당 20,000건 (공식 문서가 오류 020으로 정의한 값). 계수기가 로컬에서 세고,
경보 문턱을 넘으면 경고, 한도면 예외를 던져 「재시도할수록 더 막히는」 상황을 차단한다.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import io
import ipaddress
import json
import os
import re
import struct
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.runtime_paths import ENV_DATA_ROOT, LOCAL_LOG_DIR, runtime_log_dir
from core import credentialed_http, usage_store

DAILY_LIMIT = 20_000          # 공식 한도 (오류 020)
WARN_RATIO = 0.8              # 경보 문턱 — 80% 소진 시 경고
BASE_URL = "https://opendart.fss.or.kr/api"
TIMEOUT_LIGHT_SEC = 10        # 회사·목록 조회
JSON_RESPONSE_MAX_BYTES = 4 * 1024 * 1024
COUNTER_FILENAME = "dart_usage.json"
# 예전 코드가 가져다 쓸 수 있도록 남겨 둔 로컬 기본값이다.
COUNTER_PATH = LOCAL_LOG_DIR / COUNTER_FILENAME

ERR_NO_DATA = "013"           # 원래 없다 — 즉시 포기
ERR_LIMIT = "020"             # 한도 소진 — 즉시 중단


class DartClientError(RuntimeError):
    """비밀값·원문을 노출하지 않는 DART 어댑터 오류."""


class DartLimitReached(DartClientError):
    """일일 한도 도달 — 오늘은 더 부르지 않는다."""


class DartAuthenticationError(DartClientError):
    """API 키·접근 권한이 거부되었다."""


class DartTransportError(DartClientError):
    """타임아웃·네트워크 오류로 응답을 확정할 수 없다."""


class DartResponseError(DartClientError):
    """DART가 예상한 계약과 다른 응답을 돌려줬다."""


#: 인증이 죽은 상태들 — 회사의 문제가 아니라 «열쇠»의 문제다.
#: ★ 901 추가 (적대 검수) — 공식 코드표에서 901 은
#:   「사용자 계정의 개인정보 보유기간이 만료되어 사용할 수 없는 키」다.
#:   여기 없으면 열쇠가 죽었는데도 회사마다 오류로 나와, 배치가 남은 회사를
#:   전부 돌며 DART 호출만 계속 태운다. 열쇠가 죽었으면 즉시 멈추는 게 맞다.
#:   출처: https://opendart.fss.or.kr/guide/detail.do?apiGrpCd=DS003&apiId=2019016
_AUTH_STATUSES = frozenset({"010", "011", "012", "901"})
_STATUS_PATTERNS = (
    re.compile(rb"<status>\s*([0-9]{3})\s*</status>", re.IGNORECASE),
    re.compile(rb'"status"\s*:\s*"([0-9]{3})"', re.IGNORECASE),
)

_NO_REDIRECT_OPENER = credentialed_http.build_no_redirect_opener()


def _urlopen(url: str, *, timeout: float):
    """고정 DART API 요청을 API key redirect 없이 한 번만 연다."""

    return _NO_REDIRECT_OPENER.open(url, timeout=timeout)


def _read_url(
    url: str,
    *,
    timeout: float,
    max_bytes: int,
    response_label: str,
) -> bytes:
    """URL·키·원문을 예외에 싣지 않고 응답 바이트를 읽는다."""
    try:
        with _urlopen(url, timeout=timeout) as response:
            credentialed_http.require_exact_response_url(
                response,
                expected_url=url,
            )
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise DartResponseError(
                    f"{response_label} 응답이 허용 크기를 초과했습니다"
                )
            return data
    except urllib.error.HTTPError as error:
        if error.code == 429:
            raise DartLimitReached("DART HTTP 429 — 사용량 한도 도달") from None
        if error.code in {401, 403}:
            raise DartAuthenticationError("DART API 인증이 거부되었습니다") from None
        raise DartResponseError(
            f"DART API가 HTTP {error.code} 오류를 돌려줬습니다"
        ) from None
    except credentialed_http.CredentialedHTTPContractError:
        raise DartResponseError("DART API 응답 위치가 올바르지 않습니다") from None
    except (urllib.error.URLError, TimeoutError, OSError):
        raise DartTransportError("DART API와 통신하지 못했습니다") from None


def _status_from_error_body(data: bytes) -> str:
    """오류 원문을 반사하지 않고 3자리 상태 코드만 읽는다."""
    for pattern in _STATUS_PATTERNS:
        matched = pattern.search(data[:4096])
        if matched is not None:
            return matched.group(1).decode("ascii")
    return ""


def _raise_download_response_error(label: str, data: bytes) -> None:
    status = _status_from_error_body(data)
    if status == ERR_LIMIT:
        raise DartLimitReached(f"{label} 응답 020 — 서버 기준 한도 소진")
    if status in _AUTH_STATUSES:
        raise DartAuthenticationError(f"{label} DART API 인증이 거부되었습니다")
    raise DartResponseError(f"{label} 다운로드 응답 형식이 올바르지 않습니다")


class UsageCounter:
    """날짜별 호출 수를 파일로 세는 계수기 (프로세스 재시작에도 유지)."""

    def __init__(self, path: Path | None = None, limit: int = DAILY_LIMIT) -> None:
        # 기본 인자에서 경로를 미리 계산하지 않는다. 그래야 배포 환경변수를 읽은 뒤
        # 객체를 만들었을 때 Render 영속 디스크 경로가 정확히 적용된다.
        self.path = path if path is not None else default_counter_path()
        self.limit = limit

    def today_count(self, today: str | None = None) -> int:
        key = today or dt.date.today().isoformat()
        return usage_store.today_count(self.path, key)

    def tick(self, today: str | None = None) -> int:
        """호출 1건 기록. 한도면 DartLimitReached, 경보 문턱이면 경고 출력."""
        key = today or dt.date.today().isoformat()
        try:
            count = usage_store.tick(self.path, key, self.limit)
        except usage_store.UsageLimitReached as exc:
            raise DartLimitReached(
                f"DART 일일 한도({self.limit}건) 도달 — 내일까지 중단"
            ) from exc
        if count >= int(self.limit * WARN_RATIO):
            print(f"⚠️ DART 사용량 {count}/{self.limit} — 경보 문턱({WARN_RATIO:.0%}) 초과")
        return count


def default_counter_path() -> Path:
    """DART 사용량 기록 경로를 돌려준다.

    로컬에서는 기존 ``analysis_engine/logs``를 그대로 쓴다. 배포에서는
    ``APP_DATA_ROOT=/var/data``로 두면 ``/var/data/logs``에 기록되어 서버를
    다시 시작해도 계수기가 유지된다.
    """
    return runtime_log_dir() / COUNTER_FILENAME


def api_key() -> str:
    key = os.environ.get("DART_API_KEY", "").strip()
    if not key:
        raise DartAuthenticationError(
            "DART_API_KEY가 없습니다 — analysis_engine/.env 에 'DART_API_KEY=발급키' 한 줄을 넣고, "
            "실행 전 환경변수로 로드하세요 (opendart.fss.or.kr 개인 즉시 발급·무료)"
        )
    return key


def get_json(endpoint: str, params: dict[str, Any],
             counter: UsageCounter | None = None) -> dict[str, Any]:
    """가벼운 JSON API 호출 (company.json · list.json). 호출 전 계수기 tick."""
    key = api_key()
    query = urllib.parse.urlencode({"crtfc_key": key, **params})
    (counter or UsageCounter()).tick()
    data = _read_url(
        f"{BASE_URL}/{endpoint}?{query}",
        timeout=TIMEOUT_LIGHT_SEC,
        max_bytes=JSON_RESPONSE_MAX_BYTES,
        response_label="DART JSON",
    )
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise DartResponseError("DART JSON 응답을 해석하지 못했습니다") from None
    if not isinstance(payload, dict):
        raise DartResponseError("DART JSON 응답 형식이 올바르지 않습니다")
    status = payload.get("status")
    if not isinstance(status, str) or not status:
        raise DartResponseError("DART JSON 응답에 상태 코드가 없습니다")
    if status == ERR_LIMIT:
        raise DartLimitReached("DART 응답 020 — 서버 기준 한도 소진")
    if status in _AUTH_STATUSES:
        raise DartAuthenticationError("DART API 인증이 거부되었습니다")
    return payload


TIMEOUT_CORPCODE_SEC = 60     # corpCode.zip 약 1~2MB
TIMEOUT_DOCUMENT_SEC = 60     # 공시서류 원본 zip — 감사보고서는 보통 수백 KB
CORPCODE_ZIP_RESPONSE_MAX_BYTES = 16 * 1024 * 1024
CORPCODE_XML_MAX_BYTES = 64 * 1024 * 1024
CORPCODE_ZIP_TOTAL_UNCOMPRESSED_MAX_BYTES = 64 * 1024 * 1024
CORPCODE_ZIP_MAX_MEMBERS = 8
CORPCODE_ZIP_CENTRAL_DIRECTORY_MAX_BYTES = 256 * 1024
DOCUMENT_ZIP_RESPONSE_MAX_BYTES = 32 * 1024 * 1024
DOCUMENT_MEMBER_MAX_BYTES = 64 * 1024 * 1024
DOCUMENT_ZIP_TOTAL_UNCOMPRESSED_MAX_BYTES = 128 * 1024 * 1024
DOCUMENT_ZIP_MAX_MEMBERS = 512
DOCUMENT_ZIP_CENTRAL_DIRECTORY_MAX_BYTES = 4 * 1024 * 1024
ZIP_MEMBER_MAX_COMPRESSION_RATIO = 200
DOCUMENT_URL_SIDECAR_VERSION = "dart-document-official-url-candidates-v1"
DOCUMENT_URL_SIDECAR_MAX_BYTES = 128 * 1024
DOCUMENT_URL_SIDECAR_MAX_CANDIDATES = 12
DOCUMENT_URL_SIDECAR_MEMBER_NAME_MAX_CHARS = 512
_ZIP_END_SIGNATURE = b"PK\x05\x06"
_ZIP_END_RECORD = struct.Struct("<4s4H2LH")
_ZIP_END_MIN_BYTES = _ZIP_END_RECORD.size
_ZIP_MAX_COMMENT_BYTES = (1 << 16) - 1
_DOCUMENT_WEB_URL_PATTERN = re.compile(
    r"(?<![\w@])(?:https?://|www\.)[^\s<>\"']{3,2048}",
    re.IGNORECASE,
)
_DOCUMENT_URL_STRONG_LABEL_SIGNALS = (
    "홈페이지",
    "웹사이트",
    "공식 사이트",
    "공식사이트",
    "website",
    "homepage",
)
_DOCUMENT_URL_WEAK_LABEL_SIGNALS = ("url",)
_DOCUMENT_NON_COMPANY_INFRA_HOST_SUFFIXES = (
    "dart.fss.or.kr",
    "opendart.fss.or.kr",
    "fss.or.kr",
    "w3.org",
    "xbrl.or.kr",
    "xml.or.kr",
)
_DOCUMENT_NON_HTML_URL_SUFFIXES = (
    ".css",
    ".js",
    ".json",
    ".xml",
    ".xsd",
    ".dtd",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".zip",
    ".pdf",
)
_SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
_DART_RECEIPT_NUMBER_RE = re.compile(r"[0-9]{14}")
_DOCUMENT_URL_SIDECAR_LOCATION_RE = re.compile(
    r"raw_xml_chars:([0-9]{1,10})-([0-9]{1,10})"
)
_DOCUMENT_URL_SIDECAR_TOP_LEVEL_KEYS = frozenset(
    {"version", "rcept_no", "main_document_sha256", "candidates"}
)
_DOCUMENT_URL_SIDECAR_CANDIDATE_KEYS = frozenset(
    {"url", "source_member_name", "source_location", "source_payload_sha256"}
)
_DOCUMENT_CACHE_LOCK_COUNT = 64
_DOCUMENT_CACHE_LOCKS = tuple(
    threading.Lock() for _index in range(_DOCUMENT_CACHE_LOCK_COUNT)
)


@dataclass(frozen=True)
class DocumentUrlSidecarCandidate:
    """DART ZIP 멤버에서 찾았지만 아직 회사 공식 여부는 모르는 URL."""

    url: str
    source_member_name: str
    source_location: str
    source_payload_sha256: str


@dataclass(frozen=True)
class DocumentUrlSidecarLoadResult:
    """로컬 sidecar의 결속 검증 결과와 아직 미승격인 URL 후보."""

    is_valid: bool
    candidates: tuple[DocumentUrlSidecarCandidate, ...] = ()


def _decode_document_member(raw: bytes) -> str:
    for encoding in ("utf-8", "cp949", "euc-kr"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def is_document_xml_payload(raw: bytes) -> bool:
    """확장자만 XML인 바이너리를 대표 공시나 URL 출처로 쓰지 않는다."""

    text = _decode_document_member(raw).lstrip("\ufeff \t\r\n")
    return text.startswith("<") and ">" in text[:4096]


def _safe_sidecar_member_name(value: object) -> str:
    """ZIP 이름을 경로로 사용하지 않는 제한된 provenance 문자열로 만든다."""

    name = str(value or "").replace("\\", "/")
    parts = name.split("/")
    if (
        not name
        or len(name) > DOCUMENT_URL_SIDECAR_MEMBER_NAME_MAX_CHARS
        or name.startswith("/")
        or any(part in {"", ".", ".."} for part in parts)
        or any(ord(character) < 32 for character in name)
    ):
        return ""
    return name


def normalize_document_web_url(value: object) -> str:
    """공시 원문·sidecar가 함께 쓰는 닫힌 웹 URL 정규화 경계."""

    if type(value) is not str:
        return ""
    candidate = value.strip()
    if (
        not candidate
        or len(candidate) > 2048
        or "\\" in candidate
        or any(ord(character) < 32 for character in candidate)
    ):
        return ""
    try:
        parsed = urllib.parse.urlsplit(candidate)
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
    default_port = 80 if scheme == "http" else 443 if scheme == "https" else None
    if (
        default_port is None
        or not host
        or "." not in host
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, default_port)
        or any(
            host == suffix or host.endswith(f".{suffix}")
            for suffix in _DOCUMENT_NON_COMPANY_INFRA_HOST_SUFFIXES
        )
        or parsed.path.casefold().endswith(_DOCUMENT_NON_HTML_URL_SUFFIXES)
    ):
        return ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return ""
    # default port의 표기 유무나 Unicode host 표기로 후보가 중복되지 않게
    # authority를 직접 만든다. fragment는 공시 출처 위치와 무관해 버린다.
    return urllib.parse.urlunsplit(
        (scheme, host, parsed.path or "/", parsed.query, "")
    )


def _iter_ranked_document_web_url_candidates(
    raw: bytes,
    *,
    member_name: str,
) -> Iterator[tuple[int, int, DocumentUrlSidecarCandidate]]:
    """멤버 한 건의 explicit URL을 위치·payload hash와 함께 하나씩 채점한다.

    최종 sidecar는 12개뿐인데 중간 ``list``와 ``set``을 원문의 URL 수만큼
    키우면, 압축은 작고 해제 후에는 URL이 매우 많은 XML 하나로 프로세스
    메모리를 소진할 수 있다. 따라서 이 함수는 후보를 보관하지 않고
    스트리밍하며, 아래 bounded top-k 정본만 메모리에 후보를 남긴다.
    """

    safe_member_name = _safe_sidecar_member_name(member_name)
    if not safe_member_name:
        return
    decoded = _decode_document_member(raw)
    payload_sha256 = hashlib.sha256(raw).hexdigest()
    for match in _DOCUMENT_WEB_URL_PATTERN.finditer(decoded):
        value = html.unescape(match.group(0)).rstrip(".,;:!?)]}〉》。·")
        if value.casefold().startswith("www."):
            value = f"https://{value}"
        normalized = normalize_document_web_url(value)
        if not normalized:
            continue
        parsed = urllib.parse.urlsplit(normalized)
        context = decoded[
            max(0, match.start() - 120) : min(len(decoded), match.end() + 120)
        ].casefold()
        score = 0
        if any(
            signal in context for signal in _DOCUMENT_URL_STRONG_LABEL_SIGNALS
        ):
            score += 100
        elif any(
            signal in context for signal in _DOCUMENT_URL_WEAK_LABEL_SIGNALS
        ):
            # XML tag/속성명에 흔한 ``URL`` 하나를 「공식 홈페이지」와 같은
            # 강도로 보면 앞쪽 외부 링크 12개가 실제 홈페이지를 밀어낸다.
            score += 10
        if parsed.path in ("", "/") and not parsed.query:
            score += 20
        if match.group(0).casefold().startswith("www."):
            score += 5
        yield (
            -score,
            match.start(),
            DocumentUrlSidecarCandidate(
                url=normalized,
                source_member_name=safe_member_name,
                source_location=f"raw_xml_chars:{match.start()}-{match.end()}",
                source_payload_sha256=payload_sha256,
            ),
        )


def _ranked_candidate_key(
    item: tuple[int, int, DocumentUrlSidecarCandidate],
) -> tuple[int, str, int, str]:
    return (
        item[0],
        item[2].source_member_name.casefold(),
        item[1],
        item[2].url,
    )


def _retain_bounded_ranked_candidate(
    selected_by_url: dict[
        str, tuple[int, int, DocumentUrlSidecarCandidate]
    ],
    item: tuple[int, int, DocumentUrlSidecarCandidate],
    *,
    max_candidates: int,
) -> None:
    """URL별 최선의 provenance 중 전역 top-k만 일정한 메모리로 보존한다."""

    if max_candidates <= 0:
        return
    url = item[2].url
    previous = selected_by_url.get(url)
    if previous is not None:
        if _ranked_candidate_key(item) < _ranked_candidate_key(previous):
            selected_by_url[url] = item
        return
    if len(selected_by_url) < max_candidates:
        selected_by_url[url] = item
        return
    worst_url, worst = max(
        selected_by_url.items(),
        key=lambda entry: _ranked_candidate_key(entry[1]),
    )
    if _ranked_candidate_key(item) < _ranked_candidate_key(worst):
        del selected_by_url[worst_url]
        selected_by_url[url] = item


def _ranked_document_web_url_candidates(
    raw: bytes,
    *,
    member_name: str,
    max_candidates: int = DOCUMENT_URL_SIDECAR_MAX_CANDIDATES,
) -> list[tuple[int, int, DocumentUrlSidecarCandidate]]:
    """호환용 멤버 단위 bounded top-k 후보를 돌려준다."""

    selected_by_url: dict[
        str, tuple[int, int, DocumentUrlSidecarCandidate]
    ] = {}
    for item in _iter_ranked_document_web_url_candidates(
        raw, member_name=member_name
    ):
        _retain_bounded_ranked_candidate(
            selected_by_url,
            item,
            max_candidates=max_candidates,
        )
    return list(selected_by_url.values())


def extract_document_web_url_candidates(
    raw: bytes,
    *,
    member_name: str,
    max_candidates: int = DOCUMENT_URL_SIDECAR_MAX_CANDIDATES,
) -> tuple[DocumentUrlSidecarCandidate, ...]:
    """대표 XML fallback도 sidecar와 똑같은 URL 정책을 쓰게 하는 정본."""

    limit = max(0, min(int(max_candidates), DOCUMENT_URL_SIDECAR_MAX_CANDIDATES))
    ranked = _ranked_document_web_url_candidates(
        raw,
        member_name=member_name,
        max_candidates=limit,
    )
    ranked.sort(key=lambda item: (item[0], item[1], item[2].url))
    return tuple(candidate for _score, _start, candidate in ranked[:limit])


def document_url_sidecar_path(document_path: Path) -> Path:
    """대표 XML 옆 versioned 공식 URL 후보 sidecar 경로."""

    return document_path.with_name(
        f"{document_path.stem}.official-urls-v1.json"
    )


def load_document_url_sidecar(
    document_path: Path,
    *,
    rcept_no: str,
    main_document: bytes,
) -> DocumentUrlSidecarLoadResult:
    """로컬 sidecar의 형식·접수번호·대표 XML 결속만 검증한다.

    이 파일은 DART에서 받은 ZIP을 같은 프로세스가 풀며 만든 **로컬 cache**다.
    작은 member 원문은 개인정보·용량 범위를 넓히지 않기 위해 저장하지 않으므로
    각 ``source_payload_sha256``은 다운로드 당시 provenance이지, 이후 로컬에서
    다시 인증할 수 있는 서명이나 원문 증명은 아니다. 따라서 여기서 통과한 URL도
    그 자체로 공식 사이트가 아니다. 앱은 대상 페이지의 법인명+등록번호와
    same-origin을 별도로 확인한 뒤에만 회사 자료로 승격해야 한다.

    후보가 0개인 정상 sidecar와 깨진/missing sidecar를 cache 갱신 코드가 구분할
    수 있도록 ``is_valid``를 별도로 돌려준다.
    """

    if _DART_RECEIPT_NUMBER_RE.fullmatch(str(rcept_no or "")) is None:
        return DocumentUrlSidecarLoadResult(is_valid=False)
    sidecar_path = document_url_sidecar_path(document_path)
    try:
        with sidecar_path.open("rb") as stream:
            encoded = stream.read(DOCUMENT_URL_SIDECAR_MAX_BYTES + 1)
    except OSError:
        return DocumentUrlSidecarLoadResult(is_valid=False)
    if len(encoded) > DOCUMENT_URL_SIDECAR_MAX_BYTES:
        return DocumentUrlSidecarLoadResult(is_valid=False)
    try:
        payload = json.loads(encoded.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return DocumentUrlSidecarLoadResult(is_valid=False)
    if (
        type(payload) is not dict
        or set(payload) != _DOCUMENT_URL_SIDECAR_TOP_LEVEL_KEYS
        or payload.get("version") != DOCUMENT_URL_SIDECAR_VERSION
        or payload.get("rcept_no") != rcept_no
        or payload.get("main_document_sha256")
        != hashlib.sha256(main_document).hexdigest()
    ):
        return DocumentUrlSidecarLoadResult(is_valid=False)
    rows = payload.get("candidates")
    if (
        type(rows) is not list
        or len(rows) > DOCUMENT_URL_SIDECAR_MAX_CANDIDATES
    ):
        return DocumentUrlSidecarLoadResult(is_valid=False)

    candidates: list[DocumentUrlSidecarCandidate] = []
    seen_urls: set[str] = set()
    for row in rows:
        if (
            type(row) is not dict
            or set(row) != _DOCUMENT_URL_SIDECAR_CANDIDATE_KEYS
            or any(
                type(row.get(key)) is not str
                for key in _DOCUMENT_URL_SIDECAR_CANDIDATE_KEYS
            )
        ):
            return DocumentUrlSidecarLoadResult(is_valid=False)
        url = str(row["url"])
        member_name = str(row["source_member_name"])
        location = str(row["source_location"])
        payload_sha256 = str(row["source_payload_sha256"])
        normalized_url = normalize_document_web_url(url)
        location_match = _DOCUMENT_URL_SIDECAR_LOCATION_RE.fullmatch(location)
        if (
            not normalized_url
            or normalized_url != url
            or _safe_sidecar_member_name(member_name) != member_name
            or location_match is None
            or int(location_match.group(2)) <= int(location_match.group(1))
            or _SHA256_HEX_RE.fullmatch(payload_sha256) is None
            or url in seen_urls
        ):
            return DocumentUrlSidecarLoadResult(is_valid=False)
        seen_urls.add(url)
        candidates.append(
            DocumentUrlSidecarCandidate(
                url=url,
                source_member_name=member_name,
                source_location=location,
                source_payload_sha256=payload_sha256,
            )
        )
    return DocumentUrlSidecarLoadResult(
        is_valid=True,
        candidates=tuple(candidates),
    )


def _read_valid_cached_document(path: Path) -> bytes | None:
    """기존 대표 cache를 bounded read로 확인하고 유효한 XML만 돌려준다."""

    try:
        with path.open("rb") as stream:
            document = stream.read(DOCUMENT_MEMBER_MAX_BYTES + 1)
    except OSError:
        return None
    if (
        len(document) > DOCUMENT_MEMBER_MAX_BYTES
        or not is_document_xml_payload(document)
    ):
        return None
    return document


def _document_cache_lock(rcept_no: str) -> threading.Lock:
    """같은 접수번호 backfill을 프로세스 안에서 한 번만 실행한다."""

    return _DOCUMENT_CACHE_LOCKS[int(rcept_no) % _DOCUMENT_CACHE_LOCK_COUNT]


def _write_private_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temp_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as stream:
            descriptor = None
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        _unlink_cache_artifact(temp_path)


def _unlink_cache_artifact(path: Path) -> None:
    """보조 cache 정리 실패가 원래 다운로드 결과를 가리지 않게 한다."""

    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _document_url_sidecar_bytes(
    *,
    rcept_no: str,
    main_document: bytes,
    ranked_candidates: list[tuple[int, int, DocumentUrlSidecarCandidate]],
) -> bytes:
    ranked_candidates.sort(
        key=lambda item: (
            item[0],
            item[2].source_member_name.casefold(),
            item[1],
            item[2].url,
        )
    )
    rows: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for _score, _start, candidate in ranked_candidates:
        if candidate.url in seen_urls:
            continue
        seen_urls.add(candidate.url)
        rows.append(
            {
                "url": candidate.url,
                "source_member_name": candidate.source_member_name,
                "source_location": candidate.source_location,
                "source_payload_sha256": candidate.source_payload_sha256,
            }
        )
        if len(rows) >= DOCUMENT_URL_SIDECAR_MAX_CANDIDATES:
            break
    payload = {
        "version": DOCUMENT_URL_SIDECAR_VERSION,
        "rcept_no": rcept_no,
        "main_document_sha256": hashlib.sha256(main_document).hexdigest(),
        "candidates": rows,
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(encoded) > DOCUMENT_URL_SIDECAR_MAX_BYTES:
        raise DartResponseError("공시서류 URL 후보 sidecar가 허용 크기를 초과했습니다")
    return encoded


def _validate_zip_container(
    data: bytes,
    *,
    member_max_count: int,
    central_directory_max_bytes: int,
    archive_label: str,
) -> None:
    """ZipFile 객체 생성 전 중앙 디렉터리의 항목 수·크기를 제한한다."""
    search_start = max(0, len(data) - _ZIP_END_MIN_BYTES - _ZIP_MAX_COMMENT_BYTES)
    search_end = len(data) - _ZIP_END_MIN_BYTES
    end_record: tuple[bytes, int, int, int, int, int, int, int] | None = None
    end_offset = -1
    while search_end >= search_start:
        candidate = data.rfind(
            _ZIP_END_SIGNATURE,
            search_start,
            search_end + len(_ZIP_END_SIGNATURE),
        )
        if candidate < 0:
            break
        values = _ZIP_END_RECORD.unpack_from(data, candidate)
        comment_size = int(values[-1])
        if candidate + _ZIP_END_MIN_BYTES + comment_size == len(data):
            end_record = values
            end_offset = candidate
            break
        search_end = candidate - 1
    if end_record is None:
        raise zipfile.BadZipFile("ZIP 종료 레코드를 찾지 못했습니다")

    (
        _signature,
        disk_number,
        central_directory_disk,
        disk_member_count,
        total_member_count,
        central_directory_size,
        central_directory_offset,
        _comment_size,
    ) = end_record
    if (
        disk_number != 0
        or central_directory_disk != 0
        or disk_member_count != total_member_count
    ):
        raise DartResponseError(f"{archive_label}의 분할 ZIP 형식은 허용되지 않습니다")
    if (
        total_member_count == 0xFFFF
        or central_directory_size == 0xFFFFFFFF
        or central_directory_offset == 0xFFFFFFFF
    ):
        raise DartResponseError(f"{archive_label}의 ZIP64 형식은 허용되지 않습니다")
    if total_member_count > member_max_count:
        raise DartResponseError(f"{archive_label} 항목 수가 허용 범위를 초과했습니다")
    if central_directory_size > central_directory_max_bytes:
        raise DartResponseError(
            f"{archive_label}의 중앙 디렉터리 크기가 허용 범위를 초과했습니다"
        )
    if central_directory_offset + central_directory_size > end_offset:
        raise zipfile.BadZipFile("ZIP 중앙 디렉터리 위치가 올바르지 않습니다")


def _validate_zip_archive(
    infos: list[zipfile.ZipInfo],
    *,
    member_max_bytes: int,
    total_max_bytes: int,
    member_max_count: int,
    archive_label: str,
) -> list[zipfile.ZipInfo]:
    """ZIP 중앙 디렉터리의 선언값을 먼저 검증해 과다 해제를 막는다."""
    if not infos:
        raise DartResponseError(f"{archive_label}이 비어 있습니다")
    if len(infos) > member_max_count:
        raise DartResponseError(f"{archive_label} 항목 수가 허용 범위를 초과했습니다")

    files: list[zipfile.ZipInfo] = []
    total_size = 0
    for info in infos:
        if info.is_dir():
            continue
        if info.flag_bits & 0x1:
            raise DartResponseError(f"{archive_label}에 암호화된 항목이 있습니다")
        if info.file_size > member_max_bytes:
            raise DartResponseError(
                f"{archive_label} 항목의 선언 크기가 허용 범위를 초과했습니다"
            )
        total_size += info.file_size
        if total_size > total_max_bytes:
            raise DartResponseError(
                f"{archive_label}의 전체 선언 크기가 허용 범위를 초과했습니다"
            )
        if info.file_size:
            if info.compress_size <= 0:
                raise DartResponseError(f"{archive_label}의 압축 정보가 올바르지 않습니다")
            ratio = info.file_size / info.compress_size
            if ratio > ZIP_MEMBER_MAX_COMPRESSION_RATIO:
                raise DartResponseError(
                    f"{archive_label} 항목의 압축비가 허용 범위를 초과했습니다"
                )
        files.append(info)

    if not files:
        raise DartResponseError(f"{archive_label}에 파일이 없습니다")
    return files


def _read_zip_member(
    archive: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    *,
    max_bytes: int,
    archive_label: str,
) -> bytes:
    """선언값을 신뢰하지 않고 실제 해제 바이트도 상한까지만 읽는다."""
    try:
        with archive.open(info) as source:
            data = source.read(max_bytes + 1)
    except (
        EOFError,
        NotImplementedError,
        OSError,
        RuntimeError,
        ValueError,
        zipfile.BadZipFile,
    ):
        raise DartResponseError(f"{archive_label} 항목을 안전하게 읽지 못했습니다") from None
    if len(data) > max_bytes:
        raise DartResponseError(
            f"{archive_label} 항목의 실제 크기가 허용 범위를 초과했습니다"
        )
    return data


def download_document(
    rcept_no: str,
    dest_dir: Path,
    counter: UsageCounter | None = None,
    *,
    require_official_url_sidecar: bool = False,
) -> Path:
    """공시서류 대표 XML과 모든 XML의 URL 후보 sidecar를 한 호출로 저장한다.

    기존 호출자 계약대로 가장 큰 파일의 ``Path``를 반환한다. 단, 작은 표지·첨부
    XML에만 공식 홈페이지가 적힐 수 있으므로 URL·멤버명·문자 위치·멤버 hash만
    versioned JSON sidecar에 보존한다. 원문 전체나 응답 header·인증값은 싣지 않는다.
    ``require_official_url_sidecar=True``인 FULL 정식 수집은 구버전 warm cache도
    보조 파일까지 backfill하며, 실패를 조용히 자료 부족으로 바꾸지 않고 예외로
    알린다. 기본값은 v1/SHADOW의 기존 대표 XML 재사용 계약을 보존한다.
    오류 응답은 zip이 아닌 XML로 오므로 corpCode와 같은 방식으로 상태만 노출한다.
    """
    if _DART_RECEIPT_NUMBER_RE.fullmatch(str(rcept_no or "")) is None:
        raise DartResponseError("공시서류 DART 접수번호 형식이 올바르지 않습니다")
    out_path = dest_dir / f"{rcept_no}.xml"
    with _document_cache_lock(rcept_no):
        # 프로세스 잠금만으로는 같은 영속 디스크를 쓰는 worker 둘의
        # sidecar/XML 교차 교체를 막지 못한다. 사용량 계수기와 같은 OS 파일
        # 잠금을 접수번호별 cache 경로에 걸어 두 파일의 생산 구간을 직렬화한다.
        with usage_store._exclusive_lock(out_path):  # noqa: SLF001
            cached_document = _read_valid_cached_document(out_path)
            if cached_document is not None:
                loaded_sidecar = load_document_url_sidecar(
                    out_path,
                    rcept_no=rcept_no,
                    main_document=cached_document,
                )
                if loaded_sidecar.is_valid or not require_official_url_sidecar:
                    return out_path

            # FULL 정식 경계는 구버전 배포가 남긴 대표 XML만 있는 warm cache도
            # sidecar를 한 번 채운다. v1/SHADOW는 기본값 False라 기존 XML을
            # 그대로 재사용하며, 이 추가 네트워크/실패 의미가 소급 적용되지 않는다.
            try:
                key = api_key()
            except RuntimeError:
                if cached_document is not None and not require_official_url_sidecar:
                    return out_path
                raise
            try:
                return _download_document_uncached(
                    rcept_no,
                    dest_dir,
                    counter=counter,
                    key=key,
                    require_official_url_sidecar=require_official_url_sidecar,
                )
            except (DartClientError, OSError):
                # 어느 단계가 실패해도 기존 정상 XML은 훼손하지 않는다. FULL은
                # 예외를 재전파해 「자료 부족」 오분류를 막고 다음 요청에서 재시도한다.
                # 기존 모드는 대표 XML fallback을 유지한다.
                if cached_document is not None and not require_official_url_sidecar:
                    return out_path
                raise


def _download_document_uncached(
    rcept_no: str,
    dest_dir: Path,
    *,
    counter: UsageCounter | None,
    key: str,
    require_official_url_sidecar: bool,
) -> Path:
    """접수번호별 lock 안에서 DART ZIP을 받아 cache 쌍을 교체한다."""

    out_path = dest_dir / f"{rcept_no}.xml"
    query = urllib.parse.urlencode({"crtfc_key": key, "rcept_no": rcept_no})
    (counter or UsageCounter()).tick()
    data = _read_url(
        f"{BASE_URL}/document.xml?{query}",
        timeout=TIMEOUT_DOCUMENT_SEC,
        max_bytes=DOCUMENT_ZIP_RESPONSE_MAX_BYTES,
        response_label="공시서류 ZIP",
    )
    try:
        _validate_zip_container(
            data,
            member_max_count=DOCUMENT_ZIP_MAX_MEMBERS,
            central_directory_max_bytes=DOCUMENT_ZIP_CENTRAL_DIRECTORY_MAX_BYTES,
            archive_label="공시서류 ZIP",
        )
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        try:
            _raise_download_response_error(f"공시서류 {rcept_no}", data)
        except DartClientError as error:
            raise error from exc
    with archive:
        files = _validate_zip_archive(
            archive.infolist(),
            member_max_bytes=DOCUMENT_MEMBER_MAX_BYTES,
            total_max_bytes=DOCUMENT_ZIP_TOTAL_UNCOMPRESSED_MAX_BYTES,
            member_max_count=DOCUMENT_ZIP_MAX_MEMBERS,
            archive_label="공시서류 ZIP",
        )
        # 사업보고서 zip은 본문 XML과 PDF·이미지 첨부가 함께 올 수 있다.
        # 전체 파일 중 가장 큰 것을 고르면 바이너리를 ``.xml``로 저장하고
        # 평문 파서가 쓰레기 문자열을 근거처럼 읽는다. 대표도 XML 안에서만
        # 고르고, XML이 하나도 없으면 형식을 추측하지 않고 닫는다.
        xml_files = [
            info for info in files if info.filename.casefold().endswith(".xml")
        ]
        if not xml_files:
            raise DartResponseError("공시서류 ZIP에 XML 문서가 없습니다")
        main: zipfile.ZipInfo | None = None
        document = b""
        invalid_xml_infos: set[int] = set()
        for candidate_info in sorted(
            xml_files,
            key=lambda info: (-info.file_size, info.filename.casefold()),
        ):
            candidate_payload = _read_zip_member(
                archive,
                candidate_info,
                max_bytes=DOCUMENT_MEMBER_MAX_BYTES,
                archive_label="공시서류 ZIP",
            )
            if is_document_xml_payload(candidate_payload):
                main = candidate_info
                document = candidate_payload
                break
            invalid_xml_infos.add(id(candidate_info))
        if main is None:
            raise DartResponseError("공시서류 ZIP의 XML 문서 본문을 확인할 수 없습니다")
        ranked_candidates_by_url: dict[
            str, tuple[int, int, DocumentUrlSidecarCandidate]
        ] = {}
        for info in xml_files:
            if id(info) in invalid_xml_infos:
                continue
            member = (
                document
                if info is main
                else _read_zip_member(
                    archive,
                    info,
                    max_bytes=DOCUMENT_MEMBER_MAX_BYTES,
                    archive_label="공시서류 ZIP",
                )
            )
            if not is_document_xml_payload(member):
                continue
            for ranked_candidate in _iter_ranked_document_web_url_candidates(
                member,
                member_name=info.filename,
            ):
                _retain_bounded_ranked_candidate(
                    ranked_candidates_by_url,
                    ranked_candidate,
                    max_candidates=DOCUMENT_URL_SIDECAR_MAX_CANDIDATES,
                )

    ranked_candidates = list(ranked_candidates_by_url.values())

    sidecar_path = document_url_sidecar_path(out_path)
    sidecar_written = False
    try:
        sidecar = _document_url_sidecar_bytes(
            rcept_no=rcept_no,
            main_document=document,
            ranked_candidates=ranked_candidates,
        )
        _write_private_bytes_atomic(sidecar_path, sidecar)
        sidecar_written = True
    except (OSError, DartResponseError, TypeError, ValueError) as error:
        # URL 보조 metadata를 못 만들었다고 이미 검증한 대표 공시 원문까지
        # 버리지는 않는다. 남은 sidecar는 사용되지 않게 제거하고, typed
        # fetcher가 대표 XML만 다시 훑는 안전한 fallback을 쓴다.
        _unlink_cache_artifact(sidecar_path)
        if require_official_url_sidecar:
            raise DartResponseError(
                "공시서류 공식 URL sidecar를 안전하게 저장하지 못했습니다"
            ) from error
    try:
        _write_private_bytes_atomic(out_path, document)
    except OSError:
        if sidecar_written:
            _unlink_cache_artifact(sidecar_path)
        raise
    return out_path


def download_corpcode(dest_dir: Path, counter: UsageCounter | None = None) -> Path:
    """corpCode.xml(전체 회사 고유번호 목록)을 내려받아 푼다. 이미 있으면 재사용 — 호출 절약.

    오류 응답(잘못된 키 등)은 zip이 아니라 XML로 오므로, 그 경우 상태 코드만 노출한다(키 노출 금지).
    """
    xml_path = dest_dir / "CORPCODE.xml"
    if xml_path.exists():
        return xml_path
    key = api_key()
    query = urllib.parse.urlencode({"crtfc_key": key})
    (counter or UsageCounter()).tick()
    data = _read_url(
        f"{BASE_URL}/corpCode.xml?{query}",
        timeout=TIMEOUT_CORPCODE_SEC,
        max_bytes=CORPCODE_ZIP_RESPONSE_MAX_BYTES,
        response_label="corpCode ZIP",
    )
    try:
        _validate_zip_container(
            data,
            member_max_count=CORPCODE_ZIP_MAX_MEMBERS,
            central_directory_max_bytes=CORPCODE_ZIP_CENTRAL_DIRECTORY_MAX_BYTES,
            archive_label="corpCode ZIP",
        )
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        try:
            _raise_download_response_error("corpCode", data)
        except DartClientError as error:
            raise error from exc
    with archive:
        files = _validate_zip_archive(
            archive.infolist(),
            member_max_bytes=CORPCODE_XML_MAX_BYTES,
            total_max_bytes=CORPCODE_ZIP_TOTAL_UNCOMPRESSED_MAX_BYTES,
            member_max_count=CORPCODE_ZIP_MAX_MEMBERS,
            archive_label="corpCode ZIP",
        )
        corpcode_infos = [info for info in files if info.filename == "CORPCODE.xml"]
        if len(corpcode_infos) != 1:
            raise DartResponseError("corpCode ZIP의 필수 항목 구성이 올바르지 않습니다")
        corpcode_xml = _read_zip_member(
            archive,
            corpcode_infos[0],
            max_bytes=CORPCODE_XML_MAX_BYTES,
            archive_label="corpCode ZIP",
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    xml_path.write_bytes(corpcode_xml)
    return xml_path
