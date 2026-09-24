"""생성 품질 관측값을 출고 모드에 맞는 «한 줄»로 기록한다.

부분 보고서(``ReleaseMode.SHADOW``)에서 엄격 계약은 «관측 전용»이다. 그런데
관측 결과를 경고로 찍으면 장 9개와 문장별 ``fact_id`` 수십 줄이 매 실행마다
쌓여, 정작 사람이 봐야 할 진짜 경고가 묻힌다. 그래서 부분 보고서에서는
문제를 «유형»으로 묶어 INFO 한 줄로 줄이고, 원본 전체 목록은 DEBUG로 내린다.

전체 보고서(``ReleaseMode.FULL``)와 ``ReleaseMode.ENFORCE_NO_PARTIAL``에서는
이 경고가 실제로 출고를 막는 판정과 이어지므로 «기존 WARNING 문구 그대로»
남긴다. 줄이는 것은 관측 전용 갈래뿐이다.
"""

from __future__ import annotations

import logging
import re
from typing import Final, Mapping, Sequence

from src.features.composer.prose_facts import PROSE_FACT_ID_PREFIX
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality.composition_diagnostic_constants import (
    SAFETY_BLOCK_KINDS_FIELD,
    SAFETY_BLOCK_ROUND_FIELD,
    SAFETY_BLOCK_SECTION_ORDER,
    SAFETY_BLOCK_SECTIONS_FIELD,
    SAFETY_BLOCK_STEP,
    SAFETY_BLOCK_TOTAL_FIELD,
)
from src.shared.report_quality.dto import ReportCandidate
from src.shared.report_quality.generation import GenerationQualityObservation
from src.shared.report_quality.models import GenerationAssessment
from src.shared.report_quality.safety_problem_kinds import (
    SAFETY_PROBLEM_KINDS,
    safety_problem_kind,
)


#: 부분 보고서 요약 한 줄에 실을 유형의 최대 개수. 넘치면 나머지는 개수로만
#: 알린다 — 한 줄이 다시 길어지면 줄인 뜻이 없어진다.
MAX_SUMMARY_TYPES: Final[int] = 10

#: 산문 사실의 ``fact_id``는 문장마다 다른 해시라 그대로 세면 «전부 1건»이 된다.
#: 유형을 세려면 해시 자리를 지워야 한다.
_PROSE_FACT_ID_RE: Final[re.Pattern[str]] = re.compile(
    re.escape(PROSE_FACT_ID_PREFIX) + r"[0-9a-fA-F]{32}"
)
_NORMALIZED_PROSE_FACT_ID: Final[str] = PROSE_FACT_ID_PREFIX + "*"

#: ``portfolio장``·``past_changes장``처럼 영문 장 이름이 붙은 토큰. 장 이름이
#: 다르다는 이유로 같은 문제가 여러 유형으로 갈라지지 않게 자리를 지운다.
_SECTION_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[A-Za-z][A-Za-z0-9_]*장")
_NORMALIZED_SECTION_TOKEN: Final[str] = "<장>장"

#: 유형 하나를 «유형(건수)»로 적는 형식과 유형끼리 잇는 구분자.
_TYPE_COUNT_FORMAT: Final[str] = "{type_text}({count}건)"
_TYPE_JOINER: Final[str] = " / "
_TRUNCATED_SUFFIX_FORMAT: Final[str] = "{joiner}외 {remaining}유형"

#: 기존 경고 문구 — FULL·ENFORCE_NO_PARTIAL은 «글자 하나 바꾸지 않고» 이 형식을
#: 그대로 쓴다. 부분 보고서에서는 같은 형식을 DEBUG로 내려 원본을 보존한다.
_FULL_PROBLEM_LOG_FORMAT: Final[str] = (
    "v2 생성 품질 판정(전체 안전은 관측 전용): "
    "계약=%s · 품질=%s · 안전=%s · 문제=%s"
)

#: 부분 보고서 전용 요약 문구. 관측 전용임을 문장에 박아 둔다.
_SHADOW_SUMMARY_LOG_FORMAT: Final[str] = (
    "v2 생성 품질 관측(부분 보고서, 관측 전용): "
    "계약=%s · 품질=%s · 안전=%s · 문제=%d건 · 유형=%s"
)


