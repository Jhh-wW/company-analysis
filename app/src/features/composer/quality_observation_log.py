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
from typing import Final, Sequence

from src.features.composer.prose_facts import PROSE_FACT_ID_PREFIX
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality.generation import GenerationQualityObservation


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


__all__ = [
    "MAX_SUMMARY_TYPES",
    "log_generation_quality_observation",
    "summarize_safety_problems",
]
