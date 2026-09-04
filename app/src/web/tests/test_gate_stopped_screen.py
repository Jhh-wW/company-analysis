# -*- coding: utf-8 -*-
"""자동검사 중단(409) 화면이 «막다른 길»이 되지 않는지 지키는 시험.

★ 왜 이 파일이 따로 있나
  기존 시험은 상태코드와 안내 문구만 봤다. 그래서 «버튼이 아무 데도 안 가고
  신고할 칸도 없다»는 실제 증상을 한 건도 잡지 못했다. 여기서는 화면에서
  사용자가 실제로 «누를 수 있는 것»을 본다.
"""

from __future__ import annotations

import re
import time
import uuid
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.core import clock
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.export_pdf.automatic_release import AutomaticGateStopped
from src.features.export_pdf.release import PDFReleaseBlockedError
from src.features.feedback_report import constants as feedback_constants
from src.features.pipeline.demo import DemoPipeline, available_companies
from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
from src.features.report_access import constants as report_access_constants
from src.features.report_access import store as report_access_store
from src.features.report_delivery import store as delivery_store
from src.features.sharelink import allowlist as share_allow
from src.features.storage import db as storage_db
from src.web import job_runtime, request_helpers
from src.web.main import app
from src.web.routers import reports as reports_router


#: 자동검사가 실제로 쓰는 것과 같은 모양의 고정 한국어 사유.
_GATE_REASONS = (
    "사실·인용·수치·목차·금지 문구 정본 검사를 통과하지 못했습니다",
    "PDF 전 페이지 생성·렌더 검사를 통과하지 못했습니다",
)
#: 예외 메시지에만 들어가는 값. 화면에 나오면 str(error)를 쓴 것이다.
_ERROR_ONLY_MARKER = "ERROR-MESSAGE-ONLY-MARKER"

#: 안내 화면의 주 버튼을 뽑는 정규식. 링크 글자까지 같이 잡아 «빈 버튼»도 본다.
_BUTTON_PATTERN = re.compile(
    r'<a class="btn"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', re.DOTALL
)
#: 화면 전체에서 「글자 없는 버튼」을 찾는 정규식 (접근성 이름이 없는 링크).
_EMPTY_BUTTON_PATTERN = re.compile(r'<a class="btn"[^>]*>\s*</a>')
#: 안내 화면의 오류 신고 링크를 뽑는 정규식.
_FEEDBACK_PATTERN = re.compile(r'href="(/feedback\?[^"]*)"')


def _demo_report():
    pipeline = DemoPipeline()
    sample = next(item for item in available_companies() if item["is_report"])
    user_input = UserInput(
        company=sample["company"],
        job=sample["job"],
        region="",
        posting_text="",
    )
    result = pipeline.run(user_input, pipeline.find_company(user_input))
    assert result.outcome is Outcome.REPORT and result.report is not None
    return result.report


def _synthetic_request(*, method: str = "GET", path: str = "/result/synthetic"):
    """라우팅 없이 안내 화면 함수만 직접 그려 보기 위한 최소 요청."""
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": path,
            "raw_path": path.encode(),
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "server": ("127.0.0.1", 8000),
        }
    )


@pytest.fixture
def gate_stopped(monkeypatch):
    """자동검사가 반드시 멈추는 상태를 만들고 5경로에서 쓸 재료를 돌려준다."""
    report = _demo_report()

    def stopped(**_kwargs):
        raise AutomaticGateStopped(_GATE_REASONS)

    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    monkeypatch.setattr(reports_router, "_approved_public_report", lambda _id, r: r)
    # ★ conftest의 autouse fixture가 승인 성공으로 덮어 두므로 여기서 다시 막는다.
    monkeypatch.setattr(reports_router, "_release_state", stopped)
    return report


def _memory_job(job_id: str, report):
    """GET /result의 «메모리 job» 갈래를 타게 하는 완료 상태 job."""
    return job_runtime.Job(
        job_id=job_id,
        user_input=UserInput(company=report.company, job="영업", region=""),
        card=CompanyCard(
            legal_name=report.company,
            typed_name=report.company,
            address="",
            ceo="",
            founded="",
        ),
        finished=True,
        report_persisted=True,
        result=RunResult(outcome=Outcome.REPORT, report=report),
    )


