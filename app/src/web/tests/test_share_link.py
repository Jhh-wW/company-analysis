"""회사별 «열쇠 링크»가 실제로 도는지 못 박는다 (문제로그 P-94).

★ 이 링크가 풀려는 문제 — 포트폴리오를 본 인사팀이 **로그인 없이** 도구를 눌러보게 하는 것.
  계정을 주면 로그인이 귀찮아 아무도 안 쓰고, 아무나 열어두면 돈이 무제한으로 샌다.

★ 그래서 링크가 하는 일 셋을 여기서 지킨다:
  ① 로그인 없이 들어와진다
  ② **누가 언제 열어봤는지** 기록된다 — 인사팀이 내 포폴을 봤는지 아는 유일한 방법이다
  ③ 미리 구운 보고서로 **바로** 간다 (0원·즉시, 예산과 무관)

⚠️ **아무 글자나 열쇠가 되면 안 된다** — 그러면 주소창에 타이핑해서
  «새 통장»을 무한히 만들 수 있고, 링크당 상한이 아무 의미가 없어진다.
"""

from __future__ import annotations

import datetime as dt

import pytest
from fastapi.testclient import TestClient

from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import (
    KEY_COOKIE_NAME,
    PER_LINK_DAILY_BUDGET_KRW,
    PUBLIC_BUCKET,
)
from src.features.storage import db as storage_db
from src.web import main

_카카오열쇠 = "a1b2c3d4e5f60718"
_네이버열쇠 = "0f1e2d3c4b5a6978"


@pytest.fixture
def client():
    """★ 반드시 `with` — 아니면 뒤에서 도는 조사가 취소된다 (P-92 교훈)."""
    main._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        yield client


def _링크발급(key: str, company: str, report_id: str = "") -> None:
    with storage_db.connect() as conn:
        share_store.save(
            conn, key=key, company=company, job="마케팅",
            report_id=report_id, now_iso="2026-08-16T10:00:00",
        )


def _보고서를_만든다(client: TestClient) -> str:
    form = {
        "company": "우리엔", "job": "영업", "region": "서울", "posting_text": "x",
        "legal_name": "우리엔", "ref": "재수집-p003", "address": "-",
    }
    run = client.post("/run", data=form, follow_redirects=False)
    job_id = run.headers["location"].rsplit("/", 1)[-1]
    for _ in range(200):
        if client.get(f"/api/progress/{job_id}").json()["finished"]:
            break
    return job_id


# ══════════════════════════════════════════════════════════
# ① 로그인 없이 들어와진다
# ══════════════════════════════════════════════════════════


def test_열쇠_링크로_들어오면_열쇠가_기억된다(client: TestClient):
    """★ 한 번 들어오면 주소를 안 달고 다녀도 같은 링크로 인정된다."""
    _링크발급(_카카오열쇠, "카카오")

    response = client.get(f"/k/{_카카오열쇠}", follow_redirects=False)

    assert response.status_code == 303
    assert KEY_COOKIE_NAME in response.cookies
    assert response.cookies[KEY_COOKIE_NAME] == _카카오열쇠


def test_로그인_없이도_첫_화면이_열린다(client: TestClient):
    _링크발급(_카카오열쇠, "카카오")
    client.get(f"/k/{_카카오열쇠}")

    assert client.get("/").status_code == 200


# ══════════════════════════════════════════════════════════
# ② 열어본 기록 — 인사팀이 봤는지 아는 «유일한» 방법
# ══════════════════════════════════════════════════════════


def test_열어보면_기록이_남는다(client: TestClient):
    """★ 이게 안 되면 「링크로 준다」는 방식을 고른 이유의 절반이 사라진다."""
    _링크발급(_카카오열쇠, "카카오")

    client.get(f"/k/{_카카오열쇠}")
    client.get(f"/k/{_카카오열쇠}")

    with storage_db.connect() as conn:
        link = share_store.load(conn, _카카오열쇠)
    assert link.opened_count == 2
    assert link.first_opened_at
    assert link.last_opened_at


def test_처음_열어본_시각은_안_덮인다(client: TestClient):
    """★ 나중 방문이 덮으면 「언제 처음 봤나」를 영영 못 알게 된다 — 그게 알고 싶은 값이다."""
    _링크발급(_카카오열쇠, "카카오")
    client.get(f"/k/{_카카오열쇠}")
    with storage_db.connect() as conn:
        처음 = share_store.load(conn, _카카오열쇠).first_opened_at

    client.get(f"/k/{_카카오열쇠}")

    with storage_db.connect() as conn:
        assert share_store.load(conn, _카카오열쇠).first_opened_at == 처음


# ══════════════════════════════════════════════════════════
# ③ 미리 구운 보고서로 «바로» 간다
# ══════════════════════════════════════════════════════════


