"""근거 부족 종료가 정상 보고서나 막연한 재시도로 보이지 않는지 검사한다."""

from __future__ import annotations

import uuid

import pytest
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
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_COMPARISON_BLOCKED,
    FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED,
    FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT,
    FINAL_GATE_REASON_PUBLISH_BLOCKED,
    FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
)
from src.web import job_runtime, main, recording
from src.web.tests.report_route_support import bind_public_report_access


def _stopped_job(
    *,
    sources: bool = True,
    reason: str = FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
) -> tuple[str, job_runtime.Job]:
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
            final_gate_reason=reason,
        ),
    )
    return job_id, job


def test_근거부족은_정상보고서가_아니라_의미와_다음행동을_보여준다() -> None:
    job_id, job = _stopped_job()
    job_runtime._JOBS[job_id] = job
    try:
        with TestClient(main.app, base_url="https://testserver") as client:
            bind_public_report_access(client, job_id)
            response = client.get(f"/result/{job_id}")
    finally:
        job_runtime._JOBS.pop(job_id, None)

    assert response.status_code == 200
    assert "완성 보고서에 필요한 공식 근거가 부족합니다" in response.text
    assert "회사에 강점이나 경쟁우위가 없다는 판정이 아닙니다" in response.text
    assert "입력과 공식 자료 상태가 그대로라면" in response.text
    assert "회사의 공식 공시·홈페이지 자료가 추가된 뒤" in response.text
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
        final_gate_reason=job.result.final_gate_reason,
    )
    job_runtime._JOBS[job_id] = job
    try:
        with TestClient(main.app, base_url="https://testserver") as client:
            bind_public_report_access(client, job_id)
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
        with TestClient(main.app, base_url="https://testserver") as client:
            bind_public_report_access(client, job_id)
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
        with TestClient(main.app, base_url="https://testserver") as client:
            bind_public_report_access(client, job_id)
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
        final_gate_reason=job.result.final_gate_reason,
    )
    session = auth_logic.create_session("admin@example.com", True)
    job_runtime._JOBS[job_id] = job
    try:
        with TestClient(main.app, base_url="https://testserver") as client:
            bind_public_report_access(client, job_id)
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


_STATE_TITLES = (
    "회사 자료가 아니라 시스템 내부 연결 문제입니다",
    "공식 자료의 뜻을 자동으로 끝까지 확인하지 못했습니다",
    "공식 자료를 가져오는 중 잠시 문제가 생겼습니다",
    "완성 보고서에 필요한 공식 근거가 부족합니다",
    "같은 조건으로 비교할 경쟁사 공식 근거가 부족합니다",
    "만든 초안이 품질 기준을 통과하지 못했습니다",
    "안전 검사를 통과하지 못해 보고서를 내보내지 않았습니다",
)


@pytest.mark.parametrize(
    ("reason", "title", "meaning", "action", "button"),
    (
        (
            FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
            _STATE_TITLES[0],
            "같은 회사라도 내부 연결을 고치면 결과가 달라질 수 있습니다",
            "시스템의 자료 연결 문제가 수정된 뒤",
            "입력 화면으로 돌아가기",
        ),
        (
            FINAL_GATE_REASON_EVIDENCE_CLASSIFICATION_UNDETERMINED,
            _STATE_TITLES[1],
            "회사 자료 부족이나 시스템 내부 연결 오류로 단정한 결과가 아닙니다",
            "자료 분류 범위가 보완된 뒤",
            "입력 화면으로 돌아가기",
        ),
        (
            FINAL_GATE_REASON_OFFICIAL_EVIDENCE_TRANSIENT,
            _STATE_TITLES[2],
            "일시적인 접속·응답 문제 때문에 안전하게 멈춘 것입니다",
            "잠시 뒤 자료 제공처가 정상화되면",
            "잠시 후 다시 시작",
        ),
        (
            FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
            _STATE_TITLES[3],
            "현재 공개된 공식 자료만으로는 확인할 수 없다는 뜻입니다",
            "정식 법인명과 시·군·구 주소",
            "회사·주소 확인하고 다시 시작",
        ),
        (
            FINAL_GATE_REASON_COMPARISON_BLOCKED,
            _STATE_TITLES[4],
            "회사의 경쟁력이 없다는 판정이 아닙니다",
            "동일 조건의 양사 공식 자료가 확보되거나",
            "입력 화면으로 돌아가기",
        ),
        (
            FINAL_GATE_REASON_PUBLISH_BLOCKED_QUALITY_FLOOR,
            _STATE_TITLES[5],
            "확인되지 않은 내용을 억지로 채우는 대신",
            "생성 품질 보완이 적용된 뒤",
            "입력 화면으로 돌아가기",
        ),
        (
            FINAL_GATE_REASON_PUBLISH_BLOCKED,
            _STATE_TITLES[6],
            "회사 자료 문제인지 시스템 문제인지 정확히 나눌 수 없습니다",
            "원인이 확인된 뒤",
            "입력 화면으로 돌아가기",
        ),
    ),
)
def test_실제_result_경로가_최종사유별_서로_다른_안내만_보여준다(
    reason: str,
    title: str,
    meaning: str,
    action: str,
    button: str,
) -> None:
    job_id, job = _stopped_job(reason=reason)
    job_runtime._JOBS[job_id] = job
    try:
        with TestClient(main.app, base_url="https://testserver") as client:
            bind_public_report_access(client, job_id)
            response = client.get(f"/result/{job_id}")
    finally:
        job_runtime._JOBS.pop(job_id, None)

    assert response.status_code == 200
    assert title in response.text
    assert meaning in response.text
    assert action in response.text
    assert button in response.text
    for other_title in set(_STATE_TITLES) - {title}:
        assert other_title not in response.text


def test_서버_재시작으로_메모리가_비어도_저장된_내부사유를_복원한다() -> None:
    job_id, job = _stopped_job(
        reason=FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
    )
    assert job.result is not None
    assert recording.record_run(
        job.user_input,
        job.result,
        1.0,
        run_id=job_id,
    )
    assert job_id not in job_runtime._JOBS

    with TestClient(main.app, base_url="https://testserver") as client:
        bind_public_report_access(client, job_id)
        response = client.get(f"/result/{job_id}")

    assert response.status_code == 200
    assert _STATE_TITLES[0] in response.text
    assert "완성 보고서에 필요한 공식 근거가 부족합니다" not in response.text
    assert "어디까지 찾아봤나" not in response.text


def test_비용미확정_실패의_같은_진단을_게이트중단으로_잘못_복원하지_않는다() -> None:
    job_id = uuid.uuid4().hex
    assert recording.record_run(
        UserInput(company="우리엔", job="", region="서울 중구"),
        RunResult(
            outcome=Outcome.FAILED,
            billing_uncertain=True,
            final_gate_reason=FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
        ),
        1.0,
        run_id=job_id,
    )

    with TestClient(main.app, base_url="https://testserver") as client:
        bind_public_report_access(client, job_id)
        response = client.get(f"/result/{job_id}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/?report_status=unavailable"
