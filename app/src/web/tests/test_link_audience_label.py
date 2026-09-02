"""링크 표시 이름과 고유번호 경고를 못 박는다 (티켓 G-S4, G-S12b(b)).

★ 이 시험이 지키는 것 두 가지
  ① **표시 이름은 우리 쪽 값이다.** 링크가 스무 개가 되면 회사명만으로는
     어느 링크인지 모른다. 그래서 관리 화면에는 보이고, **받는 사람 화면에는
     절대 안 나간다.** 「하이브 인사팀」처럼 상대 조직을 적는 값이라 그대로
     내보내면 받는 사람에게 우리 내부 분류를 보여 주는 셈이 된다.
  ② **고유번호 없는 결속은 경고한다.** 회사 고유번호가 없는 보고서를 링크에
     묶으면 이후 재결속은 «이름»만 대조하게 되고, 같은 이름의 다른 회사가
     통과한다(발견 F-GS2p1b).
"""

from __future__ import annotations

import re
import uuid
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import main, runtime

_표시이름 = "하이브 인사팀"
_발급 = "2026-09-02T09:00:00+09:00"


@pytest.fixture
def client():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    csrf = auth_logic.csrf_token_for_session(session.token)
    original_post = client.post

    def post_with_csrf(url, *args, **kwargs):
        data = dict(kwargs.pop("data", {}) or {})
        data.setdefault("csrf_token", csrf)
        return original_post(url, *args, data=data, **kwargs)

    client.post = post_with_csrf
    return client


def _발급요청(admin: TestClient, **extra) -> tuple[str, str]:
    data = {"company": "하이브", "job": "인사", "audience_label": _표시이름}
    data.update(extra)
    response = admin.post("/admin/links/new", data=data, follow_redirects=False)
    assert response.status_code == 200
    found = re.search(r"/k/([0-9a-f]{32})", response.text)
    assert found is not None
    return found.group(1), response.headers["x-link-identifier"]


def _보고서를_저장한다(*, company_id: str) -> tuple[str, str]:
    """고유번호가 있는/없는 저장 보고서를 하나 만든다.

    ★ 고유번호는 저장 인자가 아니라 **보고서 본문의 값**이다
      (`storage/reports.py` 는 `report.company_id` 를 payload에 넣는다).
      인자만 바꾸면 두 경우가 똑같아져 대조군이 대조를 못 한다.
    """

    report_id = uuid.uuid4().hex
    report = replace(build_demo_report(), company_id=company_id)
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, company_id, report.job, report)
        stored = report_store.load(conn, report_id)
    assert stored is not None and stored.company_id == company_id
    return report_id, report.company


# ══════════════════════════════════════════════════════════
# ① 표시 이름
# ══════════════════════════════════════════════════════════


def test_audience_label은_관리자_화면에만_보인다(admin: TestClient):
    key, key_hash = _발급요청(admin)

    with storage_db.connect() as conn:
        저장 = share_store.load(conn, key)
    assert 저장 is not None and 저장.audience_label == _표시이름

    관리화면 = [
        admin.get("/admin/access"),
        admin.get(f"/admin/link/{key_hash}/extend"),
        admin.get("/admin/links"),
        admin.get(f"/admin/links/{key_hash}"),
    ]
    for 화면 in 관리화면:
        assert 화면.status_code == 200, 화면.url
        assert _표시이름 in 화면.text, 화면.url

    # 받는 사람: 열쇠 쿠키만 들고 오는 손님의 화면에는 없어야 한다.
    손님 = TestClient(main.app)
    with 손님:
        입장 = 손님.get(f"/k/{key}", follow_redirects=False)
        assert 입장.status_code == 303
        assert 손님.cookies.get(KEY_COOKIE_NAME) == key
        첫화면 = 손님.get("/")
    assert 첫화면.status_code == 200
    assert _표시이름 not in 첫화면.text


def test_표시이름을_안_적으면_화면이_비어도_망가지지_않는다(admin: TestClient):
    key, key_hash = _발급요청(admin, audience_label="")

    with storage_db.connect() as conn:
        저장 = share_store.load(conn, key)
    assert 저장 is not None and 저장.audience_label == ""
    assert admin.get(f"/admin/link/{key_hash}/extend").status_code == 200
    assert admin.get("/admin/links").status_code == 200


# ══════════════════════════════════════════════════════════
# ② 고유번호 없는 결속 경고 (G-S12b(b))
# ══════════════════════════════════════════════════════════

_경고문 = "이 보고서에는 회사 고유번호가 없어 같은 이름의 다른 회사와 구분하지 못합니다"


def test_고유번호_없는_결속은_상세화면에_경고를_보인다(admin: TestClient):
    report_id, company = _보고서를_저장한다(company_id="")
    _열쇠 = "1111222233334444aaaabbbbccccdddd"
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=_열쇠,
            company=company,
            job="인사",
            report_id=report_id,
            now_iso=_발급,
        )
        conn.commit()
    key_hash = share_store.key_hash_of(_열쇠)

    화면 = admin.get(f"/admin/link/{key_hash}/extend")

    assert 화면.status_code == 200
    assert _경고문 in 화면.text


def test_고유번호가_있으면_경고를_보이지_않는다(admin: TestClient):
    """★ 대조군 — 경고가 «언제나» 뜨면 아무것도 알려 주지 않는다."""

    report_id, company = _보고서를_저장한다(company_id="00126380")
    _열쇠 = "5555666677778888aaaabbbbccccdddd"
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=_열쇠,
            company=company,
            job="인사",
            report_id=report_id,
            now_iso=_발급,
        )
        conn.commit()
    key_hash = share_store.key_hash_of(_열쇠)

    화면 = admin.get(f"/admin/link/{key_hash}/extend")

    assert 화면.status_code == 200
    assert _경고문 not in 화면.text


def test_결속_보고서가_없으면_경고를_보이지_않는다(admin: TestClient):
    _key, key_hash = _발급요청(admin)

    화면 = admin.get(f"/admin/link/{key_hash}/extend")

    assert 화면.status_code == 200
    assert _경고문 not in 화면.text
