"""동시 작성에서도 동일한 입력·결과·실제 호출 영수증을 지킨다."""

from __future__ import annotations

import contextvars
import json
import threading
from collections import Counter
from functools import partial
from types import SimpleNamespace

import pytest

from src.features.composer.constants import GRADE_CONFIRMED, SECTION_IDS
from src.features.composer.logic import compose_sections
from src.features.composer.parallel_sections import run_section_jobs, section_worker_count
from src.features.composer.pipeline import _CallLedgerRecorder
from src.features.composer.port import CollectedFragment
from src.shared.generation_validation_receipt import ValidationRound


WAIT_SECONDS = 5
WORKERS = 3


@pytest.mark.parametrize("safe,workers,expected", [
    (True, 3, 3), (True, 100, 3), (True, 2, 2), (True, 0, 1),
    (True, True, 1), (True, "3", 1), (False, 3, 1), (1, 3, 1),
])
def test_명시된_안전_능력과_정수_상한만_허용한다(safe, workers, expected):
    assert section_worker_count(SimpleNamespace(
        parallel_safe=safe, max_parallel_calls=workers,
    )) == expected


def test_부모_문맥을_상속하고_작업별_변경은_격리한다():
    identity = contextvars.ContextVar("시험_요청", default="없음")
    token = identity.set("부모")
    barrier = threading.Barrier(WORKERS)

    def job(index):
        before = identity.get()
        identity.set(str(index))
        barrier.wait(timeout=WAIT_SECONDS)
        return before, identity.get()

    try:
        assert run_section_jobs(
            [partial(job, index) for index in range(WORKERS)], max_workers=WORKERS,
        ) == [("부모", str(index)) for index in range(WORKERS)]
        assert identity.get() == "부모"
    finally:
        identity.reset(token)


def test_끝나는_순서가_뒤집혀도_목차_순서로_반환한다():
    barrier = threading.Barrier(WORKERS)
    finished = [threading.Event() for _ in range(WORKERS)]
    completion = []

    def job(index):
        barrier.wait(timeout=WAIT_SECONDS)
        if index < WORKERS - 1:
            assert finished[index + 1].wait(WAIT_SECONDS)
        completion.append(index)
        finished[index].set()
        return index

    assert run_section_jobs(
        [partial(job, index) for index in range(WORKERS)], max_workers=WORKERS,
    ) == list(range(WORKERS))
    assert completion == list(reversed(range(WORKERS)))


def test_실패한_뒤_새_일을_보내지_않고_기존_일은_정산까지_기다린다(monkeypatch):
    from src.features.composer import parallel_sections

    original_wait = parallel_sections.wait
    barrier = threading.Barrier(WORKERS)
    failure_observed = threading.Event()
    completed = []
    started = []

    def observe_wait(*args, **kwargs):
        result = original_wait(*args, **kwargs)
        assert any(future.exception() is not None for future in result[0])
        failure_observed.set()
        return result

    monkeypatch.setattr(parallel_sections, "wait", observe_wait)

    def job(index):
        started.append(index)
        barrier.wait(timeout=WAIT_SECONDS)
        if index == 0:
            raise RuntimeError("공급자 실패")
        assert failure_observed.wait(WAIT_SECONDS)
        completed.append(index)
        return index

    with pytest.raises(RuntimeError, match="공급자 실패"):
        run_section_jobs([partial(job, index) for index in range(9)], max_workers=WORKERS)
    assert sorted(started) == [0, 1, 2]
    assert sorted(completed) == [1, 2]


def _response():
    return json.dumps({"문장들": [{
        "글": "가나다전자는 반도체 검사 장비를 제조한다.",
        "인용": ["1"], "등급": GRADE_CONFIRMED,
    }]}, ensure_ascii=False)


def _packets():
    fragment = CollectedFragment("1", "사업내용", "가나다전자는 반도체 검사 장비를 제조한다.")
    return {section: (fragment,) for section in SECTION_IDS}


