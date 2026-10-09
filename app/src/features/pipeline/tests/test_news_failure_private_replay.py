"""실패 요청의 비공개 보존은 실제 전송·원인·보수부채와 독립적이다."""

import copy
import hashlib
import json
import os
import time

import anthropic
import httpx
import pytest

from src.core.provider_gateway import gateway
from src.core.provider_gateway.types import BillingDisposition, TransportState
from src.features.observability import run_diagnostics
from src.features.pipeline import private_replay as replay, real
from src.features.pipeline.private_replay_constants import (
    NEWS_OBSERVATION_FILE_PATTERN, REPLAY_DEPLOYMENT_MARKERS, REPLAY_ENABLED_ENV,
    REPLAY_FAILURE_CAPTURE_KIND, REPLAY_FAILURE_DIAGNOSTIC_STEP,
    REPLAY_FAILURE_ERROR_MESSAGE_MAX_CHARS, REPLAY_FAILURE_ERROR_TYPE_MAX_CHARS,
    REPLAY_FAILURE_FILE_PATTERN, REPLAY_FAILURE_SCHEMA_VERSION, REPLAY_RETENTION_SECONDS,
    REPLAY_FAILURE_REQUEST_FINGERPRINT_SCOPE, REPLAY_RUN_ENV,
)
from src.features.pipeline.tests.test_news_analysis_exact_cache import (
    Messages, attempts, environment, setup_engine,
)
from src.features.pipeline.tests.test_private_replay import local_replay


PROMPT = "비공개 합성 요청\r\n둘째 줄"
SCHEMA = {"type": "object", "properties": {
    "items": {"type": "array", "uniqueItems": True, "items": {"type": "string"}},
}, "required": ["items"], "additionalProperties": False}
SDK_MESSAGE = "비공개 합성 SDK 오류 메시지"


def _bad_request(body=None):
    request = httpx.Request("POST", "https://provider.invalid/messages",
                            headers={"authorization": "secret-header"})
    response = httpx.Response(400, request=request,
                              headers={"request-id": "private-request-id"})
    return anthropic.BadRequestError("예외 repr에만 있는 비밀", response=response, body=body)


class FailingMessages(Messages):
    def __init__(self, error):
        super().__init__()
        self.error = error

    def create(self, **kwargs):
        self.requests.append(kwargs)
        raise self.error


def _save(**overrides):
    values = dict(prompt=PROMPT, response_schema=SCHEMA, model="test-model",
                  output_limit=128, stage="news_grounding", error_type="invalid_request_error",
                  error_message=SDK_MESSAGE, status_code=400)
    values.update(overrides)
    return replay.record_local_provider_failure(**values)


