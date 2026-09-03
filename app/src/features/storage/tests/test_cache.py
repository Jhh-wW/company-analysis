"""캐시 1층·2층·별칭 시험.

★ 가장 중요한 시험 묶음은 「다른 회사가 안 섞이는지」다 — 캐시가 틀린 회사의
  보고서를 돌려주면 최악의 사고이기 때문이다.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from src.core import deployment_identity
from src.features.pipeline.port import Grade, Report
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.provenance.sources import Source, SourceKind
from src.features.report_standard.publish import PublishBlockedError
from src.features.storage import cache, db
from src.shared import engine_build_identity as build_identity_contract

SOURCE_IDENTITY_DIGEST = "a" * 64
BUILD_A = f"{build_identity_contract.ENGINE_BUILD_ID_CONTRACT_VERSION}:{'a' * 40}"
BUILD_B = f"{build_identity_contract.ENGINE_BUILD_ID_CONTRACT_VERSION}:{'b' * 40}"
BUILD_IDENTITY_A = build_identity_contract.EngineBuildIdentity("a" * 40, BUILD_A)
BUILD_IDENTITY_B = build_identity_contract.EngineBuildIdentity("b" * 40, BUILD_B)
UNKNOWN_BUILD_IDENTITY = build_identity_contract.EngineBuildIdentity(
    "", build_identity_contract.UNKNOWN_BUILD_ID
)


@pytest.fixture(autouse=True)
def _검증된_A배포에서_캐시를_시험한다(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)


def _report(company: str = "가나다전자", grade: Grade = Grade.COMPLETE) -> Report:
    return Report(
        company=company,
        job="영업",
        corp_type="상장사",
        grade=grade,
        sections=[],
        generated_at="2026-08-15",
    )


def _report_with_news(published_at: str) -> Report:
    return Report(
        company="가나다전자",
        job="영업",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
        citations=[
            Source(
                number=1,
                kind=SourceKind.NEWS,
                label="가나다전자 관련 기사",
                published_at=published_at,
                domain="example.co.kr",
            )
        ],
        generated_at="2026-08-15",
    )


def _company_report() -> Report:
    return build_demo_report()


def _legacy_company_report() -> Report:
    return Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
        requirements=[],
        generated_at="2026-08-15",
    )


# ══════════════════════════════════════════════════════════
# 정규화 · 지문
# ══════════════════════════════════════════════════════════


def test_normalize_job_collapses_whitespace_and_case() -> None:
    assert cache.normalize_job("  백엔드   개발자  ") == cache.normalize_job("백엔드 개발자")
    assert cache.normalize_job("Backend Engineer") == cache.normalize_job("backend  engineer")


def test_posting_fingerprint_ignores_order_and_spacing() -> None:
    a = cache.posting_fingerprint(["3년 이상 경력", "  관련 전공  우대 "])
    b = cache.posting_fingerprint(["관련 전공 우대", "3년 이상 경력"])
    assert a == b


def test_posting_fingerprint_differs_for_different_content() -> None:
    a = cache.posting_fingerprint(["3년 이상 경력"])
    b = cache.posting_fingerprint(["5년 이상 경력"])
    assert a != b


# ══════════════════════════════════════════════════════════
# 1층 — 저장·조회·미스
# ══════════════════════════════════════════════════════════


def test_layer1_save_then_hit(tmp_path: Path) -> None:
    report = _report()
    with db.connect(tmp_path / "storage.db") as conn:
        cache.save_layer1(
            conn,
            corp_id="CORP-001",
            job="영업",
            requirements=["3년 이상 경력"],
            report=report,
            engine_build_identity=BUILD_IDENTITY_A,
            fiscal_year=2025,
        )
        hit = cache.get_layer1_hit(
            conn,
            corp_id="CORP-001",
            job="영업",
            requirements=["3년 이상 경력"],
            engine_build_identity=BUILD_IDENTITY_A,
            current_fiscal_year=2025,
        )
    assert hit == report


def test_layer1_has_candidates_before_fingerprint_known(tmp_path: Path) -> None:
    """4번(캐시 확인)은 지문 없이 회사×직무만으로 "후보가 있는지"만 본다."""
    with db.connect(tmp_path / "storage.db") as conn:
        assert cache.has_layer1_candidates(conn, corp_id="CORP-001", job="영업") is False
        cache.save_layer1(
            conn,
            corp_id="CORP-001",
            job="영업",
            requirements=["3년 이상 경력"],
            report=_report(),
            engine_build_identity=BUILD_IDENTITY_A,
            fiscal_year=2025,
        )
        assert cache.has_layer1_candidates(conn, corp_id="CORP-001", job="영업") is True


def test_layer1_miss_when_posting_fingerprint_differs(tmp_path: Path) -> None:
    """같은 회사·직무여도 «다른 공고»면 미스 — 캐시 키에 지문이 들어간 이유다."""
    with db.connect(tmp_path / "storage.db") as conn:
        cache.save_layer1(
            conn,
            corp_id="CORP-001",
            job="영업",
            requirements=["3년 이상 경력"],
            report=_report(),
            engine_build_identity=BUILD_IDENTITY_A,
            fiscal_year=2025,
        )
        hit = cache.get_layer1_hit(
            conn,
            corp_id="CORP-001",
            job="영업",
            requirements=["전혀 다른 요구역량"],
            engine_build_identity=BUILD_IDENTITY_A,
            current_fiscal_year=2025,
        )
    assert hit is None


def test_옛_빈직무_빈공고_항목은_회사분석_캐시에_적중하지_않는다(tmp_path: Path) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        cache.save_layer1(
            conn,
            corp_id="CORP-001",
            job="",
            requirements=[],
            report=_report(),
            engine_build_identity=BUILD_IDENTITY_A,
            fiscal_year=2025,
        )

        hit = cache.get_company_report_hit(
            conn,
            corp_id="CORP-001",
            build_identity=BUILD_IDENTITY_A,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            current_fiscal_year=2025,
        )

    assert hit is None


def test_회사분석_캐시는_직무와_공고_없이_전용_namespace로_왕복한다(tmp_path: Path) -> None:
    report = _company_report()
    with db.connect(tmp_path / "storage.db") as conn:
        cache.save_company_report(
            conn,
            corp_id="CORP-001",
            report=report,
            build_identity=BUILD_IDENTITY_A,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            fiscal_year=2025,
        )
        hit = cache.get_company_report_hit(
            conn,
            corp_id="CORP-001",
            build_identity=BUILD_IDENTITY_A,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            current_fiscal_year=2025,
        )

    assert hit == report


def test_layer1_commit실패는_본문과열쇠를_모두_rollback한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_commit(_conn) -> None:
        raise sqlite3.OperationalError("주입한 commit 실패")

    monkeypatch.setattr(cache, "_commit_connection", fail_commit)
    with pytest.raises(sqlite3.OperationalError, match="commit 실패"):
        with db.connect(tmp_path / "storage.db") as conn:
            cache.save_layer1(
                conn,
                corp_id="CORP-001",
                job="영업",
                requirements=["3년 이상 경력"],
                report=_report(),
                engine_build_identity=BUILD_IDENTITY_A,
                fiscal_year=2025,
            )

    with db.connect(tmp_path / "storage.db") as conn:
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache.TABLE_LAYER1_CACHE}"
        ).fetchone()[0] == 0
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache.TABLE_REPORTS}"
        ).fetchone()[0] == 0


def test_layer1_commit응답만_잃으면_exact_key와_epoch로_성공을_복구한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "cache-commit-response-loss.db"

    def commit_then_lose_response(conn) -> None:
        conn.commit()
        raise sqlite3.OperationalError("commit 응답만 손실")

    monkeypatch.setattr(cache, "_commit_connection", commit_then_lose_response)
    with db.connect_explicit_commit(target) as conn:
        report_id = cache.save_company_report(
            conn,
            corp_id="CORP-001",
            report=_company_report(),
            build_identity=BUILD_IDENTITY_A,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            fiscal_year=2025,
        )

    assert report_id
    with db.connect(target) as conn:
        row = conn.execute(
            f"""
            SELECT c.report_id, c.engine_epoch_digest, r.engine_epoch_digest
            FROM {cache.TABLE_LAYER1_CACHE} AS c
            JOIN {cache.TABLE_REPORTS} AS r ON r.report_id = c.report_id
            """
        ).fetchone()
    assert row is not None
    assert tuple(row) == (
        report_id,
        BUILD_IDENTITY_A.epoch_digest,
        BUILD_IDENTITY_A.epoch_digest,
    )


def test_layer1_commit뒤_epoch영수증이_바뀌면_캐시와본문을_격리한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "cache-commit-drift.db"

    def commit_tamper_then_lose_response(conn) -> None:
        conn.commit()
        conn.execute(
            f"UPDATE {cache.TABLE_REPORTS} SET engine_epoch_digest = ?",
            (BUILD_IDENTITY_B.epoch_digest,),
        )
        conn.commit()
        raise sqlite3.OperationalError("commit 뒤 epoch 영수증 drift")

    monkeypatch.setattr(
        cache,
        "_commit_connection",
        commit_tamper_then_lose_response,
    )
    with pytest.raises(sqlite3.OperationalError, match="영수증 drift"):
        with db.connect_explicit_commit(target) as conn:
            cache.save_company_report(
                conn,
                corp_id="CORP-001",
                report=_company_report(),
                build_identity=BUILD_IDENTITY_A,
                source_identity_digest=SOURCE_IDENTITY_DIGEST,
                fiscal_year=2025,
            )

    with db.connect(target) as conn:
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache.TABLE_LAYER1_CACHE}"
        ).fetchone()[0] == 0
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache.TABLE_REPORTS}"
        ).fetchone()[0] == 0


def test_layer1_commit직후_process_epoch가_바뀌면_새행을_격리한다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "cache-post-commit-process-drift.db"

    def commit_then_change_process_epoch(conn) -> None:
        conn.commit()
        build_identity_contract._reset_process_engine_build_identity_for_tests()  # noqa: SLF001
        build_identity_contract.freeze_process_engine_build_identity(BUILD_IDENTITY_B)

    monkeypatch.setattr(
        cache,
        "_commit_connection",
        commit_then_change_process_epoch,
    )
    with pytest.raises(build_identity_contract.EngineBuildIdentityChangedError):
        with db.connect_explicit_commit(target) as conn:
            cache.save_company_report(
                conn,
                corp_id="CORP-001",
                report=_company_report(),
                build_identity=BUILD_IDENTITY_A,
                source_identity_digest=SOURCE_IDENTITY_DIGEST,
                fiscal_year=2025,
            )

    with db.connect(target) as conn:
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache.TABLE_LAYER1_CACHE}"
        ).fetchone()[0] == 0
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache.TABLE_REPORTS}"
        ).fetchone()[0] == 0


def test_layer1은_요청중_raw환경변화가_아닌_process_epoch_A만_쓴다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captures = iter((BUILD_IDENTITY_A, BUILD_IDENTITY_B))
    monkeypatch.setattr(
        build_identity_contract,
        "capture_engine_build_identity",
        lambda: next(captures),
    )

    with db.connect(tmp_path / "storage.db") as conn:
        cache.save_layer1(
            conn,
            corp_id="CORP-001",
            job="영업",
            requirements=["3년 이상 경력"],
            report=_report(),
            engine_build_identity=BUILD_IDENTITY_A,
            fiscal_year=2025,
        )

    with db.connect(tmp_path / "storage.db") as conn:
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache.TABLE_LAYER1_CACHE}"
        ).fetchone()[0] == 1
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache.TABLE_REPORTS}"
        ).fetchone()[0] == 1


def test_v1_회사분석_캐시는_배포_A_결과를_배포_B에_주지_않는다(
    tmp_path: Path,
) -> None:
    report = _company_report()
    with db.connect(tmp_path / "storage.db") as conn:
        saved = cache.save_company_report(
            conn,
            corp_id="CORP-001",
            report=report,
            build_identity=BUILD_IDENTITY_A,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            fiscal_year=2025,
        )
        hit_a = cache.get_company_report_hit(
            conn,
            corp_id="CORP-001",
            build_identity=BUILD_IDENTITY_A,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            current_fiscal_year=2025,
        )
        hit_b = cache.get_company_report_hit(
            conn,
            corp_id="CORP-001",
            build_identity=BUILD_IDENTITY_B,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            current_fiscal_year=2025,
        )

    assert saved is not None
    assert hit_a == report
    assert hit_b is None


@pytest.mark.parametrize("current_commit", ("b" * 40, ""))
def test_v1_실제저장직전_A가_B나_unknown이면_행을_남기지_않는다(
    current_commit: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if current_commit:
        monkeypatch.setenv("RENDER_GIT_COMMIT", current_commit)
    else:
        monkeypatch.delenv("RENDER_GIT_COMMIT", raising=False)

    with pytest.raises(build_identity_contract.EngineBuildIdentityChangedError):
        with db.connect(tmp_path / "storage.db") as conn:
            cache.save_company_report(
                conn,
                corp_id="CORP-001",
                report=_company_report(),
                build_identity=BUILD_IDENTITY_A,
                source_identity_digest=SOURCE_IDENTITY_DIGEST,
                fiscal_year=2025,
            )

    with db.connect(tmp_path / "storage.db") as conn:
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache.TABLE_LAYER1_CACHE}"
        ).fetchone()[0] == 0
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache.TABLE_REPORTS}"
        ).fetchone()[0] == 0


def test_v1_unknown생성뒤_commit이_생겨도_행을_남기지_않는다(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    frozen = build_identity_contract.capture_engine_build_identity()
    monkeypatch.setenv("RENDER_GIT_COMMIT", "b" * 40)

    with db.connect(tmp_path / "storage.db") as conn:
        assert cache.save_company_report(
            conn,
            corp_id="CORP-001",
            report=_company_report(),
            build_identity=frozen,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            fiscal_year=2025,
        ) is None
        assert conn.execute(
            f"SELECT COUNT(*) FROM {cache.TABLE_LAYER1_CACHE}"
        ).fetchone()[0] == 0


def test_v1_회사분석_캐시는_배포commit을_모르면_읽지도_쓰지도_않는다(
    tmp_path: Path,
) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        saved = cache.save_company_report(
            conn,
            corp_id="CORP-001",
            report=_company_report(),
            build_identity=UNKNOWN_BUILD_IDENTITY,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            fiscal_year=2025,
        )
        hit = cache.get_company_report_hit(
            conn,
            corp_id="CORP-001",
            build_identity=UNKNOWN_BUILD_IDENTITY,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            current_fiscal_year=2025,
        )
        rows = conn.execute(
            f"SELECT COUNT(*) FROM {cache.TABLE_LAYER1_CACHE}"
        ).fetchone()[0]

    assert saved is None
    assert hit is None
    assert rows == 0


def test_회사분석_캐시는_DART_출처가_바뀌면_옛_보고서를_내주지_않는다(
    tmp_path: Path,
) -> None:
    report = _company_report()
    with db.connect(tmp_path / "storage.db") as conn:
        cache.save_company_report(
            conn,
            corp_id="CORP-001",
            report=report,
            build_identity=BUILD_IDENTITY_A,
            source_identity_digest="a" * 64,
            fiscal_year=2025,
        )
        hit = cache.get_company_report_hit(
            conn,
            corp_id="CORP-001",
            build_identity=BUILD_IDENTITY_A,
            source_identity_digest="b" * 64,
            current_fiscal_year=2025,
        )

    assert hit is None


def test_회사분석_캐시는_출처를_모르면_읽지도_쓰지도_않는다(
    tmp_path: Path,
) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        saved = cache.save_company_report(
            conn,
            corp_id="CORP-001",
            report=_company_report(),
            build_identity=BUILD_IDENTITY_A,
            source_identity_digest="",
            fiscal_year=2025,
        )
        hit = cache.get_company_report_hit(
            conn,
            corp_id="CORP-001",
            build_identity=BUILD_IDENTITY_A,
            source_identity_digest="",
            current_fiscal_year=2025,
        )

    assert saved is None
    assert hit is None


def test_출고_불가한_canonical_보고서는_캐시에_저장하지_않는다(tmp_path: Path) -> None:
    invalid = replace(_company_report(), summary_items=[])

    with db.connect(tmp_path / "storage.db") as conn:
        with pytest.raises(PublishBlockedError):
            cache.save_company_report(
                conn,
                corp_id="CORP-001",
                report=invalid,
                build_identity=BUILD_IDENTITY_A,
                source_identity_digest=SOURCE_IDENTITY_DIGEST,
                fiscal_year=2025,
            )


def test_회사분석_schema_version이_바뀌면_옛_캐시는_미적중한다(
    tmp_path: Path, monkeypatch
) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        cache.save_company_report(
            conn,
            corp_id="CORP-001",
            report=_company_report(),
            build_identity=BUILD_IDENTITY_A,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            fiscal_year=2025,
        )
        monkeypatch.setattr(
            cache,
            "_COMPANY_ANALYSIS_SCHEMA_REQUIREMENTS",
            ("schema:company-report-v2",),
        )
        hit = cache.get_company_report_hit(
            conn,
            corp_id="CORP-001",
            build_identity=BUILD_IDENTITY_A,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            current_fiscal_year=2025,
        )

    assert hit is None


def test_v2_회사분석_payload는_v3_캐시에_적중하지_않는다(tmp_path: Path) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        # 과거 버전이 새 namespace 아래 잘못 저장된 손상 상태를 직접 재현한다.
        # 공개 저장 API 자체는 이제 canonical 보고서만 받는다.
        cache.save_layer1(
            conn,
            corp_id="CORP-001",
            job=cache._COMPANY_ANALYSIS_PRODUCT_KEY,
            requirements=list(cache._COMPANY_ANALYSIS_SCHEMA_REQUIREMENTS),
            report=_legacy_company_report(),
            engine_build_identity=BUILD_IDENTITY_A,
            fiscal_year=2025,
        )

        hit = cache.get_company_report_hit(
            conn,
            corp_id="CORP-001",
            build_identity=BUILD_IDENTITY_A,
            source_identity_digest=SOURCE_IDENTITY_DIGEST,
            current_fiscal_year=2025,
        )

    assert hit is None


def test_layer1_never_leaks_across_different_companies(tmp_path: Path) -> None:
    """★ 가장 중요한 시험 — 회사명이 같아도(계열사 등) corp_id가 다르면 절대 안 섞인다."""
    same_looking_report_a = _report(company="가나다전자")
    same_looking_report_b = _report(company="가나다전자")  # 이름은 같은데 다른 법인
    requirements = ["3년 이상 경력"]

    with db.connect(tmp_path / "storage.db") as conn:
        cache.save_layer1(
            conn,
            corp_id="CORP-A",
            job="영업",
            requirements=requirements,
            report=same_looking_report_a,
            engine_build_identity=BUILD_IDENTITY_A,
            fiscal_year=2025,
        )
        cache.save_layer1(
            conn,
            corp_id="CORP-B",
            job="영업",
            requirements=requirements,
            report=same_looking_report_b,
            engine_build_identity=BUILD_IDENTITY_A,
            fiscal_year=2025,
        )

        hit_a = cache.get_layer1_hit(
            conn, corp_id="CORP-A", job="영업", requirements=requirements,
            engine_build_identity=BUILD_IDENTITY_A,
            current_fiscal_year=2025,
        )
        hit_b = cache.get_layer1_hit(
            conn, corp_id="CORP-B", job="영업", requirements=requirements,
            engine_build_identity=BUILD_IDENTITY_A,
            current_fiscal_year=2025,
        )
        miss_c = cache.get_layer1_hit(
            conn, corp_id="CORP-C", job="영업", requirements=requirements,
            engine_build_identity=BUILD_IDENTITY_A,
            current_fiscal_year=2025,
        )

    assert hit_a is not None and hit_a.company == "가나다전자"
    assert hit_b is not None and hit_b.company == "가나다전자"
    assert hit_a is not hit_b  # 서로 다른 저장물이다(우연히 값이 같아 보일 뿐)
    assert miss_c is None  # 저장한 적 없는 회사는 당연히 미스


def test_layer1_save_twice_same_key_updates_report(tmp_path: Path) -> None:
    """같은 키로 두 번 저장 — 중복 행이 아니라 덮어써야 한다."""
    requirements = ["3년 이상 경력"]
    with db.connect(tmp_path / "storage.db") as conn:
        cache.save_layer1(
            conn, corp_id="CORP-001", job="영업", requirements=requirements,
            report=_report(grade=Grade.PARTIAL),
            engine_build_identity=BUILD_IDENTITY_A, fiscal_year=2025,
        )
        cache.save_layer1(
            conn, corp_id="CORP-001", job="영업", requirements=requirements,
            report=_report(grade=Grade.COMPLETE),
            engine_build_identity=BUILD_IDENTITY_A, fiscal_year=2025,
        )
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM layer1_cache "
            "WHERE corp_id = 'CORP-001' AND job_key = ?",
            (cache.normalize_job("영업"),),
        ).fetchone()["n"]
        hit = cache.get_layer1_hit(
            conn, corp_id="CORP-001", job="영업", requirements=requirements,
            engine_build_identity=BUILD_IDENTITY_A,
            current_fiscal_year=2025,
        )
    assert count == 1
    assert hit is not None
    assert hit.grade is Grade.COMPLETE


# ══════════════════════════════════════════════════════════
# 1층 — O9 신선도
# ══════════════════════════════════════════════════════════


def test_layer1_stale_when_fiscal_year_changed(tmp_path: Path) -> None:
    requirements = ["3년 이상 경력"]
    with db.connect(tmp_path / "storage.db") as conn:
        cache.save_layer1(
            conn, corp_id="CORP-001", job="영업", requirements=requirements,
            report=_report(), engine_build_identity=BUILD_IDENTITY_A, fiscal_year=2024,
        )
        hit = cache.get_layer1_hit(
            conn, corp_id="CORP-001", job="영업", requirements=requirements,
            engine_build_identity=BUILD_IDENTITY_A,
            current_fiscal_year=2025,  # 사업연도가 바뀌었다
        )
    assert hit is None


def test_layer1_treated_as_stale_when_fiscal_year_unknown(tmp_path: Path) -> None:
    """current_fiscal_year를 안 넘기면(모르면) 신선하다고 우기지 않는다(보수적 기본값)."""
    requirements = ["3년 이상 경력"]
    with db.connect(tmp_path / "storage.db") as conn:
        cache.save_layer1(
            conn, corp_id="CORP-001", job="영업", requirements=requirements,
            report=_report(), engine_build_identity=BUILD_IDENTITY_A, fiscal_year=2025,
        )
        hit = cache.get_layer1_hit(
            conn, corp_id="CORP-001", job="영업", requirements=requirements,
            engine_build_identity=BUILD_IDENTITY_A,
            current_fiscal_year=None,
        )
    assert hit is None


def test_layer1_stale_when_news_citation_older_than_3_years(tmp_path: Path) -> None:
    requirements = ["3년 이상 경력"]
    old_news_report = _report_with_news(published_at="2020-01-01")
    with db.connect(tmp_path / "storage.db") as conn:
        cache.save_layer1(
            conn, corp_id="CORP-001", job="영업", requirements=requirements,
            report=old_news_report,
            engine_build_identity=BUILD_IDENTITY_A, fiscal_year=2025,
        )
        hit = cache.get_layer1_hit(
            conn, corp_id="CORP-001", job="영업", requirements=requirements,
            engine_build_identity=BUILD_IDENTITY_A,
            current_fiscal_year=2025, today=dt.date(2026, 8, 15),
        )
    assert hit is None


def test_layer1_fresh_when_news_citation_within_3_years(tmp_path: Path) -> None:
    requirements = ["3년 이상 경력"]
    recent_news_report = _report_with_news(published_at="2025-06-01")
    with db.connect(tmp_path / "storage.db") as conn:
        cache.save_layer1(
            conn, corp_id="CORP-001", job="영업", requirements=requirements,
            report=recent_news_report,
            engine_build_identity=BUILD_IDENTITY_A, fiscal_year=2025,
        )
        hit = cache.get_layer1_hit(
            conn, corp_id="CORP-001", job="영업", requirements=requirements,
            engine_build_identity=BUILD_IDENTITY_A,
            current_fiscal_year=2025, today=dt.date(2026, 8, 15),
        )
    assert hit is not None


@pytest.mark.parametrize(
    ("collected_at", "expected"),
    [("2022-08-15", False), ("2026-08-01", True)],
)
def test_layer1_공식웹은_현재조회일_기준_400일안에서만_재사용한다(
    collected_at: str,
    expected: bool,
) -> None:
    report = replace(
        _report(),
        citations=[
            Source(
                number=1,
                kind=SourceKind.OTHER,
                label="회사 소개",
                collected_at=collected_at,
                source_type="회사 공식 웹",
                url="https://company.example/about",
            )
        ],
    )

    assert cache._is_layer1_fresh(
        report,
        cached_fiscal_year=2025,
        current_fiscal_year=2025,
        today=dt.date(2026, 8, 15),
    ) is expected


# ══════════════════════════════════════════════════════════
# 1층 — 보관 상한 (5개, 축출 우선순위)
# ══════════════════════════════════════════════════════════


def test_layer1_evicts_oldest_beyond_cap(tmp_path: Path) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        for i in range(cache.LAYER1_MAX_ENTRIES_PER_JOB + 2):
            cache.save_layer1(
                conn,
                corp_id="CORP-001",
                job="영업",
                requirements=[f"요구역량-{i}"],
                report=_report(),
                engine_build_identity=BUILD_IDENTITY_A,
                fiscal_year=2025,
                now=dt.datetime(2026, 1, 1) + dt.timedelta(minutes=i),
            )
        count = conn.execute(
            "SELECT COUNT(*) AS n FROM layer1_cache WHERE corp_id = 'CORP-001'"
        ).fetchone()["n"]
        # 가장 먼저 저장한(가장 오래된) 지문 두 개는 지워졌어야 한다.
        earliest_hit = cache.get_layer1_hit(
            conn, corp_id="CORP-001", job="영업", requirements=["요구역량-0"],
            engine_build_identity=BUILD_IDENTITY_A,
            current_fiscal_year=2025,
        )
        latest_hit = cache.get_layer1_hit(
            conn, corp_id="CORP-001", job="영업",
            requirements=[f"요구역량-{cache.LAYER1_MAX_ENTRIES_PER_JOB + 1}"],
            engine_build_identity=BUILD_IDENTITY_A,
            current_fiscal_year=2025,
        )
    assert count == cache.LAYER1_MAX_ENTRIES_PER_JOB
    assert earliest_hit is None
    assert latest_hit is not None


def test_layer1_eviction_prefers_stale_fiscal_year_first(tmp_path: Path) -> None:
    """상한을 넘기면 «가장 오래된 것»이 아니라 «사업연도가 다른 것»을 먼저 지운다.

    시나리오 — 신선(2025)한 항목 여러 개 사이에 옛 사업연도(2024) 하나를 끼워
    넣는다. 옛 사업연도 항목은 «가장 오래된» 것이 아니다(나이는 중간). 단순
    "오래된 순" 축출이었다면 가장 먼저 저장한 신선한 항목이 지워져야 하지만,
    정본(§보관 상한)은 "사업연도가 바뀐 것"을 우선 축출하라고 한다.
    """
    cap = cache.LAYER1_MAX_ENTRIES_PER_JOB
    base = dt.datetime(2026, 1, 1)

    with db.connect(tmp_path / "storage.db") as conn:
        # 가장 오래된 신선한 항목 — 단순 "오래된 순"이면 이게 축출 1순위여야 한다.
        cache.save_layer1(
            conn, corp_id="CORP-001", job="영업", requirements=["신선-가장오래됨"],
            report=_report(), engine_build_identity=BUILD_IDENTITY_A,
            fiscal_year=2025, now=base,
        )
        for i in range(1, cap - 1):
            cache.save_layer1(
                conn, corp_id="CORP-001", job="영업", requirements=[f"신선-{i}"],
                report=_report(), engine_build_identity=BUILD_IDENTITY_A,
                fiscal_year=2025, now=base + dt.timedelta(minutes=i),
            )
        # 옛 사업연도 — 나이는 중간이지만 사업연도가 다르다. 여기까지 총 cap개(상한 안).
        cache.save_layer1(
            conn, corp_id="CORP-001", job="영업", requirements=["옛-사업연도"],
            report=_report(), engine_build_identity=BUILD_IDENTITY_A,
            fiscal_year=2024, now=base + dt.timedelta(minutes=cap),
        )
        # cap+1번째 — 신선한 항목을 하나 더 넣어 상한을 넘긴다(축출 발생).
        cache.save_layer1(
            conn, corp_id="CORP-001", job="영업", requirements=["신선-최신"],
            report=_report(), engine_build_identity=BUILD_IDENTITY_A,
            fiscal_year=2025, now=base + dt.timedelta(minutes=cap + 1),
        )

        stale_hit = cache.get_layer1_hit(
            conn, corp_id="CORP-001", job="영업", requirements=["옛-사업연도"],
            engine_build_identity=BUILD_IDENTITY_A,
            current_fiscal_year=2025,
        )
        oldest_fresh_hit = cache.get_layer1_hit(
            conn, corp_id="CORP-001", job="영업", requirements=["신선-가장오래됨"],
            engine_build_identity=BUILD_IDENTITY_A,
            current_fiscal_year=2025,
        )

    assert stale_hit is None  # 사업연도가 다른 항목이 먼저 축출됐다
    assert oldest_fresh_hit is not None  # 신선한 항목은(더 오래됐어도) 살아남는다


# ══════════════════════════════════════════════════════════
# 2층 — 회사 단위 수집 자료
# ══════════════════════════════════════════════════════════


def test_corp_only_layer2는_읽기와쓰기를_모두_명시적으로_차단한다(
    tmp_path: Path,
) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        with pytest.raises(cache.Layer2CacheIdentityRequiredError, match="저장"):
            cache.save_layer2(
                conn,
                corp_id="CORP-001",
                fragments={1: {"종류": "공시", "원문": "본문", "출처": ""}},
            )
        assert conn.execute("SELECT COUNT(*) FROM layer2_cache").fetchone()[0] == 0
        conn.execute(
            """
            INSERT INTO layer2_cache (
                corp_id, fragments_json, filing_json, cell_judgments_json,
                fiscal_year, collected_at, updated_at
            ) VALUES (?, ?, NULL, NULL, ?, ?, ?)
            """,
            ("CORP-001", "[]", 2025, "2026-08-31", "2026-08-31"),
        )
        with pytest.raises(cache.Layer2CacheIdentityRequiredError, match="재사용"):
            cache.get_layer2(conn, "CORP-001")
        assert conn.execute("SELECT COUNT(*) FROM layer2_cache").fetchone()[0] == 1


# ══════════════════════════════════════════════════════════
# 별칭 캐시
# ══════════════════════════════════════════════════════════


def test_alias_save_and_get(tmp_path: Path) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        assert cache.get_alias(conn, "가나다") is None
        cache.save_alias(conn, "가나다", "CORP-001")
        assert cache.get_alias(conn, "가나다") == "CORP-001"
        assert cache.get_alias(conn, "  가나다  ") == "CORP-001"  # 정규화 후 같은 키


def test_alias_invalidate_removes_entry(tmp_path: Path) -> None:
    with db.connect(tmp_path / "storage.db") as conn:
        cache.save_alias(conn, "가나다", "CORP-001")
        cache.invalidate_alias(conn, "가나다")
        assert cache.get_alias(conn, "가나다") is None


# ══════════════════════════════════════════════════════════
# S2 — 공고 원문이 캐시 경로 어디에도 들어갈 수 없다
# ══════════════════════════════════════════════════════════


def test_save_layer1_signature_has_no_posting_text_parameter() -> None:
    """`save_layer1`은 `requirements`(요구역량 목록)만 받는다 — 공고 원문을
    넘길 파라미터 자체가 없다(도구정의 규칙 4 — "요구역량 목록만 캐시").
    """
    import inspect

    params = set(inspect.signature(cache.save_layer1).parameters)
    assert "posting_text" not in params
    assert "posting" not in params
    assert {"corp_id", "job", "requirements", "report"} <= params
