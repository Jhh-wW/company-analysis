"""웹 라우트 시험의 공개 모드를 명시한다.

운영 기본값은 fail-closed지만, 기존 개별 라우트 시험은 인증과 무관한 동작을 본다.
관리자 전용 게이트 시험만 각 시험 안에서 다시 ``1``로 덮어쓴다.
"""

from __future__ import annotations

import hashlib
import io
from types import SimpleNamespace

import pytest

from src.features.auth import constants as auth_constants


@pytest.fixture(autouse=True)
def _explicitly_disable_beta_gate(monkeypatch):
    # 웹 프로세스는 시작 때 exact full commit을 동결한다. 배포 신원이 필요한
    # 출고 시험들이 로컬 shell 환경에 우연히 의존하지 않게 정상 기본값을 둔다.
    # unknown 동작을 보는 시험은 자기 본문에서 두 이름을 명시적으로 지운다.
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.delenv("APP_GIT_COMMIT", raising=False)
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

    from src.core import clock  # noqa: PLC0415
    from src.features.admin_dashboard import store as dashboard_store  # noqa: PLC0415
    from src.features.storage import db as storage_db  # noqa: PLC0415
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
    # 링크 길이·CSRF 시험마다 전체 보고서 PDF를 그리면 느리지만, 상태만 손으로
    # normal로 바꾸면 Delivery가 없는 raw를 정상처럼 여기는 거짓 초록불이 된다.
    # 따라서 PDF 후보 렌더만 1쪽짜리 결정론적 후보로 줄이고, 의무→Delivery→
    # artifact→승인→청구→publication은 생산 함수를 그대로 통과시킨다.
    from pypdf import PdfReader, PdfWriter  # noqa: PLC0415
    from reportlab.lib.pagesizes import A4  # noqa: PLC0415
    from reportlab.pdfgen import canvas  # noqa: PLC0415

    from src.features.export_pdf import logic as pdf_logic  # noqa: PLC0415
    from src.features.export_pdf.release import (  # noqa: PLC0415
        PdfReleaseCandidate,
        _render_all_pages,
        report_fact_id_ledger,
    )

    def _cheap_candidate(_report_id, report):
        manifest_version, manifest_sha256 = pdf_logic._content_manifest_metadata(
            report,
            projection=report.public_projection,
        )
        raw = io.BytesIO()
        pdf = canvas.Canvas(raw, pagesize=A4, invariant=1)
        pdf.drawString(72, 760, "Company analysis test delivery")
        pdf.showPage()
        pdf.save()
        reader = PdfReader(io.BytesIO(raw.getvalue()), strict=True)
        writer = PdfWriter(clone_from=reader)
        writer.add_metadata(
            {
                "/CompanyAnalysisContentManifestVersion": manifest_version,
                "/CompanyAnalysisContentManifestSHA256": manifest_sha256,
            }
        )
        sealed = io.BytesIO()
        writer.write(sealed)
        pdf_bytes = sealed.getvalue()
        pages = _render_all_pages(pdf_bytes, scale=1.0)
        return PdfReleaseCandidate(
            pdf_bytes=pdf_bytes,
            pdf_sha256=hashlib.sha256(pdf_bytes).hexdigest(),
            pages=pages,
            expected_fact_ids=report_fact_id_ledger(report),
            render_scale=1.0,
            content_manifest_version=manifest_version,
            content_manifest_sha256=manifest_sha256,
        )

    monkeypatch.setattr(reports_router, "_candidate_for_report", _cheap_candidate)
    yield
    job_runtime._start_job_runtime()
