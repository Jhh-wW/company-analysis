"""공급자 오류 본문을 기록하지 않고 닫힌 진단 값만 추출한다."""
from __future__ import annotations

from src.features.pipeline import constants as c


def safe_provider_error_metadata(error: BaseException | None) -> dict[str, str]:
    """알려진 SDK 오류 유형과 정확히 일치한 문구만 진단 코드로 돌려준다.

    메시지는 메모리 안에서 비교한 뒤 버린다. 예외 문자열, 프롬프트,
    HTTP 헤더와 원문 응답을 반환하거나 과금·재시도 정책을 바꾸지 않는다.
    """
    body = getattr(error, "body", None)
    # SDK 0.122.0은 응답 JSON을 그대로 보존한다. 평탄 fixture도 닫힌 값만 읽는다.
    detail = (body.get("error") if "error" in body else body) if type(body) is dict else None
    api_type = getattr(error, "type", None)
    if api_type is None and type(detail) is dict:
        api_type = detail.get("type")
    if type(api_type) is not str or api_type not in c.PROVIDER_API_ERROR_TYPES:
        api_type = c.PROVIDER_ERROR_CATEGORY_UNKNOWN
    category = c.PROVIDER_ERROR_CATEGORY_UNKNOWN
    message = detail.get("message") if type(detail) is dict else None
    status = getattr(error, "status_code", None)
    if (
        api_type == "invalid_request_error"
        and status == c.PROVIDER_INVALID_REQUEST_STATUS
        and type(message) is str
        and len(message) <= c.PROVIDER_ERROR_MESSAGE_MAX_CHARS
    ):
        if message == c.PROVIDER_SCHEMA_COMPLEXITY_MESSAGE:
            category = c.PROVIDER_ERROR_CATEGORY_SCHEMA_COMPLEXITY
        else:
            for prefix, code in c.PROVIDER_SPEND_LIMIT_PREFIXES:
                if message == prefix or (message.startswith(prefix) and message[len(prefix):].startswith(c.PROVIDER_ERROR_PREFIX_BOUNDARIES)):
                    category = code
                    break
    details = detail.get("details") if type(detail) is dict else None
    if (
        status == c.PROVIDER_RATE_LIMIT_STATUS
        and api_type == "rate_limit_error"
        and type(details) is dict
        and details.get("error_code") == c.PROVIDER_ENFORCED_SPEND_LIMIT_CODE
    ):
        category = c.PROVIDER_MONTHLY_SPEND_CATEGORY
    return {
        "provider_api_error_type": api_type,
        "provider_error_category": category,
    }
