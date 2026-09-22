"""보고서가 차단돼도 단계별 숫자와 닫힌 코드만 실행 기록에 전달한다."""

import re
from collections.abc import Mapping

from src.shared.report_quality.composition_diagnostic_constants import (
    BODY_MACHINE_STEP, BODY_DISPOSITION_STEP, BODY_SECTION_IDS, BODY_DISPOSITIONS,
    BODY_SECTION_MOVE_STEP, SECTION_MOVE_BLOCKERS, SECTION_MOVE_REASONS,
    EMPTY_RECOVERY_STEP, EMPTY_RECOVERY_STATES, EMPTY_RECOVERY_ERRORS,
    GROUNDING_REWRITE_STEP, GROUNDING_REWRITE_STATES, GROUNDING_REWRITE_COUNT_KEYS,
    GROUNDING_REWRITE_STATE_DONE, GROUNDING_REWRITE_STATE_FORMAT_FAILED,
    GROUNDING_REWRITE_STATE_CALL_ABORTED,
    DERIVED_RATIO_DECIMAL_FIELDS,
    DERIVED_RATIO_FINGERPRINT_FIELD,
    DERIVED_RATIO_KINDS,
    DERIVED_RATIO_REASONS,
    DERIVED_RATIO_SECTION_IDS,
    DERIVED_RATIO_STEP,
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
    SECTION_EXECUTION_STEP,
    SECTION_EXECUTION_COUNT_FIELDS,
    EMPTY_RECOVERY_RESPONSE_SHAPES, EMPTY_RECOVERY_STAGE_COUNT_KEYS,
    STYLE_COUNTS_FIELD,
    STYLE_REASONS,
    STYLE_RENDER_FIELD,
    STYLE_RENDERS,
    STYLE_STEP,
)


def _count(value: object, *, minimum: int = 0) -> bool:
    return type(value) is int and value >= minimum


def _section_execution(record: Mapping) -> dict[str, object] | None:
    if any(not _count(record.get(field)) for field in SECTION_EXECUTION_COUNT_FIELDS):
        return None
    if record["동시상한"] < 1:
        return None
    return {"step": SECTION_EXECUTION_STEP, **{
        field: record[field] for field in SECTION_EXECUTION_COUNT_FIELDS
    }}


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
#: 근거 쌍 지문 — sha256 16진수 64자리. 빈 문자열은 «근거 쌍 없음»이다.
_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")


def _derived_ratio(record: Mapping) -> dict[str, object] | None:
    """도식 수치 관문의 파생 비율 판정 — 장·사유 코드·수만 통과시킨다.

    ★ 수 칸은 «후보가 적어 낸 백분율» 하나뿐이고, 근거 쌍은 지문으로만 받는다.
      문자열이면 무엇이든 담기는 칸을 두면 거기로 원문이 새어 나간다.
    """

    section, reason, kind = (record.get(key) for key in ("장", "사유코드", "종류"))
    # ⚠️ 먼저 «문자열인가»를 본다 — list·dict 같은 해시 불가 값을 닫힌 목록에
    #    바로 대면 TypeError로 터진다. 진단이 실행을 멈추게 하면 안 된다
    #    (자매 함수 `_protocol`이 같은 순서로 본다).
    if not all(isinstance(value, str) for value in (section, reason, kind)):
        return None
    if (section not in DERIVED_RATIO_SECTION_IDS
            or reason not in DERIVED_RATIO_REASONS
            or kind not in DERIVED_RATIO_KINDS):
        return None
    result: dict[str, object] = {
        "step": DERIVED_RATIO_STEP, "장": section, "사유코드": reason, "종류": kind,
    }
    for field in DERIVED_RATIO_DECIMAL_FIELDS:
        value = record.get(field)
        if not isinstance(value, str) or _DECIMAL_TEXT_RE.fullmatch(value) is None:
            return None
        result[field] = value
    # 근거 쌍은 지문으로만 받는다. 원문 금액이 실려 오면 항목을 통째로 버린다.
    fingerprint = record.get(DERIVED_RATIO_FINGERPRINT_FIELD)
    if not isinstance(fingerprint, str):
        return None
    if fingerprint and _FINGERPRINT_RE.fullmatch(fingerprint) is None:
        return None
    result[DERIVED_RATIO_FINGERPRINT_FIELD] = fingerprint
    return result


