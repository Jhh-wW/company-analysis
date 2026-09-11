"""부분 보고서의 품질 관측 기록이 «유형 한 줄»로 줄어드는지 지킨다.

지키는 것 세 가지:

  ① 유형 묶기 — 건마다 달라지는 ``fact_id`` 해시와 영문 장 이름 때문에 같은
     문제가 여러 유형으로 갈라지지 않는다.
  ② 부분 보고서(SHADOW) — 경고가 아니라 INFO 한 줄이고, 원본 전체 목록은
     DEBUG로 남는다(지우지 않는다).
  ③ 전체 보고서(FULL·ENFORCE_NO_PARTIAL) — 기존 경고 문구가 그대로 남는다.

★ ④는 «배선» 시험이다. 이 파일 안에서 helper를 직접 부르는 것으로는 운영
  경로가 실제로 그 helper를 타는지 알 수 없어서, 진짜 ``run_v2`` SHADOW 실행을
  돌려 로그를 본다. 재료는 옆 파일 ``test_public_projection_wiring.py``의
  SHADOW 실행 도구를 그대로 빌린다 — 여기서 다시 지으면 두 벌이 갈라진다.
"""

from __future__ import annotations

import logging

import pytest

from src.features.composer import pipeline as pipeline_module
from src.features.composer.quality_observation_log import (
    MAX_SUMMARY_TYPES,
    log_generation_quality_observation,
    summarize_safety_problems,
)
from src.features.composer.tests.test_public_projection_wiring import _run_shadow
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality.generation import GenerationQualityObservation


# ── 시험 재료 ────────────────────────────────────────────────
# ★ 기준값은 «생산 상수 import»가 아니라 글자로 적는다. 상수를 그대로 빌려
#   쓰면 접두사나 자리표시자가 바뀌어도 시험이 함께 따라가 아무것도 못 잡는다.
_HEX_A = "0" * 32
_HEX_B = "1" * 32
_HEX_C = "abcdef0123456789abcdef0123456789"

_UNBOUND_PROBLEMS = (
    "portfolio장에 fact_id와 결속되지 않은 공개 내용이 있습니다",
    "past_changes장에 fact_id와 결속되지 않은 공개 내용이 있습니다",
)
_DUPLICATE_PROBLEMS = tuple(
    f"fact_id v2-prose-{hex_digits}가 중복됐습니다"
    for hex_digits in (_HEX_A, _HEX_B, _HEX_C)
)
_MIXED_PROBLEMS = _DUPLICATE_PROBLEMS + _UNBOUND_PROBLEMS

_EXPECTED_MIXED_SUMMARY = (
    ("fact_id v2-prose-*가 중복됐습니다", 3),
    ("<장>장에 fact_id와 결속되지 않은 공개 내용이 있습니다", 2),
)

_SHADOW_PREFIX = "v2 생성 품질 관측(부분 보고서, 관측 전용):"
_FULL_PREFIX = "v2 생성 품질 판정(전체 안전은 관측 전용): 계약="

_TEST_LOGGER_NAME = "src.features.composer.tests.i33_quality_observation_log"


def _observation(
    *,
    release_allowed: bool,
    safety_problems: tuple[str, ...],
) -> GenerationQualityObservation:
    """로그 수위만 보는 최소 관측값을 만든다."""

    return GenerationQualityObservation(
        mode="generation-shadow",
        contract_version="generation-quality-v0-test",
        quality_grade="미완성",
        safety_decision="차단",
        publication_grade="미완성",
        release_allowed=release_allowed,
        quality_shortfalls=(),
        safety_problems=safety_problems,
        substantive_claims=0,
        verified_claims=0,
        verified_ratio="0",
        document_sources=1,
    )


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        record
        for record in caplog.records
        if record.name == _TEST_LOGGER_NAME
    ]


def _log(
    observation: GenerationQualityObservation,
    release_mode: ReleaseMode,
    caplog: pytest.LogCaptureFixture,
) -> list[logging.LogRecord]:
    logger = logging.getLogger(_TEST_LOGGER_NAME)
    with caplog.at_level(logging.DEBUG, logger=_TEST_LOGGER_NAME):
        log_generation_quality_observation(
            observation,
            release_mode,
            logger=logger,
        )
    return _records(caplog)


# ══════════════════════════════════════════════════════════
# ① 유형 묶기
# ══════════════════════════════════════════════════════════


def test_해시_fact_id와_장_이름을_지워_같은_문제를_한_유형으로_센다() -> None:
    assert summarize_safety_problems(_MIXED_PROBLEMS) == _EXPECTED_MIXED_SUMMARY


def test_문제가_없으면_빈_요약이다() -> None:
    assert summarize_safety_problems(()) == ()


def test_건수가_같으면_유형_문자열_오름차순이다() -> None:
    assert summarize_safety_problems(("사유 나입니다", "사유 가입니다")) == (
        ("사유 가입니다", 1),
        ("사유 나입니다", 1),
    )


def test_해시가_아닌_글자는_손대지_않는다() -> None:
    # 접두사가 붙어도 16진수 32자리가 아니면 fact_id가 아니다 — 지우지 않는다.
    problem = "fact_id v2-prose-짧은값가 중복됐습니다"

    assert summarize_safety_problems((problem,)) == ((problem, 1),)


# ══════════════════════════════════════════════════════════
# ② 부분 보고서(SHADOW)
# ══════════════════════════════════════════════════════════


