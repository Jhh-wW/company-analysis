"""구글이 «왜» 거절했는지가 로그에 남는지 못 박는다.

★ 이 시험이 잡는 것 — **로그인이 조용히 실패하는데 로그만 보고는 아무것도 모르는 것.**
  구글은 거절 이유를 **응답 «본문»**에 담아 보낸다:
      {"error":"invalid_client","error_description":"Unauthorized"}
  그런데 우리 코드는 본문을 안 읽고 버려서, 로그에 「HTTP Error 401」만 남았다.
  · `invalid_client`   → 클라이언트 ID·비밀이 안 맞는다
  · `redirect_uri_mismatch` → 콘솔에 등록한 주소가 다르다
  이 둘은 **고치는 법이 완전히 다른데** 구별할 방법이 없었다.

★ 외부 오류 설명은 신뢰하지 않는다. 공급자가 요청값을 되비춰도 비밀이 로그에
  남지 않게, 운영 판단에 필요한 닫힌 오류 코드만 기록한다.

⚠️ 사용자 «화면»에는 여전히 안 내보낸다. 내부 사정을 흘리지 않는 규칙은 그대로다.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from src.features.auth.google import _ERROR_DETAIL_MAX_CHARS, _error_detail


def _http_error(body: str, *, code: int = 401) -> urllib.error.HTTPError:
    """구글이 오류를 돌려준 상황을 만든다."""
    return urllib.error.HTTPError(
        url="https://oauth2.googleapis.com/token",
        code=code,
        msg="Unauthorized",
        hdrs=None,                                   # type: ignore[arg-type]
        fp=io.BytesIO(body.encode("utf-8")),
    )


# ══════════════════════════════════════════════════════════
# ① 진짜 이유를 꺼낸다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "오류코드, 설명",
    [
        ("invalid_client", "Unauthorized"),
        ("redirect_uri_mismatch", "Bad Request"),
        ("invalid_grant", "Bad Request"),
    ],
)
def test_구글이_준_오류_코드를_꺼낸다(오류코드: str, 설명: str):
    """★ 이 코드가 로그에 없으면 「왜 안 되는지」를 영영 못 찾는다."""
    exc = _http_error(json.dumps({"error": 오류코드, "error_description": 설명}))

    detail = _error_detail(exc)

    assert 오류코드 in detail
    assert detail == 오류코드


def test_설명이_없어도_코드는_꺼낸다():
    """구글이 `error`만 보낼 때도 있다."""
    exc = _http_error('{"error": "invalid_client"}')

    assert "invalid_client" in _error_detail(exc)


def test_오류설명이_요청비밀을_반사해도_로그문구에_남기지_않는다():
    reflected_secret = "oauth-code-or-client-secret-must-not-log"
    exc = _http_error(
        json.dumps(
            {
                "error": "invalid_client",
                "error_description": reflected_secret,
            }
        )
    )

    detail = _error_detail(exc)

    assert detail == "invalid_client"
    assert reflected_secret not in detail


def test_알수없는_error필드에_비밀을_넣어도_그대로_반사하지_않는다():
    reflected_secret = "secret-in-error-field"

    detail = _error_detail(
        _http_error(json.dumps({"error": reflected_secret}))
    )

    assert detail == "(알 수 없는 오류 코드)"
    assert reflected_secret not in detail


# ══════════════════════════════════════════════════════════
# ② 이상한 응답에도 안 죽는다 (진단 도구가 죽으면 안 된다)
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "본문",
    [
        "",                       # 빈 응답
        "   ",                    # 공백뿐
        "<html>Server Error</html>",   # JSON이 아님 (프록시가 가로챈 경우)
        "[1, 2, 3]",              # JSON이지만 dict가 아님
        "null",
    ],
)
def test_이상한_응답에도_예외를_안_던진다(본문: str):
    """★ 진단하려다 «진단 코드»가 터지면 원래 오류까지 묻힌다."""
    detail = _error_detail(_http_error(본문))

    assert isinstance(detail, str)


def test_본문을_못_읽어도_안_죽는다():
    """이미 읽힌 뒤라 다시 못 읽는 경우가 있다."""
    exc = _http_error('{"error": "x"}')
    exc.read()                      # 먼저 다 읽어 버린다

    assert isinstance(_error_detail(exc), str)


# ══════════════════════════════════════════════════════════
# ③ 로그를 뒤덮지 않는다
# ══════════════════════════════════════════════════════════


def test_아주_긴_본문은_잘라서_남긴다():
    """★ 자르지 않으면 언젠가 로그가 통째로 뒤덮인다."""
    긴본문 = "x" * 5000

    detail = _error_detail(_http_error(긴본문))

    assert len(detail) <= _ERROR_DETAIL_MAX_CHARS


def test_오류_본문도_고정_바이트_이상은_읽거나_로그에_반사하지_않는다():
    secret_tail = "provider-secret-reflection"
    exc = _http_error(
        "x" * 5000 + secret_tail,
    )

    detail = _error_detail(exc)

    assert detail == "(오류 본문이 너무 큼)"
    assert secret_tail not in detail
