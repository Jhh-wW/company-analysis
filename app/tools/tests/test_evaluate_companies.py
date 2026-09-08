"""유료 공급자 없이 공식 HTTP 경계·복구·관측값의 정직성을 검증한다."""

import html
import io
import json
import os
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from src.features.pilot_evaluation.runner import LedgerResult, PilotBatchBlocked
from tools.evaluate_companies import (
    EvaluationError, HttpEvaluation, diagnostic_metrics, digest, load_manifest,
    protect_session, text_metrics,
)
from tools.evaluation_constants import FEATURE_KEYS, MANIFEST_SCHEMA

ORIGIN = "http://127.0.0.1:8020"
RUN_ID = "a" * 32
TOKEN = "비밀_저장금지_토큰"


def form(action, fields):
    return '<form action="' + action + '">' + "".join(
        f'<input name="{name}" value="{html.escape(value)}">' for name, value in fields.items()
    ) + "</form>"


def manifest_value():
    return {"schema_version": MANIFEST_SCHEMA, "cases": [{
        "case_id": "CUSTOM", "input_name": "예제", "expected_legal_name": "예제",
        "corp_code": "00126380", "identity_confirmed": True,
    }]}


@pytest.fixture
def harness(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(manifest_value()), encoding="utf-8")
    settings = tmp_path / "evaluation-settings.json"
    settings.write_text(json.dumps({
        "schema_version": "company-evaluation-settings-v1", "origin": ORIGIN,
        "settings": {key: "0" for key in FEATURE_KEYS}, "paid_providers_enabled": True,
        "code_identity_verified": True, "execution_source_clean": True, "app_git_commit": "e" * 40,
        "production_parity": "not_verified",
    }), encoding="utf-8")
    settings.with_suffix(".sha256").write_text(digest(settings.read_bytes()), encoding="utf-8")
    storage = tmp_path / "storage.db"
    with sqlite3.connect(storage) as connection:
        connection.execute("CREATE TABLE storage_database_identity (singleton_id INTEGER, identity TEXT)")
        connection.execute("INSERT INTO storage_database_identity VALUES (1, '고정신원')")
        connection.execute("CREATE TABLE reports (report_id TEXT, payload_json TEXT)")
    calls = []
    controls = {"mismatch": False, "fail_run": False}

    def transport(request):
        calls.append((request.method, request.url.path))
        if request.url.path == "/":
            content = form("/confirm", {"csrf_token": "b" * 64, "evaluation_workflow_id": "c" * 32})
            content += '<input name="evaluation_paid_consent" type="checkbox" value="yes">'
            return httpx.Response(200, text=content, headers={"content-type": "text/html"})
        if request.url.path == "/confirm":
            code = "99999999" if controls["mismatch"] else "00126380"
            content = f'<div class="company-card" data-dart-corp-code="{code}"></div><div class="legal">예제</div>'
            content += form("/run", {"csrf_token": "b" * 64, "company": "예제", "region": "",
                                     "paid_attempt_token": TOKEN, "evaluation_consent_grant": TOKEN})
            return httpx.Response(200, text=content, headers={"content-type": "text/html"})
        if request.url.path == "/run":
            if controls["fail_run"]:
                raise httpx.ReadTimeout(TOKEN, request=request)
            return httpx.Response(303, headers={"location": f"/progress/{RUN_ID}",
                                               "set-cookie": f"report_access=protected; Path=/"})
        if request.url.path == f"/api/progress/{RUN_ID}":
            return httpx.Response(200, json={"finished": True, "next_url": f"/result/{RUN_ID}"})
        if request.url.path == f"/result/{RUN_ID}":
            return httpx.Response(200, text=TOKEN, headers={"content-type": "text/html"})
        if request.url.path == f"/download/pdf/{RUN_ID}":
            from pypdf import PdfWriter
            writer = PdfWriter()
            writer.add_blank_page(width=600, height=800)
            buffer = io.BytesIO()
            writer.write(buffer)
            return httpx.Response(200, content=buffer.getvalue(), headers={"content-type": "application/pdf"})
        raise AssertionError("예상하지 않은 HTTP 경로")

    monkeypatch.setattr("tools.evaluate_companies.protect_session", lambda data, **kwargs: b"enc:" + data if not kwargs.get("decrypt") else data[4:])
    monkeypatch.setattr("tools.evaluate_companies.source_receipt", lambda: {"source_sha256": "test-source"})
    client = httpx.Client(base_url=ORIGIN, transport=httpx.MockTransport(transport), follow_redirects=False)

    def build():
        runner = HttpEvaluation(origin=ORIGIN, storage=storage, settings=settings, manifest=manifest,
                                client=client, poll_timeout=1, poll_interval=0.01)
        monkeypatch.setattr(runner.bridge, "_validate_storage", lambda: None)
        monkeypatch.setattr(runner.bridge, "_lifecycle_ids", lambda: set())
        monkeypatch.setattr(runner.bridge, "_single_new_lifecycle_id", lambda *args, **kwargs: RUN_ID)
        monkeypatch.setattr(runner.bridge, "_wait_for_ledger", lambda _: LedgerResult(
            "GATE_STOPPED", 12.5, False, "", "", "", "evidence_insufficient"))
        return runner

    yield build, calls, controls, manifest, settings
    client.close()