@pytest.mark.parametrize("kind", ["bad_request", "transport"])
@pytest.mark.parametrize("mode", ["enabled", "disabled", "deployment", "save_failure"])
def test_실패보존이_단회전송_원예외와_보수부채를_바꾸지_않는다(
    local_replay, monkeypatch, attempts, kind, mode, caplog,
):
    if mode == "disabled":
        monkeypatch.delenv(REPLAY_ENABLED_ENV)
    elif mode == "deployment":
        monkeypatch.setenv(REPLAY_DEPLOYMENT_MARKERS[0], "synthetic-service")
    elif mode == "save_failure":
        def fail(**_kwargs):
            raise RuntimeError("저장 장애의 비공개 원문")
        monkeypatch.setattr(replay, "record_local_provider_failure", fail)
    body = {"error": {"type": "invalid_request_error", "message": SDK_MESSAGE,
                      "request": "수집하면 안 되는 추가 필드"}, "token": "secret-body-token"}
    error = _bad_request(body) if kind == "bad_request" else TimeoutError("전송 오류의 비공개 원문")
    engine, client, messages = setup_engine(FailingMessages(error))
    analyze = real._news_grounded_analyzer(engine, client)
    source_schema = copy.deepcopy(SCHEMA)
    diagnostics = run_diagnostics.begin_run()
    try:
        with real.provider_budget.activate(1000) as budget:
            with pytest.raises(gateway.ProviderCallFailed) as failed:
                analyze(PROMPT, SCHEMA, 128)
            assert failed.value.__cause__ is error
            assert failed.value.observation is attempts[1][0]
            assert attempts[1][0].billing_disposition is BillingDisposition.CONSERVATIVE_LIABILITY
            assert attempts[1][0].known_cost_krw == 0
            assert attempts[1][0].liability_krw == pytest.approx(attempts[0][0][2])
            assert budget.accounted_krw == pytest.approx(attempts[0][0][2])
            assert budget.accounted_krw > 0
            assert engine.billing_uncertain
            assert attempts[1][0].transport_state is (
                TransportState.RESPONSE_RECEIVED if kind == "bad_request" else TransportState.TRANSPORT_AMBIGUOUS
            )
            with pytest.raises(real.provider_budget.ProviderBudgetUnavailable):
                analyze("후속 호출 금지", SCHEMA, 128)
        assert len(messages.requests) == len(attempts[0]) == len(attempts[1]) == 1
        assert engine._provider_dispatch_count == 1 and engine._private_news_request.get() is None
        assert SCHEMA == source_schema
        steps = [row for row in diagnostics.steps if row["step"] == REPLAY_FAILURE_DIAGNOSTIC_STEP]
        expected = [] if mode in {"disabled", "deployment"} else [{
            "step": REPLAY_FAILURE_DIAGNOSTIC_STEP, "단계": "news_grounding", "시도수": 1,
            "저장수": int(mode == "enabled"), "미보관수": int(mode != "enabled"),
        }]
        assert steps == expected
        for private in (PROMPT, SDK_MESSAGE, "secret-header", "secret-body-token",
                        "private-request-id", "저장 장애의 비공개 원문", str(local_replay)):
            assert private not in repr(diagnostics.steps) and private not in caplog.text
    finally:
        diagnostics.finish()
    records = list(local_replay.glob(REPLAY_FAILURE_FILE_PATTERN))
    assert len(records) == int(mode == "enabled")
    assert not list(local_replay.glob("call-*.json"))
    if mode in {"disabled", "deployment"}:
        assert not local_replay.exists()
    if records:
        record = replay.read_local_provider_failure(records[0])
        assert record["schema_version"] == REPLAY_FAILURE_SCHEMA_VERSION
        assert record["capture_kind"] == REPLAY_FAILURE_CAPTURE_KIND
        assert record["request_fingerprint_scope"] == REPLAY_FAILURE_REQUEST_FINGERPRINT_SCOPE
        assert record["response_available"] is False
        assert "response" not in record and "response_sha256" not in record
        wire = messages.requests[0]
        assert record["prompt"] == wire["messages"][0]["content"] == PROMPT
        assert record["response_schema"] == wire["output_config"]["format"]["schema"]
        assert record["response_schema"] != SCHEMA  # SDK가 uniqueItems를 설명으로 옮긴 실제 전송본
        assert record["model"] == wire["model"] and record["output_limit"] == wire["max_tokens"]
        assert record["sdk_error"]["message"] == (SDK_MESSAGE if kind == "bad_request" else "")
        assert record["sdk_error"]["status"] == ("400" if kind == "bad_request" else "")
        expected_id = attempts[1][0].request_id
        assert record["provider_request_id_sha256"] == (
            hashlib.sha256(expected_id.encode("utf-8")).hexdigest() if expected_id else None
        )
        raw = records[0].read_text(encoding="utf-8")
        assert all(private not in raw for private in (
            "secret-header", "secret-body-token", "private-request-id", "예외 repr에만 있는 비밀",
            "수집하면 안 되는 추가 필드", "전송 오류의 비공개 원문",
        ))
        with pytest.raises(ValueError):
            replay.read_local_provider_replay(records[0])


