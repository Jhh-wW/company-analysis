"""핵심 요약 «고르기»를 못 박는다 (엔진 v2 소단계 3-3).

★ 의도가 바뀐 근거 (2026-09-11) — 예전 이 파일은 「AI가 요약을 새로 쓴다」를
  지켰다. 그 계약이 실측에서 두 가지 결말로 갈렸다.
    · 결속을 요구하는 실행(4차 멀티캠퍼스 run e193846f): 새로 쓴 초안 4문장이
      어느 본문 사실과도 축자로 맞지 않아 검수·수치 안전 검사에 전부 지워졌고
      (수치검사후수 0) 본문 재활용으로 되돌아갔다.
    · 결속을 요구하지 않는 실행(2차 인텍에프에이): 결속 없는 AI 요약 2건이
      그대로 출고됐다.
  작성기는 축자 재사용을 «재탕»으로 버리고 결속기는 축자만 인정하는 모순이라,
  요약을 「검증된 본문 문장 중에서 고른다」로 바꿨다.

★ 여기서 지키는 것:
  ① 후보 만들기 — 요약 잣대를 통과한 본문 문장만, 같은 문장은 한 번만.
  ② 프롬프트 — 번호·장 이름·문장과 고르는 규칙이 실린다.
  ③ 응답 판독 — «번호»만 인정한다. 범위 밖·중복·비JSON·참거짓은 버린다.
  ④ 결속은 본문과 «같은 수준» — 돌려주는 것은 후보로 받은 «그 문장 객체»다.
     응답이 문장 글자를 담아 와도 그 글자는 요약에 실리지 않는다
     (예전 재탕 검출 `test_summary_near_copy.py`가 지키던 자리를 대신한다 —
      AI가 글자를 만들 수 없게 됐으므로 «옮겨 적기»가 원리적으로 불가능하다).
  ⑤ 실패해도 보고서를 멈추지 않는다. 다만 요청 전역 장애는 그대로 올린다.
"""

from __future__ import annotations

import json

import pytest

from src.features.composer.constants import (
    FORBIDDEN_TOPICS_GUIDE,
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    SECTION_IDS,
    SECTION_TITLES,
)
from src.features.composer.logic import (
    SUMMARY_MAX_SENTENCES,
    SUMMARY_MIN_SENTENCES,
    SUMMARY_SELECTION_NUMBERS_KEY,
    SummaryCandidate,
    build_summary_selection_prompt,
    parse_summary_selection,
    select_summary_sentences,
    summary_candidates,
)
from src.features.composer.port import (
    AskFatalError,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)


# ══════════════════════════════════════════════════════════
# 시험 재료 — compose_sections·verify_report가 만든 본문을 흉내 낸 보고서
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


class _FakeAsk:
    """프롬프트를 기록하고 준비된 답을 차례로 돌려주는 가짜 AI."""

    def __init__(self, responses: list[str]):
        self.responses = responses
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        index = min(len(self.prompts) - 1, len(self.responses) - 1)
        return self.responses[index]


# ══════════════════════════════════════════════════════════
# ① 후보 만들기
# ══════════════════════════════════════════════════════════


def test_후보는_본문_순서대로_모든_장에서_모인다():
    report = _full_report()

    candidates = summary_candidates(report)

    assert len(candidates) == 2 * len(SECTION_IDS)
    assert candidates[0].section_id == SECTION_IDS[0]
    assert candidates[0].sentence is report.sections[0].sentences[0]
    assert [c.section_id for c in candidates[:2]] == [SECTION_IDS[0]] * 2


def test_후보는_요약_잣대를_통과한_문장만_담는다():
    """수치 안전 검사를 뒤가 아니라 «앞»에 건다 — 이게 이 설계의 핵심이다."""
    report = _full_report()
    막을_문장 = report.sections[1].sentences[0].text

    candidates = summary_candidates(
        report, accept=lambda sentence: sentence.text != 막을_문장
    )

    assert 막을_문장 not in [c.sentence.text for c in candidates]
    assert len(candidates) == 2 * len(SECTION_IDS) - 1


