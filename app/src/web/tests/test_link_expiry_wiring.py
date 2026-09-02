"""저장된 만료일이 «남은 판정과 화면»에도 닿는지 못 박는다 (티켓 G-S4b).

★ 왜 따로 두나 — 만료일을 표에 적어 두어도, 그 값을 **안 읽는 판정이 하나라도
  남아 있으면** 링크는 그 판정에서만 다르게 산다. 여기서 보는 것은 판정 로직이
  아니라 **배선**이다.

★ 함께 보는 것 하나 더 — 관리 화면의 「보고서 생성 후 N일」은 LINK 수명이 아니라
  **보고서 공개 기간**이다(`budget/sharing.py` `REPORT_LINK_MAX_AGE_DAYS`). 두 값이
  60일로 같던 시절에는 아무 값이나 넣어도 맞아 보였다. LINK가 90일이 되면서
  갈렸으므로, 「LINK 수명을 바꿔도 이 문구는 안 바뀐다」를 못 박는다.

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
from src.web import main, request_helpers, runtime

_열쇠 = "7a7a7a7a8b8b8b8b9c9c9c9c0d0d0d0d"

#: 옛 규칙(리터럴). 이 날 수로 굳은 링크는 그날부터 닫혀 있어야 한다.
_옛수명 = 60
#: 보고서 공개 기간(리터럴). LINK 수명과 **다른 값**이다.
_보고서공개일 = 60


@pytest.fixture
def client():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as test_client:
        yield test_client


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    return client


def _옛규칙으로_굳은_링크(*, 지난날: int = _옛수명 + 1, report_id: str = "") -> str:
    """발급한 지 `지난날`이 지났고 만료일이 «발급 + 60일»로 굳어 있는 링크.

    ★ 이 모양이 결정적이다 — 발급일만 보면 (기본 90일이라) 아직 안 닫혔고,
      저장된 만료일을 봐야 닫혀 있다. 두 판정을 가르는 유일한 경우다.
    """

    발급일 = clock.today_kst() - dt.timedelta(days=지난날)
    발급시각 = f"{발급일.isoformat()}T09:00:00+09:00"
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=_열쇠,
            company="카카오",
            job="인사",
            report_id=report_id,
            now_iso=발급시각,
        )
        assert share_store.set_expires_at(
            conn,
            key_hash=share_store.key_hash_of(_열쇠),
            expires_at=(발급일 + dt.timedelta(days=_옛수명)).isoformat(),
        )
        conn.commit()
    return share_store.key_hash_of(_열쇠)


def _아직_안_닫힌_링크(report_id: str = "") -> str:
    발급시각 = clock.iso_now_kst()
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=_열쇠,
            company="카카오",
            job="인사",
            report_id=report_id,
            now_iso=발급시각,
        )
        conn.commit()
    return share_store.key_hash_of(_열쇠)


def _보고서를_굽는다() -> str:
    report_id = uuid.uuid4().hex
    report = replace(build_demo_report(), company_id="")
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "00126380", report.job, report)
        conn.commit()
    return report_id


# ══════════════════════════════════════════════════════════
# ① 남은 판정 두 곳
# ══════════════════════════════════════════════════════════


def test_옛규칙으로_굳은_링크는_조사권한_판정에서도_만료다():
    """`request_helpers._load_active_share_link` — 새 조사 권한의 입구."""

    _옛규칙으로_굳은_링크()

    with storage_db.connect() as conn:
        assert request_helpers._load_active_share_link(conn, _열쇠) is None


def test_아직_안_닫힌_링크는_조사권한_판정을_통과한다():
    """★ 대조군 — 전부 None이면 「만료를 읽는다」가 아니라 「다 막는다」다."""

    _아직_안_닫힌_링크()

    with storage_db.connect() as conn:
        assert request_helpers._load_active_share_link(conn, _열쇠) is not None


def test_옛규칙으로_굳은_링크는_상세화면에서도_만료로_보인다(admin: TestClient):
    """`dashboard.py`의 상세 화면 상태 — 관리자가 보는 글자."""

    key_hash = _옛규칙으로_굳은_링크()

    화면 = admin.get(f"/admin/links/{key_hash}")

    assert 화면.status_code == 200
    assert "자동 만료됨" in 화면.text
    assert "사용 가능" not in 화면.text


def test_아직_안_닫힌_링크는_상세화면에서_사용_가능이다(admin: TestClient):
    key_hash = _아직_안_닫힌_링크()

    화면 = admin.get(f"/admin/links/{key_hash}")

    assert 화면.status_code == 200
    assert "사용 가능" in 화면.text
    assert "자동 만료됨" not in 화면.text


def test_상세화면_만료일은_저장된_값을_그대로_보여준다(admin: TestClient):
    key_hash = _아직_안_닫힌_링크()
    미룬날 = (clock.today_kst() + dt.timedelta(days=45)).isoformat()
    with storage_db.connect() as conn:
        assert share_store.set_expires_at(
            conn, key_hash=key_hash, expires_at=미룬날
        )
        conn.commit()

    화면 = admin.get(f"/admin/links/{key_hash}")

    assert 화면.status_code == 200
    assert 미룬날 in 화면.text


# ══════════════════════════════════════════════════════════
# ② 「보고서 생성 후 N일」은 LINK 수명이 아니다
# ══════════════════════════════════════════════════════════


def test_보고서_공개기간_문구는_LINK_수명을_바꿔도_60일이다(
    admin: TestClient, monkeypatch
):
    """★ 두 값이 60으로 같던 시절에는 무엇을 넣어도 맞아 보였다.

    LINK 수명을 아주 다른 값으로 밀어 두 값이 겹칠 수 없게 만든 뒤 확인한다.
    """

    monkeypatch.setenv("SHARE_LINK_MAX_AGE_DAYS", "120")
    report_id = _보고서를_굽는다()
    key_hash = _아직_안_닫힌_링크(report_id)

    화면 = admin.get(f"/admin/links/{key_hash}")

    assert 화면.status_code == 200
    assert f"보고서 생성 후 {_보고서공개일}일까지" in 화면.text
    assert "보고서 생성 후 120일까지" not in 화면.text
    assert "보고서 생성 후 90일까지" not in 화면.text


# ══════════════════════════════════════════════════════════
# ③ 고유번호 경고가 상세 화면에도 뜬다
# ══════════════════════════════════════════════════════════

_경고문 = "이 보고서에는 회사 고유번호가 없어 같은 이름의 다른 회사와 구분하지 못합니다"


def test_고유번호_없는_결속은_상세화면에도_경고를_보인다(admin: TestClient):
    report_id = uuid.uuid4().hex
    report = replace(build_demo_report(), company_id="")
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "", report.job, report)
        conn.commit()
    key_hash = _아직_안_닫힌_링크(report_id)

    화면 = admin.get(f"/admin/links/{key_hash}")

    assert 화면.status_code == 200
    assert _경고문 in 화면.text


def test_고유번호가_있으면_상세화면도_조용하다(admin: TestClient):
    """★ 대조군 — 경고가 늘 뜨면 아무것도 알려 주지 않는다."""

    key_hash = _아직_안_닫힌_링크(_보고서를_굽는다())

    화면 = admin.get(f"/admin/links/{key_hash}")

    assert 화면.status_code == 200
    assert _경고문 not in 화면.text


def test_결속_보고서가_없으면_상세화면도_조용하다(admin: TestClient):
    key_hash = _아직_안_닫힌_링크()

    화면 = admin.get(f"/admin/links/{key_hash}")

    assert 화면.status_code == 200
    assert _경고문 not in 화면.text
