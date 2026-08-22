from __future__ import annotations

import json
import sqlite3

from src.features.release_acceptance.logic import (
    AcceptanceReport,
    CheckResult,
    CheckStatus,
    HttpResponse,
    LeakTracker,
    build_child_environment,
    contains_sensitive_marker,
    extract_input_value,
    overall_status,
    render_korean_summary,
    storage_snapshot,
)


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