def _style(record: Mapping) -> dict[str, object] | None:
    """문체·시점 표기 기록 — 닫힌 렌더 구분·닫힌 사유 코드·«개수»만 통과시킨다.

    렌더 구분이 닫힌 값(1차/보충/확보근거) 밖이거나, 사유 목록 밖 키·정수가
    아닌 값(bool 포함)·0 이하·빈 사유별이면 기록을 통째로 버린다(장 이동·
    처분 기록과 같은 fail-closed). 원문·응답 같은 여분 칸은 애초에 결과에
    옮겨 적지 않는다.
    """

    render = record.get(STYLE_RENDER_FIELD)
    counts = record.get(STYLE_COUNTS_FIELD)
    if not isinstance(render, str) or render not in STYLE_RENDERS:
        return None
    if not isinstance(counts, Mapping) or not counts:
        return None
    if any(not isinstance(key, str) or key not in STYLE_REASONS
           or not _count(value, minimum=1) for key, value in counts.items()):
        return None
    return {
        "step": STYLE_STEP, STYLE_RENDER_FIELD: render,
        STYLE_COUNTS_FIELD: dict(counts),
    }


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
            _section_execution(record) if step == SECTION_EXECUTION_STEP else
            _protocol(record) if step == PROTOCOL_STEP else
            _summary(record) if step == SUMMARY_STEP else
            _diagram_rows(record) if step == DIAGRAM_ROW_COUNT_STEP else
            _derived_ratio(record) if step == DERIVED_RATIO_STEP else
            _body_machine(record) if step == BODY_MACHINE_STEP else
            _body_disposition(record) if step == BODY_DISPOSITION_STEP else
            _body_section_move(record) if step == BODY_SECTION_MOVE_STEP else
            _empty_recovery(record) if step == EMPTY_RECOVERY_STEP else
            _grounding_rewrite(record) if step == GROUNDING_REWRITE_STEP else
            _style(record) if step == STYLE_STEP else None
        )
        if normalized is not None:
            result.append(normalized)
    return tuple(result)


def _section_ids(value: object, *, summary: bool = False) -> list[str] | None:
    if not isinstance(value, (tuple, list)):
        return None
    if any(not isinstance(item, str) or item not in BODY_SECTION_IDS
           or (item == "summary" and not summary) for item in value):
        return None
    return list(dict.fromkeys(value))


def _body_machine(record: Mapping) -> dict[str, object] | None:
    counts = record.get("장별")
    if not isinstance(counts, Mapping):
        return None
    clean = {}
    for section_id, values in counts.items():
        if (not isinstance(section_id, str) or section_id not in BODY_SECTION_IDS
                or not isinstance(values, Mapping)):
            return None
        draft, passed = values.get("초안"), values.get("기계통과")
        if not _count(draft) or not _count(passed) or passed > draft:
            return None
        clean[section_id] = {"초안": draft, "기계통과": passed}
    return {"step": BODY_MACHINE_STEP, "장별": clean}


def _body_disposition(record: Mapping) -> dict[str, object] | None:
    empty = _section_ids(record.get("장별빈본문"), summary=True)
    rewrite = record.get("문장재작성허용")
    counts = record.get("판정별")
    if empty is None or type(rewrite) is not bool or not isinstance(counts, Mapping):
        return None
    if any(not isinstance(key, str) or key not in BODY_DISPOSITIONS or not _count(value)
           for key, value in counts.items()):
        return None
    return {"step": BODY_DISPOSITION_STEP, "장별빈본문": empty,
            "문장재작성허용": rewrite, "판정별": dict(counts)}


def _body_section_move(record: Mapping) -> dict[str, object] | None:
    """장 배치 위반 문장을 «버리지 않고 옮긴» 결과를 닫힌 칸으로만 통과시킨다.

    출발 장과 도착 장은 서로 달라야 하고 둘 다 본문 장이어야 한다(요약은 장이
    아니므로 받지 않는다). 못 옮긴 사유는 닫힌 목록이며, 「옮긴 수 + 못 옮긴
    수」가 0이면 아무 일도 없었던 기록이라 버린다.
    """

    source, target = record.get("출발장"), record.get("도착장")
    reason = record.get("사유코드")
    moved = record.get("이동")
    blocked = record.get("이동불가")
    if (not isinstance(source, str) or source not in BODY_SECTION_IDS
            or source == "summary"
            or not isinstance(target, str) or target not in BODY_SECTION_IDS
            or target == "summary" or source == target
            or not isinstance(reason, str) or reason not in SECTION_MOVE_REASONS
            or not _count(moved) or not isinstance(blocked, Mapping)):
        return None
    if any(not isinstance(key, str) or key not in SECTION_MOVE_BLOCKERS
           or not _count(value, minimum=1) for key, value in blocked.items()):
        return None
    if not moved and not blocked:
        return None
    return {"step": BODY_SECTION_MOVE_STEP, "출발장": source, "도착장": target,
            "사유코드": reason, "이동": moved, "이동불가": dict(blocked)}