def test_부분_보고서는_경고_없이_요약_한_줄과_전체_DEBUG를_남긴다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    records = _log(
        _observation(release_allowed=False, safety_problems=_MIXED_PROBLEMS),
        ReleaseMode.SHADOW,
        caplog,
    )

    assert [record.levelno for record in records] == [
        logging.INFO,
        logging.DEBUG,
    ]
    summary_message = records[0].getMessage()
    assert summary_message.startswith(_SHADOW_PREFIX)
    assert "문제=5건" in summary_message
    assert "fact_id v2-prose-*가 중복됐습니다(3건)" in summary_message
    assert (
        "<장>장에 fact_id와 결속되지 않은 공개 내용이 있습니다(2건)"
        in summary_message
    )
    # 원본은 지우지 않는다 — DEBUG 한 줄에 전부 남아 있어야 한다.
    debug_message = records[1].getMessage()
    for problem in _MIXED_PROBLEMS:
        assert problem in debug_message


def test_유형_열개까지는_그대로_보이고_열한개부터_남은_수를_알린다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # 경계 행동으로 확인한다 — 상수를 import해 기대값을 만들면 상한을 1로
    # 낮춰도 시험이 함께 따라가 아무것도 못 잡는다.
    assert MAX_SUMMARY_TYPES == 10

    ten_types = tuple(f"안전 문제 {index}입니다" for index in range(10))
    records = _log(
        _observation(release_allowed=False, safety_problems=ten_types),
        ReleaseMode.SHADOW,
        caplog,
    )
    assert "외 " not in records[0].getMessage()
    assert "안전 문제 9입니다(1건)" in records[0].getMessage()

    caplog.clear()
    eleven_types = tuple(f"안전 문제 {index}입니다" for index in range(11))
    records = _log(
        _observation(release_allowed=False, safety_problems=eleven_types),
        ReleaseMode.SHADOW,
        caplog,
    )
    assert "외 1유형" in records[0].getMessage()
    assert "문제=11건" in records[0].getMessage()


# ══════════════════════════════════════════════════════════
# ③ 전체 보고서(FULL·ENFORCE_NO_PARTIAL)
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "release_mode",
    (ReleaseMode.FULL, ReleaseMode.ENFORCE_NO_PARTIAL),
)
def test_전체_보고서는_기존_경고를_그대로_남긴다(
    release_mode: ReleaseMode,
    caplog: pytest.LogCaptureFixture,
) -> None:
    records = _log(
        _observation(release_allowed=False, safety_problems=_MIXED_PROBLEMS),
        release_mode,
        caplog,
    )

    assert [record.levelno for record in records] == [logging.WARNING]
    message = records[0].getMessage()
    assert message.startswith(_FULL_PREFIX)
    for problem in _MIXED_PROBLEMS:
        assert problem in message


@pytest.mark.parametrize(
    "release_mode",
    (ReleaseMode.SHADOW, ReleaseMode.FULL, ReleaseMode.ENFORCE_NO_PARTIAL),
)
def test_출고가_허용되면_아무_기록도_남기지_않는다(
    release_mode: ReleaseMode,
    caplog: pytest.LogCaptureFixture,
) -> None:
    records = _log(
        _observation(release_allowed=True, safety_problems=_MIXED_PROBLEMS),
        release_mode,
        caplog,
    )

    assert records == []


# ══════════════════════════════════════════════════════════
# ④ 배선 — 진짜 SHADOW 실행이 이 경로를 탄다
# ══════════════════════════════════════════════════════════


def test_SHADOW_실행은_품질_경고_대신_관측_한_줄을_남긴다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    pipeline_logger_name = pipeline_module.logger.name

    # ★ 재료가 바뀐 근거 (2026-09-11) — 예전에는 이 실행이 «저절로» 출고
    #   불가였다. AI가 새로 쓴 요약 문장이 어느 fact에도 결속되지 않았기
    #   때문이다. 이제 요약은 이미 결속된 본문 문장을 그대로 고르므로 그
    #   문제가 사라졌다(같은 fixture로 실측: safety_problems 0건, 출고 가능).
    #   이 시험이 재는 것은 «출고 불가일 때의 로그 수위»이므로, 결속 없는
    #   공개 내용을 본문 한 장에 «명시적으로» 넣어 그 상황을 만든다.
    with caplog.at_level(logging.DEBUG, logger=pipeline_logger_name):
        output = _run_shadow(unbound_section_id="competitive_position")

    # 이 실행이 실제로 «출고 불가» 관측을 만들었는지부터 못 박는다.
    # 그렇지 않으면 아래 단정은 아무 일도 안 일어난 것을 통과시킨다.
    assert output.quality_observation is not None
    assert output.quality_observation.release_allowed is False
    assert output.quality_observation.safety_problems

    records = [
        record
        for record in caplog.records
        if record.name == pipeline_logger_name
    ]
    assert [
        record.levelno
        for record in records
        if "v2 생성 품질" in record.getMessage()
    ] == [logging.INFO, logging.DEBUG]
    assert not [
        record.getMessage()
        for record in records
        if record.levelno >= logging.WARNING
        and record.getMessage().startswith("v2 생성 품질 판정")
    ]
    summary_message = next(
        record.getMessage()
        for record in records
        if record.levelno == logging.INFO
        and record.getMessage().startswith(_SHADOW_PREFIX)
    )
    assert "문제=" in summary_message
    assert "유형=" in summary_message