def test_병렬_작성은_동일한_프롬프트와_결과를_아홉_번_호출로_만든다():
    serial_prompts = []
    parallel_prompts = []
    barrier = threading.Barrier(WORKERS)
    lock = threading.Lock()
    active = 0
    peak = 0

    def serial_ask(prompt):
        serial_prompts.append(prompt)
        return _response()

    def parallel_ask(prompt):
        nonlocal active, peak
        with lock:
            parallel_prompts.append(prompt)
            active += 1
            peak = max(peak, active)
        barrier.wait(timeout=WAIT_SECONDS)
        with lock:
            active -= 1
        return _response()

    parallel_ask.parallel_safe = True
    parallel_ask.max_parallel_calls = WORKERS
    serial = compose_sections("가나다전자", (), None, serial_ask, section_evidence_packets=_packets())
    diagnostics = []
    recorder = _CallLedgerRecorder()
    writer = recorder.wrap(parallel_ask, role="writer", validation_round=ValidationRound.PRIMARY,
                           section_ids=SECTION_IDS)
    parallel = compose_sections("가나다전자", (), None, writer, section_evidence_packets=_packets(),
                                composition_diagnostics=diagnostics)
    assert parallel == serial
    assert len(parallel_prompts) == len(serial_prompts) == len(SECTION_IDS)
    assert Counter(parallel_prompts) == Counter(serial_prompts)
    assert peak == WORKERS
    assert recorder.freeze().writer_calls == len(SECTION_IDS)
    assert tuple(record.section_id for record in recorder.freeze().records) == SECTION_IDS
    execution = next(item for item in diagnostics if item["step"] == "v2_작성_실행방식")
    assert execution["동시상한"] == WORKERS
    assert set(execution) == {"step", "동시상한", "장수", "소요_ms"}


def test_이전_장을_참고하는_기존_작성은_항상_순차로_유지한다():
    prompts = []
    parent_thread = threading.get_ident()

    def ask(prompt):
        assert threading.get_ident() == parent_thread
        prompts.append(prompt)
        return _response()

    def forbidden_prepare():
        pytest.fail("기존 작성은 병렬 준비를 호출하지 않아야 한다")

    ask.parallel_safe = True
    ask.max_parallel_calls = WORKERS
    ask.prepare_parallel = forbidden_prepare
    compose_sections("가나다전자", _packets()[SECTION_IDS[0]], None, ask)
    assert len(prompts) == len(SECTION_IDS)
    assert "가나다전자는 반도체 검사 장비를 제조한다." in prompts[-1]


def test_검증된_packet만_부모에서_준비하고_그_문맥을_복사한다():
    initialized = contextvars.ContextVar("시험_초기화", default=False)
    parent = threading.get_ident()
    barrier = threading.Barrier(WORKERS)
    prepared = []

    def ask(prompt):
        assert initialized.get() is True
        assert threading.get_ident() != parent
        barrier.wait(timeout=WAIT_SECONDS)
        return _response()

    def prepare():
        assert threading.get_ident() == parent
        prepared.append(initialized.set(True))
        ask.parallel_safe = True
        ask.max_parallel_calls = WORKERS

    ask.prepare_parallel = prepare
    writer = _CallLedgerRecorder().wrap(ask, role="writer", validation_round=ValidationRound.PRIMARY,
                                         section_ids=SECTION_IDS)
    with pytest.raises(ValueError):
        compose_sections("가나다전자", (), None, writer, section_evidence_packets={})
    assert prepared == []
    try:
        compose_sections("가나다전자", (), None, writer, section_evidence_packets=_packets())
        assert len(prepared) == 1
    finally:
        initialized.reset(prepared[0])


def test_역순_완료에도_영수증이_정확한_장과_응답에_결속된다():
    recorder = _CallLedgerRecorder()
    barrier = threading.Barrier(WORKERS)
    completed = [threading.Event() for _ in range(WORKERS)]

    def ask(prompt):
        index = int(prompt)
        barrier.wait(timeout=WAIT_SECONDS)
        if index < WORKERS - 1:
            assert completed[index + 1].wait(WAIT_SECONDS)
        completed[index].set()
        return "응답 " + prompt

    tracked = recorder.wrap(ask, role="writer", validation_round=ValidationRound.PRIMARY,
                            section_ids=SECTION_IDS[:WORKERS])
    bound = [tracked.for_section(section) for section in SECTION_IDS[:WORKERS]]
    assert recorder.calls_for(ValidationRound.PRIMARY, role="writer") == 0
    assert recorder.freeze().records == ()
    assert run_section_jobs([partial(call, str(index)) for index, call in enumerate(bound)],
                            max_workers=WORKERS) == ["응답 " + str(index) for index in range(WORKERS)]
    records = recorder.freeze().records
    assert [record.sequence for record in records] == [1, 2, 3]
    assert [record.role_index for record in records] == [1, 2, 3]
    assert tuple(record.section_id for record in records) == SECTION_IDS[:WORKERS]
    with pytest.raises(RuntimeError, match="두 번"):
        bound[0]("0")
    assert len(recorder.freeze().records) == WORKERS


def test_실패도_실제_호출_한_번으로_기록한다():
    def ask(prompt):
        raise RuntimeError("실패")

    recorder = _CallLedgerRecorder()
    writer = recorder.wrap(ask, role="writer", validation_round=ValidationRound.PRIMARY,
                           section_ids=SECTION_IDS)
    with pytest.raises(RuntimeError):
        writer.for_section(SECTION_IDS[0])("입력")
    record, = recorder.freeze().records
    assert record.outcome == "failed"
    assert record.response_sha256 == ""
    assert record.error_kind == "RuntimeError"
