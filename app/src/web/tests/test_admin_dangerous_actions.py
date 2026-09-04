"""관리자 위험 동작이 «확인 화면»을 거친 뒤에만 실행되는지 못 박는다.

★ 왜 필요한가 — 원칙은 「위험 동작은 두 단계」다. 지금은 초대 링크
  철회와 회원 빼기가 목록 행의 단추 하나로 즉시 실행된다.
  잘못 누르면 이미 뿌린 QR이 죽거나 친구의 로그인이 끊긴다. 되돌리는 길은 새로
  발급하고 다시 초대하는 것뿐이라 «누르기 전»에 대상을 다시 보여 줘야 한다.

★ 이 시험이 지키는 것
  ① 확인 화면(GET)이 대상 요약을 다시 보여 주고 1회용 확인 표를 발급한다.
  ② 확인 표 없이 온 POST는 거절되고(400 계열) **아무것도 실행되지 않는다**.
  ③ 거절도 감사 기록에 `denied`로 남는다 — 조용히 사라지지 않는다.
  ④ 같은 확인 표를 두 번 쓸 수 없다(뒤로 가기·새로고침 재실행 방지).
  ⑤ 한도를 바꾸는 화면은 전 값과 후 값을 함께 보여 주고 이유를 20자 이상 받는다.
  ⑥ 확인 화면 문구에 내부 용어가 없다.

★ 이 시험이 못 지키는 것 — 확인 표의 «만료»는 시간을 앞으로 돌려야 확인할 수
  있어 상수 검증으로만 본다. 확인 화면의 시각적 배치는 사람이 봐야 한다.
"""

from __future__ import annotations

import json
import re

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.web import main, runtime
from src.web.routers import admin as admin_router


_친구 = "friend@example.com"


@pytest.fixture
def client() -> TestClient:
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        yield client


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """관리자로 로그인한 손님. CSRF 표는 자동으로 붙인다."""

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


def _발급한_링크(admin: TestClient) -> str:
    """새 초대 링크를 만들고 관리용 식별자(해시)만 돌려준다."""

    created = admin.post(
        "/admin/links/new",
        data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )
    assert created.status_code == 200, created.status_code
    return created.headers["x-link-identifier"]


def _초대한_친구(admin: TestClient) -> str:
    invited = admin.post(
        "/admin/invite",
        data={"email": _친구, "display_name": "김민지", "note": "스터디"},
        follow_redirects=False,
    )
    assert invited.status_code == 303, invited.status_code
    return _친구


def _확인표(page_text: str) -> str:
    """확인 화면 폼에 심긴 1회용 확인 표를 뽑는다."""

    found = re.search(r'name="confirm_token"\s+value="([0-9a-f]+)"', page_text)
    assert found is not None, "확인 화면에 1회용 확인 표가 없습니다"
    return found.group(1)


def _감사사건(caplog) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for record in caplog.records:
        if record.name != "security.admin_audit":
            continue
        message = record.getMessage()
        assert message.startswith("admin_audit ")
        events.append(json.loads(message.removeprefix("admin_audit ")))
    return events


# ══════════════════════════════════════════════════════════
# ① 초대 링크 철회
# ══════════════════════════════════════════════════════════


def test_철회는_확인화면을_거쳐야_실행된다(admin: TestClient):
    key_hash = _발급한_링크(admin)

    확인화면 = admin.get(f"/admin/links/{key_hash}/revoke")

    assert 확인화면.status_code == 200
    # 대상을 다시 보여 준다 — 「무엇을 닫는지」 모른 채 누르지 않게.
    assert "카카오" in 확인화면.text
    assert "마케팅" in 확인화면.text
    # 이미 받은 보고서 주소는 계속 열린다는 사실을 확인 화면에서 말한다.
    assert "이미" in 확인화면.text and "보고서" in 확인화면.text

    실행 = admin.post(
        "/admin/links/revoke",
        data={"key": key_hash, "confirm_token": _확인표(확인화면.text)},
        follow_redirects=False,
    )

    assert 실행.status_code == 303
    with storage_db.connect() as conn:
        link = share_store.load_by_hash(conn, key_hash)
    assert link is not None and link.is_revoked


