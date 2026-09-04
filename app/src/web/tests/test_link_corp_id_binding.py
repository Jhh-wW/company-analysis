"""옛 저장본도 동명 회사를 가르는지 못 박는다.

★ 무엇이 문제였나 — 보고서 본문의 `company_id`는 **출고 상태가 FULL일 때만**
  채워진다(`pipeline/real.py:3519`). 안전 확인 중에 나간 옛 저장본은 본문이 비어
  있어서, 본문만 보고 대조하면 「이름이 같은 다른 법인」이 그대로 통과한다.

★ 무엇으로 막나 — 저장 표 `reports`의 **`corp_id` 열**은 출고 상태와 무관하게
  저장 경로가 항상 채운다(`storage/cache.py:398` → `storage/reports.py:save`).
  결속 대조는 본문이 아니라 이 열을 먼저 읽어야 한다.

⚠️ 여기서 쓰는 두 고유번호는 **서로 다른 리터럴**이다. 같은 값을 쓰면 「가른다」를
  확인하는 것이 아니라 아무것도 확인하지 않는 것이 된다.
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
# ① 본문이 비어도 열로 가른다
# ══════════════════════════════════════════════════════════


def test_본문에_고유번호가_없어도_동명_다른법인은_묶이지_않는다(admin: TestClient):
    처음, 회사 = _옛저장본(_우리회사)
    key_hash = _링크에_묶는다(처음, 회사)
    남의것, 같은이름 = _옛저장본(_동명타사)
    assert 같은이름 == 회사, "이름이 같아야 «이름만으로는 못 가른다»를 확인한다"

    응답 = admin.post(
        "/admin/links/report",
        data={"key": key_hash, "report_reference": 남의것},
        follow_redirects=False,
    )

    assert 응답.status_code == 400
    assert "다른 법인의 보고서입니다" in 응답.text
    assert _동명타사 in 응답.text
    assert _결속된_보고서() == 처음


def test_같은_법인의_다른_보고서는_본문이_비어도_묶인다(admin: TestClient):
    """★ 대조군 — 전부 막으면 「가른다」가 아니라 「아무것도 못 묶는다」다."""

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
# ② 화면 경고는 «둘 다 없을 때»만
# ══════════════════════════════════════════════════════════

_경고문 = "이 보고서에는 회사 고유번호가 없어 같은 이름의 다른 회사와 구분하지 못합니다"


def test_열에만_고유번호가_있으면_경고를_보이지_않는다(admin: TestClient):
    처음, 회사 = _옛저장본(_우리회사)
    key_hash = _링크에_묶는다(처음, 회사)

    화면 = admin.get(f"/admin/link/{key_hash}/extend")

    assert 화면.status_code == 200
    assert _경고문 not in 화면.text


def test_열도_본문도_비면_경고를_보인다(admin: TestClient):
    처음, 회사 = _옛저장본("")
    key_hash = _링크에_묶는다(처음, 회사)

    화면 = admin.get(f"/admin/link/{key_hash}/extend")

    assert 화면.status_code == 200
    assert _경고문 in 화면.text


# ══════════════════════════════════════════════════════════
# ③ 읽기 실패는 여전히 «거부»
# ══════════════════════════════════════════════════════════


def test_열을_읽지_못하면_연결하지_않는다(admin: TestClient, monkeypatch):
    """★ 열 읽기를 새로 넣었다고 fail-open이 생기면 안 된다."""

    처음, 회사 = _옛저장본(_우리회사)
    key_hash = _링크에_묶는다(처음, 회사)
    새것, _같은이름 = _옛저장본(_우리회사)

    def 터진다(*_args, **_kwargs):
        raise RuntimeError("저장소를 읽지 못했습니다")

    monkeypatch.setattr(
        "src.web.routers.admin.report_store.load_corp_id", 터진다
    )

    응답 = admin.post(
        "/admin/links/report",
        data={"key": key_hash, "report_reference": 새것},
        follow_redirects=False,
    )

    assert 응답.status_code == 400
    assert "확인할 수 없어" in 응답.text
    assert _결속된_보고서() == 처음
