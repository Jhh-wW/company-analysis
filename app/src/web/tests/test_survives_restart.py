"""★ 서버를 껐다 켜도 보고서가 남는지 확인한다.

이게 없으면 **서버를 끄는 순간 만든 보고서가 전부 사라진다.**
사용자가 면접 전날 만들어 둔 자료가 다음 날 없어지는 것과 같다.

정본: 확정/00_공통/1_흐름/01_전체흐름.md 「14. 저장」
     · 확정/03_수집/2_규칙/03_캐시와저장.md
"""

from __future__ import annotations

import re
import time
import urllib.parse
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.export_pdf.release import ReleasedPdf, prepare_pdf_release
from src.features.pipeline.canonical_demo import (
    DEMO_COMPANY as CANONICAL_DEMO_COMPANY,
)
from src.features.pipeline.demo import DemoPipeline
from src.features.pipeline.port import Outcome
from src.features.report_access import constants as report_access_constants
from src.features.report_access import store as report_access_store
from src.features.report_standard import CANONICAL_SECTION_IDS, SECTION_BY_ID
from src.features.storage import db, job_interruptions, reports
from src.core import clock
from src.shared import engine_build_identity as build_identity_contract
from src.web import main
from src.web import job_runtime, runtime
from src.web.routers import reports as reports_router

#: 현재 출고 게이트를 통과하는 1~9장 canonical 데모 회사.
COMPANY = CANONICAL_DEMO_COMPANY
_REAL_REQUIRE_REPORT_DELIVERY = job_runtime._require_report_delivery


class _OwnedReportId(str):
    """문자열 호환 report ID와 브라우저 전용 grant를 함께 보존한다."""

    grant_token: str

    def __new__(cls, value: str, grant_token: str):
        instance = super().__new__(cls, value)
        instance.grant_token = grant_token
        return instance


def _owner_client(report_id: _OwnedReportId) -> TestClient:
    client = TestClient(main.app, base_url="https://testserver")
    client.cookies.set(
        report_access_constants.PUBLIC_GRANT_COOKIE_NAME,
        report_id.grant_token,
    )
    return client


def _admin_client() -> TestClient:
    client = TestClient(main.app, base_url="https://testserver")
    session = auth_logic.create_session(
        "admin@example.com", True, subject="google:restart-test-admin"
    )
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    return client


@pytest.fixture
def approved_pdf_route(monkeypatch):
    """재시작 시험은 승인 내용이 아닌 승인된 PDF의 영속 다운로드를 검증한다."""

    def release_state(*, report_id: str, report):
        del report_id
        candidate = prepare_pdf_release(report)
        record = SimpleNamespace(
            pdf_sha256=candidate.pdf_sha256,
            record_sha256="a" * 64,
        )
        return candidate, ReleasedPdf(content=candidate.pdf_bytes, record=record)

    monkeypatch.setattr(reports_router, "_release_state", release_state)


def _make_report(
    client: TestClient,
    *,
    expected_outcome: Outcome = Outcome.REPORT,
) -> str:
    """조사를 한 건 끝까지 돌리고 그 번호를 돌려준다."""
    runtime._PIPELINE = DemoPipeline()
    form = {
        "company": COMPANY,
        "region": "인천 서구",
    }
    confirm = client.post("/confirm", data=form)
    token = re.search(
        r'name="paid_attempt_token" value="([^"]+)"', confirm.text
    )
    assert token is not None
    run = client.post(
        "/run",
        data={**form, "paid_attempt_token": token.group(1)},
        follow_redirects=False,
    )
    job_id = run.headers["location"].rsplit("/", 1)[-1]
    for _ in range(100):
        if client.get(f"/api/progress/{job_id}").json()["finished"]:
            break
        time.sleep(0.05)
    else:
        pytest.fail("조사가 끝나지 않았습니다")

    result = job_runtime._JOBS[job_id].result
    assert result is not None and result.outcome is expected_outcome
    if expected_outcome is Outcome.REPORT:
        assert result.report is not None
        assert tuple(section.cell for section in result.report.sections) == (
            CANONICAL_SECTION_IDS
        )
    else:
        assert result.report is None
    return job_id


