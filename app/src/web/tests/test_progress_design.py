"""진행 화면의 단계 카드·기업명 표시와 보조기술 상태가 같이 바뀌는지 확인한다."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.core.constants import PROGRESS_STEPS
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.web import job_runtime, main
from src.web.routers import analysis


WEB = Path(__file__).parents[1]
TEMPLATE = WEB / "templates" / "progress.html"
STYLE = WEB / "static" / "style.css"


def _set_admin_cookie(client: TestClient) -> None:
    session = auth_logic.create_session(
        "admin@example.com", True, subject="google:progress-design-admin"
    )
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)


def _render_progress_page(job_id: str, legal_name: str) -> str:
    """가짜 실행 중 Job으로 진행 화면 HTML을 실제 라우터로 렌더한다."""
    job_runtime._JOBS[job_id] = SimpleNamespace(
        job_id=job_id,
        card=SimpleNamespace(legal_name=legal_name),
    )
    try:
        with TestClient(main.app) as client:
            _set_admin_cookie(client)
            response = client.get(f"/progress/{job_id}")
    finally:
        job_runtime._JOBS.pop(job_id, None)
    assert response.status_code == 200
    return response.text


def test_단계_카드_묶음은_실제_백엔드_단계_키와_정확히_일치한다():
    flat = tuple(
        key
        for _title, keys in analysis._COMPANY_ANALYSIS_PROGRESS_PHASES
        for key in keys
    )

    # 누락·중복·순서 불일치 어느 쪽도 허용하지 않는다. 파이프라인 단계가 바뀌면
    # 표시 묶음도 함께 바꿔야 이 시험이 통과한다 (가짜 진행 표시 방지).
    assert flat == tuple(
        key for key, _label in analysis._COMPANY_ANALYSIS_PROGRESS_STEPS
    )
    assert frozenset(flat) == analysis._COMPANY_ANALYSIS_PROGRESS_KEYS
    assert set(flat) <= {key for key, _label in PROGRESS_STEPS}


def test_진행_화면은_조사_대상_기업명을_보여준다():
    html = _render_progress_page("1" * 32, "제이와이피엔터테인먼트")

    assert "조사 대상 기업" in html
    assert "제이와이피엔터테인먼트" in html


def test_진행_화면은_단계_카드를_그리고_가짜_진행률은_만들지_않는다():
    html = _render_progress_page("2" * 32, "샘플기업")

    assert html.count("<li data-keys=") == len(
        analysis._COMPANY_ANALYSIS_PROGRESS_PHASES
    )
    for index, (title, keys) in enumerate(
        analysis._COMPANY_ANALYSIS_PROGRESS_PHASES, start=1
    ):
        joined_keys = " ".join(keys)
        assert f"{index}단계" in html
        assert title in html
        assert f'data-keys="{joined_keys}"' in html
    # 초기 상태는 전부 대기. 백엔드가 주지 않는 % 게이지는 만들지 않는다.
    assert html.count(">대기</span>") == len(
        analysis._COMPANY_ANALYSIS_PROGRESS_PHASES
    )
    assert "전체 진행률" not in html
    assert "<progress" not in html
    assert 'class="gauge"' not in html


def test_진행_화면_안내는_실측_근거와_이탈_허용을_함께_말한다():
    html = TEMPLATE.read_text(encoding="utf-8")

    # 닫아도 계속된다는 안내 + 실측 기반 소요 안내 + 변동 가능성 고지.
    assert "페이지를 닫아도 조사는 계속 진행됩니다" in html
    assert "지금까지 조사는 대체로 5분 안에 끝났습니다" in html
    assert "걸리는 시간이 달라질 수 있습니다" in html
    # 근거 없는 확정 문구는 금지.
    assert "보통 1~3분" not in html
    assert "최대 5분" not in html
    assert "비용이 들지 않습니다" not in html


def test_진행_화면은_현재_단계를_보조기술에_알린다():
    html = TEMPLATE.read_text(encoding="utf-8")

    assert 'aria-label="조사 진행 단계"' in html
    assert 'id="progress-status"' in html
    # 일반 진행과 재시도 가능한 503은 이 polite status 하나만 갱신한다.
    assert html.count('role="status"') == 1
    assert html.count('aria-live="polite"') == 1
    assert html.count('aria-atomic="true"') == 1
    assert "li.removeAttribute('aria-current')" in html
    assert "li.setAttribute('aria-current', 'step')" in html
    assert "announcement !== lastAnnouncement" in html
    # 화면의 「현재 단계」 줄은 백엔드 세부 단계 이름만 보여준다.
    assert 'id="current-step-label"' in html
    assert "STEP_LABELS" in html
    assert 'id="progress-interrupted"' in html
    assert 'role="alert"' not in html
    assert 'id="progress-retry"' in html
    assert "showInterrupted(d.error, d.retry_url)" in html
    assert "interruptedTitle.focus()" in html
    assert 'id="progress-check-problem"' in html
    assert 'id="progress-manual-retry"' in html
    assert "MAX_AUTO_FAILURES = 3" in html
    assert "response.status === 503" in html
    assert "response.headers.get('Retry-After')" in html
    assert "handlePollFailure" in html
    assert "manualRetry.addEventListener('click'" in html
    assert "조사를 새로 시작하지 마세요" in html


def test_진행_실패는_상태별_주_알림_경로를_하나만_쓴다():
    html = TEMPLATE.read_text(encoding="utf-8")

    check_problem = html[
        html.index('<section id="progress-check-problem"'):
        html.index('<section id="progress-interrupted"')
    ]
    interrupted = html[
        html.index('<section id="progress-interrupted"'):
        html.index('<p class="muted" style="margin-top:14px">')
    ]
    interrupted_script = html[
        html.index("function showInterrupted"):
        html.index("function retryDelay")
    ]

    # 503 문제 카드는 보이는 설명일 뿐이며 숨은 status만 polite하게 알린다.
    assert 'role="status"' not in check_problem
    assert 'role="alert"' not in check_problem
    assert "aria-live" not in check_problem
    assert "progressStatus.textContent" in html[
        html.index("function showCheckProblem"):
        html.index("function handlePollFailure")
    ]

    # 410은 별도 live/alert를 만들거나 숨은 status를 다시 쓰지 않고 H2 focus만 쓴다.
    assert 'role="status"' not in interrupted
    assert 'role="alert"' not in interrupted
    assert "aria-live" not in interrupted
    assert "progressStatus.textContent" not in interrupted_script
    assert "progressStatus.removeAttribute('role')" in interrupted_script
    assert "progressStatus.removeAttribute('aria-live')" in interrupted_script
    assert "progressStatus.removeAttribute('aria-atomic')" in interrupted_script
    assert "interruptedTitle.focus()" in interrupted_script


def test_진행_상태_알림은_화면_배치를_밀지_않는다():
    css = STYLE.read_text(encoding="utf-8")

    assert ".sr-only {" in css
    assert "clip-path: inset(50%);" in css
    assert "white-space: nowrap;" in css


def test_완료_표시는_승인색이_아닌_중립_회색과_체크를_함께_쓴다():
    html = TEMPLATE.read_text(encoding="utf-8")
    css = STYLE.read_text(encoding="utf-8")

    # 색을 구별하지 못해도 체크(완료)·회전 테두리(진행)·글자 상태로 알아볼 수 있고,
    # 상태 안내는 별도의 live region/aria-current 계약을 그대로 유지한다.
    assert '<span class="mark" aria-hidden="true">' in html
    assert "mark.textContent = '✓'" in html
    assert "state.textContent = '완료'" in html
    assert "state.textContent = '진행 중'" in html
    assert "state.textContent = '대기'" in html
    assert "--progress-done: #5f6368;" in css
    assert "background: var(--progress-done);" in css
    assert "border-color: var(--progress-done);" in css
    assert ".phase-cards li.done .mark { background: var(--ok)" not in css
    # 움직임을 줄인 설정에서는 회전을 멈추고 테두리로만 표시한다.
    assert "prefers-reduced-motion: reduce" in css


def test_회사분석_진행_API는_레거시_공고단계를_노출하지_않는다():
    job_id = "3" * 32
    job_runtime._JOBS[job_id] = SimpleNamespace(
        done_steps=["identify", "posting"],
        current_step="posting",
        finished=False,
        report_persisted=None,
        persistence_warning="",
    )
    try:
        with TestClient(main.app) as client:
            _set_admin_cookie(client)
            response = client.get(f"/api/progress/{job_id}")
    finally:
        job_runtime._JOBS.pop(job_id, None)

    assert response.status_code == 200
    assert response.json()["done"] == ["identify"]
    assert response.json()["current"] == ""


def test_끝난_작업은_결과_주소로_보낸다_실패도_같은_동선이다():
    """실패한 실행도 /result로 이동해 기존 중단 안내(stopped) 화면으로 이어진다."""
    job_id = "4" * 32
    job_runtime._JOBS[job_id] = SimpleNamespace(
        done_steps=[key for key, _label in PROGRESS_STEPS],
        current_step="",
        finished=True,
        report_persisted=None,
        persistence_warning="",
    )
    try:
        with TestClient(main.app) as client:
            _set_admin_cookie(client)
            response = client.get(f"/api/progress/{job_id}")
    finally:
        job_runtime._JOBS.pop(job_id, None)

    assert response.status_code == 200
    assert response.json()["finished"] is True
    assert response.json()["next_url"] == f"/result/{job_id}"
