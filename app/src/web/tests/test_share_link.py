"""회사별 «열쇠 링크»가 실제로 도는지 못 박는다 (문제로그 P-94).

★ 이 링크가 풀려는 문제 — 포트폴리오를 본 인사팀이 **로그인 없이** 도구를 눌러보게 하는 것.
  계정을 주면 로그인이 귀찮아 아무도 안 쓰고, 아무나 열어두면 돈이 무제한으로 샌다.

★ 그래서 링크가 하는 일 셋을 여기서 지킨다:
  ① 로그인 없이 들어와진다
  ② 링크 GET 요청 횟수와 최초·최근 시각이 기록된다 — 미리보기·봇도 포함될 수 있다
  ③ 미리 구운 보고서로 **바로** 간다 (0원·즉시, 예산과 무관)

⚠️ **아무 글자나 열쇠가 되면 안 된다** — 그러면 주소창에 타이핑해서
  «새 통장»을 무한히 만들 수 있고, 링크당 상한이 아무 의미가 없어진다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import inspect
import re
import time
import uuid
from html.parser import HTMLParser

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

from src.core import clock
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget import spend_store
from src.features.pipeline.canonical_demo import (
    DEMO_COMPANY as CANONICAL_DEMO_COMPANY,
    build_demo_report,
)
from src.features.pipeline.demo import DemoPipeline
from src.features.pipeline.port import CompanyCard, Outcome
from src.features.report_standard import CANONICAL_SECTION_IDS
from src.features.sharelink import access_control as share_access
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink import tracks as share_tracks
from src.features.sharelink.constants import (
    ACCESS_PER_CAPABILITY_LIMIT,
    KEY_COOKIE_NAME,
    PER_LINK_DAILY_BUDGET_KRW,
    PUBLIC_BUCKET,
    PUBLIC_DAILY_BUDGET_KRW,
)
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.shared import engine_build_identity as build_identity_contract
from src.web import deployment_mode, job_runtime, main
from src.web import paid_runtime, request_helpers, runtime
from src.web.routers import analysis as analysis_router
from src.web.routers import reports as reports_router

_REAL_RELEASE_STATE = reports_router._release_state

_카카오열쇠 = "a1b2c3d4e5f60718a1b2c3d4e5f60718"
_네이버열쇠 = "0f1e2d3c4b5a69780f1e2d3c4b5a6978"
_구형열쇠 = "a1b2c3d4e5f60718"


class _MainCounter(HTMLParser):
    """실제 렌더 HTML의 main landmark 수를 세는 작은 표준 parser."""

    def __init__(self) -> None:
        super().__init__()
        self.count = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag == "main":
            self.count += 1


def _main_count(html: str) -> int:
    parser = _MainCounter()
    parser.feed(html)
    return parser.count


@pytest.fixture
def client():
    """★ 반드시 `with` — 아니면 뒤에서 도는 조사가 취소된다 (P-92 교훈)."""
    runtime._PIPELINE = DemoPipeline()
    # 공유 쿠키는 배포 기본값대로 Secure다. HTTPS에서 실제 브라우저처럼 왕복시킨다.
    with TestClient(main.app, base_url="https://testserver") as client:
        yield client


def _링크발급(
    key: str,
    company: str,
    report_id: str = "",
    now_iso: str = "2026-08-16T10:00:00",
) -> None:
    with storage_db.connect() as conn:
        share_store.insert_new(
            conn, key=key, company=company, job="마케팅",
            report_id=report_id, now_iso=now_iso,
        )


def test_시험공개에서도_살아있는_링크는_자동출고본문과PDF만열고_관리자는_잠근다(
    client: TestClient, monkeypatch
):
    """★ 기대값 이전(G-S6·D-G10) — `/k/`는 이제 결과가 아니라 첫 화면으로 보낸다.

    이 시험이 지키는 것은 「살아 있는 링크가 본문과 PDF를 열고 관리 화면은
    잠근다」이지 도착지가 아니다. 도착지는 랜딩으로 바뀌었으므로 결과 화면은
    보고서 주소로 직접 연다. 랜딩 자체는 `test_link_landing.py`가 본다.
    """
    report_id = uuid.uuid4().hex
    report = build_demo_report()
    with storage_db.connect() as conn:
        report_store.save(
            conn, report_id, "demo-corp", report.job, report
        )
    reports_router.finalize_new_report_delivery(
        report_id=report_id,
        corp_id="demo-corp",
        billing_bucket_id="link-test",
        report=report,
        actual_models=("deterministic-demo",),
        reused_from_cache=False,
        engine_build_identity=build_identity_contract.process_engine_build_identity(),
    )
    _링크발급(_카카오열쇠, report.company, report_id=report_id)
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    monkeypatch.setattr(reports_router, "_release_state", _REAL_RELEASE_STATE)

    opened = client.get(f"/k/{_카카오열쇠}", follow_redirects=False)
    with storage_db.connect() as conn:
        assert share_store.list_report_view_events_by_hash(
            conn, share_store.key_hash_of(_카카오열쇠)
        ) == []
    result = client.get(f"/result/{report_id}", follow_redirects=False)
    refreshed = client.get(f"/result/{report_id}", follow_redirects=False)
    pdf = client.get(f"/download/pdf/{report_id}", follow_redirects=False)
    admin = client.get("/admin", follow_redirects=False)

    assert opened.status_code == 303
    assert opened.headers["location"] == "/"
    assert result.status_code == 200
    assert refreshed.status_code == 200
    assert report.company in result.text
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert len(pdf.headers["x-pdf-release-record"]) == 64
    assert admin.status_code == 303
    assert admin.headers["location"] == "/auth/login"
    with storage_db.connect() as conn:
        views = share_store.list_report_view_events_by_hash(
            conn, share_store.key_hash_of(_카카오열쇠)
        )
    # 결과/PDF GET은 권한 판정·Delivery 읽기만 한다. 조회 KPI 쓰기는 immutable
    # 공개 GET 계약과 충돌하므로 /k open 사건만 별도 원장에 남는다.
    assert views == []


def test_LINK결과는_조회사건연결을_다시확인하지못하면_열지않는다(
    client: TestClient, monkeypatch
):
    report_id = uuid.uuid4().hex
    report = build_demo_report()
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "demo-corp", report.job, report)
    _링크발급(_카카오열쇠, report.company, report_id=report_id)
    monkeypatch.setattr(reports_router, "_release_state", _REAL_RELEASE_STATE)

    opened = client.get(f"/k/{_카카오열쇠}", follow_redirects=False)
    monkeypatch.setattr(request_helpers, "_current_share_link", lambda _request: None)
    # 기대값 이전(G-S6·D-G10): `/k/`는 이제 첫 화면으로 보낸다. 이 시험의 대상은
    # 결과 경로의 조회사건 재확인이므로 보고서 주소를 직접 연다.
    result = client.get(f"/result/{report_id}", follow_redirects=False)

    assert opened.headers["location"] == "/"
    assert result.status_code == 503
    assert "LINK 보고서를 확인할 수 없습니다" in result.text
    with storage_db.connect() as conn:
        assert share_store.list_report_view_events_by_hash(
            conn, share_store.key_hash_of(_카카오열쇠)
        ) == []


@pytest.mark.parametrize(
    "contract",
    (
        deployment_mode.RENDER_ADMIN_DEMO_NO_FORWARDED_CONTRACT,
        deployment_mode.RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT,
    ),
)
def test_좁은Render관리자운영판은_살아있는_공유capability도_열지않는다(
    client: TestClient, monkeypatch, contract
):
    report_id = uuid.uuid4().hex
    report = build_demo_report()
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "demo-corp", report.job, report)
    _링크발급(_카카오열쇠, report.company, report_id=report_id)
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        contract,
    )
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, "https://demo.example")
    capability_headers = {
        "Host": "demo.example",
        "Cookie": f"{KEY_COOKIE_NAME}={_카카오열쇠}",
    }

    opened = client.get(
        f"/k/{_카카오열쇠}", headers=capability_headers, follow_redirects=False
    )
    result = client.get(
        f"/result/{report_id}", headers=capability_headers, follow_redirects=False
    )
    pdf = client.get(
        f"/download/pdf/{report_id}",
        headers=capability_headers,
        follow_redirects=False,
    )

    for response in (opened, result, pdf):
        assert response.status_code == 303
        assert response.headers["location"] == "/auth/login"


def _post_run(
    client: TestClient,
    form: dict,
    *,
    paid_attempt_share_key: str = "",
    **kwargs,
):
    """브라우저가 화면에서 받은 권한 쿠키용 CSRF를 함께 보내는 요청."""
    data = dict(form)
    secret = (
        client.cookies.get(auth_constants.SESSION_COOKIE_NAME)
        or client.cookies.get(KEY_COOKIE_NAME)
        or ""
    )
    if secret:
        data["csrf_token"] = auth_logic.csrf_token_for_session(secret)
    if isinstance(runtime._PIPELINE, DemoPipeline):
        confirmed = client.post("/confirm", data=data)
        if confirmed.status_code != 200:
            return confirmed
        token = re.search(
            r'name="paid_attempt_token" value="([^"]+)"', confirmed.text
        )
        assert token is not None
        data["paid_attempt_token"] = token.group(1)
    elif paid_attempt_share_key:
        token = uuid.uuid4().hex
        user_input = request_helpers.company_analysis_input(
            company=data["company"],
            region=data["region"],
        )
        job_runtime._PAID_ATTEMPTS[token] = job_runtime.PaidAttempt(
            token=token,
            run_id=f"share-link-guard-{token}",
            user_input=user_input,
            card=CompanyCard(
                legal_name=user_input.company,
                typed_name=user_input.company,
                address="서울",
                ceo="",
                founded="",
                ref="share-link-guard",
            ),
            share_key=paid_attempt_share_key,
            bucket_id=spend_store.bucket_id(paid_attempt_share_key),
            lookup_cost_krw=0.0,
            models=(),
            elapsed_sec=0.0,
            created_at=time.monotonic(),
        )
        data["paid_attempt_token"] = token
    return client.post("/run", data=data, **kwargs)


def _post_confirm(client: TestClient, form: dict, **kwargs):
    data = dict(form)
    secret = client.cookies.get(KEY_COOKIE_NAME) or ""
    if secret:
        data["csrf_token"] = auth_logic.csrf_token_for_session(secret)
    return client.post("/confirm", data=data, **kwargs)


def _보고서를_만든다(client: TestClient) -> str:
    form = {
        "company": CANONICAL_DEMO_COMPANY,
        "region": "인천 서구",
    }
    run = _post_run(client, form, follow_redirects=False)
    job_id = run.headers["location"].rsplit("/", 1)[-1]
    for _ in range(200):
        if client.get(f"/api/progress/{job_id}").json()["finished"]:
            break
    else:
        pytest.fail("canonical 데모 조사가 끝나지 않았습니다")

    result = job_runtime._JOBS[job_id].result
    assert result is not None and result.outcome is Outcome.REPORT
    assert result.report is not None
    assert tuple(section.cell for section in result.report.sections) == (
        CANONICAL_SECTION_IDS
    )
    return job_id


# ══════════════════════════════════════════════════════════
# ① 로그인 없이 들어와진다
# ══════════════════════════════════════════════════════════


def test_열쇠_링크로_들어오면_열쇠가_기억된다(client: TestClient):
    """★ 한 번 들어오면 주소를 안 달고 다녀도 같은 링크로 인정된다."""
    _링크발급(_카카오열쇠, "카카오")

    response = client.get(f"/k/{_카카오열쇠}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["referrer-policy"] == "no-referrer"
    assert KEY_COOKIE_NAME in response.cookies
    assert response.cookies[KEY_COOKIE_NAME] == _카카오열쇠


def test_로그인_없이도_첫_화면이_열린다(client: TestClient):
    _링크발급(_카카오열쇠, "카카오")
    client.get(f"/k/{_카카오열쇠}")

    landing = client.get("/")
    assert landing.status_code == 200
    assert landing.headers["referrer-policy"] == "same-origin"


def test_LINK_지원회사는_맥락으로_채우되_회사입력은_편집할수있다(
    client: TestClient,
):
    _링크발급(_카카오열쇠, "카카오")

    response = client.get(f"/k/{_카카오열쇠}")

    assert 'id="company"' in response.text
    assert 'value="카카오"' in response.text
    assert 'name="job"' not in response.text
    company_input = re.search(r'<input[^>]+id="company"[^>]*>', response.text)
    assert company_input is not None
    assert "readonly" not in company_input.group(0)
    # 기대값 이전(G-S6): 초대 링크 손님의 첫 화면 문구가 인사팀 눈높이로 바뀌었다.
    # 옛 배너는 내부 용어(LINK)를 썼다 — 설계 03장 §5 금지어.
    assert "초대 링크로 들어오셨습니다" in response.text
    assert "다른 회사 분석해 보기" in response.text


# ══════════════════════════════════════════════════════════
# ② 링크 GET 요청 기록 — 사람 식별 지표로 과장하지 않는다
# ══════════════════════════════════════════════════════════


def test_열어보면_offset포함_KST_기록이_남는다(
    client: TestClient,
    monkeypatch,
):
    """GET 요청 횟수와 시각을 관찰 지표로 남긴다."""
    _링크발급(_카카오열쇠, "카카오")
    fixed = "2026-08-20T00:30:00+09:00"
    monkeypatch.setattr(analysis_router.clock, "iso_now_kst", lambda: fixed)

    client.get(f"/k/{_카카오열쇠}")
    client.get(f"/k/{_카카오열쇠}")

    with storage_db.connect() as conn:
        link = share_store.load(conn, _카카오열쇠)
        events = share_store.list_open_events_by_hash(conn, link.key_hash)
    assert link.opened_count == 2
    assert link.first_opened_at == fixed
    assert link.last_opened_at == fixed
    assert [event.opened_at for event in events] == [fixed]
    assert [event.opened_count for event in events] == [2]


def test_반복_GET은_개인을_식별하지않고_링크전체상한을_지킨다(
    client: TestClient,
    monkeypatch,
):
    _링크발급(_카카오열쇠, "카카오")
    fixed = "2026-08-20T00:30:00+09:00"
    monkeypatch.setattr(analysis_router.clock, "iso_now_kst", lambda: fixed)

    def requester_path_must_not_run(*_args, **_kwargs):
        raise AssertionError("LINK GET이 요청자 식별 함수를 불렀습니다")

    # 과거 함수 이름을 일부러 폭탄으로 심는다. 실제 /k 경로가 이 값을 읽지 않아야 한다.
    monkeypatch.setattr(
        share_access,
        "requester_hash_of",
        requester_path_must_not_run,
        raising=False,
    )

    responses = [
        client.get(f"/k/{_카카오열쇠}", follow_redirects=False)
        for _ in range(ACCESS_PER_CAPABILITY_LIMIT + 1)
    ]

    assert all(response.status_code == 303 for response in responses[:-1])
    assert responses[-1].status_code == 429
    assert responses[-1].headers["retry-after"] == "60"
    assert "no-store" in responses[-1].headers["cache-control"]
    with storage_db.connect() as conn:
        link = share_store.load(conn, _카카오열쇠)
        windows = conn.execute(
            f"SELECT opened_count FROM {share_store.TABLE_OPEN_WINDOWS}"
        ).fetchall()
        subjects = conn.execute(
            f"SELECT requester_hash, opened_count "
            f"FROM {share_store.TABLE_ACCESS_SUBJECTS}"
        ).fetchall()
        dump = "\n".join(conn.iterdump())
    assert link is not None and link.opened_count == ACCESS_PER_CAPABILITY_LIMIT
    assert [int(row[0]) for row in windows] == [ACCESS_PER_CAPABILITY_LIMIT]
    assert subjects == []
    assert _카카오열쇠 not in dump
    assert "testclient" not in dump


def test_LINK_GET_제품경로에는_IP나_요청자_파생의존이_없다() -> None:
    source = inspect.getsource(analysis_router.open_share_link)

    assert "request.client" not in source
    assert "client_host" not in source
    assert "requester" not in source


def test_처음_열어본_시각은_안_덮인다(client: TestClient):
    """나중 요청이 최초 요청 시각을 덮지 않는다."""
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
    """★ 인사팀이 «자기 회사» 보고서를 곧바로 보는 것 — 이 방식의 핵심이다.

    ★ 기대값 이전(G-S6·D-G10) — 결과로 «직행»하지 않고 첫 화면의 버튼 한 번으로
      연다. 직행하면 「다른 회사도 돌려 볼 수 있다」를 영영 못 보기 때문이다.
      「바로」는 그대로다: 조사 0회·0원이고 누르는 곳이 한 군데다.
    """
    report_id = _보고서를_만든다(client)
    _링크발급(_카카오열쇠, CANONICAL_DEMO_COMPANY, report_id=report_id)

    response = client.get(f"/k/{_카카오열쇠}", follow_redirects=False)
    랜딩 = client.get("/")

    assert response.headers["location"] == "/"
    assert f'href="/result/{report_id}"' in 랜딩.text
    assert f"{CANONICAL_DEMO_COMPANY} 보고서 보기" in 랜딩.text


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

    assert response.status_code == 404
    assert KEY_COOKIE_NAME not in response.cookies


@pytest.mark.parametrize("missing_key", [_네이버열쇠, "b" * 32])
def test_없는_32자리_열쇠를_거절한다(
    client: TestClient, missing_key: str
):
    response = client.get(f"/k/{missing_key}", follow_redirects=False)

    assert response.headers["location"] == "/?share_status=missing"
    assert KEY_COOKIE_NAME not in response.cookies


def test_DB에_남은_16자리_열쇠는_404_cookie삭제_0원이며_행은_보존된다(
    client: TestClient,
):
    _링크발급(_구형열쇠, "기존회사")
    client.cookies.set(KEY_COOKIE_NAME, _구형열쇠)

    response = client.get(f"/k/{_구형열쇠}", follow_redirects=False)

    assert response.status_code == 404
    cookie = response.headers.get("set-cookie", "").lower()
    assert "share_key=" in cookie and "max-age=0" in cookie
    with storage_db.connect() as conn:
        stored = share_store.load(conn, _구형열쇠)
    assert stored is not None
    assert stored.opened_count == 0

    request = Request(
        {
            "type": "http",
            "headers": [(b"cookie", f"{KEY_COOKIE_NAME}={_구형열쇠}".encode())],
        }
    )
    track, bucket, budget = request_helpers._track_of(request)
    assert track is share_tracks.Track.PUBLIC
    assert bucket == PUBLIC_BUCKET
    assert budget == PUBLIC_DAILY_BUDGET_KRW == 0.0


def test_HEAD는_요청횟수를_늘리지않는다(client: TestClient):
    _링크발급(_카카오열쇠, "카카오")

    response = client.head(f"/k/{_카카오열쇠}")

    assert response.status_code == 405
    with storage_db.connect() as conn:
        assert share_store.load(conn, _카카오열쇠).opened_count == 0


def test_60일_지난_알려진_LINK는_기록없이_권한을_닫는다(
    client: TestClient,
):
    _링크발급(_카카오열쇠, "카카오", now_iso="2000-01-01T10:00:00")

    response = client.get(f"/k/{_카카오열쇠}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?share_status=expired"
    assert KEY_COOKIE_NAME not in response.cookies
    with storage_db.connect() as conn:
        link = share_store.load(conn, _카카오열쇠)
        events = share_store.list_open_events_by_hash(conn, link.key_hash)
    assert link.opened_count == 0
    assert events == []


def test_이상한_열쇠는_공용_통장으로_묶인다():
    """★ 아무 글자나 새 통장이 되면 링크당 상한이 무의미해진다."""
    assert not share_logic.is_valid_key("아무글자")


def test_LINK_지원회사와_달라도_confirm에서_검색을_허용한다(client: TestClient):
    _링크발급(_카카오열쇠, "카카오")
    client.get(f"/k/{_카카오열쇠}")

    response = _post_confirm(
        client,
        {
            "company": "다른회사",
            "job": "무시할 옛 직무",
            "region": "서울",
            "posting_text": "공고",
        },
    )

    assert response.status_code == 200
    assert "카카오 분석에만" not in response.text
    assert _main_count(response.text) == 1


def test_회사링크는_옛_직무변조를_권한범위로_쓰지않는다(client: TestClient):
    _링크발급(_카카오열쇠, "카카오")
    client.get(f"/k/{_카카오열쇠}")

    response = _post_confirm(
        client,
        {
            "company": "카카오",
            "job": "변조해도 무시할 옛 직무",
            "region": "서울",
            "posting_text": "무시할 옛 공고",
        },
    )

    assert response.status_code == 200


def test_LINK_다른회사_confirm화면도_main_landmark를_중첩하지_않는다(
    client: TestClient,
):
    _링크발급(_카카오열쇠, "카카오")
    client.get(f"/k/{_카카오열쇠}")

    response = _post_confirm(
        client,
        {
            "company": "다른회사",
            "job": "마케팅",
            "region": "서울",
            "posting_text": "공고",
        },
    )

    assert response.status_code == 200
    assert _main_count(response.text) == 1
    assert "카카오 분석에만" not in response.text


def test_살아있는_LINK의_run은_다른회사_범위오류로_거절되지않는다(
    client: TestClient, monkeypatch
):
    _링크발급(_카카오열쇠, "카카오")
    client.get(f"/k/{_카카오열쇠}")
    monkeypatch.setattr(runtime, "_PIPELINE", object())

    response = _post_run(
        client,
        {
            "company": "다른회사",
            "job": "마케팅",
            "region": "서울",
            "posting_text": "공고",
            "legal_name": "다른회사",
            "ref": "재수집-p003",
            "address": "-",
        },
        paid_attempt_share_key=_카카오열쇠,
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert "카카오 분석에만" not in response.text


@pytest.mark.parametrize("state", ["expired", "revoked"])
def test_만료되거나_철회된_LINK는_GET을_기록하지않고_새생성권한도_주지않는다(
    client: TestClient, monkeypatch, state: str
):
    now_iso = (
        "2000-01-01T10:00:00"
        if state == "expired"
        else "2026-08-21T10:00:00+09:00"
    )
    _링크발급(_카카오열쇠, "카카오", now_iso=now_iso)
    if state == "revoked":
        with storage_db.connect() as conn:
            assert share_store.delete(
                conn,
                _카카오열쇠,
                revoked_at="2026-08-21T10:01:00+09:00",
            )

    opened = client.get(f"/k/{_카카오열쇠}", follow_redirects=False)
    assert opened.status_code == 303
    assert opened.headers["location"] == f"/?share_status={state}"
    assert client.cookies.get(KEY_COOKIE_NAME) is None

    # 공격자가 닫힌 raw cookie를 다시 심어도 LINK 통장으로 복원되면 안 된다.
    client.cookies.set(KEY_COOKIE_NAME, _카카오열쇠)
    monkeypatch.setattr(runtime, "_PIPELINE", object())
    before_jobs = set(job_runtime._JOBS)
    blocked = _post_run(
        client,
        {
            "company": "네이버",
            "region": "서울",
            "legal_name": "네이버(주)",
            "ref": "00266961",
            "address": "경기도 성남시",
        },
        paid_attempt_share_key=_카카오열쇠,
        follow_redirects=False,
    )

    assert blocked.status_code == 403
    assert "요청을 확인할 수 없습니다" in blocked.text
    assert set(job_runtime._JOBS) == before_jobs
    with storage_db.connect() as conn:
        link = share_store.load(conn, _카카오열쇠)
        events = share_store.list_open_events_by_hash(conn, link.key_hash)
        runs = share_store.list_runs_by_hash(conn, link.key_hash)
    assert link.opened_count == 0
    assert events == []
    assert runs == []


@pytest.mark.parametrize("kind", ["invalid", "missing", "expired"])
def test_잘못된_다음링크는_이전_활성쿠키를_지운다(
    client: TestClient, kind: str
):
    _링크발급(_카카오열쇠, "카카오")
    client.get(f"/k/{_카카오열쇠}")
    assert client.cookies.get(KEY_COOKIE_NAME) == _카카오열쇠

    if kind == "invalid":
        target = "zzzz"
    elif kind == "missing":
        target = _네이버열쇠
    else:
        target = _네이버열쇠
        _링크발급(target, "네이버", now_iso="2000-01-01T10:00:00")

    response = client.get(f"/k/{target}", follow_redirects=False)

    if kind == "invalid":
        assert response.status_code == 404
        assert "location" not in response.headers
    else:
        assert response.status_code == 303
        assert response.headers["location"] == f"/?share_status={kind}"
    assert client.cookies.get(KEY_COOKIE_NAME) is None
    assert "Max-Age=0" in response.headers.get("set-cookie", "")


def test_링크저장소_장애는_이전쿠키를_지우고_503을_보인다(
    client: TestClient, monkeypatch
):
    _링크발급(_카카오열쇠, "카카오")
    client.get(f"/k/{_카카오열쇠}")

    def broken_connect(*_args, **_kwargs):
        raise OSError("DB unavailable")

    monkeypatch.setattr(storage_db, "connect", broken_connect)
    response = client.get(f"/k/{_네이버열쇠}", follow_redirects=False)

    assert response.status_code == 503
    assert 'role="alert"' in response.text
    assert client.cookies.get(KEY_COOKIE_NAME) is None
    assert _main_count(response.text) == 1


def test_연결보고서가_만료되면_죽은결과가_아니라_prefill과_안내로_간다(
    client: TestClient, monkeypatch
):
    report_id = _보고서를_만든다(client)
    _링크발급(_카카오열쇠, CANONICAL_DEMO_COMPANY, report_id=report_id)
    monkeypatch.setattr("src.web.job_runtime._link_expired", lambda _report: True)

    opened = client.get(f"/k/{_카카오열쇠}", follow_redirects=False)

    assert opened.status_code == 303
    assert opened.headers["location"] == "/?share_status=report-expired"
    page = client.get(opened.headers["location"])
    assert f'value="{CANONICAL_DEMO_COMPANY}"' in page.text
    assert 'name="job"' not in page.text
    assert "기존 보고서의 공유 기간이 지나" in page.text


def test_연결보고서가_없어도_prefill과_안내로_간다(client: TestClient):
    _링크발급(_카카오열쇠, "카카오", report_id="a" * 32)

    opened = client.get(f"/k/{_카카오열쇠}", follow_redirects=False)

    assert opened.headers["location"] == "/?share_status=report-missing"
    page = client.get(opened.headers["location"])
    assert 'value="카카오"' in page.text
    assert "기존 보고서를 찾을 수 없어" in page.text


def test_시작보고서는_지원회사_꼬리표와_달라도_그대로_열린다(client: TestClient):
    """★ 기대값 이전(G-S6·D-G10) — 도착지가 결과에서 첫 화면으로 바뀌었다.

    이 시험이 지키는 것은 「회사 꼬리표가 달라도 묶인 보고서가 열린다」이다.
    그래서 랜딩의 버튼이 그 보고서를 가리키는지까지 확인한다.
    """
    report_id = _보고서를_만든다(client)  # canonical 진영 보고서
    _링크발급(_카카오열쇠, "다른회사", report_id=report_id)

    opened = client.get(f"/k/{_카카오열쇠}", follow_redirects=False)
    랜딩 = client.get("/")

    assert opened.status_code == 303
    assert opened.headers["location"] == "/"
    assert f'href="/result/{report_id}"' in 랜딩.text
    assert "다른회사 보고서 보기" in 랜딩.text
    with storage_db.connect() as conn:
        link = share_store.load(conn, _카카오열쇠)
    assert link.report_id == report_id
    assert link.company == "다른회사"


def test_robots는_capability_경로를_경로단위로_제외한다(client: TestClient):
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "Disallow: /k/" in response.text
    assert "Disallow: /result/" in response.text
    assert "Disallow: /download/" in response.text


# ══════════════════════════════════════════════════════════
# ⑤ 링크마다 예산이 «따로» 센다
# ══════════════════════════════════════════════════════════


def test_한_링크가_다_써도_다른_링크는_돈다(client: TestClient, monkeypatch):
    """★ P-94의 핵심 — 「전체 하나」가 아니라 「링크당」을 고른 이유다."""
    _링크발급(_카카오열쇠, "카카오")
    _링크발급(_네이버열쇠, "카카오")
    오늘 = clock.today_kst()
    monkeypatch.setattr(runtime, "_PIPELINE", object())          # 돈이 드는 것으로 본다
    monkeypatch.setattr(
        paid_runtime, "_LINK_SPEND",
        share_logic.add_spend(
            share_logic.DailySpend(day=오늘), _카카오열쇠, 오늘, PER_LINK_DAILY_BUDGET_KRW
        ),
    )
    form = {
        "company": "카카오", "job": "마케팅", "region": "서울", "posting_text": "x",
        "legal_name": "카카오", "ref": "재수집-p003", "address": "-",
    }

    client.cookies.set(KEY_COOKIE_NAME, _카카오열쇠)
    막힘 = _post_run(
        client,
        form,
        paid_attempt_share_key=_카카오열쇠,
        follow_redirects=False,
    )
    client.cookies.set(KEY_COOKIE_NAME, _네이버열쇠)
    통과 = _post_run(client, form, follow_redirects=False)

    assert 막힘.status_code == 429, "다 쓴 링크는 막혀야 한다"
    assert 통과.status_code == 303, "★ 다른 링크는 멀쩡히 돌아야 한다"


def test_열쇠_없는_손님도_상한을_받는다(client: TestClient, monkeypatch):
    """★ 안 걸면 「열쇠 없이 들어오는 길」이 상한 없는 구멍이 된다."""
    오늘 = clock.today_kst()
    monkeypatch.setattr(runtime, "_PIPELINE", object())
    monkeypatch.setattr(
        paid_runtime, "_LINK_SPEND",
        share_logic.add_spend(
            share_logic.DailySpend(day=오늘), PUBLIC_BUCKET, 오늘, PER_LINK_DAILY_BUDGET_KRW
        ),
    )
    form = {
        "company": "카카오", "job": "마케팅", "region": "서울", "posting_text": "x",
        "legal_name": "카카오", "ref": "재수집-p003", "address": "-",
    }

    assert _post_run(client, form, follow_redirects=False).status_code == 403


# ══════════════════════════════════════════════════════════
# ⑥ ★★ 로그인만으로는 «아무 권한도» 안 준다 (P-95)
# ══════════════════════════════════════════════════════════
# 사용자가 직접 지적해 잡힌 구멍이다 (2026-08-16):
#   「링크로 들어와서 그냥 구글로 로그인하면 어떻게 되나?」
# 그때는 **아무나 로그인만 하면 하루 1,000원**을 쓸 수 있었다.


def _로그인시킨다(client: TestClient, email: str, *, is_admin: bool = False) -> None:
    """이 손님을 «로그인한 상태»로 만든다 (초대 여부는 별개다)."""
    subject = "google:test-" + hashlib.sha256(email.encode("utf-8")).hexdigest()[:20]
    session = auth_logic.create_session(email, is_admin, subject=subject)
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
    monkeypatch.setattr(runtime, "_PIPELINE", object())          # 돈이 드는 것으로 본다
    _로그인시킨다(client, "stranger@gmail.com")
    form = {
        "company": "카카오", "job": "마케팅", "region": "서울", "posting_text": "x",
        "legal_name": "카카오", "ref": "재수집-p003", "address": "-",
    }

    response = _post_run(
        client,
        form,
        paid_attempt_share_key=PUBLIC_BUCKET,
        follow_redirects=False,
    )

    assert response.status_code == 429
    assert "초대 링크로 들어오신 분만" in response.text


def test_초대한_친구는_진짜_조사를_할_수_있다(client: TestClient, monkeypatch):
    """★ 반대 방향 — 다 막아버리면 친구들이 못 쓴다."""
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    _초대한다("friend@gmail.com")
    _로그인시킨다(client, "friend@gmail.com")
    form = {
        "company": "우리엔", "job": "영업", "region": "서울", "posting_text": "x",
        "legal_name": "우리엔", "ref": "재수집-p003", "address": "-",
    }

    assert _post_run(client, form, follow_redirects=False).status_code == 303


def test_링크로_들어와_로그인해도_링크_몫만_쓴다(client: TestClient, monkeypatch):
    """★ 사용자가 물어본 바로 그 상황.

    방문자가 열쇠 링크로 들어와 호기심에 구글 로그인을 눌러도,
    **같은 LINK에 배정된 여러 회사 조사 합계 몫**을 쓴다. 로그인했다고 통장이
    하나 더 생기지 않는다.
    """
    _링크발급(_카카오열쇠, "카카오")
    오늘 = clock.today_kst()
    monkeypatch.setattr(runtime, "_PIPELINE", object())
    monkeypatch.setattr(
        paid_runtime, "_LINK_SPEND",
        share_logic.add_spend(
            share_logic.DailySpend(day=오늘), _카카오열쇠, 오늘, PER_LINK_DAILY_BUDGET_KRW
        ),
    )
    client.cookies.set(KEY_COOKIE_NAME, _카카오열쇠)
    _로그인시킨다(client, "hr@kakao.com")            # 초대 명단에는 없다
    form = {
        "company": "카카오", "job": "마케팅", "region": "서울", "posting_text": "x",
        "legal_name": "카카오", "ref": "재수집-p003", "address": "-",
    }

    response = _post_run(
        client,
        form,
        paid_attempt_share_key=_카카오열쇠,
        follow_redirects=False,
    )

    assert response.status_code == 429, "로그인으로 «몫이 늘면» 안 된다"


def test_명단에서_빼면_바로_막힌다(client: TestClient, monkeypatch):
    """★ 되돌릴 방법이 있어야 한다 — 다 썼거나 계정이 넘어갔을 때."""
    monkeypatch.setattr(runtime, "_PIPELINE", object())
    _초대한다("friend@gmail.com")
    with storage_db.connect() as conn:
        share_allow.revoke(conn, "friend@gmail.com")
    _로그인시킨다(client, "friend@gmail.com")
    form = {
        "company": "우리엔", "job": "영업", "region": "서울", "posting_text": "x",
        "legal_name": "우리엔", "ref": "재수집-p003", "address": "-",
    }

    assert _post_run(
        client,
        form,
        paid_attempt_share_key=PUBLIC_BUCKET,
        follow_redirects=False,
    ).status_code == 429


def test_모르는_손님도_데모_화면은_그대로_본다(client: TestClient):
    """★ 「진짜 조사만」 막는 것이다 — 도구가 어떤 건지는 다 보여준다."""
    assert client.get("/").status_code == 200