def test_확인없는_POST는_거절되고_감사에_denied가_남는다(
    admin: TestClient, caplog
):
    key_hash = _발급한_링크(admin)

    with caplog.at_level("INFO", logger="security.admin_audit"):
        거절 = admin.post(
            "/admin/links/revoke",
            data={"key": key_hash},
            follow_redirects=False,
        )

    assert 400 <= 거절.status_code < 500, 거절.status_code
    # ★ 실행이 0이어야 한다 — 「거절했다」고 말하면서 닫아 버리면 최악이다.
    with storage_db.connect() as conn:
        link = share_store.load_by_hash(conn, key_hash)
    assert link is not None and not link.is_revoked

    denied = [
        event
        for event in _감사사건(caplog)
        if event["action"] == "admin.link.revoke" and event["outcome"] == "denied"
    ]
    assert denied, "확인 없는 철회 요청이 감사에 denied로 남지 않았습니다"
    # 감사행 사유코드는 ASCII만 허용된다.
    assert denied[-1]["reason_code"].isascii()
    assert "confirm" in denied[-1]["reason_code"]


def test_확인_토큰은_한_번만_쓴다(admin: TestClient):
    첫_링크 = _발급한_링크(admin)
    확인화면 = admin.get(f"/admin/links/{첫_링크}/revoke")
    표 = _확인표(확인화면.text)

    첫_실행 = admin.post(
        "/admin/links/revoke",
        data={"key": 첫_링크, "confirm_token": 표},
        follow_redirects=False,
    )
    assert 첫_실행.status_code == 303

    다시 = admin.post(
        "/admin/links/revoke",
        data={"key": 첫_링크, "confirm_token": 표},
        follow_redirects=False,
    )

    assert 400 <= 다시.status_code < 500, 다시.status_code


def test_다른_관리자가_발급받은_확인_표로는_실행할_수_없다(
    admin: TestClient, caplog
):
    """확인 표는 «그 표를 받은 사람»에게만 듣는다.

    ★ 왜 필요한가 — 표가 사람에 묶이지 않으면, 한 관리자가 확인 화면을 열어 둔
      사이에 다른 관리자가 그 표로 대신 실행할 수 있다. 그러면 감사 기록의
      「누가 승인했나」가 실제로 화면을 본 사람과 어긋난다.
    ★ 뒤쪽 대조가 중요하다 — 앞의 400이 「이 사람은 원래 못 한다」가 아니라
      「이 표가 이 사람 것이 아니다」임을 보이려면, 같은 사람이 자기 표로는
      해낸다는 것을 같은 시험에서 함께 봐야 한다.
    """

    갑_링크 = _발급한_링크(admin)
    을_링크 = _발급한_링크(admin)
    갑_표 = _확인표(admin.get(f"/admin/links/{갑_링크}/revoke").text)

    # 여기서부터는 «다른 관리자»로 요청한다.
    다른_세션 = auth_logic.create_session("관리자@example.com", True)
    admin.cookies.set(auth_constants.SESSION_COOKIE_NAME, 다른_세션.token)
    다른_csrf = auth_logic.csrf_token_for_session(다른_세션.token)

    with caplog.at_level("INFO", logger="security.admin_audit"):
        남의_표로 = admin.post(
            "/admin/links/revoke",
            data={"key": 갑_링크, "confirm_token": 갑_표, "csrf_token": 다른_csrf},
            follow_redirects=False,
        )

    assert 400 <= 남의_표로.status_code < 500, 남의_표로.status_code
    with storage_db.connect() as conn:
        assert not share_store.load_by_hash(conn, 갑_링크).is_revoked
    denied = [
        event
        for event in _감사사건(caplog)
        if event["action"] == "admin.link.revoke" and event["outcome"] == "denied"
    ]
    assert denied, "남의 확인 표를 쓴 요청이 감사에 denied로 남지 않았습니다"

    # ★ 대조 — 같은 사람이 «자기가 받은» 표로는 실제로 중단할 수 있다.
    자기_표 = _확인표(admin.get(f"/admin/links/{을_링크}/revoke").text)
    자기_표로 = admin.post(
        "/admin/links/revoke",
        data={"key": 을_링크, "confirm_token": 자기_표, "csrf_token": 다른_csrf},
        follow_redirects=False,
    )

    assert 자기_표로.status_code == 303, 자기_표로.status_code
    with storage_db.connect() as conn:
        assert share_store.load_by_hash(conn, 을_링크).is_revoked


