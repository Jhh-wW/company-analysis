"""FULL 공개 안전 차단 줄이 생산 real.py·저장본까지 끝-끝으로 남는다(«3월→03월» 변형).

공개 worker 대역(``test_public_boundary_full_evidence_e2e``)을 그대로 빌려 쓴다. 공식 웹
쪽 문장 하나에 «2019년 3월 15일»을 넣고, 가짜 작가만 그 문장을 «2019년 03월 15일»로
옮겨 쓴다(값은 같아 앞 단계 숫자 대조는 통과한다). 원문과 다른 숫자 표기를 담은 확인
산문이라 FULL 공개 안전이 수치 문구 두 개로 막고 보고서 없이 멈춘다. 그때
«8_공개안전_차단유형» 한 줄이 composer 목록 → real.py 정화기 → 실행 진단 저장까지
끊기지 않고 남는다 — 평가 도구가 이 저장본을 그대로 diagnostics.json 으로 내보낸다.

같은 파일군의 다른 끝-끝 시험처럼 local_integration 으로 둔다. CI 에서 도는 전파 시험은
``test_safety_block_diagnostic_propagation.py`` 에 있다.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from src.features.observability import run_steps_store
from src.features.storage import db as storage_db
from src.web.tests import test_public_boundary_full_evidence_e2e as e2e
# pytest 픽스처는 이 모듈 이름공간에 있어야 쓰인다.
from src.web.tests.test_public_boundary_full_evidence_e2e import (  # noqa: F401
    _isolated_company_catalog_state,
)

_JOB_ID = "public-boundary-full-safety-block-e2e"
_SHARE_KEY = "safety-block-admin@example.com"
_STEP = "8_공개안전_차단유형"
#: 숫자를 넣을 공식 웹 문장 — 장과 그 장의 문장 번호.
_SECTION = "operations_partners"
_SENTENCE_INDEX = 2
_SOURCE_PHRASE = "2019년 3월 15일 기준"
_SOURCE_DATE = "2019년 3월 15일"
_WRITTEN_DATE = "2019년 03월 15일"
#: 관리자 실행 한 번의 유료 상한(원) — 가짜 provider 라 넉넉하기만 하면 된다.
_PAID_CAP_KRW = 100_000.0


def _install_numbered_page(monkeypatch: pytest.MonkeyPatch) -> str:
    """공식 웹 쪽 문장 하나에 날짜를 넣는다 — 작가 대역과 수집 대역이 같은 글자를 본다.

    ``_WEB_HTML`` 은 그 모듈을 읽을 때 한 번 만들어지므로 문장 함수와 쪽 HTML을 함께
    바꾼다. 돌려주는 값은 날짜를 넣은 원문 문장이다.
    """

    original = e2e._section_sentences

    def numbered(section_id: str) -> tuple[str, ...]:
        sentences = list(original(section_id))
        if section_id == _SECTION:
            ordinal = e2e._ORDINALS[_SENTENCE_INDEX]
            sentences[_SENTENCE_INDEX] = sentences[_SENTENCE_INDEX].replace(
                f"{ordinal} ", f"{_SOURCE_PHRASE} {ordinal} ", 1,
            )
        return tuple(sentences)

    monkeypatch.setattr(e2e, "_section_sentences", numbered)
    for section_id, path in e2e._PAGE_PATH_BY_SECTION.items():
        monkeypatch.setitem(e2e._WEB_HTML, path, e2e._page_html(section_id))
    return numbered(_SECTION)[_SENTENCE_INDEX]


def _rewrite_writer_sentence(messages: Any, source: str, written: str) -> list[str]:
    """가짜 작가 응답에서 문장 하나의 글만 바꾼다. 바꾼 글을 목록에 모아 돌려준다.

    근거 조각은 가짜 작가가 원문 문장으로 먼저 찾는다 — 인용은 그대로 두고 공개
    후보에 실릴 글만 바뀐다.
    """

    original_create = messages.create
    rewritten: list[str] = []

    def create(**kwargs: Any) -> Any:
        response = original_create(**kwargs)
        payload = json.loads(response.content[0].text)
        rows = payload.get("문장들") if isinstance(payload, dict) else None
        if isinstance(rows, list):
            for row in rows:
                if row.get("글") == source:
                    row["글"] = written
                    rewritten.append(written)
            response.content[0].text = json.dumps(payload, ensure_ascii=False)
        return response

    messages.create = create
    return rewritten


def _run_full_admin_job(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, source: str, written: str,
) -> tuple[Any, list[str]]:
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

    _engine, services = e2e._install_production_engine_with_fake_external_services(
        monkeypatch, tmp_path,
    )
    rewritten = _rewrite_writer_sentence(services.client.messages, source, written)
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
    slot_bucket_id = e2e.paid_runtime._reserve_run_slot(  # noqa: SLF001
        e2e.share_tracks.Track.ADMIN,
        _SHARE_KEY,
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
        share_key=_SHARE_KEY,
        is_paid=True,
        paid_cap_krw=_PAID_CAP_KRW,
        slot_bucket_id=slot_bucket_id,
        report_audience=e2e.ReportAudience.ADMIN,
    )
    asyncio.run(e2e.job_runtime._run_job(job))
    return job, rewritten


def _stored_steps(job_id: str, step_name: str) -> list[dict]:
    """평가 도구가 diagnostics.json 으로 내보내는 저장본에서 한 단계만 고른다."""

    with storage_db.connect() as conn:
        stored = run_steps_store.load(conn, job_id)
    assert stored is not None
    return [step for step in stored.steps if step.get("step") == step_name]


@pytest.mark.local_integration
def test_실제FULL에서_작가가_원문_숫자표기를_바꾸면_막히고_공개안전_차단유형이_저장된다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    _isolated_company_catalog_state: None,
) -> None:
    source = _install_numbered_page(monkeypatch)
    written = source.replace(_SOURCE_DATE, _WRITTEN_DATE, 1)
    assert written != source

    job, rewritten = _run_full_admin_job(
        monkeypatch, tmp_path, source=source, written=written,
    )

    # 가짜 작가가 실제로 그 문장을 바꿔 썼다 — 안 바뀌었으면 이 시험은 아무것도 못 본다.
    assert rewritten
    assert job.result is not None
    assert job.result.outcome is e2e.Outcome.GATE_STOPPED, (
        job.result.final_gate_reason,
        job.result.message,
    )
    assert job.result.final_gate_reason == "publish_blocked"
    assert job.result.report is None
    assert job.result.charged is False
    assert _stored_steps(_JOB_ID, _STEP) == [{
        "step": _STEP,
        "회차": "1차",
        "문제수": 2,
        "유형별": {"numeric_labels_missing": 1, "numeric_binding_missing": 1},
        "장별": {"operations_partners": 2},
    }]