def _normalize_problem(problem: str) -> str:
    """문제 문장 하나에서 «건마다 달라지는 자리»만 지운다.

    Args:
        problem: 안전 판정이 만든 사람이 읽는 문제 문장.

    Returns:
        해시 ``fact_id``와 영문 장 이름을 자리표시자로 바꾼 문장. 그 밖의
        글자는 손대지 않는다.
    """

    normalized = _PROSE_FACT_ID_RE.sub(_NORMALIZED_PROSE_FACT_ID, problem)
    return _SECTION_TOKEN_RE.sub(_NORMALIZED_SECTION_TOKEN, normalized)


def summarize_safety_problems(
    problems: Sequence[str],
) -> tuple[tuple[str, int], ...]:
    """문제 문장들을 유형으로 묶어 건수와 함께 돌려준다.

    Args:
        problems: 안전 판정이 남긴 문제 문장들. 빈 입력을 허용한다.

    Returns:
        ``(유형 문장, 건수)`` 짝의 tuple. 건수 내림차순으로 정렬하고, 건수가
        같으면 유형 문장 오름차순으로 정렬한다. 빈 입력이면 빈 tuple.
    """

    counts: dict[str, int] = {}
    for problem in problems:
        normalized = _normalize_problem(problem)
        counts[normalized] = counts.get(normalized, 0) + 1
    return tuple(
        sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    )


def _format_problem_types(summary: Sequence[tuple[str, int]]) -> str:
    """유형·건수 짝을 한 줄 문자열로 잇는다.

    Args:
        summary: :func:`summarize_safety_problems` 결과.

    Returns:
        ``유형(건수)``를 ``/``로 이은 문자열. ``MAX_SUMMARY_TYPES``를 넘으면
        나머지는 ``외 N유형``으로만 알린다.
    """

    shown = [
        _TYPE_COUNT_FORMAT.format(type_text=type_text, count=count)
        for type_text, count in summary[:MAX_SUMMARY_TYPES]
    ]
    line = _TYPE_JOINER.join(shown)
    remaining = len(summary) - len(shown)
    if remaining > 0:
        line += _TRUNCATED_SUFFIX_FORMAT.format(
            joiner=_TYPE_JOINER,
            remaining=remaining,
        )
    return line


def log_generation_quality_observation(
    observation: GenerationQualityObservation,
    release_mode: ReleaseMode,
    *,
    logger: logging.Logger,
) -> None:
    """출고가 허용되지 않은 관측값을 모드에 맞는 수위로 기록한다.

    Args:
        observation: 생성 시점 품질·안전 관측값.
        release_mode: 이번 실행의 출고 모드.
        logger: 기록할 로거. 호출부의 로거를 그대로 받아 기록 이름이
            새 모듈로 갈라지지 않게 한다.

    Returns:
        없음. ``release_allowed``가 참이면 아무것도 남기지 않는다.
    """

    if observation.release_allowed:
        return

    if release_mode is ReleaseMode.SHADOW:
        summary = summarize_safety_problems(observation.safety_problems)
        logger.info(
            _SHADOW_SUMMARY_LOG_FORMAT,
            observation.contract_version,
            observation.quality_grade,
            observation.safety_decision,
            len(observation.safety_problems),
            _format_problem_types(summary),
        )
        # 원본 목록은 «지우지 않는다» — 진단이 필요할 때만 켜서 보도록 내린다.
        logger.debug(
            _FULL_PROBLEM_LOG_FORMAT,
            observation.contract_version,
            observation.quality_grade,
            observation.safety_decision,
            observation.safety_problems,
        )
        return

    logger.warning(
        _FULL_PROBLEM_LOG_FORMAT,
        observation.contract_version,
        observation.quality_grade,
        observation.safety_decision,
        observation.safety_problems,
    )


#: 요약 판정 문장이 가리키는 장 id와 그 문장의 머리말.
_SUMMARY_SECTION_ID: Final[str] = "summary"
_SUMMARY_PROBLEM_HEAD: Final[str] = "요약"
#: fact_id 바로 뒤에 붙는 조사·구분자. 이 중 하나가 이어질 때만 그 사실의 문장으로 본다
#: — 다른 fact_id의 앞부분과 우연히 겹친 것을 그 사실로 세지 않는다.
_FACT_ID_FOLLOWERS: Final[tuple[str, ...]] = ("의", ":", "가")
#: 진단 줄을 못 만들었을 때의 경고 — 예외 «종류»만 남긴다(문장·원문은 싣지 않는다).
_SAFETY_BLOCK_FAILURE_LOG_FORMAT: Final[str] = (
    "FULL 공개 안전 차단 진단 줄을 만들지 못했습니다(%s)"
)


