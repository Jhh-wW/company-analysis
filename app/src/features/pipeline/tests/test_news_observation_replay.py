"""뉴스 개인 관측은 원 공급자 응답과 구분하며 생산 환경에서는 끈다."""
import json
import threading
import datetime as dt
from types import SimpleNamespace

import pytest

from src.features.pipeline import private_replay as replay
from src.features.pipeline.private_replay_constants import (
    NEWS_OBSERVATION_FILE_PATTERN, NEWS_OBSERVATION_SCHEMA, REPLAY_DEPLOYMENT_MARKERS,
    REPLAY_THREAD_LOCK_TIMEOUT_SECONDS,
)
from src.features.pipeline.tests.test_private_replay import local_replay  # noqa: F401 - 같은 기능의 평가 격리 fixture


def test_뉴스별도스키마_전체지문_원응답파일명분리(local_replay):
    payload = {"snapshot": {"digest": "synthetic"}, "queries": []}
    assert replay.record_local_news_observation(event="search_snapshot", payload=payload)
    path, = local_replay.glob(NEWS_OBSERVATION_FILE_PATTERN)
    assert list(local_replay.glob("call-*.json")) == []
    saved = replay.read_local_news_observation(path)
    assert saved["schema_version"] == NEWS_OBSERVATION_SCHEMA
    assert saved["payload"] == payload
    with pytest.raises(ValueError):
        replay.read_local_provider_replay(path)
    saved["payload"]["queries"].append({"tampered": True})
    path.write_text(json.dumps(saved), encoding="utf-8")
    with pytest.raises(ValueError, match="지문"):
        replay.read_local_news_observation(path)


def test_생산환경과_미설정은_뉴스관측도_디렉토리없이_차단(local_replay, monkeypatch):
    from src.features.pipeline import real
    monkeypatch.setenv(REPLAY_DEPLOYMENT_MARKERS[0], "synthetic-deployment")
    assert real._local_news_research_observer() is None
    assert not replay.record_local_news_observation(event="search_snapshot", payload={})
    assert not local_replay.exists()


def test_잠금은_무한대기하지_않고_정해진상한만_기다린다(local_replay, monkeypatch):
    observed = []

    class BusyLock:
        def acquire(self, *, timeout):
            observed.append(timeout)
            return False

    monkeypatch.setattr(replay, "_WRITE_LOCK", BusyLock())
    assert not replay.record_local_news_observation(event="body_selection", payload={})
    assert observed == [REPLAY_THREAD_LOCK_TIMEOUT_SECONDS]
    assert not local_replay.exists()


def test_짧은동시쓰기경쟁은_응답보관을_버리지_않는다(local_replay):
    started = threading.Event()
    release = threading.Event()

    def hold():
        with replay._WRITE_LOCK:
            started.set()
            release.wait(timeout=REPLAY_THREAD_LOCK_TIMEOUT_SECONDS / 2)

    thread = threading.Thread(target=hold)
    thread.start()
    assert started.wait(timeout=1)
    try:
        assert replay.record_local_provider_replay(prompt="합성 근거", response="{}",
                                                  stage="v2_compose", model="synthetic", output_limit=1)
    finally:
        release.set()
        thread.join(timeout=1)
    assert len(list(local_replay.glob("call-*.json"))) == 1


def test_허용하지않은관측단계와_직렬화오류는_제품에_전파하지않는다(local_replay):
    assert not replay.record_local_news_observation(event="authorization", payload={})
    assert not replay.record_local_news_observation(event="search_snapshot", payload={"bad": object()})
    assert not local_replay.exists()


def test_뉴스관측은_공급자응답_보존한도를_정리하지_않는다(local_replay, monkeypatch):
    monkeypatch.setattr(replay, "REPLAY_MAX_RECORDS", 1)
    assert replay.record_local_provider_replay(prompt="보존 원문", response="{}",
                                              stage="v2_compose", model="synthetic", output_limit=1)
    original, = local_replay.glob("call-*.json")
    original_bytes = original.read_bytes()
    assert replay.record_local_news_observation(event="search_snapshot", payload={})
    assert replay.record_local_news_observation(event="body_selection", payload={})
    assert original.read_bytes() == original_bytes
    assert len(list(local_replay.glob("call-*.json"))) == 1
    assert len(list(local_replay.glob(NEWS_OBSERVATION_FILE_PATTERN))) == 1


def test_실제core세션이_eval기록기까지_두관측을_전달한다(local_replay):
    from src.core.news_research_adapter import prepare_news_research
    from src.features.news_intake.models import NewsCollectionPolicy
    from src.features.pipeline import real
    observer = real._local_news_research_observer()
    assert observer is not None

    def search(*_, **__):
        return SimpleNamespace(state="success", reason_code="news_search_ok", items=[],
                               transport_attempts=1, retry_recovered=False,
                               attempt_reason_codes=("news_search_ok",))

    def unused(*_):
        raise AssertionError("빈 검색 결과의 본문·AI를 호출하면 안 됩니다")

    session = prepare_news_research(search_news=search, company_name="합성기업", aliases=(),
                                    domain="", executive_names=(), identity_context="",
                                    as_of=dt.date(2026, 10, 1), observer=observer,
                                    policy=NewsCollectionPolicy(max_analysis_calls=0))
    result = session.collect(fetch_text=unused, analyze_grounded=unused)
    assert not result.fragments
    records = [replay.read_local_news_observation(path)
               for path in local_replay.glob(NEWS_OBSERVATION_FILE_PATTERN)]
    assert {record["event"] for record in records} == {"search_snapshot", "body_selection"}
    assert len(records) == 2 and not list(local_replay.glob("call-*.json"))
