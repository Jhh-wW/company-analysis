# -*- coding: utf-8 -*-
"""요약이 본문을 «거의 그대로» 옮겨 적은 경우도 재탕으로 잡는다.

★ 왜 필요한가 (실측) — 교육서비스 회사의 실제 유료 실행에서 핵심 요약 3건이
  1·2·3장 첫 문장과 유사도 1.00으로 같았다. 그 3건은 요약 문장이 모두
  탈락한 뒤 «본문 재사용» 보충 경로가 채운 것이라 재탕 검출이 보는 자리가
  아니었지만, 검출 자체에도 구멍이 있었다: 공백만 지운 문자열이 «정확히»
  같아야만 재탕으로 봤다. 조사·어미·마침표 하나만 바꿔 옮기면 그대로
  지나간다(실측 difflib 0.9754·0.9950).

★ 이 파일이 지키는 것:
  ① 어미·마침표만 바꾼 옮겨 적기도 재탕으로 보고 1회 재요청한다.
  ② 짧은 문장은 우연히 닮으므로 «정확히 같을 때»만 재탕으로 본다.
  ③ 정말 새로 쓴 요약은 그대로 통과한다 (헛 재요청을 만들지 않는다).
  ④ 어디부터 «못» 잡는지도 실측값으로 남긴다 — 꼬리 절이 통째로 바뀐
     3장↔7장 짝은 0.9242로 이 규칙 밖이고, 그건 dedupe가 맡는다.
"""
from __future__ import annotations

import json

import pytest

from src.features.composer.constants import (
    GRADE_CONFIRMED,
    SECTION_IDS,
    SECTION_TITLES,
)
from src.features.composer.logic import (
    SUMMARY_DUPLICATE_REMINDER,
    SUMMARY_NEAR_COPY_MIN_CHARS,
    SUMMARY_NEAR_COPY_RATIO,
    build_summary_prompt,
    compose_summary,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)

# 실측 본문 문장(3장 첫 문장, run 979d5e3f… report.json 그대로).
BODY_TEXT = (
    "교육서비스 부문은 2024년 289,115백만원에서 2025년 253,902백만원으로 "
    "감소했으며, 이는 국내 경기위축에 따른 공공기관 및 교육예산 축소의 영향을 "
    "받은 것으로 보인다."
)
# 어미만 바꿔 옮긴 것 — difflib 0.9754. 예전 «정확히 같을 때만» 규칙은 놓친다.
ENDING_CHANGED_TEXT = BODY_TEXT.replace("보인다.", "파악된다.")
# 꼬리 마침표만 뗀 것 — difflib 0.9950.
PUNCTUATION_CHANGED_TEXT = BODY_TEXT.rstrip(".")
# 종합해서 새로 쓴 문장 — 같은 사실을 다루지만 옮겨 적기가 아니다.
SYNTHESIZED_TEXT = (
    "교육 부문이 두 해 만에 350억원 넘게 줄면서 회사 전체 매출 감소를 이끌었고, "
    "회사는 그 원인을 공공 예산 축소로 본다."
)
# 실측 3장↔7장 수주 문장 짝 — 꼬리 «절»이 통째로 바뀌어 difflib 0.9242다.
# 이 규칙(0.95)은 여기까지 내려가지 않는다. 장 간 중복은 dedupe가 맡는다.
CLAUSE_REWRITTEN_BODY = (
    "2025년 당사는 삼성청년S/W 아카데미 위탁 운영 사업에서 34,917백만원, "
    "K-Digital Training(KDT) 프로그램에서 21,699백만원의 수주를 기록했으며, "
    "이는 정부 정책 기반 교육사업이 교육서비스 포트폴리오의 중요한 수익원임을 "
    "보여준다."
)
CLAUSE_REWRITTEN_COPY = (
    "2025년 기준 삼성청년S/W 아카데미 위탁 운영 사업에서 34,917백만원, "
    "K-Digital Training(KDT) 프로그램에서 21,699백만원의 수주를 기록했으며, "
    "이는 정부 정책 기반 교육사업이 회사의 주요 수익원임을 보여준다."
)
FILLER_TEXTS = ("새로 쓴 요약 둘이다.", "새로 쓴 요약 셋이다.", "새로 쓴 요약 넷이다.")


