"""핵심 요약 생성을 못 박는다 (엔진 v2 소단계 3-3).

★ 여기서 지키는 것:
  ① 정상 생성 — 요약이 새 문장으로 채워지고 본문(sections)은 그대로다.
  ② 재탕 검출 — 본문 문장을 글자 그대로 옮기면 1회 재요청한다.
  ③ 보충 경로 — 재요청 후에도 3문장 미만이면 본문 «확인» 문장으로 보충한다
     (서로 다른 장 우선). 빈 요약으로 인한 차단은 없다.
  ④ 분량 보장 — 재료가 있으면 요약은 3~5문장 사이다.
"""

from __future__ import annotations

import json

from src.features.composer.constants import (
    FORBIDDEN_TOPICS_GUIDE,
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    RETRY_REMINDER,
    SECTION_IDS,
    SECTION_TITLES,
)
from src.features.composer.logic import (
    SUMMARY_DUPLICATE_REMINDER,
    SUMMARY_MAX_SENTENCES,
    SUMMARY_MIN_SENTENCES,
    build_summary_prompt,
    compose_summary,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)


# ══════════════════════════════════════════════════════════
# 시험 재료 — compose_sections가 만든 본문을 흉내 낸 보고서
# ══════════════════════════════════════════════════════════


def _full_report() -> ComposedReport:
    """9개 장 전부에 «확인» 1문장 + «해석» 1문장이 있는 본문."""
    sections: list[ComposedSection] = []
    for order, section_id in enumerate(SECTION_IDS, start=1):
        sections.append(
            ComposedSection(
                section_id=section_id,
                sentences=(
                    ComposedSentence(
                        text=f"{SECTION_TITLES[section_id]} 장의 확인 사실 {order}번이다.",
                        citations=(str(order),),
                        grade=GRADE_CONFIRMED,
                    ),
                    ComposedSentence(
                        text=f"{SECTION_TITLES[section_id]} 장의 해석 {order}번이다.",
                        citations=(),
                        grade=GRADE_INTERPRETED,
                    ),
                ),
            )
        )
    return ComposedReport(sections=tuple(sections))


def _summary_json(texts: list[str]) -> str:
    """주어진 문장들로 요약 JSON 응답을 만든다 (전부 확인 등급·인용 ["1"])."""
    items = [
        {"글": text, "인용": ["1"], "등급": GRADE_CONFIRMED} for text in texts
    ]
    return json.dumps({"문장들": items}, ensure_ascii=False)


