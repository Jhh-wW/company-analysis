from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.features.cost_tracking import store as cost_store
from src.features.report_delivery import authority as authority_store
from src.shared.automatic_release_record import (
    AutomaticCheckResult,
    AutomaticReleaseRecord,
)
from src.web import report_completion


def _digest(character: str) -> str:
    return character * 64


def _authority(*, charge_digest: str | None = None):
    charge = cost_store.CustomerChargeDecision(
        eligible=True,
        amount_krw=0.0,
        reason="price_not_configured",
    )
    release_digest = _digest("1")
    run_id = "charge-run-1"
    return authority_store.ReleaseAuthority.issue_owner(
        public_id="report-1",
        delivery_id="delivery-1",
        company_id="00123456",
        billing_bucket_id="bucket-1",
        content_snapshot_id="content-1",
        artifact_id="artifact-1",
        report_payload_sha256=_digest("2"),
        producer_evidence_sha256=_digest("3"),
        assessment_sha256=_digest("4"),
        public_content_sha256=_digest("5"),
        public_manifest_sha256=_digest("6"),
        evidence_generation_sha256=_digest("7"),
        build_identity_sha256=_digest("8"),
        automatic_release_sha256=release_digest,
        charge_run_id=run_id,
        charge_decision_sha256=charge_digest
        or cost_store.charge_decision_sha256(
            run_id=run_id,
            automatic_release_sha256=release_digest,
            decision=charge,
        ),
        issued_at=dt.datetime(
            2026,
            8,
            31,
            tzinfo=dt.timezone.utc,
        ),
    )


def _automatic_release(authority):
    return AutomaticReleaseRecord(
        checker_version="checker-v1",
        report_sha256=authority.report_payload_sha256,
        pdf_sha256=_digest("9"),
        page_count=1,
        page_png_sha256s=(_digest("a"),),
        expected_fact_ids=(),
        checks=(AutomaticCheckResult("layout", True, _digest("b")),),
        released_at="2026-08-31T00:00:00+00:00",
        record_sha256=authority.automatic_release_sha256,
    )


class _Intent:
    public_id = "report-1"
    state = "complete"


class _Pointer:
    sha256 = _digest("9")


class _Version:
    checker_version = "checker-v1"


class _Artifact:
    original_state = report_completion.artifact_store.ArtifactOriginalState.STORED
    content_snapshot_id = "content-1"
    blob_pointer = _Pointer()
    version = _Version()


def _install_exact_loaders(monkeypatch, authority, *, charge=None):
    automatic = _automatic_release(authority)
    stored_charge = charge or cost_store.CustomerChargeDecision(
        eligible=True,
        amount_krw=0.0,
        reason="price_not_configured",
    )
    monkeypatch.setattr(
        report_completion.authority_store,
        "load_release_authority",
        lambda _conn, _authority_id: authority,
    )
    monkeypatch.setattr(
        report_completion.authority_store,
        "load_release_authority_by_public_id",
        lambda _conn, _public_id: authority,
    )
    monkeypatch.setattr(
        report_completion.delivery_store,
        "load_delivery_intent",
        lambda _conn, _public_id: _Intent(),
    )
    monkeypatch.setattr(
        report_completion.artifact_store,
        "load_artifact_metadata",
        lambda _conn, _artifact_id: _Artifact(),
    )
    monkeypatch.setattr(
        report_completion.pdf_release_store,
        "load_automatic_release_record",
        lambda _conn, **_kwargs: automatic,
    )
    monkeypatch.setattr(
        report_completion.cost_store,
        "load_automatic_release_charge",
        lambda _conn, **_kwargs: stored_charge,
    )
    return automatic, stored_charge


def test_완료_영수증은_본문_pdf_자동승인_청구가_모두_같아야_한다(monkeypatch):
    authority = _authority()
    automatic, charge = _install_exact_loaders(monkeypatch, authority)

    receipt = report_completion.assert_exact_report_completion(object(), authority)

    assert receipt == report_completion.ReportCompletionReceipt(
        authority=authority,
        automatic_release=automatic,
        charge=charge,
    )


def test_청구_내용이_바뀌면_저장된_권위가_있어도_완료가_아니다(monkeypatch):
    authority = _authority()
    _install_exact_loaders(
        monkeypatch,
        authority,
        charge=cost_store.CustomerChargeDecision(
            eligible=True,
            amount_krw=9900.0,
            reason="released",
        ),
    )

    with pytest.raises(report_completion.ReportCompletionError, match="청구 결정"):
        report_completion.assert_exact_report_completion(object(), authority)