def _report(body_texts: tuple[str, ...]) -> ComposedReport:
    """첫 장에 주어진 본문 문장을 넣고 나머지 장은 비워 둔다."""
    sections = [
        ComposedSection(
            section_id=SECTION_IDS[0],
            sentences=tuple(
                ComposedSentence(text, ("1",), GRADE_CONFIRMED)
                for text in body_texts
            ),
        )
    ]
    sections += [ComposedSection(section_id, ()) for section_id in SECTION_IDS[1:]]
    return ComposedReport(sections=tuple(sections))


def _summary_json(texts: tuple[str, ...]) -> str:
    return json.dumps(
        {"문장들": [
            {"글": text, "인용": ["1"], "등급": GRADE_CONFIRMED} for text in texts
        ]},
        ensure_ascii=False,
    )


class _FakeAsk:
    def __init__(self, responses: list[str]):
        self.responses = responses
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.responses[min(len(self.prompts) - 1, len(self.responses) - 1)]


# ══════════════════════════════════════════════════════════
# ① 어미·마침표만 바꾼 옮겨 적기도 재탕이다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "near_copy", (ENDING_CHANGED_TEXT, PUNCTUATION_CHANGED_TEXT)
)
def test_어미만_바꾼_본문_옮겨적기도_재탕으로_잡아_1회_재요청한다(near_copy):
    assert near_copy != BODY_TEXT, "픽스처가 정확 일치라 예전 규칙도 잡습니다"
    report = _report((BODY_TEXT,))
    retry = ("다시 쓴 요약 하나다.", "다시 쓴 요약 둘이다.", "다시 쓴 요약 셋이다.")
    ask = _FakeAsk([
        _summary_json((near_copy,) + FILLER_TEXTS[:2]),
        _summary_json(retry),
    ])

    result = compose_summary(report, ask)

    assert len(ask.prompts) == 2, "재탕 재요청이 발동하지 않았습니다"
    assert SUMMARY_DUPLICATE_REMINDER in ask.prompts[1]
    assert near_copy not in [s.text for s in result.summary]
    assert [s.text for s in result.summary] == list(retry)


def test_이_규칙의_경계를_실측값으로_못_박는다():
    """어디까지 잡고 어디부터 못 잡는지 숫자로 남긴다 — 과장하지 않기 위해서다.

    ★ 실측 3장↔7장 수주 문장 짝은 꼬리 «절»이 통째로 바뀌어 0.9242다.
      이 규칙은 그 아래로 내려가지 않는다 — 그 모양은 장 간 중복 제거
      (dedupe.drop_cross_section_duplicates)가 맡는다. 여기서 기준을 그
      아래로 낮추면 «서로 다른 문장»까지 재탕으로 몰게 된다.
    """
    from difflib import SequenceMatcher

    def ratio(left: str, right: str) -> float:
        return SequenceMatcher(None, left, right).ratio()

    assert ratio(BODY_TEXT, PUNCTUATION_CHANGED_TEXT) >= SUMMARY_NEAR_COPY_RATIO
    assert ratio(BODY_TEXT, ENDING_CHANGED_TEXT) >= SUMMARY_NEAR_COPY_RATIO
    assert ratio(BODY_TEXT, SYNTHESIZED_TEXT) < SUMMARY_NEAR_COPY_RATIO
    assert ratio(CLAUSE_REWRITTEN_BODY, CLAUSE_REWRITTEN_COPY) < SUMMARY_NEAR_COPY_RATIO