def test_같은_문장이_두_장에_있어도_후보에는_한_번만_담긴다():
    같은_문장 = "두 장에 똑같이 실린 문장이다."
    report = ComposedReport(
        sections=(
            ComposedSection(
                "identity",
                (ComposedSentence(같은_문장, ("1",), GRADE_CONFIRMED),),
            ),
            ComposedSection(
                "business_model",
                (ComposedSentence(f"  {같은_문장}  ", ("1",), GRADE_CONFIRMED),),
            ),
        )
    )

    candidates = summary_candidates(report)

    assert len(candidates) == 1
    assert candidates[0].section_id == "identity"


# ══════════════════════════════════════════════════════════
# ② 프롬프트 — 번호·장 이름·문장 + 고르는 규칙
# ══════════════════════════════════════════════════════════


def test_프롬프트에_번호와_장_이름과_후보_문장이_실린다():
    report = _full_report()
    candidates = summary_candidates(report)

    prompt = build_summary_selection_prompt(candidates)

    assert "1. [기업 정체성] 기업 정체성 장의 확인 사실 1번이다." in prompt
    for section_id in SECTION_IDS:
        assert SECTION_TITLES[section_id] in prompt
    # 마지막 후보까지 번호가 이어진다
    assert f"{len(candidates)}. [" in prompt


def test_프롬프트가_고르는_규칙을_말한다():
    """★ 4항이 뒤집힌 근거 (2026-09-11 독립 검토 P2-1).

    예전 지시는 「수치나 고유명사가 들어 있는 문장을 먼저」였다. 출고 계약
    (`docs/출력물 기준/00_핵심_요약/README.md` 조사 절차 3·제외 기준)은 그
    반대다 — 같은 장에서는 숫자 없는 문장을 먼저 고르고, 날짜·출처 번호의
    재기재를 막는다. 그래서 프롬프트를 계약 쪽으로 뒤집고 이 시험도 뒤집는다.
    """

    prompt = build_summary_selection_prompt(
        summary_candidates(_full_report())
    )

    assert f"{SUMMARY_MIN_SENTENCES}~{SUMMARY_MAX_SENTENCES}개의 번호" in prompt
    assert "취업준비생" in prompt
    assert "한 장에서 하나씩만 고른다" in prompt
    assert "숫자가 없는 문장을 먼저 고른다" in prompt
    assert "수익 구조" in prompt and "성장 방향" in prompt
    assert "번호만 답한다" in prompt
    assert f'{{"{SUMMARY_SELECTION_NUMBERS_KEY}": [1, 4, 7]}}' in prompt
    assert FORBIDDEN_TOPICS_GUIDE in prompt
    # 새로 쓰라는 말이 남아 있으면 안 된다 — 그게 결속을 깨뜨린 원인이었다
    assert "새로» 쓴다" not in prompt
    # 계약과 반대 방향인 옛 지시가 되살아나면 안 된다
    assert "수치나 고유명사" not in prompt


def test_빈_장은_후보에도_프롬프트에도_없다():
    report = ComposedReport(
        sections=(
            ComposedSection(
                "identity",
                (ComposedSentence("확인 문장이다.", ("1",), GRADE_CONFIRMED),),
            ),
            ComposedSection("culture", (), notice="자료 부족 안내"),
        )
    )

    prompt = build_summary_selection_prompt(summary_candidates(report))

    assert SECTION_TITLES["identity"] in prompt
    assert SECTION_TITLES["culture"] not in prompt
    assert "자료 부족 안내" not in prompt


# ══════════════════════════════════════════════════════════
# ③ 응답 판독 — 번호만 인정한다
# ══════════════════════════════════════════════════════════


def test_번호_배열을_그대로_읽는다():
    assert parse_summary_selection("[1, 4, 7]", 9) == (1, 4, 7)


def test_번호_키를_가진_객체도_읽는다():
    raw = json.dumps({SUMMARY_SELECTION_NUMBERS_KEY: [2, 5]}, ensure_ascii=False)
    assert parse_summary_selection(raw, 9) == (2, 5)


def test_코드_펜스가_붙은_객체도_읽는다():
    raw = "```json\n{\"번호\": [3, 1]}\n```"
    assert parse_summary_selection(raw, 9) == (3, 1)