def _admin_cookies():
    session = auth_logic.create_session(
        "admin@example.com", True, subject="test:gate-screen-admin"
    )
    return (
        {auth_constants.SESSION_COOKIE_NAME: session.token},
        auth_logic.csrf_token_for_session(session.token),
    )


def _member_cookies(job_id: str, email: str = "member@example.com"):
    subject = "test:gate-screen-member"
    session = auth_logic.create_session(email, False, subject=subject)
    with storage_db.connect() as conn:
        share_allow.invite(
            conn, email=email, note="중단 화면 시험", now_iso="2026-08-25T10:00:00"
        )
        report_access_store.bind_member_run(
            conn, run_id=job_id, identity_subject=subject
        )
    return {auth_constants.SESSION_COOKIE_NAME: session.token}


def _public_grant_cookies(job_id: str) -> dict[str, str]:
    with storage_db.connect() as conn:
        grant = report_access_store.issue_and_bind(
            conn, existing_token=None, run_id=job_id, now=time.time()
        )
    return {report_access_constants.PUBLIC_GRANT_COOKIE_NAME: grant.token}


def _persist_gate_failure(job_id: str) -> None:
    """GET이 현재 검사를 다시 돌리지 않고 영속 실패 코드만 읽게 한다."""

    now = clock.now_kst()
    with storage_db.connect() as conn:
        delivery_store.mark_delivery_required(
            conn, public_id=job_id, required_at=now
        )
        delivery_store.mark_delivery_failed(
            conn,
            public_id=job_id,
            failure_code="automatic_release_gate_blocked",
            failed_at=now,
        )


def _blocked_responses(client, job_id: str, report, cookies, csrf: str):
    """자동검사 중단 화면을 내는 5경로를 모두 한 번씩 부른다."""
    _persist_gate_failure(job_id)
    saved_path = client.get(f"/result/{job_id}", cookies=cookies)

    memory_id = uuid.uuid4().hex
    job_runtime._JOBS[memory_id] = _memory_job(memory_id, report)
    _persist_gate_failure(memory_id)
    memory_path = client.get(f"/result/{memory_id}", cookies=cookies)

    download_path = client.get(f"/download/pdf/{job_id}", cookies=cookies)
    notion_path = client.post(
        f"/notion/{job_id}", cookies=cookies, data={"csrf_token": csrf}
    )
    return {
        "저장본_result": saved_path,
        "메모리_result": memory_path,
        "download_pdf": download_path,
        "notion_post": notion_path,
    }


def test_자동검사중단_5경로_어디에도_빈_href_버튼이_없다(gate_stopped, monkeypatch):
    report = gate_stopped
    job_id = uuid.uuid4().hex
    cookies, csrf = _admin_cookies()

    with TestClient(app, base_url="https://testserver") as client:
        responses = _blocked_responses(client, job_id, report, cookies, csrf)
        # 승인이 아예 없는 다섯 번째 갈래(released is None)도 같은 화면이다.
        monkeypatch.setattr(
            reports_router, "_release_state", lambda **_kwargs: (object(), None)
        )
        responses["notion_승인없음"] = client.post(
            f"/notion/{job_id}", cookies=cookies, data={"csrf_token": csrf}
        )

    for name, response in responses.items():
        assert response.status_code == 409, name
        assert 'href=""' not in response.text, f"{name}: 아무 데도 안 가는 버튼"
        assert not _EMPTY_BUTTON_PATTERN.search(response.text), f"{name}: 빈 버튼"
        button = _BUTTON_PATTERN.search(response.text)
        assert button is not None, f"{name}: 나갈 버튼이 없다"
        target, label = button.group(1), button.group(2).strip()
        assert target.startswith("/"), f"{name}: 버튼 주소 {target!r}"
        assert label, f"{name}: 버튼에 글자가 없다"
        # 같은 주소를 다시 부르게 하면 결정적 게이트라 또 같은 화면이 된다.
        assert job_id not in target, f"{name}: 제자리로 돌려보낸다 ({target})"