def _closed_shape_list(value: object) -> list[str] | None:
    """시도별 응답 꼴 목록 — 비어 있지 않고 원소가 전부 닫힌 코드일 때만 돌려준다."""

    if not isinstance(value, list) or not value:
        return None
    if any(not isinstance(item, str) or item not in EMPTY_RECOVERY_RESPONSE_SHAPES
           for item in value):
        return None
    return list(value)


def _empty_recovery(record: Mapping) -> dict[str, object] | None:
    state = record.get("상태")
    targets = _section_ids(record.get("대상장"))
    if not isinstance(state, str) or state not in EMPTY_RECOVERY_STATES or targets is None:
        return None
    result = {"step": EMPTY_RECOVERY_STEP, "상태": state, "대상장": targets}
    if state == "작성완료":
        count = record.get("작성문장수")
        if not _count(count):
            return None
        result["작성문장수"] = count
        # 요청하지 않은 장을 답에 끼워 넣은 «형식 어긋남»의 크기. 부분 수용으로
        # 바꾸면서 통째 포기가 사라졌으므로, 이 값이 계속 크면 지시문을 손봐야
        # 한다는 신호로 남긴다. 장 이름은 남기지 않고 개수만 센다.
        extra = record.get("요청밖장수")
        if not _count(extra):
            return None
        result["요청밖장수"] = extra
    if state == "작성완료" and "응답꼴" in record:
        shape = record.get("응답꼴")
        if not isinstance(shape, str) or shape not in EMPTY_RECOVERY_RESPONSE_SHAPES:
            return None
        result["응답꼴"] = shape
    if state == "작성형식실패":
        attempts = record.get("시도")
        if not _count(attempts, minimum=1):
            return None
        result["시도"] = attempts
        # 시도마다 어떤 꼴이었는지 — 열린 문자열이 아니라 닫힌 코드만 남긴다.
        if "응답꼴" in record:
            shapes = _closed_shape_list(record.get("응답꼴"))
            if shapes is None:
                return None
            result["응답꼴"] = shapes
    if state == "검수완료":
        recovered = _section_ids(record.get("복구장"))
        if recovered is None or not set(recovered) <= set(targets):
            return None
        result["복구장"] = recovered
        # 관문별 문장 수. 옛 기록에는 없으므로 있을 때만 검사한다.
        for key in EMPTY_RECOVERY_STAGE_COUNT_KEYS:
            if key not in record:
                continue
            if not _count(record.get(key)):
                return None
            result[key] = record[key]
    if state == "호출중단":
        error = record.get("오류종류")
        if not isinstance(error, str) or error not in EMPTY_RECOVERY_ERRORS:
            return None
        result["오류종류"] = error
    return result


def _grounding_rewrite(record: Mapping) -> dict[str, object] | None:
    """근거 결속 재작성 단계 — 대상장·개수·닫힌 코드만 통과시킨다.

    「대상(고쳐 쓰지 않았다면 사라졌을 문장 수)」과 「최종반영(고쳐 써서 살아난
    문장 수)」을 같은 기록에 담아, 같은 실행 안에서 기능 유무를 비교할 수 있게
    한다. 원문·재작성 응답은 애초에 이 함수에 들어오지 않는 칸이라 걸러낼 것도
    없지만, 열린 문자열 칸(응답꼴)은 닫힌 목록으로만 받는다.
    """

    state = record.get("상태")
    targets = _section_ids(record.get("대상장"))
    target_count = record.get("대상")
    if (not isinstance(state, str) or state not in GROUNDING_REWRITE_STATES
            or targets is None or not _count(target_count)):
        return None
    result: dict[str, object] = {
        "step": GROUNDING_REWRITE_STEP, "상태": state, "대상장": targets,
        "대상": target_count,
    }
    if state == GROUNDING_REWRITE_STATE_DONE:
        for key in GROUNDING_REWRITE_COUNT_KEYS:
            value = record.get(key)
            if not _count(value):
                return None
            result[key] = value
        if "응답꼴" in record:
            shape = record.get("응답꼴")
            if (not isinstance(shape, str)
                    or shape not in EMPTY_RECOVERY_RESPONSE_SHAPES):
                return None
            result["응답꼴"] = shape
    if state == GROUNDING_REWRITE_STATE_FORMAT_FAILED:
        shapes = _closed_shape_list(record.get("응답꼴"))
        if shapes is None:
            return None
        result["응답꼴"] = shapes
    if state == GROUNDING_REWRITE_STATE_CALL_ABORTED:
        error = record.get("오류종류")
        if not isinstance(error, str) or error not in EMPTY_RECOVERY_ERRORS:
            return None
        result["오류종류"] = error
    return result
