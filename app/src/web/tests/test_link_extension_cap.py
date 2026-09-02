"""링크 총 수명 상한과 「확인 불가」 경고를 못 박는다 (티켓 G-S4c, 결정 D-G8b).

★ 왜 총 상한이 필요한가 — 한 번에 90일까지만 미룰 수 있어도, 미루기를 반복하면
  링크는 영원히 산다. QR은 한 번 뿌리면 회수할 수 없으므로 **발급일로부터 세는
  천장**이 따로 있어야 한다. 1회 상한(90일)은 「실수로 2099년」을 막고, 총 상한
  (365일)은 「조금씩 계속 미루기」를 막는다. 둘은 다른 위험을 막는다.

★ 함께 보는 것 — 고유번호를 «확인하지 못했을 때»도 관리자에게 말한다. 「없다」와
  「못 읽었다」를 같은 침묵으로 처리하면, 읽기가 깨진 동안 관리자는 아무 문제가
  없다고 믿는다.

⚠️ 날짜 수는 **리터럴**이다. 생산 상수를 import해 같은 상수와 비교하면 값이 몰래
  바뀌어도 시험이 그대로 통과한다.
"""

from __future__ import annotations

import datetime as dt
import uuid
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import main, runtime

_열쇠 = "1c1c1c1c2d2d2d2d3e3e3e3e4f4f4f4f"

#: 발급일부터 세는 총 수명 상한(리터럴).
_총상한 = 365
#: 한 번에 미룰 수 있는 최대 날 수(리터럴).
_1회상한 = 90

_상한도달문구 = "이 링크는 더 미룰 수 없습니다. 새 링크를 발급해 주세요"


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


def _발급일() -> dt.date:
    """오늘을 발급일로 삼는다 — 「발급일 + N일」을 그대로 셀 수 있게."""

    return clock.today_kst()


def _링크(*, 만료가_발급후: int, report_id: str = "") -> str:
    """발급일이 오늘이고 지금 만료일이 «발급 + 만료가_발급후일»인 링크."""

    발급 = _발급일()
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=_열쇠,
            company="카카오",
            job="인사",
            report_id=report_id,
            now_iso=f"{발급.isoformat()}T09:00:00+09:00",
        )
        assert share_store.set_expires_at(
            conn,
            key_hash=share_store.key_hash_of(_열쇠),
            expires_at=(발급 + dt.timedelta(days=만료가_발급후)).isoformat(),
        )
        conn.commit()
    return share_store.key_hash_of(_열쇠)


def _만료일() -> str:
    with storage_db.connect() as conn:
        link = share_store.load(conn, _열쇠)
    assert link is not None
    return link.expires_at


def _발급후(days: int) -> str:
    return (_발급일() + dt.timedelta(days=days)).isoformat()


def _미룬다(admin: TestClient, key_hash: str, 새날: str):
    return admin.post(
        f"/admin/link/{key_hash}/extend",
        data={"expires_on": 새날, "reason": "채용 일정이 밀렸습니다"},
        follow_redirects=False,
    )


# ══════════════════════════════════════════════════════════
# ① 총 수명 상한 365일
# ══════════════════════════════════════════════════════════


def test_총_수명_상한을_넘기는_연장은_거절한다(admin: TestClient):
    """발급 + 300일에서 한 번에 90일을 더 미루면 발급 + 390일이 된다."""

    key_hash = _링크(만료가_발급후=300)
    이전 = _만료일()

    응답 = _미룬다(admin, key_hash, _발급후(300 + _1회상한))

    assert 응답.status_code == 400
    assert _만료일() == 이전
    with storage_db.connect() as conn:
        assert share_store.list_link_adjustments(conn, key_hash=key_hash) == []


def test_정확히_발급후_365일까지는_미룰_수_있다(admin: TestClient):
    """★ 경계 바로 안쪽 — 상한이 하루 어긋나면 못 잡는다.

    발급 + 275일에서 90일을 더하면 정확히 발급 + 365일이다.
    """

    key_hash = _링크(만료가_발급후=_총상한 - _1회상한)

    응답 = _미룬다(admin, key_hash, _발급후(_총상한))

    assert 응답.status_code == 303
    assert _만료일() == _발급후(_총상한)


def test_발급후_366일은_거절한다(admin: TestClient):
    """경계 바로 바깥. **1회 상한(90일) 안**이지만 총 상한을 하루 넘는다.

    ★ 기준 만료일을 발급 + 300일로 둔다. 300 + 90 = 390이라 1회 상한은 366을
      허용하고, 막는 것은 오직 총 상한이다 — 그래야 이 시험이 총 상한만 잰다.
    """

    key_hash = _링크(만료가_발급후=300)
    이전 = _만료일()

    응답 = _미룬다(admin, key_hash, _발급후(_총상한 + 1))

    assert _총상한 + 1 <= 300 + _1회상한, "1회 상한 안쪽이어야 총 상한만 재는 시험이다"
    assert 응답.status_code == 400
    assert _만료일() == 이전


