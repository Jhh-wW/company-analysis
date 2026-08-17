"""보고서 링크가 «안전하게 공유되고, 두 달 뒤 닫히는지» 못 박는다 (문제로그 P-93).

★ 결정 (2026-08-16 사용자) — 링크는 **아는 사람 누구나** 본다.
  인사팀이 동료에게 링크를 넘기는 것이 오히려 원하는 일이고, 내용도
  공개된 공시·뉴스라 민감도가 낮다. 공고 원문은 애초에 저장되지 않는다(정본 S2).

★ 대신 «새어나갈 길»을 막았다. 이 시험이 세 가지를 지킨다:
  ① 주소를 추측 못 하게 (128비트)
  ② 검색엔진 차단
  ③ 외부 사이트로 주소가 자동 전달되지 않게 (리퍼러)
  그리고 **두 달 뒤 저절로 닫힌다.** 공유·만료 설명은 사용자 요청에 따라 화면에서 뺐다.
"""

from __future__ import annotations

import datetime as dt
import re

import pytest
from fastapi.testclient import TestClient

from src.core.constants import REMOVED_RESULT_COPY_MARKERS
from src.features.budget.sharing import REPORT_ID_HEX_CHARS, REPORT_LINK_MAX_AGE_DAYS
from src.features.pipeline.demo import DemoPipeline
from src.web import main
from src.web.tests._visible_text import visible_text


@pytest.fixture
def client():
    """★ 반드시 `with`로 연다.

    그냥 `TestClient(app)`를 쓰면 요청마다 실행 흐름이 새로 열리고 닫혀서,
    뒤에서 도는 조사(`asyncio.create_task`)가 **중간에 취소된다.**
    그러면 `finished=True`인데 `result=None`인 «반쪽 상태»가 되어
    보고서가 영영 안 나온다 — 실제로 그것 때문에 시험 8개가 깨졌다.
    """
    with TestClient(main.app) as client:
        yield client


def _보고서를_만든다(client: TestClient) -> str:
    """정상 흐름으로 보고서 하나를 만들고 그 주소 번호를 돌려준다."""
    main._PIPELINE = DemoPipeline()
    form = {
        "company": "우리엔",
        "job": "영업",
        "region": "서울",
        "posting_text": "x",
        "legal_name": "우리엔",
        "ref": "재수집-p003",
        "address": "-",
    }
    run = client.post("/run", data=form, follow_redirects=False)
    job_id = run.headers["location"].rsplit("/", 1)[-1]
    for _ in range(200):
        if client.get(f"/api/progress/{job_id}").json()["finished"]:
            break
    return job_id


# ══════════════════════════════════════════════════════════
# ① 주소를 추측할 수 없다
# ══════════════════════════════════════════════════════════


def test_주소가_128비트다(client: TestClient):
    """★ 공개 링크가 되는 이상 여유를 크게 둔다. 예전에는 12자리(48비트)였다."""
    job_id = _보고서를_만든다(client)

    assert len(job_id) == REPORT_ID_HEX_CHARS == 32
    assert re.fullmatch(r"[0-9a-f]{32}", job_id)


# ══════════════════════════════════════════════════════════
# ② 검색엔진·리퍼러 차단
# ══════════════════════════════════════════════════════════


def test_보고서_화면이_검색에_안_걸리게_한다(client: TestClient):
    job_id = _보고서를_만든다(client)

    headers = client.get(f"/result/{job_id}").headers

    assert "noindex" in headers.get("x-robots-tag", "")


def test_보고서_화면이_주소를_외부로_안_흘린다(client: TestClient):
    """★ 가장 현실적인 유출 경로 — 보고서 안의 뉴스 링크를 누르면
    그 사이트에 「어느 주소에서 왔는지」가 자동 전달된다."""
    job_id = _보고서를_만든다(client)

    headers = client.get(f"/result/{job_id}").headers

    assert headers.get("referrer-policy") == "no-referrer"


def test_내려받기에도_같은_보호가_걸린다(client: TestClient):
    job_id = _보고서를_만든다(client)

    headers = client.get(f"/download/{job_id}").headers

    assert "noindex" in headers.get("x-robots-tag", "")
    assert headers.get("referrer-policy") == "no-referrer"
    assert ".docx" in headers.get("content-disposition", ""), "내려받기는 계속 돼야 한다"


# ══════════════════════════════════════════════════════════
# ③ 삭제한 공유·내려받기 설명이 화면에 없다
# ══════════════════════════════════════════════════════════


def test_삭제한_공유와_내려받기_설명이_화면에_없다(client: TestClient):
    job_id = _보고서를_만든다(client)

    shown = visible_text(client.get(f"/result/{job_id}").text)

    for removed in REMOVED_RESULT_COPY_MARKERS:
        assert removed not in shown


# ══════════════════════════════════════════════════════════
# ④ 두 달 뒤 저절로 닫힌다
# ══════════════════════════════════════════════════════════


def test_기간이_지난_링크는_안_열린다(client: TestClient, monkeypatch):
    """★ P-93 그 자체."""
    job_id = _보고서를_만든다(client)
    지난뒤 = dt.date.today() + dt.timedelta(days=REPORT_LINK_MAX_AGE_DAYS + 1)
    monkeypatch.setattr(main.link_expiry, "is_expired", lambda *a, **k: True)

    response = client.get(f"/result/{job_id}")

    assert response.status_code == 410, "410 Gone = 「있었는데 이제 없다」"
    assert "기간이 지난" in response.text
    del 지난뒤


def test_기간이_지나면_내려받기도_막힌다(client: TestClient, monkeypatch):
    """★ 화면만 막고 파일을 열어 두면 막은 게 아니다."""
    job_id = _보고서를_만든다(client)
    monkeypatch.setattr(main.link_expiry, "is_expired", lambda *a, **k: True)

    response = client.get(f"/download/{job_id}")

    assert response.status_code == 410
    assert "docx" not in response.headers.get("content-disposition", "")


def test_만료_화면이_막다른_길이_아니다(client: TestClient, monkeypatch):
    """★ 「없는 보고서」로 보이면 사용자는 자기가 잘못 왔다고 생각한다."""
    job_id = _보고서를_만든다(client)
    monkeypatch.setattr(main.link_expiry, "is_expired", lambda *a, **k: True)

    text = client.get(f"/result/{job_id}").text

    assert "보고서가 잘못된 것이 아닙니다" in text
    assert "새로 조사하기" in text


def test_기간_안이면_그대로_열린다(client: TestClient):
    """★ 반대 방향 — 오늘 만든 것이 안 열리면 그게 고장이다."""
    job_id = _보고서를_만든다(client)

    assert client.get(f"/result/{job_id}").status_code == 200
