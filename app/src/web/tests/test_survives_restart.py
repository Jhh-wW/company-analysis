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

from src.features.export_pdf.release import ReleasedPdf, prepare_pdf_release
from src.features.pipeline.canonical_demo import (
    DEMO_COMPANY as CANONICAL_DEMO_COMPANY,
)
from src.features.pipeline.demo import DemoPipeline
from src.features.pipeline.port import Outcome
from src.features.report_standard import CANONICAL_SECTION_IDS, SECTION_BY_ID
from src.features.storage import db, reports
from src.web import main
from src.web import job_runtime, runtime
from src.web.routers import reports as reports_router

#: 현재 출고 게이트를 통과하는 1~9장 canonical 데모 회사.
COMPANY = CANONICAL_DEMO_COMPANY


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


def _make_report(client: TestClient) -> str:
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
    assert result is not None and result.outcome is Outcome.REPORT
    assert result.report is not None
    assert tuple(section.cell for section in result.report.sections) == (
        CANONICAL_SECTION_IDS
    )
    return job_id


@pytest.fixture
def finished_job():
    """조사를 한 건 끝낸 뒤 «서버를 껐다 켠 것처럼» 메모리를 비운다."""
    with TestClient(main.app) as client:
        job_id = _make_report(client)
    job_runtime._JOBS.clear()          # ★ 재시작 흉내 — 메모리에 있던 것이 전부 사라진다
    yield job_id


def test_보고서가_저장소에_남는다(finished_job):
    with db.connect() as conn:
        saved = reports.load(conn, finished_job)
    assert saved is not None, "서버를 끄면 보고서가 사라집니다"
    assert saved.sections, "항목이 통째로 비었습니다"
    assert saved.company == COMPANY


def test_재시작_뒤에도_보고서_화면이_열린다(finished_job):
    with TestClient(main.app) as client:
        response = client.get(f"/result/{finished_job}", follow_redirects=False)
    assert response.status_code == 200
    # 본문 항목이 실제로 그려져야 한다 (껍데기만 뜨면 안 된다)
    # ★ 제목 «글자»도, 제목 «마크업»도 박지 않는다 — 상수를 보고, 태그는 벗겨서 본다.
    #   박아 두면 문구를 다듬거나 꾸밈 태그를 넣을 때마다 «기능은 멀쩡한데» 깨진다
    #   (2026-08-16 하루에 두 번 깨졌다 — 보고서체로 바꿀 때, 번호를 span으로 뺄 때).
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
    job_runtime._JOBS.clear()

    with TestClient(main.app) as client:
        response = client.get(f"/result/{legacy_uuid_id}", follow_redirects=False)

    assert response.status_code == 200
    assert saved.company in response.text


def test_재시작_뒤_완료된_진행주소는_저장된_결과로_복구한다(finished_job):
    with TestClient(main.app) as client:
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
    with TestClient(main.app) as client:
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


def test_없는_진행번호의_410은_번호나_내부정보를_반사하지않는다():
    requested = "attacker-controlled-missing-job-secret"
    job_runtime._JOBS.clear()
    with TestClient(main.app) as client:
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
    with TestClient(main.app) as client:
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


def test_보고서저장실패는_재시작복구불가를_알리고_새로고침으로_재시도한다(
    monkeypatch,
):
    original_save = job_runtime.report_store.insert_new

    def broken_save(*_args, **_kwargs):
        raise OSError("시험용 저장 장애")

    with TestClient(main.app) as client:
        monkeypatch.setattr(job_runtime.report_store, "insert_new", broken_save)
        job_id = _make_report(client)
        first = client.get(f"/result/{job_id}")

        assert first.status_code == 200
        assert "아직 저장되지 않았습니다" in first.text
        assert "서버가 다시 시작되면 복구할 수 없습니다" in first.text
        assert "저장 다시 시도" in first.text
        assert "지금 PDF도 내려받아 보관해 주세요" in first.text
        assert "지금 PDF 보고서 받기" in first.text
        assert "DOCX" not in first.text
        assert job_runtime._JOBS[job_id].report_persisted is False

        monkeypatch.setattr(job_runtime.report_store, "insert_new", original_save)
        retried = client.get(f"/result/{job_id}")
        assert retried.status_code == 200
        assert "아직 저장되지 않았습니다" not in retried.text
        assert job_runtime._JOBS[job_id].report_persisted is True

        job_runtime._JOBS.clear()
        recovered = client.get(f"/api/progress/{job_id}")
        assert recovered.status_code == 200
        assert recovered.json()["recovered"] is True


def test_워드_다운로드는_재시작_뒤에도_410으로_닫혀_있다(finished_job):
    with TestClient(main.app) as client:
        response = client.get(f"/download/{finished_job}", follow_redirects=False)
    assert response.status_code == 410
    assert "PDF 보고서 받기" in response.text
    assert "no-store" in response.headers["cache-control"]


def test_재시작_뒤에도_PDF로_내려받을_수_있다(
    finished_job, approved_pdf_route
):
    with TestClient(main.app) as client:
        response = client.get(
            f"/download/pdf/{finished_job}", follow_redirects=False
        )

    assert response.status_code == 200, "재시작 뒤 PDF 내려받기가 막혔습니다"
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"].startswith(
        'attachment; filename="'
    )
    assert "filename*=UTF-8''" in response.headers["content-disposition"]
    expected_name = urllib.parse.quote(f"{COMPANY}_분석_보고서.pdf")
    assert f"filename*=UTF-8''{expected_name}" in response.headers[
        "content-disposition"
    ]
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.content.startswith(b"%PDF-")
    assert response.content.rstrip().endswith(b"%%EOF")


def test_PDF_저장소_조회장애는_503_no_store로_응답한다(monkeypatch):
    def unavailable(_job_id):
        raise job_runtime.ReportStoreUnavailable("시험용 저장소 장애")

    job_runtime._JOBS.clear()
    monkeypatch.setattr(job_runtime, "_load_saved_report", unavailable)

    with TestClient(main.app) as client:
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
    with TestClient(main.app) as client:
        for path in ("/result/없는번호zzz", "/download/pdf/없는번호zzz"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 303, f"{path} 가 열렸습니다"
        retired = client.get("/download/없는번호zzz", follow_redirects=False)
        assert retired.status_code == 410
        assert "no-store" in retired.headers["cache-control"]


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
