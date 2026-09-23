"""선택적 로컬 원문 보관은 정산과 공개 진단에 영향을 주지 않는다."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, nullcontext
import contextvars
import json
import multiprocessing
import os
from pathlib import Path
import time

import pytest

from src.features.pipeline import private_replay as replay
from src.features.pipeline.private_replay_constants import (
    REPLAY_DEPLOYMENT_MARKERS, REPLAY_DIRECTORY, REPLAY_ENABLED_ENV,
    REPLAY_RETENTION_SECONDS, REPLAY_RUN_ENV, REPLAY_LOCK_FILENAME, REPLAY_DIAGNOSTIC_STEP,
)


@pytest.fixture
def local_replay(tmp_path, monkeypatch):
    monkeypatch.setattr(replay.paths, "APP_ROOT", tmp_path)
    monkeypatch.setenv(REPLAY_ENABLED_ENV, "1")
    monkeypatch.setenv(REPLAY_RUN_ENV, "anonymous-test")
    for name in REPLAY_DEPLOYMENT_MARKERS:
        monkeypatch.delenv(name, raising=False)
    return tmp_path.joinpath(*REPLAY_DIRECTORY, "anonymous-test")


def _record(**kwargs):
    return replay.record_local_provider_replay(
        prompt="검증할 한글 근거\n둘째 줄", response='{"결과":"참"}',
        stage="v2_review", model="test-model", output_limit=100,
        response_schema={"type": "object"}, **kwargs,
    )


def test_기본설정에서는_디렉토리도_만들지_않는다(tmp_path, monkeypatch):
    monkeypatch.setattr(replay.paths, "APP_ROOT", tmp_path)
    monkeypatch.delenv(REPLAY_ENABLED_ENV, raising=False)
    assert not _record()
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("run_id", ("../escape", "", "A", "a/b"))
def test_실행표지가_경로로_해석되지_않는다(local_replay, monkeypatch, run_id):
    monkeypatch.setenv(REPLAY_RUN_ENV, run_id)
    assert not _record()
    assert not local_replay.exists()


def test_배포서비스에서는_명시적으로_켜도_보관하지_않는다(local_replay, monkeypatch):
    monkeypatch.setenv(REPLAY_DEPLOYMENT_MARKERS[0], "test-deployment")
    assert not _record()
    assert not local_replay.exists()


def test_원문과_스키마_지문을_왕복_검증한다(local_replay):
    assert _record()
    path, = local_replay.glob("call-*.json")
    stored = replay.read_local_provider_replay(path)
    assert stored["prompt"] == "검증할 한글 근거\n둘째 줄"
    assert stored["response"] == '{"결과":"참"}'
    assert stored["response_schema"] == {"type": "object"}
    stored["response"] = "변경된 응답"
    path.write_text(json.dumps(stored), encoding="utf-8")
    with pytest.raises(ValueError, match="지문"):
        replay.read_local_provider_replay(path)


def test_여러_실행의_동시보관도_전체_개수와_보존기간을_지킨다(local_replay, monkeypatch):
    monkeypatch.setattr(replay, "REPLAY_MAX_RECORDS", 3)
    old_dir = local_replay.parent / "old-run"
    old_dir.mkdir(parents=True)
    old_file = old_dir / "call-old.json"
    old_file.write_text("오래된 시험자료", encoding="utf-8")
    old_time = time.time() - REPLAY_RETENTION_SECONDS - 1
    os.utime(old_file, (old_time, old_time))
    unrelated = old_dir / "preserve.txt"
    unrelated.write_text("기존 파일", encoding="utf-8")
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda _: _record(), range(8)))
    assert any(results)
    # 경쟁 중인 호출은 기다리지 않고 보관을 건너뛴다. 순차 보관으로 정리 상한도 확인한다.
    assert all(_record() for _ in range(3))
    paths = list(local_replay.parent.glob("*/call-*.json"))
    assert len(paths) == 3
    assert not old_file.exists()
    assert unrelated.read_text(encoding="utf-8") == "기존 파일"
    assert len({replay.read_local_provider_replay(path)["call_id"] for path in paths}) == 3


def test_보관_실패와_크기초과는_호출자에게_예외를_전파하지_않는다(local_replay, monkeypatch):
    monkeypatch.setattr(replay, "REPLAY_MAX_RECORD_BYTES", 1)
    assert not _record()
    assert not local_replay.exists()


def test_root_잠금파일이_외부파일과_연결돼_있으면_보관하지_않는다(local_replay, tmp_path):
    local_replay.parent.mkdir(parents=True)
    outside = tmp_path / "preserve.txt"
    outside.write_bytes(b"original")
    (local_replay.parent / REPLAY_LOCK_FILENAME).hardlink_to(outside)
    assert not _record()
    assert list(local_replay.parent.glob("*/call-*.json")) == []
    assert outside.read_bytes() == b"original"


@pytest.mark.parametrize("failure_mode", ("", "size", "thread_lock", "process_lock", "disabled", "deployment", "invalid_run", "unexpected_error"))
def test_공급자_배선에서_원문_보관과_실제_정산이_독립적이다(
    local_replay, monkeypatch, failure_mode, caplog,
):
    from types import SimpleNamespace
    from src.core.pricing import usage_cost_krw
    from src.core.provider_gateway import attempt_context
    from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
    from src.features.pipeline import real
    from src.features.observability import run_diagnostics
    from src.features.pipeline.tests.test_diagram_budget_metering import RecordingMessages

    save_succeeds = not failure_mode
    if failure_mode == "size":
        monkeypatch.setattr(replay, "REPLAY_MAX_RECORD_BYTES", 1)
    elif failure_mode == "disabled":
        monkeypatch.delenv(REPLAY_ENABLED_ENV, raising=False)
    elif failure_mode == "deployment":
        monkeypatch.setenv(REPLAY_DEPLOYMENT_MARKERS[0], "test-deployment")
    elif failure_mode == "invalid_run":
        monkeypatch.setenv(REPLAY_RUN_ENV, "../private")
    elif failure_mode == "unexpected_error":
        def unavailable(**_kwargs):
            raise RuntimeError("비공개 원문이나 경로가 들어 있을 수 있는 오류")
        monkeypatch.setattr(replay, "record_local_provider_replay", unavailable)
    local_replay.parent.mkdir(parents=True, exist_ok=True)
    lock_context = nullcontext()
    if failure_mode == "thread_lock":
        lock_context = replay._WRITE_LOCK
    elif failure_mode == "process_lock":
        lock_context = replay.try_exclusive_file_lock(local_replay.parent / REPLAY_LOCK_FILENAME)
    messages = RecordingMessages("end_turn")
    engine = real._MeteredEngine(SimpleNamespace(MODEL="claude-haiku-4-5"))
    client = real._metered_client(engine, SimpleNamespace(messages=messages))
    observations = []
    callbacks = ProviderAttemptCallbacks(
        lambda *_: 1, lambda _: None, lambda _: None,
        lambda _, observation: observations.append(observation),
    )
    ask = real._v2_ask_via_provider(engine, client, stage="v2_review", max_tokens=2048)
    prompt = "비공개 시험 원문\n두 번째 줄"
    collector = run_diagnostics.begin_run()
    try:
        with lock_context, real.provider_budget.activate(1000) as budget, attempt_context.activate(callbacks):
            result = ask(prompt)
            assert result == '{"판정": []}'
            assert budget.accounted_krw == usage_cost_krw("claude-haiku-4-5", 1234, 1200)
        replay_steps = [step for step in collector.steps if step["step"] == REPLAY_DIAGNOSTIC_STEP]
        if failure_mode in {"disabled", "deployment", "invalid_run"}:
            assert replay_steps == []
        else:
            assert replay_steps == [{
                "step": REPLAY_DIAGNOSTIC_STEP, "단계": "v2_review",
                "시도수": 1, "저장수": int(save_succeeds), "미보관수": int(not save_succeeds),
            }]
        assert prompt not in repr(collector.steps) and result not in repr(collector.steps)
        assert str(local_replay) not in repr(collector.steps)
        assert "비공개 원문이나 경로" not in repr(collector.steps)
    finally:
        collector.finish()
    assert len(observations) == len(messages.requests) == len(engine.usages) == 1
    paths = list(local_replay.glob("call-*.json"))
    assert len(paths) == int(save_succeeds)
    if save_succeeds:
        record = replay.read_local_provider_replay(paths[0])
        assert record["prompt"] == prompt and record["response"] == result
        assert record["stage"] == "v2_review" and record["output_limit"] == 2048
        assert record["stop_reason"] == "end_turn"
    assert prompt not in caplog.text and result not in caplog.text


def test_병렬_보관경쟁의_시도수와_누락수를_원문없이_센다(local_replay, caplog, capsys):
    from types import SimpleNamespace
    from src.core.pricing import usage_cost_krw
    from src.core.provider_gateway import attempt_context
    from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
    from src.features.observability import run_diagnostics
    from src.features.pipeline import real
    from src.features.pipeline.tests.test_diagram_budget_metering import RecordingMessages

    def ask_once():
        messages = RecordingMessages("end_turn")
        engine = real._MeteredEngine(SimpleNamespace(MODEL="claude-haiku-4-5"))
        client = real._metered_client(engine, SimpleNamespace(messages=messages))
        observations = []
        callbacks = ProviderAttemptCallbacks(
            lambda *_: 1, lambda _: None, lambda _: None,
            lambda _, observation: observations.append(observation),
        )
        ask = real._v2_ask_via_provider(engine, client, stage="v2_compose", max_tokens=2048)
        with real.provider_budget.activate(1000) as budget, attempt_context.activate(callbacks):
            result = ask("원문 노출 금지 합성 입력")
            assert budget.accounted_krw == usage_cost_krw("claude-haiku-4-5", 1234, 1200)
        assert len(messages.requests) == len(engine.usages) == len(observations) == 1
        return result

    collector = run_diagnostics.begin_run()
    try:
        with replay._WRITE_LOCK, ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(contextvars.copy_context().run, ask_once) for _ in range(3)]
            assert [future.result(timeout=5) for future in futures] == ['{"판정": []}'] * 3
        assert ask_once() == '{"판정": []}'
        steps = [step for step in collector.steps if step["step"] == REPLAY_DIAGNOSTIC_STEP]
        assert len(steps) == 4 and all(step["단계"] == "v2_compose" for step in steps)
        assert sum(step["시도수"] for step in steps) == 4
        assert sum(step["저장수"] for step in steps) == 1
        assert sum(step["미보관수"] for step in steps) == 3
        assert len(list(local_replay.glob("call-*.json"))) == 1
        assert "원문 노출 금지" not in repr(collector.steps)
        assert str(local_replay) not in repr(collector.steps)
    finally:
        collector.finish()
    captured = capsys.readouterr()
    assert "원문 노출 금지" not in captured.out + captured.err + caplog.text


@pytest.mark.parametrize("diagnostic_available", (True, False))
def test_보관진단은_임의단계를_숨기고_장애에도_원응답을_유지한다(local_replay, monkeypatch, diagnostic_available):
    from types import SimpleNamespace
    from src.features.observability import run_diagnostics
    from src.features.pipeline import real

    def unavailable():
        raise RuntimeError("진단 장애")

    if not diagnostic_available:
        monkeypatch.setattr(run_diagnostics, "current_steps", unavailable)
    response = SimpleNamespace(content=[SimpleNamespace(text="공백과\r\n줄바꿈 유지  ")])
    client = SimpleNamespace(messages=SimpleNamespace(create=lambda **_: response))
    engine = SimpleNamespace(MODEL="test-model", current_stage="", prompt_cache_enabled=False)
    monkeypatch.setattr(real, "_meter_stage", lambda *_args, **_kwargs: nullcontext())
    ask = real._v2_ask_via_provider(engine, client, stage="비공개 단계 표지", max_tokens=100)
    collector = run_diagnostics.begin_run()
    try:
        assert ask("합성 입력") == "공백과\r\n줄바꿈 유지  "
        path, = local_replay.glob("call-*.json")
        assert replay.read_local_provider_replay(path)["response"] == "공백과\r\n줄바꿈 유지  "
        if diagnostic_available:
            step, = [step for step in collector.steps if step["step"] == REPLAY_DIAGNOSTIC_STEP]
            assert step == {"step": REPLAY_DIAGNOSTIC_STEP, "단계": "unknown", "시도수": 1, "저장수": 1, "미보관수": 0}
        else:
            assert collector.steps == []
        assert "비공개 단계 표지" not in repr(collector.steps)
        assert "공백과" not in repr(collector.steps)
    finally:
        collector.finish()


def _process_record_worker(app_root, role, ready, release, retry, results):
    """서로 다른 프로세스가 한 보관 root를 쓰는 실제 파일 잠금 시험."""
    replay.paths.APP_ROOT = Path(app_root)
    replay.REPLAY_MAX_RECORDS = 1
    os.environ[REPLAY_ENABLED_ENV] = "1"
    os.environ[REPLAY_RUN_ENV] = role
    for name in REPLAY_DEPLOYMENT_MARKERS:
        os.environ.pop(name, None)

    def record():
        return replay.record_local_provider_replay(
            prompt=f"익명 합성 {role}", response='{"결과":"확인"}',
            stage="test", model="test-model", output_limit=100,
        )

    if role == "holder":
        original_lock = replay.try_exclusive_file_lock

        @contextmanager
        def held_lock(path):
            with original_lock(path) as acquired:
                if acquired:
                    ready.set()
                    if not release.wait(timeout=10):
                        raise RuntimeError("시험 잠금 해제 신호를 받지 못했습니다")
                yield acquired

        replay.try_exclusive_file_lock = held_lock
        results.put((role, record()))
    else:
        if not ready.wait(timeout=10):
            raise RuntimeError("시험 잠금 준비 신호를 받지 못했습니다")
        results.put(("contender_busy", record()))
        if not retry.wait(timeout=10):
            raise RuntimeError("시험 재시도 신호를 받지 못했습니다")
        results.put(("contender_after", record()))


def test_두_독립_프로세스도_기다리지_않고_전체상한과_지문을_지킨다(local_replay):
    context = multiprocessing.get_context("spawn")
    ready, release, retry = (context.Event() for _ in range(3))
    results = context.Queue()
    processes = [context.Process(
        target=_process_record_worker,
        args=(str(replay.paths.APP_ROOT), role, ready, release, retry, results),
    ) for role in ("holder", "contender")]
    try:
        for process in processes:
            process.start()
        # holder가 계속 잠근 상태에서 경쟁 호출의 False가 먼저 돌아와야 한다.
        assert results.get(timeout=10) == ("contender_busy", False)
        assert processes[0].is_alive()
        release.set()
        processes[0].join(timeout=10)
        assert processes[0].exitcode == 0
        assert results.get(timeout=5) == ("holder", True)
        first_path, = local_replay.parent.glob("*/call-*.json")
        assert replay.read_local_provider_replay(first_path)["prompt"] == "익명 합성 holder"
        retry.set()
        processes[1].join(timeout=10)
        assert processes[1].exitcode == 0
        assert results.get(timeout=5) == ("contender_after", True)
        final_path, = local_replay.parent.glob("*/call-*.json")
        assert replay.read_local_provider_replay(final_path)["prompt"] == "익명 합성 contender"
        assert final_path != first_path and not first_path.exists()
        assert (local_replay.parent / REPLAY_LOCK_FILENAME).is_file()
    finally:
        release.set()
        retry.set()
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)
        results.close()
