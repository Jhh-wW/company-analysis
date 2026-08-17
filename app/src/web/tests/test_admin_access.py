"""초대·링크 관리 화면이 실제로 도는지 못 박는다 (문제로그 P-96).

★ 이 화면이 왜 필요했나 — 열쇠 링크와 초대 명단은 코드로는 완성이었는데
  **발급하려면 DB에 손으로 넣어야** 했다. 사용자 입장에서는 «없는 기능»이나 같다.

★ 여기서 지키는 것
  ① **관리자만** 들어온다 — 화면에서 버튼을 숨기는 건 방어가 아니다
  ② 발급·삭제·초대·취소가 «실제로» DB에 반영된다
  ③ **최악의 하루 지출**을 화면이 말한다 — 전체 상한이 없으니 그 숫자가 곧 예산이다
  ④ **localhost 주소로 발급하면 경고한다** — 그대로 포폴에 넣으면 죽은 링크가 된다
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.web import main


@pytest.fixture
def client():
    main._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        yield client


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """관리자로 로그인한 손님."""
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    return client


# ══════════════════════════════════════════════════════════
# ① 관리자만 들어온다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "경로", ["/admin/access", "/admin/link/a1b2c3d4e5f60718"]
)
def test_로그인_안_하면_관리_화면을_못_본다(client: TestClient, 경로: str):
    response = client.get(경로, follow_redirects=False)

    assert response.status_code in (302, 303, 307), "막혀야 한다"


@pytest.mark.parametrize(
    "경로", ["/admin/link/new", "/admin/link/delete", "/admin/invite", "/admin/revoke"]
)
def test_로그인_안_하면_바꾸지도_못한다(client: TestClient, 경로: str):
    """★ 화면을 막는 것과 «바꾸는 것»을 막는 것은 다른 일이다."""
    response = client.post(
        경로,
        data={"company": "x", "job": "y", "key": "a1b2c3d4e5f60718", "email": "a@b.c"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303, 307)


def test_관리자는_들어온다(admin: TestClient):
    assert admin.get("/admin/access").status_code == 200


# ══════════════════════════════════════════════════════════
# ② 발급·삭제가 실제로 반영된다
# ══════════════════════════════════════════════════════════


def test_링크를_발급하면_저장된다(admin: TestClient):
    response = admin.post(
        "/admin/link/new",
        data={"company": "카카오", "job": "마케팅", "note": "하반기 공채"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    key = response.headers["location"].rsplit("/", 1)[-1]
    with storage_db.connect() as conn:
        link = share_store.load(conn, key)
    assert link is not None
    assert (link.company, link.job, link.note) == ("카카오", "마케팅", "하반기 공채")


def test_발급하면_바로_주소_화면으로_보낸다(admin: TestClient):
    """★ 목록으로 돌려보내면 방금 만든 것을 다시 찾아 눌러야 한다."""
    response = admin.post(
        "/admin/link/new", data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )

    assert response.headers["location"].startswith("/admin/link/")


def test_링크를_닫을_수_있다(admin: TestClient):
    """★ 되돌릴 방법이 있어야 한다 — 잘못 보냈거나 지원이 끝났을 때."""
    created = admin.post(
        "/admin/link/new", data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )
    key = created.headers["location"].rsplit("/", 1)[-1]

    admin.post("/admin/link/delete", data={"key": key}, follow_redirects=False)

    with storage_db.connect() as conn:
        assert share_store.load(conn, key) is None


def test_친구를_초대하고_뺄_수_있다(admin: TestClient):
    admin.post(
        "/admin/invite", data={"email": "Friend@Gmail.com", "note": "스터디"},
        follow_redirects=False,
    )
    with storage_db.connect() as conn:
        assert share_allow.is_allowed(conn, "friend@gmail.com"), "대소문자를 맞춰야 한다"

    admin.post("/admin/revoke", data={"email": "friend@gmail.com"},
               follow_redirects=False)

    with storage_db.connect() as conn:
        assert not share_allow.is_allowed(conn, "friend@gmail.com")


def test_이상한_이메일은_안_들어간다(admin: TestClient):
    admin.post("/admin/invite", data={"email": "골뱅이없음"}, follow_redirects=False)

    with storage_db.connect() as conn:
        assert share_allow.list_all(conn) == []


# ══════════════════════════════════════════════════════════
# ③ ★ 최악의 하루 지출을 화면이 «말한다»
# ══════════════════════════════════════════════════════════


def test_최악의_지출을_화면에_적는다(admin: TestClient):
    """★ 전체 상한을 두지 않기로 했으므로(사용자 결정), **뿌린 개수가 곧 예산**이다.

    이 숫자를 안 보여주면 얼마가 나갈지 «모르는 채로» 두는 셈이 된다.
    """
    admin.post("/admin/link/new", data={"company": "카카오", "job": "마케팅"})
    admin.post("/admin/invite", data={"email": "f@g.com"})

    text = admin.get("/admin/access").text

    assert "최악의 하루 지출" in text
    # 링크 1개(3,000) + 친구 1명(1,000) + 관리자(5,000) = 9,000원
    assert "9,000원" in text


def test_전체_상한이_없다는_사실을_숨기지_않는다(admin: TestClient):
    """★ 위험을 «없앤» 게 아니라 본인이 감수하기로 한 것이다 — 계속 보여준다."""
    assert "전체 상한이 없습니다" in admin.get("/admin/access").text


# ══════════════════════════════════════════════════════════
# ④ QR·주소, 그리고 localhost 경고
# ══════════════════════════════════════════════════════════


def test_주소와_QR을_보여준다(admin: TestClient):
    created = admin.post(
        "/admin/link/new", data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )
    key = created.headers["location"].rsplit("/", 1)[-1]

    text = admin.get(f"/admin/link/{key}").text

    assert f"/k/{key}" in text
    assert "<svg" in text, "QR이 화면에 있어야 한다"


def test_localhost_주소면_경고한다(admin: TestClient):
    """★ 그대로 포폴에 넣으면 인사팀에게는 «안 열리는 링크»가 된다."""
    created = admin.post(
        "/admin/link/new", data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )
    key = created.headers["location"].rsplit("/", 1)[-1]

    text = admin.get(f"/admin/link/{key}").text

    assert "내 컴퓨터에서만 열립니다" in text


def test_없는_링크는_목록으로_보낸다(admin: TestClient):
    response = admin.get("/admin/link/0f1e2d3c4b5a6978", follow_redirects=False)

    assert response.headers["location"] == "/admin/access"
