"""보고서가 차단돼도 단계별 숫자와 닫힌 코드만 실행 기록에 전달한다."""

import re
from collections.abc import Mapping

from src.shared.report_quality.composition_diagnostic_constants import (
    DERIVED_RATIO_KINDS,
    DERIVED_RATIO_REASONS,
    DERIVED_RATIO_SECTION_IDS,
    DERIVED_RATIO_STEP,
    DERIVED_RATIO_VALUE_FIELDS,
    DIAGRAM_ROW_COUNT_SECTION_IDS,
    DIAGRAM_ROW_COUNT_STAGES,
    DIAGRAM_ROW_COUNT_STEP,
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


def _diagram_rows(record: Mapping) -> dict[str, object] | None:
    """장별 도식 행 수 기록 — 단계 이름과 «개수»만 통과시킨다.

    칸 내용·인용 id는 애초에 담기지 않지만, 계약 밖 장 이름과 숫자가 아닌 값은
    여기서 닫아서 버린다(다른 기록과 같은 방식).
    """

    stage = record.get("단계")
    if not isinstance(stage, str) or stage not in DIAGRAM_ROW_COUNT_STAGES:
        return None
    counts = record.get("장별행수")
    if not isinstance(counts, Mapping):
        return None
    return {
        "step": DIAGRAM_ROW_COUNT_STEP,
        "단계": stage,
        "장별행수": {
            key: value for key, value in counts.items()
            if isinstance(key, str) and key in DIAGRAM_ROW_COUNT_SECTION_IDS
            and _count(value)
        },
    }


_DECIMAL_TEXT_RE = re.compile(r"\d+(?:\.\d+)?")


def _derived_ratio(record: Mapping) -> dict[str, object] | None:
    """도식 수치 관문의 파생 비율 판정 — 장·사유 코드·수만 통과시킨다.

    ★ 수는 십진수 문자열로만 받는다. 문자열이면 무엇이든 담기는 칸을 두면
      거기로 원문이 새어 나간다(다른 기록과 같은 방식으로 닫는다).
    """

    section, reason, kind = (record.get(key) for key in ("장", "사유코드", "종류"))
    if (section not in DERIVED_RATIO_SECTION_IDS
            or reason not in DERIVED_RATIO_REASONS
            or kind not in DERIVED_RATIO_KINDS):
        return None
    result: dict[str, object] = {
        "step": DERIVED_RATIO_STEP, "장": section, "사유코드": reason, "종류": kind,
    }
    for field in DERIVED_RATIO_VALUE_FIELDS:
        value = record.get(field)
        if not isinstance(value, str):
            return None
        if value and _DECIMAL_TEXT_RE.fullmatch(value) is None:
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
            _summary(record) if step == SUMMARY_STEP else
            _diagram_rows(record) if step == DIAGRAM_ROW_COUNT_STEP else
            _derived_ratio(record) if step == DERIVED_RATIO_STEP else None
        )
        if normalized is not None:
            result.append(normalized)
    return tuple(result)
