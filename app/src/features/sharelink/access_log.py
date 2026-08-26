"""Capability URL 원문을 웹 서버 접근 로그에서 지운다.

LINK 원문은 URL과 HttpOnly 쿠키에서 권한으로만 잠시 쓰며, DB·HTML·로그에는
남기지 않는다. Uvicorn은 기본 접근 로그에 요청 경로를 넣으므로 애플리케이션을
불러올 때 32자리 비밀 경로만 안전한 표식으로 치환한다.
"""

from __future__ import annotations

import logging
import re
from typing import Final


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