def test_preflight_never_posts(harness):
    build, calls, _, _, _ = harness
    assert build().operate()["paid_posts"] == 0
    assert calls == [("GET", "/")]


@pytest.fixture
def asgi_confirm_boundary(harness, monkeypatch):
    """실제 앱의 CSRF·동의 경계까지만 통과하고 외부 전송은 금지한다."""
    from fastapi.testclient import TestClient
    from src.features.auth import constants as auth_constants
    from src.web import deployment_mode, evaluation_mode, job_runtime, runtime
    from src.web.main import app

    _, _, _, manifest, settings = harness
    external_calls = []

    def forbidden_external_call(*args, **kwargs):
        external_calls.append(True)
        raise AssertionError("ASGI 경계 회귀에서 공급자나 실제 소켓을 호출했습니다")

    monkeypatch.setenv(evaluation_mode.ENV_MODE, "1")
    monkeypatch.setenv(evaluation_mode.ENV_PAID_PROVIDERS, "1")
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "0")
    monkeypatch.delenv(deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT, raising=False)
    monkeypatch.setattr(job_runtime, "_ACCEPTING_JOBS", True)
    monkeypatch.setattr(runtime, "_PIPELINE", SimpleNamespace(
        find_company_metered=forbidden_external_call,
        run=forbidden_external_call,
    ))
    monkeypatch.setattr(socket, "create_connection", forbidden_external_call)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden_external_call)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_external_call)
    # lifespan을 시작하지 않아 실제 엔진·공급자 키·서버 시작 작업을 불러오지 않는다.
    client = TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 50123),
                        follow_redirects=False)
    calls = []
    client.event_hooks["request"].append(
        lambda request: calls.append((request.method, request.url.path))
    )

    def build():
        runner = HttpEvaluation(origin=ORIGIN, storage=manifest.parent / "storage.db",
                                settings=settings, manifest=manifest, client=client)
        monkeypatch.setattr(runner.bridge, "_validate_storage", lambda: None)
        monkeypatch.setattr(runner.bridge, "_lifecycle_ids", lambda: set())
        return runner

    try:
        yield build, client, calls, external_calls
    finally:
        client.close()


@pytest.mark.parametrize("bad_origin", [None, "null", "http://localhost:8020"])
def test_asgi_confirm_rejects_missing_or_wrong_origin_and_preserves_no_retry(
    asgi_confirm_boundary, bad_origin,
):
    from src.features.pilot_evaluation.runner import PilotRunnerError

    build, client, calls, external_calls = asgi_confirm_boundary
    runner = build()
    # 수정 전 요청을 재현한다. 화면 토큰·동의·workflow는 실제 실행기가 채운다.
    if bad_origin is None:
        del client.headers["Origin"]
    else:
        client.headers["Origin"] = bad_origin
    with pytest.raises(PilotRunnerError):
        runner.operate(execute=True)
    row = json.loads(runner.checkpoint_path.read_text(encoding="utf-8"))["cases"]["CUSTOM"]
    assert row["state"] == "identity_submission_uncertain"
    assert row["last_http_response"] == {"method": "POST", "path": "/confirm", "status": 403}
    # Origin을 수정해도 불확실한 이전 POST를 자동으로 반복하지 않는다.
    with pytest.raises(EvaluationError, match="재전송"):
        build().operate(execute=True)
    assert calls.count(("POST", "/confirm")) == 1
    assert ("POST", "/run") not in calls
    assert external_calls == []


