"""근거 부족 종료가 정상 보고서나 막연한 재시도로 보이지 않는지 검사한다."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

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
    assert "공식 근거가 부족해 보고서를 내보내지 않았습니다" in response.text
    assert "회사에 강점이나 경쟁우위가 없다는 판정이 아닙니다" in response.text
    assert "입력과 공식 자료 상태가 그대로라면" in response.text
    assert "못 가져온 자료가 정상화되거나" in response.text
    assert "회사·주소 확인하고 다시 시작" in response.text
    assert 'href="#stopped-sources-title"' in response.text
    assert "어디까지 찾아봤나" in response.text


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
