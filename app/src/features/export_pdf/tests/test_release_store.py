from __future__ import annotations

import io
import json
import sqlite3
import threading
from dataclasses import replace
from functools import cache
from pathlib import Path

import pytest
from reportlab.pdfgen.canvas import Canvas

from src.features.export_pdf.release import (
    ApprovalDecision,
    PdfReleaseCandidate,
    PdfReleaseApproval,
    PdfReleaseRecord,
    ReleasedPdf,
    prepare_pdf_bytes,
    release_record_sha256,
    release_pdf,
)
from src.features.export_pdf import release_store
from src.features.export_pdf.release_store import (
    DECISION_TABLE_NAME,
    TABLE_NAME,
    PdfRoleDecision,
    PdfReleaseStoreError,
    finalize_release,
    ensure_participant_ledger,
    load_approval,
    load_complete_approval,
    load_participant_ledger,
    load_release_record,
    load_role_decisions,
    record_release,
    save_approval,
    save_role_decision,
)

_AT = "2026-08-19T21:30:00+09:00"
_PDF_HASH = "a" * 64
_PAGE_HASHES = ("b" * 64, "c" * 64)
_FACT_IDS = ("fact-1", "fact-2")
_FACT_REVIEWER = "user:" + "1" * 20
_EDITORIAL_REVIEWER = "user:" + "2" * 20
_VISUAL_REVIEWER = "user:" + "3" * 20
_SAME_REVIEWER = "user:" + "4" * 20
_OTHER_EDITOR = "user:" + "5" * 20
_VISUAL_REVIEWER_A = "user:" + "6" * 20
_VISUAL_REVIEWER_B = "user:" + "7" * 20
_AUTHOR = "user:" + "8" * 20
_PRODUCER = "user:" + "9" * 20


def _participants(
    *,
    fact: str = _FACT_REVIEWER,
    editorial: str = _EDITORIAL_REVIEWER,
    visual: str = _VISUAL_REVIEWER,
) -> dict[str, str]:
    return {
        "author": _AUTHOR,
        "producer": _PRODUCER,
        "fact": fact,
        "editorial": editorial,
        "visual": visual,
    }


def _assign(
    conn: sqlite3.Connection,
    *,
    report_id: str = "report-1",
    pdf_sha256: str = _PDF_HASH,
    participants: dict[str, str] | None = None,
) -> None:
    ensure_participant_ledger(
        conn,
        report_id=report_id,
        pdf_sha256=pdf_sha256,
        participants=participants or _participants(),
        assigned_at=_AT,
    )


def _approval() -> PdfReleaseApproval:
    return PdfReleaseApproval(
        pdf_sha256=_PDF_HASH,
        page_png_sha256s=_PAGE_HASHES,
        reviewed_pages=(1, 2),
        reviewed_fact_ids=_FACT_IDS,
        fact_failed_count=0,
        fact=ApprovalDecision(True, _FACT_REVIEWER, _AT),
        editorial=ApprovalDecision(True, _EDITORIAL_REVIEWER, _AT),
        visual=ApprovalDecision(True, _VISUAL_REVIEWER, _AT),
        visual_review_kind="human",
    )


def _record() -> PdfReleaseRecord:
    unsigned = PdfReleaseRecord(
        pdf_sha256=_PDF_HASH,
        page_count=2,
        page_png_sha256s=_PAGE_HASHES,
        expected_fact_ids=_FACT_IDS,
        reviewed_fact_ids=_FACT_IDS,
        fact_failed_count=0,
        fact_reviewer=_FACT_REVIEWER,
        fact_approved_at=_AT,
        editorial_reviewer=_EDITORIAL_REVIEWER,
        editorial_approved_at=_AT,
        visual_reviewer=_VISUAL_REVIEWER,
        visual_approved_at=_AT,
        visual_review_kind="human",
        released_at=_AT,
        record_sha256="",
    )
    return replace(unsigned, record_sha256=release_record_sha256(unsigned))


def _connection() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    return connection