def test_펜스_유무와_모양을_가리지_않고_같은_번호를_읽는다():
    """★ 유료 호출 1회가 통째로 버려지던 자리 (2026-09-11 독립 검토 P1).

    공용 회수기(`extract_json_payload`)는 펜스가 붙으면 «첫 { ~ 마지막 }»만
    자른다. 중괄호가 없는 «맨 배열»은 한 번도 회수되지 않아, 실측에서
    "```json\\n[1, 4, 7]\\n```" 가 번호 0개로 읽혔다. 그러면 곧장 규칙 보충으로
    돌아가고 그 실행의 고르기 호출은 0문장 기여로 사라진다.
    안내문은 객체형을 요구하지만, 모델이 어느 모양으로 답해도 읽어야 한다.
    """

    같은답 = (
        '{"번호": [1, 4, 7]}',
        '```json\n{"번호": [1, 4, 7]}\n```',
        "[1, 4, 7]",
        "```json\n[1, 4, 7]\n```",
        "고른 번호: [1, 4, 7]",
    )

    for raw in 같은답:
        assert parse_summary_selection(raw, 9) == (1, 4, 7), raw


def test_범위_밖_번호와_중복은_버린다():
    assert parse_summary_selection("[0, 1, 1, 10, 3, -2]", 9) == (1, 3)


def test_참거짓은_번호로_읽지_않는다():
    """파이썬에서 True는 int라 그냥 두면 1번 후보로 읽힌다."""
    assert parse_summary_selection("[true, false, 2]", 9) == (2,)


def test_숫자_문자열도_번호로_읽는다():
    assert parse_summary_selection('["1", "3"]', 9) == (1, 3)


def test_아주_긴_숫자_문자열은_버린다():
    """자릿수 상한 때문에 int() 자체가 예외를 던지는 자리를 미리 막는다."""
    assert parse_summary_selection(f'["{"9" * 5000}", 2]', 9) == (2,)


def test_비JSON과_문장_응답은_아무_번호도_못_읽는다():
    assert parse_summary_selection("이건 JSON이 아니다", 9) == ()
    assert parse_summary_selection('{"문장들": [{"글": "새 요약이다."}]}', 9) == ()
    assert parse_summary_selection("", 9) == ()


def test_다섯_개를_넘게_고르면_다섯에서_끊는다():
    numbers = parse_summary_selection("[1, 2, 3, 4, 5, 6, 7]", 9)
    assert numbers == (1, 2, 3, 4, 5)
    assert len(numbers) == SUMMARY_MAX_SENTENCES


# ══════════════════════════════════════════════════════════
# ④ 고르기 — 결속은 본문 문장과 «같은 수준»이다
# ══════════════════════════════════════════════════════════


def test_고른_문장은_후보로_받은_그_객체_그대로다():
    """글자를 새로 만들지 않으므로 인용·등급·구조화 사실이 본문과 같다.

    그래서 요약은 그 본문 문장의 결속(bound_summary_fact_id)과 «같은 수준»이
    된다 — 복사본이 아니라 같은 객체를 돌려주므로 나중에 필드가 하나 늘어도
    어긋날 자리가 없다.
    ⚠️ 「구성상 보장」이라고는 말하지 않는다 (2026-09-11 독립 검토) — 본문
      문장이 FactRecord를 못 만든 실행에서는 요약도 결속 0건이다. 요약이 본문보다
      느슨해지지 않을 뿐, 없는 결속을 만들어 주지는 않는다.
    """
    report = _full_report()
    candidates = summary_candidates(report)
    ask = _FakeAsk([json.dumps([1, 3, 5])])

    chosen = select_summary_sentences(candidates, ask)

    assert len(ask.prompts) == 1  # 재요청 없이 1회로 끝난다
    assert chosen == (
        candidates[0].sentence,
        candidates[2].sentence,
        candidates[4].sentence,
    )
    assert all(
        any(sentence is c.sentence for c in candidates) for sentence in chosen
    )


