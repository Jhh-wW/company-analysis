"""Capability URL 원문을 웹 서버 접근 로그에서 지운다.

LINK 원문은 URL과 HttpOnly 쿠키에서 권한으로만 잠시 쓰며, DB·HTML·로그에는
남기지 않는다. Uvicorn은 기본 접근 로그에 요청 경로를 넣으므로 애플리케이션을
불러올 때 32자리 비밀 경로만 안전한 표식으로 치환한다.
"""

from __future__ import annotations

import logging
import re
from typing import Final


_RAW_LINK_PATH: Final[re.Pattern[str]] = re.compile(
    r"(?P<prefix>/(?:k|admin/link)/)[0-9a-f]{32}"
    r"(?:[?#][^\s\"]*)?(?=$|[/\s\"])",
    re.IGNORECASE,
)
_REDACTED = r"\g<prefix>[LINK_REDACTED]"


def redact_capability_path(value: object) -> object:
    """문자열 속 LINK 경로만 가리고 나머지 로그 인자는 그대로 둔다."""

    if not isinstance(value, str):
        return value
    return _RAW_LINK_PATH.sub(_REDACTED, value)


def _contains_raw_link_path(value: object) -> bool:
    return isinstance(value, str) and _RAW_LINK_PATH.search(value) is not None


class CapabilityAccessLogFilter(logging.Filter):
    """Uvicorn의 지연 포맷 인자를 포맷되기 전에 정리한다."""

    def filter(self, record: logging.LogRecord) -> bool:
        message_has_link = _contains_raw_link_path(record.msg)
        if message_has_link and isinstance(record.msg, str):
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
            if len(values) >= 3 and _contains_raw_link_path(values[2]):
                values[0] = "[CLIENT_REDACTED]"
            record.args = tuple(redact_capability_path(value) for value in values)
        elif isinstance(record.args, dict):
            has_link = any(_contains_raw_link_path(value) for value in record.args.values())
            record.args = {
                key: (
                    "[CLIENT_REDACTED]"
                    if has_link and str(key).lower() in {"client", "client_addr"}
                    else redact_capability_path(value)
                )
                for key, value in record.args.items()
            }
        return True


def install_uvicorn_access_log_filter() -> None:
    """필터를 멱등 설치한다."""

    logger = logging.getLogger("uvicorn.access")
    if any(isinstance(item, CapabilityAccessLogFilter) for item in logger.filters):
        return
    logger.addFilter(CapabilityAccessLogFilter())