@pytest.mark.parametrize("body", [None, "비공개 텍스트 body", [], {"error": []},
                                {"type": "flat-type", "message": "flat-message"},
                                {"error": {"type": {}, "message": ["wrong"]}}])
def test_SDK_body가_잘못된_형태면_예외문자열로_대체하지_않는다(local_replay, attempts, body):
    error = _bad_request(body)
    engine, client, messages = setup_engine(FailingMessages(error))
    with real.provider_budget.activate(1000), pytest.raises(gateway.ProviderCallFailed) as failed:
        real._news_grounded_analyzer(engine, client)(PROMPT, SCHEMA, 128)
    assert failed.value.__cause__ is error and len(messages.requests) == 1
    path, = local_replay.glob(REPLAY_FAILURE_FILE_PATTERN)
    record = replay.read_local_provider_failure(path)
    assert record["sdk_error"]["type"] == record["sdk_error"]["message"] == ""
    assert record["sdk_error"]["status"] == "400"
    assert record["sdk_error"]["type_original_chars"] == record["sdk_error"]["message_original_chars"] == 0


def test_오류_발췌는_길이와_잘림을_명시한다(local_replay):
    error_type, message = "유" * (REPLAY_FAILURE_ERROR_TYPE_MAX_CHARS + 1), "한" * (REPLAY_FAILURE_ERROR_MESSAGE_MAX_CHARS + 3)
    assert _save(error_type=error_type, error_message=message)
    path, = local_replay.glob(REPLAY_FAILURE_FILE_PATTERN)
    record = replay.read_local_provider_failure(path)
    error = record["sdk_error"]
    assert error["type"] == error_type[:REPLAY_FAILURE_ERROR_TYPE_MAX_CHARS]
    assert error["message"] == message[:REPLAY_FAILURE_ERROR_MESSAGE_MAX_CHARS]
    assert error["type_original_chars"] == len(error_type) and error["message_original_chars"] == len(message)
    assert error["type_truncated"] is error["message_truncated"] is True


@pytest.mark.parametrize("field", ["prompt", "response_schema", "model", "output_limit", "sdk_error",
                                 "request_sha256", "provider_request_id_sha256"])
def test_요청과_오류_변조는_전체_지문으로_거절한다(local_replay, field):
    assert _save()
    path, = local_replay.glob(REPLAY_FAILURE_FILE_PATTERN)
    record = json.loads(path.read_text(encoding="utf-8"))
    replacement = {"prompt": "바뀐 원문", "response_schema": {}, "model": "other-model",
                   "output_limit": 1, "sdk_error": {**record["sdk_error"], "status": "401"},
                   "request_sha256": "0" * 64, "provider_request_id_sha256": "0" * 64}
    record[field] = replacement[field]
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="지문"):
        replay.read_local_provider_failure(path)


def test_전체지문만_다시써도_모델변조는_요청지문에서_거절한다(local_replay):
    assert _save()
    path, = local_replay.glob(REPLAY_FAILURE_FILE_PATTERN)
    record = json.loads(path.read_text(encoding="utf-8"))
    record.pop("record_sha256")
    record["model"] = "other-model"
    record["record_sha256"] = hashlib.sha256(json.dumps(
        record, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode("utf-8")).hexdigest()
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="지문"):
        replay.read_local_provider_failure(path)


@pytest.mark.parametrize("field,value", [("response", "가짜 응답"), ("response_available", True),
                                        ("headers", {"token": "secret"})])
def test_실패기록에_응답이나_추가요청필드를_섞을수_없다(local_replay, field, value):
    assert _save()
    path, = local_replay.glob(REPLAY_FAILURE_FILE_PATTERN)
    record = json.loads(path.read_text(encoding="utf-8"))
    record[field] = value
    path.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(ValueError, match="계약"):
        replay.read_local_provider_failure(path)


