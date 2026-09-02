"""P11~P25 무과금 shadow 저장소가 유료 P01~P10 원장과 분리됐는지 확인한다.

★ 이 시험은 생산 임계값(40·0.50·8)을 import해 비교하지 않는다. 관측값은
  생산 평가기(`observe_generation`)가 만든 것을 그대로 저장했다가 다시 읽어
  「저장한 값과 읽은 값이 같은가」만 단정한다.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from src.features.pilot_evaluation.manifest import (
    APPROVED_PAID_CASE_IDS,
    CANONICAL_PILOT_CASES,
)
from src.features.pilot_evaluation.quality_store import QUALITY_FILENAME
from src.features.pilot_evaluation.schema import (
    PILOT_BINDING_SCHEMA_VERSION,
    PILOT_BINDING_TABLE,
    ensure_schema,
)
from src.features.pilot_evaluation.shadow_store import (
    SHADOW_CASE_IDS,
    SHADOW_OBSERVATION_TABLE,
    PilotShadowObservationStore,
    ShadowStoreError,
)
from src.shared.report_quality.dto import (
    ClaimFact,
    ReportCandidate,
    ReportSectionCandidate,
    SourceDocument,
)
from src.shared.report_quality.generation import (
    GenerationQualityObservation,
    observe_generation,
)


_RECORDED_AT = "2026-09-02T00:00:00Z"


def _observation(*, verified: bool) -> GenerationQualityObservation:
    """생산 평가기로 관측값 하나를 만든다. 회사 자료를 흉내내지 않는 합성 입력이다."""

    candidate = ReportCandidate(
        sections=(
            ReportSectionCandidate(
                section_id="identity",
                fact_ids=("synthetic-fact-1", "synthetic-fact-2"),
            ),
        ),
        facts=(
            ClaimFact(
                fact_id="synthetic-fact-1",
                section_owner="identity",
                source_id="synthetic-source",
                source_identity="합성 문서",
                verification_state="verified",
                claim_slot="identity:corporate_identity",
                evidence_binding_valid=True,
                claim="합성 시험 문장 가",
            ),
            ClaimFact(
                fact_id="synthetic-fact-2",
                section_owner="identity",
                source_id="synthetic-source",
                source_identity="합성 문서",
                verification_state="verified" if verified else "unverified",
                claim_slot="identity:business_definition",
                evidence_binding_valid=True,
                claim="합성 시험 문장 나",
            ),
        ),
        sources=(
            SourceDocument(source_id="synthetic-source", document_identity="합성 문서"),
        ),
    )
    return observe_generation(candidate)


def _binding_row(conn: sqlite3.Connection) -> tuple[object, ...] | None:
    return conn.execute(
        f"SELECT pilot_key, schema_version, binding_id, manifest_sha256, origin, "
        f"server_instance_sha256, data_path_sha256, checkpoint_path_sha256, "
        f"checkpoint_content_sha256, created_at FROM {PILOT_BINDING_TABLE}"
    ).fetchone()


def _seed_paid_binding(db_path: Path) -> tuple[object, ...]:
    """유료 파일럿 원장(P01~P10 결속)을 같은 DB 파일에 먼저 만들어 둔다."""

    with sqlite3.connect(db_path) as conn:
        ensure_schema(conn)
        conn.execute(
            f"INSERT INTO {PILOT_BINDING_TABLE} ("
            "pilot_key, schema_version, binding_id, manifest_sha256, origin, "
            "server_instance_sha256, data_path_sha256, checkpoint_path_sha256, "
            "checkpoint_content_sha256, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                "g3.5-canonical-pilot25",
                PILOT_BINDING_SCHEMA_VERSION,
                "binding-for-paid-cases",
                "0" * 64,
                "https://example.invalid",
                "1" * 64,
                "2" * 64,
                "3" * 64,
                "4" * 64,
                _RECORDED_AT,
            ),
        )
        conn.commit()
        row = _binding_row(conn)
    assert row is not None
    return row


def test_shadow_store는_quality_store와_분리된다(tmp_path: Path) -> None:
    db_path = tmp_path / "pilot.sqlite3"
    before = _seed_paid_binding(db_path)

    store = PilotShadowObservationStore(db_path)
    store.ensure_schema()
    store.record(
        case_id="P11",
        fixture_ref="synthetic-fixture-a",
        recorded_at=_RECORDED_AT,
        observation=_observation(verified=True),
    )
    store.record(
        case_id="P12",
        fixture_ref="synthetic-fixture-b",
        recorded_at=_RECORDED_AT,
        observation=_observation(verified=False),
    )

    assert SHADOW_OBSERVATION_TABLE != PILOT_BINDING_TABLE
    with sqlite3.connect(db_path) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        # 유료 원장 행은 shadow 기록 뒤에도 한 글자도 바뀌지 않는다.
        assert _binding_row(conn) == before
        assert (
            conn.execute(f"SELECT COUNT(*) FROM {PILOT_BINDING_TABLE}").fetchone()[0]
            == 1
        )
        # shadow 표에는 유료 결속 열이 없다.
        shadow_columns = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({SHADOW_OBSERVATION_TABLE})")
        }
    assert {PILOT_BINDING_TABLE, SHADOW_OBSERVATION_TABLE} <= tables
    assert "pilot_key" not in shadow_columns
    assert "checkpoint_content_sha256" not in shadow_columns

    # shadow 저장소는 유료 원장 행을 하나도 못 본다.
    assert tuple(record.case_id for record in store.read_all()) == ("P11", "P12")
    # 유료 품질판정 JSON을 만들지도 건드리지도 않는다.
    assert list(tmp_path.glob(QUALITY_FILENAME)) == []
    assert list(tmp_path.glob("*.lock")) == []


def test_P01_P10_id는_shadow_store가_거절한다(tmp_path: Path) -> None:
    store = PilotShadowObservationStore(tmp_path / "shadow.sqlite3")
    store.ensure_schema()
    observation = _observation(verified=True)

    for paid_case_id in sorted(APPROVED_PAID_CASE_IDS):
        with pytest.raises(ShadowStoreError):
            store.record(
                case_id=paid_case_id,
                fixture_ref="synthetic-fixture",
                recorded_at=_RECORDED_AT,
                observation=observation,
            )

    with pytest.raises(ShadowStoreError):
        store.record(
            case_id="P99",
            fixture_ref="synthetic-fixture",
            recorded_at=_RECORDED_AT,
            observation=observation,
        )

    assert store.read_all() == ()


def test_shadow_후보_목록은_manifest에서_P01_P10을_뺀_나머지다() -> None:
    expected = tuple(
        case.case_id
        for case in CANONICAL_PILOT_CASES
        if case.case_id not in APPROVED_PAID_CASE_IDS
    )

    assert SHADOW_CASE_IDS == expected
    assert SHADOW_CASE_IDS[0] == "P11"
    assert SHADOW_CASE_IDS[-1] == "P25"
    assert len(SHADOW_CASE_IDS) == 15


def test_저장한_관측값은_한_글자도_바뀌지_않고_돌아온다(tmp_path: Path) -> None:
    store = PilotShadowObservationStore(tmp_path / "shadow.sqlite3")
    store.ensure_schema()
    observation = _observation(verified=False)

    store.record(
        case_id="P13",
        fixture_ref="synthetic-fixture",
        recorded_at=_RECORDED_AT,
        observation=observation,
    )
    (record,) = store.read_all()

    assert record.observation == observation
    assert record.case_id == "P13"
    assert record.fixture_ref == "synthetic-fixture"
    assert record.recorded_at == _RECORDED_AT
    # 회사 유형은 입력이 아니라 manifest에서 파생한다 — 호출자가 지어낼 수 없다.
    assert record.category == "unlisted_disclosure"


def test_읽기_순서는_case_id_오름차순으로_결정론적이다(tmp_path: Path) -> None:
    store = PilotShadowObservationStore(tmp_path / "shadow.sqlite3")
    store.ensure_schema()
    observation = _observation(verified=True)
    for case_id in ("P22", "P11", "P19"):
        store.record(
            case_id=case_id,
            fixture_ref=f"fixture-{case_id}",
            recorded_at=_RECORDED_AT,
            observation=observation,
        )

    assert tuple(record.case_id for record in store.read_all()) == (
        "P11",
        "P19",
        "P22",
    )
    assert store.observed_case_ids() == frozenset({"P11", "P19", "P22"})


def test_같은_후보를_두_번_기록하면_거절한다(tmp_path: Path) -> None:
    store = PilotShadowObservationStore(tmp_path / "shadow.sqlite3")
    store.ensure_schema()
    observation = _observation(verified=True)
    store.record(
        case_id="P11",
        fixture_ref="fixture-1",
        recorded_at=_RECORDED_AT,
        observation=observation,
    )

    with pytest.raises(ShadowStoreError):
        store.record(
            case_id="P11",
            fixture_ref="fixture-2",
            recorded_at=_RECORDED_AT,
            observation=observation,
        )

    assert tuple(record.fixture_ref for record in store.read_all()) == ("fixture-1",)


@pytest.mark.parametrize(
    "fixture_ref, recorded_at",
    [
        ("", _RECORDED_AT),
        ("   ", _RECORDED_AT),
        ("fixture", ""),
        ("fixture", "2026-09-02"),
        ("fixture", "2026-09-02T00:00:00+09:00"),
        ("fixture", "2026-13-02T00:00:00Z"),
    ],
)
def test_출처_표시와_기록시각이_없거나_형식이_다르면_거절한다(
    tmp_path: Path, fixture_ref: str, recorded_at: str
) -> None:
    store = PilotShadowObservationStore(tmp_path / "shadow.sqlite3")
    store.ensure_schema()

    with pytest.raises(ShadowStoreError):
        store.record(
            case_id="P11",
            fixture_ref=fixture_ref,
            recorded_at=recorded_at,
            observation=_observation(verified=True),
        )

    assert store.read_all() == ()


def test_관측값이_아닌_값은_거절한다(tmp_path: Path) -> None:
    store = PilotShadowObservationStore(tmp_path / "shadow.sqlite3")
    store.ensure_schema()

    with pytest.raises(ShadowStoreError):
        store.record(
            case_id="P11",
            fixture_ref="fixture",
            recorded_at=_RECORDED_AT,
            observation={"release_allowed": True},  # type: ignore[arg-type]
        )

    assert store.read_all() == ()


def test_저장된_회사유형이_manifest와_다르면_읽기에서_거절한다(tmp_path: Path) -> None:
    db_path = tmp_path / "shadow.sqlite3"
    store = PilotShadowObservationStore(db_path)
    store.ensure_schema()
    store.record(
        case_id="P11",
        fixture_ref="fixture",
        recorded_at=_RECORDED_AT,
        observation=_observation(verified=True),
    )

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"UPDATE {SHADOW_OBSERVATION_TABLE} SET category=? WHERE case_id=?",
            ("listed", "P11"),
        )
        conn.commit()

    with pytest.raises(ShadowStoreError):
        store.read_all()