def test_종합해_새로_쓴_요약은_재요청을_만들지_않는다():
    """헛 재요청 금지 — 같은 사실을 다뤄도 옮겨 적기가 아니면 통과한다."""
    report = _report((BODY_TEXT,))
    texts = (SYNTHESIZED_TEXT,) + FILLER_TEXTS
    ask = _FakeAsk([_summary_json(texts)])

    result = compose_summary(report, ask)

    assert len(ask.prompts) == 1
    assert [s.text for s in result.summary] == list(texts)


# ══════════════════════════════════════════════════════════
# ② 짧은 문장은 «정확히 같을 때»만 재탕이다
# ══════════════════════════════════════════════════════════


def test_짧은_문장은_한_글자_달라도_재탕으로_보지_않는다():
    short_body = "매출은 감소했다."
    assert len(short_body) < SUMMARY_NEAR_COPY_MIN_CHARS
    report = _report((short_body,))
    texts = ("매출이 감소했다.",) + FILLER_TEXTS
    ask = _FakeAsk([_summary_json(texts)])

    result = compose_summary(report, ask)

    assert len(ask.prompts) == 1, "짧은 문장에서 재요청이 잘못 발동했습니다"
    assert [s.text for s in result.summary] == list(texts)


def test_짧은_문장도_정확히_같으면_재탕이다():
    short_body = "매출은 감소했다."
    report = _report((short_body,))
    retry = ("다시 하나다.", "다시 둘이다.", "다시 셋이다.")
    ask = _FakeAsk([
        _summary_json((short_body,) + FILLER_TEXTS[:2]),
        _summary_json(retry),
    ])

    result = compose_summary(report, ask)

    assert len(ask.prompts) == 2
    assert short_body not in [s.text for s in result.summary]


# ══════════════════════════════════════════════════════════
# ③ 안내문이 «거의 그대로»까지 금지한다고 말한다
# ══════════════════════════════════════════════════════════


def test_요약_안내문이_조사_어미만_바꾼_옮겨적기도_금지한다():
    prompt = build_summary_prompt(_report((BODY_TEXT,)))
    assert "글자 그대로" in prompt
    assert "조사·어미·꼬리말만 바꿔 옮기는 것도" in prompt
    assert "종합" in prompt


def test_재탕_안내문이_거의_그대로도_가리킨다():
    assert "거의 그대로" in SUMMARY_DUPLICATE_REMINDER


def test_재탕_기준값이_거의_글자_그대로만_가리킨다():
    """기준을 낮춰 «다른 문장»까지 재탕으로 몰지 않도록 못 박는다."""
    assert 0.9 <= SUMMARY_NEAR_COPY_RATIO < 1.0
    assert SUMMARY_NEAR_COPY_MIN_CHARS >= 20


# ══════════════════════════════════════════════════════════
# ④ 본문이 여러 장일 때도 어느 장의 문장이든 잡는다
# ══════════════════════════════════════════════════════════


def test_다른_장_본문_문장을_옮겨_적어도_잡는다():
    sections = [
        ComposedSection(
            section_id=section_id,
            sentences=(
                ComposedSentence(
                    f"{SECTION_TITLES[section_id]} 장이 공식 자료로 확인한 사실이다.",
                    ("1",),
                    GRADE_CONFIRMED,
                ),
            ),
        )
        for section_id in SECTION_IDS
    ]
    report = ComposedReport(sections=tuple(sections))
    last_title = SECTION_TITLES[SECTION_IDS[-1]]
    near_copy = f"{last_title} 장이 공식 자료로 확인한 사실이었다."
    retry = ("다시 하나다.", "다시 둘이다.", "다시 셋이다.")
    ask = _FakeAsk([
        _summary_json((near_copy,) + FILLER_TEXTS[:2]),
        _summary_json(retry),
    ])

    result = compose_summary(report, ask)

    assert len(ask.prompts) == 2
    assert near_copy not in [s.text for s in result.summary]