def test_실패한도에서_오래된실패나_성공응답_뉴스관측을_삭제하지_않는다(local_replay, monkeypatch):
    monkeypatch.setattr(replay, "REPLAY_FAILURE_MAX_RECORDS", 1)
    monkeypatch.setattr(replay, "REPLAY_MAX_RECORDS", 1)
    assert replay.record_local_provider_replay(prompt="보존 응답", response="{}",
                                             stage="v2_review", model="test", output_limit=1)
    assert replay.record_local_news_observation(event="search_snapshot", payload={})
    assert _save()
    failure, = local_replay.glob(REPLAY_FAILURE_FILE_PATTERN)
    old_time = time.time() - REPLAY_RETENTION_SECONDS - 1
    os.utime(failure, (old_time, old_time))
    before = {path: path.read_bytes() for path in local_replay.glob("*.json")}
    assert not _save(prompt="두번째 실패")
    assert {path: path.read_bytes() for path in local_replay.glob("*.json")} == before
    assert replay.record_local_provider_replay(prompt="새 응답", response="{}",
                                             stage="v2_review", model="test", output_limit=1)
    assert replay.record_local_news_observation(event="body_selection", payload={})
    assert failure.read_bytes() == before[failure]
    assert len(list(local_replay.glob(REPLAY_FAILURE_FILE_PATTERN))) == 1
    assert len(list(local_replay.glob("call-*.json"))) == 1
    assert len(list(local_replay.glob(NEWS_OBSERVATION_FILE_PATTERN))) == 1


@pytest.mark.parametrize("marker", REPLAY_DEPLOYMENT_MARKERS)
def test_배포marker에서_실패보관도_파일없이_거절한다(local_replay, monkeypatch, marker):
    monkeypatch.setenv(marker, "synthetic-service")
    assert not _save() and not local_replay.exists()


def test_알수없는prefix는_기존파일에_접근하지_않는다(local_replay):
    assert not replay._store_local_record({}, prefix="unsupported")
    assert not local_replay.exists()


@pytest.mark.parametrize("run_id", ["../escape", "", "a/b", "A"])
def test_실패보관의_실행표지가_유효하지않으면_파일없이_거절한다(local_replay, monkeypatch, run_id):
    monkeypatch.setenv(REPLAY_RUN_ENV, run_id)
    assert not _save() and not local_replay.exists()


def test_보관배선_자체예외도_원래wrapper와_cause를_보존한다(local_replay, monkeypatch, attempts):
    error = _bad_request()
    engine, client, messages = setup_engine(FailingMessages(error))
    def fail(**_kwargs):
        raise RuntimeError("비공개 보관 배선 장애")
    monkeypatch.setattr(real, "_record_local_news_analysis_failure", fail)
    with real.provider_budget.activate(1000) as budget:
        with pytest.raises(gateway.ProviderCallFailed) as failed:
            real._news_grounded_analyzer(engine, client)(PROMPT, SCHEMA, 128)
        assert failed.value.__cause__ is error and failed.value.observation is attempts[1][0]
        assert budget.accounted_krw == pytest.approx(attempts[0][0][2])
    assert engine.billing_uncertain and engine._private_news_request.get() is None
    assert len(messages.requests) == len(attempts[0]) == len(attempts[1]) == 1
    assert not local_replay.exists()


@pytest.mark.parametrize("change", [dict(output_limit=True), dict(response_schema={"bad": object()}),
                                  dict(prompt=object()), dict(status_code=True, error_message=object()),
                                  dict(status_code="400", error_type={})])
def test_잘못된인자는_문자열repr로_저장하지_않는다(local_replay, change):
    result = _save(**change)
    if "status_code" in change:
        assert result
        path, = local_replay.glob(REPLAY_FAILURE_FILE_PATTERN)
        record = replay.read_local_provider_failure(path)
        assert record["sdk_error"]["status"] == ""
    else:
        assert not result and not local_replay.exists()