def test_다른_링크의_확인_토큰으로는_철회할_수_없다(admin: TestClient):
    갑 = _발급한_링크(admin)
    을 = _발급한_링크(admin)
    갑_표 = _확인표(admin.get(f"/admin/links/{갑}/revoke").text)

    잘못된_실행 = admin.post(
        "/admin/links/revoke",
        data={"key": 을, "confirm_token": 갑_표},
        follow_redirects=False,
    )

    assert 400 <= 잘못된_실행.status_code < 500, 잘못된_실행.status_code
    with storage_db.connect() as conn:
        assert not share_store.load_by_hash(conn, 을).is_revoked


# ══════════════════════════════════════════════════════════
# ② 회원 빼기
# ══════════════════════════════════════════════════════════


def test_회원_빼기도_확인화면을_거친다(admin: TestClient):
    email = _초대한_친구(admin)

    확인화면 = admin.get(f"/admin/members/{email}/remove")

    assert 확인화면.status_code == 200
    assert "김민지" in 확인화면.text
    assert email in 확인화면.text
    # 「지금 로그인 세션도 끊긴다」를 사람 말로 알린다.
    assert "로그인" in 확인화면.text

    확인없이 = admin.post(
        "/admin/revoke", data={"email": email}, follow_redirects=False
    )
    assert 400 <= 확인없이.status_code < 500, 확인없이.status_code
    with storage_db.connect() as conn:
        assert share_allow.is_allowed(conn, email), "확인 없이 빠지면 안 된다"

    실행 = admin.post(
        "/admin/revoke",
        data={"email": email, "confirm_token": _확인표(확인화면.text)},
        follow_redirects=False,
    )

    assert 실행.status_code == 303
    with storage_db.connect() as conn:
        assert not share_allow.is_allowed(conn, email)


# ══════════════════════════════════════════════════════════
# ③ 한도 변경 — 전→후 값과 이유
# ══════════════════════════════════════════════════════════


def test_한도_변경_확인화면은_전후_값과_이유를_보인다(admin: TestClient):
    email = _초대한_친구(admin)

    확인화면 = admin.get(
        f"/admin/members/{email}/limit",
        params={"daily_success_limit": "7", "daily_budget_krw": "9000"},
    )

    assert 확인화면.status_code == 200
    # 전 값(기본 3건·3,000원)과 후 값(7건·9,000원)이 함께 보여야 한다.
    assert "3건" in 확인화면.text and "7건" in 확인화면.text
    assert "3,000원" in 확인화면.text and "9,000원" in 확인화면.text
    assert 'name="reason"' in 확인화면.text
    _확인표(확인화면.text)


def test_이유가_짧으면_거절한다(admin: TestClient):
    email = _초대한_친구(admin)
    확인화면 = admin.get(
        f"/admin/members/{email}/limit",
        params={"daily_success_limit": "7", "daily_budget_krw": "9000"},
    )

    짧은_이유 = admin.post(
        f"/admin/members/{email}/limit",
        data={
            "daily_success_limit": "7",
            "daily_budget_krw": "9000",
            "reason": "그냥",
            "confirm_token": _확인표(확인화면.text),
        },
        follow_redirects=False,
    )

    assert 짧은_이유.status_code == 400, 짧은_이유.status_code
    assert str(admin_router.DANGEROUS_ACTION_REASON_MIN_CHARS) in 짧은_이유.text
    with storage_db.connect() as conn:
        saved = share_allow.load(conn, email)
    assert saved is not None and saved.daily_success_limit is None


def test_이유가_충분히_길면_한도가_바뀐다(admin: TestClient):
    email = _초대한_친구(admin)
    확인화면 = admin.get(
        f"/admin/members/{email}/limit",
        params={"daily_success_limit": "7", "daily_budget_krw": "9000"},
    )
    이유 = "이번 주 채용 설명회 준비로 조사 건수를 늘려 달라고 요청받았습니다."
    assert len(이유) >= admin_router.DANGEROUS_ACTION_REASON_MIN_CHARS

    바꿈 = admin.post(
        f"/admin/members/{email}/limit",
        data={
            "daily_success_limit": "7",
            "daily_budget_krw": "9000",
            "reason": 이유,
            "confirm_token": _확인표(확인화면.text),
        },
        follow_redirects=False,
    )

    assert 바꿈.status_code == 303, 바꿈.status_code
    with storage_db.connect() as conn:
        saved = share_allow.load(conn, email)
    assert saved is not None
    assert saved.daily_success_limit == 7
    assert saved.limit_reason == 이유


# ══════════════════════════════════════════════════════════
# ④ 만료 연장도 확인 표를 요구한다
# ══════════════════════════════════════════════════════════


