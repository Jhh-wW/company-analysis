"""P11~P25 무과금 shadow 관측값만 담는, 유료 품질 원장과 분리된 저장소.

★ 왜 별도 저장소인가 — `quality_store.py`는 사용자가 실제로 돈을 내고 돌린
  P01~P10의 사람 품질판정을 봉인된 배치(checkpoint + SQLite binding)에 결속해
  담는다. 이 파일이 담는 값은 성격이 완전히 다르다: 돈을 쓰지 않고, 이미
  저장된 자료만 다시 세어 본 «관찰» 결과다. 둘을 같은 저장물에 섞으면 유료
  원장의 건수·판정이 관찰 작업 때문에 달라 보일 수 있다.

★ 왜 DB 표가 아니라 JSON 파일인가 — 이 저장물은 오프라인 평가용이며 운영
  DB에 남으면 안 된다. `src/core/persistent_schema.py` registry는 «운영 DB에
  영속되는» bootstrap만 담고, 운영 코드가 표를 만드는 SQL을 담고 있으면 반드시
  그 registry(또는 base bootstrap)에 등록돼 있어야 한다
  (`core/tests/test_persistent_schema.py`). 이 관찰 표를 거기에 등록하면 운영
  DB에 평가 전용 표가 생기므로, 표를 만들지 않고 파일 한 개로 담는다.

★ 이 모듈은 P01~P10 case ID를 «거절»한다. 실수로 유료 결과를 여기에 적어
  두 벌의 진실이 생기는 일을 코드로 막는다. 저장 파일 이름도 고정이라
  유료 품질판정 JSON을 덮어쓸 수 없다.

★ AI·DART·네트워크를 부르지 않는다. import도 하지 않는다.

⚠️ 한계 — 여러 프로세스가 동시에 `record`하는 상황은 가정하지 않는다(잠금
   없음). 오프라인에서 한 사람이 한 번 돌리는 관찰 도구다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Mapping

from src.core.persisted_json import (
    PersistedJsonContractError,
    validate_persisted_json_text,
)
from src.features.pilot_evaluation.manifest import (
    APPROVED_PAID_CASE_IDS,
    CANONICAL_PILOT_CASES,
)
from src.shared.report_quality.generation import (
    GenerationQualityObservation,
    generation_quality_observation_from_dict,
    generation_quality_observation_to_dict,
)


SHADOW_STORE_SCHEMA_VERSION: Final[int] = 1
#: 유료 품질판정 JSON(`canonical-pilot25-quality.json`)과 겹치지 않는 고정 이름.
#: 저장소는 폴더만 받고 파일 이름은 항상 이 값이라, 유료 저장물을 덮어쓸 수 없다.
SHADOW_STORE_FILENAME: Final[str] = "canonical-pilot25-shadow-observations.json"
SHADOW_STORE_KIND: Final[str] = "pilot-shadow-quality-observations"

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
_STORE_TOP_FIELDS: Final[frozenset[str]] = frozenset(
    {"schema_version", "store_kind", "cases"}
)
_CASE_FIELDS: Final[frozenset[str]] = frozenset(
    {"case_id", "category", "fixture_ref", "recorded_at", "observation"}
)


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


def _validated_observation(observation: object) -> GenerationQualityObservation:
    if type(observation) is not GenerationQualityObservation:
        raise ShadowStoreError(
            "정확한 GenerationQualityObservation이 필요합니다 — 문자열에서 판정을 "
            "다시 만들지 않습니다"
        )
    return observation


def _record_to_wire(record: ShadowObservationRecord) -> dict[str, Any]:
    return {
        "case_id": record.case_id,
        "category": record.category,
        "fixture_ref": record.fixture_ref,
        "recorded_at": record.recorded_at,
        "observation": generation_quality_observation_to_dict(record.observation),
    }


def _record_from_wire(data: object) -> ShadowObservationRecord:
    if type(data) is not dict or set(data) != _CASE_FIELDS:
        raise ShadowStoreError("shadow 관측 항목의 key 또는 형식이 다릅니다")
    case_id = _validated_case_id(data["case_id"])
    expected_category = SHADOW_CATEGORY_BY_CASE_ID[case_id]
    if data["category"] != expected_category:
        raise ShadowStoreError(
            f"{case_id} 회사 유형이 manifest와 다릅니다: {data['category']!r}"
        )
    try:
        observation = generation_quality_observation_from_dict(data["observation"])
    except (TypeError, ValueError) as error:
        raise ShadowStoreError(
            f"{case_id} 관측값을 원본 그대로 복원하지 못했습니다"
        ) from error
    return ShadowObservationRecord(
        case_id=case_id,
        category=expected_category,
        fixture_ref=_validated_fixture_ref(data["fixture_ref"]),
        recorded_at=_validated_recorded_at(data["recorded_at"]),
        observation=observation,
    )


class PilotShadowObservationStore:
    """P11~P25 관측값만 담는 JSON 파일. 유료 저장물을 읽지도 쓰지도 않는다.

    같은 폴더에 유료 품질판정 JSON이나 평가 SQLite가 이미 있어도 상관없다 —
    이 저장소는 자기 파일(`SHADOW_STORE_FILENAME`) 밖의 어떤 경로도 열지 않는다.
    """

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        #: 저장 파일 경로. 이름이 고정이라 호출자가 다른 파일을 가리킬 수 없다.
        self.path = self.directory / SHADOW_STORE_FILENAME

    def ensure_store(self) -> None:
        """빈 저장소 파일을 만든다. 이미 있으면 그대로 둔다."""

        if self.path.exists():
            self._validated_existing_path()
            return
        self._write(())

    def _validated_existing_path(self) -> None:
        if self.path.is_symlink() or not self.path.is_file():
            raise ShadowStoreError("shadow 저장 파일이 일반 파일이 아닙니다")

    def _write(self, records: tuple[ShadowObservationRecord, ...]) -> None:
        payload = {
            "schema_version": SHADOW_STORE_SCHEMA_VERSION,
            "store_kind": SHADOW_STORE_KIND,
            "cases": [
                _record_to_wire(record)
                for record in sorted(records, key=lambda item: item.case_id)
            ],
        }
        text = json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        try:
            validate_persisted_json_text(text)
        except PersistedJsonContractError as error:
            raise ShadowStoreError(
                "shadow 관측 저장물이 공통 JSON 자원 상한을 넘었습니다"
            ) from error
        self.directory.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(text, encoding="utf-8")
            os.replace(temporary, self.path)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise ShadowStoreError("shadow 관측값을 저장하지 못했습니다") from error

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
            ShadowStoreError: 계약 위반, 중복 기록, 저장 실패.
        """

        validated_case_id = _validated_case_id(case_id)
        record = ShadowObservationRecord(
            case_id=validated_case_id,
            category=SHADOW_CATEGORY_BY_CASE_ID[validated_case_id],
            fixture_ref=_validated_fixture_ref(fixture_ref),
            recorded_at=_validated_recorded_at(recorded_at),
            observation=_validated_observation(observation),
        )
        existing = self.read_all()
        if any(item.case_id == record.case_id for item in existing):
            raise ShadowStoreError(
                f"{record.case_id} 관측값이 이미 있습니다. 덮어쓰지 않습니다"
            )
        self._write(existing + (record,))
        return record

    def read_all(self) -> tuple[ShadowObservationRecord, ...]:
        """저장된 관측값을 case ID 오름차순으로 읽는다(결정론적).

        저장 파일이 아직 없으면 빈 tuple이다 — 「없는 자료」를 0건으로 센다.

        Raises:
            ShadowStoreError: 저장본이 손상됐거나 manifest 회사 유형과 다를 때.
        """

        if not self.path.exists():
            return ()
        self._validated_existing_path()
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as error:
            raise ShadowStoreError("shadow 관측값을 읽지 못했습니다") from error
        try:
            payload = validate_persisted_json_text(text)
        except PersistedJsonContractError as error:
            raise ShadowStoreError("shadow 관측 저장물이 손상됐습니다") from error
        if type(payload) is not dict or set(payload) != _STORE_TOP_FIELDS:
            raise ShadowStoreError("shadow 저장물의 최상위 key가 다릅니다")
        if payload["schema_version"] != SHADOW_STORE_SCHEMA_VERSION:
            raise ShadowStoreError(
                f"shadow 저장물의 스키마 판이 다릅니다: {payload['schema_version']!r}"
            )
        if payload["store_kind"] != SHADOW_STORE_KIND:
            raise ShadowStoreError(
                f"shadow 저장물이 아닙니다: {payload['store_kind']!r}"
            )
        cases = payload["cases"]
        if type(cases) is not list:
            raise ShadowStoreError("shadow 저장물의 관측 목록이 배열이 아닙니다")
        records = tuple(_record_from_wire(item) for item in cases)
        if len({record.case_id for record in records}) != len(records):
            raise ShadowStoreError("shadow 저장물에 같은 후보가 두 번 있습니다")
        return tuple(sorted(records, key=lambda item: item.case_id))

    def observed_case_ids(self) -> frozenset[str]:
        """관측값이 저장된 후보 ID 집합."""

        return frozenset(record.case_id for record in self.read_all())


__all__ = [
    "PilotShadowObservationStore",
    "SHADOW_CASE_IDS",
    "SHADOW_CATEGORY_BY_CASE_ID",
    "SHADOW_STORE_FILENAME",
    "SHADOW_STORE_KIND",
    "SHADOW_STORE_SCHEMA_VERSION",
    "ShadowObservationRecord",
    "ShadowStoreError",
]