class _FakeAsk:
    """프롬프트를 기록하고 준비된 답을 차례로 돌려주는 가짜 작가."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self.responses) - 1)
        return self.responses[index]


# ══════════════════════════════════════════════════════════
# ① 정상 생성
# ══════════════════════════════════════════════════════════


def test_정상_생성이면_요약이_새_문장으로_채워진다():
    report = _full_report()
    texts = ["요약 하나다.", "요약 둘이다.", "요약 셋이다.", "요약 넷이다."]
    ask = _FakeAsk([_summary_json(texts)])

    result = compose_summary(report, ask)

    assert len(ask.prompts) == 1  # 정상이면 1회 호출로 끝
    assert [s.text for s in result.summary] == texts
    assert result.sections == report.sections  # 본문은 손대지 않는다
    # 인용·등급 계약이 그대로 실린다
    assert result.summary[0].citations == ("1",)
    assert result.summary[0].grade == GRADE_CONFIRMED


def test_프롬프트에_본문_전체와_규칙이_실린다():
    report = _full_report()

    prompt = build_summary_prompt(report)

    # 장 제목·본문 문장·인용 번호가 재료로 실린다
    for section_id in SECTION_IDS:
        assert SECTION_TITLES[section_id] in prompt
    assert "기업 정체성 장의 확인 사실 1번이다." in prompt
    assert "[인용: 1]" in prompt
    # 재탕 금지·인용/등급 규칙·JSON 강제·금지 주제
    assert "글자 그대로" in prompt
    assert GRADE_CONFIRMED in prompt
    assert GRADE_INTERPRETED in prompt
    assert "JSON" in prompt
    assert FORBIDDEN_TOPICS_GUIDE in prompt
    # 목표 분량 3~5문장이 안내된다
    assert f"{SUMMARY_MIN_SENTENCES}~{SUMMARY_MAX_SENTENCES}문장" in prompt


def test_빈_장은_요약_재료에서_빠진다():
    report = ComposedReport(
        sections=(
            ComposedSection(
                section_id="identity",
                sentences=(
                    ComposedSentence(
                        text="확인 문장이다.", citations=("1",), grade=GRADE_CONFIRMED
                    ),
                ),
            ),
            ComposedSection(
                section_id="culture", sentences=(), notice="자료 부족 안내"
            ),
        )
    )

    prompt = build_summary_prompt(report)

    assert SECTION_TITLES["identity"] in prompt
    assert SECTION_TITLES["culture"] not in prompt
    assert "자료 부족 안내" not in prompt


# ══════════════════════════════════════════════════════════
# ② 재탕 검출 → 재요청
# ══════════════════════════════════════════════════════════


def test_재탕을_검출하면_한_번_재요청한다():
    report = _full_report()
    dup_text = report.sections[0].sentences[0].text
    retry_texts = ["다시 쓴 요약 하나다.", "다시 쓴 요약 둘이다.", "다시 쓴 요약 셋이다."]
    ask = _FakeAsk(
        [
            _summary_json([dup_text, "새 요약 하나다.", "새 요약 둘이다."]),
            _summary_json(retry_texts),
        ]
    )

    result = compose_summary(report, ask)

    assert len(ask.prompts) == 2  # 재탕 재요청은 정확히 1회
    assert SUMMARY_DUPLICATE_REMINDER in ask.prompts[1]
    assert [s.text for s in result.summary] == retry_texts


def test_공백만_다른_재탕도_잡는다():
    report = _full_report()
    dup_text = report.sections[0].sentences[0].text
    spaced_dup = dup_text.replace(" ", "   ")  # 공백만 다른 같은 문장
    ask = _FakeAsk(
        [
            _summary_json([spaced_dup, "새 요약 하나다."]),
            _summary_json(["다시 쓴 하나다.", "다시 쓴 둘이다.", "다시 쓴 셋이다."]),
        ]
    )

    result = compose_summary(report, ask)

    assert len(ask.prompts) == 2
    assert spaced_dup not in [s.text for s in result.summary]


def test_재요청_결과에서도_재탕은_버린다():
    report = _full_report()
    dup_text = report.sections[0].sentences[0].text
    ask = _FakeAsk(
        [
            _summary_json([dup_text, "일차 새 문장이다."]),
            _summary_json(
                [dup_text, "이차 하나다.", "이차 둘이다.", "이차 셋이다."]
            ),
        ]
    )

    result = compose_summary(report, ask)

    texts = [s.text for s in result.summary]
    assert len(ask.prompts) == 2  # 재요청은 1회로 끝, 더 조르지 않는다
    assert dup_text not in texts
    assert texts == ["이차 하나다.", "이차 둘이다.", "이차 셋이다."]


def test_재요청이_전부_재탕이면_일차_생존_문장에_보충한다():
    report = _full_report()
    dup_text = report.sections[0].sentences[0].text
    ask = _FakeAsk(
        [
            _summary_json([dup_text, "일차 새 문장이다."]),
            _summary_json([dup_text]),  # 재요청도 전부 재탕
        ]
    )

    result = compose_summary(report, ask)

    texts = [s.text for s in result.summary]
    assert len(texts) == SUMMARY_MIN_SENTENCES
    assert texts[0] == "일차 새 문장이다."
    # 보충분은 본문 «확인» 문장이고, 서로 다른 장에서 왔다
    supplemented = result.summary[1:]
    assert all(s.grade == GRADE_CONFIRMED for s in supplemented)
    assert [s.text for s in supplemented] == [
        report.sections[0].sentences[0].text,
        report.sections[1].sentences[0].text,
    ]


# ══════════════════════════════════════════════════════════
# ③ 보충 경로 — 빈 요약으로 인한 차단 없음
# ══════════════════════════════════════════════════════════


def test_짧은_정상_응답은_본문_확인_문장으로_보충한다():
    report = _full_report()
    ask = _FakeAsk([_summary_json(["새 요약 하나다.", "새 요약 둘이다."])])

    result = compose_summary(report, ask)

    assert len(ask.prompts) == 1  # 재탕이 없으면 재요청 없이 바로 보충
    assert len(result.summary) == SUMMARY_MIN_SENTENCES
    assert result.summary[2].text == report.sections[0].sentences[0].text
    assert result.summary[2].grade == GRADE_CONFIRMED


def test_보충은_서로_다른_장을_우선한다():
    report = _full_report()
    # 작가가 «쓸 문장이 없다»고 정상 응답한 경우
    ask = _FakeAsk([json.dumps({"문장들": []}, ensure_ascii=False)])

    result = compose_summary(report, ask)

    expected = [report.sections[i].sentences[0].text for i in range(3)]
    assert [s.text for s in result.summary] == expected


def test_확인이_한_장에_몰려_있어도_장을_돌며_고른다():
    def _confirmed(text: str) -> ComposedSentence:
        return ComposedSentence(text=text, citations=("1",), grade=GRADE_CONFIRMED)

    report = ComposedReport(
        sections=(
            ComposedSection(
                section_id="identity",
                sentences=(_confirmed("A1이다."), _confirmed("A2이다."), _confirmed("A3이다.")),
            ),
            ComposedSection(
                section_id="business_model", sentences=(_confirmed("B1이다."),)
            ),
            ComposedSection(section_id="culture", sentences=(), notice="비었다"),
        )
    )
    ask = _FakeAsk([json.dumps({"문장들": []}, ensure_ascii=False)])

    result = compose_summary(report, ask)

    # 한 바퀴: A1, B1 → 다음 바퀴: A2 (같은 장 연속 선택보다 다른 장 우선)
    assert [s.text for s in result.summary] == ["A1이다.", "B1이다.", "A2이다."]


def test_파싱_실패는_한_번_재시도하고_보충으로_간다():
    report = _full_report()
    ask = _FakeAsk(["이건 JSON이 아니다"])

    result = compose_summary(report, ask)

    assert len(ask.prompts) == 2  # 원요청 1 + 파싱 재요청 1
    assert RETRY_REMINDER in ask.prompts[1]
    assert len(result.summary) == SUMMARY_MIN_SENTENCES
    assert all(s.grade == GRADE_CONFIRMED for s in result.summary)


def test_ask가_예외를_던져도_보충으로_요약이_나온다():
    report = _full_report()

    def _dying_ask(prompt: str) -> str:
        raise RuntimeError("provider 죽음")

    result = compose_summary(report, _dying_ask)

    assert len(result.summary) == SUMMARY_MIN_SENTENCES
    assert result.sections == report.sections


def test_본문이_통째로_비면_호출_없이_빈_요약이다():
    report = ComposedReport(
        sections=tuple(
            ComposedSection(section_id=sid, sentences=(), notice="비었다")
            for sid in SECTION_IDS
        )
    )
    ask = _FakeAsk(["{}"])

    result = compose_summary(report, ask)

    assert ask.prompts == []  # 재료가 없으면 작가를 부르지 않는다
    assert result.summary == ()
    assert result.sections == report.sections


def test_본문에_확인이_없으면_짧아도_그대로_통과한다():
    """보충 재료(확인 문장)가 없어도 예외·차단 없이 있는 만큼만 돌려준다."""
    report = ComposedReport(
        sections=(
            ComposedSection(
                section_id="identity",
                sentences=(
                    ComposedSentence(
                        text="해석뿐인 본문이다.", citations=(), grade=GRADE_INTERPRETED
                    ),
                ),
            ),
        )
    )
    ask = _FakeAsk([_summary_json(["요약 한 문장이다."])])

    result = compose_summary(report, ask)

    assert [s.text for s in result.summary] == ["요약 한 문장이다."]


# ══════════════════════════════════════════════════════════
# ④ 분량 보장 — 3~5문장
# ══════════════════════════════════════════════════════════


def test_다섯_문장을_넘으면_다섯으로_자른다():
    report = _full_report()
    texts = [f"넘치는 요약 {n}번이다." for n in range(1, 9)]  # 8문장
    ask = _FakeAsk([_summary_json(texts)])

    result = compose_summary(report, ask)

    assert len(result.summary) == SUMMARY_MAX_SENTENCES
    assert [s.text for s in result.summary] == texts[:SUMMARY_MAX_SENTENCES]


def test_재료가_있으면_요약은_항상_3에서_5문장이다():
    report = _full_report()
    cases = [
        _FakeAsk([_summary_json(["하나다."])]),  # 부족 → 보충
        _FakeAsk([_summary_json(["하나다.", "둘이다.", "셋이다.", "넷이다."])]),  # 정상
        _FakeAsk([_summary_json([f"문장 {n}이다." for n in range(1, 11)])]),  # 초과
        _FakeAsk(["JSON 아님"]),  # 파싱 실패 → 보충
    ]

    for ask in cases:
        result = compose_summary(report, ask)
        assert (
            SUMMARY_MIN_SENTENCES
            <= len(result.summary)
            <= SUMMARY_MAX_SENTENCES
        )
