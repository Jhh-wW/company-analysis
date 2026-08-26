# -*- coding: utf-8 -*-
"""앱을 «불러오기만 해도» 로그 설정이 실제로 걸리는지 지킨다.

★ 왜 따로 있나 — ``core/tests/test_logging_setup.py``는 함수 자체가 옳은지만
  본다. 그 함수를 «부르지 않으면» 운영은 그대로 깜깜하다. 조립 지점
  (``src/web/main.py``)이 정말 부르는지는 여기서만 확인된다.

★ 보안 — 비밀 링크 주소가 애플리케이션 로그로 새지 않는지도 함께 본다.
  기존 ``install_uvicorn_access_log_filter``는 «접근 로그»만 막는다.
"""

from __future__ import annotations

import logging

from src.core import logging_setup
from src.features.sharelink.access_log import CapabilityAccessLogFilter

import src.web.main  # noqa: F401  (불러오는 것 자체가 시험 대상이다)


_비밀_열쇠 = "a1b2c3d4e5f60718293a4b5c6d7e8f90"


def _설치된_핸들러() -> logging.Handler | None:
    for handler in logging.getLogger().handlers:
        if getattr(handler, logging_setup._OWNED_HANDLER_MARK, False):
            return handler
    return None


def test_앱을_불러오면_최상위_핸들러가_달린다():
    assert _설치된_핸들러() is not None


def test_앱을_불러오면_info_로그가_살아_있다():
    """이게 거짓이면 「장별 문장 수」 같은 무과금 진단 로그가 또 죽는다."""
    assert logging.getLogger("src.features.composer.dedupe").isEnabledFor(logging.INFO)


def test_최상위_핸들러에_링크_가림_필터가_걸려_있다():
    핸들러 = _설치된_핸들러()
    assert 핸들러 is not None
    assert any(isinstance(f, CapabilityAccessLogFilter) for f in 핸들러.filters)


def test_애플리케이션_로그의_비밀_링크가_가려진다():
    """접근 로그가 아니라 «앱 코드»가 남긴 줄도 가려져야 한다."""
    핸들러 = _설치된_핸들러()
    assert 핸들러 is not None

    레코드 = logging.LogRecord(
        name="src.web.routers.reports",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=f"열람 링크 /k/{_비밀_열쇠} 를 처리했습니다",
        args=(),
        exc_info=None,
    )
    핸들러.filter(레코드)

    assert _비밀_열쇠 not in 레코드.getMessage()
    assert "[LINK_REDACTED]" in 레코드.getMessage()


def test_지연_포맷_인자에_담긴_비밀_링크도_가려진다():
    """``logger.info("%s", path)`` 처럼 인자로 넘어온 경우도 막는다."""
    핸들러 = _설치된_핸들러()
    assert 핸들러 is not None

    레코드 = logging.LogRecord(
        name="src.web.routers.reports",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="열람 링크 %s 를 처리했습니다",
        args=(f"/k/{_비밀_열쇠} ",),
        exc_info=None,
    )
    핸들러.filter(레코드)

    assert _비밀_열쇠 not in 레코드.getMessage()