def test_응답이_문장_글자를_담아_와도_그_글자는_실리지_않는다():
    """예전 «재탕 검출»이 지키던 자리 — 이제는 구조가 대신 지킨다."""
    candidates = summary_candidates(_full_report())
    ask = _FakeAsk([
        json.dumps(
            {"번호": [2], "문장들": [{"글": "AI가 지어낸 새 요약 문장이다."}]},
            ensure_ascii=False,
        )
    ])

    chosen = select_summary_sentences(candidates, ask)

    assert [s.text for s in chosen] == [candidates[1].sentence.text]
    assert all("지어낸" not in sentence.text for sentence in chosen)


def test_한_장에서_여러_개를_고르면_그_장의_첫_번호만_남는다():
    """★ 계약 「장당 최대 1개」를 프롬프트가 아니라 코드가 지킨다 (P2-2).

    실측 재현 — 1장에 후보 3개를 두고 [1, 2, 3]을 답하게 하면 예전에는 요약
    3건이 전부 1장에서 나왔다. 막는 코드도 경고도 없었고, 지시 한 줄이
    유일한 방어였다. 빈자리는 부르는 쪽의 규칙 보충이 «다른 장»에서 채운다.
    """

    report = ComposedReport(
        sections=(
            ComposedSection(
                "identity",
                tuple(
                    ComposedSentence(f"개요 {n}번 문장이다.", ("1",), GRADE_CONFIRMED)
                    for n in (1, 2, 3)
                ),
            ),
            ComposedSection(
                "business_model",
                (ComposedSentence("사업 문장이다.", ("2",), GRADE_CONFIRMED),),
            ),
        )
    )
    candidates = summary_candidates(report)
    ask = _FakeAsk([json.dumps({"번호": [1, 2, 3]}, ensure_ascii=False)])

    chosen = select_summary_sentences(candidates, ask)

    assert [sentence.text for sentence in chosen] == ["개요 1번 문장이다."]
    assert len(ask.prompts) == 1  # 골라 낸 것을 다시 물어보지 않는다


def test_번호를_하나도_못_읽으면_빈_결과로_보충에_넘긴다():
    candidates = summary_candidates(_full_report())
    ask = _FakeAsk(["이건 JSON이 아니다"])

    chosen = select_summary_sentences(candidates, ask)

    assert chosen == ()
    assert len(ask.prompts) == 1  # 번호 하나 받자고 재요청하지 않는다


def test_후보가_없으면_AI를_부르지_않는다():
    ask = _FakeAsk(["[1]"])

    chosen = select_summary_sentences((), ask)

    assert chosen == ()
    assert ask.prompts == []


# ══════════════════════════════════════════════════════════
# ⑤ 실패 처리
# ══════════════════════════════════════════════════════════


def test_ask가_예외를_던져도_빈_결과로_돌아온다():
    candidates = summary_candidates(_full_report())

    def _dying_ask(prompt: str) -> str:
        raise RuntimeError("provider 죽음")

    assert select_summary_sentences(candidates, _dying_ask) == ()


def test_요청_전역_장애는_삼키지_않고_그대로_올린다():
    """한도·예산 소진은 «이 요청 몫을 다 썼다»라 부르는 쪽이 기록해야 한다."""
    candidates = summary_candidates(_full_report())
    fatal = AskFatalError(RuntimeError("요청 한도 시험 사유"), call_limit=True)

    def _limited_ask(prompt: str) -> str:
        raise fatal

    with pytest.raises(AskFatalError):
        select_summary_sentences(candidates, _limited_ask)


def test_고른_문장은_후보_범위를_절대_벗어나지_않는다():
    """번호가 어떻게 오든 색인 오류나 «없는 문장»이 나오지 않는다."""
    candidates = summary_candidates(_full_report())
    응답들 = [
        "[0]", "[-1]", f"[{len(candidates) + 1}]", "[1.5]", "[[1]]",
        '{"번호": "1"}', "null", "[]",
    ]

    for raw in 응답들:
        chosen = select_summary_sentences(candidates, _FakeAsk([raw]))
        assert all(
            any(sentence is c.sentence for c in candidates)
            for sentence in chosen
        ), raw
        assert len(chosen) <= SUMMARY_MAX_SENTENCES