def _role(role: str, reviewer: str) -> PdfRoleDecision:
    return PdfRoleDecision(
        role=role,
        pdf_sha256=_PDF_HASH,
        page_png_sha256s=_PAGE_HASHES,
        reviewed_pages=(1, 2),
        expected_fact_ids=_FACT_IDS,
        reviewed_fact_ids=_FACT_IDS if role == "fact" else (),
        fact_failed_count=0,
        decision=ApprovalDecision(True, reviewer, _AT),
        visual_review_kind="human" if role == "visual" else "",
    )


def _save_roles(conn: sqlite3.Connection, *, report_id: str = "report-1") -> None:
    _assign(conn, report_id=report_id)
    for role, reviewer in (
        ("fact", _FACT_REVIEWER),
        ("editorial", _EDITORIAL_REVIEWER),
        ("visual", _VISUAL_REVIEWER),
    ):
        save_role_decision(
            conn,
            report_id=report_id,
            role_decision=_role(role, reviewer),
        )


def _save_complete_approval(
    conn: sqlite3.Connection,
    *,
    report_id: str = "report-1",
) -> None:
    _save_roles(conn, report_id=report_id)
    save_approval(conn, report_id=report_id, approval=_approval(), created_at=_AT)


def _resign(record: PdfReleaseRecord, **changes: object) -> PdfReleaseRecord:
    unsigned = replace(record, **changes, record_sha256="")
    return replace(unsigned, record_sha256=release_record_sha256(unsigned))


@cache
def _release_candidate() -> PdfReleaseCandidate:
    output = io.BytesIO()
    canvas = Canvas(output, invariant=1)
    canvas.drawString(72, 720, "release integrity witness")
    canvas.showPage()
    canvas.save()
    return prepare_pdf_bytes(
        output.getvalue(),
        render_scale=0.5,
        expected_fact_ids=_FACT_IDS,
    )


def _release_approval(
    candidate: PdfReleaseCandidate | None = None,
    *,
    visual_reviewer: str = _VISUAL_REVIEWER,
) -> PdfReleaseApproval:
    candidate = candidate or _release_candidate()
    return PdfReleaseApproval(
        pdf_sha256=candidate.pdf_sha256,
        page_png_sha256s=tuple(page.png_sha256 for page in candidate.pages),
        reviewed_pages=tuple(page.number for page in candidate.pages),
        reviewed_fact_ids=candidate.expected_fact_ids,
        fact_failed_count=0,
        fact=ApprovalDecision(True, _FACT_REVIEWER, _AT),
        editorial=ApprovalDecision(True, _EDITORIAL_REVIEWER, _AT),
        visual=ApprovalDecision(True, visual_reviewer, _AT),
        visual_review_kind="human",
    )


def _release_role(
    role: str,
    reviewer: str,
    candidate: PdfReleaseCandidate | None = None,
) -> PdfRoleDecision:
    candidate = candidate or _release_candidate()
    return PdfRoleDecision(
        role=role,
        pdf_sha256=candidate.pdf_sha256,
        page_png_sha256s=tuple(page.png_sha256 for page in candidate.pages),
        reviewed_pages=tuple(page.number for page in candidate.pages),
        expected_fact_ids=candidate.expected_fact_ids,
        reviewed_fact_ids=candidate.expected_fact_ids if role == "fact" else (),
        fact_failed_count=0,
        decision=ApprovalDecision(True, reviewer, _AT),
        visual_review_kind="human" if role == "visual" else "",
    )


def _save_release_roles(
    conn: sqlite3.Connection,
    *,
    report_id: str = "release-report",
    visual_reviewer: str = _VISUAL_REVIEWER,
) -> None:
    candidate = _release_candidate()
    _assign(
        conn,
        report_id=report_id,
        pdf_sha256=candidate.pdf_sha256,
        participants=_participants(visual=visual_reviewer),
    )
    for role, reviewer in (
        ("fact", _FACT_REVIEWER),
        ("editorial", _EDITORIAL_REVIEWER),
        ("visual", visual_reviewer),
    ):
        save_role_decision(
            conn,
            report_id=report_id,
            role_decision=_release_role(role, reviewer, candidate),
        )


