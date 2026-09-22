"""옛 저장본도 회사 고유번호 대조 없이 관리자가 연결할 수 있다.

보고서 본문과 저장 열의 고유번호가 다르거나 비어 있어도 연결을 허용한다.
보고서 자체의 존재·출고·공유 기간 검사는 별도로 유지한다.
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import main, runtime

_열쇠 = "0f0f0f0f1a1a1a1a2b2b2b2b3c3c3c3c"
_발급 = "2026-09-02T09:00:00+09:00"

#: 서로 다른 두 법인의 고유번호(리터럴).
_우리회사 = "00126380"
_동명타사 = "99999999"


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


def _옛저장본(corp_id: str) -> tuple[str, str]:
    """열에는 고유번호가 있고 **본문에는 없는** 보고서 하나.

    ★ 안전 확인 중에 나간 저장본이 정확히 이 모양이다. 두 값을 다 채우면
      옛 저장본을 흉내 내지 못해 이 시험이 아무것도 안 지킨다.
    """

    report_id = uuid.uuid4().hex
    report = replace(build_demo_report(), company_id="")
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, corp_id, report.job, report)
        되살린 = report_store.load(conn, report_id)
        열값 = report_store.load_corp_id(conn, report_id)
    assert 되살린 is not None and 되살린.company_id == ""
    assert 열값 == corp_id
    return report_id, report.company


def _링크에_묶는다(report_id: str, company: str) -> str:
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
    return share_store.key_hash_of(_열쇠)


def _결속된_보고서() -> str:
    with storage_db.connect() as conn:
        link = share_store.load(conn, _열쇠)
    assert link is not None
    return link.report_id


# ══════════════════════════════════════════════════════════
# ① 본문이나 저장 열의 고유번호는 연결을 제한하지 않는다
# ══════════════════════════════════════════════════════════


def test_legacy_report_with_different_stored_company_id_can_be_attached(admin: TestClient):
    original_id, company = _옛저장본(_우리회사)
    key_hash = _링크에_묶는다(original_id, company)
    replacement_id, replacement_company = _옛저장본(_동명타사)
    assert replacement_company == company

    response = admin.post(
        "/admin/links/report",
        data={"key": key_hash, "report_reference": replacement_id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert _결속된_보고서() == replacement_id
    with storage_db.connect() as conn:
        assert report_store.load_corp_id(conn, original_id) == _우리회사
        assert report_store.load_corp_id(conn, replacement_id) == _동명타사


def test_같은_법인의_다른_보고서는_본문이_비어도_묶인다(admin: TestClient):
    """같은 고유번호의 옛 보고서도 계속 연결할 수 있다."""

    처음, 회사 = _옛저장본(_우리회사)
    key_hash = _링크에_묶는다(처음, 회사)
    새것, _같은이름 = _옛저장본(_우리회사)

    응답 = admin.post(
        "/admin/links/report",
        data={"key": key_hash, "report_reference": 새것},
        follow_redirects=False,
    )

    assert 응답.status_code == 303
    assert _결속된_보고서() == 새것


# ══════════════════════════════════════════════════════════
# ② 고유번호가 없어도 연결 폼을 제공한다
# ══════════════════════════════════════════════════════════

_경고문 = "이 보고서에는 회사 고유번호가 없어 같은 이름의 다른 회사와 구분하지 못합니다"


def test_열에만_고유번호가_있으면_경고를_보이지_않는다(admin: TestClient):
    처음, 회사 = _옛저장본(_우리회사)
    key_hash = _링크에_묶는다(처음, 회사)

    화면 = admin.get(f"/admin/link/{key_hash}/extend")

    assert 화면.status_code == 200
    assert _경고문 not in 화면.text


def test_missing_company_id_does_not_restrict_attachment_form(admin: TestClient):
    report_id, company = _옛저장본("")
    key_hash = _링크에_묶는다(report_id, company)

    page = admin.get(f"/admin/link/{key_hash}/extend")

    assert page.status_code == 200
    assert _경고문 not in page.text
    assert 'name="report_reference"' in page.text
    assert f'value="{report_id}"' in page.text


# ══════════════════════════════════════════════════════════
# ③ 고유번호 조회는 연결에 필요하지 않다
# ══════════════════════════════════════════════════════════


def test_attachment_does_not_read_company_id_column(admin: TestClient, monkeypatch):
    """선택한 보고서를 읽을 수 있으면 별도 고유번호 조회 없이 연결한다."""

    original_id, company = _옛저장본(_우리회사)
    key_hash = _링크에_묶는다(original_id, company)
    replacement_id, _ = _옛저장본(_우리회사)
    calls = []

    def fail_company_id_lookup(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("저장소를 읽지 못했습니다")

    monkeypatch.setattr(
        "src.web.routers.admin.report_store.load_corp_id", fail_company_id_lookup
    )

    response = admin.post(
        "/admin/links/report",
        data={"key": key_hash, "report_reference": replacement_id},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert _결속된_보고서() == replacement_id
    assert calls == []
