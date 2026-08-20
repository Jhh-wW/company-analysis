"""명시적 로컬 실시간 성능시험의 UI·환경·요청 경계를 검증한다."""

from __future__ import annotations

import json
import re
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

from fastapi import Request
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from src.core.constants import PIPELINE_ENV, PIPELINE_REAL
from src.features.auth import logic as auth_logic
from src.features.budget.constants import (
    SPEND_PHASE_CANDIDATE,
    SPEND_PHASE_IDENTIFY,
    SPEND_PHASE_OCR,
    SPEND_PHASE_PIPELINE,
)
from src.features.business_candidate import google_places
from src.features.pipeline.port import (
    CompanyCard,
    CompanyLookupResult,
    Grade,
    Outcome,
    Report,
    ReportSection,
    RunResult,
    SourceStatus,
    UserInput,
)
from src.features.posting_image import logic as image_logic
from src.features.sharelink import tracks as share_tracks
from src.features.storage import db as storage_db
from src.web import evaluation_mode, job_runtime, request_helpers, runtime
from src.web.main import app


class _FakeRealPipeline:
    pass


class _JypEvaluationPipeline:
    """실제 네트워크 대신 계약 객체만 돌리는 JYP 전체 흐름 fake."""

    def __init__(self) -> None:
        self.lookup_calls: list[str] = []
        self.run_calls = 0

    def find_company_metered(self, user_input: UserInput) -> CompanyLookupResult:
        self.lookup_calls.append(user_input.company)
        if user_input.company == "JYP":
            return CompanyLookupResult(
                card=None,
                cost_krw=100.0,
                model="fake-dart",
            )
        assert user_input.company == "(주)제이와이피엔터테인먼트"
        return CompanyLookupResult(
            card=CompanyCard(
                legal_name="(주)제이와이피엔터테인먼트",
                typed_name="JYP",
                address="서울특별시 강동구 강동대로 205",
                ceo="정욱",
                founded="19970425",
                homepage="jype.com",
                homepage_url="https://www.jype.com/",
                ref="fake-dart-jyp-001",
            ),
            cost_krw=100.0,
            model="fake-dart",
        )

    def run(self, user_input: UserInput, _card: CompanyCard, on_step=None) -> RunResult:
        self.run_calls += 1
        if on_step is not None:
            on_step("collect")
        report = Report(
            company="(주)제이와이피엔터테인먼트",
            job=user_input.job,
            corp_type="상장사",
            grade=Grade.PARTIAL,
            sections=[
                ReportSection(
                    cell="1",
                    title="회사 이해",
                    lines=[("JYP fake E2E 검증 문장입니다.", "격리 시험 근거")],
                )
            ],
            sources=[SourceStatus("DART", "ok", "fake adapter")],
            cells={"1": True},
            generated_at="2026-08-18T12:00:00+09:00",
        )
        return RunResult(
            outcome=Outcome.REPORT,
            report=report,
            sources=report.sources,
            charged=True,
            cost_krw=900.0,
            model="fake-anthropic",
        )


class _FakePlacesResponse:
    status = 200

    def geturl(self) -> str:
        return google_places.ENDPOINT

    def read(self, _limit: int) -> bytes:
        return json.dumps(
            {
                "places": [
                    {
                        "id": "fake-jyp-place",
                        "displayName": {
                            "text": "(주)제이와이피엔터테인먼트"
                        },
                        "formattedAddress": "서울특별시 강동구 강동대로 205",
                        "websiteUri": "https://www.jype.com/",
                        "businessStatus": "OPERATIONAL",
                        "attributions": [],
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")

    def close(self) -> None:
        return None


def _valid_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), color=(255, 255, 255)).save(output, "PNG")
    return output.getvalue()


_VALID_PNG = _valid_png()


def _set_evaluation_environment(monkeypatch, *, paid: bool) -> None:
    monkeypatch.setenv(evaluation_mode.ENV_MODE, "1")
    monkeypatch.setenv(
        evaluation_mode.ENV_PAID_PROVIDERS, "1" if paid else "0"
    )
    monkeypatch.setenv(PIPELINE_ENV, PIPELINE_REAL)
    monkeypatch.setenv(evaluation_mode.ENV_DISABLE_ENGINE_DOTENV, "1")
    monkeypatch.setenv(evaluation_mode.ENV_PER_RUN_CAP_KRW, "1200")
    monkeypatch.setenv(evaluation_mode.ENV_DAILY_CAP_KRW, "2200")
    if paid:
        for name in evaluation_mode.REQUIRED_PROVIDER_ENV_NAMES:
            monkeypatch.setenv(name, f"unit-test-{name.lower()}")
        monkeypatch.setenv(
            evaluation_mode.GOOGLE_PLACES_KEY_ENV,
            "unit-test-google-places-api-key",
        )
        monkeypatch.setenv("GOOGLE_PLACES_BILLING_ACK", "1")
        monkeypatch.setenv("GOOGLE_PLACES_TERMS_ACK", "yes")
        monkeypatch.setenv("BUSINESS_CANDIDATE_PROVIDER", "google_places")
    else:
        monkeypatch.setenv("BUSINESS_CANDIDATE_PROVIDER", "disabled")


def _request(
    *, origin: str | None = "http://127.0.0.1:8020", forwarded: bool = False
) -> Request:
    headers = [(b"host", b"127.0.0.1:8020")]
    if origin is not None:
        headers.append((b"origin", origin.encode("ascii")))
    if forwarded:
        headers.append((b"x-forwarded-for", b"203.0.113.8"))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "scheme": "http",
            "method": "POST",
            "path": "/confirm",
            "raw_path": b"/confirm",
            "query_string": b"",
            "headers": headers,
            "server": ("127.0.0.1", 8020),
            "client": ("127.0.0.1", 50123),
        }
    )


