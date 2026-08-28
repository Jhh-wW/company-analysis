"""API key·인증 헤더를 싣는 고정 endpoint의 공통 HTTP 경계."""

from __future__ import annotations

import urllib.request


class CredentialedHTTPContractError(RuntimeError):
    """고정 API 응답이 redirect·출처 계약을 벗어났다."""


class NoCredentialRedirect(urllib.request.HTTPRedirectHandler):
    """표준 urllib가 인증정보를 Location 대상에 다시 보내지 못하게 한다."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def build_no_redirect_opener() -> urllib.request.OpenerDirector:
    """나머지 표준 동작은 유지하고 redirect만 닫은 opener를 만든다."""

    return urllib.request.build_opener(NoCredentialRedirect())


def require_exact_response_url(response: object, *, expected_url: str) -> None:
    """고정 endpoint 호출 결과가 다른 주소의 응답으로 바뀌지 않았는지 확인한다."""

    getter = getattr(response, "geturl", None)
    actual = expected_url if not callable(getter) else str(getter() or "")
    if actual != expected_url:
        raise CredentialedHTTPContractError("외부 API 응답 위치가 바뀌었습니다")