def test_미리_구운_보고서로_바로_보낸다(client: TestClient):
    """★ 인사팀이 «자기 회사» 보고서를 곧바로 보는 것 — 이 방식의 핵심이다."""
    report_id = _보고서를_만든다(client)
    _링크발급(_카카오열쇠, "카카오", report_id=report_id)

    response = client.get(f"/k/{_카카오열쇠}", follow_redirects=False)

    assert response.headers["location"] == f"/result/{report_id}"


def test_안_구웠으면_첫_화면으로_보낸다(client: TestClient):
    """★ 아직 안 구운 링크도 «죽은 링크»가 되면 안 된다."""
    _링크발급(_카카오열쇠, "카카오", report_id="")

    response = client.get(f"/k/{_카카오열쇠}", follow_redirects=False)

    assert response.headers["location"] == "/"


# ══════════════════════════════════════════════════════════
# ④ 이상한 열쇠 — «새 통장»을 무한히 만들 수 없다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("열쇠", ["zzzz", "a" * 100, "%3Cscript%3E", "1234"])
def test_이상한_열쇠는_첫_화면으로_보낸다(client: TestClient, 열쇠: str):
    """★ 오류를 띄우지 않는다 — 인사팀 눈에 「안 되는 사이트」로 보이는 게 가장 나쁘다.

    ⚠️ `../../etc` 같은 «경로 장난»은 여기서 시험하지 않는다 —
      주소가 이 함수에 닿기 «전에» 정규화돼서 아예 다른 경로가 된다.
      막히긴 하지만 «이 코드가» 막는 게 아니라서, 여기서 시험하면
      실제로는 안 도는 방어를 「된다」고 착각하게 된다.
      열쇠 «모양» 검사 자체는 `sharelink/tests/test_logic.py`가 본다.
    """
    response = client.get(f"/k/{열쇠}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert KEY_COOKIE_NAME not in response.cookies


def test_없는_열쇠도_첫_화면으로_보낸다(client: TestClient):
    response = client.get(f"/k/{_네이버열쇠}", follow_redirects=False)

    assert response.headers["location"] == "/"
    assert KEY_COOKIE_NAME not in response.cookies


def test_이상한_열쇠는_공용_통장으로_묶인다():
    """★ 아무 글자나 새 통장이 되면 링크당 상한이 무의미해진다."""
    assert not share_logic.is_valid_key("아무글자")


# ══════════════════════════════════════════════════════════
# ⑤ 링크마다 예산이 «따로» 센다
# ══════════════════════════════════════════════════════════


def test_한_링크가_다_써도_다른_링크는_돈다(client: TestClient, monkeypatch):
    """★ P-94의 핵심 — 「전체 하나」가 아니라 「링크당」을 고른 이유다."""
    _링크발급(_카카오열쇠, "카카오")
    _링크발급(_네이버열쇠, "네이버")
    오늘 = dt.date.today()
    monkeypatch.setattr(main, "_PIPELINE", object())          # 돈이 드는 것으로 본다
    monkeypatch.setattr(
        main, "_LINK_SPEND",
        share_logic.add_spend(
            share_logic.DailySpend(day=오늘), _카카오열쇠, 오늘, PER_LINK_DAILY_BUDGET_KRW
        ),
    )
    form = {
        "company": "우리엔", "job": "영업", "region": "서울", "posting_text": "x",
        "legal_name": "우리엔", "ref": "재수집-p003", "address": "-",
    }

    client.cookies.set(KEY_COOKIE_NAME, _카카오열쇠)
    막힘 = client.post("/run", data=form, follow_redirects=False)
    client.cookies.set(KEY_COOKIE_NAME, _네이버열쇠)
    통과 = client.post("/run", data=form, follow_redirects=False)

    assert 막힘.status_code == 429, "다 쓴 링크는 막혀야 한다"
    assert 통과.status_code == 303, "★ 다른 링크는 멀쩡히 돌아야 한다"


def test_열쇠_없는_손님도_상한을_받는다(client: TestClient, monkeypatch):
    """★ 안 걸면 「열쇠 없이 들어오는 길」이 상한 없는 구멍이 된다."""
    오늘 = dt.date.today()
    monkeypatch.setattr(main, "_PIPELINE", object())
    monkeypatch.setattr(
        main, "_LINK_SPEND",
        share_logic.add_spend(
            share_logic.DailySpend(day=오늘), PUBLIC_BUCKET, 오늘, PER_LINK_DAILY_BUDGET_KRW
        ),
    )
    form = {
        "company": "우리엔", "job": "영업", "region": "서울", "posting_text": "x",
        "legal_name": "우리엔", "ref": "재수집-p003", "address": "-",
    }

    assert client.post("/run", data=form, follow_redirects=False).status_code == 429


# ══════════════════════════════════════════════════════════
# ⑥ ★★ 로그인만으로는 «아무 권한도» 안 준다 (P-95)
# ══════════════════════════════════════════════════════════
# 사용자가 직접 지적해 잡힌 구멍이다 (2026-08-16):
#   「링크로 들어와서 그냥 구글로 로그인하면 어떻게 되나?」
# 그때는 **아무나 로그인만 하면 하루 1,000원**을 쓸 수 있었다.


def _로그인시킨다(client: TestClient, email: str, *, is_admin: bool = False) -> None:
    """이 손님을 «로그인한 상태»로 만든다 (초대 여부는 별개다)."""
    from src.features.auth import logic as auth_logic
    from src.features.auth import constants as auth_constants

    session = auth_logic.create_session(email, is_admin)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)