@pytest.fixture
def finished_job():
    """조사를 한 건 끝낸 뒤 «서버를 껐다 켠 것처럼» 메모리를 비운다."""
    with TestClient(main.app, base_url="https://testserver") as client:
        job_id = _make_report(client)
        grant_token = client.cookies.get(
            report_access_constants.PUBLIC_GRANT_COOKIE_NAME
        )
        assert grant_token
    job_runtime._JOBS.clear()          # ★ 재시작 흉내 — 메모리에 있던 것이 전부 사라진다
    yield _OwnedReportId(job_id, grant_token)


def test_보고서가_저장소에_남는다(finished_job):
    with db.connect() as conn:
        saved = reports.load(conn, finished_job)
    assert saved is not None, "서버를 끄면 보고서가 사라집니다"
    assert saved.sections, "항목이 통째로 비었습니다"
    assert saved.company == COMPANY


def test_재시작_뒤에도_보고서_화면이_열린다(finished_job):
    with _owner_client(finished_job) as client:
        response = client.get(f"/result/{finished_job}", follow_redirects=False)
    assert response.status_code == 200
    # 본문 항목이 실제로 그려져야 한다 (껍데기만 뜨면 안 된다)
    # ★ 제목 «글자»도, 제목 «마크업»도 박지 않는다 — 상수를 보고, 태그는 벗겨서 본다.
    #   박아 두면 문구를 다듬거나 꾸밈 태그를 넣을 때마다 «기능은 멀쩡한데» 깨진다
    #   (하루에 두 번 깨졌다 — 보고서체로 바꿀 때, 번호를 span으로 뺄 때).
    heads = [
        re.sub(r"<[^>]+>", "", h).strip()
        for h in re.findall(r"<h2>(.*?)</h2>", response.text, re.S)
    ]
    assert len(heads) == len(CANONICAL_SECTION_IDS), f"본문이 비었습니다: {heads}"
    for section_id, heading in zip(CANONICAL_SECTION_IDS, heads, strict=True):
        spec = SECTION_BY_ID[section_id]
        assert heading.startswith(spec.display_number)
        assert spec.title in heading


def test_기존_UUIDv4_32hex_결과도_재시작_뒤_계속_조회된다(finished_job):
    legacy_uuid_id = "123e4567e89b42d3a456426614174000"
    with db.connect() as conn:
        saved = reports.load(conn, finished_job)
        assert saved is not None
        reports.save(conn, legacy_uuid_id, "legacy-corp", saved.job, saved)
        report_access_store.issue_and_bind(
            conn,
            existing_token=finished_job.grant_token,
            run_id=legacy_uuid_id,
        )
    job_runtime._JOBS.clear()

    with _owner_client(finished_job) as client:
        response = client.get(f"/result/{legacy_uuid_id}", follow_redirects=False)

    assert response.status_code == 200
    assert saved.company in response.text


def test_재시작_뒤_완료된_진행주소는_저장된_결과로_복구한다(finished_job):
    with _owner_client(finished_job) as client:
        page = client.get(f"/progress/{finished_job}", follow_redirects=False)
        state = client.get(f"/api/progress/{finished_job}")

    assert page.status_code == 303
    assert page.headers["location"] == f"/result/{finished_job}"
    assert state.status_code == 200
    assert state.json() == {
        "done": [],
        "current": "",
        "finished": True,
        "next_url": f"/result/{finished_job}",
        "recovered": True,
    }


def test_재시작으로_미완료_진행정보가_사라지면_원인과_재시도를_보여준다():
    job_runtime._JOBS.clear()
    with _admin_client() as client:
        page = client.get("/progress/interrupted-job", follow_redirects=False)
        state = client.get("/api/progress/interrupted-job")

    assert page.status_code == 410
    assert "조사가 중단되었습니다" in page.text
    assert "서버가 다시 시작되었거나" in page.text
    assert "입력 오류가 아닙니다" in page.text
    assert 'href="/">처음부터 다시 조사하기</a>' in page.text

    assert state.status_code == 410
    assert state.json()["code"] == "job_unavailable"
    assert state.json()["retry_url"] == "/"
    assert "서버가 다시 시작되었거나" in state.json()["error"]


