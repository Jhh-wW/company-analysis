"""P11~P25 무과금 shadow 관측값만 담는, 유료 품질 원장과 분리된 저장소.

★ 왜 별도 저장소인가 — `quality_store.py`는 사용자가 실제로 돈을 내고 돌린
  P01~P10의 사람 품질판정을 봉인된 배치(checkpoint + SQLite binding)에 결속해
  담는다. 이 파일이 담는 값은 성격이 완전히 다르다: 돈을 쓰지 않고, 이미
  저장된 자료만 다시 세어 본 «관찰» 결과다. 둘을 같은 표에 섞으면 유료 원장의
  행 수·판정이 관찰 작업 때문에 달라 보일 수 있다. 그래서 표 이름부터 다르게
  두고, 이 모듈은 `quality_store`의 어떤 함수도 부르지 않는다.

★ 이 모듈은 P01~P10 case ID를 «거절»한다. 실수로 유료 결과를 여기에 적어
  두 벌의 진실이 생기는 일을 코드로 막는다.

★ AI·DART·네트워크를 부르지 않는다. import도 하지 않는다.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final, Mapping

from src.features.pilot_evaluation.manifest import (
    APPROVED_PAID_CASE_IDS,
    CANONICAL_PILOT_CASES,
)
from src.shared.report_quality.generation import (
    GenerationQualityObservation,
    generation_quality_observation_from_dict,
    generation_quality_observation_to_dict,
)


SHADOW_SCHEMA_VERSION: Final[int] = 1
#: 유료 원장 표(`canonical_pilot25_bindings`·`pdf_automatic_release_records`)와
#: 이름이 겹치지 않게 접두사를 명시한다.
SHADOW_OBSERVATION_TABLE: Final[str] = "pilot_shadow_quality_observations"

#: 승인된 유료 실행(P01~P10)을 뺀 나머지 파일럿 후보. manifest가 정본이므로
#: 여기서 ID를 다시 나열하지 않는다.
SHADOW_CASE_IDS: Final[tuple[str, ...]] = tuple(
    case.case_id
    for case in CANONICAL_PILOT_CASES
    if case.case_id not in APPROVED_PAID_CASE_IDS
)
#: case ID → 회사 유형. 호출자가 유형을 지어내지 못하게 저장 시각에 파생한다.
SHADOW_CATEGORY_BY_CASE_ID: Final[Mapping[str, str]] = {
    case.case_id: case.category.value
    for case in CANONICAL_PILOT_CASES
    if case.case_id in frozenset(SHADOW_CASE_IDS)
}

_RECORDED_AT_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%SZ"

_CREATE_SHADOW_TABLE_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {SHADOW_OBSERVATION_TABLE} (
    case_id          TEXT PRIMARY KEY,
    schema_version   INTEGER NOT NULL,
    category         TEXT NOT NULL,
    fixture_ref      TEXT NOT NULL,
    recorded_at      TEXT NOT NULL,
    observation_json TEXT NOT NULL
)
"""


class ShadowStoreError(RuntimeError):
    """shadow 관측 저장 계약을 어겼다(유료 case ID·형식 위반·중복 기록 등)."""


@dataclass(frozen=True)
class ShadowObservationRecord:
    """P11~P25 후보 한 건에 대해 저장된 무과금 관측값."""

    case_id: str
    #: `PilotCategory` 값. manifest에서 파생하며 호출자가 정하지 않는다.
    category: str
    #: 이 관측값이 어떤 저장 자료에서 나왔는지 가리키는 표시(옛 파일럿 실행 ID 등).
    fixture_ref: str
    #: 관측을 «기록한» 시각(UTC, `YYYY-MM-DDTHH:MM:SSZ`). 회사 자료의 시각이 아니다.
    recorded_at: str
    observation: GenerationQualityObservation


