"""이미 확정(COMPLETED)된 LINK 보고서가 재렌더 해시 불일치로 열람 불가가
되는 회귀를 막는 시험.

★ 무엇이 문제였나 — PDF를 그리는 코드(report_standard/visualization.py 등)가
  바뀌면, 보고서 «내용»은 그대로인데 다시 렌더한 PDF의 바이트만 달라져
  candidate.pdf_sha256이 저장 당시 값과 어긋난다. 이 저장소는 PDF 원문
  바이트를 DB에 남기지 않고 매번 다시 그려서 해시로만 결속하므로,
  «재렌더 == 저장 당시 렌더»를 요구하면 렌더러가 한 번이라도 바뀌는 순간
  이미 팔린·이미 완료된 LINK 보고서가 통째로 다시 열리지 않게 된다
  (500/503/409 중 하나로 막힘 — 정확한 코드는 이력 상태에 따라 갈린다).

★ 무엇을 지키는가 — 「내용」 자체가 실제로 깨지거나 바뀐 경우는 여전히
  막아야 한다. `automatic_release_pdf`가 매번 다시 돌리는 4검사(정본·PDF
  무결성·채널 동등성·해시 자기일관성)는 «이력과의 비교»가 아니라 «지금
  렌더 자체가 온전한가»를 보므로, 이 검사가 실패하면 여전히 차단된다.
"""

from __future__ import annotations

import io

import pytest
from pypdf import PdfReader, PdfWriter

from src.features.export_pdf.automatic_release import automatic_release_pdf
from src.features.export_pdf.release import (
    PDFReleaseBlockedError,
    PdfReleaseCandidate,
    prepare_pdf_bytes,
    prepare_pdf_release,
)
from src.features.export_pdf import release_store as pdf_release_store
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.web.routers import reports as reports_router

#: app/src/web/tests/conftest.py의 autouse fixture가 모든 웹 시험에서
#: reports_router._release_state를 가짜로 덮어쓴다 (다른 웹 시험이 승인
#: 경계를 매번 다시 안 태우게 하려는 의도). 이 시험은 그 경계 자체를
#: 검증해야 하므로, fixture가 손대기 «전» 모듈 로드 시점에 진짜 함수를
#: 붙잡아 둔다 (test_link_release_history.py와 같은 패턴).
_REAL_RELEASE_STATE = reports_router._release_state

#: 시험에서 재사용하는 고정 출고 시각 (04장 시각 형식 — KST 오프셋 포함).
_RELEASED_AT = "2026-08-21T10:02:00+09:00"


def _rerendered_with_different_bytes(
    candidate: PdfReleaseCandidate,
) -> PdfReleaseCandidate:
    """«시각화 코드가 바뀌어 같은 내용도 다른 PDF 바이트가 된» 상황을 재현한다.

    실제 원인(report_standard/visualization.py 변경)은 이 에이전트 소유가
    아니라서 코드를 바꿔 재현할 수 없다. 대신 pypdf로 같은 PDF를 다시
    직렬화한다 — 페이지 수·글자 내용은 완전히 같지만(=재검증 통과),
    xref·객체 순서가 달라져 pdf_sha256은 실제로 달라진다. «내용은 같은데
    렌더 바이트만 다른» 조건을 인위적 조작 없이 진짜로 만족한다.
    """

    reader = PdfReader(io.BytesIO(candidate.pdf_bytes))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    buffer = io.BytesIO()
    writer.write(buffer)
    return prepare_pdf_bytes(
        buffer.getvalue(),
        render_scale=candidate.render_scale,
        expected_fact_ids=candidate.expected_fact_ids,
    )


def _completed_link_run(
    *, raw_key: str, report_id: str, pdf_sha256: str, release_sha256: str
) -> None:
    """이미 자동출고까지 끝나 COMPLETED로 확정된 LINK run을 실제 저장소에 재현한다."""

    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=raw_key,
            company="카카오",
            job="데이터 분석",
            now_iso="2026-08-21T09:00:00+09:00",
        )
        assert share_store.start_run(
            conn,
            key=raw_key,
            run_id=report_id,
            started_at="2026-08-21T10:00:00+09:00",
            input_company="카카오",
            confirmed_company="카카오",
            company_id="corp-kakao",
        )
        assert share_store.finish_run(
            conn,
            run_id=report_id,
            status=share_store.RUN_STATUS_AWAITING_RELEASE,
            finished_at="2026-08-21T10:01:00+09:00",
            report_id=report_id,
            internal_ai_cost_krw=0.0,
        )
        assert share_store.mark_released(
            conn,
            report_id=report_id,
            pdf_sha256=pdf_sha256,
            release_sha256=release_sha256,
            released_at=_RELEASED_AT,
            customer_charge_krw=0.0,
        )


