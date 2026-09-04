from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[2]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.features.pipeline.port import Grade, Report
from src.features.storage import db, reports as report_store
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_generation.constants import ENGINE_V2_SCHEMA_VERSION
from src.shared.report_quality.constants import LEGACY_STRICT_QUALITY_CONTRACT_VERSION
from src.shared.report_quality.generation import GenerationQualityObservation
from tools import quality_observations


def _observation(*, release_allowed: bool) -> GenerationQualityObservation:
    return GenerationQualityObservation(
        mode="generation-shadow",
        contract_version=LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
        quality_grade="부분 완성",
        safety_decision="공개 가능" if release_allowed else "공개 차단",
        publication_grade="부분 완성",
        release_allowed=release_allowed,
        quality_shortfalls=(),
        safety_problems=(),
        substantive_claims=41,
        verified_claims=21,
        verified_ratio="0.5121951219512195121951219512",
        document_sources=9,
        quality_problem_codes=() if release_allowed else ("too_few_document_sources",),
    )


def _build_fixture_db(db_path: Path) -> None:
    with db.connect(db_path) as conn:
        report_store.save(
            conn,
            "blocked-1",
            "CORP-001",
            "",
            Report(
                company="가나다전자",
                job="",
                corp_type="상장사",
                grade=Grade.PARTIAL,
                sections=[],
                generated_at="2026-08-01T00:00:00",
                schema_version=ENGINE_V2_SCHEMA_VERSION,
                quality_contract_version=LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
                company_id="CORP-001",
                release_mode=ReleaseMode.ENFORCE_NO_PARTIAL.value,
                quality_observation=_observation(release_allowed=False),
            ),
        )
        report_store.save(
            conn,
            "allowed-1",
            "CORP-002",
            "",
            Report(
                company="라마바전자",
                job="",
                corp_type="상장사",
                grade=Grade.COMPLETE,
                sections=[],
                generated_at="2026-08-02T00:00:00",
                schema_version=ENGINE_V2_SCHEMA_VERSION,
                quality_contract_version=LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
                company_id="CORP-002",
                release_mode=ReleaseMode.ENFORCE_NO_PARTIAL.value,
                quality_observation=_observation(release_allowed=True),
            ),
        )
        report_store.save(
            conn,
            "legacy-1",
            "CORP-003",
            "",
            Report(
                company="옛보고서",
                job="",
                corp_type="상장사",
                grade=Grade.PARTIAL,
                sections=[],
                generated_at="2026-08-03T00:00:00",
            ),
        )


def test_CLI는_임시_DB에서_요약을_출력하고_DB를_수정하지_않는다(
    tmp_path: Path, capsys
) -> None:
    db_path = tmp_path / "storage.db"
    _build_fixture_db(db_path)
    before_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()

    exit_code = quality_observations.main(["--db", str(db_path), "--json"])

    after_hash = hashlib.sha256(db_path.read_bytes()).hexdigest()
    assert before_hash == after_hash

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["total_reports_scanned"] == 3
    assert output["without_observation_count"] == 1
    assert output["total_observations"] == 2
    assert output["release_blocked_count"] == 1
    assert output["release_blocked_ratio"] == "0.5"
    assert {"CORP-001", "CORP-002"} == {
        item["company_id"] for item in output["top_companies"]
    }


def test_인자_없으면_사용법과_종료코드_2(capsys) -> None:
    with pytest.raises(SystemExit) as exc_info:
        quality_observations.main([])

    assert exc_info.value.code == 2
    captured = capsys.readouterr()
    assert "usage" in captured.err.lower()


def test_DB_파일이_없으면_1을_돌려주고_생성하지_않는다(tmp_path: Path, capsys) -> None:
    missing = tmp_path / "no-such-storage.db"

    exit_code = quality_observations.main(["--db", str(missing)])

    assert exit_code == 1
    assert not missing.exists()
    assert "없습니다" in capsys.readouterr().err


def test_표_출력은_JSON_없이도_0건일때_안내문을_보여준다(tmp_path: Path, capsys) -> None:
    db_path = tmp_path / "storage.db"
    with db.connect(db_path) as conn:
        report_store.save(
            conn,
            "legacy-only",
            "CORP-999",
            "",
            Report(
                company="옛보고서",
                job="",
                corp_type="상장사",
                grade=Grade.PARTIAL,
                sections=[],
                generated_at="2026-08-01T00:00:00",
            ),
        )

    exit_code = quality_observations.main(["--db", str(db_path)])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "quality_observation 있는 보고서(아래 집계 대상): 0건" in out
    assert "이 CLI의 결함이 아닙니다" in out
