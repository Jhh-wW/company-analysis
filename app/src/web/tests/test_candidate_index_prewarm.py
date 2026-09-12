"""기동 시 후보 검색 색인 예열이 운영 배선에 제대로 꽂혔는지 검사한다.

★ 진짜 카탈로그 함수(``real._company_catalog``)를 가짜로 바꿔 끼운다.
  다른 진입점(예: 이 파일이 새로 만든 래퍼)만 부르는지 보면, 예열이 첫
  검색과 «다른» 캐시를 채우고도 시험은 초록불일 수 있다 — 그래서 검색이
  실제로 쓰는 함수 이름으로 직접 단정한다(``app/src/features/pipeline/real.py``
  ``search_business_candidates``의 ``index = _company_candidate_index()``,
  그 함수가 부르는 ``_company_catalog()``).

★ 예열은 ``_lifespan``이 연 데몬 스레드(``_candidate_index_manager_loop``)에서
  돈다. 그 루프는 예열 한 번 다음 법인목록 7일 주기 갱신 검사를
  무한 반복한다 — 시험에서 진짜 ``time.sleep(3600)``으로 늘어지지 않게,
  이 파일의 모든 시험은 기본으로 검사 함수를 1회만 부르고 멈추게 한다
  (``_stop_manager_loop_promptly`` 참고).

★ 예열은 스레드 완료를 결정적으로 기다리기 위해
  ``runtime._prewarm_candidate_index``(진짜 구현을 그대로 호출하는 얇은
  스파이)를 감싸 완료 이벤트를 받는다 — 카탈로그 함수를 가짜로 바꾼 것과는
  별개로, 실제 운영 코드 경로는 전부 그대로 실행된다.
"""

from __future__ import annotations

import threading

import pytest
from fastapi.testclient import TestClient

from src.core.constants import PIPELINE_ENV, PIPELINE_REAL
from src.features.pipeline import real
from src.web import runtime
from src.web.main import app

# 이 파일의 모든 시험은 무한 루프인 색인 관리자 스레드를 «1회 검사 뒤 예외로
# 멈추기» 패턴으로 끝낸다(데몬 스레드 안에서 던지므로 프로세스·다른 시험은
# 안 죽는다). pytest는 그 처리되지 않은 스레드 예외를
# PytestUnhandledThreadExceptionWarning으로 보고하는데, 이건 버그가 아니라
# 의도한 정지 신호이므로 이 파일 전체에서 그 경고만 끈다.
pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnhandledThreadExceptionWarning"
)


class _ManagerLoopStoppedForTest(Exception):
    """시험에서 색인 관리자 루프를 1회 검사 뒤 바로 멈추려고만 쓰는 표식.

    데몬 스레드 안에서 던지므로 이 예외가 나도 프로세스·다른 시험은 안 죽는다
    (파이썬 스레드의 처리되지 않은 예외는 stderr에만 찍히고 끝난다).
    """


@pytest.fixture(autouse=True)
def _reset_candidate_index_globals(monkeypatch):
    """예열 시험이 다른 시험의 색인 캐시 상태를 오염시키지 않게 한다."""

    monkeypatch.setattr(real, "_COMPANY_CANDIDATE_INDEX_SOURCE", None)
    monkeypatch.setattr(real, "_COMPANY_CANDIDATE_INDEX", None)


@pytest.fixture(autouse=True)
def _stop_manager_loop_promptly(monkeypatch):
    """이 파일의 모든 시험에서 관리자 루프가 진짜 sleep(3600)로 늘어지지 않게 한다.

    ``_candidate_index_manager_loop``는 예열 뒤 무한 루프를 돈다. 시험이
    끝나도 데몬 스레드는 pytest 프로세스 안에서 계속 살아 있을 수 있으므로,
    기본값으로 검사 함수가 불리자마자 예외를 던져 스레드를 끝낸다. 개별
    시험이 ``runtime._run_candidate_catalog_refresh_check``를 자기 것으로
    다시 monkeypatch하면(같은 ``monkeypatch`` 객체라 나중 설정이 이긴다)
    그 시험의 설정이 이 기본값을 덮어쓴다.
    """

    def _default_stop(*_args, **_kwargs) -> None:
        raise _ManagerLoopStoppedForTest

    monkeypatch.setattr(
        runtime, "_run_candidate_catalog_refresh_check", _default_stop
    )


def _spy_prewarm(monkeypatch) -> threading.Event:
    """진짜 ``_prewarm_candidate_index``를 그대로 부르되 완료 신호를 남긴다."""

    done = threading.Event()
    original = runtime._prewarm_candidate_index

    def spy() -> None:
        try:
            original()
        finally:
            done.set()

    monkeypatch.setattr(runtime, "_prewarm_candidate_index", spy)
    return done


