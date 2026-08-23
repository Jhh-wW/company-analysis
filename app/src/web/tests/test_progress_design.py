"""진행 화면의 시각 표시와 보조기술 상태가 같이 바뀌는지 확인한다."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.web import job_runtime, main


WEB = Path(__file__).parents[1]
TEMPLATE = WEB / "templates" / "progress.html"
STYLE = WEB / "static" / "style.css"


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
    assert "보통 1~3분" not in html and "최대 5분" not in html
    assert "상태에 따라 걸리는 시간이 달라질 수 있습니다" in html
    assert "비용이 들지 않습니다" not in html


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

    # 색을 구별하지 못해도 체크 모양으로 완료를 알아볼 수 있고, 상태 안내는
    # 별도의 live region/aria-current 계약을 그대로 유지한다.
    assert '<span class="mark" aria-hidden="true">' in html
    assert "li.querySelector('.mark').textContent = '✓'" in html
    assert "--progress-done: #5f6368;" in css
    assert "background: var(--progress-done);" in css
    assert "border-color: var(--progress-done);" in css
    assert ".steps li.done .mark { background: var(--ok)" not in css


def test_회사분석_진행_API는_레거시_공고단계를_노출하지_않는다():
    job_id = "company-only-progress"
    job_runtime._JOBS[job_id] = SimpleNamespace(
        done_steps=["identify", "posting"],
        current_step="posting",
        finished=False,
        report_persisted=None,
        persistence_warning="",
    )
    try:
        with TestClient(main.app) as client:
            response = client.get(f"/api/progress/{job_id}")
    finally:
        job_runtime._JOBS.pop(job_id, None)

    assert response.status_code == 200
    assert response.json()["done"] == ["identify"]
    assert response.json()["current"] == ""