def test_크기와_잠금_저장거절은_기존실패파일을_보존한다(local_replay, monkeypatch):
    assert _save()
    path, = local_replay.glob(REPLAY_FAILURE_FILE_PATTERN)
    before = path.read_bytes()
    with monkeypatch.context() as patch:
        patch.setattr(replay, "REPLAY_MAX_RECORD_BYTES", 1)
        assert not _save() and path.read_bytes() == before
    class BusyLock:
        def acquire(self, *, timeout):
            return False
    monkeypatch.setattr(replay, "_WRITE_LOCK", BusyLock())
    assert not _save() and path.read_bytes() == before


def test_연속호출_문맥이_다음실패에_이전요청을_결속하지_않는다(local_replay, attempts):
    engine, client, messages = setup_engine()
    analyze = real._news_grounded_analyzer(engine, client)
    with real.provider_budget.activate(1000):
        analyze("첫 성공 원문", SCHEMA, 128)
        assert engine._private_news_request.get() is None
        error = _bad_request({"error": {"type": "invalid_request_error", "message": SDK_MESSAGE}})
        def fail(**kwargs):
            messages.requests.append(kwargs)
            raise error
        messages.create = fail
        with pytest.raises(gateway.ProviderCallFailed) as captured:
            analyze("둘째 실패 원문", SCHEMA, 128)
        assert captured.value.__cause__ is error and engine._private_news_request.get() is None
    path, = local_replay.glob(REPLAY_FAILURE_FILE_PATTERN)
    assert replay.read_local_provider_failure(path)["prompt"] == "둘째 실패 원문"
    assert len(messages.requests) == len(attempts[0]) == len(attempts[1]) == 2


def test_중첩된호출의_요청문맥을_각실패에_정확히_결속한다(local_replay, attempts):
    inner_engine, inner_client, inner_messages = setup_engine(FailingMessages(_bad_request()))
    inner = real._news_grounded_analyzer(inner_engine, inner_client)
    outer_error = _bad_request()
    class NestedMessages(Messages):
        def create(self, **kwargs):
            self.requests.append(kwargs)
            with pytest.raises(gateway.ProviderCallFailed):
                inner("안쪽 실패 원문", SCHEMA, 64)
            raise outer_error
    engine, client, messages = setup_engine(NestedMessages())
    with real.provider_budget.activate(1000), pytest.raises(gateway.ProviderCallFailed) as captured:
        real._news_grounded_analyzer(engine, client)("바깥 실패 원문", SCHEMA, 128)
    assert captured.value.__cause__ is outer_error
    assert engine._private_news_request.get() is inner_engine._private_news_request.get() is None
    records = [replay.read_local_provider_failure(p) for p in local_replay.glob(REPLAY_FAILURE_FILE_PATTERN)]
    assert {(row["prompt"], row["output_limit"]) for row in records} == {
        ("안쪽 실패 원문", 64), ("바깥 실패 원문", 128),
    }
    assert len(messages.requests) == len(inner_messages.requests) == 1


@pytest.mark.parametrize("dispatches", [0, 2])
def test_단회전송이_확인되지않은실패는_요청파일을_쓰지_않는다(local_replay, dispatches):
    from types import SimpleNamespace
    from src.core.provider_gateway.types import ProviderObservation
    failure = gateway.ProviderCallFailed(ProviderObservation(
        transport_state=TransportState.TRANSPORT_AMBIGUOUS,
        billing_disposition=BillingDisposition.CONSERVATIVE_LIABILITY,
        known_cost_krw=0, liability_krw=1, status_code=None, error_type="", request_id="",
    ))
    failure.__cause__ = TimeoutError("합성 실패")
    engine = real._MeteredEngine(SimpleNamespace(MODEL="test-model"))
    def fail(*_args, **_kwargs):
        engine._provider_dispatch_count += dispatches
        raise failure
    engine._engine._ask = fail
    with pytest.raises(gateway.ProviderCallFailed) as captured:
        real._news_grounded_analyzer(engine, None)(PROMPT, SCHEMA, 128)
    assert captured.value is failure and engine._private_news_request.get() is None
    assert not local_replay.exists()
