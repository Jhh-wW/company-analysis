"""FULL 공개 안전의 공식 원문 산문 숫자 예외(ADR 0005) — 생산 real.py·저장·배달 끝-끝.

공개 worker 대역(``test_public_boundary_full_evidence_e2e``)을 그대로 빌려 쓰고, 공식 웹
쪽 문장에 날짜·개수를 넣는다. 숫자가 인용 조각 원문에 그대로 있으면 FULL COMPLETE로
저장·배달된다. 같은 파일의 다른 끝-끝 시험처럼 local_integration 으로 둔다. 판정 자체의
CI 시험은 ``shared/report_quality/tests``·``features/composer/tests``에 있다.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.web.tests import test_public_boundary_full_evidence_e2e as e2e
# pytest 픽스처는 이 모듈 이름공간에 있어야 쓰인다.
from src.web.tests.test_public_boundary_full_evidence_e2e import (  # noqa: F401
    _isolated_company_catalog_state,
)

_JOB_ID = "public-boundary-full-numeric-prose-e2e"
_PHRASES: dict[tuple[str, int], str] = {
    ("identity", 2): "2019년 3월 15일 기준",
    ("identity", 3): "12개 항목 기준",
    ("business_model", 2): "2022년 기준",
    ("past_changes", 2): "2017년 기준",
    ("operations_partners", 2): "7곳 기준",
}


def _install_numeric_official_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """공식 웹 쪽 문장과 작가 대역이 같은 숫자 문구를 보게 한다.

    ``_WEB_HTML`` 은 그 모듈을 읽을 때 한 번 만들어지므로 문장 함수와 쪽 HTML을 함께 바꾼다.
    """

    original = e2e._section_sentences

    def numbered(section_id: str) -> tuple[str, ...]:
        sentences = list(original(section_id))
        for (owner, index), phrase in _PHRASES.items():
            if owner == section_id:
                ordinal = e2e._ORDINALS[index]
                sentences[index] = sentences[index].replace(
                    f"{ordinal} ", f"{phrase} {ordinal} ", 1
                )
        return tuple(sentences)

    monkeypatch.setattr(e2e, "_section_sentences", numbered)
    for section_id, path in e2e._PAGE_PATH_BY_SECTION.items():
        monkeypatch.setitem(e2e._WEB_HTML, path, e2e._page_html(section_id))


def _run_full_admin_job(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """생산 real.py·장부·영수증·저장·배달을 그대로 타는 FULL 관리자 실행 한 번."""

    monkeypatch.setenv(e2e.PIPELINE_ENV, e2e.PIPELINE_REAL)
    monkeypatch.setenv(e2e.real.ENGINE_V2_ENV_NAME, e2e.real.ENGINE_V2_ENV_ON)
    monkeypatch.setenv(
        e2e.real.REPORT_RELEASE_MODE_ENV_NAME, e2e.ReleaseMode.FULL.value,
    )
    monkeypatch.setenv("APP_DATA_ROOT", str(tmp_path / "artifacts"))
    for name in e2e.deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)

    e2e._install_production_engine_with_fake_external_services(monkeypatch, tmp_path)
    e2e._install_actual_official_collector_with_fake_http(monkeypatch)
    monkeypatch.setattr(
        e2e.job_runtime,
        "_require_report_delivery",
        e2e._ACTUAL_REQUIRE_REPORT_DELIVERY,
    )
    monkeypatch.setattr(
        e2e.job_runtime,
        "_finalize_report_delivery",
        e2e._ACTUAL_FINALIZE_REPORT_DELIVERY,
    )
    monkeypatch.setattr(
        e2e.reports_router, "_release_state", e2e._ACTUAL_RELEASE_STATE,
    )
    pipeline = e2e.runtime.make_pipeline()
    assert isinstance(pipeline, e2e.real.RealPipeline)
    monkeypatch.setattr(e2e.runtime, "_PIPELINE", pipeline)

    e2e.paid_runtime.prepare_budget_state_machine_cutover()
    share_key = "numeric-prose-admin@example.com"
    slot_bucket_id = e2e.paid_runtime._reserve_run_slot(  # noqa: SLF001
        e2e.share_tracks.Track.ADMIN,
        share_key,
    )
    assert slot_bucket_id
    e2e._begin_running_lifecycle(_JOB_ID)
    job = e2e.job_runtime.Job(
        job_id=_JOB_ID,
        user_input=e2e.UserInput(company="가나다전자", job="", region=""),
        card=e2e.CompanyCard(
            legal_name="가나다전자",
            typed_name="가나다전자",
            address="서울특별시 강남구 테헤란로",
            ceo="홍길동",
            founded="20000101",
            ref=e2e._COMPANY_ID,
        ),
        share_key=share_key,
        is_paid=True,
        paid_cap_krw=100_000.0,
        slot_bucket_id=slot_bucket_id,
        report_audience=e2e.ReportAudience.ADMIN,
    )
    asyncio.run(e2e.job_runtime._run_job(job))
    return job


@pytest.mark.local_integration
def test_실제FULL은_원문에_그대로_있는_날짜와_개수를_실은_확인산문으로_FULL출고한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _isolated_company_catalog_state: None,
) -> None:
    """2026-09-24 7차 FULL 실측의 끝까지 시험.

    날짜·개수 든 공식 산문이 숫자가 원문에 그대로면 FULL을 막지 않는다.
    """

    _install_numeric_official_pages(monkeypatch)

    job = _run_full_admin_job(monkeypatch, tmp_path)

    assert job.result is not None
    assert job.result.outcome is e2e.Outcome.REPORT, (
        job.result.final_gate_reason,
        job.result.message,
    )
    report = job.result.report
    assert report is not None
    assert report.grade is e2e.Grade.COMPLETE
    assert report.release_mode == e2e.ReleaseMode.FULL.value
    assert job.report_persisted is True
    assert job.delivery_persisted is True
    # 숫자 문장 다섯이 «실제로» 공개 사실로 실렸고, 그 사실이 인용 조각 원문을 싣는다.
    carried = [
        fact.claim for fact in report.fact_records
        if "official_exact_text" in (fact.state_evidence or "")
    ]
    assert len(carried) == len(_PHRASES)
    for phrase in _PHRASES.values():
        assert sum(phrase in claim for claim in carried) == 1, phrase