def test_자동검사중단_화면은_막은_사유와_문의번호를_보여준다(gate_stopped):
    report = gate_stopped
    job_id = uuid.uuid4().hex
    cookies, _csrf = _admin_cookies()
    _persist_gate_failure(job_id)

    with TestClient(app, base_url="https://testserver") as client:
        response = client.get(f"/result/{job_id}", cookies=cookies)

    assert response.status_code == 409
    # GET은 현재 검사를 다시 돌리지 않으므로 저장된 닫힌 실패 코드가 안전한
    # 고정 안내로 번역된다. 예외 원문을 복원하려고 시도하지 않는다.
    assert "자동 출고 승인을 확인하지 못했습니다" in response.text
    # 문의 번호는 응답 헤더의 상관 ID와 같은 값이어야 로그와 대조된다.
    assert response.headers["X-Request-ID"] in response.text
    # 「청구되지 않는다」는 사실이 아니다 — 다시 나타나면 안 된다.
    assert "청구되지 않습니다" not in response.text
    # ↻ 는 «다시 하면 된다»는 뜻이라 결정적으로 막힌 이 화면에는 쓰지 않는다.
    assert "↻" not in response.text
    assert report.company or True


def test_일시장애_화면은_다시_시도_아이콘과_안내를_그대로_쓴다():
    """아이콘·안내 교체가 «다시 하면 되는» 화면까지 바꾸지 않았는지 본다."""
    html = reports_router._link_view_event_unavailable_response(
        _synthetic_request()
    ).body.decode("utf-8")

    assert "↻" in html
    # 버튼 값을 손보다가 이 화면 고유의 안내를 잃어버리면 템플릿 기본 문구
    # (「입력 화면에서 같은 회사와 직무로 다시 시작해 주세요」)가 나온다 — 틀린 안내다.
    assert "잠시 후 같은 초대 링크로 다시 시도해 주세요." in html
    assert "입력 화면에서 같은 회사와 직무로" not in html


def test_PDF를_못_만든_화면에도_문의_번호가_있다(monkeypatch):
    """일시 장애 화면도 로그와 대조할 번호를 사용자에게 준다."""
    job_id = uuid.uuid4().hex
    now = clock.now_kst()
    with storage_db.connect() as conn:
        delivery_store.mark_delivery_required(conn, public_id=job_id, required_at=now)
        delivery_store.mark_delivery_failed(
            conn,
            public_id=job_id,
            failure_code="artifact_finalization_failed",
            failed_at=now,
        )
    cookies, _csrf = _admin_cookies()

    with TestClient(app, base_url="https://testserver") as client:
        response = client.get(
            f"/download/pdf/{job_id}", cookies=cookies, follow_redirects=False
        )

    assert response.status_code == 503
    assert "저장된 보고서를 확인할 수 없습니다" in response.text
    assert response.headers["X-Request-ID"] in response.text


def test_자동검사중단_화면은_예외메시지_원문을_그리지_않는다(monkeypatch):
    report = _demo_report()

    def stopped(**_kwargs):
        raise PDFReleaseBlockedError(f"GATE_STOPPED: {_ERROR_ONLY_MARKER}")

    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    monkeypatch.setattr(reports_router, "_release_state", stopped)
    job_id = uuid.uuid4().hex
    cookies, _csrf = _admin_cookies()
    _persist_gate_failure(job_id)

    with TestClient(app, base_url="https://testserver") as client:
        response = client.get(f"/result/{job_id}", cookies=cookies)

    assert response.status_code == 409
    assert _ERROR_ONLY_MARKER not in response.text
    assert "PDFReleaseBlockedError" not in response.text
    # 사유를 못 읽어도 «알 수 없음»이 아니라 정직한 고정 문구를 쓴다.
    assert "자동 출고 승인을 확인하지 못했습니다" in response.text


