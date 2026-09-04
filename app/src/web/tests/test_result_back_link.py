"""초대 링크 손님이 결과 화면에서 «돌아갈 길»과 «언제 자료인지»를 본다.

★ 무엇이 문제였나 — `/k/`의 도착지를 랜딩으로 바꾼 뒤, 손님이 보고서를
  열고 나면 랜딩으로 돌아갈 길이 화면에 없다. 주소창을 지우고 `/`를 직접 치는
  방법은 인사팀이 쓰는 방법이 아니다. 그래서 결과 화면 «위»에 길을 하나 둔다.

  ① 손님이 직접 돌린 보고서 → 「보고서로 돌아가기」(랜딩으로)
  ② 링크에 원래 묶여 있던 보고서(결속 보고서) → 「다른 회사 분석해 보기」
     ★ 결속 보고서에서 「돌아가기」는 방금 온 곳으로 되돌아가는 셈이라
       손님이 같은 자리를 맴돈다. 그래서 여기서만 «앞으로 가는 길»을 준다.

★ 그리고 결속 보고서 위에는 「{날짜}에 만든 보고서」 한 줄을 둔다. 안 알리면
  인사팀이 «옛 숫자»를 오늘 숫자로 읽는다.
  신선도 기한이 지났으면 날짜 대신 「오래된 보고서입니다」라고 말한다 —
  자동으로 다시 조사하지 않는다. 사람 승인 없이 돈을 쓰는 일이기 때문이다.

⚠️ 이 화면의 독자는 개발자가 아니라 인사팀이다. 내부 용어(LINK·capability·
  통장·원장 …)와 만든 쪽 사정(「우리」·「내 회사」)은 한 글자도 나오면 안 된다.
⚠️ 초대 링크 손님 «만» 바뀐다. PUBLIC·MEMBER·ADMIN의 결과 화면은 표지 위
  장식까지 바이트 그대로여야 한다 — 아래 ④가 수정 «전» 코드로 찍은 골든과
  대조한다.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import pathlib
import re
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.demo import DemoPipeline
from src.features.report_access import constants as report_access_constants
from src.features.report_access import store as report_access_store
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import main, runtime
from src.web.tests import report_route_support
from src.web.tests._visible_text import visible_text

_열쇠 = "c3d4e5f6a7b8c9d0c3d4e5f6a7b8c9d0"

#: ★ 화면에 나와야 하는 글자를 «리터럴»로 적는다. 생산 상수를 import해 비교하면
#:   문구가 몰래 바뀌어도 시험이 통과한다(W2).
_돌아가기 = "보고서로 돌아가기"
_다른회사 = "다른 회사 분석해 보기"
_오래된 = "오래된 보고서입니다 — 준비 중인 새 보고서를 기다려 주세요"

#: 결속 보고서 신선도 기한(일). ★ 제품 상수(`REPORT_LINK_MAX_AGE_DAYS`)를
#:   import하지 않는다 — 그 값이 낮아지면 아래 «59일은 신선하다»가 빨개져야 한다.
_신선도기한일 = 60


@pytest.fixture
def client():
    """★ 반드시 `with` — 아니면 뒤에서 도는 조사가 취소된다 (교훈)."""
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app, base_url="https://testserver") as client:
        yield client


# ══════════════════════════════════════════════════════════
# 준비 도우미
# ══════════════════════════════════════════════════════════


def _보고서를_저장한다(company: str = "하이브", *, generated_at: str = "") -> str:
    """보고서 한 건을 저장소에 바로 넣는다 (파이프라인 0회·0원)."""
    report_id = uuid.uuid4().hex
    report = dataclasses.replace(build_demo_report(), company=company)
    if generated_at:
        report = dataclasses.replace(report, generated_at=generated_at)
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "demo-corp", report.job, report)
    return report_id


def _링크발급(company: str, *, report_id: str = "") -> None:
    with storage_db.connect() as conn:
        share_store.insert_new(
            conn,
            key=_열쇠,
            company=company,
            job="",
            report_id=report_id,
            now_iso="2026-08-16T10:00:00",
        )


def _손님으로_연다(client: TestClient, report_id: str) -> str:
    """초대 링크 쿠키를 든 손님이 그 결과 주소를 연다."""
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)
    응답 = client.get(f"/result/{report_id}")
    assert 응답.status_code == 200, 응답.status_code
    return 응답.text


def _직접_돌린_보고서를_연다(client: TestClient, report_id: str) -> str:
    """손님이 «직접 돌린» 보고서는 제품의 PUBLIC 발급 API로 결속한다."""
    with storage_db.connect() as conn:
        발급 = report_access_store.issue_and_bind(
            conn, existing_token=None, run_id=report_id
        )
    client.cookies.set(report_access_constants.PUBLIC_GRANT_COOKIE_NAME, 발급.token)
    return _손님으로_연다(client, report_id)


def _결속보고서를_저장본으로_연다(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, *, 며칠전: int
) -> str:
    """생성일이 «며칠전»인 결속 보고서를 불변 저장본 갈래로 연다.

    ★ 왜 저장본 갈래인가 — legacy 갈래는 `_link_expired`가 참이면 라우트가
      결과를 아예 안 열고 「기간이 지난 링크」 화면(410)을 준다(실측). 저장본
      갈래의 만료는 그 delivery 자신의 `expires_at`이 정하므로, 링크 수명이
      보고서 신선도보다 긴 실제 상황(링크 수명 90일)에서는
      «열리지만 오래된» 보고서가 존재한다. 이 시험이 보는 것이 그 상태다.
    ★ 바꿔치는 것은 «저장소에서 읽어오는 부분»뿐이다. 신선도 판정
      (`job_runtime._link_expired`)은 제품 코드가 그대로 돈다.
    """
    from src.web.routers import reports as reports_router

    생성일 = clock.today_kst() - dt.timedelta(days=며칠전)
    report_id = _보고서를_저장한다("하이브", generated_at=생성일.isoformat())
    _링크발급("하이브", report_id=report_id)
    with storage_db.connect() as conn:
        report = report_store.load(conn, report_id)
    저장본 = reports_router._StoredPublicDelivery(
        delivery=SimpleNamespace(
            expires_at=dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)
        ),
        report=report,
        pdf_bytes=b"%PDF-1.4\n% gs7 fixture\n",
        pdf_sha256="a" * 64,
        artifact_id="artifact_gs7_fixture",
        release_record_sha256="b" * 64,
    )
    monkeypatch.setattr(
        reports_router,
        "_stored_public_delivery",
        lambda public_id: 저장본 if public_id == report_id else None,
    )
    return _손님으로_연다(client, report_id)


# ══════════════════════════════════════════════════════════
# ① 돌아갈 길 — 결속 보고서인지에 따라 «다른» 버튼
# ══════════════════════════════════════════════════════════


def test_LINK결속_보고서가_아닌_결과에서만_돌아가기_버튼이_보인다(
    client: TestClient,
):
    """★ 결속 보고서에서 「돌아가기」는 방금 온 곳으로 되돌아가는 셈이다."""
    결속 = _보고서를_저장한다("하이브")
    _링크발급("하이브", report_id=결속)
    직접 = _보고서를_저장한다("카카오")

    직접화면 = _직접_돌린_보고서를_연다(client, 직접)
    결속화면 = _손님으로_연다(client, 결속)

    직접본문 = visible_text(직접화면)
    결속본문 = visible_text(결속화면)

    # 직접 돌린 보고서 → 랜딩으로 돌아가는 길
    assert _돌아가기 in 직접본문
    assert _다른회사 not in 직접본문
    assert f'href="/">{_돌아가기}' in 직접화면.replace("\n", "")

    # 결속 보고서 → 같은 자리를 맴돌지 않도록 «앞으로» 가는 길
    assert _다른회사 in 결속본문
    assert _돌아가기 not in 결속본문
    assert 'href="/#analysisForm"' in 결속화면


def test_결속보고서가_아닌_결과에는_생성일_띠가_없다(client: TestClient):
    """★ 띠는 «링크에 묶여 있던» 보고서의 신선도를 말한다.

    손님이 방금 직접 돌린 보고서에까지 붙이면, 오늘 만든 것을 두고
    「언제 만든 것인지 확인하라」고 말하는 셈이라 군더더기다.
    """
    _링크발급("하이브", report_id=_보고서를_저장한다("하이브"))
    직접 = _보고서를_저장한다("카카오")

    본문 = visible_text(_직접_돌린_보고서를_연다(client, 직접))

    assert "에 만든 보고서" not in 본문
    assert _오래된 not in 본문


# ══════════════════════════════════════════════════════════
# ② 신선도 띠 — 「언제 자료인지」
# ══════════════════════════════════════════════════════════


def test_결속보고서_상단에_생성일_띠가_있다(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
):
    """★ 안 알리면 인사팀이 옛 숫자를 오늘 숫자로 읽는다."""
    오늘 = clock.today_kst()

    화면 = _결속보고서를_저장본으로_연다(monkeypatch, client, 며칠전=0)

    기대 = f"{오늘.year}년 {오늘.month}월 {오늘.day}일에 만든 보고서"
    assert 기대 in visible_text(화면)


@pytest.mark.parametrize(
    ("며칠전", "오래됐나"),
    [
        (_신선도기한일 - 1, False),
        (_신선도기한일 + 1, True),
    ],
)
def test_만료된_결속보고서는_오래된_보고서_띠를_보인다(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, 며칠전: int, 오래됐나: bool
):
    """★ 60일 경계 «양쪽»을 함께 본다 — 한쪽만 보면 기한이 몰래 짧아져도 통과한다.

    ★ 기한이 지나도 «자동으로 다시 조사하지 않는다».
      화면은 기다려 달라고만 말한다.
    """
    화면 = _결속보고서를_저장본으로_연다(monkeypatch, client, 며칠전=며칠전)
    본문 = visible_text(화면)

    if 오래됐나:
        assert _오래된 in 본문
        assert "에 만든 보고서" not in 본문
    else:
        assert _오래된 not in 본문
        assert "에 만든 보고서" in 본문


def test_만료된_결속보고서를_옛저장본으로_열면_기존_만료화면_그대로다(
    client: TestClient,
):
    """★ 신선도 띠를 넣는다고 기존 만료 차단이 느슨해지면 안 된다.

    저장본(delivery)이 없는 옛 보고서는 라우트가 결과를 열기 «전에»
    「기간이 지난 링크」 화면으로 막는다. 그 경계는 그대로다.
    """
    생성일 = clock.today_kst() - dt.timedelta(days=_신선도기한일 + 1)
    결속 = _보고서를_저장한다("하이브", generated_at=생성일.isoformat())
    _링크발급("하이브", report_id=결속)
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)

    응답 = client.get(f"/result/{결속}")

    assert 응답.status_code == 410
    assert "기간이 지난 링크입니다" in visible_text(응답.text)


# ══════════════════════════════════════════════════════════
# ③ 인사팀이 읽는 글자 — 내부 용어·「우리」 금지
# ══════════════════════════════════════════════════════════

#: 사람이 읽는 글자에 절대 나오면 안 되는 내부 용어.
_소문자_내부용어 = ("capability", "bucket", "track")
#: 대문자 갈래 이름은 HTML 속성·주석에도 없어야 한다.
_대문자_내부용어 = ("LINK", "MEMBER", "PUBLIC", "ADMIN")


@pytest.mark.parametrize("며칠전", [0, _신선도기한일 + 1])
def test_결과화면_띠와_버튼은_내부용어를_쓰지않는다(
    monkeypatch: pytest.MonkeyPatch, client: TestClient, 며칠전: int
):
    """★ 「우리」는 만든 쪽을 가리켜 받는 사람을 헷갈리게 한다.

    신선한 띠와 「오래된 보고서」 띠를 «따로» 연다 — 한 시험에서 링크를 두 번
    발급하면 같은 열쇠가 이미 있어 두 번째 보고서가 결속되지 않는다(실측 404).
    """
    화면 = _결속보고서를_저장본으로_연다(monkeypatch, client, 며칠전=며칠전)

    본문 = visible_text(화면)
    낮춘본문 = 본문.lower()
    for 용어 in _소문자_내부용어:
        assert 용어 not in 낮춘본문, 용어
    for 용어 in _대문자_내부용어:
        assert 용어 not in 화면, 용어
    assert "우리" not in 본문
    assert "예산" not in 본문
    assert "원장" not in 본문
    # 띠가 실제로 그려진 화면에서만 이 검사가 뜻이 있다.
    assert ("에 만든 보고서" in 본문) or (_오래된 in 본문)


# ══════════════════════════════════════════════════════════
# ④ 다른 손님의 결과 화면은 한 바이트도 안 바뀐다
# ══════════════════════════════════════════════════════════

#: ★ 폴더 이름에 ``data``를 쓰지 않는다 — `app/.gitignore`가 `data/`를 통째로
#:   무시해서 골든 파일이 커밋되지 않는다 (실측).
_스냅샷_폴더 = pathlib.Path(__file__).parent / "result_page_snapshots"

#: 골든을 찍을 때 쓴 고정 보고서 번호. 번호가 화면에 그대로 박히므로 고정한다.
_골든_보고서번호 = "31" * 16

#: 표지 «위»의 화면 장식이 끝나는 자리. 이 위가 이 시험 묶음이 건드리는 영역이다.
_표지시작 = '<article class="report-paper">'


def _정규화(본문: str) -> str:
    """요청마다 달라지는 CSRF 값만 지운다 — 그 밖은 바이트 그대로 본다."""
    return re.sub(
        r'name="csrf_token" value="[^"]*"',
        'name="csrf_token" value="<csrf>"',
        본문,
    )


def _머리(본문: str) -> str:
    """표지 위 장식 영역만 바이트 그대로 자른다."""
    assert _표지시작 in 본문, "결과 화면에 표지(report-paper)가 없다"
    return 본문[: 본문.index(_표지시작) + len(_표지시작)]


def _글자줄만(본문: str) -> tuple[str, ...]:
    """사람이 읽는 «글자 줄»만 남긴다 (빈 줄 제외)."""
    return tuple(
        줄.strip() for 줄 in visible_text(본문).splitlines() if 줄.strip()
    )


def _골든(이름: str, 확장: str) -> str:
    """이 수정 «전»(b15bb06) 코드로 찍어 둔 결과 화면.

    ★ 다시 찍는 법 — 결과 화면을 일부러 바꾸는 변경에서만, 그 커밋의 코드로
      `_머리(_정규화(html))`와 `_글자줄만(...)`을 그대로 덮어쓴다.
    """
    return (_스냅샷_폴더 / f"{이름}.{확장}").read_text(encoding="utf-8")


def _로그인(client: TestClient, email: str, *, is_admin: bool) -> None:
    session = auth_logic.create_session(email, is_admin)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)


def _골든_화면(monkeypatch: pytest.MonkeyPatch, client: TestClient) -> None:
    """골든을 찍을 때와 «같은» 보고서·같은 저장 갈래를 세운다."""
    report = build_demo_report()
    with storage_db.connect() as conn:
        report_store.save(conn, _골든_보고서번호, "demo-corp", report.job, report)
        발급 = report_access_store.issue_and_bind(
            conn, existing_token=None, run_id=_골든_보고서번호
        )
    client.cookies.set(report_access_constants.PUBLIC_GRANT_COOKIE_NAME, 발급.token)
    report_route_support.serve_legacy_report_snapshot(
        monkeypatch, report, report_id=_골든_보고서번호
    )


def _본다(client: TestClient) -> str:
    응답 = client.get(f"/result/{_골든_보고서번호}")
    assert 응답.status_code == 200, 응답.status_code
    return _정규화(응답.text)


def test_PUBLIC_MEMBER_ADMIN_결과화면은_바뀌지_않는다(
    monkeypatch: pytest.MonkeyPatch, client: TestClient
):
    """★ 초대 링크 손님만 바꾼다. 나머지 세 손님의 결과 화면은 그대로다.

    표지 위 장식은 «바이트»로, 화면 전체는 «읽는 글자»로 대조한다. 새 띠나
    버튼이 다른 손님에게 한 줄이라도 새면 여기서 걸린다.
    """
    _골든_화면(monkeypatch, client)
    공개 = _본다(client)

    with storage_db.connect() as conn:
        share_allow.invite(
            conn,
            email="friend@example.com",
            note="시험",
            now_iso="2026-08-16T10:00:00",
        )
        conn.commit()
    _로그인(client, "friend@example.com", is_admin=False)
    회원 = _본다(client)

    _로그인(client, "admin@example.com", is_admin=True)
    관리자 = _본다(client)

    for 이름, 화면 in (("public", 공개), ("member", 회원), ("admin", 관리자)):
        assert _머리(화면) == _골든(이름, "head.html"), 이름
        assert _글자줄만(화면) == tuple(
            _골든(이름, "text.txt").splitlines()
        ), 이름
        # 화면 틀 구조 자체도 못 박는다 — 조건 블록이 빈 껍데기를 남기지 않는다.
        assert 화면.count(_표지시작) == 1, 이름
        assert "link-result" not in 화면, 이름
        assert _돌아가기 not in 화면, 이름
        assert _다른회사 not in 화면, 이름


# ══════════════════════════════════════════════════════════
# ⑤ 랜딩 버튼 모양 — 카드 B 구분과 손가락 크기
# ══════════════════════════════════════════════════════════

_스타일 = pathlib.Path(__file__).parents[1] / "static" / "style.css"

#: 화면 규정 「버튼 A/B … 높이 44px 이상」. ★ 폰 엄지로 누르는 버튼이다.
_최소높이 = "min-height: 44px;"

#: 카드 B 버튼을 고르는 선택자. GS6가 남긴 화면 틀에는 카드 B만 가리키는
#: class가 없어(실측), «두 번째 선택지 묶음»이라는 순서로 고른다.
_카드B선택자 = (
    ".home-page .link-landing .link-landing-choice + .link-landing-choice .btn"
)


def test_랜딩_버튼_CSS_규칙이_있고_44px이다():
    """★ 화면 틀에 class만 적고 규칙이 없으면 화면에서는 아무 일도 안 일어난다."""
    css = _스타일.read_text(encoding="utf-8")

    # (a) 카드 B가 카드 A와 다르게 보인다.
    assert _카드B선택자 in css
    # (b) 첫 화면(=랜딩이 얹히는 화면) 버튼이 손가락 크기다.
    앞 = css.index(".home-page .btn {")
    뒤 = css.index("}", 앞)
    assert _최소높이 in css[앞:뒤], css[앞:뒤]


def test_카드B_규칙은_첫화면_기본_버튼보다_뒤에_있다():
    """★ 같은 자리를 다투는 규칙은 «뒤»에 있어야 이긴다.

    `.home-page .btn`이 뒤에 오면 카드 B 규칙이 통째로 묻혀 화면이 안 바뀐다.
    """
    css = _스타일.read_text(encoding="utf-8")

    assert css.index(_카드B선택자) > css.index(".home-page .btn {")