def test_전달_의무가_complete가_아니면_부분저장을_성공으로_보지_않는다(monkeypatch):
    authority = _authority()
    _install_exact_loaders(monkeypatch, authority)
    monkeypatch.setattr(
        report_completion.delivery_store,
        "load_delivery_intent",
        lambda _conn, _public_id: SimpleNamespace(
            public_id="report-1",
            state="required",
        ),
    )

    with pytest.raises(report_completion.ReportCompletionError, match="완료 상태"):
        report_completion.assert_exact_report_completion(object(), authority)


class _FakeConnection:
    def __init__(self, *, fail_second_commit: bool = False):
        self.commits = 0
        self.statements: list[str] = []
        self.fail_second_commit = fail_second_commit

    def commit(self):
        self.commits += 1
        if self.fail_second_commit and self.commits == 2:
            raise OSError("commit 응답만 유실")

    def execute(self, statement):
        self.statements.append(statement)


def test_commit_응답만_사라져도_정확한_영수증이면_재생성하지_않는다(monkeypatch):
    authority = _authority()
    write_conn = _FakeConnection(fail_second_commit=True)
    read_conn = object()

    @contextmanager
    def writing(_path=None):
        yield write_conn

    @contextmanager
    def reading(_path=None):
        yield read_conn

    monkeypatch.setattr(report_completion.storage_db, "connect", writing)
    monkeypatch.setattr(
        report_completion.storage_db,
        "connect_readonly_existing",
        reading,
    )
    monkeypatch.setattr(
        report_completion,
        "assert_exact_report_completion",
        lambda conn, expected: (
            report_completion.ReportCompletionReceipt(
                authority=expected,
                automatic_release=_automatic_release(expected),
                charge=cost_store.CustomerChargeDecision(
                    eligible=True,
                    amount_krw=0.0,
                    reason="price_not_configured",
                ),
            )
        ),
    )

    recovered = report_completion.commit_report_completion(
        lambda _conn: authority,
    )

    assert recovered.authority == authority
    assert write_conn.statements == ["BEGIN IMMEDIATE"]
    assert write_conn.commits == 2


def test_commit_응답유실_뒤_정확한_영수증이_없으면_성공을_추측하지_않는다(monkeypatch):
    authority = _authority()
    write_conn = _FakeConnection(fail_second_commit=True)

    @contextmanager
    def writing(_path=None):
        yield write_conn

    @contextmanager
    def reading(_path=None):
        yield object()

    monkeypatch.setattr(report_completion.storage_db, "connect", writing)
    monkeypatch.setattr(
        report_completion.storage_db,
        "connect_readonly_existing",
        reading,
    )

    calls = 0

    def verify(_conn, expected):
        nonlocal calls
        calls += 1
        if calls == 1:
            return report_completion.ReportCompletionReceipt(
                authority=expected,
                automatic_release=_automatic_release(expected),
                charge=cost_store.CustomerChargeDecision(
                    eligible=True,
                    amount_krw=0.0,
                    reason="price_not_configured",
                ),
            )
        raise report_completion.ReportCompletionError("없음")

    monkeypatch.setattr(report_completion, "assert_exact_report_completion", verify)

    with pytest.raises(
        report_completion.ReportCompletionCommitUncertain,
        match="재확인",
    ):
        report_completion.commit_report_completion(lambda _conn: authority)


def test_stage_중간실패는_옛_영수증으로_성공_복구하지_않는다(monkeypatch):
    write_conn = _FakeConnection()
    readback_called = False

    @contextmanager
    def writing(_path=None):
        yield write_conn

    @contextmanager
    def reading(_path=None):
        nonlocal readback_called
        readback_called = True
        yield object()

    monkeypatch.setattr(report_completion.storage_db, "connect", writing)
    monkeypatch.setattr(
        report_completion.storage_db,
        "connect_readonly_existing",
        reading,
    )

    def fail(_conn):
        raise ValueError("stage 실패")

    with pytest.raises(ValueError, match="stage 실패"):
        report_completion.commit_report_completion(fail)
    assert readback_called is False


def test_stage가_권위를_반환해도_commit전_검증실패는_복구하지_않는다(monkeypatch):
    authority = _authority()
    write_conn = _FakeConnection()
    readback_called = False

    @contextmanager
    def writing(_path=None):
        yield write_conn

    @contextmanager
    def reading(_path=None):
        nonlocal readback_called
        readback_called = True
        yield object()

    monkeypatch.setattr(report_completion.storage_db, "connect", writing)
    monkeypatch.setattr(
        report_completion.storage_db,
        "connect_readonly_existing",
        reading,
    )
    monkeypatch.setattr(
        report_completion,
        "assert_exact_report_completion",
        lambda _conn, _expected: (_ for _ in ()).throw(
            report_completion.ReportCompletionError("부분 저장")
        ),
    )

    with pytest.raises(report_completion.ReportCompletionError, match="부분 저장"):
        report_completion.commit_report_completion(lambda _conn: authority)
    assert readback_called is False
    assert write_conn.commits == 1