def test_종료시간을_넘긴_작업은_재시작뒤_명시적_중단상태로_복구한다():
    job_id = "known-interrupted-job"
    with db.connect() as conn:
        job_interruptions.mark(
            conn,
            job_id=job_id,
            interrupted_at=clock.iso_now_kst(),
            reason="shutdown_timeout",
        )
    job_runtime._JOBS.clear()

    with _admin_client() as client:
        page = client.get(f"/progress/{job_id}", follow_redirects=False)
        state = client.get(f"/api/progress/{job_id}")

    assert page.status_code == state.status_code == 409
    assert "작업이 중단되었습니다" in page.text
    assert "처음부터 다시 조사하기" in page.text
    assert state.json()["code"] == "job_interrupted"
    assert state.json()["retry_url"] == "/"


def test_없는_진행번호의_410은_번호나_내부정보를_반사하지않는다():
    requested = "attacker-controlled-missing-job-secret"
    job_runtime._JOBS.clear()
    with _admin_client() as client:
        page = client.get(f"/progress/{requested}", follow_redirects=False)
        state = client.get(f"/api/progress/{requested}")

    assert page.status_code == state.status_code == 410
    for body in (page.text, state.text):
        assert requested not in body
        assert "Traceback" not in body
        assert "storage.db" not in body
        assert "SELECT " not in body


def test_보고서_DB조회장애는_없는_job_410과_구분해_503_no_store로_응답한다(
    monkeypatch,
):
    def broken_load(*_args, **_kwargs):
        raise OSError("시험용 DB 조회 장애")

    job_runtime._JOBS.clear()
    with _admin_client() as client:
        monkeypatch.setattr(job_runtime.report_store, "load", broken_load)
        page = client.get("/progress/db-outage", follow_redirects=False)
        state = client.get("/api/progress/db-outage")

    assert page.status_code == state.status_code == 503
    for response in (page, state):
        assert response.headers["retry-after"] == "3"
        assert response.headers["cache-control"] == "private, no-store"
        assert "db-outage" not in response.text
        assert "시험용 DB 조회 장애" not in response.text
    assert state.json()["code"] == "progress_store_unavailable"
    assert state.json()["retryable"] is True
    assert "새 조사를 시작하지 말고" in state.json()["error"]


def test_새보고서저장실패는_불변출고없이_임시화면이나_재렌더로_열지않는다(
    monkeypatch,
):
    monkeypatch.setattr(
        job_runtime,
        "_require_report_delivery",
        _REAL_REQUIRE_REPORT_DELIVERY,
    )
    original_save = job_runtime.report_store.insert_new

    def broken_save(*_args, **_kwargs):
        raise OSError("시험용 저장 장애")

    with TestClient(main.app, base_url="https://testserver") as client:
        monkeypatch.setattr(job_runtime.report_store, "insert_new", broken_save)
        job_id = _make_report(client, expected_outcome=Outcome.FAILED)
        first = client.get(f"/result/{job_id}")

        assert first.status_code == 200
        failed_job = job_runtime._JOBS[job_id]
        assert failed_job.result is not None
        assert failed_job.result.outcome is Outcome.FAILED
        assert failed_job.result.report is None
        assert failed_job.result.charged is False
        assert failed_job.report_persisted is False
        assert failed_job.delivery_persisted is False
        assert "PDF 보고서 받기" not in first.text

        monkeypatch.setattr(job_runtime.report_store, "insert_new", original_save)
        # 새로고침 GET이 저장·승인·PDF 생성을 대신하면 같은 결함이 되살아난다.
        retried = client.get(f"/result/{job_id}", follow_redirects=False)
        assert retried.status_code == 200
        assert job_runtime._JOBS[job_id].report_persisted is False

        job_runtime._JOBS.clear()
        restarted = client.get(f"/result/{job_id}", follow_redirects=False)
        assert restarted.status_code == 503


def test_워드_다운로드는_재시작_뒤에도_410으로_닫혀_있다(finished_job):
    with TestClient(main.app) as client:
        response = client.get(f"/download/{finished_job}", follow_redirects=False)
    assert response.status_code == 410
    assert "PDF 보고서 받기" in response.text
    assert "no-store" in response.headers["cache-control"]


