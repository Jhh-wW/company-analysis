"""Capability·OAuth 인가정보·보고서 locator 원문을 앱·웹 서버 로그에서 지운다.

LINK 원문은 URL과 HttpOnly 쿠키에서 권한으로만 잠시 쓰며, DB·HTML·로그에는
남기지 않는다. Uvicorn은 기본 접근 로그에 요청 경로를 넣으므로 애플리케이션을
불러올 때 32자리 비밀 경로만 안전한 표식으로 치환한다.
"""

from __future__ import annotations

import logging
import re
from typing import Final
from urllib.parse import unquote


# ★ 뒷경계를 «hex가 아니면 전부»로 잡는다 (2026-08-26 적대 검수가 뚫었다).
#   예전에는 `(?=$|[/\s"])` 로 «슬래시·공백·따옴표·문자열끝»만 경계로 인정했다.
#   그래서 열쇠 바로 뒤에 다른 글자가 붙으면 매치 자체가 실패해 **원문 열쇠가
#   로그에 그대로 남았다.** 재현으로 확인한 통과 사례:
#     /k/<32자리>.json   ← 크롤러가 확장자를 붙여 요청
#     /k/<32자리>.       ← 링크를 문장 끝에 붙여 공유하면 마침표가 따라온다
#   링크 미리보기 크롤러가 그 주소를 그대로 요청하면 접근 로그에 열쇠가 찍힌다.
#
# ⚠️ 길이는 «정확히 32자»로 유지한다. 「32자 이상」으로 넓히면
#   `/admin/link/<64자리 해시>`까지 가려 버리는데, 그 해시는 비밀이 아니라
#   추적에 쓰라고 «일부러 남기는» 값이다
#   (test_access_log.py::test_안전한_관리자_해시와_일반_보고서_ID는_로그에서_유지한다).
#   뒤에 hex가 더 붙어 있으면(33자 이상) 발급된 열쇠가 아니므로 건드리지 않는다.
_RAW_LINK_PATH: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>/(?:k|admin/links?)/)[0-9a-f]{32}"
    r"(?![0-9a-f])(?:[?#][^\s\"]*)?",
    re.IGNORECASE,
)
_REDACTED = r"\g<prefix>[LINK_REDACTED]"

# report/job ID는 이제 권한 비밀은 아니지만 사람에게 발급된 자료 위치다. 주소가
# 새도 접근은 별도 grant가 막고, 로그에는 운영상 필요 없는 원문과 client IP를
# 함께 남기지 않는다. 64자리 내부 hash나 문장 속 임의 숫자는 건드리지 않는다.
_RAW_REPORT_PATH: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>/(?:result|progress|api/progress|download/pdf|reports)/)"
    r"[0-9a-f]{32}(?![0-9a-f])(?:[?#][^\s\"]*)?",
    re.IGNORECASE,
)
_RAW_ID_FIELD: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>\b(?:report_id|job_id|public_id|run_id)=)"
    r"[0-9a-f]{32}(?![0-9a-f])",
    re.IGNORECASE,
)
_REPORT_REDACTED = r"\g<prefix>[REPORT_ID_REDACTED]"

# OAuth callback query의 ``code``는 Google 인가 코드를, ``state``는 로그인
# 왕복 capability를 담는다. 로컬 데모 시작 query의 ``token``은 실행기가 만든
# 관리자 root capability다. Uvicorn은 query 전체를 기본 접근 로그에 넣으므로
# 파라미터별 파싱에 기대지 않고 이 두 교환 경로의 query를 통째로 지운다.
# 순서·중복·percent-encoding으로 우회할 여지도 함께 없어진다.
_RAW_OAUTH_CALLBACK_QUERY: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>/auth/callback)\?[^\s\"]*",
    re.IGNORECASE,
)
_OAUTH_REDACTED = r"\g<prefix>?[OAUTH_QUERY_REDACTED]"
_RAW_LOCAL_DEMO_START_QUERY: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>/auth/local-demo/start)\?[^\s\"]*",
    re.IGNORECASE,
)
_LOCAL_DEMO_REDACTED = r"\g<prefix>?[LOCAL_DEMO_QUERY_REDACTED]"

#: 경로 접두어만 찾는 «싼» 검사. 비싼 포맷을 할지 말지 여기서 먼저 거른다.
_SENSITIVE_PREFIX: Final[re.Pattern[str]] = re.compile(
    r"/(?:k|admin/links?|result|progress|api/progress|download/pdf|reports)/"
    r"|/auth/callback\?"
    r"|/auth/local-demo/start\?"
    r"|\b(?:report_id|job_id|public_id|run_id)=",
    re.IGNORECASE,
)


