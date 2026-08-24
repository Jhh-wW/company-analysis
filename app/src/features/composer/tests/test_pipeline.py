"""v2 오케스트레이션(run_v2)을 못 박는다 (엔진 v2 소단계 3-4b).

★ 여기서 지키는 것:
  ① 정상 흐름 — 가짜 작가·검수만으로 compose→verify→summary→render→validate가
     한 번에 이어져 v2 스키마의 pipeline Report가 나온다 (AI·네트워크 0회).
  ② 역할 분리 — 작가 ask는 작성 프롬프트만, 검수 ask는 판정 프롬프트만 받는다
     (Generator/Evaluator 분리).
  ③ fail-closed — 본문이 통째로 비어 요약을 만들 수 없으면 V2ValidationError로
     끝난다 (조용한 통과 없음).
  ④ 관측 지표 — 초안·생존 문장 수가 실제 개수와 일치한다.
"""

from __future__ import annotations

import json
import re

import pytest

from src.features.composer.constants import (
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    SECTION_IDS,
)
from src.features.composer.pipeline import V2RunOutput, run_v2
from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
from src.features.composer.validate import V2ValidationError

#: 장 순서를 표시할 숫자 없는 한국어 표지 — 숫자를 넣으면 수치 검증(3-2)이
#: 근거에 없는 숫자로 보고 강등하므로 일부러 뺀다.
_SECTION_MARKS = "가나다라마바사아자"

#: 검수 프롬프트에서 대조 문장 번호를 읽는 모양 (verify._build_review_prompt)
_REVIEW_NUMBER_RE = re.compile(r"\[(\d+)\] \(인용:")


def _raw_fragments() -> dict[int, dict[str, str]]:
    return {
        1: {"종류": "사업내용", "원문": "가나다전자는 반도체 검사 장비 전문기업이다."},
        2: {
            "종류": "홈페이지",
            "원문": "고객 존중을 핵심 가치로 삼는다.",
            "출처": "https://www.ganada.example/about",
            "문서일": "2026-08-01",
        },
    }


def _section_json(mark: str) -> str:
    """장 하나 응답 — «확인» 1문장(조각 1) + «해석» 1문장(조각 2)."""
    return json.dumps(
        {
            "문장들": [
                {
                    "글": f"{mark} 장의 확인 사실 서술이다.",
                    "인용": ["1"],
                    "등급": GRADE_CONFIRMED,
                },
                {
                    "글": f"{mark} 장의 해석 서술이다.",
                    "인용": ["2"],
                    "등급": GRADE_INTERPRETED,
                },
            ]
        },
        ensure_ascii=False,
    )


_SUMMARY_TEXTS = ["요약 첫 문장이다.", "요약 둘째 문장이다.", "요약 셋째 문장이다."]


def _summary_json() -> str:
    return json.dumps(
        {
            "문장들": [
                {"글": text, "인용": ["1"], "등급": GRADE_CONFIRMED}
                for text in _SUMMARY_TEXTS
            ]
        },
        ensure_ascii=False,
    )


class _FakeWriter:
    """작성 프롬프트만 받아 장·요약 JSON을 돌려주는 가짜 작가."""

    def __init__(self, section_response=None):
        self.prompts: list[str] = []
        self.section_calls = 0
        self._section_response = section_response

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if "핵심 요약" in prompt:
            return _summary_json()
        # 장 프롬프트는 v3 정본 순서로 들어온다 (compose_sections 계약)
        mark = _SECTION_MARKS[self.section_calls % len(_SECTION_MARKS)]
        self.section_calls += 1
        if self._section_response is not None:
            return self._section_response
        return _section_json(mark)


class _FakeReviewer:
    """판정 프롬프트의 문장 번호 전부를 «참»으로 돌려주는 가짜 검수."""

    def __init__(self):
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        numbers = [int(value) for value in _REVIEW_NUMBER_RE.findall(prompt)]
        return json.dumps(
            {"판정": [{"번호": number, "결과": "참"} for number in numbers]},
            ensure_ascii=False,
        )


# ══════════════════════════════════════════════════════════
# ①②④ 정상 흐름
# ══════════════════════════════════════════════════════════