def test_회원과_링크손님만_중단화면에서_오류신고로_갈_수_있다(gate_stopped):
    job_id = uuid.uuid4().hex
    _persist_gate_failure(job_id)
    # 한글 단계값은 링크에서 퍼센트 인코딩된다 — 서버가 되돌려 읽는지까지 본다.
    expected = f"/feedback?stage={quote(feedback_constants.STAGE_REPORT)}"

    with TestClient(app, base_url="https://testserver") as client:
        member = client.get(
            f"/result/{job_id}", cookies=_member_cookies(job_id)
        )
        assert member.status_code == 409
        assert expected in member.text, "회원에게 신고 통로가 없다"
        feedback_href = _FEEDBACK_PATTERN.search(member.text)
        assert feedback_href is not None
        # 링크가 «실제로 열리는지»까지 확인한다. 주소만 있고 안 열리면 같은 결함이다.
        opened = client.get(
            feedback_href.group(1).replace("&amp;", "&"),
            cookies=_member_cookies(job_id),
            follow_redirects=False,
        )
        public_demo = client.get(
            f"/result/{job_id}", cookies=_public_grant_cookies(job_id)
        )
        stranger = client.get(f"/result/{job_id}")

    assert opened.status_code == 200
    assert feedback_constants.STAGE_REPORT in opened.text
    # ★ report= 를 넘기면 신고 폼의 「보고서로 돌아가기」가 이 막힌 화면으로 되돌린다.
    assert f"report={job_id}" not in member.text
    assert f"/result/{job_id}" not in opened.text

    assert public_demo.status_code == 409
    # PUBLIC grant 브라우저는 POST /feedback 대상이 아니므로 막힌 링크를 숨긴다.
    assert expected not in public_demo.text
    # 같은 ID를 주운 다른 브라우저는 중단 사유에도 접근할 수 없다.
    assert stranger.status_code == 404


def test_Notion_중단화면의_버튼을_그대로_열면_405가_아니다(gate_stopped):
    report = gate_stopped
    job_id = uuid.uuid4().hex
    cookies, csrf = _admin_cookies()
    # Notion도 결과/PDF와 같은 영속 delivery 판정을 읽는다. 폐기된 메모리
    # 보고서 재검사 경로에 기대지 않고, 생산 실패가 남기는 intent를 만든다.
    _persist_gate_failure(job_id)

    with TestClient(app, base_url="https://testserver") as client:
        blocked = client.post(
            f"/notion/{job_id}", cookies=cookies, data={"csrf_token": csrf}
        )
        assert blocked.status_code == 409
        button = _BUTTON_PATTERN.search(blocked.text)
        assert button is not None
        target = button.group(1)
        assert target != f"/notion/{job_id}", "POST 전용 경로로 되돌려 보낸다"
        followed = client.get(target, cookies=cookies, follow_redirects=False)

    assert followed.status_code != 405, f"{target} 가 GET을 받지 못한다"
    assert followed.status_code != 404, f"{target} 가 없는 주소다"
    assert report is not None


def test_라벨이_비어_있던_두_화면에_글자없는_버튼이_없다():
    """`retry_label=""` 로 부르던 두 화면이 이름 없는 빈 버튼을 그리지 않는다.

    두 화면의 «옳은 다음 행동»이 다르므로 버튼의 뜻도 다르다. 그 구분까지 본다.
    """
    screens = {
        # 권한이 없는 것이 확정 — 이 화면을 다시 열어도 결과가 같다.
        "MEMBER권한": (
            reports_router._revoked_member_response(
                _synthetic_request(), unavailable=False
            ),
            False,
        ),
        # 조회 사건을 못 쓴 일시 장애 — 같은 LINK로 다시 여는 것이 재시도다.
        "LINK조회사건": (
            reports_router._link_view_event_unavailable_response(_synthetic_request()),
            True,
        ),
    }
    for name, (response, same_page) in screens.items():
        html = response.body.decode("utf-8")
        assert not _EMPTY_BUTTON_PATTERN.search(html), f"{name}: 글자 없는 버튼"
        button = _BUTTON_PATTERN.search(html)
        assert button is not None, f"{name}: 나갈 버튼이 없다"
        assert button.group(2).strip(), f"{name}: 버튼에 글자가 없다"
        # ★ 템플릿의 빈 값 접기에 기대지 않는다. 라우터도 «자기 몫»을 채워야
        #   한 겹이 사라졌을 때 이름 없는 버튼이 되살아나지 않는다.
        assert response.context["retry_label"], f"{name}: 라우터가 글자를 안 넘긴다"
        assert response.context["retry_same_page"] is same_page, name
        if same_page:
            # 빈 href는 HTML에서 «지금 이 주소»다 — 일시 장애에서만 뜻이 맞는다.
            assert button.group(1) == "", f"{name}: {button.group(1)!r}"
        else:
            assert button.group(1).startswith("/"), f"{name}: {button.group(1)!r}"
            assert response.context["retry_url"], f"{name}: 라우터가 주소를 안 넘긴다"


