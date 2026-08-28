"""비밀 헤더·본문을 싣는 고정 외부 API의 공통 HTTP 안전 경계."""

from __future__ import annotations

import urllib.request


class CredentialedHTTPContractError(RuntimeError):
    """고정 API 응답이 redirect·크기·형식 계약을 벗어났다."""


class NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    """표준 urllib가 인증 헤더를 Location으로 복사하지 못하게 한다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def build_no_redirect_opener() -> urllib.request.OpenerDirector:
    """프록시 등 나머지 표준 동작은 유지하고 redirect만 닫은 opener를 만든다."""

    return urllib.request.build_opener(NoCredentialRedirect())


def require_exact_response_url(response: object, *, expected_url: str) -> None:
    """고정 endpoint 호출이 다른 위치의 응답으로 바뀌지 않았는지 확인한다."""

    getter = getattr(response, "geturl", None)
    actual = expected_url if not callable(getter) else str(getter() or "")
    if actual != expected_url:
        raise CredentialedHTTPContractError("외부 API 응답 위치가 바뀌었습니다")


def read_limited_bytes(response: object, *, max_bytes: int) -> bytes:
    """선언 크기와 실제 읽기 모두 ``max_bytes``를 넘기 전에 닫는다."""

    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise ValueError("응답 크기 상한은 양의 정수여야 합니다")

    headers = getattr(response, "headers", None)
    get_all = getattr(headers, "get_all", None)
    if callable(get_all):
        declared_values = list(get_all("Content-Length") or ())
    else:
        getter = getattr(headers, "get", None)
        declared = getter("Content-Length") if callable(getter) else None
        declared_values = [] if declared is None else [declared]
    if len(declared_values) > 1:
        raise CredentialedHTTPContractError("외부 API 응답 길이 선언이 중복됐습니다")
    if declared_values:
        try:
            declared_size = int(str(declared_values[0]).strip())
        except (TypeError, ValueError) as exc:
            raise CredentialedHTTPContractError(
                "외부 API 응답 길이 선언이 올바르지 않습니다"
            ) from exc
        if declared_size < 0 or declared_size > max_bytes:
            raise CredentialedHTTPContractError(
                "외부 API 응답이 허용 크기를 넘었습니다"
            )

    reader = getattr(response, "read", None)
    if not callable(reader):
        raise CredentialedHTTPContractError("외부 API 응답을 읽을 수 없습니다")
    data = reader(max_bytes + 1)
    if not isinstance(data, bytes):
        raise CredentialedHTTPContractError("외부 API 응답 형식이 올바르지 않습니다")
    if len(data) > max_bytes:
        raise CredentialedHTTPContractError("외부 API 응답이 허용 크기를 넘었습니다")
    return data
