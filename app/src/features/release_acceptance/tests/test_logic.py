from __future__ import annotations

import http.server
import json
import socket
import sqlite3
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Iterator

import pytest

from src.features.release_acceptance.logic import (
    AcceptanceFailure,
    AcceptanceReport,
    CheckResult,
    CheckStatus,
    HttpSession,
    HttpResponse,
    LeakTracker,
    build_child_environment,
    contains_sensitive_marker,
    extract_input_value,
    extract_issued_share_url,
    issued_share_link_from_screen,
    overall_status,
    render_korean_summary,
    storage_snapshot,
)


class _LocalHttpServer(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, body: bytes):
        super().__init__(("127.0.0.1", 0), _FixedResponseHandler)
        self.body = body
        self.request_paths: list[str] = []


class _FixedResponseHandler(http.server.BaseHTTPRequestHandler):
    server: _LocalHttpServer

    def do_GET(self) -> None:  # noqa: N802 - 표준 라이브러리 handler 계약
        self.server.request_paths.append(self.path)
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(self.server.body)))
        self.end_headers()
        self.wfile.write(self.server.body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args


@contextmanager
def _serve_local(body: bytes) -> Iterator[_LocalHttpServer]:
    server = _LocalHttpServer(body)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _closed_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _install_malicious_parent_proxy(monkeypatch, proxy_port: int) -> None:
    proxy_url = f"http://127.0.0.1:{proxy_port}"
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ):
        monkeypatch.setenv(name, proxy_url)
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    # 플랫폼별 localhost 자동 우회를 제거해 부모 프록시 상속 공격을 재현한다.
    monkeypatch.setattr(urllib.request, "proxy_bypass", lambda _host: False)


def _check(status: CheckStatus, evidence: str = "근거") -> CheckResult:
    return CheckResult(
        check_id="sample",
        title="표본",
        status=status,
        evidence=evidence,
    )


def test_전체판정은_fail_blocked_pass_우선순위를_지킨다() -> None:
    assert overall_status((_check(CheckStatus.PASS),)) is CheckStatus.PASS
    assert overall_status(
        (_check(CheckStatus.PASS), _check(CheckStatus.BLOCKED))
    ) is CheckStatus.BLOCKED
    assert overall_status(
        (_check(CheckStatus.BLOCKED), _check(CheckStatus.FAIL))
    ) is CheckStatus.FAIL


def test_자식환경은_os_allowlist와_명시값만_남긴다() -> None:
    parent = {
        "SystemRoot": r"C:\Windows",
        "PATH": r"C:\Python",
        "ANTHROPIC_API_KEY": "부모-비밀",
        "UNRELATED_SECRET": "또다른-비밀",
    }
    child = build_child_environment(
        parent,
        {"PIPELINE": "demo", "STORAGE_DB_PATH": r"C:\tmp\storage.db"},
    )

    assert child == {
        "SystemRoot": r"C:\Windows",
        "PATH": r"C:\Python",
        "PIPELINE": "demo",
        "STORAGE_DB_PATH": r"C:\tmp\storage.db",
    }


def test_html_input은_요청한_숨은값만_읽는다() -> None:
    html = (
        '<input name="csrf_token" value="csrf-1">'
        '<input name="paid_attempt_token" value="attempt-1">'
    )
    assert extract_input_value(html, "paid_attempt_token") == "attempt-1"
    assert extract_input_value(html, "missing") == ""


#: 발급 결과 «화면»의 주소 칸 구조. 실제 템플릿(`web/templates/admin_link_issued.html`)
#: 과 같은 앵커를 쓰며, 실제 렌더 결과로도 읽히는지는 web 쪽 시험이 함께 지킨다.
_ISSUE_SCREEN_KEY = "0123456789abcdef0123456789abcdef"


def _issue_screen_html(url: str, *, anchors: int = 1) -> str:
    anchor = f'  <p id="issued-link-url" style="word-break:break-all">{url}</p>\n'
    return (
        "<!doctype html>\n<html lang=\"ko\"><body>\n"
        "<div class=\"card\"><h2>받은 사람에게 전달할 주소</h2>\n"
        + anchor * anchors
        + '<svg class="segno"><path d="M0 0h1"/></svg>\n'
        "</div></body></html>\n"
    )


def test_수락시험은_발급_화면의_주소_앵커에서_링크를_읽는다() -> None:
    """★ 발급 응답이 텍스트 파일에서 HTML 화면 1장으로 바뀌었다.

    본문 전체를 주소로 파싱하면 scheme이 빈 값이 되어 loopback 검사에서 튕긴다.
    실서버 없이 이 파싱만 떼어 확인한다.
    """
    url = f"http://127.0.0.1:8123/k/{_ISSUE_SCREEN_KEY}"

    assert extract_issued_share_url(_issue_screen_html(url)) == url
    assert issued_share_link_from_screen(_issue_screen_html(url), port=8123) == (
        f"/k/{_ISSUE_SCREEN_KEY}",
        _ISSUE_SCREEN_KEY,
    )


def test_발급_화면에_주소_앵커가_없거나_비었거나_여럿이면_실패한다() -> None:
    """★ 「못 읽었다」를 조용히 빈 값으로 넘기면 뒤에서 엉뚱한 이유로 깨진다."""
    url = f"http://127.0.0.1:8123/k/{_ISSUE_SCREEN_KEY}"

    with pytest.raises(AcceptanceFailure, match="찾지 못했습니다"):
        extract_issued_share_url("<html><body><p>발급했습니다</p></body></html>")
    with pytest.raises(AcceptanceFailure, match="비어 있습니다"):
        extract_issued_share_url(_issue_screen_html("   "))
    # 주소가 화면에 두 번 나오면 원문을 회수할 곳이 두 곳이 된다.
    with pytest.raises(AcceptanceFailure, match="여러 개"):
        extract_issued_share_url(_issue_screen_html(url, anchors=2))


