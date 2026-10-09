"""뉴스의 해석된 응답 보관은 실제 전송과 정산·캐시 경계를 유지한다."""

import json

import pytest

from src.features.pipeline import private_replay as replay, real
from src.features.pipeline.private_replay_constants import (
    REPLAY_DEPLOYMENT_MARKERS, REPLAY_ENABLED_ENV, REPLAY_PARSED_CAPTURE_KIND,
    REPLAY_PARSED_CAPTURE_SCHEMA, REPLAY_PARSED_SCHEMA_VERSION, REPLAY_DIAGNOSTIC_STEP,
)
from src.features.pipeline.tests.test_news_analysis_exact_cache import (
    attempts, collect, environment, setup_engine, MODEL, INPUT, NEWS_OUTPUT, Messages,
)
from src.core.pricing import usage_cost_krw
from src.features.pipeline.tests.test_private_replay import local_replay
from src.features.observability import run_diagnostics


@pytest.mark.parametrize("mode", ("enabled", "disabled", "deployment", "save_failure"))
def test_뉴스응답_보관여부가_실제호출과_정산을_바꾸지_않는다(
    local_replay, monkeypatch, attempts, mode, caplog,
):
    if mode == "disabled":
        monkeypatch.delenv(REPLAY_ENABLED_ENV)
    elif mode == "deployment":
        monkeypatch.setenv(REPLAY_DEPLOYMENT_MARKERS[0], "synthetic-deployment")
    elif mode == "save_failure":
        def fail(**_kwargs):
            raise RuntimeError("비공개 원문이 들어 있을 수 있는 저장 장애")
        monkeypatch.setattr(replay, "record_local_provider_replay", fail)
    engine, client, messages = setup_engine()
    diagnostics = run_diagnostics.begin_run()
    try:
        with real.provider_budget.activate(1000) as budget:
            result = collect(engine, client)
        assert result.fragments and budget.accounted_krw == usage_cost_krw(MODEL, INPUT, NEWS_OUTPUT)
        assert len(messages.requests) == len(engine.usages) == len(attempts[0]) == len(attempts[1]) == 1
        steps = [row for row in diagnostics.steps if row["step"] == REPLAY_DIAGNOSTIC_STEP]
        if mode in {"disabled", "deployment"}:
            assert not steps
        else:
            assert steps == [{"step": REPLAY_DIAGNOSTIC_STEP, "단계": "news_grounding",
                              "시도수": 1, "저장수": int(mode == "enabled"),
                              "미보관수": int(mode != "enabled")}]
        assert str(local_replay) not in repr(diagnostics.steps)
        assert "비공개 원문" not in repr(diagnostics.steps)
    finally:
        diagnostics.finish()
    records = list(local_replay.glob("call-*.json"))
    assert len(records) == int(mode == "enabled")
    if records:
        stored = replay.read_local_provider_replay(records[0])
        assert stored["schema_version"] == REPLAY_PARSED_SCHEMA_VERSION
        assert stored["capture_kind"] == REPLAY_PARSED_CAPTURE_KIND
        assert stored["capture_schema"] == REPLAY_PARSED_CAPTURE_SCHEMA
        assert stored["response_schema"]["type"] == "object"
        assert stored["response"] == json.dumps(json.loads(stored["response"]), ensure_ascii=False,
                                                sort_keys=True, separators=(",", ":"))
        assert stored["prompt"] not in caplog.text and stored["response"] not in caplog.text


def test_뉴스분석_캐시적중은_새공급자기록을_만들지_않는다(local_replay, attempts):
    spend = []
    requests = []
    for _ in range(2):
        engine, client, messages = setup_engine()
        with real.provider_budget.activate(1000) as budget:
            collect(engine, client)
        spend.append(budget.accounted_krw)
        requests.append(len(messages.requests))
    assert requests == [1, 0]
    assert spend[0] > spend[1] == 0
    assert len(attempts[0]) == len(attempts[1]) == len(list(local_replay.glob("call-*.json"))) == 1


def test_뉴스보관은_요청별칭보다_공급자응답의_실제모델을_우선한다(local_replay, attempts):
    actual_model = "claude-haiku-4-5-20251001"

    class DatedModelMessages(Messages):
        def create(self, **kwargs):
            response = super().create(**kwargs)
            response.model = actual_model
            return response

    engine, client, messages = setup_engine(DatedModelMessages())
    with real.provider_budget.activate(1000):
        collect(engine, client)
    path, = local_replay.glob("call-*.json")
    assert engine.MODEL != actual_model
    assert replay.read_local_provider_replay(path)["model"] == actual_model
    assert len(messages.requests) == len(attempts[0]) == 1


@pytest.mark.parametrize("field", ("capture_kind", "capture_schema"))
def test_해석된응답의_표현표시_삭제나_변조는_읽기에서_거절한다(local_replay, field):
    assert replay.record_local_provider_replay(
        prompt="합성 뉴스 본문", response='{"items":[]}', stage="news_grounding", model="test-model",
        output_limit=100, response_schema={"type": "object"}, capture_kind=REPLAY_PARSED_CAPTURE_KIND,
    )
    path, = local_replay.glob("call-*.json")
    stored = json.loads(path.read_text(encoding="utf-8"))
    for change in ("delete", "tamper"):
        changed = dict(stored)
        if change == "delete":
            changed.pop(field)
        else:
            changed[field] = "raw-provider-text"
        path.write_text(json.dumps(changed), encoding="utf-8")
        with pytest.raises(ValueError, match="응답 표현"):
            replay.read_local_provider_replay(path)


def test_기존원응답_보관형태는_변하지_않는다(local_replay):
    assert replay.record_local_provider_replay(
        prompt="원문", response=" 공백과 줄바꿈\r\n유지 ", stage="v2_compose", model="test-model",
        output_limit=100,
    )
    path, = local_replay.glob("call-*.json")
    stored = replay.read_local_provider_replay(path)
    assert stored["schema_version"] == "provider-replay-v1"
    assert "capture_kind" not in stored and "capture_schema" not in stored
    assert stored["response"] == " 공백과 줄바꿈\r\n유지 "