def _validated_case_id(case_id: object) -> str:
    if type(case_id) is not str:
        raise ShadowStoreError(f"case ID는 문자열이어야 합니다: {case_id!r}")
    if case_id in APPROVED_PAID_CASE_IDS:
        raise ShadowStoreError(
            f"{case_id}는 유료 실행 후보입니다. 유료 결과는 quality_store가 담습니다"
        )
    if case_id not in SHADOW_CATEGORY_BY_CASE_ID:
        raise ShadowStoreError(f"P11~P25 파일럿 후보가 아닙니다: {case_id}")
    return case_id


def _validated_fixture_ref(fixture_ref: object) -> str:
    if type(fixture_ref) is not str or not fixture_ref.strip():
        raise ShadowStoreError("관측값의 출처 표시(fixture_ref)가 비었습니다")
    if fixture_ref != fixture_ref.strip():
        raise ShadowStoreError("관측값의 출처 표시 앞뒤에 공백이 있습니다")
    return fixture_ref


def _validated_recorded_at(recorded_at: object) -> str:
    if type(recorded_at) is not str:
        raise ShadowStoreError(f"기록 시각은 문자열이어야 합니다: {recorded_at!r}")
    try:
        datetime.strptime(recorded_at, _RECORDED_AT_FORMAT)
    except ValueError as error:
        raise ShadowStoreError(
            "기록 시각은 UTC 고정 형식(YYYY-MM-DDTHH:MM:SSZ)이어야 합니다"
        ) from error
    return recorded_at


def _validated_observation(
    observation: object,
) -> GenerationQualityObservation:
    if type(observation) is not GenerationQualityObservation:
        raise ShadowStoreError(
            "정확한 GenerationQualityObservation이 필요합니다 — 문자열에서 판정을 "
            "다시 만들지 않습니다"
        )
    return observation