def test_만료_연장도_확인_토큰_없이는_실행되지_않는다(admin: TestClient):
    key_hash = _발급한_링크(admin)
    with storage_db.connect() as conn:
        이전_만료일 = share_store.load_by_hash(conn, key_hash).expires_at
    확인화면 = admin.get(f"/admin/link/{key_hash}/extend")
    assert 확인화면.status_code == 200
    새_만료일 = re.search(r'name="expires_on"[^>]*max="(\d{4}-\d{2}-\d{2})"', 확인화면.text)
    assert 새_만료일 is not None, "연장 화면에 상한 날짜가 없습니다"
    assert 새_만료일.group(1) != 이전_만료일
    이유 = "포트폴리오 제출 기한이 미뤄져 링크를 더 열어 두기로 했습니다."

    확인없이 = admin.post(
        f"/admin/link/{key_hash}/extend",
        data={"expires_on": 새_만료일.group(1), "reason": 이유},
        follow_redirects=False,
    )

    assert 400 <= 확인없이.status_code < 500, 확인없이.status_code
    with storage_db.connect() as conn:
        link = share_store.load_by_hash(conn, key_hash)
    assert link is not None and link.expires_at == 이전_만료일

    # 확인 화면(연장 화면)이 준 표를 실으면 실제로 미뤄진다.
    실행 = admin.post(
        f"/admin/link/{key_hash}/extend",
        data={
            "expires_on": 새_만료일.group(1),
            "reason": 이유,
            "confirm_token": _확인표(확인화면.text),
        },
        follow_redirects=False,
    )

    assert 실행.status_code == 303, 실행.status_code
    with storage_db.connect() as conn:
        assert share_store.load_by_hash(conn, key_hash).expires_at == 새_만료일.group(1)


def test_연장_이유가_짧으면_거절한다(admin: TestClient):
    key_hash = _발급한_링크(admin)
    확인화면 = admin.get(f"/admin/link/{key_hash}/extend")
    새_만료일 = re.search(
        r'name="expires_on"[^>]*max="(\d{4}-\d{2}-\d{2})"', 확인화면.text
    )
    assert 새_만료일 is not None
    with storage_db.connect() as conn:
        이전_만료일 = share_store.load_by_hash(conn, key_hash).expires_at

    짧은_이유 = admin.post(
        f"/admin/link/{key_hash}/extend",
        data={
            "expires_on": 새_만료일.group(1),
            "reason": "그냥요",
            "confirm_token": _확인표(확인화면.text),
        },
        follow_redirects=False,
    )

    assert 짧은_이유.status_code == 400, 짧은_이유.status_code
    assert str(admin_router.DANGEROUS_ACTION_REASON_MIN_CHARS) in 짧은_이유.text
    with storage_db.connect() as conn:
        assert share_store.load_by_hash(conn, key_hash).expires_at == 이전_만료일
        assert share_store.list_link_adjustments(conn, key_hash=key_hash) == []


# ══════════════════════════════════════════════════════════
# ⑤ 화면 문구
# ══════════════════════════════════════════════════════════


#: 확인 화면에 나오면 안 되는 내부 용어. 관리자도 사람이라 「revoke」로는
#: 무엇이 사라지는지 모른다.
_내부_용어 = ("LINK", "MEMBER", "capability", "hash", "revoke", "key_hash", "token")


@pytest.mark.parametrize("어느_화면", ["링크_철회", "회원_빼기", "회원_한도"])
def test_확인_화면에_내부_용어가_없다(admin: TestClient, 어느_화면: str):
    if 어느_화면 == "링크_철회":
        화면 = admin.get(f"/admin/links/{_발급한_링크(admin)}/revoke")
    elif 어느_화면 == "회원_빼기":
        화면 = admin.get(f"/admin/members/{_초대한_친구(admin)}/remove")
    else:
        화면 = admin.get(
            f"/admin/members/{_초대한_친구(admin)}/limit",
            params={"daily_success_limit": "7", "daily_budget_krw": "9000"},
        )

    assert 화면.status_code == 200
    본문 = _사람이_읽는_글자(화면.text)
    for 용어 in _내부_용어:
        assert 용어 not in 본문, f"확인 화면에 내부 용어 {용어!r}가 보입니다"


def _사람이_읽는_글자(html: str) -> str:
    """사람 눈에 보이는 글자만 남긴다 — 폼 이름·주소는 화면 문구가 아니다."""

    from src.web.tests._visible_text import visible_text  # noqa: PLC0415

    return visible_text(html)
