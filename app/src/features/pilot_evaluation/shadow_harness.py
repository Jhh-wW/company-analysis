"""P11~P25 후보를 저장된 자료만으로 세는 무과금 오프라인 하네스.

★ 무엇을 위한 것인가 — 「FULL 모드에서 정상 회사가 억울하게 거절되는 비율」을
  돈을 쓰지 않고 어림잡기 위한 «관찰» 도구다. 이 결과로 품질 하한(승인된 세
  기준값)을 자동으로 바꾸지 않는다. 사람이 보고 판단할 근거를 만들 뿐이다.

★ AI·DART·네트워크를 부르지 않는다. 그 모듈들을 import조차 하지 않는다.
  P11~P25는 별도 승인 전 유료 실행이 금지돼 있으므로(docs/REVIEW_GUIDE.md §7)
  이 하네스는 «이미 저장된 것»만 읽는다.

★ 없는 자료를 만들지 않는다. 후보 15건은 정확히 세 가지 중 하나로 센다.
  - `SHADOW_MATERIAL_OBSERVED`   저장된 관측값이 있다 → 요약에 들어간다
  - `SHADOW_MATERIAL_UNASSESSABLE` 옛 보고서 자료는 있지만 원자 claim 결속이
    없어 관측값을 복원할 수 없다 → 요약에 넣지 않고 따로 센다
  - `SHADOW_MATERIAL_NONE`       아무 자료도 없다 → 「자료 없음」으로 센다

★ 다시 판정하지 않는다. 등급·출고 가능 여부는 저장된 관측값이 이미 담고 있고,
  최종 게이트 사유는 shared 분류기(`classify_v2_validation_final_gate_reason`)
  하나에만 맡긴다. 이 모듈에 임계값(건수·비율) 비교문은 없다.

★ CLI 진입점을 두지 않는다. 파일을 읽는 함수는
  :func:`load_legacy_pilot_fixture_index` 하나뿐이고 나머지는 순수 함수다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Iterable, Mapping

from src.features.pilot_evaluation.manifest import (
    APPROVED_PAID_CASE_IDS,
    CANONICAL_PILOT_CASES,
)
from src.features.pilot_evaluation.shadow_store import (
    SHADOW_CASE_IDS,
    SHADOW_CATEGORY_BY_CASE_ID,
    ShadowObservationRecord,
)
from src.shared.final_gate_diagnostics import (
    QUALITY_FLOOR_PROBLEM_CODES,
    classify_v2_validation_final_gate_reason,
)
from src.shared.report_quality.generation import GenerationQualityObservation
from src.shared.report_quality.observation_summary import (
    ObservationRow,
    QualityObservationSummary,
    summarize_quality_observations,
)


#: 저장된 관측값이 있어 요약에 들어가는 후보.
SHADOW_MATERIAL_OBSERVED: Final[str] = "관측값_있음"
#: 옛 자료는 있지만 관측값을 복원할 수 없는 후보. 요약에 넣지 않는다.
SHADOW_MATERIAL_UNASSESSABLE: Final[str] = "옛자료_판정불가"
#: 저장된 자료가 아무것도 없는 후보.
SHADOW_MATERIAL_NONE: Final[str] = "자료_없음"

_SHADOW_CASE_ID_SET: Final[frozenset[str]] = frozenset(SHADOW_CASE_IDS)
#: case ID → 정본 법인명. 요약기의 `company_id` 칸에 이 값을 투영한다.
SHADOW_LEGAL_NAME_BY_CASE_ID: Final[Mapping[str, str]] = {
    case.case_id: case.expected_legal_name
    for case in CANONICAL_PILOT_CASES
    if case.case_id in _SHADOW_CASE_ID_SET
}
#: 옛 파일럿 실행 기록의 회사명과 대조할 정본 입력 이름. 정확히 같을 때만 센다.
SHADOW_INPUT_NAME_BY_CASE_ID: Final[Mapping[str, str]] = {
    case.case_id: case.input_name
    for case in CANONICAL_PILOT_CASES
    if case.case_id in _SHADOW_CASE_ID_SET
}


@dataclass(frozen=True)
class ShadowCandidateMaterial:
    """후보 한 건이 어떤 자료를 가졌는지. 판정 결과가 아니라 «자료 유무»다."""

    case_id: str
    category: str
    #: `SHADOW_MATERIAL_*` 세 값 중 하나.
    material_state: str
    #: 그 자료를 가리키는 표시. 자료가 없으면 빈 tuple이다.
    fixture_refs: tuple[str, ...]


@dataclass(frozen=True)
class QualityFloorBreakdown:
    """품질 하한 세 코드의 분포와, 막힌 관측값의 최종 게이트 사유 분포."""

    #: 세 코드를 «항상» 모두 담는다. 0건도 0으로 적어 표 모양이 변하지 않는다.
    quality_floor_code_counts: tuple[tuple[str, int], ...]
    #: 세 코드 중 하나라도 붙은 관측값 수(출고 가능 여부와 무관).
    quality_floor_observation_count: int
    #: 출고가 막힌 관측값에만 shared 분류기를 적용한 사유별 건수. 사유 오름차순.
    blocked_final_gate_reason_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class ShadowCategoryReport:
    """회사 유형 한 가지에 대한 부분 집계."""

    category: str
    candidate_count: int
    observed_count: int
    unassessable_count: int
    no_material_count: int
    summary: QualityObservationSummary
    quality_floor: QualityFloorBreakdown


@dataclass(frozen=True)
class ShadowHarnessReport:
    """P11~P25 전체 관찰 결과. 입력 순서와 무관하게 결정론적이다."""

    candidate_count: int
    observed_count: int
    unassessable_count: int
    no_material_count: int
    summary: QualityObservationSummary
    quality_floor: QualityFloorBreakdown
    #: 회사 유형 오름차순. P11~P25에 실제로 있는 유형만 담는다.
    by_category: tuple[ShadowCategoryReport, ...]
    #: manifest 순서(P11→P25)의 후보별 자료 상태.
    materials: tuple[ShadowCandidateMaterial, ...]


@dataclass(frozen=True)
class LegacyFixtureIndex:
    """옛 파일럿 실행 기록에서 P11~P25와 이름이 정확히 같은 것만 찾은 결과."""

    #: (case_id, 실행 ID 오름차순 tuple) 쌍을 case ID 오름차순으로 담는다.
    refs_by_case_id: tuple[tuple[str, tuple[str, ...]], ...]
    scanned_record_count: int
    matched_record_count: int
    #: P11~P25 어느 후보와도 이름이 같지 않은 기록 수(P01~P10·목록 밖 회사 포함).
    unmatched_record_count: int


def _validated_shadow_case_id(case_id: object, *, label: str) -> str:
    if type(case_id) is not str:
        raise ValueError(f"{label}의 case ID는 문자열이어야 합니다: {case_id!r}")
    if case_id in APPROVED_PAID_CASE_IDS:
        raise ValueError(
            f"{label}에 유료 실행 후보 {case_id}가 있습니다. 이 하네스는 P11~P25만 셉니다"
        )
    if case_id not in _SHADOW_CASE_ID_SET:
        raise ValueError(f"{label}에 P11~P25가 아닌 case ID가 있습니다: {case_id}")
    return case_id


def observation_rows(
    records: Iterable[ShadowObservationRecord],
) -> tuple[ObservationRow, ...]:
    """저장 기록을 shared 요약기 입력 행으로 옮긴다. 값을 만들지 않는다.

    요약기의 네 문자열 칸에 파일럿 후보의 값을 그대로 투영한다.

    - `report_id` ← case ID (P11~P25)
    - `company_id` ← manifest의 정본 법인명
    - `generated_at` ← 관측을 «기록한» 시각 (회사 자료의 시각이 아니다)
    - `release_mode` ← 관측값이 스스로 담고 있는 품질 계약 판(v1 / FULL)

    운영 집계(`admin_dashboard.quality_observations`)와 같은 자료형을 쓰되,
    오프라인 후보에는 운영 `release_mode`가 없으므로 그 칸에는 계약 판을
    넣는다. 없는 값을 지어내지 않기 위한 선택이며, 두 집계를 나란히 볼 때
    이 칸의 뜻이 서로 다르다는 점을 기억해야 한다.

    Raises:
        ValueError: 기록이 `ShadowObservationRecord`가 아니거나 P11~P25 밖일 때.
    """

    rows: list[ObservationRow] = []
    for record in records:
        if type(record) is not ShadowObservationRecord:
            raise ValueError(
                f"정확한 ShadowObservationRecord가 필요합니다: {record!r}"
            )
        case_id = _validated_shadow_case_id(record.case_id, label="관측 기록")
        if type(record.observation) is not GenerationQualityObservation:
            raise ValueError(f"{case_id} 관측값 자료형이 다릅니다")
        rows.append(
            (
                case_id,
                SHADOW_LEGAL_NAME_BY_CASE_ID[case_id],
                record.recorded_at,
                record.observation.contract_version,
                record.observation,
            )
        )
    return tuple(rows)


def summarize_quality_floor(
    observations: Iterable[GenerationQualityObservation],
) -> QualityFloorBreakdown:
    """품질 하한 세 코드의 분포와 막힌 관측값의 게이트 사유를 센다.

    이 함수는 임계값을 다시 비교하지 않는다 — 관측값이 이미 들고 있는
    `quality_problem_codes`·`release_allowed`를 셀 뿐이다. 사유는
    출고가 «막힌» 관측값에만 붙인다. 통과한 보고서에는 최종 게이트 사유가
    존재하지 않으므로, 품질 하한 코드가 붙어 있어도 사유로 세지 않는다.
    """

    floor_codes = tuple(sorted(QUALITY_FLOOR_PROBLEM_CODES))
    code_counts = {code: 0 for code in floor_codes}
    floor_observation_count = 0
    reason_counts: dict[str, int] = {}
    for observation in observations:
        if type(observation) is not GenerationQualityObservation:
            raise ValueError(
                f"정확한 GenerationQualityObservation이 필요합니다: {observation!r}"
            )
        present = set(observation.quality_problem_codes) & QUALITY_FLOOR_PROBLEM_CODES
        for code in present:
            code_counts[code] += 1
        if present:
            floor_observation_count += 1
        if not observation.release_allowed:
            reason = classify_v2_validation_final_gate_reason(
                observation.quality_problem_codes
            )
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
    return QualityFloorBreakdown(
        quality_floor_code_counts=tuple(
            (code, code_counts[code]) for code in floor_codes
        ),
        quality_floor_observation_count=floor_observation_count,
        blocked_final_gate_reason_counts=tuple(
            (reason, reason_counts[reason]) for reason in sorted(reason_counts)
        ),
    )


def _validated_unassessable(
    unassessable_fixture_refs: Mapping[str, tuple[str, ...]] | None,
    *,
    observed_case_ids: frozenset[str],
) -> dict[str, tuple[str, ...]]:
    if unassessable_fixture_refs is None:
        return {}
    validated: dict[str, tuple[str, ...]] = {}
    for case_id, refs in unassessable_fixture_refs.items():
        valid_case_id = _validated_shadow_case_id(case_id, label="판정 불가 자료")
        if valid_case_id in observed_case_ids:
            raise ValueError(
                f"{valid_case_id}는 관측값과 판정 불가 자료에 동시에 있을 수 없습니다"
            )
        refs_tuple = tuple(refs)
        if not refs_tuple or any(
            type(ref) is not str or not ref.strip() for ref in refs_tuple
        ):
            raise ValueError(f"{valid_case_id}의 옛 자료 표시가 비었습니다")
        validated[valid_case_id] = refs_tuple
    return validated


def build_shadow_report(
    records: Iterable[ShadowObservationRecord],
    *,
    unassessable_fixture_refs: Mapping[str, tuple[str, ...]] | None = None,
    top_companies_limit: int = 20,
) -> ShadowHarnessReport:
    """저장된 관측값과 자료 유무를 한 번 세어 관찰 보고를 만든다(순수 함수).

    Args:
        records: shadow 저장소에서 읽은 P11~P25 관측 기록.
        unassessable_fixture_refs: 옛 자료는 있으나 관측값을 복원할 수 없는
            후보의 자료 표시. `records`와 겹칠 수 없다.
        top_companies_limit: 요약기의 상위 회사 목록 길이 상한.

    Returns:
        입력 순서와 무관하게 같은 값이 나오는 `ShadowHarnessReport`.

    Raises:
        ValueError: P01~P10 또는 P11~P25 밖 ID가 섞였거나, 같은 후보가
            두 번 들어왔거나, 관측값과 판정 불가 자료가 겹칠 때.
    """

    materialized = tuple(records)
    rows = observation_rows(materialized)
    observed_case_ids = tuple(row[0] for row in rows)
    if len(set(observed_case_ids)) != len(observed_case_ids):
        raise ValueError("같은 후보의 관측 기록이 두 번 들어왔습니다")
    observed_set = frozenset(observed_case_ids)
    unassessable = _validated_unassessable(
        unassessable_fixture_refs, observed_case_ids=observed_set
    )

    refs_by_observed_case_id = {
        record.case_id: (record.fixture_ref,) for record in materialized
    }
    materials = tuple(
        ShadowCandidateMaterial(
            case_id=case_id,
            category=SHADOW_CATEGORY_BY_CASE_ID[case_id],
            material_state=(
                SHADOW_MATERIAL_OBSERVED
                if case_id in observed_set
                else SHADOW_MATERIAL_UNASSESSABLE
                if case_id in unassessable
                else SHADOW_MATERIAL_NONE
            ),
            fixture_refs=refs_by_observed_case_id.get(
                case_id, unassessable.get(case_id, ())
            ),
        )
        for case_id in SHADOW_CASE_IDS
    )

    observations_by_case_id = {
        record.case_id: record.observation for record in materialized
    }
    by_category = tuple(
        _category_report(
            category=category,
            materials=materials,
            rows=rows,
            observations_by_case_id=observations_by_case_id,
            top_companies_limit=top_companies_limit,
        )
        for category in sorted({material.category for material in materials})
    )

    return ShadowHarnessReport(
        candidate_count=len(SHADOW_CASE_IDS),
        observed_count=len(observed_set),
        unassessable_count=len(unassessable),
        no_material_count=len(SHADOW_CASE_IDS) - len(observed_set) - len(unassessable),
        summary=summarize_quality_observations(
            rows, top_companies_limit=top_companies_limit
        ),
        quality_floor=summarize_quality_floor(
            observations_by_case_id[case_id] for case_id in sorted(observed_set)
        ),
        by_category=by_category,
        materials=materials,
    )


def _category_report(
    *,
    category: str,
    materials: tuple[ShadowCandidateMaterial, ...],
    rows: tuple[ObservationRow, ...],
    observations_by_case_id: Mapping[str, GenerationQualityObservation],
    top_companies_limit: int,
) -> ShadowCategoryReport:
    in_category = tuple(
        material for material in materials if material.category == category
    )
    case_ids = {material.case_id for material in in_category}
    category_rows = tuple(row for row in rows if row[0] in case_ids)
    observed_case_ids = sorted(case_ids & set(observations_by_case_id))
    return ShadowCategoryReport(
        category=category,
        candidate_count=len(in_category),
        observed_count=sum(
            material.material_state == SHADOW_MATERIAL_OBSERVED
            for material in in_category
        ),
        unassessable_count=sum(
            material.material_state == SHADOW_MATERIAL_UNASSESSABLE
            for material in in_category
        ),
        no_material_count=sum(
            material.material_state == SHADOW_MATERIAL_NONE
            for material in in_category
        ),
        summary=summarize_quality_observations(
            category_rows, top_companies_limit=top_companies_limit
        ),
        quality_floor=summarize_quality_floor(
            observations_by_case_id[case_id] for case_id in observed_case_ids
        ),
    )


def load_legacy_pilot_fixture_index(runs_jsonl_path: Path | str) -> LegacyFixtureIndex:
    """옛 파일럿 실행 기록(JSONL)에서 P11~P25와 이름이 같은 것만 찾는다.

    ★ 대조 규칙은 «정확히 같은 문자열»뿐이다. 공백을 지우거나 「주식회사」를
      떼거나 비슷한 이름을 이어붙이지 않는다. 이름이 한 글자라도 다르면 그
      후보에는 자료가 없는 것으로 센다 — 없는 자료를 만들지 않기 위해서다.

    ★ 여기서 «자료가 있다»는 말은 옛 실행 기록이 있다는 뜻이지, 그 기록으로
      품질 관측값을 복원할 수 있다는 뜻이 아니다. 옛 기록은 원자 claim 결속이
      없는 판이라 그대로는 `SHADOW_MATERIAL_UNASSESSABLE`로 센다.

    Args:
        runs_jsonl_path: 한 줄에 JSON 객체 하나인 실행 기록 파일 경로.

    Returns:
        case ID 오름차순 색인과 훑은 기록 수.

    Raises:
        FileNotFoundError: 파일이 없을 때.
        ValueError: JSON으로 읽을 수 없는 줄이 있을 때(조용히 넘기지 않는다).
    """

    path = Path(runs_jsonl_path)
    case_id_by_input_name = {
        input_name: case_id
        for case_id, input_name in SHADOW_INPUT_NAME_BY_CASE_ID.items()
    }
    refs: dict[str, list[str]] = {}
    scanned = 0
    matched = 0
    unmatched = 0
    with path.open(encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except ValueError as error:
                raise ValueError(
                    f"옛 파일럿 기록 {line_number}번째 줄을 JSON으로 읽지 못했습니다"
                ) from error
            scanned += 1
            company = ""
            if type(payload) is dict and type(payload.get("input")) is dict:
                raw_company = payload["input"].get("company")
                company = raw_company if type(raw_company) is str else ""
            case_id = case_id_by_input_name.get(company)
            if case_id is None:
                unmatched += 1
                continue
            run_id = payload.get("id")
            if type(run_id) is not str or not run_id.strip():
                raise ValueError(
                    f"옛 파일럿 기록 {line_number}번째 줄에 실행 ID가 없습니다"
                )
            matched += 1
            refs.setdefault(case_id, []).append(run_id)
    return LegacyFixtureIndex(
        refs_by_case_id=tuple(
            (case_id, tuple(sorted(refs[case_id]))) for case_id in sorted(refs)
        ),
        scanned_record_count=scanned,
        matched_record_count=matched,
        unmatched_record_count=unmatched,
    )


__all__ = [
    "LegacyFixtureIndex",
    "QualityFloorBreakdown",
    "SHADOW_INPUT_NAME_BY_CASE_ID",
    "SHADOW_LEGAL_NAME_BY_CASE_ID",
    "SHADOW_MATERIAL_NONE",
    "SHADOW_MATERIAL_OBSERVED",
    "SHADOW_MATERIAL_UNASSESSABLE",
    "ShadowCandidateMaterial",
    "ShadowCategoryReport",
    "ShadowHarnessReport",
    "build_shadow_report",
    "load_legacy_pilot_fixture_index",
    "observation_rows",
    "summarize_quality_floor",
]
