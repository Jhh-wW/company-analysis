"""저장본을 다시 읽는 경계가 출처의 수집 도장을 실제로 보는지 지킨다.

★ 무엇이 걱정인가 — 저장된 본문을 지키는 검사는 대부분 «열쇠 없는» 해시다.
  저장소에 직접 쓸 수 있는 쪽은 본문을 고친 뒤 그 해시를 전부 다시 계산해
  통과시킬 수 있다. 수집 도장(수집 경계가 서버 열쇠로 찍는 값)만 저장소
  밖에 열쇠가 있으므로, 읽어서 화면에 내보내기 직전에 그 도장을 한 번 더 본다.

이 파일이 못 박는 것:

  · 저장된 출처를 고치면 결과 화면이 열리지 않는다(닫는 화면).
  · 도장 열쇠를 바꿔 서버를 다시 띄우면 기존 도장 저장본도 닫힌다.
  · 공개 봉인을 가진 본문은 출고 기록이 사라져도 과거 화면으로 격하되지 않는다.
  · 도장 칸이 처음부터 빈 옛 저장본은 지금까지처럼 열리되, 그 화면이
    「모두 검증을 거쳤다」고 단언하지 않는다.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.composer.tests.test_section_public_manifest import _run_full
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import Report
from src.features.provenance import sources as provenance_sources
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import job_runtime
from src.web import main as web_main
from src.web.tests.test_public_projection_parity import _unsealed_v2_report


#: 닫는 화면의 제목 — `routers/reports.py`의 문구와 같은 값을 리터럴로 적는다.
_CLOSED_TITLE = "저장된 보고서를 확인할 수 없습니다"

#: 출처표가 「모두 검증을 거쳤다」고 단언하는 문장의 특징 조각.
_VERIFIED_CLAIM = "인용 원문 대조"


def _stored(report: Report) -> str:
    """delivery·출고 의무 표식 없이 본문만 저장한다 — 과거 저장본과 같은 상태."""

    report_id = uuid.uuid4().hex
    with storage_db.connect() as conn:
        report_store.save(conn, report_id, "seal-guard-corp", report.job, report)
    return report_id


def _result(monkeypatch: pytest.MonkeyPatch, report_id: str):
    job_runtime._JOBS.pop(report_id, None)  # noqa: SLF001
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    session = auth_logic.create_session("admin@example.com", True)
    with TestClient(web_main.app) as client:
        return client.get(
            f"/result/{report_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
            follow_redirects=False,
        )


def _with_forged_first_source(report: Report, **changes: str) -> Report:
    """도장은 그대로 두고 출처 한 줄의 값만 바꾼 저장본."""

    citations = [
        replace(source, **changes) if index == 0 else source
        for index, source in enumerate(report.citations)
    ]
    return replace(report, citations=citations)


def test_저장된_출처를_고치면_결과_화면이_열리지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """같은 재료를 «고치지 않은 채»로도 한 번 열어, 닫힘이 도장 때문임을 보인다."""

    report = build_demo_report()
    intact = _result(monkeypatch, _stored(report))
    assert intact.status_code == 200

    forged = _result(
        monkeypatch,
        _stored(_with_forged_first_source(report, host="news.example")),
    )

    assert forged.status_code == 503
    assert _CLOSED_TITLE in forged.text
    assert _VERIFIED_CLAIM not in forged.text
    assert forged.headers["cache-control"] == "private, no-store"


def test_도장_열쇠를_바꿔_다시_띄우면_기존_저장본도_닫힌다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """열쇠를 바꾸면 기존 보고서가 닫힌다 — 운영이 미리 알아야 하는 동작이다."""

    report_id = _stored(build_demo_report())
    assert _result(monkeypatch, report_id).status_code == 200

    monkeypatch.setattr(
        provenance_sources,
        "_PROVENANCE_SEAL_KEY",
        secrets.token_bytes(32),
    )
    rotated = _result(monkeypatch, report_id)

    assert rotated.status_code == 503
    assert _CLOSED_TITLE in rotated.text


def test_공개_봉인을_가진_본문은_출고_기록이_없어도_과거_화면으로_내려가지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 격하 통로 — 출고 기록 두 줄을 지우면 오늘 보고서도 과거 화면으로
    내려가고, 그 화면은 오늘의 검사를 한 번도 부르지 않는다. 그러면 검사를
    지우는 것만으로 검사를 피할 수 있다. 그 길을 닫는다.
    """

    output, _writer, _reviewer, _diagram = _run_full()
    report = output.report
    assert report.generation_evidence is not None

    response = _result(monkeypatch, _stored(report))

    assert response.status_code == 503
    assert _CLOSED_TITLE in response.text
    assert _VERIFIED_CLAIM not in response.text


def test_도장_없는_옛_저장본은_열리되_검증을_마쳤다고_말하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """반대 경우 시험 — 새 점검이 옛 저장본을 막지 않는다.

    같은 화면이 이미 「지금의 검사로 다시 고치거나 막지 않았다」고 고지하므로,
    출처표가 「모두 검증을 거쳤다」고 단언하면 한 화면이 두 말을 하게 된다.
    """

    response = _result(monkeypatch, _stored(_unsealed_v2_report()))

    assert response.status_code == 200
    assert "과거 방식으로 저장된 본문을 그대로" in response.text
    assert _VERIFIED_CLAIM not in response.text
    assert "당시 검증 상태는 확인할 수 없습니다" in response.text