def _hidden(html: str, name: str) -> str:
    match = re.search(
        rf'<input[^>]*name="{re.escape(name)}"[^>]*value="([^"]*)"', html
    )
    assert match is not None, f"숨은 입력 {name!r}을 찾지 못했습니다"
    return match.group(1)


def test_preview_ui_is_explicit_and_submit_is_locked(monkeypatch) -> None:
    _set_evaluation_environment(monkeypatch, paid=False)
    monkeypatch.setattr(runtime, "_PIPELINE", _FakeRealPipeline())

    with TestClient(
        app,
        base_url="http://127.0.0.1:8020",
        client=("127.0.0.1", 50123),
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.text.count("실시간 성능시험") >= 2
    assert "외부 서비스 호출이 0건" in response.text
    assert "미리보기 · 외부 조사 잠김" in response.text
    assert 'id="confirmSubmitButton"' in response.text
    assert "disabled" in response.text
    assert 'name="evaluation_paid_consent"' not in response.text
    csrf = re.search(r'name="csrf_token" value="([0-9a-f]{64})"', response.text)
    assert csrf is not None


def test_paid_ui_requires_explicit_browser_confirmation(monkeypatch) -> None:
    _set_evaluation_environment(monkeypatch, paid=True)
    monkeypatch.setattr(runtime, "_PIPELINE", _FakeRealPipeline())

    with TestClient(
        app,
        base_url="http://127.0.0.1:8020",
        client=("127.0.0.1", 50123),
    ) as client:
        response = client.get("/")

    assert response.status_code == 200
    assert "실제 비용이 발생할 수 있습니다" in response.text
    assert "1건 1200원" in response.text
    assert "하루 2200원" in response.text
    assert "hard cap이 아니라" in response.text
    assert 'name="evaluation_paid_consent" value="yes" required' in response.text
    assert re.search(
        r'name="evaluation_workflow_id"\s+value="[0-9a-f]{32}"',
        response.text,
    )


def test_paid_configuration_fails_closed_without_google_ack(monkeypatch) -> None:
    _set_evaluation_environment(monkeypatch, paid=True)
    monkeypatch.setenv("GOOGLE_PLACES_BILLING_ACK", "0")

    try:
        evaluation_mode.validate_startup_configuration()
    except evaluation_mode.EvaluationConfigurationError as exc:
        assert "GOOGLE_PLACES_BILLING_ACK=1" in str(exc)
    else:  # pragma: no cover - 실패 메시지를 더 분명하게 보이기 위한 분기
        raise AssertionError("Google Places 비용 동의 없이 시작 설정이 통과했습니다")


def test_paid_configuration_allows_google_disabled_without_google_key(
    monkeypatch,
) -> None:
    _set_evaluation_environment(monkeypatch, paid=True)
    monkeypatch.setenv("BUSINESS_CANDIDATE_PROVIDER", "disabled")
    monkeypatch.delenv(evaluation_mode.GOOGLE_PLACES_KEY_ENV, raising=False)
    monkeypatch.delenv("GOOGLE_PLACES_BILLING_ACK", raising=False)
    monkeypatch.delenv("GOOGLE_PLACES_TERMS_ACK", raising=False)

    current = evaluation_mode.validate_startup_configuration()

    assert current.paid_providers_enabled is True


def test_google_candidate_mode_requires_separate_terms_ack(monkeypatch) -> None:
    _set_evaluation_environment(monkeypatch, paid=True)
    monkeypatch.setenv("GOOGLE_PLACES_TERMS_ACK", "no")

    with pytest.raises(
        evaluation_mode.EvaluationConfigurationError,
        match="GOOGLE_PLACES_TERMS_ACK=yes",
    ):
        evaluation_mode.validate_startup_configuration()


def test_evaluation_anonymous_budget_exists_only_for_direct_loopback(monkeypatch) -> None:
    _set_evaluation_environment(monkeypatch, paid=True)

    track, bucket, cap = request_helpers._track_of(_request())
    assert (track, bucket, cap) == (
        share_tracks.Track.ADMIN,
        evaluation_mode.LOCAL_BUCKET,
        2200.0,
    )

    forwarded_track, _bucket, forwarded_cap = request_helpers._track_of(
        _request(forwarded=True)
    )
    assert forwarded_track is share_tracks.Track.PUBLIC
    assert forwarded_cap == 0


def test_evaluation_csrf_requires_nonempty_exact_origin_and_direct_loopback(
    monkeypatch,
) -> None:
    _set_evaluation_environment(monkeypatch, paid=True)

    csrf = auth_logic.csrf_token_for_session(evaluation_mode.csrf_secret())
    assert request_helpers.require_analysis_action_csrf(_request(), csrf) is None
    assert request_helpers.require_analysis_action_csrf(_request(), "").status_code == 403
    assert (
        request_helpers.require_analysis_action_csrf(_request(origin=None), csrf)
        .status_code
        == 403
    )
    assert (
        request_helpers.require_analysis_action_csrf(_request(origin="null"), csrf)
        .status_code
        == 403
    )
    assert (
        request_helpers.require_analysis_action_csrf(
            _request(origin="http://attacker.invalid"), csrf
        ).status_code
        == 403
    )
    assert (
        request_helpers.require_analysis_action_csrf(_request(forwarded=True), csrf)
        .status_code
        == 403
    )


def test_preview_guard_blocks_before_any_provider_path(monkeypatch) -> None:
    _set_evaluation_environment(monkeypatch, paid=False)

    response = request_helpers._guard_run(_request(), count_start=False)

    assert response is not None
    assert response.status_code == 429
    assert evaluation_mode.PREVIEW_BLOCKED_MESSAGE in response.body.decode("utf-8")


def test_paid_server_consent_is_exact_and_fail_closed(monkeypatch) -> None:
    _set_evaluation_environment(monkeypatch, paid=True)

    missing = request_helpers.require_evaluation_consent(_request(), "")
    wrong = request_helpers.require_evaluation_consent(_request(), "true")

    assert missing is not None and missing.status_code == 422
    assert wrong is not None and wrong.status_code == 422
    assert "외부 호출 확인이 필요합니다" in missing.body.decode("utf-8")
    assert request_helpers.require_evaluation_consent(_request(), "yes") is None


def test_consent_grant_is_signed_bound_and_short_lived(monkeypatch) -> None:
    _set_evaluation_environment(monkeypatch, paid=True)
    fields = {
        "company": "JYP",
        "job": "매니지먼트",
        "region": "서울 강동구",
        "posting_text": "채용 공고 원문",
        "bucket_id": "hashed-evaluation-bucket",
    }
    workflow_id = "a" * evaluation_mode.WORKFLOW_ID_HEX_LENGTH

    grant = evaluation_mode.issue_consent_grant(
        **fields, workflow_id=workflow_id, now=1_000.0
    )

    assert "JYP" not in grant
    assert "서울" not in grant
    assert "채용" not in grant
    assert evaluation_mode.consent_grant_valid(grant, **fields, now=1_001.0)
    assert not evaluation_mode.consent_grant_valid(
        grant, **{**fields, "company": "다른 회사"}, now=1_001.0
    )
    tampered = grant.replace("." + workflow_id + ".", "." + "b" * 32 + ".")
    assert not evaluation_mode.consent_grant_valid(tampered, **fields, now=1_001.0)
    assert not evaluation_mode.consent_grant_valid(
        grant, **fields, expected_transition="run", now=1_001.0
    )
    assert not evaluation_mode.consent_grant_valid(
        grant,
        **fields,
        now=1_000.0 + evaluation_mode.CONSENT_GRANT_TTL_SEC + 1,
    )


def test_evaluation_workflow_nonce_is_atomically_consumed_once(monkeypatch) -> None:
    _set_evaluation_environment(monkeypatch, paid=True)
    workflow_id = evaluation_mode.issue_workflow_id(now=1_000.0)

    with ThreadPoolExecutor(max_workers=8) as pool:
        accepted = list(
            pool.map(
                lambda _index: evaluation_mode.consume_workflow_id(
                    workflow_id, now=1_001.0
                ),
                range(8),
            )
        )

    assert accepted.count(True) == 1
    assert accepted.count(False) == 7


def test_followup_requires_signed_grant_even_if_raw_yes_is_replayed(monkeypatch) -> None:
    _set_evaluation_environment(monkeypatch, paid=True)
    user_input = UserInput("JYP", "매니지먼트", "서울 강동구", "채용 공고")
    workflow_id = evaluation_mode.issue_workflow_id()

    initial_blocked, grant = request_helpers.evaluation_consent_roundtrip(
        _request(),
        user_input=user_input,
        bucket=evaluation_mode.LOCAL_BUCKET,
        received="yes",
        grant="",
        workflow_id=workflow_id,
        allow_issue=True,
    )
    assert initial_blocked is None and grant

    replay_blocked, replay_grant = request_helpers.evaluation_consent_roundtrip(
        _request(),
        user_input=user_input,
        bucket=evaluation_mode.LOCAL_BUCKET,
        received="yes",
        grant=grant,
        workflow_id=workflow_id,
        allow_issue=True,
    )
    assert replay_blocked is not None and replay_blocked.status_code == 422
    assert replay_grant == ""

    followup_blocked, same_grant = request_helpers.evaluation_consent_roundtrip(
        _request(),
        user_input=user_input,
        bucket=evaluation_mode.LOCAL_BUCKET,
        received="",
        grant=grant,
        workflow_id="",
        allow_issue=False,
    )
    assert followup_blocked is None
    assert same_grant == grant


def _jyp_form(csrf: str, **changes: str) -> dict[str, str]:
    form = {
        "company": "JYP",
        "job": "매니지먼트",
        "region": "서울 강동구",
        "posting_text": "채용 공고",
        "csrf_token": csrf,
    }
    form.update(changes)
    return form


def test_direct_post_without_consent_calls_no_provider(monkeypatch) -> None:
    _set_evaluation_environment(monkeypatch, paid=True)
    pipeline = _JypEvaluationPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    transport_calls = 0

    def forbidden_transport(*_args, **_kwargs):
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("동의 없는 POST가 Google provider를 불렀습니다")

    monkeypatch.setattr(google_places, "_urlopen", forbidden_transport)
    with TestClient(
        app,
        base_url="http://127.0.0.1:8020",
        client=("127.0.0.1", 50123),
    ) as client:
        csrf = _hidden(client.get("/").text, "csrf_token")
        response = client.post(
            "/confirm",
            data=_jyp_form(csrf),
            headers={"Origin": "http://127.0.0.1:8020"},
        )

    assert response.status_code == 422
    assert "외부 호출 확인이 필요합니다" in response.text
    assert pipeline.lookup_calls == []
    assert pipeline.run_calls == 0
    assert transport_calls == 0


def test_initial_confirm_workflow_is_one_time_and_signed_grant_cannot_replay_it(
    monkeypatch,
) -> None:
    _set_evaluation_environment(monkeypatch, paid=True)
    pipeline = _JypEvaluationPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    transport_calls = 0

    def forbidden_transport(*_args, **_kwargs):
        nonlocal transport_calls
        transport_calls += 1
        raise AssertionError("초기 /confirm 재생이 Google provider를 불렀습니다")

    monkeypatch.setattr(google_places, "_urlopen", forbidden_transport)
    origin = {"Origin": "http://127.0.0.1:8020"}
    with TestClient(
        app,
        base_url="http://127.0.0.1:8020",
        client=("127.0.0.1", 50123),
    ) as client:
        home = client.get("/")
        csrf = _hidden(home.text, "csrf_token")
        workflow_id = _hidden(home.text, "evaluation_workflow_id")
        initial_form = _jyp_form(
            csrf,
            evaluation_paid_consent="yes",
            evaluation_workflow_id=workflow_id,
        )

        missing_nonce = client.post(
            "/confirm",
            data=_jyp_form(csrf, evaluation_paid_consent="yes"),
            headers=origin,
        )
        first = client.post("/confirm", data=initial_form, headers=origin)
        nonce_replay = client.post("/confirm", data=initial_form, headers=origin)
        signed_replay = client.post(
            "/confirm",
            data=_jyp_form(
                csrf,
                evaluation_consent_grant=_hidden(
                    first.text, "evaluation_consent_grant"
                ),
            ),
            headers=origin,
        )

    assert missing_nonce.status_code == 422
    assert first.status_code == 200
    assert nonce_replay.status_code == 422
    assert signed_replay.status_code == 422
    assert pipeline.lookup_calls == ["JYP"]
    assert pipeline.run_calls == 0
    assert transport_calls == 0


def test_followup_raw_yes_without_signed_grant_calls_no_dart_or_ai(monkeypatch) -> None:
    _set_evaluation_environment(monkeypatch, paid=True)
    pipeline = _JypEvaluationPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    transport_calls = 0

    def fake_transport(_request, _timeout):
        nonlocal transport_calls
        transport_calls += 1
        return _FakePlacesResponse()

    monkeypatch.setattr(google_places, "_urlopen", fake_transport)
    origin = {"Origin": "http://127.0.0.1:8020"}
    with TestClient(
        app,
        base_url="http://127.0.0.1:8020",
        client=("127.0.0.1", 50123),
    ) as client:
        home = client.get("/")
        csrf = _hidden(home.text, "csrf_token")
        workflow_id = _hidden(home.text, "evaluation_workflow_id")
        not_found = client.post(
            "/confirm",
            data=_jyp_form(
                csrf,
                evaluation_paid_consent="yes",
                evaluation_workflow_id=workflow_id,
            ),
            headers=origin,
        )
        candidates = client.post(
            "/confirm",
            data=_jyp_form(
                csrf,
                candidate_search_requested="yes",
                candidate_search_grant=_hidden(
                    not_found.text, "candidate_search_grant"
                ),
                evaluation_consent_grant=_hidden(
                    not_found.text, "evaluation_consent_grant"
                ),
            ),
            headers=origin,
        )
        replay = client.post(
            "/confirm",
            data=_jyp_form(
                csrf,
                candidate_resolution_confirmed="yes",
                candidate_attempt_token=_hidden(
                    candidates.text, "candidate_attempt_token"
                ),
                candidate_selection_token=_hidden(
                    candidates.text, "candidate_selection_token"
                ),
                candidate_index=_hidden(candidates.text, "candidate_index"),
                candidate_name=_hidden(candidates.text, "candidate_name"),
                candidate_provider=_hidden(candidates.text, "candidate_provider"),
                evaluation_paid_consent="yes",
                # 서명 grant를 일부러 빼고 raw checkbox 값만 재생한다.
            ),
            headers=origin,
        )

    assert replay.status_code == 422
    assert pipeline.lookup_calls == ["JYP"]
    assert pipeline.run_calls == 0
    assert transport_calls == 1


def test_jyp_fake_adapters_complete_signed_flow_but_legacy_report_is_gate_stopped(
    monkeypatch,
) -> None:
    _set_evaluation_environment(monkeypatch, paid=True)
    pipeline = _JypEvaluationPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    transport_calls = 0
    ocr_calls = 0

    def fake_transport(request, _timeout):
        nonlocal transport_calls
        transport_calls += 1
        assert request.full_url == google_places.ENDPOINT
        return _FakePlacesResponse()

    def fake_extract(images: list[bytes]) -> image_logic.ExtractResult:
        nonlocal ocr_calls
        ocr_calls += 1
        assert images == [_VALID_PNG]
        return image_logic.ExtractResult(
            text="OCR 채용 공고",
            looks_like_posting=True,
            cost_krw=100.0,
            model="fake-ocr",
        )

    monkeypatch.setattr(google_places, "_urlopen", fake_transport)
    monkeypatch.setattr(job_runtime, "default_extract", fake_extract)
    origin = {"Origin": "http://127.0.0.1:8020"}

    with TestClient(
        app,
        base_url="http://127.0.0.1:8020",
        client=("127.0.0.1", 50123),
    ) as client:
        home = client.get("/")
        csrf = _hidden(home.text, "csrf_token")
        workflow_id = _hidden(home.text, "evaluation_workflow_id")
        not_found = client.post(
            "/confirm",
            data=_jyp_form(
                csrf,
                evaluation_paid_consent="yes",
                evaluation_workflow_id=workflow_id,
                posting_image_consent="yes",
            ),
            headers=origin,
        )
        assert not_found.status_code == 200
        assert "Google Maps로 회사 후보 찾기" in not_found.text
        evaluation_grant = _hidden(not_found.text, "evaluation_consent_grant")

        candidates = client.post(
            "/confirm",
            data=_jyp_form(
                csrf,
                candidate_search_requested="yes",
                candidate_search_grant=_hidden(
                    not_found.text, "candidate_search_grant"
                ),
                evaluation_consent_grant=evaluation_grant,
                posting_image_consent="yes",
            ),
            headers=origin,
        )
        assert candidates.status_code == 200
        assert "주소와 함께 찾은 회사 후보입니다" in candidates.text
        assert "(주)제이와이피엔터테인먼트" in candidates.text
        assert transport_calls == 1

        confirmed = client.post(
            "/confirm",
            data=_jyp_form(
                csrf,
                candidate_resolution_confirmed="yes",
                candidate_attempt_token=_hidden(
                    candidates.text, "candidate_attempt_token"
                ),
                candidate_selection_token=_hidden(
                    candidates.text, "candidate_selection_token"
                ),
                candidate_index=_hidden(candidates.text, "candidate_index"),
                candidate_name=_hidden(candidates.text, "candidate_name"),
                candidate_provider=_hidden(candidates.text, "candidate_provider"),
                evaluation_consent_grant=_hidden(
                    candidates.text, "evaluation_consent_grant"
                ),
                posting_image_consent="yes",
            ),
            headers=origin,
        )
        assert confirmed.status_code == 200, confirmed.text
        assert "이 회사가 맞나요?" in confirmed.text
        assert pipeline.lookup_calls == [
            "JYP",
            "(주)제이와이피엔터테인먼트",
        ]

        run = client.post(
            "/run",
            data=_jyp_form(
                csrf,
                legal_name="(주)제이와이피엔터테인먼트",
                ref=_hidden(confirmed.text, "ref"),
                address="서울특별시 강동구 강동대로 205",
                paid_attempt_token=_hidden(
                    confirmed.text, "paid_attempt_token"
                ),
                evaluation_consent_grant=_hidden(
                    confirmed.text, "evaluation_consent_grant"
                ),
                posting_image_consent="yes",
            ),
            files={"posting_images": ("posting.png", _VALID_PNG, "image/png")},
            headers=origin,
            follow_redirects=False,
        )
        assert run.status_code == 303
        job_id = run.headers["location"].rsplit("/", 1)[-1]
        for _ in range(100):
            if client.get(f"/api/progress/{job_id}").json()["finished"]:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("JYP fake 본조사가 끝나지 않았습니다")
        result = client.get(f"/result/{job_id}")

    # This paid-flow fixture intentionally returns the pre-canonical one-section
    # Report above. Consent, candidate resolution, and charging still complete,
    # but a partial/legacy report must never cross the public result boundary.
    assert result.status_code == 409
    assert '<div class="stopped-icon">' in result.text
    assert "JYP fake E2E 검증 문장" not in result.text
    assert pipeline.run_calls == 1
    assert transport_calls == 1
    # 회사분석 전용 흐름은 레거시 이미지 필드를 받아도 OCR을 호출하지 않는다.
    assert ocr_calls == 0
    with storage_db.connect() as conn:
        rows = [
            (str(row[0]), str(row[1]), float(row[2]))
            for row in conn.execute(
                "SELECT run_id, phase, cost_krw FROM budget_spend_events"
            ).fetchall()
        ]
    candidate_run_ids = {
        run_id for run_id, phase, _cost in rows if phase == SPEND_PHASE_CANDIDATE
    }
    assert len(candidate_run_ids) == 1
    candidate_run_id = next(iter(candidate_run_ids))
    same_attempt_costs = {
        phase: cost for run_id, phase, cost in rows if run_id == candidate_run_id
    }
    assert same_attempt_costs == {
        SPEND_PHASE_CANDIDATE: 49.0,
        SPEND_PHASE_IDENTIFY: 100.0,
        SPEND_PHASE_PIPELINE: 900.0,
    }
    assert sum(same_attempt_costs.values()) == 1049.0