def test_재렌더로_PDF해시가달라져도_이미확정된LINK는_계속열린다(monkeypatch):
    report = build_demo_report()
    report_id = "hash-drift-report"
    original_candidate = prepare_pdf_release(report)
    original_released = automatic_release_pdf(
        report, original_candidate, released_at=_RELEASED_AT
    )
    with storage_db.connect() as conn:
        pdf_release_store.save_automatic_release(
            conn, report_id=report_id, released_pdf=original_released
        )
    _completed_link_run(
        raw_key="hash-drift-link",
        report_id=report_id,
        pdf_sha256=original_released.record.pdf_sha256,
        release_sha256=original_released.record.record_sha256,
    )

    drifted_candidate = _rerendered_with_different_bytes(original_candidate)
    assert drifted_candidate.pdf_sha256 != original_candidate.pdf_sha256, (
        "사전 조건: 재직렬화해도 해시가 같으면 이 시험이 재현하려는 "
        "«재렌더 드리프트» 상황 자체가 성립하지 않는다"
    )
    monkeypatch.setattr(
        reports_router, "_candidate_for_report", lambda *_a, **_kw: drifted_candidate
    )

    candidate, released = _REAL_RELEASE_STATE(report_id=report_id, report=report)

    assert candidate is drifted_candidate
    assert released is not None
    assert released.content == drifted_candidate.pdf_bytes
    with storage_db.connect() as conn:
        run = share_store.load_run(conn, report_id)
    assert run is not None
    assert run.status == share_store.RUN_STATUS_COMPLETED
    # ★ 원본 승인 이력(정본 해시·청구액)은 재렌더로 덮이지 않는다 — 재대조
    #   실패를 이유로 감사 이력을 재기록하거나 재청구하면 안 된다.
    assert run.pdf_sha256 == original_released.record.pdf_sha256
    assert run.release_sha256 == original_released.record.record_sha256


def test_이미확정된LINK도_재렌더_내용검사가_실패하면_여전히_막는다(monkeypatch):
    """★ «저장된 해시와 다르면 무조건 통과»로 풀면 안 된다 — 진짜 위조/손상은
    그대로 걸려야 한다. 이 시험은 그 하한선을 못 박는다.
    """

    report = build_demo_report()
    report_id = "hash-drift-corrupt-report"
    original_candidate = prepare_pdf_release(report)
    original_released = automatic_release_pdf(
        report, original_candidate, released_at=_RELEASED_AT
    )
    with storage_db.connect() as conn:
        pdf_release_store.save_automatic_release(
            conn, report_id=report_id, released_pdf=original_released
        )
    _completed_link_run(
        raw_key="hash-drift-corrupt-link",
        report_id=report_id,
        pdf_sha256=original_released.record.pdf_sha256,
        release_sha256=original_released.record.record_sha256,
    )

    drifted_candidate = _rerendered_with_different_bytes(original_candidate)
    monkeypatch.setattr(
        reports_router, "_candidate_for_report", lambda *_a, **_kw: drifted_candidate
    )
    monkeypatch.setattr(
        reports_router,
        "automatic_release_pdf",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            PDFReleaseBlockedError("시험용: 지금 렌더 내용 검사 실패")
        ),
    )

    with pytest.raises(PDFReleaseBlockedError):
        _REAL_RELEASE_STATE(report_id=report_id, report=report)

    with storage_db.connect() as conn:
        run = share_store.load_run(conn, report_id)
    assert run is not None
    # ★ 자동검사 실패가 완료 상태 자체를 STOPPED로 덮어써서는 안 된다
    #   (share_store.mark_release_stopped의 "status <> COMPLETED" 가드).
    assert run.status == share_store.RUN_STATUS_COMPLETED
