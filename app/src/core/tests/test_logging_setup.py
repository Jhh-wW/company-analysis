# -*- coding: utf-8 -*-
"""최상위 로거 설정이 «실제로» 로그를 살리는지 지킨다.

★ 이 시험대가 지키는 결함 (2026-08-26 실측)
  운영에서 최상위 로거는 핸들러 0개·레벨 WARNING이라 앱의 ``logger.info``가
  **레코드조차 만들지 못했다.** 그래서 「장별 문장 수」 같은 무과금 진단 로그가
  한 번도 안 찍혔다. 여기 ①번 시험이 그 상태를 «일부러 재현»한 뒤,
  ``configure_logging``이 그것을 뒤집는지 본다.
"""

from __future__ import annotations

import io
import logging

import pytest

from src.core import logging_setup


@pytest.fixture
def 빈_최상위_로거():
    """최상위 로거를 «설정 이전»으로 되돌린 뒤, 끝나면 원래대로 복구한다.

    ★ 최상위 로거는 프로세스에 하나뿐인 전역 상태다. 복구하지 않으면 이 시험이
      뒤따르는 다른 시험의 로그 수집을 망가뜨린다.
    """
    root = logging.getLogger()
    원래_핸들러 = list(root.handlers)
    원래_수준 = root.level
    # 남의 라이브러리 로거 수준도 전역이라 되돌려 놔야 한다.
    원래_남의것 = {
        이름: logging.getLogger(이름).level
        for 이름 in logging_setup.NOISY_THIRD_PARTY_LOGGERS
    }
    root.handlers = []
    root.setLevel(logging.WARNING)  # 파이썬 기본값 = 결함이 있던 그 상태
    try:
        yield root
    finally:
        root.handlers = 원래_핸들러
        root.setLevel(원래_수준)
        for 이름, 수준 in 원래_남의것.items():
            logging.getLogger(이름).setLevel(수준)