def test_재시작_뒤에도_PDF로_내려받을_수_있다(
    finished_job, approved_pdf_route
):
    # 이 파일의 공통 fixture는 unrelated 웹 시험을 빠르게 하려고 완료 adapter를
    # 값싼 성공으로 바꾼다. PDF 재시작 계약만큼은 실제 불변 delivery를 먼저
    # 확정해, 원본 없는 legacy를 오늘 renderer로 만드는 옛 동작에 기대지 않는다.
    with db.connect() as conn:
        saved = reports.load(conn, finished_job)
    assert saved is not None
    reports_router.finalize_new_report_delivery(
        report_id=finished_job,
        corp_id="restart-pdf-corp",
        billing_bucket_id="public",
        report=saved,
        actual_models=("deterministic-demo",),
        reused_from_cache=False,
        engine_build_identity=build_identity_contract.process_engine_build_identity(),
    )
    with _owner_client(finished_job) as client:
        response = client.get(
            f"/download/pdf/{finished_job}", follow_redirects=False
        )

    assert response.status_code == 200, "재시작 뒤 PDF 내려받기가 막혔습니다"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="'
    )
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    expected_name = urllib.parse.quote("주-진영-company-analysis.pdf")
    assert f"filename*=UTF-8''{expected_name}" in response.headers[
        "content-disposition"
    ]
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content.startswith(b"%PDF-")
    assert response.content.rstrip().endswith(b"%%EOF")


def test_PDF_저장소_조회장애는_503_no_store로_응답한다(monkeypatch):
    def unavailable(_job_id):
        raise reports_router.report_delivery_adapter.DeliveryAdapterError(
            "시험용 저장소 장애"
        )

    job_runtime._JOBS.clear()
    monkeypatch.setattr(
        reports_router.report_delivery_adapter,
        "load_legacy_public_report",
        unavailable,
    )

    with _admin_client() as client:
        response = client.get(
            "/download/pdf/storage-outage", follow_redirects=False
        )

    assert response.status_code == 503
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["retry-after"] == "3"
    assert "storage-outage" not in response.text
    assert "시험용 저장소 장애" not in response.text
    assert "content-disposition" not in response.headers


def test_없는_번호는_첫_화면으로_돌려보낸다():
    """★ 남의 번호를 찍어 넣어도 «남의 보고서»가 열리면 안 된다."""
    with _admin_client() as client:
        for path in ("/result/없는번호zzz", "/download/pdf/없는번호zzz"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 303, f"{path} 가 열렸습니다"
            assert response.headers["location"] == "/?report_status=unavailable"
            notice = client.get(response.headers["location"])
            assert notice.status_code == 200
            assert "요청한 보고서를 열 수 없어 일반 첫 화면을 열었습니다" in notice.text
            assert "없는번호zzz" not in notice.text
            assert "보고서가 존재하지" not in notice.text
            assert "보고서를 찾을 수 없" not in notice.text
        retired = client.get("/download/없는번호zzz", follow_redirects=False)
        assert retired.status_code == 410
        assert "no-store" in retired.headers["cache-control"]


def test_공유링크와_보고서_안내가_동시에_있어도_둘다_표시한다():
    with TestClient(main.app, base_url="https://testserver") as client:
        response = client.get("/?share_status=missing&report_status=unavailable")

    assert response.status_code == 200
    # 기대값 이전: 안내문에서 내부 용어 LINK를 걷어냈다.
    # 이 시험의 대상은 「두 안내가 동시에 나온다」이지 문구 자체가 아니다.
    assert "이 초대 링크를 찾을 수 없어" in response.text
    assert "요청한 보고서를 열 수 없어" in response.text


def test_저장된_보고서에_공고_원문이_없다(finished_job):
    """★ S2 — 공고 원문·이미지 잔존 0건 고정 (기획서 안전 가드레일).

    제3자 저작물이라 재배포하면 안 되고, 개인정보가 섞여 있을 수 있다.
    """
    with db.connect() as conn:
        row = conn.execute(
            "SELECT payload_json FROM reports WHERE report_id = ?", (finished_job,)
        ).fetchone()
    assert row is not None
    payload = row[0]
    # 요구역량(원문 «문장»)은 남아도 된다 — 공고 «원문 전체»가 없어야 한다.
    assert "posting_text" not in payload
    assert "공고원문" not in payload