def _finalize_valid_release(
    conn: sqlite3.Connection,
    *,
    report_id: str = "release-report",
):
    candidate = _release_candidate()
    approval = _release_approval(candidate)
    _save_release_roles(conn, report_id=report_id)
    released = finalize_release(
        conn,
        report_id=report_id,
        candidate=candidate,
        approval=approval,
        created_at=_AT,
        released_at=_AT,
    )
    return candidate, approval, released


def test_서로_다른_세_검수자의_역할승인을_누적한_뒤에만_최종승인이_된다():
    conn = _connection()
    _assign(conn)
    save_role_decision(
        conn, report_id="report-1", role_decision=_role("fact", _FACT_REVIEWER)
    )
    assert load_complete_approval(conn, report_id="report-1", pdf_sha256=_PDF_HASH) is None

    save_role_decision(
        conn,
        report_id="report-1",
        role_decision=_role("editorial", _EDITORIAL_REVIEWER),
    )
    assert load_complete_approval(conn, report_id="report-1", pdf_sha256=_PDF_HASH) is None

    save_role_decision(
        conn,
        report_id="report-1",
        role_decision=_role("visual", _VISUAL_REVIEWER),
    )
    assert load_complete_approval(conn, report_id="report-1", pdf_sha256=_PDF_HASH) == _approval()
    assert len(load_role_decisions(conn, report_id="report-1", pdf_sha256=_PDF_HASH)) == 3


def test_참여자원장이_없으면_형식이_맞는_승인도_fail_closed한다():
    conn = _connection()
    with pytest.raises(PdfReleaseStoreError, match="미리 배정된"):
        save_role_decision(
            conn,
            report_id="report-1",
            role_decision=_role("fact", _FACT_REVIEWER),
        )


@pytest.mark.parametrize("excluded_role", ("author", "producer"))
def test_작성자나_생산자를_검수자로_배정할_수_없다(excluded_role: str):
    conn = _connection()
    invalid = _participants(fact=_participants()[excluded_role])
    with pytest.raises(PdfReleaseStoreError, match="올바른 PDF 참여자"):
        _assign(conn, participants=invalid)


def test_PDF별_참여자원장은_정확히_같은_재시도만_허용하고_덮어쓰지_않는다():
    conn = _connection()
    _assign(conn)
    _assign(conn)
    assert load_participant_ledger(
        conn, report_id="report-1", pdf_sha256=_PDF_HASH
    ) == _participants()

    changed = _participants(fact=_SAME_REVIEWER)
    with pytest.raises(PdfReleaseStoreError, match="덮어쓸 수 없습니다"):
        _assign(conn, participants=changed)


def test_참여자원장이_승인뒤_변조되면_최종승인을_읽지_않는다():
    conn = _connection()
    _save_complete_approval(conn)
    conn.execute(
        f"UPDATE {release_store.PARTICIPANT_TABLE_NAME} SET person_id=? WHERE role='fact'",
        (_SAME_REVIEWER,),
    )
    with pytest.raises(PdfReleaseStoreError, match="참여자 원장"):
        load_approval(conn, report_id="report-1", pdf_sha256=_PDF_HASH)


