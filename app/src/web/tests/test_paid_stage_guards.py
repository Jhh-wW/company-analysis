"""P-125·P-126 — 첫 유료 호출 전 차단과 단계 비용·재시작 복원을 묶어 본다.

실제 AI는 한 번도 부르지 않는다. 회사 식별·OCR·본조사 모두 비용 숫자를 돌려주는
가짜를 쓰며, 되돌리면 각 spy 호출 수나 총액이 바로 달라지게 한다.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import re
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from starlette.datastructures import UploadFile as StarletteUploadFile

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.core import clock
from src.features.budget import logic as budget_logic
from src.features.budget import spend_store
from src.features.budget.constants import (
    MAX_CONCURRENT_PER_LINK,
    MAX_CONCURRENT_RUNS,
    PAID_PHASE_PROVIDER_BUDGET_KRW,
    SPEND_PHASE_IDENTIFY,
    SPEND_PHASE_OCR,
    SPEND_PHASE_PIPELINE,
)
from src.features.observability.records import read_records
from src.features.observability import constants as obs
from src.features.observability import lifecycle
from src.features.pipeline.port import (
    CompanyCard,
    CompanyLookupResult,
    Outcome,
    RunResult,
    UserInput,
)
from src.features.posting_image import constants as image_constants
from src.features.posting_image import logic as image_logic
from src.features.sharelink import logic as share_logic
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import store as share_store
from src.features.sharelink import tracks as share_tracks
from src.features.sharelink.constants import (
    KEY_COOKIE_NAME,
    PER_LINK_DAILY_BUDGET_KRW,
)
from src.features.storage import db as storage_db
from src.web import main
from src.web import job_runtime, paid_runtime, recording, request_helpers, runtime
from src.web.recording import record_run, records_path

_LINK_A = "a1b2c3d4e5f60718a1b2c3d4e5f60718"
_LINK_B = "b1b2c3d4e5f60718b1b2c3d4e5f60718"
_FORM = {
    "company": "가나다전자",
    "job": "영업",
    "region": "서울",
    "posting_text": "채용 공고 원문",
}


def _valid_png() -> bytes:
    output = BytesIO()
    Image.new("RGB", (2, 2), color=(255, 255, 255)).save(output, "PNG")
    return output.getvalue()


_VALID_PNG = _valid_png()


class FakePaidPipeline:
    def __init__(self, *, lookup_cost: float = 10.0, pipeline_cost: float = 30.0):
        self.lookup_cost = lookup_cost
        self.pipeline_cost = pipeline_cost
        self.lookup_calls = 0
        self.run_calls = 0

    def find_company_metered(self, user_input: UserInput) -> CompanyLookupResult:
        self.lookup_calls += 1
        return CompanyLookupResult(
            card=CompanyCard(
                legal_name=user_input.company,
                typed_name=user_input.company,
                address="서울",
                ceo="대표",
                founded="20200101",
                ref="corp-001",
            ),
            cost_krw=self.lookup_cost,
            model="lookup-model",
        )

    def run(self, *_args, **_kwargs) -> RunResult:
        self.run_calls += 1
        return RunResult(
            outcome=Outcome.GATE_STOPPED,
            cost_krw=self.pipeline_cost,
            model="pipeline-model",
        )


class BlockingLookupPipeline(FakePaidPipeline):
    """정해진 수가 회사 식별에 들어올 때까지 잡아 두는 무과금 동시성 가짜."""

    def __init__(self, target: int):
        super().__init__()
        self.target = target
        self.entered = 0
        self.lock = threading.Lock()
        self.all_entered = threading.Event()
        self.release = threading.Event()

    def find_company_metered(self, user_input: UserInput) -> CompanyLookupResult:
        with self.lock:
            self.lookup_calls += 1
            self.entered += 1
            if self.entered == self.target:
                self.all_entered.set()
        if not self.release.wait(timeout=10):
            raise AssertionError("동시성 시험의 회사 식별을 제때 풀지 못했습니다")
        return CompanyLookupResult(
            card=CompanyCard(
                legal_name=user_input.company,
                typed_name=user_input.company,
                address="서울",
                ceo="대표",
                founded="20200101",
                ref="corp-001",
            ),
            cost_krw=self.lookup_cost,
            model="lookup-model",
        )


def _install_analysis_csrf(client: TestClient) -> None:
    """이 파일의 비용·원장 시험이 새 CSRF 입구를 정상 폼처럼 지나게 한다."""
    if getattr(client, "_analysis_csrf_installed", False):
        return
    original_post = client.post

    def post_with_csrf(url, *args, **kwargs):
        if url in {"/confirm", "/reject", "/run"}:
            data = dict(kwargs.pop("data", {}) or {})
            secret = (
                client.cookies.get(auth_constants.SESSION_COOKIE_NAME)
                or client.cookies.get(KEY_COOKIE_NAME)
                or ""
            )
            if secret:
                data.setdefault(
                    "csrf_token", auth_logic.csrf_token_for_session(secret)
                )
            kwargs["data"] = data
        return original_post(url, *args, **kwargs)

    client.post = post_with_csrf
    client._analysis_csrf_installed = True


def _발급(client: TestClient, key: str = _LINK_A) -> None:
    with storage_db.connect() as conn:
        share_store.save(
            conn,
            key=key,
            company="가나다전자",
            job="영업",
            now_iso="2026-08-17T10:00:00",
        )
    client.cookies.set(KEY_COOKIE_NAME, key)
    _install_analysis_csrf(client)


def _로그인(client: TestClient, email: str) -> None:
    session = auth_logic.create_session(email, False)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    _install_analysis_csrf(client)


def _초대하고_로그인(client: TestClient, email: str) -> None:
    with storage_db.connect() as conn:
        share_allow.invite(
            conn,
            email=email,
            note="동시성 시험",
            now_iso="2026-08-17T10:00:00",
        )
    _로그인(client, email)


def _confirm(client: TestClient):
    return client.post("/confirm", data=_FORM, follow_redirects=False)


def _확인값(html: str) -> tuple[str, str]:
    token = re.search(r'name="paid_attempt_token" value="([^"]+)"', html)
    ref = re.search(r'name="ref" value="([^"]+)"', html)
    assert token is not None and ref is not None
    return token.group(1), ref.group(1)


def _run_form(token: str, ref: str, **changes: str) -> dict[str, str]:
    form = {
        **_FORM,
        "legal_name": _FORM["company"],
        "ref": ref,
        "address": "서울",
        "paid_attempt_token": token,
        "posting_image_consent": "yes",
    }
    form.update(changes)
    return form


def _기다린다(client: TestClient, response) -> str:
    assert response.status_code == 303
    job_id = response.headers["location"].rsplit("/", 1)[-1]
    for _ in range(100):
        if client.get(f"/api/progress/{job_id}").json()["finished"]:
            return job_id
        time.sleep(0.01)
    raise AssertionError("가짜 본조사가 끝나지 않았습니다")


def test_공개손님_confirm은_회사식별을_한번도_부르지_않는다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)

    response = _confirm(client)

    assert response.status_code == 403
    assert pipeline.lookup_calls == 0


def test_로그인만_하고_초대받지_않은_confirm도_식별을_안_부른다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)
    _로그인(client, "stranger@example.com")

    response = _confirm(client)

    assert response.status_code == 429
    assert pipeline.lookup_calls == 0


def test_동시실행이_꽉_차면_confirm_식별보다_먼저_막는다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(paid_runtime, "_RUNNING", MAX_CONCURRENT_RUNS)
    client = TestClient(main.app)
    _발급(client)

    assert _confirm(client).status_code == 429
    assert pipeline.lookup_calls == 0


def test_로그인_사용자_다섯명은_함께_식별하고_여섯번째는_기다린다(monkeypatch):
    pipeline = BlockingLookupPipeline(target=MAX_CONCURRENT_RUNS)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    # 이 시험은 동시 자리만 본다. 같은 시험 클라이언트 IP의 속도 제한과 분리한다.
    monkeypatch.setattr(request_helpers, "RATE_MAX_RUNS", 100)
    clients = [TestClient(main.app) for _ in range(MAX_CONCURRENT_RUNS + 1)]
    for index, client in enumerate(clients):
        _초대하고_로그인(client, f"member-{index}@example.com")

    try:
        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_RUNS) as pool:
            futures = [pool.submit(_confirm, client) for client in clients[:-1]]
            assert pipeline.all_entered.wait(timeout=10)

            sixth = _confirm(clients[-1])
            assert sixth.status_code == 429
            assert "진행 중" in sixth.text
            assert pipeline.lookup_calls == MAX_CONCURRENT_RUNS

            pipeline.release.set()
            assert [future.result(timeout=10).status_code for future in futures] == [
                200
            ] * MAX_CONCURRENT_RUNS
    finally:
        pipeline.release.set()
        for client in clients:
            client.close()

    assert paid_runtime._RUNNING == 0
    assert paid_runtime._RUNNING_BY_BUCKET == {}


def test_같은_로그인계정의_두번째_식별은_provider전에_기다린다(monkeypatch):
    pipeline = BlockingLookupPipeline(target=1)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(request_helpers, "RATE_MAX_RUNS", 100)
    first_client = TestClient(main.app)
    second_client = TestClient(main.app)
    email = "same-member@example.com"
    _초대하고_로그인(first_client, email)
    _로그인(second_client, email)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            first = pool.submit(_confirm, first_client)
            assert pipeline.all_entered.wait(timeout=10)

            second = _confirm(second_client)
            assert second.status_code == 429
            assert "진행 중" in second.text
            assert pipeline.lookup_calls == 1

            pipeline.release.set()
            assert first.result(timeout=10).status_code == 200
    finally:
        pipeline.release.set()
        first_client.close()
        second_client.close()

    assert paid_runtime._RUNNING == 0
    assert paid_runtime._RUNNING_BY_BUCKET == {}


def test_같은_초대링크는_세명까지_식별하고_네번째는_기다린다(monkeypatch):
    target = MAX_CONCURRENT_PER_LINK
    pipeline = BlockingLookupPipeline(target=target)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(request_helpers, "RATE_MAX_RUNS", 100)
    clients = [TestClient(main.app) for _ in range(target + 1)]
    for client in clients:
        _발급(client, _LINK_A)

    try:
        with ThreadPoolExecutor(max_workers=target) as pool:
            futures = [pool.submit(_confirm, client) for client in clients[:target]]
            assert pipeline.all_entered.wait(timeout=10)

            fourth = _confirm(clients[-1])
            assert fourth.status_code == 429
            assert "진행 중" in fourth.text
            assert pipeline.lookup_calls == target

            pipeline.release.set()
            assert [future.result(timeout=10).status_code for future in futures] == [
                200
            ] * target
    finally:
        pipeline.release.set()
        for client in clients:
            client.close()

    assert paid_runtime._RUNNING == 0
    assert paid_runtime._RUNNING_BY_BUCKET == {}
    assert paid_runtime._ACTIVE_PAID_PHASES == set()
    assert paid_runtime._UNRESOLVED_BUCKETS == set()


def test_확인부터_본조사까지_속도횟수는_한번만_센다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)
    _발급(client)
    confirm = _confirm(client)
    token, ref = _확인값(confirm.text)

    job_id = _기다린다(
        client,
        client.post(
            "/run", data=_run_form(token, ref), follow_redirects=False
        ),
    )

    assert job_id
    assert sum(len(starts) for starts in paid_runtime._RATE_HISTORY.starts.values()) == 1


def test_회사분석은_레거시_이미지를_무시하고_식별_본조사비용만_남긴다(monkeypatch):
    pipeline = FakePaidPipeline(lookup_cost=10.0, pipeline_cost=30.0)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(
        job_runtime,
        "default_extract",
        lambda _images: image_logic.ExtractResult(
            text="읽은 공고", cost_krw=20.0, model="ocr-model"
        ),
    )
    with TestClient(main.app) as client:
        _발급(client)
        token, ref = _확인값(_confirm(client).text)

        response = client.post(
            "/run",
            data=_run_form(token, ref),
            files={
                "posting_images": ("posting.png", _VALID_PNG)
            },
            follow_redirects=False,
        )
        job_id = _기다린다(client, response)

        assert job_runtime._JOBS[job_id].result.cost_krw == 40.0
        assert job_runtime._JOBS[job_id].result.model == (
            "lookup-model + pipeline-model"
        )
        records = read_records(records_path()).records
        assert len(records) == 1
        assert records[0].run_id == job_id
        assert records[0].cost_krw == 40.0
        with storage_db.connect() as conn:
            spend_store.ensure_schema(conn)
            snapshot = spend_store.load_day(conn, dt.date.today())
            unresolved = spend_store.load_unresolved_day(conn, dt.date.today())
        assert snapshot.by_run == {job_id: 40.0}
        assert unresolved == frozenset()


def test_OCR_직전_예산검사에_걸리면_extractor를_안_부른다(monkeypatch):
    pipeline = FakePaidPipeline()
    calls = 0

    def extractor(_images):
        nonlocal calls
        calls += 1
        return image_logic.ExtractResult(text="부르면 안 됨")

    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(job_runtime, "default_extract", extractor)
    client = TestClient(main.app)
    _발급(client)
    token, ref = _확인값(_confirm(client).text)
    today = dt.date.today()
    paid_runtime._LINK_SPEND = share_logic.add_spend(
        share_logic.DailySpend(day=today),
        spend_store.bucket_id(_LINK_A),
        today,
        PER_LINK_DAILY_BUDGET_KRW,
    )

    response = client.post(
        "/run",
        data=_run_form(token, ref),
        files={"posting_images": ("posting.png", _VALID_PNG)},
        follow_redirects=False,
    )

    assert response.status_code == 429
    assert calls == 0
    assert pipeline.run_calls == 0


def test_위조한_입력과_ref는_일회용_확인을_통과하지_못한다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)
    _발급(client)
    token, ref = _확인값(_confirm(client).text)

    wrong_input = client.post(
        "/run",
        data=_run_form(token, ref, company="다른회사"),
        follow_redirects=False,
    )
    wrong_ref = client.post(
        "/run",
        data=_run_form(token, "forged-ref"),
        follow_redirects=False,
    )

    # 회사 링크의 회사·직무 범위는 일회용 확인 토큰 검사보다 앞선 서버 인가 경계다.
    assert wrong_input.status_code == 403
    assert wrong_ref.status_code == 303
    assert pipeline.run_calls == 0


def test_확인뒤_통장을_바꾸면_토큰을_쓸_수_없다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)
    _발급(client, _LINK_A)
    _발급(client, _LINK_B)
    client.cookies.set(KEY_COOKIE_NAME, _LINK_A)
    token, ref = _확인값(_confirm(client).text)
    client.cookies.set(KEY_COOKIE_NAME, _LINK_B)

    response = client.post(
        "/run", data=_run_form(token, ref), follow_redirects=False
    )

    assert response.status_code == 303
    assert pipeline.run_calls == 0


def test_레거시_이미지는_OCR없이_분석하고_토큰은_재사용하지_못한다(monkeypatch):
    pipeline = FakePaidPipeline()
    calls = 0

    def failed_ocr(_images):
        nonlocal calls
        calls += 1
        return image_logic.ExtractResult(
            text="", cost_krw=20.0, model="ocr-model"
        )

    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(job_runtime, "default_extract", failed_ocr)
    client = TestClient(main.app)
    _발급(client)
    token, ref = _확인값(_confirm(client).text)
    request = dict(
        data=_run_form(token, ref),
        files={"posting_images": ("posting.png", _VALID_PNG)},
        follow_redirects=False,
    )

    first = client.post("/run", **request)
    _기다린다(client, first)
    second = client.post("/run", **request)

    assert first.status_code == 303
    assert second.status_code == 303
    assert paid_runtime._RUNNING == 0
    assert calls == 0
    assert pipeline.run_calls == 1
    records = read_records(records_path()).records
    assert len(records) == 1
    assert records[0].end_step != "05.5_이미지입력"


def test_레거시_이미지의_OCR_provider는_호출하지_않는다(monkeypatch):
    pipeline = FakePaidPipeline()
    calls = 0

    def timeout(_images):
        nonlocal calls
        calls += 1
        raise TimeoutError("provider 응답 불명")

    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(job_runtime, "default_extract", timeout)
    client = TestClient(main.app)
    _발급(client)
    token, ref = _확인값(_confirm(client).text)

    response = client.post(
        "/run",
        data=_run_form(token, ref),
        files={"posting_images": ("posting.png", _VALID_PNG)},
        follow_redirects=False,
    )

    _기다린다(client, response)

    assert response.status_code == 303
    assert paid_runtime._RUNNING == 0
    assert pipeline.run_calls == 1
    assert calls == 0
    with storage_db.connect() as conn:
        unresolved = spend_store.load_unresolved_day(conn, dt.date.today())
    assert spend_store.bucket_id(_LINK_A) not in unresolved


def test_레거시_이미지의_OCR_계약은_회사분석_실행에_관여하지_않는다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(job_runtime, "default_extract", lambda _images: None)
    client = TestClient(main.app, raise_server_exceptions=False)
    _발급(client)
    token, ref = _확인값(_confirm(client).text)

    response = client.post(
        "/run",
        data=_run_form(token, ref),
        files={"posting_images": ("posting.png", _VALID_PNG)},
        follow_redirects=False,
    )

    _기다린다(client, response)

    assert response.status_code == 303
    assert pipeline.run_calls == 1
    assert paid_runtime._RUNNING == 0
    assert paid_runtime._ACTIVE_PAID_PHASES == set()
    assert (dt.date.today().isoformat(), spend_store.bucket_id(_LINK_A)) not in (
        paid_runtime._UNRESOLVED_BUCKETS
    )
    with storage_db.connect() as conn:
        rows = spend_store.list_inflight_day(conn, dt.date.today())
    assert [(row.phase, row.bucket_id) for row in rows] == []


def test_본조사_provider예외는_알려진비용과_미확정표식을_함께_남긴다(monkeypatch):
    class UncertainPipeline(FakePaidPipeline):
        def run(self, *_args, **_kwargs):
            self.run_calls += 1
            # 1판 내부가 provider 오류를 공고 폐기로 잘못 접어도, 과금 불확실 신호가
            # 최종 분류를 기술 실패로 바로잡아야 한다.
            return RunResult(
                outcome=Outcome.POSTING_DISCARDED,
                cost_krw=30.0,
                model="pipeline-model",
                billing_uncertain=True,
            )

    pipeline = UncertainPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)
    _발급(client)
    token, ref = _확인값(_confirm(client).text)

    job_id = _기다린다(
        client,
        client.post("/run", data=_run_form(token, ref), follow_redirects=False),
    )

    assert job_runtime._JOBS[job_id].result.outcome is Outcome.FAILED
    assert job_runtime._JOBS[job_id].result.cost_krw == 40.0
    assert _confirm(client).status_code == 429
    with storage_db.connect() as conn:
        snapshot = spend_store.load_day(conn, dt.date.today())
        unresolved = spend_store.load_unresolved_day(conn, dt.date.today())
    assert snapshot.by_run[job_id] == 40.0
    assert spend_store.bucket_id(_LINK_A) in unresolved


def test_비용원장_쓰기실패뒤에는_다음_AI호출을_닫는다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)
    _발급(client)

    def broken_store(*_args, **_kwargs):
        raise OSError("시험용 저장 실패")

    monkeypatch.setattr(spend_store, "append_spend", broken_store)
    first = _confirm(client)
    second = _confirm(client)

    assert first.status_code == 200, "이미 쓴 식별 결과 화면까지 버리면 안 된다"
    assert second.status_code == 429
    assert pipeline.lookup_calls == 1
    assert paid_runtime._BUDGET_STORE_HEALTHY is False


def test_재시작때_원장_일부단계와_이력총액의_차이를_같은통장에_보충한다():
    today = dt.date.today()
    user_input = UserInput(company="가나다", job="영업", region="서울")
    with storage_db.connect() as conn:
        spend_store.ensure_schema(conn)
        spend_store.append_spend(
            conn,
            run_id="partial-run",
            phase=SPEND_PHASE_IDENTIFY,
            day=today,
            bucket=_LINK_A,
            cost_krw=10.0,
            created_at=dt.datetime.now().isoformat(),
        )
    record_run(
        user_input,
        RunResult(
            outcome=Outcome.NOT_FOUND,
            cost_krw=60.0,
            model="test-model",
        ),
        1.0,
        run_id="partial-run",
    )
    paid_runtime._LEDGER = budget_logic.Ledger(day=today)
    paid_runtime._LINK_SPEND = share_logic.DailySpend(day=today)

    paid_runtime._seed_ledger()

    bucket = spend_store.bucket_id(_LINK_A)
    assert paid_runtime._LEDGER.spent_krw == 60.0
    assert share_logic.spent_for(paid_runtime._LINK_SPEND, bucket, today) == 60.0


def test_발급되지_않은_16진수_쿠키는_새_링크통장을_만들지_못한다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)
    client.cookies.set(KEY_COOKIE_NAME, _LINK_A)

    response = _confirm(client)

    assert response.status_code == 403
    assert pipeline.lookup_calls == 0


def test_seed_전에는_빈_장부를_믿고_식별을_열지_않는다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(paid_runtime, "_BUDGET_STORE_HEALTHY", False)
    client = TestClient(main.app)
    _발급(client)

    assert _confirm(client).status_code == 429
    assert pipeline.lookup_calls == 0


def test_유료_알맹이에_계량식별_약속이_없으면_옛_함수를_부르지_않는다(monkeypatch):
    class LegacyPaidPipeline:
        def __init__(self):
            self.lookup_calls = 0

        def find_company(self, _user_input):
            self.lookup_calls += 1
            raise AssertionError("0원으로 기록될 옛 유료 함수를 부르면 안 됨")

        def run(self, *_args):
            raise AssertionError("본조사도 부르면 안 됨")

    pipeline = LegacyPaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)
    _발급(client)

    response = _confirm(client)

    assert response.status_code == 200
    assert pipeline.lookup_calls == 0
    assert "회사 정보를 불러오지 못했습니다" in response.text
    assert "찾지 못했습니다" not in response.text


def test_식별_기술실패는_회사없음이_아닌_별도_오류로_관측한다(monkeypatch):
    class FailedLookup(FakePaidPipeline):
        def find_company_metered(self, _user_input):
            self.lookup_calls += 1
            return CompanyLookupResult(card=None, cost_krw=10.0, failed=True)

    pipeline = FailedLookup()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)
    _발급(client)

    response = _confirm(client)

    assert response.status_code == 200
    assert "회사 정보를 불러오지 못했습니다" in response.text
    assert "찾지 못했습니다" not in response.text
    records = read_records(records_path()).records
    assert len(records) == 1
    assert records[0].end_step == "01_식별오류"


def test_식별_API예외는_알려진비용을_남기고_현재통장만_즉시_닫는다(monkeypatch):
    class UncertainLookup(FakePaidPipeline):
        def find_company_metered(self, user_input):
            if self.lookup_calls == 0:
                self.lookup_calls += 1
                return CompanyLookupResult(
                    card=None,
                    cost_krw=10.0,
                    failed=True,
                    billing_uncertain=True,
                )
            return super().find_company_metered(user_input)

    pipeline = UncertainLookup()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    first_client = TestClient(main.app)
    second_client = TestClient(main.app)
    _발급(first_client, _LINK_A)
    _발급(second_client, _LINK_B)

    first = _confirm(first_client)
    blocked = _confirm(first_client)
    other = _confirm(second_client)

    assert first.status_code == 200
    assert blocked.status_code == 429
    assert other.status_code == 200
    assert pipeline.lookup_calls == 2
    with storage_db.connect() as conn:
        snapshot = spend_store.load_day(conn, dt.date.today())
        unresolved = spend_store.load_unresolved_day(conn, dt.date.today())
    assert snapshot.by_bucket[spend_store.bucket_id(_LINK_A)] == 10.0
    assert spend_store.bucket_id(_LINK_A) in unresolved
    assert spend_store.bucket_id(_LINK_B) not in unresolved


def test_같은링크의_정상진행_세건은_장애표식으로_오인하지_않는다():
    tickets = [
        paid_runtime._begin_paid_phase(
            run_id=f"healthy-{index}",
            phase=SPEND_PHASE_IDENTIFY,
            share_key=_LINK_A,
        )
        for index in range(MAX_CONCURRENT_PER_LINK)
    ]
    assert all(tickets)
    assert len(paid_runtime._ACTIVE_PAID_PHASES) == MAX_CONCURRENT_PER_LINK
    assert paid_runtime._UNRESOLVED_BUCKETS == set()

    first, *rest = tickets
    assert first is not None
    paid_runtime._settle_paid_phase(first, amount_krw=10.0, billing_uncertain=False)

    # DB에는 두 inflight가 남아도 둘 다 이 프로세스가 정상 실행 중이므로
    # 재시작·API 예외와 같은 «결과 모름»으로 통장을 닫으면 안 된다.
    assert paid_runtime._UNRESOLVED_BUCKETS == set()
    assert len(paid_runtime._ACTIVE_PAID_PHASES) == MAX_CONCURRENT_PER_LINK - 1

    for ticket in rest:
        assert ticket is not None
        paid_runtime._cancel_paid_phase(ticket)
    assert paid_runtime._ACTIVE_PAID_PHASES == set()
    assert paid_runtime._UNRESOLVED_BUCKETS == set()


def test_같은링크_세건을_다른스레드에서_함께마감해도_거짓미확정이_생기지않는다():
    tickets = [
        paid_runtime._begin_paid_phase(
            run_id=f"concurrent-settle-{index}",
            phase=SPEND_PHASE_IDENTIFY,
            share_key=_LINK_A,
        )
        for index in range(MAX_CONCURRENT_PER_LINK)
    ]
    assert all(tickets)
    barrier = threading.Barrier(MAX_CONCURRENT_PER_LINK)

    def settle(ticket):
        barrier.wait(timeout=10)
        paid_runtime._settle_paid_phase(ticket, amount_krw=10.0, billing_uncertain=False)

    with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_PER_LINK) as pool:
        futures = [pool.submit(settle, ticket) for ticket in tickets]
        for future in futures:
            future.result(timeout=10)

    assert paid_runtime._ACTIVE_PAID_PHASES == set()
    assert paid_runtime._UNRESOLVED_BUCKETS == set()
    with storage_db.connect() as conn:
        snapshot = spend_store.load_day(conn, dt.date.today())
        inflight = spend_store.list_inflight_day(conn, dt.date.today())
    assert snapshot.total_krw == 30.0
    assert inflight == ()


def test_DB마감뒤_메모리장부실패도_active를_지우고_전체를_fail_closed한다(monkeypatch):
    ticket = paid_runtime._begin_paid_phase(
        run_id="memory-ledger-failure",
        phase=SPEND_PHASE_IDENTIFY,
        share_key=_LINK_A,
    )
    assert ticket is not None

    def broken_memory_spend(*_args, **_kwargs):
        raise RuntimeError("시험용 메모리 장부 실패")

    monkeypatch.setattr(share_logic, "add_spend", broken_memory_spend)
    paid_runtime._settle_paid_phase(ticket, amount_krw=10.0, billing_uncertain=False)

    assert not paid_runtime._BUDGET_STORE_HEALTHY
    assert paid_runtime._ACTIVE_PAID_PHASES == set()
    assert (dt.date.today().isoformat(), ticket.bucket_id) in (
        paid_runtime._UNRESOLVED_BUCKETS
    )
    with storage_db.connect() as conn:
        snapshot = spend_store.load_day(conn, dt.date.today())
        inflight = spend_store.list_inflight_day(conn, dt.date.today())
    assert snapshot.by_run == {"memory-ledger-failure": 10.0}
    assert inflight == ()


def test_to_thread_취소는_비용을_0원확정하지_않고_통장만_닫는다(monkeypatch):
    ticket = paid_runtime._begin_paid_phase(
        run_id="cancelled-run",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_LINK_A,
    )
    assert ticket is not None
    slot = paid_runtime._reserve_run_slot(share_tracks.Track.LINK, _LINK_A)
    assert slot is not None

    async def cancelled_to_thread(*_args, **_kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(job_runtime.asyncio, "to_thread", cancelled_to_thread)
    monkeypatch.setattr(job_runtime, "record_run", lambda *_args, **_kwargs: None)
    job = job_runtime.Job(
        job_id="cancelled-run",
        user_input=UserInput(company="가나다전자", job="영업", region="서울"),
        card=CompanyCard(
            legal_name="가나다전자",
            typed_name="가나다전자",
            address="서울",
            ceo="대표",
            founded="20200101",
            ref="corp-001",
        ),
        share_key=_LINK_A,
        paid_phase=ticket,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(job_runtime._run_job(job))

    unresolved_key = (dt.date.today().isoformat(), ticket.bucket_id)
    assert job.result is not None and job.result.billing_uncertain
    assert unresolved_key in paid_runtime._UNRESOLVED_BUCKETS
    assert paid_runtime._ACTIVE_PAID_PHASES == set()
    assert paid_runtime._RUNNING == 0
    assert paid_runtime._RUNNING_BY_BUCKET == {}
    with storage_db.connect() as conn:
        rows = spend_store.list_inflight_day(conn, dt.date.today())
    assert [(row.run_id, row.phase) for row in rows] == [
        ("cancelled-run", SPEND_PHASE_PIPELINE)
    ]


def test_바깥요청이_취소돼도_실제_worker가_끝날때까지_동시자리를_유지한다(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    class BlockingRunPipeline(FakePaidPipeline):
        def run(self, *_args, **_kwargs) -> RunResult:
            self.run_calls += 1
            entered.set()
            if not release.wait(timeout=10):
                raise AssertionError("취소 시험의 worker가 제때 풀리지 않았습니다")
            return RunResult(
                outcome=Outcome.GATE_STOPPED,
                cost_krw=self.pipeline_cost,
                model="pipeline-model",
            )

    pipeline = BlockingRunPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(job_runtime, "record_run", lambda *_args, **_kwargs: None)
    ticket = paid_runtime._begin_paid_phase(
        run_id="outer-cancelled-run",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_LINK_A,
    )
    assert ticket is not None
    slot = paid_runtime._reserve_run_slot(share_tracks.Track.LINK, _LINK_A)
    assert slot is not None
    other_slots = [
        paid_runtime._reserve_run_slot(
            share_tracks.Track.MEMBER, f"other-member-{index}@example.com"
        )
        for index in range(MAX_CONCURRENT_RUNS - 1)
    ]
    assert all(other_slots)
    job = job_runtime.Job(
        job_id="outer-cancelled-run",
        user_input=UserInput(company="가나다전자", job="영업", region="서울"),
        card=CompanyCard(
            legal_name="가나다전자",
            typed_name="가나다전자",
            address="서울",
            ceo="대표",
            founded="20200101",
            ref="corp-001",
        ),
        share_key=_LINK_A,
        paid_phase=ticket,
    )

    async def scenario() -> None:
        task = asyncio.create_task(job_runtime._run_job(job))
        deadline = asyncio.get_running_loop().time() + 5
        while not entered.is_set():
            if asyncio.get_running_loop().time() >= deadline:
                raise AssertionError("worker가 시작되지 않았습니다")
            await asyncio.sleep(0.01)

        task.cancel()
        await asyncio.sleep(0.05)

        # 브라우저 쪽 요청만 취소됐고 실제 provider worker는 여전히 돌고 있다.
        # 따라서 이 순간 자리를 반환하면 실제 동시 호출 수가 5를 넘을 수 있다.
        assert not task.done()
        assert paid_runtime._RUNNING == MAX_CONCURRENT_RUNS
        assert paid_runtime._RUNNING_BY_BUCKET[ticket.bucket_id] == 1
        assert (
            paid_runtime._reserve_run_slot(
                share_tracks.Track.MEMBER, "sixth-member@example.com"
            )
            is None
        )

        # 정리를 기다리는 도중 연결 계층이 취소를 한 번 더 보내도 worker는 계속 돈다.
        task.cancel()
        await asyncio.sleep(0.05)
        assert not task.done()
        assert paid_runtime._RUNNING == MAX_CONCURRENT_RUNS
        assert paid_runtime._RUNNING_BY_BUCKET[ticket.bucket_id] == 1
        assert (
            paid_runtime._reserve_run_slot(
                share_tracks.Track.MEMBER, "sixth-member@example.com"
            )
            is None
        )

        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())

    assert pipeline.run_calls == 1
    assert job.result is not None
    assert job.result.cost_krw == pipeline.pipeline_cost
    assert not job.result.billing_uncertain
    assert paid_runtime._RUNNING == MAX_CONCURRENT_RUNS - 1
    for stored_bucket in other_slots:
        assert stored_bucket is not None
        paid_runtime._release_run_slot(stored_bucket)
    assert paid_runtime._RUNNING == 0
    assert paid_runtime._RUNNING_BY_BUCKET == {}
    assert paid_runtime._ACTIVE_PAID_PHASES == set()
    assert paid_runtime._UNRESOLVED_BUCKETS == set()
    with storage_db.connect() as conn:
        assert spend_store.list_inflight_day(conn, dt.date.today()) == ()


def test_본조사_계약밖결과도_active고아없이_미확정으로_마감한다(monkeypatch):
    class InvalidPipeline(FakePaidPipeline):
        def run(self, *_args, **_kwargs):
            self.run_calls += 1
            return None

    pipeline = InvalidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(job_runtime, "record_run", lambda *_args, **_kwargs: None)
    ticket = paid_runtime._begin_paid_phase(
        run_id="invalid-result-run",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_LINK_A,
    )
    assert ticket is not None
    slot = paid_runtime._reserve_run_slot(share_tracks.Track.LINK, _LINK_A)
    assert slot is not None
    job = job_runtime.Job(
        job_id="invalid-result-run",
        user_input=UserInput(company="가나다전자", job="영업", region="서울"),
        card=CompanyCard(
            legal_name="가나다전자",
            typed_name="가나다전자",
            address="서울",
            ceo="대표",
            founded="20200101",
            ref="corp-001",
        ),
        share_key=_LINK_A,
        paid_phase=ticket,
    )

    asyncio.run(job_runtime._run_job(job))

    assert pipeline.run_calls == 1
    assert job.result is not None
    assert job.result.outcome is Outcome.FAILED
    assert job.result.billing_uncertain
    assert paid_runtime._RUNNING == 0
    assert paid_runtime._RUNNING_BY_BUCKET == {}
    assert paid_runtime._ACTIVE_PAID_PHASES == set()
    assert (dt.date.today().isoformat(), ticket.bucket_id) in (
        paid_runtime._UNRESOLVED_BUCKETS
    )
    with storage_db.connect() as conn:
        rows = spend_store.list_inflight_day(conn, dt.date.today())
    assert [(row.run_id, row.phase) for row in rows] == [
        ("invalid-result-run", SPEND_PHASE_PIPELINE)
    ]


def test_오늘_미확정표식을_재시작복원하면_그통장만_식별전에_막는다(monkeypatch):
    today = dt.date.today()
    with storage_db.connect() as conn:
        spend_store.ensure_schema(conn)
        spend_store.begin_inflight(
            conn,
            run_id="crashed-run",
            phase=SPEND_PHASE_IDENTIFY,
            day=today,
            bucket=_LINK_A,
            started_at=dt.datetime.now().isoformat(),
        )
    paid_runtime._seed_ledger()
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    first_client = TestClient(main.app)
    second_client = TestClient(main.app)
    _발급(first_client, _LINK_A)
    _발급(second_client, _LINK_B)

    blocked = _confirm(first_client)
    other = _confirm(second_client)

    assert blocked.status_code == 429
    assert other.status_code == 200
    assert pipeline.lookup_calls == 1


def test_어제_메모리표식은_오늘을_막지_않고_어제마감이_오늘표식을_지우지_않는다():
    today = dt.date.today()
    yesterday = today - dt.timedelta(days=1)
    stored_bucket = spend_store.bucket_id(_LINK_A)
    old = paid_runtime.PaidPhase(
        run_id="old", phase=SPEND_PHASE_IDENTIFY, day=yesterday,
        share_key=_LINK_A, bucket_id=stored_bucket,
    )
    with storage_db.connect() as conn:
        spend_store.ensure_schema(conn)
        spend_store.begin_inflight(
            conn, run_id="old", phase=SPEND_PHASE_IDENTIFY,
            day=yesterday, bucket=_LINK_A, started_at="어제",
        )
        spend_store.begin_inflight(
            conn, run_id="today", phase=SPEND_PHASE_IDENTIFY,
            day=today, bucket=_LINK_A, started_at="오늘",
        )
    paid_runtime._UNRESOLVED_BUCKETS = {
        (yesterday.isoformat(), stored_bucket),
        (today.isoformat(), stored_bucket),
    }
    paid_runtime._LEDGER = budget_logic.Ledger(day=today, spent_krw=5.0)
    paid_runtime._LINK_SPEND = share_logic.DailySpend(
        day=today, by_key={stored_bucket: 5.0}
    )

    paid_runtime._settle_paid_phase(old, amount_krw=10.0, billing_uncertain=False)

    assert (yesterday.isoformat(), stored_bucket) not in paid_runtime._UNRESOLVED_BUCKETS
    assert (today.isoformat(), stored_bucket) in paid_runtime._UNRESOLVED_BUCKETS
    assert paid_runtime._LEDGER.spent_krw == 5.0
    assert share_logic.spent_for(paid_runtime._LINK_SPEND, stored_bucket, today) == 5.0


def test_단계시작_DB실패는_provider를_부르기전에_막는다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(
        spend_store,
        "begin_inflight",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("시험용 실패")),
    )
    client = TestClient(main.app)
    _발급(client)

    response = _confirm(client)

    assert response.status_code == 429
    assert pipeline.lookup_calls == 0
    assert paid_runtime._RUNNING == 0


def test_회사분석은_OCR_표식을_만들지_않는다(monkeypatch):
    pipeline = FakePaidPipeline()
    calls = 0
    original_begin = spend_store.begin_inflight

    def fail_ocr(conn, **kwargs):
        if kwargs["phase"] == SPEND_PHASE_OCR:
            raise OSError("OCR 표식 실패")
        return original_begin(conn, **kwargs)

    def extractor(_images):
        nonlocal calls
        calls += 1
        return image_logic.ExtractResult(text="부르면 안 됨")

    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(spend_store, "begin_inflight", fail_ocr)
    monkeypatch.setattr(job_runtime, "default_extract", extractor)
    client = TestClient(main.app)
    _발급(client)
    token, ref = _확인값(_confirm(client).text)

    response = client.post(
        "/run",
        data=_run_form(token, ref),
        files={"posting_images": ("posting.png", _VALID_PNG)},
        follow_redirects=False,
    )

    _기다린다(client, response)

    assert response.status_code == 303
    assert calls == 0
    assert pipeline.run_calls == 1
    assert paid_runtime._RUNNING == 0


def test_레거시_업로드는_파일을_읽지_않고_토큰만_정상소모한다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app, raise_server_exceptions=False)
    _발급(client)
    token, ref = _확인값(_confirm(client).text)

    async def broken_read(_self, *_args, **_kwargs):
        raise OSError("시험용 업로드 읽기 실패")

    monkeypatch.setattr(StarletteUploadFile, "read", broken_read)
    request = dict(
        data=_run_form(token, ref),
        files={"posting_images": ("posting.png", _VALID_PNG)},
        follow_redirects=False,
    )

    first = client.post("/run", **request)
    _기다린다(client, first)
    assert first.status_code == 303
    assert client.post("/run", **request).status_code == 303
    assert paid_runtime._RUNNING == 0
    assert pipeline.run_calls == 1


def test_배경작업등록예외는_슬롯과_본조사표식을_함께_되돌린다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(
        job_runtime,
        "_schedule_job",
        lambda _job: (_ for _ in ()).throw(RuntimeError("시험용 등록 실패")),
    )
    client = TestClient(main.app, raise_server_exceptions=False)
    _발급(client)
    token, ref = _확인값(_confirm(client).text)

    response = client.post(
        "/run", data=_run_form(token, ref), follow_redirects=False
    )

    assert response.status_code == 500
    assert paid_runtime._RUNNING == 0
    assert job_runtime._JOBS == {}
    with storage_db.connect() as conn:
        assert spend_store.load_unresolved_day(conn, dt.date.today()) == frozenset()


def test_confirm의_가드와_원장은_한번_읽은_같은통장을_쓴다(monkeypatch):
    pipeline = FakePaidPipeline()
    calls = 0

    def changing_track(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return share_tracks.Track.LINK, _LINK_A, PER_LINK_DAILY_BUDGET_KRW
        return share_tracks.Track.PUBLIC, "public", 0.0

    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(request_helpers, "_track_of", changing_track)
    client = TestClient(main.app)
    _로그인(client, "track-test@example.com")
    with storage_db.connect() as conn:
        share_store.save(
            conn,
            key=_LINK_A,
            company=_FORM["company"],
            job=_FORM["job"],
            now_iso="2026-08-17T10:00:00",
        )

    response = _confirm(client)

    assert response.status_code == 200
    assert calls == 1
    with storage_db.connect() as conn:
        snapshot = spend_store.load_day(conn, dt.date.today())
    assert snapshot.by_bucket == {spend_store.bucket_id(_LINK_A): 10.0}


def test_확인_거절은_같은_요청을_확인종료로_한번만_마감한다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)
    _발급(client)
    token, _ref = _확인값(_confirm(client).text)
    run_id = job_runtime._PAID_ATTEMPTS[token].run_id

    response = client.post(
        "/reject",
        data={**_FORM, "retry": "0", "paid_attempt_token": token},
    )

    assert response.status_code == 200
    with storage_db.connect() as conn:
        lifecycle.ensure_schema(conn)
        entry = lifecycle.get_entry(conn, run_id)
        audit = lifecycle.list_audit(conn, run_id)
    assert entry is not None and entry.state == lifecycle.STATE_FINAL
    assert entry.final_record is not None
    assert entry.final_record.end_step == obs.END_STEP_CONFIRM
    assert [event.to_state for event in audit] == [
        lifecycle.STATE_PENDING,
        lifecycle.STATE_FINAL,
    ]


def test_확인_만료는_다음_정리에서_확인종료로_마감한다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)
    _발급(client)
    token, _ref = _확인값(_confirm(client).text)
    attempt = job_runtime._PAID_ATTEMPTS[token]

    job_runtime._sweep_jobs(attempt.created_at + job_runtime.JOB_KEEP_SEC + 1)

    assert token not in job_runtime._PAID_ATTEMPTS
    records = read_records(records_path()).records
    assert len(records) == 1
    assert records[0].run_id == attempt.run_id
    assert records[0].end_step == obs.END_STEP_CONFIRM


def test_재시작으로_토큰을_잃은_확인대기는_확인종료로_마감한다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)
    _발급(client)
    token, _ref = _확인값(_confirm(client).text)
    run_id = job_runtime._PAID_ATTEMPTS[token].run_id
    job_runtime._PAID_ATTEMPTS.clear()  # 서버 재시작 때 메모리 토큰이 사라진 상황

    paid_runtime._recover_observation_lifecycle()

    records = read_records(records_path()).records
    assert len(records) == 1
    assert records[0].run_id == run_id
    assert records[0].end_step == obs.END_STEP_CONFIRM


def test_재시작_running은_OCR만_이미지오류로_나머지는_생성실패로_마감한다():
    now = dt.datetime.now().replace(microsecond=0)
    cases = (
        ("restart-ocr", SPEND_PHASE_OCR, obs.END_STEP_IMAGE_ERROR),
        ("restart-pipeline", SPEND_PHASE_PIPELINE, obs.END_STEP_GENERATE),
        ("restart-before-provider", None, obs.END_STEP_GENERATE),
    )
    with storage_db.connect() as conn:
        lifecycle.ensure_schema(conn)
        spend_store.ensure_schema(conn)
        for run_id, phase, _expected in cases:
            lifecycle.begin_pending(
                conn,
                run_id=run_id,
                at=now.isoformat(),
                job="영업",
                confirmed_cost_krw=10.0,
                elapsed_sec=1.0,
                model="lookup-model",
                expires_at=(now + dt.timedelta(hours=1)).isoformat(),
            )
            lifecycle.mark_running(
                conn,
                run_id,
                event_at=(now + dt.timedelta(seconds=1)).isoformat(),
            )
            if phase is not None:
                spend_store.begin_inflight(
                    conn,
                    run_id=run_id,
                    phase=phase,
                    day=now.date(),
                    bucket=_LINK_A,
                    started_at=(now + dt.timedelta(seconds=2)).isoformat(),
                )

    paid_runtime._recover_observation_lifecycle()

    records = {record.run_id: record for record in read_records(records_path()).records}
    assert {
        run_id: records[run_id].end_step for run_id, _phase, _expected in cases
    } == {run_id: expected for run_id, _phase, expected in cases}


def test_깨진_재시작단계_한건이_정상_OCR과_본조사_복구를_막지않는다():
    now = dt.datetime.now().replace(microsecond=0)
    phases_by_run = {
        "restart-broken": (SPEND_PHASE_OCR, SPEND_PHASE_PIPELINE),
        "restart-healthy-ocr": (SPEND_PHASE_OCR,),
        "restart-healthy-pipeline": (SPEND_PHASE_PIPELINE,),
    }
    with storage_db.connect() as conn:
        lifecycle.ensure_schema(conn)
        spend_store.ensure_schema(conn)
        for run_id, phases in phases_by_run.items():
            lifecycle.begin_pending(
                conn,
                run_id=run_id,
                at=now.isoformat(),
                job="영업",
                confirmed_cost_krw=10.0,
                elapsed_sec=1.0,
                model="lookup-model",
                expires_at=(now + dt.timedelta(hours=1)).isoformat(),
            )
            lifecycle.mark_running(
                conn,
                run_id,
                event_at=(now + dt.timedelta(seconds=1)).isoformat(),
            )
            for phase in phases:
                spend_store.begin_inflight(
                    conn,
                    run_id=run_id,
                    phase=phase,
                    day=now.date(),
                    bucket=_LINK_A,
                    started_at=(now + dt.timedelta(seconds=2)).isoformat(),
                )

    paid_runtime._recover_observation_lifecycle()

    records = {record.run_id: record for record in read_records(records_path()).records}
    assert {run_id: records[run_id].end_step for run_id in phases_by_run} == {
        "restart-broken": obs.END_STEP_GENERATE,
        "restart-healthy-ocr": obs.END_STEP_IMAGE_ERROR,
        "restart-healthy-pipeline": obs.END_STEP_GENERATE,
    }


def test_재시작단계_SQLite오류는_한건손상으로_삼키지않는다(monkeypatch):
    now = dt.datetime.now().replace(microsecond=0)
    phases_by_run = {
        "restart-before-db-error": SPEND_PHASE_OCR,
        "restart-db-error": SPEND_PHASE_PIPELINE,
    }
    with storage_db.connect() as conn:
        lifecycle.ensure_schema(conn)
        spend_store.ensure_schema(conn)
        for run_id, phase in phases_by_run.items():
            lifecycle.begin_pending(
                conn,
                run_id=run_id,
                at=now.isoformat(),
                job="영업",
                confirmed_cost_krw=10.0,
                elapsed_sec=1.0,
                model="lookup-model",
                expires_at=(now + dt.timedelta(hours=1)).isoformat(),
            )
            lifecycle.mark_running(
                conn,
                run_id,
                event_at=(now + dt.timedelta(seconds=1)).isoformat(),
            )
            spend_store.begin_inflight(
                conn,
                run_id=run_id,
                phase=phase,
                day=now.date(),
                bucket=_LINK_A,
                started_at=(now + dt.timedelta(seconds=2)).isoformat(),
            )

    original_get_phase = spend_store.get_inflight_phase

    def get_phase_with_db_error(conn, run_id):
        if run_id == "restart-db-error":
            raise sqlite3.OperationalError("시험용 SQLite 조회 오류")
        return original_get_phase(conn, run_id)

    monkeypatch.setattr(spend_store, "get_inflight_phase", get_phase_with_db_error)

    paid_runtime._recover_observation_lifecycle()

    assert read_records(records_path()).records == []
    with storage_db.connect() as conn:
        assert {
            entry.run_id for entry in lifecycle.list_restart_candidates(conn)
        } == set(phases_by_run)


def test_레거시_이미지_AI는_호출되지_않고_회사분석만_진행한다(monkeypatch):
    pipeline = FakePaidPipeline()
    calls = 0

    def broken_extractor(_images):
        nonlocal calls
        calls += 1
        raise RuntimeError("시험용 이미지 API 오류")

    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(job_runtime, "default_extract", broken_extractor)
    client = TestClient(main.app)
    _발급(client)
    token, ref = _확인값(_confirm(client).text)

    response = client.post(
        "/run",
        data=_run_form(token, ref),
        files={"posting_images": ("posting.png", _VALID_PNG)},
        follow_redirects=False,
    )

    _기다린다(client, response)

    assert response.status_code == 303
    records = read_records(records_path()).records
    assert len(records) == 1
    assert records[0].end_step != obs.END_STEP_IMAGE_ERROR
    assert calls == 0
    assert pipeline.run_calls == 1


def test_레거시_직무입력은_무시하고_관측DB에는_회사분석으로_남긴다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)
    # 회사 링크는 발급된 직무로 제한되므로, 임의 직무의 개인정보 마스킹 검사는
    # 별도 초대 회원 권한으로 수행한다.
    _초대하고_로그인(client, "privacy-test@example.com")
    private_job = "user@example.com"

    confirm = client.post("/confirm", data={**_FORM, "job": private_job})
    token, _ref = _확인값(confirm.text)
    attempt = job_runtime._PAID_ATTEMPTS[token]
    client.post(
        "/reject",
        data={**_FORM, "job": private_job, "retry": "0", "paid_attempt_token": token},
    )

    with storage_db.connect() as conn:
        lifecycle.ensure_schema(conn)
        entry = lifecycle.get_entry(conn, attempt.run_id)
        stored = "\n".join(conn.iterdump())
    assert entry is not None and entry.final_record is not None
    assert entry.final_record.job == "회사분석"
    assert private_job not in stored


def test_run도_가드_토큰_OCR재검사에_같은통장을_한번만_쓴다(monkeypatch):
    pipeline = FakePaidPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client = TestClient(main.app)
    _발급(client)
    token, ref = _확인값(_confirm(client).text)
    calls = 0

    def changing_track(_request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return share_tracks.Track.LINK, _LINK_A, PER_LINK_DAILY_BUDGET_KRW
        return share_tracks.Track.PUBLIC, "public", 0.0

    monkeypatch.setattr(request_helpers, "_track_of", changing_track)
    monkeypatch.setattr(
        job_runtime,
        "default_extract",
        lambda _images: image_logic.ExtractResult(text="읽은 공고"),
    )

    response = client.post(
        "/run",
        data=_run_form(token, ref),
        files={"posting_images": ("posting.png", _VALID_PNG)},
        follow_redirects=False,
    )
    _기다린다(client, response)

    assert calls == 1
    assert pipeline.run_calls == 1


def test_자정걸친_요청은_어제비용을_오늘로_보충하지_않는다():
    today = dt.date.today()
    yesterday = today - dt.timedelta(days=1)
    with storage_db.connect() as conn:
        spend_store.ensure_schema(conn)
        spend_store.append_spend(
            conn, run_id="midnight", phase=SPEND_PHASE_IDENTIFY,
            day=yesterday, bucket=_LINK_A, cost_krw=10.0, created_at="어제",
        )
        spend_store.append_spend(
            conn, run_id="midnight", phase=SPEND_PHASE_PIPELINE,
            day=today, bucket=_LINK_A, cost_krw=30.0, created_at="오늘",
        )
    record_run(
        UserInput(company="가나다", job="영업", region="서울"),
        RunResult(outcome=Outcome.GATE_STOPPED, cost_krw=40.0, model="model"),
        1.0,
        run_id="midnight",
    )

    paid_runtime._seed_ledger()

    bucket = spend_store.bucket_id(_LINK_A)
    assert paid_runtime._LEDGER.spent_krw == 30.0
    assert share_logic.spent_for(paid_runtime._LINK_SPEND, bucket, today) == 30.0
    assert paid_runtime._BUDGET_STORE_HEALTHY is True


def test_자정걸친_요청의_누락차액은_날짜를_지어내지_않고_fail_closed한다():
    today = dt.date.today()
    yesterday = today - dt.timedelta(days=1)
    with storage_db.connect() as conn:
        spend_store.ensure_schema(conn)
        spend_store.append_spend(
            conn, run_id="midnight-missing", phase=SPEND_PHASE_IDENTIFY,
            day=yesterday, bucket=_LINK_A, cost_krw=10.0, created_at="어제",
        )
    record_run(
        UserInput(company="가나다", job="영업", region="서울"),
        RunResult(outcome=Outcome.GATE_STOPPED, cost_krw=40.0, model="model"),
        1.0,
        run_id="midnight-missing",
    )

    paid_runtime._seed_ledger()

    assert paid_runtime._LEDGER.spent_krw == 0.0
    assert paid_runtime._BUDGET_STORE_HEALTHY is False


def test_오늘_옛관측만_있으면_통장을_복원할수없어_fail_closed한다():
    record_run(
        UserInput(company="가나다", job="영업", region="서울"),
        RunResult(outcome=Outcome.NOT_FOUND, cost_krw=10.0, model="old-model"),
        1.0,
        run_id="legacy",
    )

    paid_runtime._seed_ledger()

    assert paid_runtime._LEDGER.spent_krw == 10.0
    assert paid_runtime._BUDGET_STORE_HEALTHY is False


def test_깨진_관측줄이_하나라도_있으면_비용누락가능성으로_fail_closed한다():
    path = records_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{깨진 줄\n", encoding="utf-8")

    paid_runtime._seed_ledger()

    assert paid_runtime._BUDGET_STORE_HEALTHY is False


def test_링크_사용자_관리자_통장을_재시작뒤_각각_복원하고_두번_seed해도_안겹친다():
    today = dt.date.today()
    buckets = {
        _LINK_A: 10.0,
        "user:member@example.com": 20.0,
        "user:admin@example.com": 30.0,
    }
    with storage_db.connect() as conn:
        spend_store.ensure_schema(conn)
        for index, (bucket, amount) in enumerate(buckets.items()):
            spend_store.append_spend(
                conn,
                run_id=f"bucket-{index}",
                phase=SPEND_PHASE_IDENTIFY,
                day=today,
                bucket=bucket,
                cost_krw=amount,
                created_at="오늘",
            )

    paid_runtime._seed_ledger()
    paid_runtime._seed_ledger()

    assert paid_runtime._LEDGER.spent_krw == 60.0
    for bucket, amount in buckets.items():
        assert share_logic.spent_for(
            paid_runtime._LINK_SPEND, spend_store.bucket_id(bucket), today
        ) == amount


@pytest.mark.parametrize(
    ("bucket", "cap"),
    [
        (_LINK_A, 3000.0),
        ("user:member@example.com", 1000.0),
        ("user:admin@example.com", 5000.0),
    ],
)
def test_운영기준_minus_1에서는_100원_예상단계와_provider를_열지_않는다(
    bucket: str, cap: float
):
    today = clock.today_kst()
    with storage_db.connect() as conn:
        spend_store.ensure_schema(conn)
        spend_store.append_spend(
            conn,
            run_id="near-threshold-seed",
            phase=SPEND_PHASE_IDENTIFY,
            day=today,
            bucket=bucket,
            cost_krw=cap - 1,
            created_at="2026-08-18T09:00:00+09:00",
        )

    ticket = paid_runtime._begin_paid_phase(
        run_id="near-threshold-candidate",
        phase=SPEND_PHASE_PIPELINE,
        share_key=bucket,
        cap_krw=cap,
        requested_cost_krw=100.0,
    )
    provider_calls = 0

    def provider_spy():
        nonlocal provider_calls
        provider_calls += 1

    if ticket is not None:
        paid_runtime._call_paid_provider(ticket, provider_spy)

    with storage_db.connect() as conn:
        snapshot = spend_store.load_day(conn, today)
    assert ticket is None
    assert provider_calls == 0
    assert snapshot.total_krw == cap - 1


def test_실제_paid_phase는_0이_아닌_단계예상액을_DB에_먼저_예약한다():
    expected = PAID_PHASE_PROVIDER_BUDGET_KRW[SPEND_PHASE_PIPELINE]

    ticket = paid_runtime._begin_paid_phase(
        run_id="nonzero-reservation",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_LINK_A,
        cap_krw=3000.0,
    )

    assert ticket is not None
    assert ticket.reserved_krw == expected
    with storage_db.connect() as conn:
        rows = spend_store.list_inflight_day(conn, ticket.day)
    assert [(row.run_id, row.reserved_krw) for row in rows] == [
        ("nonzero-reservation", expected)
    ]
    paid_runtime._cancel_paid_phase(ticket)


def test_kst_자정전에_잡은_예상예약과_실제액은_시작일에_귀속한다(monkeypatch):
    first_day = dt.date(2026, 8, 18)
    next_day = dt.date(2026, 8, 19)
    now = dt.datetime(2026, 8, 18, 23, 59, 59, tzinfo=clock.KST)
    monkeypatch.setattr(clock, "now_kst", lambda: now)

    ticket = paid_runtime._begin_paid_phase(
        run_id="kst-midnight",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_LINK_A,
        cap_krw=3000.0,
        requested_cost_krw=100.0,
    )
    assert ticket is not None and ticket.day == first_day

    now = dt.datetime(2026, 8, 19, 0, 0, tzinfo=clock.KST)
    paid_runtime._settle_paid_phase(
        ticket, amount_krw=20.0, billing_uncertain=False
    )

    with storage_db.connect() as conn:
        first = spend_store.load_day(conn, first_day)
        second = spend_store.load_day(conn, next_day)
    assert first.total_krw == 20.0
    assert second.total_krw == 0.0


@pytest.mark.parametrize(
    "record_at",
    ["2026-08-17T15:00:00+00:00", "2026-08-18T00:00:00"],
)
def test_seed는_aware_utc와_legacy_naive를_kst_사업일로_복원한다(
    monkeypatch, record_at: str
):
    business_day = dt.date(2026, 8, 18)
    monkeypatch.setattr(
        clock,
        "now_kst",
        lambda: dt.datetime(2026, 8, 18, 0, 0, tzinfo=clock.KST),
    )
    monkeypatch.setattr(recording, "now_iso", lambda: record_at)
    with storage_db.connect() as conn:
        spend_store.ensure_schema(conn)
        spend_store.append_spend(
            conn,
            run_id="kst-seed",
            phase=SPEND_PHASE_IDENTIFY,
            day=business_day,
            bucket=_LINK_A,
            cost_krw=10.0,
            created_at="2026-08-18T00:00:00+09:00",
        )
    record_run(
        UserInput(company="가나다", job="영업", region="서울"),
        RunResult(outcome=Outcome.GATE_STOPPED, cost_krw=40.0, model="model"),
        1.0,
        run_id="kst-seed",
    )

    paid_runtime._seed_ledger()

    bucket = spend_store.bucket_id(_LINK_A)
    assert paid_runtime._LEDGER.day == business_day
    assert paid_runtime._LEDGER.spent_krw == 40.0
    assert share_logic.spent_for(
        paid_runtime._LINK_SPEND, bucket, business_day
    ) == 40.0
    assert paid_runtime._BUDGET_STORE_HEALTHY is True
