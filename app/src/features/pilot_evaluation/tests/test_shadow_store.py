"""P11~P25 무과금 shadow 저장소가 유료 P01~P10 원장과 분리됐는지 확인한다.

★ 이 시험은 생산 임계값(40·0.50·8)을 import해 비교하지 않는다. 관측값은
  생산 평가기(`observe_generation`)가 만든 것을 그대로 저장했다가 다시 읽어
  「저장한 값과 읽은 값이 같은가」만 단정한다.
"""

from __future__ import annotations

import json
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
    SHADOW_STORE_FILENAME,
    SHADOW_STORE_KIND,
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
_PAID_SQLITE_FILENAME = "canonical-pilot25.sqlite3"


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


def _seed_paid_artifacts(directory: Path) -> dict[Path, bytes]:
    """유료 파일럿 저장물(SQLite 결속 원장 + 품질판정 JSON)을 같은 폴더에 둔다."""

    db_path = directory / _PAID_SQLITE_FILENAME
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

    quality_path = directory / QUALITY_FILENAME
    quality_path.write_text(
        json.dumps({"schema_version": 4, "cases": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    return {path: path.read_bytes() for path in (db_path, quality_path)}


def test_shadow_store는_quality_store와_분리된다(tmp_path: Path) -> None:
    before = _seed_paid_artifacts(tmp_path)

    store = PilotShadowObservationStore(tmp_path)
    store.ensure_store()
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

    # 유료 저장물 두 개는 shadow 기록 뒤에도 한 바이트도 바뀌지 않는다.
    assert {path: path.read_bytes() for path in before} == before
    # 유료 결속 원장의 행도 그대로다.
    with sqlite3.connect(tmp_path / _PAID_SQLITE_FILENAME) as conn:
        assert (
            conn.execute(f"SELECT COUNT(*) FROM {PILOT_BINDING_TABLE}").fetchone()[0]
            == 1
        )
    # shadow 저장 파일은 세 번째 파일이며 이름이 유료 저장물과 겹치지 않는다.
    assert SHADOW_STORE_FILENAME != QUALITY_FILENAME
    assert store.path == tmp_path / SHADOW_STORE_FILENAME
    assert store.path.is_file()
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    assert payload["store_kind"] == SHADOW_STORE_KIND
    assert [case["case_id"] for case in payload["cases"]] == ["P11", "P12"]
    # shadow 저장소는 유료 저장물의 내용을 하나도 못 본다.
    assert tuple(record.case_id for record in store.read_all()) == ("P11", "P12")
    assert sorted(path.name for path in tmp_path.iterdir()) == sorted(
        (_PAID_SQLITE_FILENAME, QUALITY_FILENAME, SHADOW_STORE_FILENAME)
    )


def test_shadow_store는_운영_영속_스키마를_만들지_않는다() -> None:
    """운영 DB에 평가 전용 표가 생기지 않게 CREATE TABLE 자체를 두지 않는다.

    `core/tests/test_persistent_schema.py`는 운영 코드의 모든 `CREATE TABLE`이
    영속 schema registry에 등록돼 있기를 요구한다. 이 관찰 저장소는 운영 DB에
    남으면 안 되므로 registry에 넣지 않고, 대신 표를 만들지 않는다.
    """

    source = (
        Path(__file__).resolve().parents[1] / "shadow_store.py"
    ).read_text(encoding="utf-8")

    assert "CREATE TABLE" not in source
    assert "sqlite3" not in source


def test_P01_P10_id는_shadow_store가_거절한다(tmp_path: Path) -> None:
    store = PilotShadowObservationStore(tmp_path)
    store.ensure_store()
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
    store = PilotShadowObservationStore(tmp_path)
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


def test_저장_파일이_없으면_0건으로_센다(tmp_path: Path) -> None:
    store = PilotShadowObservationStore(tmp_path / "아직없는폴더")

    assert store.read_all() == ()
    assert store.observed_case_ids() == frozenset()
    assert not store.path.exists()


def test_읽기_순서는_case_id_오름차순으로_결정론적이다(tmp_path: Path) -> None:
    store = PilotShadowObservationStore(tmp_path)
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
    # 저장 바이트도 순서에 무관하게 같다.
    other = PilotShadowObservationStore(tmp_path / "other")
    for case_id in ("P19", "P22", "P11"):
        other.record(
            case_id=case_id,
            fixture_ref=f"fixture-{case_id}",
            recorded_at=_RECORDED_AT,
            observation=observation,
        )
    assert other.path.read_bytes() == store.path.read_bytes()


def test_같은_후보를_두_번_기록하면_거절한다(tmp_path: Path) -> None:
    store = PilotShadowObservationStore(tmp_path)
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
    store = PilotShadowObservationStore(tmp_path)

    with pytest.raises(ShadowStoreError):
        store.record(
            case_id="P11",
            fixture_ref=fixture_ref,
            recorded_at=recorded_at,
            observation=_observation(verified=True),
        )

    assert store.read_all() == ()


def test_관측값이_아닌_값은_거절한다(tmp_path: Path) -> None:
    store = PilotShadowObservationStore(tmp_path)

    with pytest.raises(ShadowStoreError):
        store.record(
            case_id="P11",
            fixture_ref="fixture",
            recorded_at=_RECORDED_AT,
            observation={"release_allowed": True},  # type: ignore[arg-type]
        )

    assert store.read_all() == ()


def test_저장된_회사유형이_manifest와_다르면_읽기에서_거절한다(tmp_path: Path) -> None:
    store = PilotShadowObservationStore(tmp_path)
    store.record(
        case_id="P11",
        fixture_ref="fixture",
        recorded_at=_RECORDED_AT,
        observation=_observation(verified=True),
    )
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    payload["cases"][0]["category"] = "listed"
    store.path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ShadowStoreError):
        store.read_all()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.__setitem__("store_kind", "다른-저장물"),
        lambda payload: payload.__setitem__("schema_version", 2),
        lambda payload: payload.__setitem__("cases", {}),
        lambda payload: payload.pop("store_kind"),
        lambda payload: payload.__setitem__("추가키", 1),
    ],
)
def test_다른_저장물이거나_형식이_다르면_읽기에서_거절한다(
    tmp_path: Path, mutate
) -> None:
    store = PilotShadowObservationStore(tmp_path)
    store.record(
        case_id="P11",
        fixture_ref="fixture",
        recorded_at=_RECORDED_AT,
        observation=_observation(verified=True),
    )
    payload = json.loads(store.path.read_text(encoding="utf-8"))
    mutate(payload)
    store.path.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    with pytest.raises(ShadowStoreError):
        store.read_all()
