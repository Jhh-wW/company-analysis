"""보고서가 차단돼도 단계별 숫자와 닫힌 코드만 실행 기록에 전달한다."""

from collections.abc import Mapping

from src.shared.report_quality.composition_diagnostic_constants import (
    PROTOCOL_COUNT_FIELDS,
    PROTOCOL_ENUM_FIELDS,
    PROTOCOL_OFFSET_FIELDS,
    PROTOCOL_ROW_REASONS,
    PROTOCOL_STEP,
    SUMMARY_BOOL_FIELDS,
    SUMMARY_COUNT_FIELDS,
    SUMMARY_STAGES,
    SUMMARY_STEP,
)


def _count(value: object, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _protocol(record: Mapping) -> dict[str, object] | None:
    result: dict[str, object] = {"step": PROTOCOL_STEP}
    for field, allowed in PROTOCOL_ENUM_FIELDS.items():
        value = record.get(field)
        if not isinstance(value, str) or value not in allowed:
            return None
        result[field] = value
    for field in PROTOCOL_COUNT_FIELDS:
        value = record.get(field)
        if not _count(value):
            return None
        result[field] = value
    for field in PROTOCOL_OFFSET_FIELDS:
        value = record.get(field)
        if not _count(value, minimum=-1):
            return None
        result[field] = value
    reasons = record.get("행탈락")
    if not isinstance(reasons, Mapping):
        return None
    result["행탈락"] = {
        key: value for key, value in reasons.items()
        if isinstance(key, str) and key in PROTOCOL_ROW_REASONS and _count(value)
    }
    return result


def _summary(record: Mapping) -> dict[str, object] | None:
    stage = record.get("도달단계")
    if (record.get("경로") != "legacy" or not isinstance(stage, str)
        or stage not in SUMMARY_STAGES):
        return None
    result: dict[str, object] = {
        "step": SUMMARY_STEP, "경로": "legacy", "도달단계": stage,
    }
    for field in SUMMARY_COUNT_FIELDS:
        if field not in record:
            return None
        value = record[field]
        # 실행하지 못한 단계는 None이며 실제 결과 0건과 구분한다.
        if value is not None and not _count(value):
            return None
        result[field] = value
    for field in SUMMARY_BOOL_FIELDS:
        value = record.get(field)
        if type(value) is not bool:
            return None
        result[field] = value
    return result


def observed_composition_steps(diagnostics: object) -> tuple[dict[str, object], ...]:
    """원문·응답·임의 오류문을 버리고 시도 순서와 단계 미도달을 보존한다.

    이 기록은 관측이며 공개 합격, 실제 문장 제외 수, 비용 확정의 증거가 아니다.
    동일한 응답이 두 번 와도 별도 시도이므로 중복 제거하지 않는다.
    """
    if not isinstance(diagnostics, (list, tuple)):
        return ()
    result: list[dict[str, object]] = []
    for record in diagnostics:
        if not isinstance(record, Mapping):
            continue
        step = record.get("step")
        normalized = (
            _protocol(record) if step == PROTOCOL_STEP else
            _summary(record) if step == SUMMARY_STEP else None
        )
        if normalized is not None:
            result.append(normalized)
    return tuple(result)