def _problem_section(problem: str, fact_sections: Mapping[str, str]) -> str:
    """문제 문장 하나가 어느 장 이야기인지 가린다. 못 가리면 빈 값.

    ① 문장 머리의 fact_id → 그 사실의 소유 장, ② 문장 속 첫 «{장 id}장»,
    ③ «요약»으로 시작하는 요약 판정 문장. 셋 다 아니면 장을 지어내지 않는다.
    """

    for fact_id, section_id in fact_sections.items():
        if (
            fact_id
            and problem.startswith(fact_id)
            and problem[len(fact_id):len(fact_id) + 1] in _FACT_ID_FOLLOWERS
        ):
            return section_id
    positions = [
        (problem.find(f"{section_id}장"), section_id)
        for section_id in SAFETY_BLOCK_SECTION_ORDER
        if f"{section_id}장" in problem
    ]
    if positions:
        return min(positions)[1]
    if problem.startswith(_SUMMARY_PROBLEM_HEAD):
        return _SUMMARY_SECTION_ID
    return ""


def safety_block_record(
    problems: Sequence[str],
    fact_sections: Mapping[str, str],
    *,
    validation_round: str,
) -> dict[str, object]:
    """FULL 공개 안전 차단 한 줄 — 유형별·장별 개수만 담는다.

    유형은 :func:`summarize_safety_problems` 로 fact_id 해시·장 이름을 지운 요약
    문장을 닫힌 코드로 접어 센다. 장별은 원래 문장에서 가린 장만 센다. 원문·
    fact_id·source_id는 한 글자도 싣지 않는다.

    Args:
        problems: 안전 판정이 남긴 문제 문장들.
        fact_sections: 후보 사실의 ``fact_id`` → 소유 장.
        validation_round: 닫힌 회차 이름(1차·보충).

    Returns:
        정화기(``observed_composition_steps``)를 그대로 지나는 진단 한 줄.
    """

    kinds: dict[str, int] = {}
    for type_text, count in summarize_safety_problems(problems):
        kind = safety_problem_kind(type_text)
        kinds[kind] = kinds.get(kind, 0) + count
    sections: dict[str, int] = {}
    for problem in problems:
        section_id = _problem_section(problem, fact_sections)
        if section_id in SAFETY_BLOCK_SECTION_ORDER:
            sections[section_id] = sections.get(section_id, 0) + 1
    return {
        "step": SAFETY_BLOCK_STEP,
        SAFETY_BLOCK_ROUND_FIELD: validation_round,
        SAFETY_BLOCK_TOTAL_FIELD: len(problems),
        SAFETY_BLOCK_KINDS_FIELD: {
            kind: kinds[kind] for kind in SAFETY_PROBLEM_KINDS if kind in kinds
        },
        SAFETY_BLOCK_SECTIONS_FIELD: {
            section_id: sections[section_id]
            for section_id in SAFETY_BLOCK_SECTION_ORDER
            if section_id in sections
        },
    }


def record_full_safety_block(
    sink: list[dict[str, object]],
    assessment: GenerationAssessment,
    candidate: ReportCandidate,
    *,
    validation_round: str,
    logger: logging.Logger,
) -> None:
    """FULL 공개 안전 판정이 막았으면 그 한 줄을 ``sink`` 에 남긴다. 통과면 남기지 않는다.

    ★ FULL 정지는 사유 코드만 운영 경계로 보낸다(``_raise_recovery_stop``). 문구는
      WARNING 로그에만 찍혀 실행 기록에 남지 않았다 — 이 한 줄이 그 빈칸을 닫는다.
    ★ 줄을 만들다 실패해도 판정과 사용자 안내는 그대로여야 한다. 이 함수가 예외를
      내면 출고 검증 차단이 «조립 실패»로 바뀐다 — 실패는 예외 종류만 경고로 남긴다.
    """

    problems = tuple(assessment.safety.problems)
    if not problems:
        return
    try:
        record = safety_block_record(
            problems,
            {fact.fact_id: fact.section_owner for fact in candidate.facts},
            validation_round=validation_round,
        )
    except Exception as error:  # noqa: BLE001 — 진단 실패가 출고 판정을 바꾸면 안 된다
        logger.warning(_SAFETY_BLOCK_FAILURE_LOG_FORMAT, type(error).__name__)
        return
    sink.append(record)


__all__ = [
    "MAX_SUMMARY_TYPES",
    "log_generation_quality_observation",
    "record_full_safety_block",
    "safety_block_record",
    "summarize_safety_problems",
]
