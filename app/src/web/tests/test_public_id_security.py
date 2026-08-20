"""공개 job/result bearer의 128비트 발급과 충돌 비덮어쓰기를 고정한다."""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.pipeline.port import (
    CompanyCard,
    CompanyLookupResult,
    Grade,
    Outcome,
    Report,
    RunResult,
    UserInput,
)
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import job_runtime, main, public_ids, runtime


_FORM = {
    "company": "우리엔",
    "job": "영업",
    "region": "서울",
    "posting_text": "x",
    "legal_name": "우리엔",
    "ref": "재수집-p003",
    "address": "-",
}
_SHARE_KEY = "a1b2c3d4e5f60718a1b2c3d4e5f60718"


class _PaidPipeline:
    def find_company_metered(self, user_input: UserInput) -> CompanyLookupResult:
        return CompanyLookupResult(
            card=CompanyCard(
                legal_name=user_input.company,
                typed_name=user_input.company,
                address="서울",
                ceo="대표",
                founded="20200101",
                ref="corp-001",
            ),
            cost_krw=0.0,
            model="security-test",
        )

    def run(self, *_args, **_kwargs) -> RunResult:
        return RunResult(outcome=Outcome.GATE_STOPPED)


@pytest.fixture(autouse=True)
def _clean_public_id_reservations():
    with public_ids._RESERVATION_LOCK:
        public_ids._RESERVED_IDS.clear()
    yield
    with public_ids._RESERVATION_LOCK:
        public_ids._RESERVED_IDS.clear()


def _report(company: str) -> Report:
    return Report(
        company=company,
        job="영업",
        corp_type="상장사",
        grade=Grade.INCOMPLETE,
        sections=[],
        generated_at="2026-08-18",
    )


def _job(job_id: str) -> job_runtime.Job:
    return job_runtime.Job(
        job_id=job_id,
        user_input=UserInput(company="기존회사", job="영업", region="서울"),
        card=CompanyCard(
            legal_name="기존회사",
            typed_name="기존회사",
            address="서울",
            ceo="대표",
            founded="20200101",
        ),
    )


def _seed_report(report_id: str) -> str:
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "corp-old", "영업", _report("기존보고서"))
        return conn.execute(
            "SELECT payload_json FROM reports WHERE report_id = ?", (report_id,)
        ).fetchone()[0]


def _stored_payload(report_id: str) -> str:
    with storage_db.connect() as conn:
        return conn.execute(
            "SELECT payload_json FROM reports WHERE report_id = ?", (report_id,)
        ).fetchone()[0]


def _confirmed_demo_run(client: TestClient):
    form = {"company": _FORM["company"], "region": _FORM["region"]}
    confirmed = client.post("/confirm", data=form)
    token = re.search(
        r'name="paid_attempt_token" value="([^"]+)"', confirmed.text
    )
    assert token is not None
    return client.post(
        "/run",
        data={**form, "paid_attempt_token": token.group(1)},
        follow_redirects=False,
    )


def test_Demo_run은_memory충돌_DB충돌_뒤_16바이트_후보만_사용한다(
    monkeypatch: pytest.MonkeyPatch,
):
    memory_id, stored_id, fresh_id = "1" * 32, "2" * 32, "f" * 32
    existing_job = _job(memory_id)
    job_runtime._JOBS[memory_id] = existing_job
    old_payload = _seed_report(stored_id)
    candidates = iter([memory_id, stored_id, fresh_id])
    byte_calls: list[int] = []

    def fake_token_hex(nbytes: int) -> str:
        byte_calls.append(nbytes)
        return next(candidates)

    monkeypatch.setattr(public_ids.secrets, "token_hex", fake_token_hex)
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    with TestClient(main.app) as client:
        response = _confirmed_demo_run(client)

    assert response.status_code == 303
    assert response.headers["location"] == f"/progress/{fresh_id}"
    assert byte_calls == [16, 16, 16]
    assert re.fullmatch(r"[0-9a-f]{32}", fresh_id)
    assert fresh_id[12] != "4" and fresh_id[16] not in "89ab"
    assert job_runtime._JOBS[memory_id] is existing_job
    assert _stored_payload(stored_id) == old_payload


def test_Demo_run의_bounded충돌은_503이고_기존_job_report를_보존한다(
    monkeypatch: pytest.MonkeyPatch,
):
    memory_id, stored_id = "3" * 32, "4" * 32
    existing_job = _job(memory_id)
    job_runtime._JOBS[memory_id] = existing_job
    old_payload = _seed_report(stored_id)
    calls = 0

    def always_collide(_nbytes: int) -> str:
        nonlocal calls
        calls += 1
        return memory_id if calls % 2 else stored_id

    monkeypatch.setattr(public_ids.secrets, "token_hex", always_collide)
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    with TestClient(main.app) as client:
        response = _confirmed_demo_run(client)

    assert response.status_code == 503
    assert calls == public_ids.PUBLIC_ID_ALLOCATION_ATTEMPTS
    assert job_runtime._JOBS == {memory_id: existing_job}
    assert job_runtime._JOBS[memory_id] is existing_job
    assert _stored_payload(stored_id) == old_payload


def test_paid_confirm도_같은_allocator로_memory_DB충돌을_건너뛴다(
    monkeypatch: pytest.MonkeyPatch,
):
    memory_id, stored_id, fresh_id = "5" * 32, "6" * 32, "e" * 32
    existing_job = _job(memory_id)
    job_runtime._JOBS[memory_id] = existing_job
    old_payload = _seed_report(stored_id)
    candidates = iter([memory_id, stored_id, fresh_id])
    monkeypatch.setattr(
        public_ids.secrets, "token_hex", lambda nbytes: next(candidates)
    )
    monkeypatch.setattr(runtime, "_PIPELINE", _PaidPipeline())
    with storage_db.connect() as conn:
        share_store.save(
            conn,
            key=_SHARE_KEY,
            company=_FORM["company"],
            job=_FORM["job"],
            now_iso="2026-08-18T10:00:00",
        )

    with TestClient(main.app, base_url="https://testserver") as client:
        client.get(f"/k/{_SHARE_KEY}")
        csrf = auth_logic.csrf_token_for_session(client.cookies[KEY_COOKIE_NAME])
        response = client.post("/confirm", data={**_FORM, "csrf_token": csrf})

    assert response.status_code == 200
    attempts = list(job_runtime._PAID_ATTEMPTS.values())
    assert len(attempts) == 1
    assert attempts[0].run_id == fresh_id
    assert job_runtime._JOBS[memory_id] is existing_job
    assert _stored_payload(stored_id) == old_payload
    public_ids.release(fresh_id)


def test_작업등록_경합도_기존_job을_덮지않는다():
    identifier = "7" * 32
    existing_job = _job(identifier)
    replacement = _job(identifier)
    jobs = {identifier: existing_job}

    assert not public_ids.register(jobs, identifier, replacement)
    assert jobs[identifier] is existing_job
