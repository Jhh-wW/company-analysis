"""데모 확인 카드와 본조사를 서버의 일회용 회사 기록으로 결속한다."""

from __future__ import annotations

import dataclasses
import re
import time

import pytest
from fastapi.testclient import TestClient

from src.features.pipeline.canonical_demo import DEMO_COMPANY, DEMO_REF
from src.features.pipeline.demo import DemoPipeline
from src.web import job_runtime, main, runtime


def _token(html: str) -> str:
    match = re.search(r'name="paid_attempt_token" value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


def _confirm(client: TestClient, company: str = "우리엔") -> tuple[str, dict[str, str]]:
    form = {"company": company, "region": "서울"}
    response = client.post("/confirm", data=form)
    assert response.status_code == 200
    return _token(response.text), form


@pytest.fixture(autouse=True)
def _demo_pipeline(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())


def test_confirm은_회사식별값대신_일회용토큰만_브라우저에_둔다():
    with TestClient(main.app) as client:
        response = client.post("/confirm", data={"company": "우리엔", "region": "서울"})

    token = _token(response.text)
    attempt = job_runtime._PAID_ATTEMPTS[token]
    assert not attempt.is_paid
    assert attempt.card.ref
    assert 'name="legal_name"' not in response.text
    assert 'name="ref"' not in response.text
    assert 'name="address"' not in response.text


def test_confirm없이_run을_직접_부르면_조사를_시작하지_않는다():
    with TestClient(main.app) as client:
        response = client.post(
            "/run",
            data={"company": "우리엔", "region": "서울"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert job_runtime._JOBS == {}


def test_원입력을_바꾸면_토큰을_소비하고_재사용도_거절한다():
    with TestClient(main.app) as client:
        token, form = _confirm(client)
        changed = client.post(
            "/run",
            data={**form, "company": "(주)진영", "paid_attempt_token": token},
            follow_redirects=False,
        )
        replay = client.post(
            "/run",
            data={**form, "paid_attempt_token": token},
            follow_redirects=False,
        )

    assert changed.status_code == replay.status_code == 303
    assert changed.headers["location"] == replay.headers["location"] == "/"
    assert token not in job_runtime._PAID_ATTEMPTS
    assert job_runtime._JOBS == {}


def test_숨은_회사값을_위조해도_확인한_서버카드를_사용한다():
    with TestClient(main.app) as client:
        token, form = _confirm(client)
        confirmed_card = job_runtime._PAID_ATTEMPTS[token].card
        response = client.post(
            "/run",
            data={
                **form,
                "paid_attempt_token": token,
                "legal_name": DEMO_COMPANY,
                "ref": DEMO_REF,
                "address": "공격자가 고른 주소",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        job_id = response.headers["location"].rsplit("/", 1)[-1]
        assert job_runtime._JOBS[job_id].card == confirmed_card
        assert job_runtime._JOBS[job_id].card.ref != DEMO_REF


def test_만료와_거절은_데모_확인기록을_정리한다():
    with TestClient(main.app) as client:
        expired_token, expired_form = _confirm(client)
        attempt = job_runtime._PAID_ATTEMPTS[expired_token]
        job_runtime._PAID_ATTEMPTS[expired_token] = dataclasses.replace(
            attempt,
            created_at=time.monotonic() - job_runtime.JOB_KEEP_SEC - 1,
        )
        expired = client.post(
            "/run",
            data={**expired_form, "paid_attempt_token": expired_token},
            follow_redirects=False,
        )

        rejected_token, rejected_form = _confirm(client)
        rejected = client.post(
            "/reject",
            data={**rejected_form, "paid_attempt_token": rejected_token},
        )

    assert expired.status_code == 303
    assert expired_token not in job_runtime._PAID_ATTEMPTS
    assert rejected.status_code == 200
    assert rejected_token not in job_runtime._PAID_ATTEMPTS


def test_정상_전체흐름은_한번만_실행되고_결과까지_열린다(monkeypatch):
    monkeypatch.setenv("DEMO_STEP_DELAY_SEC", "0")
    with TestClient(main.app) as client:
        assert client.get("/").status_code == 200
        token, form = _confirm(client, DEMO_COMPANY)
        run = client.post(
            "/run",
            data={**form, "paid_attempt_token": token},
            follow_redirects=False,
        )
        assert run.status_code == 303
        job_id = run.headers["location"].rsplit("/", 1)[-1]
        assert client.get(f"/progress/{job_id}").status_code == 200
        for _ in range(200):
            if client.get(f"/api/progress/{job_id}").json()["finished"]:
                break
            time.sleep(0.01)
        else:
            pytest.fail("데모 조사가 끝나지 않았습니다")

        result = client.get(f"/result/{job_id}")
        replay = client.post(
            "/run",
            data={**form, "paid_attempt_token": token},
            follow_redirects=False,
        )

    assert result.status_code == 200
    assert replay.status_code == 303
    assert replay.headers["location"] == "/"
    assert token not in job_runtime._PAID_ATTEMPTS
