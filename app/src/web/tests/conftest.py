"""웹 라우트 시험의 공개 모드를 명시한다.

운영 기본값은 fail-closed지만, 기존 개별 라우트 시험은 인증과 무관한 동작을 본다.
관리자 전용 게이트 시험만 각 시험 안에서 다시 ``1``로 덮어쓴다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.features.auth import constants as auth_constants


@pytest.fixture(autouse=True)
def _explicitly_disable_beta_gate(monkeypatch):
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "0")
    # 관리자 여부는 이제 저장된 로그인 스냅샷이 아니라 매 요청의 현재 목록으로
    # 다시 계산한다. 웹 시험에서 쓰는 두 관리자 주소를 명시해 환경에 독립시킨다.
    monkeypatch.setenv(
        auth_constants.ENV_ADMIN_EMAILS,
        "admin@example.com,관리자@example.com",
    )


@pytest.fixture(autouse=True)
def _fresh_job_admission_state():
    """lifespan 종료 플래그가 다음 독립 시험으로 새지 않게 한다."""
    from src.web import job_runtime  # noqa: PLC0415

    job_runtime._start_job_runtime()


@pytest.fixture(autouse=True)
def _fresh_share_link_access_limiter():
    """프로세스 보조 limiter가 독립 웹 시험 사이에서 상태를 공유하지 않는다."""

    from src.features.sharelink import access_control  # noqa: PLC0415

    access_control.reset_for_tests()
    try:
        yield
    finally:
        access_control.reset_for_tests()


@pytest.fixture(autouse=True)
def _approved_pdf_release_for_unrelated_web_contracts(monkeypatch):
    """기존 웹 시험은 각 기능만 본다. 실제 승인 경계는 전용 route 시험이 소유한다."""

    from src.web import job_runtime  # noqa: PLC0415
    from src.web.routers import reports as reports_router  # noqa: PLC0415

    release_record = SimpleNamespace(
        pdf_sha256="a" * 64,
        record_sha256="b" * 64,
    )
    released_pdf = SimpleNamespace(
        content=b"%PDF-1.4\n% approved test double\n",
        record=release_record,
    )
    monkeypatch.setattr(
        reports_router,
        "_release_state",
        lambda **_kwargs: (object(), released_pdf),
    )
    # 새 완료 경계는 실제 PDF 전 페이지 렌더·검사를 포함한다. 링크 길이·CSRF처럼
    # 그 경계와 무관한 시험 수백 건이 매번 PDF를 만들면 짧은 polling 계약까지
    # 렌더 시간에 종속된다. 전용 report_delivery 통합 시험이 실제 경계를 검증하고,
    # 나머지는 기존 보고서 경로를 쓰도록 두 worker 함수만 값싼 성공으로 격리한다.
    monkeypatch.setattr(job_runtime, "_require_report_delivery", lambda _job: True)
    monkeypatch.setattr(job_runtime, "_finalize_report_delivery", lambda _job: True)
    yield
    job_runtime._start_job_runtime()
