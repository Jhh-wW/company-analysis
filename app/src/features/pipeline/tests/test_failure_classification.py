# -*- coding: utf-8 -*-
"""AI 호출 실패를 «확정»과 «모호»로 가르는 규칙을 못 박는다.

★ 왜 이 파일이 생겼나 (운영 실측)
  ─────────────────────────────────────────────────────────
  「현대카드 / 서울 영등포구」 조사가 화면에 이렇게 떴다:
    「오류가 났습니다 — 보고서를 만들다 오류가 났습니다」

  Playwright 로 로컬 실서버에서 재현해 얻은 진짜 traceback:
    AskFatalError: 미확정 provider 호출 뒤에는 같은 요청에서 다시 호출할 수 없습니다

  ★ 무슨 일이었나
    AI 호출 «하나»가 실패하면 `billing_uncertain` 이 켜지고,
    그 요청의 «나머지 모든 호출»이 시작도 못 하고 막힌다(real.py:697-705).
    본조사는 1초 만에 통째로 죽었다. 8~10회 호출 중 1회 실패로 보고서가 날아간다.

  ★ 그런데 코드는 스스로 이렇게 적어 두었다:
    「SDK 예외에는 보통 usage 가 없다」
    즉 «모든» 실패가 미확정이 된다 — 타임아웃 한 번에 전부 잃는 구조였다.

★ 이 시험이 지키는 것
  ① provider 가 요청을 «거절»한 것이 확실하면(400·401·403·404) 0원으로 확정한다.
     토큰을 만들지 않았으므로 뒤 호출을 막을 이유가 없다.
  ② 「서버가 받았는지 모르는」 실패(타임아웃·연결끊김·429·5xx)는 «그대로 모호»로 둔다.
     ★ 이쪽을 확정으로 옮기면 돈을 적게 세게 된다. 되돌리지 마라.
"""

from __future__ import annotations

import pytest

from src.features.pipeline import real


class _가짜응답:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _상태있는오류(Exception):
    """anthropic.APIStatusError 흉내 — status_code 를 직접 갖는다."""

    def __init__(self, status_code: int) -> None:
        super().__init__("provider 오류")
        self.status_code = status_code


class _응답에상태있는오류(Exception):
    """status_code 가 response 안에만 있는 형태."""

    def __init__(self, status_code: int) -> None:
        super().__init__("provider 오류")
        self.response = _가짜응답(status_code)


# ══════════════════════════════════════════════════════════
# ① 확정 거절 — 토큰을 안 만들었으므로 뒤를 막지 않는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_요청_거절은_확정_실패다(status: int) -> None:
    """★ 이게 참이어야 타임아웃 한 번에 보고서 전체가 날아가지 않는다."""
    assert real._is_determinate_zero_cost(_상태있는오류(status)) is True
    assert real._is_determinate_zero_cost(_응답에상태있는오류(status)) is True


# ══════════════════════════════════════════════════════════
# ② 모호한 실패 — «반드시» 모호로 남아야 한다  ← 안전선
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("status", [408, 409, 429, 500, 502, 503, 504])
def test_서버가_받았는지_모르는_실패는_모호로_남는다(status: int) -> None:
    """★ 이걸 확정으로 옮기면 «이미 쓴 돈»을 안 세게 된다. 되돌리지 마라.

    429 는 요청이 서버까지 갔다 거절된 것이라 여전히 모호하다.
    5xx 는 서버가 처리하다 실패한 것이라 토큰이 만들어졌을 수 있다.
    """
    assert real._is_determinate_zero_cost(_상태있는오류(status)) is False


def test_상태코드가_없는_실패는_모호로_남는다() -> None:
    """타임아웃·연결끊김은 status_code 자체가 없다 — 가장 모호한 경우다."""
    assert real._is_determinate_zero_cost(TimeoutError("시간 초과")) is False
    assert real._is_determinate_zero_cost(ConnectionError("연결 끊김")) is False
    assert real._is_determinate_zero_cost(Exception("알 수 없음")) is False


def test_상태코드가_숫자가_아니면_모호로_남는다() -> None:
    """문자열 '400' 을 400 으로 «해석하지» 않는다 — 애매하면 모호가 기본값이다."""

    class _이상한오류(Exception):
        status_code = "400"

    assert real._is_determinate_zero_cost(_이상한오류()) is False


# ══════════════════════════════════════════════════════════
# ③ 목록 자체 — 넓히려면 이 시험을 먼저 고쳐라
# ══════════════════════════════════════════════════════════


def test_확정_목록이_좁게_유지된다() -> None:
    """★ 넓히면 돈을 적게 세는 방향이다. 늘리기 전에 반드시 근거를 대라.

    ⚠️ 이 판정은 «스트리밍이 아닌» 호출에만 맞다. 스트리밍을 도입하면
      중간에 400 이 날 수 있어 이미 만들어진 토큰이 생긴다 — 그때는 목록을 비워라.
    """
    assert real._DETERMINATE_ZERO_COST_STATUSES == frozenset({400, 401, 403, 404})


def test_미확정_사유_로그가_비밀을_안_흘린다(caplog) -> None:
    """★ 사유는 남기되 provider 응답 본문·예외 메시지는 남기지 않는다."""
    import logging

    비밀 = "SECRET-PROVIDER-BODY-9f3a"
    with caplog.at_level(logging.WARNING, logger=real.__name__):
        real._log_billing_uncertain("본조사", "sdk_error_without_usage", _상태있는오류(503))

    남은것 = " ".join(기록.getMessage() for 기록 in caplog.records)
    assert "본조사" in 남은것
    assert "sdk_error_without_usage" in 남은것
    assert "_상태있는오류" in 남은것, "어떤 예외였는지는 알 수 있어야 한다"
    assert "503" in 남은것
    assert 비밀 not in 남은것
    assert "provider 오류" not in 남은것, "★ 예외 메시지를 남기면 응답 본문이 샐 수 있다"