def test_상한에_닿은_링크는_연장_폼_대신_안내를_보여준다(admin: TestClient):
    key_hash = _링크(만료가_발급후=_총상한)

    화면 = admin.get(f"/admin/link/{key_hash}/extend")

    assert 화면.status_code == 200
    assert _상한도달문구 in 화면.text
    assert 'name="expires_on"' not in 화면.text


def test_상한에_닿은_링크는_POST로도_못_미룬다(admin: TestClient):
    """★ 화면에서 폼을 숨기는 것은 방어가 아니다. 주소로 바로 쳐도 막힌다."""

    key_hash = _링크(만료가_발급후=_총상한)
    이전 = _만료일()

    응답 = _미룬다(admin, key_hash, _발급후(_총상한 + 30))

    assert 응답.status_code == 400
    assert _상한도달문구 in 응답.text
    assert _만료일() == 이전


def test_아직_여유가_있으면_폼과_최대날짜를_보여준다(admin: TestClient):
    """★ 대조군 — 전부 막으면 「상한이 있다」가 아니라 「연장이 없다」다."""

    key_hash = _링크(만료가_발급후=30)

    화면 = admin.get(f"/admin/link/{key_hash}/extend")

    assert 화면.status_code == 200
    assert 'name="expires_on"' in 화면.text
    assert _상한도달문구 not in 화면.text
    assert _발급후(30 + _1회상한) in 화면.text


def test_상한_근처에서는_남은_기간까지만_보여준다(admin: TestClient):
    """발급 + 340일이면 1회 상한(90일)이 아니라 남은 25일이 최대다."""

    key_hash = _링크(만료가_발급후=_총상한 - 25)

    화면 = admin.get(f"/admin/link/{key_hash}/extend")

    assert 화면.status_code == 200
    assert f'max="{_발급후(_총상한)}"' in 화면.text


# ══════════════════════════════════════════════════════════
# ② 고유번호를 «확인하지 못했을» 때도 말한다
# ══════════════════════════════════════════════════════════

_없음문구 = "이 보고서에는 회사 고유번호가 없어 같은 이름의 다른 회사와 구분하지 못합니다"
_불가문구 = "이 보고서의 회사 고유번호를 확인하지 못했습니다"


def _보고서(corp_id: str) -> str:
    report_id = uuid.uuid4().hex
    report = replace(build_demo_report(), company_id="")
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, corp_id, report.job, report)
        conn.commit()
    return report_id


@pytest.mark.parametrize(
    "경로",
    ["/admin/link/{}/extend", "/admin/links/{}"],
)
def test_고유번호를_확인하지_못하면_두_화면_모두_확인불가를_말한다(
    admin: TestClient, monkeypatch, 경로
):
    """★ 「없다」와 「못 읽었다」를 같은 침묵으로 두면, 읽기가 깨진 동안
    관리자는 아무 문제가 없다고 믿는다.
    """

    key_hash = _링크(만료가_발급후=30, report_id=_보고서("00126380"))

    def 터진다(*_args, **_kwargs):
        raise RuntimeError("저장소를 읽지 못했습니다")

    monkeypatch.setattr(report_store, "load_corp_id", 터진다)

    화면 = admin.get(경로.format(key_hash))

    assert 화면.status_code == 200
    assert _불가문구 in 화면.text
    assert _없음문구 not in 화면.text


@pytest.mark.parametrize(
    "경로",
    ["/admin/link/{}/extend", "/admin/links/{}"],
)
def test_고유번호가_있으면_두_화면_모두_조용하다(admin: TestClient, 경로):
    """★ 대조군."""

    key_hash = _링크(만료가_발급후=30, report_id=_보고서("00126380"))

    화면 = admin.get(경로.format(key_hash))

    assert 화면.status_code == 200
    assert _불가문구 not in 화면.text
    assert _없음문구 not in 화면.text


@pytest.mark.parametrize(
    "경로",
    ["/admin/link/{}/extend", "/admin/links/{}"],
)
def test_고유번호가_없으면_두_화면_모두_없음을_말한다(admin: TestClient, 경로):
    key_hash = _링크(만료가_발급후=30, report_id=_보고서(""))

    화면 = admin.get(경로.format(key_hash))

    assert 화면.status_code == 200
    assert _없음문구 in 화면.text
    assert _불가문구 not in 화면.text