def test_PIPELINE_real이면_기동시_실제_검색이_쓰는_카탈로그함수가_백그라운드에서_불린다(
    monkeypatch, caplog
) -> None:
    """운영 배선이 ``search_business_candidates``와 같은 함수를 예열에서 부른다."""

    called = threading.Event()

    def fake_catalog():
        called.set()
        return (("00000001", "예열시험회사"),)

    monkeypatch.setattr(real, "_company_catalog", fake_catalog)
    monkeypatch.setenv(PIPELINE_ENV, PIPELINE_REAL)
    done = _spy_prewarm(monkeypatch)

    with caplog.at_level("INFO", logger=runtime.logger.name):
        with TestClient(app):
            assert called.wait(timeout=5.0), "예열 스레드가 카탈로그 함수를 부르지 않았다"
            assert done.wait(timeout=5.0), "예열 스레드가 끝나지 않았다"

    assert any(
        "후보 색인 예열 완료" in record.message for record in caplog.records
    ), "예열 완료 INFO 로그가 남지 않았다"


def test_PIPELINE_demo면_예열을_돌리지_않는다(monkeypatch) -> None:
    """데모·시험 기본값에서는 예열 스레드 자체를 띄우지 않는다."""

    called = threading.Event()
    monkeypatch.setattr(real, "_company_catalog", lambda: (called.set(), ())[1])
    monkeypatch.delenv(PIPELINE_ENV, raising=False)

    with TestClient(app):
        assert not called.wait(timeout=0.3), "demo 모드인데 카탈로그 함수가 불렸다"


def test_CANDIDATE_INDEX_PREWARM_0이면_real모드여도_예열을_돌리지_않는다(
    monkeypatch,
) -> None:
    """끄기 플래그가 정확히 "0"이면 PIPELINE=real이어도 예열·갱신 검사를 건너뛴다."""

    called = threading.Event()
    check_called = threading.Event()
    monkeypatch.setattr(real, "_company_catalog", lambda: (called.set(), ())[1])
    monkeypatch.setattr(
        runtime,
        "_run_candidate_catalog_refresh_check",
        lambda: check_called.set(),
    )
    monkeypatch.setenv(PIPELINE_ENV, PIPELINE_REAL)
    monkeypatch.setenv(runtime.CANDIDATE_INDEX_PREWARM_ENV, "0")

    with TestClient(app):
        assert not called.wait(timeout=0.3), "끄기 플래그를 켰는데 카탈로그 함수가 불렸다"
        assert not check_called.wait(
            timeout=0.1
        ), "끄기 플래그를 켰는데 갱신 검사 함수가 불렸다"


def test_예열_뒤_색인_관리자_루프가_갱신검사_함수를_부른다(monkeypatch) -> None:
    """``_candidate_index_manager_loop``가 예열 다음 단계로 갱신 검사를 부르는지 본다.

    루프 자체는 무한이므로, 검사 함수가 불리는 순간 신호를 남기고
    ``_ManagerLoopStoppedForTest``를 던져 시험 안에서 결정적으로 끝낸다.
    """

    monkeypatch.setattr(real, "_company_catalog", lambda: (("00000001", "회사"),))
    prewarm_done = _spy_prewarm(monkeypatch)
    check_called = threading.Event()

    def fake_check() -> None:
        check_called.set()
        raise _ManagerLoopStoppedForTest

    monkeypatch.setattr(runtime, "_run_candidate_catalog_refresh_check", fake_check)
    monkeypatch.setenv(PIPELINE_ENV, PIPELINE_REAL)

    with TestClient(app):
        assert prewarm_done.wait(timeout=5.0), "예열이 끝나지 않았다"
        assert check_called.wait(timeout=5.0), "예열 뒤 갱신 검사 함수가 불리지 않았다"


def test_예열이_실패해도_기동은_정상으로_열리고_비밀은_로그에_남지_않는다(
    monkeypatch, caplog
) -> None:
    """DART 키 없음 같은 예열 실패가 서비스 기동 자체를 막지 않는다."""

    secret_marker = "절대-로그에-남으면-안되는-비밀-문자열"

    def boom():
        raise RuntimeError(secret_marker)

    monkeypatch.setattr(real, "_company_catalog", boom)
    monkeypatch.setenv(PIPELINE_ENV, PIPELINE_REAL)
    done = _spy_prewarm(monkeypatch)

    with caplog.at_level("WARNING", logger=runtime.logger.name):
        with TestClient(app) as client:
            assert done.wait(timeout=5.0), "예열 스레드가 예외 뒤 끝나지 않았다"
            # ``/readyz``는 예열 성공 여부와 무관하게 응답해야 한다(기동을 막지 않음).
            response = client.get("/readyz")

    assert response.status_code != 500
    assert secret_marker not in caplog.text
    assert any(
        "후보 색인 예열이 실패했습니다" in record.message for record in caplog.records
    ), "예열 실패 WARNING 로그가 남지 않았다"