class _테스트필터(logging.Filter):
    """레코드 메시지에서 «비밀»이라는 낱말을 지우는 가짜 필터."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = record.msg.replace("비밀", "[가림]")
        return True


class _다른필터(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return True


def _우리_핸들러들(root: logging.Logger) -> list[logging.Handler]:
    """우리가 단 핸들러만 센다.

    ★ 총 개수를 세면 안 된다 — pytest가 시험을 돌리는 동안 최상위 로거에
      자기 수집 핸들러(``LogCaptureHandler``)를 «다시» 달기 때문이다.
      실측으로 확인했다(2026-08-26).
    """
    return [
        handler
        for handler in root.handlers
        if getattr(handler, logging_setup._OWNED_HANDLER_MARK, False)
    ]


# ══════════════════════════════════════════════════════════
# ① 결함 재현 — 설정이 없으면 logger.info 는 «레코드조차» 안 만들어진다
# ══════════════════════════════════════════════════════════


def test_설정하기_전에는_info가_기록되지_않는다(빈_최상위_로거):
    """운영에서 실제로 벌어지던 상태를 재현한다. 이게 결함의 정체다."""
    앱_로거 = logging.getLogger("src.features.composer.dedupe")

    assert _우리_핸들러들(빈_최상위_로거) == []
    assert 빈_최상위_로거.level == logging.WARNING
    assert 앱_로거.isEnabledFor(logging.INFO) is False


def test_설정하면_info가_기록된다(빈_최상위_로거):
    """``configure_logging`` 한 번으로 앱 전체의 info 로그가 살아난다."""
    logging_setup.configure_logging(stream=io.StringIO())

    앱_로거 = logging.getLogger("src.features.composer.dedupe")
    assert 앱_로거.isEnabledFor(logging.INFO) is True


def test_설정하면_info_한_줄이_실제로_찍힌다(빈_최상위_로거):
    """레벨만 맞추는 것이 아니라 «글자가 나오는지»까지 본다."""
    출력 = io.StringIO()
    logging_setup.configure_logging(stream=출력)

    logging.getLogger("src.features.composer.dedupe").info(
        "장별 문장 수(정리 전→후): identity:1→0"
    )

    적힌_것 = 출력.getvalue()
    assert "장별 문장 수(정리 전→후): identity:1→0" in 적힌_것
    assert "INFO" in 적힌_것
    assert "src.features.composer.dedupe" in 적힌_것


# ══════════════════════════════════════════════════════════
# ② 여러 번 불러도 핸들러가 늘어나지 않는다 (멱등)
# ══════════════════════════════════════════════════════════


def test_여러_번_불러도_핸들러가_하나뿐이다(빈_최상위_로거):
    """시험은 같은 모듈을 여러 번 import한다. 늘어나면 로그가 겹쳐 찍힌다."""
    첫번째 = logging_setup.configure_logging(stream=io.StringIO())
    두번째 = logging_setup.configure_logging(stream=io.StringIO())
    세번째 = logging_setup.configure_logging(stream=io.StringIO())

    assert 첫번째 is 두번째 is 세번째
    assert _우리_핸들러들(빈_최상위_로거) == [첫번째]


def test_남이_단_핸들러는_지우지_않는다(빈_최상위_로거):
    """pytest도 최상위에 자기 핸들러를 단다. 그걸 지우면 시험이 로그를 못 잡는다."""
    남의_핸들러 = logging.StreamHandler(io.StringIO())
    빈_최상위_로거.addHandler(남의_핸들러)

    우리_핸들러 = logging_setup.configure_logging(stream=io.StringIO())

    assert 남의_핸들러 in 빈_최상위_로거.handlers
    assert 남의_핸들러 is not 우리_핸들러
    assert _우리_핸들러들(빈_최상위_로거) == [우리_핸들러]


# ══════════════════════════════════════════════════════════
# ③ LOG_LEVEL 환경변수 — Dockerfile이 이미 넣고 있는 그 이름
# ══════════════════════════════════════════════════════════


def test_LOG_LEVEL을_읽는다(빈_최상위_로거, monkeypatch):
    monkeypatch.setenv(logging_setup.ENV_LOG_LEVEL, "warning")
    logging_setup.configure_logging(stream=io.StringIO())

    앱_로거 = logging.getLogger("src.web.main")
    assert 앱_로거.isEnabledFor(logging.INFO) is False
    assert 앱_로거.isEnabledFor(logging.WARNING) is True


def test_LOG_LEVEL이_없으면_INFO다(빈_최상위_로거, monkeypatch):
    monkeypatch.delenv(logging_setup.ENV_LOG_LEVEL, raising=False)
    logging_setup.configure_logging(stream=io.StringIO())

    assert 빈_최상위_로거.level == logging.INFO


def test_LOG_LEVEL이_오타여도_서버가_뜬다(빈_최상위_로거, monkeypatch):
    """오타 하나로 서버가 못 뜨는 것이 로그가 많은 것보다 훨씬 나쁘다."""
    monkeypatch.setenv(logging_setup.ENV_LOG_LEVEL, "INFOO")
    logging_setup.configure_logging(stream=io.StringIO())

    assert 빈_최상위_로거.level == logging.INFO


@pytest.mark.parametrize(
    ("값", "기대"),
    [
        ("debug", logging.DEBUG),
        ("INFO", logging.INFO),
        ("Warning", logging.WARNING),
        ("ERROR", logging.ERROR),
        ("", logging.INFO),
        (None, logging.INFO),
        ("아무말", logging.INFO),
        (logging.DEBUG, logging.DEBUG),
    ],
)
def test_수준_해석(값, 기대):
    assert logging_setup.resolve_level(값) == 기대


# ══════════════════════════════════════════════════════════
# ④ 보안 — 필터는 «핸들러»에 걸려야 어느 로거에서 와도 가려진다
# ══════════════════════════════════════════════════════════


def test_필터가_핸들러에_걸린다(빈_최상위_로거):
    출력 = io.StringIO()
    logging_setup.configure_logging(stream=출력, filters=(_테스트필터(),))

    logging.getLogger("src.어디선가").info("비밀 값이 여기 있다")

    적힌_것 = 출력.getvalue()
    assert "비밀" not in 적힌_것
    assert "[가림] 값이 여기 있다" in 적힌_것


def test_같은_필터를_두_번_걸지_않는다(빈_최상위_로거):
    """조립 지점이 여러 번 import돼도 필터가 쌓이면 안 된다."""
    핸들러 = logging_setup.configure_logging(
        stream=io.StringIO(), filters=(_테스트필터(),)
    )
    logging_setup.configure_logging(filters=(_테스트필터(),))
    logging_setup.configure_logging(filters=(_테스트필터(),))

    같은_종류 = [f for f in 핸들러.filters if isinstance(f, _테스트필터)]
    assert len(같은_종류) == 1


def test_다른_종류의_필터는_더_걸린다(빈_최상위_로거):
    핸들러 = logging_setup.configure_logging(
        stream=io.StringIO(), filters=(_테스트필터(),)
    )
    logging_setup.configure_logging(filters=(_다른필터(),))

    assert any(isinstance(f, _테스트필터) for f in 핸들러.filters)
    assert any(isinstance(f, _다른필터) for f in 핸들러.filters)


# ══════════════════════════════════════════════════════════
# ⑤ 남의 라이브러리는 조용히 — 우리 로그만 켠다
# ══════════════════════════════════════════════════════════


def test_httpx는_info를_찍지_않는다(빈_최상위_로거):
    """★ 실제로 시험을 빨간불로 만든 그 결함이다 (2026-08-26).

    최상위만 INFO로 내렸더니 ``httpx``가 요청 URL을 통째로 찍었고, 그 URL에
    보고서 번호가 들어 있어 「감사 기록에 비밀이 새면 안 된다」 시험이 깨졌다.
    """
    출력 = io.StringIO()
    logging_setup.configure_logging(stream=출력)

    logging.getLogger("httpx").info(
        'HTTP Request: GET http://x/result/e3adff5240b75984c9f15ea5620c2629 "200 OK"'
    )

    assert "e3adff5240b75984c9f15ea5620c2629" not in 출력.getvalue()
    assert logging.getLogger("httpx").isEnabledFor(logging.INFO) is False


def test_남의_라이브러리를_조용히_해도_경고는_남는다(빈_최상위_로거):
    """조용히 시키는 것이지 «끄는» 것이 아니다. 진짜 문제는 보여야 한다."""
    출력 = io.StringIO()
    logging_setup.configure_logging(stream=출력)

    logging.getLogger("botocore").warning("백업 업로드가 실패했습니다")

    assert "백업 업로드가 실패했습니다" in 출력.getvalue()


def test_src_밖에_있는_우리_로거도_켜진다(빈_최상위_로거):
    """★ 「우리 것만 허용」 방식이었다면 이게 조용히 죽는다.

    ``security.admin_audit``는 ``src.*`` 이름이 아니다. 그래서 이 모듈은
    «켤 것»이 아니라 «막을 것»을 적는다.
    """
    출력 = io.StringIO()
    logging_setup.configure_logging(stream=출력)

    logging.getLogger("security.admin_audit").info("admin_audit {...}")

    assert "admin_audit {...}" in 출력.getvalue()


def test_더_조용히_하라는_지시를_되살리지_않는다(빈_최상위_로거, monkeypatch):
    """``LOG_LEVEL=ERROR``인데 남의 로거만 WARNING으로 «내려» 주면 지시 위반이다."""
    monkeypatch.setenv(logging_setup.ENV_LOG_LEVEL, "ERROR")
    logging_setup.configure_logging(stream=io.StringIO())

    assert logging.getLogger("httpx").level == logging.ERROR


def test_조용히_시킬_목록이_비어_있지_않다():
    """누가 목록을 통째로 지우면 유출 경로가 다시 열린다."""
    assert "httpx" in logging_setup.NOISY_THIRD_PARTY_LOGGERS
    assert "botocore" in logging_setup.NOISY_THIRD_PARTY_LOGGERS
    assert len(set(logging_setup.NOISY_THIRD_PARTY_LOGGERS)) == len(
        logging_setup.NOISY_THIRD_PARTY_LOGGERS
    ), "같은 이름이 두 번 들어갔다"


def test_이_모듈은_feature를_모른다():
    """``core``가 ``features``를 import하면 경계가 무너진다 (feature-atomic 규칙)."""
    from pathlib import Path  # noqa: PLC0415

    본문 = Path(logging_setup.__file__).read_text(encoding="utf-8")
    assert "src.features" not in 본문