def test_동시_참여자배정은_완전한_한_원장만_남긴다(tmp_path: Path):
    database = tmp_path / "participant-race.sqlite3"
    setup = sqlite3.connect(database)
    setup.row_factory = sqlite3.Row
    load_participant_ledger(setup, report_id="report-1", pdf_sha256=_PDF_HASH)
    setup.commit()
    setup.close()

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def assign(participants: dict[str, str]) -> None:
        conn = sqlite3.connect(database, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            barrier.wait(timeout=5)
            with conn:
                ensure_participant_ledger(
                    conn,
                    report_id="report-1",
                    pdf_sha256=_PDF_HASH,
                    participants=participants,
                    assigned_at=_AT,
                )
        except (PdfReleaseStoreError, sqlite3.OperationalError):
            result = "blocked"
        else:
            result = "saved"
        finally:
            conn.close()
        with lock:
            outcomes.append(result)

    choices = (_participants(), _participants(fact=_SAME_REVIEWER))
    threads = [threading.Thread(target=assign, args=(choice,)) for choice in choices]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    verify = sqlite3.connect(database)
    verify.row_factory = sqlite3.Row
    ledger = load_participant_ledger(
        verify, report_id="report-1", pdf_sha256=_PDF_HASH
    )
    row_count = verify.execute(
        f"SELECT COUNT(*) FROM {release_store.PARTICIPANT_TABLE_NAME}"
    ).fetchone()[0]
    verify.close()
    assert sorted(outcomes) == ["blocked", "saved"]
    assert ledger in choices
    assert row_count == 5


def test_한_검수자가_두_역할을_승인할_수_없다():
    conn = _connection()
    _assign(conn, participants=_participants(fact=_SAME_REVIEWER))
    save_role_decision(
        conn, report_id="report-1", role_decision=_role("fact", _SAME_REVIEWER)
    )

    with pytest.raises(PdfReleaseStoreError, match="배정된"):
        save_role_decision(
            conn,
            report_id="report-1",
            role_decision=_role("editorial", _SAME_REVIEWER),
        )


def test_사실승인은_전체_fact_id와_실패_0건이_아니면_저장하지_않는다():
    conn = _connection()
    _assign(conn)
    incomplete = PdfRoleDecision(
        **{
            **_role("fact", _FACT_REVIEWER).__dict__,
            "reviewed_fact_ids": ("fact-1",),
        }
    )

    with pytest.raises(PdfReleaseStoreError):
        save_role_decision(conn, report_id="report-1", role_decision=incomplete)


def test_승인과_최종_PDF_hash_출고기록을_왕복한다():
    conn = _connection()
    candidate, approval, released = _finalize_valid_release(conn)

    assert (
        load_approval(
            conn,
            report_id="release-report",
            pdf_sha256=candidate.pdf_sha256,
        )
        == approval
    )
    assert (
        load_release_record(
            conn,
            report_id="release-report",
            pdf_sha256=candidate.pdf_sha256,
        )
        == released.record
    )


def test_record_release도_candidate_approval_content를_모두_재검증한다():
    conn = _connection()
    candidate = _release_candidate()
    approval = _release_approval(candidate)
    _save_release_roles(conn)
    save_approval(
        conn,
        report_id="release-report",
        approval=approval,
        created_at=_AT,
    )
    released = release_pdf(candidate, approval, released_at=_AT)

    record_release(
        conn,
        report_id="release-report",
        released_pdf=released,
        candidate=candidate,
        approval=approval,
    )

    assert (
        load_release_record(
            conn,
            report_id="release-report",
            pdf_sha256=candidate.pdf_sha256,
        )
        == released.record
    )


def test_승인행이_없으면_출고기록을_만들지_않는다():
    conn = _connection()
    candidate = _release_candidate()
    approval = _release_approval(candidate)
    released = release_pdf(candidate, approval, released_at=_AT)
    with pytest.raises(PdfReleaseStoreError):
        record_release(
            conn,
            report_id="missing",
            released_pdf=released,
            candidate=candidate,
            approval=approval,
        )


def test_이미_출고한_승인은_다른승인으로_덮지_않는다():
    conn = _connection()
    _candidate, approval, _released = _finalize_valid_release(conn)

    with pytest.raises(PdfReleaseStoreError):
        save_approval(
            conn,
            report_id="release-report",
            approval=approval,
            created_at=_AT,
        )


def test_role_rows_세_개가_없으면_최종승인을_저장하거나_읽지_않는다():
    conn = _connection()

    with pytest.raises(PdfReleaseStoreError, match="역할별 세 승인"):
        save_approval(conn, report_id="report-1", approval=_approval(), created_at=_AT)

    conn.execute(
        f"""
        INSERT INTO {TABLE_NAME} (
            report_id, pdf_sha256, approval_json, approval_created_at
        ) VALUES (?, ?, ?, ?)
        """,
        (
            "report-1",
            _PDF_HASH,
            json.dumps(
                {
                    "pdf_sha256": _approval().pdf_sha256,
                    "page_png_sha256s": _approval().page_png_sha256s,
                    "reviewed_pages": _approval().reviewed_pages,
                    "reviewed_fact_ids": _approval().reviewed_fact_ids,
                    "fact_failed_count": 0,
                    "fact": _approval().fact.__dict__,
                    "editorial": _approval().editorial.__dict__,
                    "visual": _approval().visual.__dict__,
                    "visual_review_kind": "human",
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
            _AT,
        ),
    )
    with pytest.raises(PdfReleaseStoreError, match="역할별 세 승인"):
        load_approval(conn, report_id="report-1", pdf_sha256=_PDF_HASH)


@pytest.mark.parametrize(
    "bad_hash",
    ("A" * 64, "g" * 64, "a" * 63, "a" * 65),
)
def test_모든_저장_hash는_소문자_16진수_64자리여야_한다(bad_hash: str):
    conn = _connection()
    malformed = replace(_role("fact", _FACT_REVIEWER), pdf_sha256=bad_hash)

    with pytest.raises(PdfReleaseStoreError):
        save_role_decision(conn, report_id="report-1", role_decision=malformed)


@pytest.mark.parametrize(
    "reviewer",
    (
        "USER:" + "4" * 20,
        " " + _SAME_REVIEWER,
        _SAME_REVIEWER + " ",
        "ｕｓｅｒ:" + "4" * 20,
        "user：" + "4" * 20,
        "user:" + "4" * 19,
        "user:" + "g" * 20,
    ),
)
def test_검수자_ID_표현_변형을_저장_경계에서_거부한다(reviewer: str):
    conn = _connection()

    with pytest.raises(PdfReleaseStoreError):
        save_role_decision(
            conn,
            report_id="report-1",
            role_decision=_role("fact", reviewer),
        )


def test_최종승인은_role_rows와_exact_equality여야_한다():
    conn = _connection()
    _save_roles(conn)
    mismatched = replace(
        _approval(),
        editorial=ApprovalDecision(True, _OTHER_EDITOR, _AT),
    )

    with pytest.raises(PdfReleaseStoreError, match="정확히 같은"):
        save_approval(conn, report_id="report-1", approval=mismatched, created_at=_AT)


def test_approval_created_at은_가장_늦은_역할승인시각에_결속된다():
    conn = _connection()
    _save_roles(conn)

    with pytest.raises(PdfReleaseStoreError):
        save_approval(
            conn,
            report_id="report-1",
            approval=_approval(),
            created_at="2026-08-19T21:31:00+09:00",
        )

    save_approval(conn, report_id="report-1", approval=_approval(), created_at=_AT)
    conn.execute(
        f"UPDATE {TABLE_NAME} SET approval_created_at=?",
        ("2026-08-19T21:31:00+09:00",),
    )
    with pytest.raises(PdfReleaseStoreError, match="무결성"):
        load_approval(conn, report_id="report-1", pdf_sha256=_PDF_HASH)


def test_저장된_approval_JSON이나_role_row가_변조되면_조회가_차단된다():
    conn = _connection()
    _save_complete_approval(conn)
    conn.execute(
        f"UPDATE {TABLE_NAME} SET approval_json = approval_json || ' '"
    )

    with pytest.raises(PdfReleaseStoreError, match="무결성"):
        load_approval(conn, report_id="report-1", pdf_sha256=_PDF_HASH)

    conn.rollback()
    conn = _connection()
    _save_complete_approval(conn)
    conn.execute(
        f"""
        UPDATE {DECISION_TABLE_NAME}
        SET reviewer=?
        WHERE role='editorial'
        """,
        (_OTHER_EDITOR,),
    )
    with pytest.raises(PdfReleaseStoreError, match="일치하지 않습니다"):
        load_approval(conn, report_id="report-1", pdf_sha256=_PDF_HASH)


@pytest.mark.parametrize("tampered_count", (0.75, -0.5))
def test_DB_fact_failed_count를_정수로_강제변환해_0으로_숨기지_않는다(
    tampered_count,
):
    conn = _connection()
    _save_complete_approval(conn)
    conn.execute(
        f"""
        UPDATE {DECISION_TABLE_NAME}
        SET fact_failed_count=?
        WHERE role='fact'
        """,
        (tampered_count,),
    )

    with pytest.raises(PdfReleaseStoreError, match="DB 형식"):
        load_approval(conn, report_id="report-1", pdf_sha256=_PDF_HASH)


def test_digest를_다시_만든_임의_release_record도_승인과_다르면_거부한다():
    conn = _connection()
    candidate = _release_candidate()
    approval = _release_approval(candidate)
    genuine = release_pdf(candidate, approval, released_at=_AT)
    arbitrary = _resign(genuine.record, editorial_reviewer=_OTHER_EDITOR)

    with pytest.raises(PdfReleaseStoreError):
        record_release(
            conn,
            report_id="report-1",
            released_pdf=ReleasedPdf(content=genuine.content, record=arbitrary),
            candidate=candidate,
            approval=approval,
        )


def test_release_JSON_record_hash_DB_hash를_매번_서로_재검산한다():
    conn = _connection()
    candidate, _approval_record, _released = _finalize_valid_release(conn)
    conn.execute(
        f"UPDATE {TABLE_NAME} SET release_sha256=?",
        ("e" * 64,),
    )

    with pytest.raises(PdfReleaseStoreError, match="DB digest"):
        load_release_record(
            conn,
            report_id="release-report",
            pdf_sha256=candidate.pdf_sha256,
        )

    conn.rollback()
    conn = _connection()
    candidate, _approval_record, _released = _finalize_valid_release(conn)
    raw = conn.execute(f"SELECT release_json FROM {TABLE_NAME}").fetchone()[0]
    payload = json.loads(raw)
    payload["released_at"] = "2026-08-20T00:00:00+09:00"
    conn.execute(
        f"UPDATE {TABLE_NAME} SET release_json=?",
        (json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),),
    )
    with pytest.raises(PdfReleaseStoreError):
        load_release_record(
            conn,
            report_id="release-report",
            pdf_sha256=candidate.pdf_sha256,
        )


def test_release_record의_세_DB_열중_일부만_있으면_거부한다():
    conn = _connection()
    _save_complete_approval(conn)
    conn.execute(
        f"UPDATE {TABLE_NAME} SET release_sha256=?",
        (_record().record_sha256,),
    )

    with pytest.raises(PdfReleaseStoreError, match="부분 기록"):
        load_release_record(conn, report_id="report-1", pdf_sha256=_PDF_HASH)


def test_finalize_release가_중간_실패하면_최종승인과_출고를_함께_rollback한다(
    monkeypatch,
):
    conn = _connection()
    candidate = _release_candidate()
    approval = _release_approval(candidate)
    _save_release_roles(conn)
    conn.commit()

    def fail_after_approval(*_args, **_kwargs):
        raise PdfReleaseStoreError("simulated release write failure")

    monkeypatch.setattr(release_store, "_store_release_record", fail_after_approval)
    with pytest.raises(PdfReleaseStoreError, match="simulated"):
        finalize_release(
            conn,
            report_id="release-report",
            candidate=candidate,
            approval=approval,
            created_at=_AT,
            released_at=_AT,
        )

    assert len(
        load_role_decisions(
            conn,
            report_id="release-report",
            pdf_sha256=candidate.pdf_sha256,
        )
    ) == 3
    assert (
        load_approval(
            conn,
            report_id="release-report",
            pdf_sha256=candidate.pdf_sha256,
        )
        is None
    )


def test_동시_배정자와_미배정자_동일역할_요청은_DB에서_배정자만_남긴다(tmp_path: Path):
    database = tmp_path / "release-race.sqlite3"
    setup = sqlite3.connect(database)
    setup.row_factory = sqlite3.Row
    _assign(setup)
    setup.commit()
    setup.close()

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    outcome_lock = threading.Lock()

    def approve(reviewer: str) -> None:
        conn = sqlite3.connect(database, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            barrier.wait(timeout=5)
            with conn:
                save_role_decision(
                    conn,
                    report_id="report-1",
                    role_decision=_role("fact", reviewer),
                )
        except (PdfReleaseStoreError, sqlite3.OperationalError):
            result = "blocked"
        else:
            result = "saved"
        finally:
            conn.close()
        with outcome_lock:
            outcomes.append(result)

    threads = [
        threading.Thread(target=approve, args=(reviewer,))
        for reviewer in (_FACT_REVIEWER, _SAME_REVIEWER)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    verify = sqlite3.connect(database)
    verify.row_factory = sqlite3.Row
    decisions = load_role_decisions(
        verify,
        report_id="report-1",
        pdf_sha256=_PDF_HASH,
    )
    verify.close()
    assert sorted(outcomes) == ["blocked", "saved"]
    assert len(decisions) == 1
    assert decisions[0].decision.reviewer == _FACT_REVIEWER


def test_동시_최종승인_요청은_한_승인묶음과_출고기록만_원자적으로_남긴다(
    tmp_path: Path,
):
    database = tmp_path / "final-approval-race.sqlite3"
    candidate = _release_candidate()
    setup = sqlite3.connect(database)
    setup.row_factory = sqlite3.Row
    with setup:
        _assign(
            setup,
            report_id="report-1",
            pdf_sha256=candidate.pdf_sha256,
            participants=_participants(visual=_VISUAL_REVIEWER_A),
        )
        save_role_decision(
            setup,
            report_id="report-1",
            role_decision=_release_role("fact", _FACT_REVIEWER, candidate),
        )
        save_role_decision(
            setup,
            report_id="report-1",
            role_decision=_release_role("editorial", _EDITORIAL_REVIEWER, candidate),
        )
    setup.close()

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    outcome_lock = threading.Lock()

    def finalize(reviewer: str) -> None:
        conn = sqlite3.connect(database, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            barrier.wait(timeout=5)
            with conn:
                save_role_decision(
                    conn,
                    report_id="report-1",
                    role_decision=_release_role("visual", reviewer, candidate),
                )
                approval = load_complete_approval(
                    conn,
                    report_id="report-1",
                    pdf_sha256=candidate.pdf_sha256,
                )
                assert approval is not None
                finalize_release(
                    conn,
                    report_id="report-1",
                    candidate=candidate,
                    approval=approval,
                    created_at=_AT,
                    released_at=_AT,
                )
        except (PdfReleaseStoreError, sqlite3.OperationalError):
            result = "blocked"
        else:
            result = "released"
        finally:
            conn.close()
        with outcome_lock:
            outcomes.append(result)

    threads = [
        threading.Thread(target=finalize, args=(reviewer,))
        for reviewer in (_VISUAL_REVIEWER_A, _VISUAL_REVIEWER_B)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=15)

    verify = sqlite3.connect(database)
    verify.row_factory = sqlite3.Row
    approval = load_approval(
        verify,
        report_id="report-1",
        pdf_sha256=candidate.pdf_sha256,
    )
    record = load_release_record(
        verify,
        report_id="report-1",
        pdf_sha256=candidate.pdf_sha256,
    )
    role_count = verify.execute(
        f"SELECT COUNT(*) FROM {DECISION_TABLE_NAME}"
    ).fetchone()[0]
    release_count = verify.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
    verify.close()

    assert sorted(outcomes) == ["blocked", "released"]
    assert approval is not None and record is not None
    assert record.visual_reviewer == approval.visual.reviewer
    assert role_count == 3
    assert release_count == 1