def test_발급_화면의_주소는_HTML_엔티티를_되돌려_읽는다() -> None:
    """★ 템플릿은 자동 escape 한다 — `&amp;`를 그대로 두면 주소가 달라진다."""
    escaped = f"http://127.0.0.1:8123/k/{_ISSUE_SCREEN_KEY}?a=1&amp;b=2"

    assert extract_issued_share_url(_issue_screen_html(escaped)) == (
        f"http://127.0.0.1:8123/k/{_ISSUE_SCREEN_KEY}?a=1&b=2"
    )
    # query가 붙은 주소는 capability 형식 검사에서 걸러진다.
    with pytest.raises(AcceptanceFailure, match="capability 형식"):
        issued_share_link_from_screen(_issue_screen_html(escaped), port=8123)


def test_sqlite_snapshot은_비용0원과_단일보고서_근거를_읽는다(tmp_path) -> None:
    database = tmp_path / "storage.db"
    records = tmp_path / "observability" / "runs.jsonl"
    report_id = "a" * 32
    with sqlite3.connect(database) as conn:
        conn.executescript(
            """
            CREATE TABLE reports (report_id TEXT, payload_json TEXT);
            CREATE TABLE budget_spend_events (cost_krw REAL);
            CREATE TABLE budget_spend_inflight (reserved_krw REAL);
            CREATE TABLE ai_variable_cost_events (cost_krw REAL);
            CREATE TABLE report_cost_summaries (
                internal_ai_cost_krw REAL,
                customer_charge_krw REAL
            );
            """
        )
        conn.execute(
            "INSERT INTO reports(report_id, payload_json) VALUES (?, ?)",
            (report_id, '{"company":"fixture"}'),
        )
        conn.execute(
            "INSERT INTO report_cost_summaries VALUES (0, 0)"
        )
    records.parent.mkdir(parents=True)
    records.write_text(
        json.dumps({"run_id": report_id}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    snapshot = storage_snapshot(database, records, report_id)

    assert snapshot.complete
    assert snapshot.reports_total == snapshot.target_reports == 1
    assert snapshot.record_lines == snapshot.target_record_lines == 1
    assert snapshot.ai_event_count == snapshot.inflight_count == 0
    assert snapshot.internal_ai_cost_krw == snapshot.customer_charge_krw == 0.0
    assert len(snapshot.target_payload_sha256) == 64


def test_sqlite_snapshot은_원장표가_없으면_complete로_속이지않는다(tmp_path) -> None:
    database = tmp_path / "storage.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE reports (report_id TEXT, payload_json TEXT)")

    snapshot = storage_snapshot(database, tmp_path / "missing.jsonl", "b" * 32)

    assert not snapshot.complete
    assert "ai_variable_cost_events" in snapshot.missing_tables


def test_json과한국어요약은_marker를_그대로_싣지않는다() -> None:
    marker = "release-acceptance-secret-marker"
    report = AcceptanceReport(
        schema_version="test-v1",
        started_at="2026-08-23T00:00:00+00:00",
        finished_at="2026-08-23T00:00:01+00:00",
        overall_status=CheckStatus.PASS,
        mode="isolated-local-demo",
        external_provider_calls_allowed=False,
        checks=(_check(CheckStatus.PASS, "marker 원문 없이 확인"),),
    )
    rendered = report.to_json() + render_korean_summary(report)

    assert not contains_sensitive_marker(rendered, {"canary": marker})
    assert contains_sensitive_marker(marker, {"canary": marker})
    assert json.loads(report.to_json())["external_provider_calls_allowed"] is False


def test_의도된_capability_요청url은_제외해도_응답표면은_계속_검사한다() -> None:
    capability = "c" * 32
    tracker = LeakTracker(markers={"share": capability})
    response = HttpResponse(
        status=303,
        headers={
            "location": "/result/" + "d" * 32,
            "set-cookie": "share_key=" + capability + "; HttpOnly",
        },
        body=b"",
        url="http://127.0.0.1:8010/k/" + capability,
    )

    tracker.observe(
        response,
        "share-open",
        scan_response_url=False,
        ignored_header_names=frozenset({"set-cookie"}),
    )
    assert tracker.leaks == []

    reflected = HttpResponse(
        status=303,
        headers={"location": "/result/" + capability},
        body=b"",
        url=response.url,
    )
    tracker.observe(
        reflected,
        "share-open",
        scan_response_url=False,
        ignored_header_names=frozenset({"set-cookie"}),
    )
    assert tracker.leaks == ["share-open:headers:share"]


def test_NO_PROXY가_비어도_닫힌_loopback실패를_부모proxy가_위조하지못한다(
    monkeypatch,
) -> None:
    with _serve_local(b"forged-proxy-success") as proxy:
        _install_malicious_parent_proxy(monkeypatch, proxy.server_port)
        closed_port = _closed_loopback_port()

        with pytest.raises((OSError, urllib.error.URLError)):
            HttpSession(closed_port).request("GET", "/healthz", timeout=0.5)

        assert proxy.request_paths == []


def test_실제_acceptance응답을_부모proxy가_대체하지못한다(monkeypatch) -> None:
    with _serve_local(b"real-acceptance-response") as target:
        with _serve_local(b"forged-proxy-response") as proxy:
            _install_malicious_parent_proxy(monkeypatch, proxy.server_port)

            response = HttpSession(target.server_port).request("GET", "/healthz")

            assert response.status == 200
            assert response.body == b"real-acceptance-response"
            assert target.request_paths == ["/healthz"]
            assert proxy.request_paths == []
