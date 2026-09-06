"""실행 진단 요약이 「정해진 단계만·주소 없이·상한 안에」 남는지 본다.

★ 이 시험이 지키는 것 — 요약 로그는 운영에서 원인을 추적하는 유일한 흔적이다.
  단계가 빠지면 못 보고, 주소·원문이 섞이면 남기면 안 되는 것이 남는다.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from src.features.observability import constants as obs
from src.features.observability import run_diagnostics


def _news_step(**overrides: object) -> dict:
    step = {
        "step": "5b_뉴스_수집",
        "스위치": True,
        "창": "기본",
        "검색": 12,
        "선별": 5,
        "분류AI호출": 1,
        "본문읽기": 4,
        "조각": 3,
        "실패": None,
        "제외": {"NEWS_BODY_FETCH_FAILED": 2, "NEWS_FRAGMENT_LIMIT": 1},
    }
    step.update(overrides)
    return step


def _name_step(**overrides: object) -> dict:
    step = {
        "step": "7_이름후보",
        "후보": 9,
        "조각": 4,
        "종류별": {"product": 6, "brand": 3},
        "상한적용": False,
        "입력": "표",
        "표수": 2,
        "탈락": {"근거 조각의 title 형식이 올바르지 않습니다": 5},
    }
    step.update(overrides)
    return step


def test_요약은_허용목록에_없는_단계를_담지_않는다():
    steps = [
        _news_step(),
        {"step": "6_수집_원문", "오류": "http://internal.example.com/secret?token=abc"},
        {"step": "6_수집_홈페이지", "주소": "https://corp.example.com/about"},
        _name_step(),
    ]

    summary = run_diagnostics.build_summary(steps)

    names = [item["step"] for item in summary[run_diagnostics.KEY_STEPS]]
    assert names == ["5b_뉴스_수집", "7_이름후보"]
    # 전체 단계 수는 「몇 개 중 몇 개를 골랐나」를 알려 주므로 그대로 센다.
    assert summary[run_diagnostics.KEY_STEP_TOTAL] == 4


def test_요약_한줄에_주소가_남지_않는다():
    steps = [
        _news_step(창="https://news.example.com/search?q=회사"),
        _name_step(탈락={"www.example.co.kr/doc 에서 온 조각": 2}),
    ]

    line = run_diagnostics.summary_json(steps)

    assert "://" not in line
    assert "www." not in line
    assert obs.RUN_SUMMARY_REDACTED_MARK in line


def test_요약_한줄이_긴_원문발췌를_그대로_담지_않는다():
    excerpt = "가" * 500
    steps = [_news_step(실패=excerpt)]

    line = run_diagnostics.summary_json(steps)

    assert excerpt not in line
    payload = json.loads(line)
    실패 = payload[run_diagnostics.KEY_STEPS][0]["실패"]
    assert len(실패) == obs.RUN_SUMMARY_MAX_LABEL_CHARS + len(
        obs.RUN_SUMMARY_TRUNCATED_MARK
    )


def test_요약_한줄은_길이_상한을_넘지_않고_생략을_밝힌다():
    # 허용 단계를 상한을 넘길 만큼 반복해 쌓는다.
    steps = [_news_step() for _ in range(60)] + [_name_step() for _ in range(60)]

    line = run_diagnostics.summary_json(steps)

    assert len(line) <= obs.RUN_SUMMARY_MAX_CHARS
    payload = json.loads(line)
    assert payload[run_diagnostics.KEY_OMITTED] > 0
    assert payload[run_diagnostics.KEY_STEP_TOTAL] == 120


def test_상한_안이면_생략_표시를_붙이지_않는다():
    line = run_diagnostics.summary_json([_news_step()])

    payload = json.loads(line)
    assert run_diagnostics.KEY_OMITTED not in payload


@pytest.mark.parametrize(
    "steps, expected",
    [
        ([], obs.RUN_GRADE_UNKNOWN),
        ([{"step": "v2_composer_완료", "생성문장": 30}], obs.RUN_GRADE_FULL),
        (
            [
                {"step": "6_수집_DART부분보고서전환", "사유코드": "OFFICIAL_WEB_ZERO"},
                {"step": "v2_composer_완료", "생성문장": 30},
            ],
            obs.RUN_GRADE_PARTIAL,
        ),
    ],
)
def test_최종등급은_단계기록만_보고_정한다(steps, expected):
    assert run_diagnostics.run_grade(steps) == expected


def test_요약_로그를_한_줄_남긴다(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger=run_diagnostics.logger.name)

    line = run_diagnostics.finish_run([_news_step()], corp_code="00126380")

    messages = [
        record.getMessage()
        for record in caplog.records
        if obs.RUN_SUMMARY_LOG_PREFIX in record.getMessage()
    ]
    assert len(messages) == 1
    assert "corp_code=00126380" in messages[0]
    assert line and line in messages[0]


def test_수집칸을_열어_두면_단계기록을_그대로_넘겨준다():
    steps = [_news_step(), {"step": "6_수집_원문", "오류": "x"}]

    with run_diagnostics.capture() as captured:
        run_diagnostics.finish_run(steps, corp_code="00126380")

    assert captured.filled is True
    # 원본은 요약과 달리 «전부» 넘어간다. 관리자 화면이 그걸 펼쳐 본다.
    assert [item["step"] for item in captured.steps] == [
        "5b_뉴스_수집",
        "6_수집_원문",
    ]


def test_수집칸이_없어도_실행을_막지_않는다(caplog: pytest.LogCaptureFixture):
    caplog.set_level(logging.INFO, logger=run_diagnostics.logger.name)

    assert run_diagnostics.finish_run([_news_step()]) != ""


def test_허용목록의_단계이름은_실제_생산코드에_있다():
    """이름이 바뀌면 요약이 조용히 빈다. 그 침묵을 여기서 깬다."""

    source_root = Path(__file__).resolve().parents[3]
    sources = [
        path.read_text(encoding="utf-8")
        for path in source_root.rglob("*.py")
        if "tests" not in path.parts
    ]
    assert sources, "생산 코드를 한 개도 찾지 못했습니다"

    missing = [
        name
        for name in obs.RUN_SUMMARY_STEP_NAMES
        if name not in obs.RUN_SUMMARY_PLANNED_STEP_NAMES
        and not any(f'"{name}"' in text for text in sources)
    ]
    assert missing == []