def redact_capability_path(value: object) -> object:
    """문자열 속 LINK·보고서 위치만 가리고 나머지 인자는 그대로 둔다."""

    if not isinstance(value, str):
        return value
    # ASGI 라우터는 percent-encoding을 푼 ``scope['path']``로 권한을 확인하지만,
    # Uvicorn 접근 로그는 원래 wire 경로를 남길 수 있다. 따라서 ``/k/a%61...``는
    # 정상 LINK로 열리면서도 예전 정규식에는 32자리 hex로 보이지 않아 복원 가능한
    # capability가 로그에 남았다. 퍼센트 인코딩을 한 번 푼 사본에서 탐지·치환해
    # 라우터와 로그 필터가 같은 경로를 보게 한다. 민감 경로가 아니면 원문을 그대로
    # 돌려줘 일반 로그의 URL 표기를 불필요하게 바꾸지 않는다.
    decoded = unquote(value, errors="replace") if "%" in value else value
    if not _contains_sensitive_identifier_decoded(decoded):
        return value
    redacted = _RAW_LINK_PATH.sub(_REDACTED, decoded)
    redacted = _RAW_REPORT_PATH.sub(_REPORT_REDACTED, redacted)
    redacted = _RAW_ID_FIELD.sub(_REPORT_REDACTED, redacted)
    redacted = _RAW_OAUTH_CALLBACK_QUERY.sub(_OAUTH_REDACTED, redacted)
    return _RAW_LOCAL_DEMO_START_QUERY.sub(_LOCAL_DEMO_REDACTED, redacted)


def _contains_sensitive_identifier_decoded(value: str) -> bool:
    return bool(
        _RAW_LINK_PATH.search(value) is not None
        or _RAW_REPORT_PATH.search(value) is not None
        or _RAW_ID_FIELD.search(value) is not None
        or _RAW_OAUTH_CALLBACK_QUERY.search(value) is not None
        or _RAW_LOCAL_DEMO_START_QUERY.search(value) is not None
    )


def _contains_sensitive_identifier(value: object) -> bool:
    if not isinstance(value, str):
        return False
    decoded = unquote(value, errors="replace") if "%" in value else value
    return _contains_sensitive_identifier_decoded(decoded)


class CapabilityAccessLogFilter(logging.Filter):
    """Uvicorn의 지연 포맷 인자를 포맷되기 전에 정리한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        message_has_identifier = _contains_sensitive_identifier(record.msg)
        if message_has_identifier and isinstance(record.msg, str):
            # 이미 포맷된 접근 로그라면 맨 앞 client address도 함께 없앤다.
            record.msg = re.sub(
                r"^.*? - \"",
                '[CLIENT_REDACTED] - "',
                record.msg,
                count=1,
            )
        record.msg = redact_capability_path(record.msg)
        if isinstance(record.args, tuple):
            values = list(record.args)
            # Uvicorn access tuple은 (client, method, full_path, http_version, status).
            # LINK 경로에서 client IP도 불필요한 개인정보이므로 저장하지 않는다.
            if len(values) >= 3 and _contains_sensitive_identifier(values[2]):
                values[0] = "[CLIENT_REDACTED]"
            record.args = tuple(redact_capability_path(value) for value in values)
        elif isinstance(record.args, dict):
            has_identifier = any(
                _contains_sensitive_identifier(value)
                for value in record.args.values()
            )
            record.args = {
                key: (
                    "[CLIENT_REDACTED]"
                    if has_identifier
                    and str(key).lower() in {"client", "client_addr"}
                    else (
                        "[REPORT_ID_REDACTED]"
                        if str(key).lower()
                        in {"report_id", "job_id", "public_id", "run_id"}
                        and isinstance(value, str)
                        and re.fullmatch(r"[0-9a-f]{32}", value, re.IGNORECASE)
                        else redact_capability_path(value)
                    )
                )
                for key, value in record.args.items()
            }
        self._redact_split_across_args(record)
        return True

    @staticmethod
    def _redact_split_across_args(record: logging.LogRecord) -> None:
        """「형식 문자열에 ``/k/``, 열쇠는 «인자»로」 오는 모양까지 막는다.

        예: ``logger.info("열람 링크 /k/%s 를 처리했습니다", key)``

        ★ 위 두 단계는 ``msg``와 ``args``를 **따로** 본다. 이 모양은 둘을 합쳐야
          비로소 열쇠 경로가 되므로 양쪽 다 놓친다 (2026-08-26 실측으로 확인).
          지금 저장소에 이런 호출부는 **0곳**이지만, 최상위 로거를 켜면서
          이 필터가 «앱 로그 전체»를 지키게 됐으므로 미리 막아 둔다.

        ⚠️ 여기서 포맷을 미리 해 버리면 파이썬 로깅의 「필요할 때만 문자열을
          만든다」는 이점이 사라진다. 그래서 **형식 문자열에 경로 접두어가
          보일 때만** 한다 — uvicorn 접근 로그(``'%s - "%s %s HTTP/%s" %d'``)는
          접두어가 없으므로 이 검사를 타지 않는다.
        """
        if not record.args or not isinstance(record.msg, str):
            return
        if not _SENSITIVE_PREFIX.search(record.msg):
            return
        try:
            formatted = record.getMessage()
        except (TypeError, ValueError, KeyError):
            # 포맷이 깨진 레코드까지 우리가 고칠 일은 아니다. 원본을 그대로 둔다.
            return
        if not _contains_sensitive_identifier(formatted):
            return
        record.msg = redact_capability_path(formatted)
        record.args = ()


def install_uvicorn_access_log_filter() -> None:
    """필터를 멱등 설치한다."""

    logger = logging.getLogger("uvicorn.access")
    if any(isinstance(item, CapabilityAccessLogFilter) for item in logger.filters):
        return
    logger.addFilter(CapabilityAccessLogFilter())
