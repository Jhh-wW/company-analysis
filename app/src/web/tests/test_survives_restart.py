"""★ 서버를 껐다 켜도 보고서가 남는지 확인한다.

이게 없으면 **서버를 끄는 순간 만든 보고서가 전부 사라진다.**
사용자가 면접 전날 만들어 둔 자료가 다음 날 없어지는 것과 같다.

정본: 확정/00_공통/1_흐름/01_전체흐름.md 「14. 저장」
     · 확정/03_수집/2_규칙/03_캐시와저장.md
"""

from __future__ import annotations

import io
import re

from src.core.constants import CELL_LABELS
import time

import pytest
from docx import Document
from fastapi.testclient import TestClient

from src.features.storage import db, reports
from src.web import main

#: 데모에서 보고서가 나오는 회사 (재료가 골고루 있다)
COMPANY = "파마리서치"


def _make_report(client: TestClient) -> str:
    """조사를 한 건 끝까지 돌리고 그 번호를 돌려준다."""
    form = {
        "company": COMPANY,
        "job": "의료기기 개발",
        "region": "강원 강릉시",
        "posting_text": "x",
    }
    confirm = client.post("/confirm", data=form)
    ref = re.search(r'name="ref" value="([^"]*)"', confirm.text).group(1)
    run = client.post(
        "/run", data={**form, "legal_name": COMPANY, "ref": ref}, follow_redirects=False
    )
    job_id = run.headers["location"].rsplit("/", 1)[-1]
    for _ in range(100):
        if client.get(f"/api/progress/{job_id}").json()["finished"]:
            break
        time.sleep(0.05)
    else:
        pytest.fail("조사가 끝나지 않았습니다")
    return job_id


@pytest.fixture
def finished_job():
    """조사를 한 건 끝낸 뒤 «서버를 껐다 켠 것처럼» 메모리를 비운다."""
    with TestClient(main.app) as client:
        job_id = _make_report(client)
    main._JOBS.clear()          # ★ 재시작 흉내 — 메모리에 있던 것이 전부 사라진다
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
    assert any(CELL_LABELS["1"] in h for h in heads), f"본문이 비었습니다: {heads}"


def test_재시작_뒤에도_워드로_내려받을_수_있다(finished_job):
    with TestClient(main.app) as client:
        response = client.get(f"/download/{finished_job}", follow_redirects=False)
    assert response.status_code == 200, "재시작 뒤 워드 내려받기가 막혔습니다"
    document = Document(io.BytesIO(response.content))
    assert document.paragraphs[0].text == COMPANY


def test_없는_번호는_첫_화면으로_돌려보낸다():
    """★ 남의 번호를 찍어 넣어도 «남의 보고서»가 열리면 안 된다."""
    with TestClient(main.app) as client:
        for path in ("/result/없는번호zzz", "/download/없는번호zzz"):
            response = client.get(path, follow_redirects=False)
            assert response.status_code == 303, f"{path} 가 열렸습니다"


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