def test_일시장애_안내화면은_조사번호를_화면에_되비추지_않는다(monkeypatch):
    """주소를 버튼에 박아 넣다가 번호 반사 금지 계약을 깨뜨리지 않게 지킨다."""
    secret_id = "de" * 16
    now = clock.now_kst()
    with storage_db.connect() as conn:
        delivery_store.mark_delivery_required(
            conn, public_id=secret_id, required_at=now
        )
        delivery_store.mark_delivery_failed(
            conn,
            public_id=secret_id,
            failure_code="artifact_finalization_failed",
            failed_at=now,
        )
    cookies, _csrf = _admin_cookies()

    with TestClient(app, base_url="https://testserver") as client:
        response = client.get(
            f"/download/pdf/{secret_id}", cookies=cookies, follow_redirects=False
        )

    assert response.status_code == 503
    assert secret_id not in response.text


def test_자동검사중단_라우터가_출구_주소와_글자를_직접_넘긴다(gate_stopped):
    """템플릿 기본값이 아니라 라우터가 정한 출구가 실제로 실려 나가는지 본다."""
    job_id = uuid.uuid4().hex
    cookies, csrf = _admin_cookies()
    _persist_gate_failure(job_id)

    with TestClient(app, base_url="https://testserver") as client:
        result = client.get(f"/result/{job_id}", cookies=cookies)
        notion = client.post(
            f"/notion/{job_id}", cookies=cookies, data={"csrf_token": csrf}
        )

    for name, response in (("result", result), ("notion", notion)):
        assert response.status_code == 409, name
        context = response.context
        assert context["retry_url"], f"{name}: 라우터가 출구 주소를 안 넘긴다"
        assert context["retry_label"], f"{name}: 라우터가 버튼 글자를 안 넘긴다"
        assert context["retry_url"].startswith("/"), name
        # 「다시 확인하면 달라진다」는 거짓 기대를 주지 않는다.
        assert "다시" not in context["retry_label"], f"{name}: 재시도를 권한다"
    # POST 전용 경로는 자기 주소로 돌려보내면 405가 난다.
    assert notion.context["retry_url"] != f"/notion/{job_id}"


def test_안내화면_템플릿은_빈_값을_받아도_빈_버튼을_그리지_않는다():
    """템플릿 한 겹만 놓고 본다 — 라우터가 빈 값을 넘겨도 버튼이 살아 있어야 한다."""
    request = _synthetic_request()
    response = request_helpers.templates.TemplateResponse(
        request=request,
        name="progress_unavailable.html",
        context=request_helpers._ctx(
            request,
            interruption_title="시험용 안내",
            interruption_message="시험용 본문",
            retry_url="",
            retry_label="",
        ),
    )
    html = response.body.decode("utf-8")

    assert 'href=""' not in html
    assert not _EMPTY_BUTTON_PATTERN.search(html)
    button = _BUTTON_PATTERN.search(html)
    assert button is not None
    assert button.group(1).startswith("/")
    assert button.group(2).strip()


def test_POST로_들어온_안내화면은_새로고침을_권하지_않는다():
    """POST 전용 경로를 다시 열게 하면 브라우저가 GET으로 열어 405가 난다."""
    post_context = job_runtime.retry_or_exit(
        _synthetic_request(method="POST", path="/notion/job-1"),
        retry_label="상태 다시 확인",
    )
    get_context = job_runtime.retry_or_exit(
        _synthetic_request(method="GET", path="/result/job-1"),
        retry_label="상태 다시 확인",
    )

    assert post_context["retry_same_page"] is False
    assert post_context["retry_url"] == job_runtime.DEFAULT_EXIT_URL
    assert post_context["retry_label"] == job_runtime.DEFAULT_EXIT_LABEL

    assert get_context["retry_same_page"] is True
    assert get_context["retry_label"] == "상태 다시 확인"
    # 주소에 든 조사 번호를 화면에 되비추지 않으려고 주소 문자열은 쓰지 않는다.
    assert get_context["retry_url"] == ""
