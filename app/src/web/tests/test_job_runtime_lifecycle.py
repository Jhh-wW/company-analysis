"""백그라운드 조사가 참조를 잃지 않고 서버 종료 때 안전하게 마감되는지 본다."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.features.budget.constants import SPEND_PHASE_OCR, SPEND_PHASE_PIPELINE
from src.features.cost_tracking import store as cost_store
from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
from src.features.posting_image.logic import PostingImageResult
from src.features.sharelink import tracks as share_tracks
from src.features.sharelink.constants import PUBLIC_BUCKET
from src.features.storage import db as storage_db
from src.features.storage import job_interruptions
from src.web import job_runtime, main, runtime


class _WaitingDemoPipeline:
    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def run(self, *_args, **_kwargs) -> RunResult:
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise AssertionError("종료 유예 시험의 파이프라인이 제때 풀리지 않았습니다")
        return RunResult(outcome=Outcome.GATE_STOPPED)


def _job(job_id: str) -> job_runtime.Job:
    return job_runtime.Job(
        job_id=job_id,
        user_input=UserInput(company="테스트회사", job="개발", region="서울"),
        card=CompanyCard(
            legal_name="테스트회사",
            typed_name="테스트회사",
            address="서울",
            ceo="대표",
            founded="20200101",
            ref="demo-ref",
        ),
    )


def test_회사식별과_본조사_AI비용이_한_보고서의_단계별_원가로_합쳐진다(monkeypatch):
    identify_event = cost_store.AiCostEvent(
        stage="company_identification",
        model_id="claude-identify",
        input_tokens=100,
        output_tokens=10,
        cost_krw=2.0,
    )
    report_event = cost_store.AiCostEvent(
        stage="report_generation",
        model_id="claude-report",
        input_tokens=200,
        output_tokens=20,
        cost_krw=3.0,
    )

    class _CostPipeline:
        @staticmethod
        def run(*_args, **_kwargs):
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                cost_krw=3.0,
                model="claude-report",
                ai_cost_events=(report_event,),
            )

    job = _job("combined-cost-events")
    job.upfront_cost_krw = 2.0
    job.upfront_models = ("claude-identify",)
    job.upfront_cost_events = (identify_event,)
    monkeypatch.setattr(runtime, "_PIPELINE", _CostPipeline())
    monkeypatch.setattr(job_runtime, "record_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(job_runtime, "_save_report", lambda _job: True)
    monkeypatch.setattr(job_runtime, "_release_run_slot", lambda _bucket: None)

    asyncio.run(job_runtime._run_job(job))

    assert job.result is not None
    assert job.result.cost_krw == 5.0
    assert job.result.ai_cost_events == (identify_event, report_event)
    with storage_db.connect() as conn:
        rows = conn.execute(
            f"SELECT stage, cost_krw FROM {cost_store.AI_EVENT_TABLE} "
            "WHERE run_id=? ORDER BY sequence",
            (job.job_id,),
        ).fetchall()
    assert [(row["stage"], row["cost_krw"]) for row in rows] == [
        ("company_identification", 2.0),
        ("report_generation", 3.0),
    ]


def test_등록한_작업을_강하게_참조하고_종료시_완료까지_유예한다(monkeypatch):
    pipeline = _WaitingDemoPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    job_runtime._TASKS.clear()
    job = _job("tracked-job")

    async def scenario() -> None:
        task = job_runtime._schedule_job(job)
        assert task in job_runtime._TASKS

        deadline = asyncio.get_running_loop().time() + 1
        while not pipeline.entered.is_set():
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("파이프라인 작업이 시작되지 않았습니다")
            await asyncio.sleep(0.01)

        draining = asyncio.create_task(job_runtime._drain_job_tasks())
        await asyncio.sleep(0.02)
        assert not draining.done(), "실행 중 작업을 기다리지 않고 종료했습니다"

        pipeline.release.set()
        await draining
        await asyncio.sleep(0)

        assert task.done()
        assert task not in job_runtime._TASKS

    asyncio.run(scenario())
    assert job.finished


def test_앱_lifespan_종료가_작업_마감을_호출한다(monkeypatch):
    calls = 0

    async def counted_drain() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(job_runtime, "_drain_job_tasks", counted_drain)
    with TestClient(main.app):
        pass

    assert calls == 1


class _BlockingUpload:
    filename = "posting.png"

    def __init__(self, *, block_read: bool) -> None:
        self.block_read = block_read
        self.read_started = asyncio.Event()
        self.close_started = asyncio.Event()
        self.close_release = asyncio.Event()
        self.close_calls = 0
        self.closed = False

    async def read(self, _size: int) -> bytes:
        self.read_started.set()
        if self.block_read:
            await asyncio.Event().wait()
        return b""

    async def close(self) -> None:
        self.close_calls += 1
        self.close_started.set()
        await self.close_release.wait()
        self.closed = True


class _BarrierUpload:
    filename = "posting.png"

    def __init__(self, *, block_read: bool) -> None:
        self.block_read = block_read
        self.read_started = asyncio.Event()
        self.read_release = asyncio.Event()
        self.read_count = 0
        self.closed = False

    async def read(self, _size: int) -> bytes:
        self.read_count += 1
        if self.read_count > 1:
            return b""
        self.read_started.set()
        if self.block_read:
            await self.read_release.wait()
        return b"image-bytes"

    async def close(self) -> None:
        self.closed = True


class _NeverPipeline:
    def __init__(self) -> None:
        self.run_calls = 0

    def run(self, *_args, **_kwargs) -> RunResult:
        self.run_calls += 1
        raise AssertionError("shutdown admission 뒤 pipeline을 부르면 안 됩니다")


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/run",
            "raw_path": b"/run",
            "query_string": b"",
            "headers": [],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )


def test_업로드읽기와_close중_반복취소도_모든파일을_닫고_슬롯을_한번만_푼다(
    monkeypatch,
):
    releases: list[str] = []
    monkeypatch.setattr(job_runtime, "_release_run_slot", releases.append)

    async def scenario() -> tuple[_BlockingUpload, _BlockingUpload]:
        first = _BlockingUpload(block_read=True)
        second = _BlockingUpload(block_read=False)
        task = asyncio.create_task(
            job_runtime._start_with_reserved_slot(
                request=_request(),
                original_input=UserInput(company="회사", job="직무", region="서울"),
                card=_job("upload-cancel").card,
                posting_images=[first, second],
                posting_image_consent=True,
                is_paid=False,
                resolved_track=(share_tracks.Track.PUBLIC, PUBLIC_BUCKET, 0.0),
                run_id="upload-cancel",
                upfront_cost=0.0,
                upfront_models=(),
                upfront_elapsed=0.0,
                slot_bucket_id="reserved-bucket",
            )
        )
        await first.read_started.wait()
        task.cancel()
        await first.close_started.wait()
        # close가 오래 걸려도 슬롯 반환은 이미 끝난 동기 경계여야 한다.
        assert releases == ["reserved-bucket"]
        task.cancel()
        first.close_release.set()
        second.close_release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        return first, second

    first, second = asyncio.run(scenario())
    assert releases == ["reserved-bucket"]
    assert first.closed and second.closed
    assert first.close_calls >= 1 and second.close_calls >= 1


def test_개별_upload_close의_CancelledError가_다음파일_close를_막지_않는다():
    closed: list[str] = []

    class CancellingUpload:
        async def close(self) -> None:
            raise asyncio.CancelledError

    class ClosingUpload:
        async def close(self) -> None:
            closed.append("closed")

    asyncio.run(job_runtime._close_posting_images([CancellingUpload(), ClosingUpload()]))
    assert closed == ["closed"]


def test_한_upload_close가_지연돼도_모든_close를_시도하고_제한시간에_끝낸다(
    monkeypatch,
):
    attempted: list[str] = []
    monkeypatch.setattr(job_runtime, "_UPLOAD_CLOSE_TIMEOUT_SEC", 0.01)

    class HangingUpload:
        async def close(self) -> None:
            attempted.append("hanging")
            await asyncio.Event().wait()

    class ClosingUpload:
        async def close(self) -> None:
            attempted.append("closed")

    async def scenario() -> None:
        started = asyncio.get_running_loop().time()
        await job_runtime._close_posting_images([HangingUpload(), ClosingUpload()])
        assert asyncio.get_running_loop().time() - started < 0.2

    asyncio.run(scenario())
    assert attempted == ["hanging", "closed"]


def test_shutdown은_제한시간뒤_task를_취소하고_슬롯을_정리한다(monkeypatch):
    pipeline = _WaitingDemoPipeline()
    releases: list[str] = []
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(job_runtime, "record_run", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(job_runtime, "_release_run_slot", releases.append)
    job = _job("shutdown-timeout")
    job.slot_bucket_id = "shutdown-slot"

    async def scenario() -> None:
        job_runtime._start_job_runtime()
        task = job_runtime._schedule_job(job)
        deadline = asyncio.get_running_loop().time() + 1
        while not pipeline.entered.is_set():
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("종료 시험 worker가 시작되지 않았습니다")
            await asyncio.sleep(0.01)
        started = asyncio.get_running_loop().time()
        await job_runtime._drain_job_tasks(timeout_sec=0.01, cancel_grace_sec=0.2)
        assert asyncio.get_running_loop().time() - started < 0.5
        assert task.done()
        assert job.finished
        assert releases == ["shutdown-slot"]
        with storage_db.connect() as conn:
            assert job_interruptions.exists(conn, job.job_id)
        pipeline.release.set()

    try:
        asyncio.run(scenario())
    finally:
        pipeline.release.set()
        job_runtime._start_job_runtime()


def test_shutdown_admission은_새_task와_사용자요청을_503으로_거절한다():
    job_runtime._begin_job_shutdown()
    try:
        with pytest.raises(job_runtime.JobAdmissionClosed):
            job_runtime._schedule_job(_job("too-late"))
        response = job_runtime._admission_unavailable_response(_request())
        assert response.status_code == 503
        assert response.headers["retry-after"] == "3"
        assert response.headers["cache-control"] == "private, no-store"
        assert "안전하게 종료 중" in response.body.decode("utf-8")
    finally:
        job_runtime._start_job_runtime()


def test_이미지_read중_shutdown이면_OCR과_pipeline_phase를_시작하지_않는다(
    monkeypatch,
):
    upload: _BarrierUpload
    phases: list[str] = []
    releases: list[str] = []
    ends: list[dict] = []
    ocr_calls = 0
    pipeline = _NeverPipeline()

    def begin_phase(*, phase: str, **_kwargs):
        phases.append(phase)
        return phase

    def extract(*_args, **_kwargs):
        nonlocal ocr_calls
        ocr_calls += 1
        raise AssertionError("shutdown admission 뒤 OCR provider를 부르면 안 됩니다")

    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(job_runtime, "_begin_paid_phase", begin_phase)
    monkeypatch.setattr(job_runtime, "extract_posting_text", extract)
    monkeypatch.setattr(job_runtime, "record_end", lambda **kwargs: ends.append(kwargs))
    monkeypatch.setattr(job_runtime, "_release_run_slot", releases.append)

    async def scenario():
        nonlocal upload
        job_runtime._start_job_runtime()
        upload = _BarrierUpload(block_read=True)
        task = asyncio.create_task(
            job_runtime._start_with_reserved_slot(
                request=_request(),
                original_input=UserInput(company="회사", job="직무", region="서울"),
                card=_job("read-shutdown-race").card,
                posting_images=[upload],
                posting_image_consent=True,
                is_paid=True,
                resolved_track=(share_tracks.Track.LINK, "share-key", 100.0),
                run_id="read-shutdown-race",
                upfront_cost=10.0,
                upfront_models=("lookup-model",),
                upfront_elapsed=0.1,
                slot_bucket_id="reserved-slot",
            )
        )
        await upload.read_started.wait()
        job_runtime._begin_job_shutdown()
        upload.read_release.set()
        return await task

    try:
        response = asyncio.run(scenario())
    finally:
        job_runtime._start_job_runtime()

    assert response.status_code == 503
    assert response.headers["retry-after"] == "3"
    assert phases == []
    assert ocr_calls == pipeline.run_calls == 0
    assert job_runtime._JOBS == {}
    assert job_runtime._TASKS == set()
    assert releases == ["reserved-slot"]
    assert upload.closed
    assert len(ends) == 1


def test_OCR완료와_pipeline_phase사이_shutdown이면_새_phase와_provider가_0회다(
    monkeypatch,
):
    phases: list[str] = []
    settled: list[str] = []
    cancelled: list[str] = []
    releases: list[str] = []
    ends: list[dict] = []
    ocr_calls = 0
    pipeline = _NeverPipeline()
    upload = _BarrierUpload(block_read=False)

    def begin_phase(*, phase: str, **_kwargs):
        phases.append(phase)
        return SimpleNamespace(phase=phase, reserved_krw=100.0)

    def settle_phase(ticket, **_kwargs) -> None:
        settled.append(ticket.phase)
        if ticket.phase == SPEND_PHASE_OCR:
            job_runtime._begin_job_shutdown()

    def extract(*_args, **_kwargs) -> PostingImageResult:
        nonlocal ocr_calls
        ocr_calls += 1
        return PostingImageResult(
            ok=True,
            text="채용 공고 본문",
            cost_krw=7.0,
            model="ocr-model",
        )

    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(job_runtime, "_begin_paid_phase", begin_phase)
    monkeypatch.setattr(job_runtime, "_settle_paid_phase", settle_phase)
    monkeypatch.setattr(
        job_runtime, "_cancel_paid_phase", lambda ticket: cancelled.append(ticket.phase)
    )
    monkeypatch.setattr(job_runtime, "extract_posting_text", extract)
    monkeypatch.setattr(job_runtime, "record_end", lambda **kwargs: ends.append(kwargs))
    monkeypatch.setattr(job_runtime, "_release_run_slot", releases.append)

    async def scenario():
        job_runtime._start_job_runtime()
        return await job_runtime._start_with_reserved_slot(
            request=_request(),
            original_input=UserInput(company="회사", job="직무", region="서울"),
            card=_job("ocr-shutdown-race").card,
            posting_images=[upload],
            posting_image_consent=True,
            is_paid=True,
            resolved_track=(share_tracks.Track.LINK, "share-key", 100.0),
            run_id="ocr-shutdown-race",
            upfront_cost=10.0,
            upfront_models=("lookup-model",),
            upfront_elapsed=0.1,
            slot_bucket_id="reserved-slot",
        )

    try:
        response = asyncio.run(scenario())
    finally:
        job_runtime._start_job_runtime()

    assert response.status_code == 503
    assert phases == [SPEND_PHASE_OCR]
    assert SPEND_PHASE_PIPELINE not in phases
    assert ocr_calls == 1
    assert pipeline.run_calls == 0
    assert settled == [SPEND_PHASE_OCR]
    assert cancelled == []
    assert job_runtime._JOBS == {}
    assert job_runtime._TASKS == set()
    assert releases == ["reserved-slot"]
    assert upload.closed
    assert len(ends) == 1
