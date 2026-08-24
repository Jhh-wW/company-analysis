"""composer 문장 단위 검증기를 못 박는다 (엔진 v2 소단계 3-2).

★ 여기서 지키는 것:
  ① 출처 실존 — 실존하지 않는 조각을 인용한 문장만 제거된다.
  ② 수치 검증 — 단위 붙은 수치 실패는 제거, 맨 수치 실패는 해석 강등,
     억원/원·%/비율 환산(ROUND_HALF_UP)은 통과. 실적표도 근거다.
  ③ 의미 검수 — 참=유지 / 애매=강등 / 거짓=재작성 1회 후 재검수.
     검수 불능이면 제거가 아니라 전원 해석 강등이다.
  ④ 라벨 정합 — 인용 없는 «확인»은 자동 강등, 해석 비율>50%는 경고 로그만.
  ⑤ 어떤 입력에서도 예외로 전체가 죽지 않는다. 장 개수·순서는 그대로다.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from src.features.composer.constants import (
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    PerformanceTable,
)
from src.features.composer.verify import (
    NOTICE_ALL_SENTENCES_REJECTED,
    REWRITE_PROMPT_HEADER,
    VERDICT_FALSE,
    VERDICT_TRUE,
    VERDICT_UNCLEAR,
    verify_report,
    verify_sentences,
)

LOGGER_NAME = "src.features.composer.verify"


# ══════════════════════════════════════════════════════════
# 시험 재료
# ══════════════════════════════════════════════════════════


def _raw_fragments() -> dict[int, dict[str, Any]]:
    """real.py 실측 구조 흉내 — 원 단위 공시값·비율 원값을 일부러 넣었다."""
    return {
        1: {
            "종류": "사업내용",
            "원문": (
                "가나다전자는 반도체 검사 장비 전문기업이다. "
                "2024년 매출액은 168,312,345,678원이다."
            ),
        },
        2: {
            "종류": "홈페이지",
            "원문": "영업이익률은 0.125 수준이다.",
            "출처": "https://www.ganada.example/about",
        },
    }


def _table() -> PerformanceTable:
    return PerformanceTable(
        caption="3개년 주요 실적",
        headers=("항목", "2022", "2023", "2024"),
        rows=(("매출액", "1,500", "1,600", "1,683"),),
        unit="억원",
        cite="조각 1·사업내용",
    )


def _sentence(
    text: str,
    citations: tuple[str, ...] = ("1",),
    grade: str = GRADE_CONFIRMED,
) -> ComposedSentence:
    return ComposedSentence(text=text, citations=citations, grade=grade)


def _report(
    sentences: tuple[ComposedSentence, ...],
    summary: tuple[ComposedSentence, ...] = (),
    notice: str = "",
) -> ComposedReport:
    return ComposedReport(
        sections=(
            ComposedSection(
                section_id="identity", sentences=sentences, notice=notice
            ),
        ),
        summary=summary,
    )


def _verdict_json(results: dict[int, str]) -> str:
    return json.dumps(
        {
            "판정": [
                {"번호": number, "결과": result}
                for number, result in results.items()
            ]
        },
        ensure_ascii=False,
    )


class _FakeVerifier:
    """검수·재작성 프롬프트를 구분해 준비된 답을 차례로 돌려주는 가짜 검수 AI."""

    def __init__(self, review_responses: list[str], rewrite_response: str = ""):
        self.review_responses = list(review_responses)
        self.rewrite_response = rewrite_response
        self.review_prompts: list[str] = []
        self.rewrite_prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        if prompt.startswith(REWRITE_PROMPT_HEADER):
            self.rewrite_prompts.append(prompt)
            return self.rewrite_response
        self.review_prompts.append(prompt)
        if len(self.review_prompts) <= len(self.review_responses):
            return self.review_responses[len(self.review_prompts) - 1]
        return ""  # 준비된 답이 떨어지면 빈 답 (파싱 실패로 흐른다)


def _all_true(count: int) -> str:
    return _verdict_json({n: VERDICT_TRUE for n in range(1, count + 1)})


# ══════════════════════════════════════════════════════════
# ① 출처 실존
# ══════════════════════════════════════════════════════════


def test_실존하지_않는_인용_문장만_제거된다():
    report = _report(
        (
            _sentence("가나다전자는 반도체 검사 장비 전문기업이다.", ("1",)),
            _sentence("이 회사는 우주선을 만든다.", ("99",)),
        )
    )
    ask = _FakeVerifier([_all_true(1)])

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    section = verified.sections[0]
    assert len(section.sentences) == 1
    assert section.sentences[0].text == "가나다전자는 반도체 검사 장비 전문기업이다."
    assert section.sentences[0].grade == GRADE_CONFIRMED
    assert len(ask.review_prompts) == 1


def test_깨진_인용이_하나라도_섞이면_그_문장은_제거된다():
    report = _report(
        (_sentence("가나다전자는 반도체 검사 장비 전문기업이다.", ("1", "99")),)
    )
    ask = _FakeVerifier([])

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    assert verified.sections[0].sentences == ()
    assert verified.sections[0].notice == NOTICE_ALL_SENTENCES_REJECTED
    assert ask.review_prompts == []  # 남은 확인 문장이 없으니 검수 AI를 안 부른다


# ══════════════════════════════════════════════════════════
# ② 수치 검증
# ══════════════════════════════════════════════════════════


def test_단위_붙은_수치가_근거에_없으면_문장이_제거된다():
    report = _report(
        (
            _sentence("지난해 매출은 9,999억원이다.", ("1",)),
            _sentence("가나다전자는 반도체 검사 장비 전문기업이다.", ("1",)),
        )
    )
    ask = _FakeVerifier([_all_true(1)])

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    section = verified.sections[0]
    assert [s.text for s in section.sentences] == [
        "가나다전자는 반도체 검사 장비 전문기업이다."
    ]
    assert section.notice == ""  # 문장이 남았으니 안내문은 붙지 않는다


def test_맨_수치가_근거에_없으면_해석으로_강등된다():
    report = _report(
        (_sentence("이 회사는 초창기부터 456곳의 협력사를 뒀다.", ("1",)),)
    )
    ask = _FakeVerifier([])

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    section = verified.sections[0]
    assert len(section.sentences) == 1
    assert section.sentences[0].grade == GRADE_INTERPRETED  # 제거가 아니라 강등
    assert ask.review_prompts == []  # 강등 후 확인 문장이 없으니 검수 호출 없음


def test_원화_억원_환산은_ROUND_HALF_UP으로_통과한다():
    # 조각 원문은 168,312,345,678원 — 억원 환산·반올림하면 1,683억원.
    # ★ 실적표를 일부러 빼서(None) 표의 「1,683」 원시 일치가 아니라
    #   환산·반올림 경로 자체가 통과시키는지 못 박는다.
    report = _report(
        (_sentence("2024년 매출액은 약 1,683억원이다.", ("1",)),)
    )
    ask = _FakeVerifier([_all_true(1)])

    verified = verify_report(report, _raw_fragments(), None, ask)

    assert verified.sections[0].sentences[0].grade == GRADE_CONFIRMED


def test_비율_퍼센트_환산이_통과한다():
    # 조각 원문은 0.125 — 백분율로 12.5%
    report = _report(
        (_sentence("영업이익률은 12.5% 수준이다.", ("2",)),)
    )
    ask = _FakeVerifier([_all_true(1)])

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    assert verified.sections[0].sentences[0].grade == GRADE_CONFIRMED


def test_실적표_수치도_근거로_인정된다():
    # 인용 조각(2)에는 없는 1,683이지만 실적표 셀에 있다
    report = _report(
        (_sentence("매출 규모는 1,683억원대다.", ("2",)),)
    )
    ask = _FakeVerifier([_all_true(1)])

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    assert len(verified.sections[0].sentences) == 1
    assert verified.sections[0].sentences[0].grade == GRADE_CONFIRMED


# ══════════════════════════════════════════════════════════
# ③ 의미 검수
# ══════════════════════════════════════════════════════════


def test_거짓_판정_문장은_재작성_후_참이면_확인으로_남는다():
    rewritten = "가나다전자는 반도체 검사 장비 전문기업이다."
    report = _report(
        (_sentence("가나다전자는 업계를 지배하는 절대 강자다.", ("1",)),)
    )
    ask = _FakeVerifier(
        [_verdict_json({1: VERDICT_FALSE}), _verdict_json({1: VERDICT_TRUE})],
        rewrite_response=rewritten,
    )

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    section = verified.sections[0]
    assert [s.text for s in section.sentences] == [rewritten]
    assert section.sentences[0].grade == GRADE_CONFIRMED
    assert len(ask.review_prompts) == 2  # 첫 검수 + 재검수
    assert len(ask.rewrite_prompts) == 1  # 재작성은 1회뿐


def test_재작성해도_거짓이면_제거된다():
    report = _report(
        (_sentence("가나다전자는 업계를 지배하는 절대 강자다.", ("1",)),)
    )
    ask = _FakeVerifier(
        [_verdict_json({1: VERDICT_FALSE}), _verdict_json({1: VERDICT_FALSE})],
        rewrite_response="가나다전자는 여전히 절대 강자다.",
    )

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    assert verified.sections[0].sentences == ()
    assert verified.sections[0].notice == NOTICE_ALL_SENTENCES_REJECTED


def test_재작성_응답이_비면_그_문장은_제거된다():
    report = _report(
        (_sentence("가나다전자는 업계를 지배하는 절대 강자다.", ("1",)),)
    )
    ask = _FakeVerifier(
        [_verdict_json({1: VERDICT_FALSE})], rewrite_response=""
    )

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    assert verified.sections[0].sentences == ()
    assert len(ask.review_prompts) == 1  # 재작성이 없으니 재검수도 없다


def test_애매_판정은_제거가_아니라_해석_강등이다():
    report = _report(
        (_sentence("가나다전자는 검사 장비 시장의 강자로 보인다.", ("1",)),)
    )
    ask = _FakeVerifier([_verdict_json({1: VERDICT_UNCLEAR})])

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    section = verified.sections[0]
    assert len(section.sentences) == 1
    assert section.sentences[0].grade == GRADE_INTERPRETED
    assert ask.rewrite_prompts == []  # 애매는 재작성 대상이 아니다


def test_판정에_누락된_번호도_해석_강등으로_흐른다():
    report = _report(
        (
            _sentence("가나다전자는 반도체 검사 장비 전문기업이다.", ("1",)),
            _sentence("영업이익률은 12.5% 수준이다.", ("2",)),
        )
    )
    # 2번 문장 판정을 빼먹은 응답
    ask = _FakeVerifier([_verdict_json({1: VERDICT_TRUE})])

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    grades = [s.grade for s in verified.sections[0].sentences]
    assert grades == [GRADE_CONFIRMED, GRADE_INTERPRETED]


def test_검수_응답이_계속_깨지면_제거_없이_전부_해석_강등한다():
    report = _report(
        (
            _sentence("가나다전자는 반도체 검사 장비 전문기업이다.", ("1",)),
            _sentence("영업이익률은 12.5% 수준이다.", ("2",)),
        )
    )
    ask = _FakeVerifier(["이건 JSON이 아니다", "여전히 JSON이 아니다"])

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    section = verified.sections[0]
    assert len(section.sentences) == 2  # 하나도 제거되지 않는다
    assert all(s.grade == GRADE_INTERPRETED for s in section.sentences)
    assert len(ask.review_prompts) == 2  # 원요청 + 재요청 1회


def test_검수_호출이_계속_죽어도_예외가_새지_않는다():
    def broken_ask(prompt: str) -> str:
        raise RuntimeError("검수 회선 단절")

    report = _report(
        (_sentence("가나다전자는 반도체 검사 장비 전문기업이다.", ("1",)),),
        summary=(_sentence("영업이익률은 12.5% 수준이다.", ("2",)),),
    )

    verified = verify_report(report, _raw_fragments(), _table(), broken_ask)

    assert len(verified.sections[0].sentences) == 1
    assert verified.sections[0].sentences[0].grade == GRADE_INTERPRETED
    assert len(verified.summary) == 1
    assert verified.summary[0].grade == GRADE_INTERPRETED


# ══════════════════════════════════════════════════════════
# ④ 라벨 정합
# ══════════════════════════════════════════════════════════


def test_인용_없는_확인_문장은_자동으로_해석_강등된다():
    report = _report(
        (_sentence("가나다전자는 성장 잠재력이 크다.", (), GRADE_CONFIRMED),)
    )
    ask = _FakeVerifier([])

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    section = verified.sections[0]
    assert len(section.sentences) == 1
    assert section.sentences[0].grade == GRADE_INTERPRETED
    assert ask.review_prompts == []


def test_해석_비율이_절반을_넘으면_로그_경고만_남긴다(caplog):
    report = _report(
        (
            _sentence("가나다전자는 반도체 검사 장비 전문기업이다.", ("1",)),
            _sentence("검사 장비 수요는 계속될 것으로 보인다.", (), GRADE_INTERPRETED),
            _sentence("장비 국산화 흐름의 수혜가 예상된다.", (), GRADE_INTERPRETED),
        )
    )
    ask = _FakeVerifier([_all_true(1)])

    with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
        verified = verify_report(report, _raw_fragments(), _table(), ask)

    assert len(verified.sections[0].sentences) == 3  # 차단·제거 없음
    assert any("차단 아님" in record.getMessage() for record in caplog.records)


# ══════════════════════════════════════════════════════════
# 요약·헬퍼·죽지 않음
# ══════════════════════════════════════════════════════════


def test_요약_문장도_같은_규칙으로_검증된다():
    report = _report(
        (_sentence("가나다전자는 반도체 검사 장비 전문기업이다.", ("1",)),),
        summary=(
            _sentence("영업이익률은 12.5% 수준이다.", ("2",)),
            _sentence("이 문장은 유령 조각을 인용했다.", ("77",)),
        ),
    )
    ask = _FakeVerifier([_all_true(2)])  # 본문 1 + 요약 1 = 확인 2문장

    verified = verify_report(report, _raw_fragments(), _table(), ask)

    assert len(verified.summary) == 1
    assert verified.summary[0].text == "영업이익률은 12.5% 수준이다."
    assert verified.summary[0].grade == GRADE_CONFIRMED


def test_verify_sentences_헬퍼도_같은_규칙을_적용한다():
    sentences = (
        _sentence("이 문장은 유령 조각을 인용했다.", ("77",)),
        _sentence("가나다전자는 반도체 검사 장비 전문기업이다.", ("1",)),
    )
    ask = _FakeVerifier([_all_true(1)])

    kept = verify_sentences(sentences, _raw_fragments(), _table(), ask)

    assert [s.text for s in kept] == [
        "가나다전자는 반도체 검사 장비 전문기업이다."
    ]


def test_어댑터_튜플_입력도_받는다():
    fragments = (
        CollectedFragment(
            fragment_id="1",
            kind="사업내용",
            text="가나다전자는 반도체 검사 장비 전문기업이다.",
        ),
    )
    report = _report(
        (_sentence("가나다전자는 반도체 검사 장비 전문기업이다.", ("1",)),)
    )
    ask = _FakeVerifier([_all_true(1)])

    verified = verify_report(report, fragments, None, ask)

    assert len(verified.sections[0].sentences) == 1


def test_이상한_입력에서도_전체가_죽지_않는다():
    empty_report = ComposedReport(sections=(), summary=())
    ask = _FakeVerifier(["아무 답"])

    # 빈 보고서 + 빈 조각 + 실적표 없음
    assert verify_report(empty_report, {}, None, ask) == empty_report

    # 원문이 빈 조각은 수집 목록에서 빠지므로, 그걸 인용한 문장은 제거된다
    raw: dict[int, dict[str, Any]] = {1: {"종류": "사업내용", "원문": "   "}}
    report = _report((_sentence("빈 조각을 인용한 문장이다.", ("1",)),))
    verified = verify_report(report, raw, None, ask)
    assert verified.sections[0].sentences == ()

    # 장 개수·순서는 어떤 경우에도 입력 그대로다
    assert [s.section_id for s in verified.sections] == ["identity"]


def test_검증기_내부가_망가져도_확인_강등_바닥으로_내려간다(monkeypatch):
    import src.features.composer.verify as verify_module

    def exploding_inner(*args: Any, **kwargs: Any) -> ComposedReport:
        raise ValueError("일부러 터뜨린 내부 결함")

    monkeypatch.setattr(verify_module, "_verify_report_inner", exploding_inner)
    report = _report(
        (_sentence("가나다전자는 반도체 검사 장비 전문기업이다.", ("1",)),)
    )

    verified = verify_module.verify_report(
        report, _raw_fragments(), _table(), _FakeVerifier([])
    )

    # 제거·차단 없이 «확인»만 해석으로 강등된 채 전부 남는다
    assert len(verified.sections[0].sentences) == 1
    assert verified.sections[0].sentences[0].grade == GRADE_INTERPRETED
