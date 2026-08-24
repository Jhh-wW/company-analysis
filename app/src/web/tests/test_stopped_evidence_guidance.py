"""근거 부족 종료가 정상 보고서나 막연한 재시도로 보이지 않는지 검사한다."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.port import (
    CompanyCard,
    Outcome,
    RunResult,
    SourceStatus,
    UserInput,
)
from src.web import job_runtime, main


def _stopped_job(*, sources: bool = True) -> tuple[str, job_runtime.Job]:
    job_id = uuid.uuid4().hex
    job = job_runtime.Job(
        job_id=job_id,
        user_input=UserInput(company="우리엔", job="", region="서울 중구"),
        card=CompanyCard(
            legal_name="주식회사 우리엔",
            typed_name="우리엔",
            address="서울특별시 중구",
            ceo="",
            founded="",
        ),
        finished=True,
        result=RunResult(
            outcome=Outcome.GATE_STOPPED,
            message=(
                "양사 공식 원문을 같은 지표·기간·연결범위로 비교할 수 없어 "
                "보고서를 내보내지 않았습니다."
            ),
            sources=(
                [SourceStatus("전자공시", "none", "동일 조건 비교 자료 없음")]
                if sources
                else []
            ),
        ),
    )
    return job_id, job


def test_근거부족은_정상보고서가_아니라_의미와_다음행동을_보여준다() -> None:
    job_id, job = _stopped_job()
    job_runtime._JOBS[job_id] = job
    try:
        with TestClient(main.app) as client:
            response = client.get(f"/result/{job_id}")
    finally:
        job_runtime._JOBS.pop(job_id, None)

    assert response.status_code == 200
    assert "출고 전 자동 검증에서 보고서를 내보내지 않았습니다" in response.text
    assert "회사에 강점이나 경쟁우위가 없다는 판정이 아닙니다" in response.text
    assert "입력과 공식 자료 상태가 그대로라면" in response.text
    assert "못 가져온 자료가 정상화되거나" in response.text
    assert "회사·주소 확인하고 다시 시작" in response.text
    assert 'href="#stopped-sources-title"' in response.text
    assert "어디까지 찾아봤나" in response.text


def test_정책상_쓰지_않은_뉴스와_일부만_탐색한_IR을_자료없음으로_오해시키지_않는다() -> None:
    job_id, job = _stopped_job()
    assert job.result is not None
    job.result = RunResult(
        outcome=job.result.outcome,
        message=job.result.message,
        sources=[
            SourceStatus(
                "뉴스", "none", "공식 근거 보고서에서는 뉴스를 사용하지 않습니다"
            ),
            SourceStatus("회사 공식 IR", "failed", "탐색 상한 잘림"),
        ],
    )
    job_runtime._JOBS[job_id] = job
    try:
        with TestClient(main.app) as client:
            response = client.get(f"/result/{job_id}")
    finally:
        job_runtime._JOBS.pop(job_id, None)

    assert response.status_code == 200
    assert "정책상 미사용" in response.text
    assert "공식 근거 보고서 정책에 따라 뉴스는 조사 대상에서 제외했습니다" in response.text
    assert "IR 일부만 탐색함" in response.text
    assert "보고서 중단의 직접 원인은 아님" in response.text
    assert "❌ 없음" not in response.text


def test_확인한_자료가_없으면_존재하지_않는_자료표_링크를_만들지_않는다() -> None:
    job_id, job = _stopped_job(sources=False)
    job_runtime._JOBS[job_id] = job
    try:
        with TestClient(main.app) as client:
            response = client.get(f"/result/{job_id}")
    finally:
        job_runtime._JOBS.pop(job_id, None)

    assert response.status_code == 200
    assert "다음에 할 수 있는 일" in response.text
    assert 'href="#stopped-sources-title"' not in response.text
    assert "어디까지 찾아봤나" not in response.text


def test_회사를_못찾은_중단을_기술오류라고_부르지_않는다() -> None:
    job_id, job = _stopped_job(sources=False)
    job.result = RunResult(
        outcome=Outcome.NOT_FOUND,
        message="회사명과 주소로 법인을 확정하지 못했습니다.",
    )
    job_runtime._JOBS[job_id] = job
    try:
        with TestClient(main.app) as client:
            response = client.get(f"/result/{job_id}")
    finally:
        job_runtime._JOBS.pop(job_id, None)

    assert response.status_code == 200
    assert "회사를 정확히 확인하지 못했습니다" in response.text
    assert "오류가 났습니다" not in response.text


def test_관리자에게는_중단까지_사용된_AI비용을_숨기지_않는다() -> None:
    job_id, job = _stopped_job()
    assert job.result is not None
    job.result = RunResult(
        outcome=job.result.outcome,
        message=job.result.message,
        sources=job.result.sources,
        cost_krw=209.67,
    )
    session = auth_logic.create_session("admin@example.com", True)
    job_runtime._JOBS[job_id] = job
    try:
        with TestClient(main.app) as client:
            client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
            response = client.get(f"/result/{job_id}")
    finally:
        job_runtime._JOBS.pop(job_id, None)
        auth_logic.delete_session(session.token)

    assert response.status_code == 200
    assert "성공 보고서 횟수는 차감되지 않았습니다" in response.text
    assert "AI 비용" in response.text
    assert "약 210원" in response.text
    assert "오늘 비용 원장에 기록됐습니다" in response.text