@pytest.mark.parametrize("initial_origin", [None, "null", "http://localhost:8020"])
def test_evaluator_sets_exact_origin_and_passes_real_asgi_csrf_boundary(
    asgi_confirm_boundary, initial_origin,
):
    build, client, calls, external_calls = asgi_confirm_boundary
    if initial_origin is not None:
        client.headers["Origin"] = initial_origin
    runner = build()
    assert client.headers.get_list("Origin") == [ORIGIN]
    workflow = runner.bridge._workflow_page()
    data = {"company": "예제", "csrf_token": workflow.csrf_token,
            "evaluation_workflow_id": workflow.workflow_id}
    runner.state = {"cases": {"CUSTOM": {"state": "pending"}}}
    # 실제 화면 토큰은 CSRF를 통과한다. 유료 동의를 보내지 않아 다음 경계에서 멈춘다.
    response = runner.post("/confirm", data, "CUSTOM")
    assert response.status_code == 422
    assert "외부 호출 확인이 필요합니다" in response.text
    forged = runner.post("/confirm", {**data, "csrf_token": "0" * 64}, "CUSTOM")
    assert forged.status_code == 403
    assert calls.count(("POST", "/confirm")) == 2
    assert ("POST", "/run") not in calls
    assert external_calls == []


def test_http_status_is_durable_before_session_save_failure(harness, monkeypatch):
    build, calls, _, _, _ = harness
    runner = build()
    save_session = runner.save_session

    def fail_after_confirm():
        if ("POST", "/confirm") in calls:
            raise RuntimeError(TOKEN)
        save_session()

    monkeypatch.setattr(runner, "save_session", fail_after_confirm)
    with pytest.raises(RuntimeError):
        runner.operate(execute=True)
    checkpoint = runner.checkpoint_path.read_text(encoding="utf-8")
    row = json.loads(checkpoint)["cases"]["CUSTOM"]
    assert row["state"] == "identity_submission_uncertain"
    assert row["last_http_response"] == {"method": "POST", "path": "/confirm", "status": 200}
    assert TOKEN not in checkpoint
    with pytest.raises(EvaluationError, match="재전송"):
        build().operate(execute=True)
    assert calls.count(("POST", "/confirm")) == 1
    assert ("POST", "/run") not in calls


def test_confirmed_arbitrary_case_runs_official_http_and_respects_terminal_checkpoint(harness):
    build, calls, _, _, _ = harness
    runner = build()
    result = runner.operate(execute=True)
    assert result["cases"]["CUSTOM"]["state"] == "complete"
    assert [(method, path) for method, path in calls if method == "POST"] == [("POST", "/confirm"), ("POST", "/run")]
    output = Path(result["cases"]["CUSTOM"]["artifacts"])
    assert json.loads((output / "ledger.json").read_text(encoding="utf-8"))["cost_krw"] == 12.5
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    assert metrics["human_quality_judgment"] is None
    assert metrics["news_search_count"] is None
    assert all(TOKEN not in path.read_text(encoding="utf-8") for path in output.glob("*.json"))
    build().operate(execute=True)
    assert sum(method == "POST" for method, _ in calls) == 2


def test_identity_mismatch_never_generates(harness):
    build, calls, controls, _, _ = harness
    controls["mismatch"] = True
    with pytest.raises(EvaluationError, match="법인 확인"):
        build().operate(execute=True)
    assert ("POST", "/run") not in calls


def test_missing_baseline_is_rejected_before_any_http(harness):
    build, calls, _, manifest, _ = harness
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["cases"][0]["baseline_pdf"] = "missing.pdf"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(EvaluationError, match="유료 요청 전에"):
        build()
    assert calls == []


def test_uncertain_paid_post_is_durable_and_never_retried(harness):
    build, calls, controls, _, _ = harness
    controls["fail_run"] = True
    with pytest.raises(PilotBatchBlocked):
        build().operate(execute=True)
    runner = build()
    state = json.loads(runner.checkpoint_path.read_text(encoding="utf-8"))
    assert state["cases"]["CUSTOM"]["state"] == "run_submission_uncertain"
    with pytest.raises(EvaluationError, match="재전송"):
        runner.operate(execute=True)
    assert calls.count(("POST", "/run")) == 1


def test_running_resume_uses_get_only_and_restores_session(harness, monkeypatch):
    build, calls, _, _, _ = harness
    runner = build()
    monkeypatch.setattr(runner, "finish", lambda *args: (_ for _ in ()).throw(EvaluationError("대기 시간")))
    with pytest.raises(EvaluationError):
        runner.operate(execute=True)
    count = len(calls)
    runner.client.cookies.clear()
    resumed = build()
    result = resumed.operate(resume_only=True)
    assert result["cases"]["CUSTOM"]["state"] == "complete"
    assert all(method == "GET" for method, _ in calls[count:])
    assert resumed.client.cookies.get("report_access") == "protected"


def test_settings_tamper_and_database_replacement_block_resume(harness):
    build, _, _, _, settings = harness
    runner = build()
    runner.operate()
    with sqlite3.connect(runner.storage) as connection:
        connection.execute("UPDATE storage_database_identity SET identity='교체됨'")
    with pytest.raises(EvaluationError, match="자동 재개"):
        build().operate()
    settings.write_bytes(settings.read_bytes() + b" ")
    with pytest.raises(EvaluationError, match="SHA-256"):
        build()