def test_정상_흐름이면_검증된_v2_Report가_나온다():
    writer = _FakeWriter()
    reviewer = _FakeReviewer()

    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
        corp_type="상장사",
        as_of_date="2026-08-24",
    )

    assert isinstance(output, V2RunOutput)
    report = output.report
    # v2 스키마 + 9장 전부 (장 삭제 없음, v3 정본 순서)
    assert report.schema_version == ENGINE_V2_SCHEMA_VERSION
    assert [section.cell for section in report.sections] == list(SECTION_IDS)
    assert all(section.prose_lines for section in report.sections)
    # 핵심 요약 3~5문장 — validate_v2를 통과했다는 뜻이다 (예외 없음)
    assert len(report.summary_items) == 3
    assert report.corp_type == "상장사"
    # 부록은 인용된 조각(1·2)만, 번호는 조각 번호 그대로
    assert sorted(source.number for source in report.citations) == [1, 2]


def test_작가와_검수는_서로_다른_프롬프트만_받는다():
    writer = _FakeWriter()
    reviewer = _FakeReviewer()

    run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
    )

    # 작가: 장 9회 + 요약 1회. 판정 프롬프트는 한 번도 받지 않는다.
    assert len(writer.prompts) == 10
    assert not any("판정" in prompt for prompt in writer.prompts)
    # 검수: 본문 1회 + 요약 1회. 작성 프롬프트는 한 번도 받지 않는다.
    assert len(reviewer.prompts) == 2
    assert all("판정" in prompt for prompt in reviewer.prompts)
    assert not any("핵심 요약" in prompt for prompt in reviewer.prompts)


def test_인라인_대괄호_인용_흉내는_출고검증을_막지_않는다():
    """작가가 «글» 안에 [2]처럼 대괄호 인용을 흉내내도(critical 결함) 파싱
    단계에서 걷어내, 가짜 인용-부록 불일치로 GATE_STOPPED에 빠지지 않는다."""

    def writer(prompt: str) -> str:
        if "핵심 요약" in prompt:
            return _summary_json()
        mark = _SECTION_MARKS[writer.calls % len(_SECTION_MARKS)]
        writer.calls += 1
        return json.dumps(
            {
                "문장들": [
                    {
                        "글": f"{mark} 장은 자료 [2]에서 밝힌 대로 성장했다.",
                        "인용": ["1"],
                        "등급": GRADE_CONFIRMED,
                    }
                ]
            },
            ensure_ascii=False,
        )

    writer.calls = 0
    reviewer = _FakeReviewer()

    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
    )  # V2ValidationError 없이 끝나야 한다

    report = output.report
    for section in report.sections:
        for text, _cite in section.prose_lines:
            assert "[2]" not in text  # 흉내낸 번호가 텍스트에 남지 않는다
    # 실제로 인용된 조각(1)만 부록에 실린다 — 흉내낸 [2]로 가짜 인용이 붙지 않는다
    assert sorted(source.number for source in report.citations) == [1]


def test_초안과_생존_문장_수를_그대로_센다():
    writer = _FakeWriter()
    reviewer = _FakeReviewer()

    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
    )

    # 초안: 9장 × 2문장 + 요약 3문장 = 21. 전부 통과했으므로 생존도 21.
    assert output.composed_sentences == 21
    assert output.verified_sentences == 21


# ══════════════════════════════════════════════════════════
# ③ fail-closed — 빈 본문은 출고 검증에서 막힌다
# ══════════════════════════════════════════════════════════


def test_본문이_통째로_비면_V2ValidationError로_끝난다():
    # 작가가 모든 장에서 «쓸 문장이 없다»고 답한 경우 — 요약 재료가 없어
    # 요약 3문장을 만들 수 없고, 마지막 출고 검증이 fail-closed로 막는다.
    writer = _FakeWriter(section_response=json.dumps({"문장들": []}))
    reviewer = _FakeReviewer()

    with pytest.raises(V2ValidationError) as caught:
        run_v2(
            "가나다전자",
            _raw_fragments(),
            None,
            writer_ask=writer,
            reviewer_ask=reviewer,
        )

    assert any("핵심 요약" in problem for problem in caught.value.problems)
    # 본문이 비면 요약·검수 헛호출도 없어야 한다 (작가 9회로 끝)
    assert len(writer.prompts) == 9
    assert reviewer.prompts == []
