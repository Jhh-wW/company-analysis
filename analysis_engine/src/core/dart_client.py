"""OpenDART 클라이언트 — 사용량 계수기 내장 (착수 순서 3번의 「계수기 v1 필수」).

키는 환경변수 DART_API_KEY로만 받는다(하드코딩 금지). .env는 프로그램만 읽는다.
일일 한도: 키당 20,000건 (공식 문서가 오류 020으로 정의한 값). 계수기가 로컬에서 세고,
경보 문턱을 넘으면 경고, 한도면 예외를 던져 「재시도할수록 더 막히는」 상황을 차단한다.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import re
import struct
import urllib.error
import urllib.parse
import urllib.request
import zipfile
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
        raise RuntimeError(
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
_ZIP_END_SIGNATURE = b"PK\x05\x06"
_ZIP_END_RECORD = struct.Struct("<4s4H2LH")
_ZIP_END_MIN_BYTES = _ZIP_END_RECORD.size
_ZIP_MAX_COMMENT_BYTES = (1 << 16) - 1


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


def download_document(rcept_no: str, dest_dir: Path,
                      counter: UsageCounter | None = None) -> Path:
    """공시서류 원본(document.xml)을 내려받아 첫 문서 파일을 저장한다. 있으면 재사용.

    zip 안에는 접수번호 이름의 XML(공시 원문)이 들어 있다. 오류 응답은 zip이 아닌
    XML로 오므로 corpCode와 같은 방식으로 상태만 노출한다(키 노출 금지).
    """
    out_path = dest_dir / f"{rcept_no}.xml"
    if out_path.exists():
        return out_path
    key = api_key()
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
        # 사업보고서 zip은 본문+첨부 여러 파일 — 가장 큰 파일이 본문이다.
        main = max(files, key=lambda info: info.file_size)
        document = _read_zip_member(
            archive,
            main,
            max_bytes=DOCUMENT_MEMBER_MAX_BYTES,
            archive_label="공시서류 ZIP",
        )
    dest_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(document)
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