def test_unverified_paid_server_epoch_is_rejected_before_http(harness):
    build, calls, _, _, settings = harness
    value = json.loads(settings.read_text(encoding="utf-8"))
    value["code_identity_verified"] = False
    settings.write_text(json.dumps(value), encoding="utf-8")
    settings.with_suffix(".sha256").write_text(digest(settings.read_bytes()), encoding="utf-8")
    with pytest.raises(EvaluationError, match="커밋 신원"):
        build()
    assert calls == []


def test_report_artifacts_use_official_pdf_and_same_baseline_extractor(harness, monkeypatch):
    build, calls, _, manifest, _ = harness
    from pypdf import PdfWriter
    baseline = manifest.parent / "baseline.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=600, height=800)
    writer.write(str(baseline))
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["cases"][0]["baseline_pdf"] = str(baseline)
    manifest.write_text(json.dumps(value), encoding="utf-8")
    runner = build()
    with sqlite3.connect(runner.storage) as connection:
        connection.execute("INSERT INTO reports VALUES (?, ?)", (RUN_ID, json.dumps({"sections": []})))
    monkeypatch.setattr(runner.bridge, "_wait_for_ledger", lambda _: LedgerResult(
        "REPORT", 12.5, False, RUN_ID, "00126380", "d" * 64, ""))
    result = runner.operate(execute=True)
    output = Path(result["cases"]["CUSTOM"]["artifacts"])
    metrics = json.loads((output / "metrics.json").read_text(encoding="utf-8"))
    assert ("GET", f"/download/pdf/{RUN_ID}") in calls
    assert (output / "report.json").is_file() and (output / "report.pdf").is_file()
    assert metrics["report_pdf"]["pages"] == metrics["baseline_pdf"]["pages"] == 1
    assert set(metrics["report_pdf"]) == set(metrics["baseline_pdf"])


def test_unsettled_cost_saves_evidence_and_blocks_completion(harness, monkeypatch):
    build, calls, _, _, _ = harness
    runner = build()
    monkeypatch.setattr(runner.bridge, "_wait_for_ledger", lambda _: LedgerResult(
        "GATE_STOPPED", 12.5, True, "", "", "", "evidence_insufficient"))
    with pytest.raises(EvaluationError, match="미정산"):
        runner.operate(execute=True)
    output = runner.root / "http-evaluation-artifacts" / "main" / "CUSTOM"
    assert json.loads((output / "ledger.json").read_text(encoding="utf-8"))["billing_uncertain"] is True
    assert (output / "interruption.json").is_file()
    assert runner.state["cases"]["CUSTOM"]["state"] == "running"


@pytest.mark.parametrize("change", ["unconfirmed", "missing_code", "duplicate"])
def test_manifest_must_be_confirmed_and_unique(tmp_path, change):
    value = manifest_value()
    if change == "unconfirmed":
        value["cases"][0]["identity_confirmed"] = False
    elif change == "missing_code":
        value["cases"][0]["corp_code"] = ""
    else:
        value["cases"].append(dict(value["cases"][0]))
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(EvaluationError):
        load_manifest(path)


@pytest.mark.parametrize("usage_step", ["8_뉴스_본문활용", "뉴스_본문활용"])
def test_metrics_separate_observed_zero_from_missing_and_manual_judgment(usage_step):
    assert diagnostic_metrics({})["news_body_used_count"] is None
    value = diagnostic_metrics({"observability_run_steps": [{"steps_json": json.dumps([
        {"step": "5b_뉴스_수집", "검색": 42, "본문읽기": 7, "실패": None},
        {"step": usage_step, "본문사용기사수": 0, "목록기사수": 3},
    ])}]})
    assert value["news_search_count"] == 42
    assert value["news_body_used_count"] == 0
    assert value["independent_news_article_count"] == 3
    assert value["news_distinct_event_count"] is None
    line = "2026-09-08 자료를 확인한 예제 회사의 뉴스입니다"
    metrics = text_metrics(line + "\n" + line, ("경쟁사",))
    assert metrics["repeated_line_occurrences"] == 1
    assert metrics["date_mentions"] == 2
    assert metrics["human_quality_judgment"] is None


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI 계약")
def test_real_dpapi_roundtrip_keeps_plaintext_out_of_session_file():
    raw = b"report-access-secret-value"
    protected = protect_session(raw)
    assert raw not in protected
    assert protect_session(protected, decrypt=True) == raw