def _초대한다(email: str) -> None:
    with storage_db.connect() as conn:
        share_allow.invite(
            conn, email=email, note="시험", now_iso="2026-08-16T10:00:00"
        )


def test_로그인만_하고_초대_안_됐으면_진짜_조사를_못_한다(
    client: TestClient, monkeypatch
):
    """★ P-95 그 자체 — 인터넷의 아무나 로그인해서 돈 쓰는 것을 막는다."""
    monkeypatch.setattr(main, "_PIPELINE", object())          # 돈이 드는 것으로 본다
    _로그인시킨다(client, "stranger@gmail.com")
    form = {
        "company": "우리엔", "job": "영업", "region": "서울", "posting_text": "x",
        "legal_name": "우리엔", "ref": "재수집-p003", "address": "-",
    }

    response = client.post("/run", data=form, follow_redirects=False)

    assert response.status_code == 429
    assert "초대 링크로 들어오신 분만" in response.text


def test_초대한_친구는_진짜_조사를_할_수_있다(client: TestClient, monkeypatch):
    """★ 반대 방향 — 다 막아버리면 친구들이 못 쓴다."""
    monkeypatch.setattr(main, "_PIPELINE", DemoPipeline())
    _초대한다("friend@gmail.com")
    _로그인시킨다(client, "friend@gmail.com")
    form = {
        "company": "우리엔", "job": "영업", "region": "서울", "posting_text": "x",
        "legal_name": "우리엔", "ref": "재수집-p003", "address": "-",
    }

    assert client.post("/run", data=form, follow_redirects=False).status_code == 303


def test_링크로_들어와_로그인해도_링크_몫만_쓴다(client: TestClient, monkeypatch):
    """★ 사용자가 물어본 바로 그 상황.

    인사팀이 열쇠 링크로 들어와 호기심에 구글 로그인을 눌러도,
    **그 회사에 배정된 몫**을 쓴다. 로그인했다고 통장이 하나 더 생기지 않는다.
    """
    _링크발급(_카카오열쇠, "카카오")
    오늘 = dt.date.today()
    monkeypatch.setattr(main, "_PIPELINE", object())
    monkeypatch.setattr(
        main, "_LINK_SPEND",
        share_logic.add_spend(
            share_logic.DailySpend(day=오늘), _카카오열쇠, 오늘, PER_LINK_DAILY_BUDGET_KRW
        ),
    )
    client.cookies.set(KEY_COOKIE_NAME, _카카오열쇠)
    _로그인시킨다(client, "hr@kakao.com")            # 초대 명단에는 없다
    form = {
        "company": "우리엔", "job": "영업", "region": "서울", "posting_text": "x",
        "legal_name": "우리엔", "ref": "재수집-p003", "address": "-",
    }

    response = client.post("/run", data=form, follow_redirects=False)

    assert response.status_code == 429, "로그인으로 «몫이 늘면» 안 된다"


def test_명단에서_빼면_바로_막힌다(client: TestClient, monkeypatch):
    """★ 되돌릴 방법이 있어야 한다 — 다 썼거나 계정이 넘어갔을 때."""
    monkeypatch.setattr(main, "_PIPELINE", object())
    _초대한다("friend@gmail.com")
    with storage_db.connect() as conn:
        share_allow.revoke(conn, "friend@gmail.com")
    _로그인시킨다(client, "friend@gmail.com")
    form = {
        "company": "우리엔", "job": "영업", "region": "서울", "posting_text": "x",
        "legal_name": "우리엔", "ref": "재수집-p003", "address": "-",
    }

    assert client.post("/run", data=form, follow_redirects=False).status_code == 429


def test_모르는_손님도_데모_화면은_그대로_본다(client: TestClient):
    """★ 「진짜 조사만」 막는 것이다 — 도구가 어떤 건지는 다 보여준다."""
    assert client.get("/").status_code == 200
