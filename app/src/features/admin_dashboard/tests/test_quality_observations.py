from __future__ import annotations

from pathlib import Path

from src.features.admin_dashboard.quality_observations import (
    collect_quality_observations,
)
from src.features.pipeline.port import Grade, Report
from src.features.storage import db, reports as report_store
from src.features.storage.constants import TABLE_REPORTS
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_generation.constants import ENGINE_V2_SCHEMA_VERSION
from src.shared.report_quality.constants import STRICT_QUALITY_CONTRACT_VERSION
from src.shared.report_quality.generation import GenerationQualityObservation


def _observation(
    *, release_allowed: bool = False, quality_grade: str = "부분 완성"
) -> GenerationQualityObservation:
    return GenerationQualityObservation(
        mode="generation-shadow",
        contract_version=STRICT_QUALITY_CONTRACT_VERSION,
        quality_grade=quality_grade,
        safety_decision="공개 차단" if not release_allowed else "공개 가능",
        publication_grade=quality_grade,
        release_allowed=release_allowed,
        quality_shortfalls=(),
        safety_problems=(),
        substantive_claims=41,
        verified_claims=21,
        verified_ratio="0.5121951219512195121951219512",
        document_sources=9,
    )


def _report_with_observation(
    *, generated_at: str, company_id: str, observation: GenerationQualityObservation
) -> Report:
    """`release_allowed=False`인 SHADOW 저장분을 흉내 내려면 storage의
    `report_from_dict` 왕복 검증(§`strict_reload`) 때문에 `quality_observation`이
    있는 보고서는 `ENFORCE_NO_PARTIAL`/`FULL`로만 저장할 수 있다(SHADOW·빈
    `release_mode`에 관측값을 실으면 storage가 그 자체를 거부한다 — 이 결함은
    이 티켓 범위 밖이라 root에 별도 보고했다). 여기서는 storage가 실제로
    받아들이는 가장 가벼운 조합(`ENFORCE_NO_PARTIAL`)으로 관측값 있는 보고서를
    흉내 낸다.
    """

    return Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=[],
        generated_at=generated_at,
        schema_version=ENGINE_V2_SCHEMA_VERSION,
        quality_contract_version=STRICT_QUALITY_CONTRACT_VERSION,
        company_id=company_id,
        release_mode=ReleaseMode.ENFORCE_NO_PARTIAL.value,
        quality_observation=observation,
    )


def _legacy_report_without_observation(*, generated_at: str) -> Report:
    """실제 운영 SHADOW 저장분과 같은 모양 — `quality_observation`이 없다."""

    return Report(
        company="옛보고서",
        job="",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=[],
        generated_at=generated_at,
    )


def test_저장된_보고서의_관측값을_바이트_변경_없이_읽는다(tmp_path: Path) -> None:
    db_path = tmp_path / "storage.db"
    observation = _observation(release_allowed=False)
    with db.connect(db_path) as conn:
        report_store.save(
            conn,
            "r1",
            "CORP-001",
            "",
            _report_with_observation(
                generated_at="2026-08-01T00:00:00",
                company_id="CORP-001",
                observation=observation,
            ),
        )

    with db.connect_readonly_existing(db_path) as conn:
        before = conn.execute(
            f"SELECT payload_json FROM {TABLE_REPORTS} WHERE report_id = ?", ("r1",)
        ).fetchone()[0]

        result = collect_quality_observations(conn)

        after = conn.execute(
            f"SELECT payload_json FROM {TABLE_REPORTS} WHERE report_id = ?", ("r1",)
        ).fetchone()[0]

    assert before == after
    assert len(result.rows) == 1
    report_id, company_id, generated_at, release_mode, observed = result.rows[0]
    assert report_id == "r1"
    assert company_id == "CORP-001"
    assert generated_at == "2026-08-01T00:00:00"
    assert release_mode == ReleaseMode.ENFORCE_NO_PARTIAL.value
    assert observed == observation
    assert result.without_observation_count == 0
    assert result.total_reports_scanned == 1


def test_관측값_없는_옛_보고서는_제외_건수로만_센다(tmp_path: Path) -> None:
    db_path = tmp_path / "storage.db"
    with db.connect(db_path) as conn:
        report_store.save(
            conn, "old-1", "CORP-002", "", _legacy_report_without_observation(
                generated_at="2026-01-01T00:00:00"
            )
        )
        report_store.save(
            conn, "old-2", "CORP-003", "", _legacy_report_without_observation(
                generated_at="2026-01-02T00:00:00"
            )
        )
        report_store.save(
            conn,
            "new-1",
            "CORP-004",
            "",
            _report_with_observation(
                generated_at="2026-01-03T00:00:00",
                company_id="CORP-004",
                observation=_observation(release_allowed=True),
            ),
        )

    with db.connect_readonly_existing(db_path) as conn:
        result = collect_quality_observations(conn)

    assert len(result.rows) == 1
    assert result.rows[0][0] == "new-1"
    assert result.without_observation_count == 2
    assert result.total_reports_scanned == 3


def test_기간_필터(tmp_path: Path) -> None:
    """필터는 report.generated_at이 아니라 저장 행의 created_at 기준이다.

    `report_store.list_report_ids()`(storage 공개 API)가 `created_at`으로
    거르므로, 이 시험도 `save()`의 `created_at`을 직접 지정해 통제한다.
    """

    db_path = tmp_path / "storage.db"
    with db.connect(db_path) as conn:
        for report_id, created_at in (
            ("early", "2026-01-01T00:00:00"),
            ("middle", "2026-02-01T00:00:00"),
            ("late", "2026-03-01T00:00:00"),
        ):
            report_store.save(
                conn,
                report_id,
                "CORP-005",
                "",
                _report_with_observation(
                    generated_at=created_at,
                    company_id="CORP-005",
                    observation=_observation(),
                ),
                created_at=created_at,
            )

    with db.connect_readonly_existing(db_path) as conn:
        only_middle = collect_quality_observations(
            conn, since="2026-01-15", until="2026-02-15"
        )
        from_middle = collect_quality_observations(conn, since="2026-02-01")
        until_middle = collect_quality_observations(conn, until="2026-02-01")

    assert [row[0] for row in only_middle.rows] == ["middle"]
    assert [row[0] for row in from_middle.rows] == ["middle", "late"]
    assert [row[0] for row in until_middle.rows] == ["early", "middle"]