class PilotShadowObservationStore:
    """P11~P25 관측값만 담는 SQLite 표. 유료 원장 표를 읽지도 쓰지도 않는다.

    같은 SQLite 파일 안에 유료 결속 표가 이미 있어도 상관없다 — 이 저장소는
    자기 표(`SHADOW_OBSERVATION_TABLE`) 밖의 어떤 표에도 SQL을 보내지 않는다.
    """

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)

    def _open(self) -> sqlite3.Connection:
        try:
            return sqlite3.connect(self.db_path, timeout=2.0)
        except sqlite3.Error as error:
            raise ShadowStoreError("shadow 저장소를 열지 못했습니다") from error

    def ensure_schema(self) -> None:
        """shadow 표만 만든다. 이미 있으면 그대로 둔다."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with closing(self._open()) as conn:
                conn.execute(_CREATE_SHADOW_TABLE_SQL)
                conn.commit()
        except sqlite3.Error as error:
            raise ShadowStoreError("shadow 표를 만들지 못했습니다") from error

    def record(
        self,
        *,
        case_id: str,
        fixture_ref: str,
        recorded_at: str,
        observation: GenerationQualityObservation,
    ) -> ShadowObservationRecord:
        """관측값 한 건을 기록한다. 같은 후보를 두 번 기록하면 거절한다.

        Args:
            case_id: P11~P25 중 하나. P01~P10과 목록 밖 ID는 거절한다.
            fixture_ref: 이 관측값이 나온 저장 자료 표시(빈 값 불가).
            recorded_at: 기록 시각(UTC, `YYYY-MM-DDTHH:MM:SSZ`).
            observation: 생산 평가기가 이미 만든 관측값. 여기서 다시 판정하지
                않고 wire 형식 그대로 저장한다.

        Returns:
            저장된 그대로의 `ShadowObservationRecord`.

        Raises:
            ShadowStoreError: 계약 위반 또는 SQLite 오류.
        """

        validated_case_id = _validated_case_id(case_id)
        record = ShadowObservationRecord(
            case_id=validated_case_id,
            category=SHADOW_CATEGORY_BY_CASE_ID[validated_case_id],
            fixture_ref=_validated_fixture_ref(fixture_ref),
            recorded_at=_validated_recorded_at(recorded_at),
            observation=_validated_observation(observation),
        )
        payload = json.dumps(
            generation_quality_observation_to_dict(record.observation),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        try:
            with closing(self._open()) as conn:
                conn.execute(_CREATE_SHADOW_TABLE_SQL)
                conn.execute(
                    f"INSERT INTO {SHADOW_OBSERVATION_TABLE} ("
                    "case_id, schema_version, category, fixture_ref, recorded_at, "
                    "observation_json) VALUES (?,?,?,?,?,?)",
                    (
                        record.case_id,
                        SHADOW_SCHEMA_VERSION,
                        record.category,
                        record.fixture_ref,
                        record.recorded_at,
                        payload,
                    ),
                )
                conn.commit()
        except sqlite3.IntegrityError as error:
            raise ShadowStoreError(
                f"{record.case_id} 관측값이 이미 있습니다. 덮어쓰지 않습니다"
            ) from error
        except sqlite3.Error as error:
            raise ShadowStoreError("shadow 관측값을 저장하지 못했습니다") from error
        return record

    def read_all(self) -> tuple[ShadowObservationRecord, ...]:
        """저장된 관측값을 case ID 오름차순으로 읽는다(결정론적).

        Raises:
            ShadowStoreError: 저장본이 손상됐거나 manifest 회사 유형과 다를 때.
        """

        try:
            with closing(self._open()) as conn:
                conn.execute(_CREATE_SHADOW_TABLE_SQL)
                rows = conn.execute(
                    f"SELECT case_id, schema_version, category, fixture_ref, "
                    f"recorded_at, observation_json FROM {SHADOW_OBSERVATION_TABLE} "
                    "ORDER BY case_id ASC"
                ).fetchall()
        except sqlite3.Error as error:
            raise ShadowStoreError("shadow 관측값을 읽지 못했습니다") from error

        records: list[ShadowObservationRecord] = []
        for case_id, schema_version, category, fixture_ref, recorded_at, payload in rows:
            validated_case_id = _validated_case_id(case_id)
            if schema_version != SHADOW_SCHEMA_VERSION:
                raise ShadowStoreError(
                    f"{validated_case_id} 관측값의 스키마 판이 다릅니다: {schema_version!r}"
                )
            expected_category = SHADOW_CATEGORY_BY_CASE_ID[validated_case_id]
            if category != expected_category:
                raise ShadowStoreError(
                    f"{validated_case_id} 회사 유형이 manifest와 다릅니다: {category!r}"
                )
            try:
                data = json.loads(payload)
            except (TypeError, ValueError) as error:
                raise ShadowStoreError(
                    f"{validated_case_id} 관측값 JSON이 손상됐습니다"
                ) from error
            try:
                observation = generation_quality_observation_from_dict(data)
            except (TypeError, ValueError) as error:
                raise ShadowStoreError(
                    f"{validated_case_id} 관측값을 원본 그대로 복원하지 못했습니다"
                ) from error
            records.append(
                ShadowObservationRecord(
                    case_id=validated_case_id,
                    category=expected_category,
                    fixture_ref=_validated_fixture_ref(fixture_ref),
                    recorded_at=_validated_recorded_at(recorded_at),
                    observation=observation,
                )
            )
        return tuple(records)

    def observed_case_ids(self) -> frozenset[str]:
        """관측값이 저장된 후보 ID 집합."""

        return frozenset(record.case_id for record in self.read_all())


__all__ = [
    "PilotShadowObservationStore",
    "SHADOW_CASE_IDS",
    "SHADOW_CATEGORY_BY_CASE_ID",
    "SHADOW_OBSERVATION_TABLE",
    "SHADOW_SCHEMA_VERSION",
    "ShadowObservationRecord",
    "ShadowStoreError",
]
