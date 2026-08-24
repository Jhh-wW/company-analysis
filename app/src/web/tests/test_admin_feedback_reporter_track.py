"""관리자 신고 화면에 «신고자 갈래»가 한국어로, 지문 없이 보이는지 못 박는 시험.

★ 무엇이 문제였나 — ``feedback.py``의 ``_reporter_key``가 신고자 식별자를
  ``"{track}:{SHA-256 지문}"`` 형태로 저장하도록 바뀌었지만(회원/링크 손님
  구분), 관리자 목록·상세 화면 어디에도 이 값을 꺼내 보여주는 코드가 없어
  DB에는 남아도 관리자가 볼 방법이 없었다(실측 결함).

★ 무엇을 지키는가
  - 갈래는 한국어 라벨로 보인다(원문 track.value 그대로 노출 금지).
  - 지문(SHA-256 해시) 부분은 화면 어디에도 안 보인다 — 캡처·공유로
    새어 나갈 값을 만들지 않는다.
  - 갈래 라벨 도입 이전의 접두 없는 옛 reporter_key(raw 64자리 해시)는
    예외 없이 «알 수 없음»으로 보인다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.feedback_report import constants as feedback_constants
from src.features.feedback_report import logic as feedback_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.storage import db as storage_db
from src.web import main, runtime
from src.web.routers import feedback as feedback_router

#: 시험에서 재사용하는, 형식만 갖춘 가짜 SHA-256 자리표시자.
_가짜지문_A = "a" * 64
_가짜지문_B = "b" * 64


@pytest.fixture
def client():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        yield client


def _login(client: TestClient, *, email: str = "admin@example.com") -> None:
    session = auth_logic.create_session(email, True)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)


def _seed(*, reporter_key: str, **overrides) -> str:
    values = dict(
        stage=feedback_constants.STAGE_REPORT,
        category=feedback_constants.CATEGORY_WRONG_INFO,
        body="표 숫자가 이상합니다",
        company_name="삼성전자",
        report_ref="",
        item_label="재무 지표",
        ref_url="",
        reporter_key=reporter_key,
        now_iso="2026-08-25T10:00:00+09:00",
    )
    values.update(overrides)
    with storage_db.connect() as conn:
        created = feedback_logic.create_report(conn, **values)
    return created.report_id


# ══════════════════════════════════════════════════════════
# ① 목록·상세에 한국어 갈래가 보이고, 지문은 안 보인다
# ══════════════════════════════════════════════════════════


def test_목록에_신고자_갈래가_한국어로_보이고_지문은_안보인다(client: TestClient):
    member_id = _seed(reporter_key=f"member:{_가짜지문_A}")
    link_id = _seed(
        reporter_key=f"link:{_가짜지문_B}",
        now_iso="2026-08-25T10:05:00+09:00",
    )
    _login(client)

    response = client.get("/admin/feedback-reports")

    assert response.status_code == 200
    assert "회원" in response.text
    assert "링크 손님" in response.text
    # ★ 원문 track.value("member"/"link")나 지문(해시)은 화면에 남으면 안 된다.
    assert _가짜지문_A not in response.text
    assert _가짜지문_B not in response.text
    assert member_id in response.text and link_id in response.text


def test_상세에_신고자_갈래가_한국어로_보이고_지문은_안보인다(client: TestClient):
    report_id = _seed(reporter_key=f"public:{_가짜지문_A}")
    _login(client)

    response = client.get(f"/admin/feedback-reports/{report_id}")

    assert response.status_code == 200
    assert "비회원" in response.text
    assert _가짜지문_A not in response.text


def test_관리자_갈래도_한국어로_보인다(client: TestClient):
    report_id = _seed(reporter_key=f"admin:{_가짜지문_A}")
    _login(client)

    response = client.get(f"/admin/feedback-reports/{report_id}")

    assert response.status_code == 200
    assert "관리자" in response.text


# ══════════════════════════════════════════════════════════
# ② 접두 없는 옛 기록 — 예외 없이 «알 수 없음»
# ══════════════════════════════════════════════════════════


def test_접두없는_옛_reporter_key는_목록과_상세_모두_알수없음으로_보인다(
    client: TestClient,
):
    """★ 갈래 라벨 도입 전(2026-08-25 이전) 저장된 실제 운영 데이터 모양 —
    접두 없는 raw 64자리 SHA-256뿐이다. 예외 없이 화면이 떠야 한다.
    """
    legacy_id = _seed(reporter_key=_가짜지문_A)
    _login(client)

    listing = client.get("/admin/feedback-reports")
    detail = client.get(f"/admin/feedback-reports/{legacy_id}")

    assert listing.status_code == 200
    assert detail.status_code == 200
    assert feedback_router.REPORTER_TRACK_UNKNOWN_LABEL in listing.text
    assert feedback_router.REPORTER_TRACK_UNKNOWN_LABEL in detail.text
    assert _가짜지문_A not in listing.text
    assert _가짜지문_A not in detail.text


def test_빈_reporter_key도_예외없이_알수없음으로_보인다(client: TestClient):
    report_id = _seed(reporter_key="")
    _login(client)

    response = client.get(f"/admin/feedback-reports/{report_id}")

    assert response.status_code == 200
    assert feedback_router.REPORTER_TRACK_UNKNOWN_LABEL in response.text


# ══════════════════════════════════════════════════════════
# ③ 단위 시험 — reporter_track_label() 자체의 경계
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "raw, expected",
    [
        (f"admin:{_가짜지문_A}", "관리자"),
        (f"member:{_가짜지문_A}", "회원"),
        (f"link:{_가짜지문_A}", "링크 손님"),
        (f"public:{_가짜지문_A}", "비회원"),
        (_가짜지문_A, feedback_router.REPORTER_TRACK_UNKNOWN_LABEL),  # 접두 없음
        ("", feedback_router.REPORTER_TRACK_UNKNOWN_LABEL),  # 빈 값
    ],
)
def test_reporter_track_label_경계값(raw: str, expected: str):
    assert feedback_router.reporter_track_label(raw) == expected


def test_reporter_track_label은_모르는_갈래도_원문라벨로_안전하게_보여준다():
    """★ 표에 없는 갈래가 와도(예: 이후 Track이 늘었는데 표를 못 따라간 경우)
    예외를 내지 않고, 원문 라벨을 그대로 보여준다 — 화면이 깨지지 않는다.
    """
    unknown_track_key = f"future-track:{_가짜지문_A}"

    label = feedback_router.reporter_track_label(unknown_track_key)

    assert label == "future-track"
    assert _가짜지문_A not in label
