"""composer 뼈대를 못 박는다 (엔진 v2 소단계 3-1).

★ 여기서 지키는 것:
  ① 9개 장이 «전부» 나온다 — 실패한 장도 삭제되지 않고 안내문으로 남는다.
  ② 파싱 실패는 1회 재요청 후 정직한 안내문 — 예외가 밖으로 새지 않는다.
  ③ 작가가 단 인용 조각 id는 그대로 보존된다 (처분은 3-2 검증기 몫).
  ④ 프롬프트에 금지 주제·인용 규칙·조각 전체·실적표가 실린다.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.features.composer.constants import (
    FORBIDDEN_TOPICS_GUIDE,
    GRADE_CONFIRMED,
    DEFAULT_SENTENCE_RANGE,
    GRADE_INTERPRETED,
    MAX_INTERPRETED_SENTENCES_PER_SECTION,
    NOTICE_COMPOSE_FAILED,
    NOTICE_INSUFFICIENT_EVIDENCE,
    RETRY_REMINDER,
    SECTION_GUIDES,
    SECTION_IDS,
    SECTION_SENTENCE_RANGES,
)
from src.features.composer.logic import (
    build_section_prompt,
    compose_sections,
    parse_section_response,
)
from src.features.composer.port import (
    AskFatalError,
    CollectedFragment,
    PerformanceTable,
    fragments_from_raw,
    performance_table_from_report_table,
)


# ══════════════════════════════════════════════════════════
# 시험 재료 — real.py 실측 구조를 그대로 흉내 낸 조각
# ══════════════════════════════════════════════════════════


def _raw_fragments() -> dict[int, dict[str, Any]]:
    return {
        1: {"종류": "사업내용", "원문": "가나다전자는 반도체 검사 장비 전문기업이다."},
        2: {
            "종류": "홈페이지",
            "원문": "우리는 고객의 성공을 최우선 가치로 삼는다.",
            "출처": "https://www.ganada.example/about",
        },
        3: {
            "종류": "공식 IR",
            "원문": "2025년 매출액은 1,200억원이다.",
            "출처": "https://www.ganada.example/ir.pdf",
            "문서명": "2025 IR자료",
            "원문위치": "PDF p.3 1문단",
        },
    }


def _table() -> PerformanceTable:
    return PerformanceTable(
        caption="3개년 주요 실적",
        headers=("항목", "2023", "2024", "2025"),
        rows=(("매출액", "900", "1,000", "1,200"),),
        unit="억원",
        cite="조각 3·공식 IR",
    )


def _good_response() -> str:
    return json.dumps(
        {
            "문장들": [
                {
                    "글": "가나다전자는 반도체 검사 장비를 주력으로 하는 기업이다.",
                    "인용": ["1"],
                    "등급": GRADE_CONFIRMED,
                },
                {
                    "글": "검사 장비 중심 구조는 반도체 투자 사이클의 영향을 받는다.",
                    "인용": [],
                    "등급": GRADE_INTERPRETED,
                },
            ]
        },
        ensure_ascii=False,
    )


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
# ① 9장 구조
# ══════════════════════════════════════════════════════════


def test_아홉_장_구조를_그대로_반환한다():
    ask = _FakeAsk([_good_response()])

    report = compose_sections("가나다전자", _raw_fragments(), _table(), ask)

    assert tuple(s.section_id for s in report.sections) == SECTION_IDS
    assert len(report.sections) == 9
    assert report.summary == ()  # 요약은 소단계 3-3이 채운다
    assert all(s.notice == "" for s in report.sections)
    assert all(len(s.sentences) == 2 for s in report.sections)
    assert len(ask.prompts) == 9  # 장마다 정확히 1회 호출


def test_조각을_어댑터_튜플로_줘도_같이_돈다():
    fragments = fragments_from_raw(_raw_fragments())
    ask = _FakeAsk([_good_response()])

    report = compose_sections("가나다전자", fragments, _table(), ask)

    assert tuple(s.section_id for s in report.sections) == SECTION_IDS


def test_실적표가_없어도_돈다():
    ask = _FakeAsk([_good_response()])

    report = compose_sections("가나다전자", _raw_fragments(), None, ask)

    assert len(report.sections) == 9


# ══════════════════════════════════════════════════════════
# ② 파싱 실패 → 재시도 → 안내문
# ══════════════════════════════════════════════════════════


def test_파싱_실패는_한_번_재시도하고_안내문으로_남긴다():
    ask = _FakeAsk(["이건 JSON이 아니다"])

    report = compose_sections("가나다전자", _raw_fragments(), _table(), ask)

    # 장마다 원요청 1 + 재요청 1 = 2회, 총 18회
    assert len(ask.prompts) == 18
    # 재요청 프롬프트에는 JSON 재출력 안내가 붙는다
    assert RETRY_REMINDER in ask.prompts[1]
    for section in report.sections:
        assert section.sentences == ()
        assert section.notice == NOTICE_COMPOSE_FAILED


def test_재시도에서_성공하면_본문이_채워진다():
    class _RetryAsk(_FakeAsk):
        def __call__(self, prompt: str) -> str:
            self.prompts.append(prompt)
            # 홀수 번째(원요청)는 깨진 답, 짝수 번째(재요청)는 정상 답
            if len(self.prompts) % 2 == 1:
                return "형식을 지키지 않은 답"
            return _good_response()

    ask = _RetryAsk([])

    report = compose_sections("가나다전자", _raw_fragments(), _table(), ask)

    assert len(ask.prompts) == 18
    for section in report.sections:
        assert len(section.sentences) == 2
        assert section.notice == ""


def test_ask가_예외를_던져도_전체가_중단되지_않는다():
    def _dying_ask(prompt: str) -> str:
        raise RuntimeError("provider 죽음")

    report = compose_sections("가나다전자", _raw_fragments(), _table(), _dying_ask)

    assert len(report.sections) == 9
    for section in report.sections:
        assert section.sentences == ()
        assert section.notice == NOTICE_COMPOSE_FAILED


def test_AskFatalError는_삼키지_않고_그대로_재전파한다():
    """예산 소진 같은 요청 전역 장애는 장 실패 안내문으로 위장하지 않는다."""

    def dying_ask(prompt: str) -> str:
        raise AskFatalError(RuntimeError("예산 소진"))

    with pytest.raises(AskFatalError):
        compose_sections("가나다전자", _raw_fragments(), _table(), dying_ask)


def test_빈_문장_목록은_자료부족_안내로_남는다():
    """작가가 «쓸 문장이 없다»고 정상적으로 답한 경우 — 재요청하지 않는다."""
    ask = _FakeAsk([json.dumps({"문장들": []}, ensure_ascii=False)])

    report = compose_sections("가나다전자", _raw_fragments(), _table(), ask)

    assert len(ask.prompts) == 9  # 정상 응답이므로 재요청 없음
    for section in report.sections:
        assert section.sentences == ()
        assert section.notice == NOTICE_INSUFFICIENT_EVIDENCE


# ══════════════════════════════════════════════════════════
# ③ 인용 id 보존
# ══════════════════════════════════════════════════════════


def test_인용_조각_id를_그대로_보존한다():
    """숫자로 답해도 문자열로 맞춰 보존한다. 실존 검사는 3-2 검증기 몫."""
    response = json.dumps(
        {
            "문장들": [
                {"글": "매출은 1,200억원이다.", "인용": ["3", 1], "등급": GRADE_CONFIRMED}
            ]
        },
        ensure_ascii=False,
    )
    ask = _FakeAsk([response])

    report = compose_sections("가나다전자", _raw_fragments(), _table(), ask)

    sentence = report.sections[0].sentences[0]
    assert sentence.citations == ("3", "1")
    assert sentence.grade == GRADE_CONFIRMED


def test_해석_문장은_빈_인용을_허용한다():
    parsed = parse_section_response(
        json.dumps(
            {"문장들": [{"글": "종합하면 성장 국면이다.", "등급": GRADE_INTERPRETED}]},
            ensure_ascii=False,
        )
    )

    assert parsed is not None
    assert parsed[0].citations == ()


def test_형식이_깨진_항목만_빼고_살린다():
    """계약(글·등급·인용 배열)이 안 맞는 항목은 건너뛰고 나머지는 살린다."""
    parsed = parse_section_response(
        json.dumps(
            {
                "문장들": [
                    {"글": "정상 문장이다.", "인용": ["1"], "등급": GRADE_CONFIRMED},
                    {"글": "등급이 계약 밖이다.", "인용": ["1"], "등급": "추정"},
                    {"글": "", "인용": ["1"], "등급": GRADE_CONFIRMED},
                ]
            },
            ensure_ascii=False,
        )
    )

    assert parsed is not None
    assert [s.text for s in parsed] == ["정상 문장이다."]


def test_글_안의_인라인_대괄호_인용_표기는_제거된다():
    """작가가 «글» 안에 [3]·[인용: 1, 2]·[조각 2] 같은 표기를 흉내내도(critical
    결함 — validate.py가 이를 진짜 인용 번호로 오인해 GATE_STOPPED로 죽인다)
    형식 정리로 걷어낸다. 인용 배열(citations)은 그대로 보존된다."""
    response = json.dumps(
        {
            "문장들": [
                {
                    "글": "자료 [3]에서 밝힌 대로 성장했다.",
                    "인용": ["1"],
                    "등급": GRADE_CONFIRMED,
                },
                {
                    "글": "실적은 [인용: 1, 2] 개선됐다.",
                    "인용": ["1"],
                    "등급": GRADE_CONFIRMED,
                },
                {
                    "글": "이는 [조각 2]에 근거한다.",
                    "인용": ["2"],
                    "등급": GRADE_CONFIRMED,
                },
            ]
        },
        ensure_ascii=False,
    )

    parsed = parse_section_response(response)

    assert parsed is not None
    texts = [s.text for s in parsed]
    assert texts == [
        "자료 에서 밝힌 대로 성장했다.",
        "실적은 개선됐다.",
        "이는 에 근거한다.",
    ]
    assert all("[" not in t and "]" not in t for t in texts)
    # 정식 인용(citations 배열)은 그대로 보존된다 — 정리 대상은 텍스트뿐이다
    assert [s.citations for s in parsed] == [("1",), ("1",), ("2",)]


def test_코드펜스로_감싼_JSON도_읽는다():
    wrapped = "```json\n" + _good_response() + "\n```"

    parsed = parse_section_response(wrapped)

    assert parsed is not None
    assert len(parsed) == 2


# ══════════════════════════════════════════════════════════
# ④ 프롬프트 내용
# ══════════════════════════════════════════════════════════


def test_프롬프트에_금지주제_지침이_들어간다():
    ask = _FakeAsk([_good_response()])

    compose_sections("가나다전자", _raw_fragments(), _table(), ask)

    for prompt in ask.prompts:
        assert FORBIDDEN_TOPICS_GUIDE in prompt
        assert "직무별 KPI" in prompt
        assert "자소서" in prompt
        assert "면접" in prompt
        assert "연봉 추정" in prompt


def test_최소_문장에서_멈추지_말라고_지시한다():
    """★ 2026-08-29 실측 — 이 지시가 없으면 작가가 최소치 6문장에 머문다.

    실측(현대카드): 장별 작성 8·7·6·6·8·6·6·6·6 — 아홉 장 중 여섯 장이
    범위의 «최소»였다. 장당 평균 4.44문장으로 기준 보고서(진영 5.67 ·
    하이브 5.56)에 못 미쳐 채점 8/15점이었다.
    """

    prompt = build_section_prompt("가나다전자", "past_changes", fragments_from_raw(_raw_fragments()), _table())

    assert "멈추지 마라" in prompt
    assert "아직 쓰지 않은" in prompt


def test_근거가_여러_조각이면_모두_인용하라고_말한다():
    """★ 2026-08-29 실측 — 부록 출처 수가 만점 문턱(8개) 바로 아래(7개)였다.

    스키마 예시가 `"인용": ["<조각id>"]` 단수라 작가가 조각 하나만 인용하는
    습관이 생겼다. 근거가 여러 조각에 걸쳐 있으면 모두 인용해야 부록 출처가
    늘고, 도식 검수도 더 많은 근거 원문을 받는다.
    ⚠️ 짝 문구(「뒷받침하지 않는 조각은 넣지 마라」)를 지우면 근거 없는 인용을
      부르므로 함께 지키다.
    """

    prompt = build_section_prompt("가나다전자", "identity", fragments_from_raw(_raw_fragments()), _table())

    assert "«모두» 인용한다" in prompt
    assert "뒷받침하지 않는 조각은 넣지 마라" in prompt


def test_장별_최소_문장수는_8이다():
    """★ 실측으로 정한 값이다 — 낮추면 보고서가 다시 얇아진다.

    작가는 거의 언제나 «최소치»를 쓴다(현대카드 두 번 실측: 아홉 장 중 여섯
    장이 최소치). 작가 산출의 약 35%가 장 간 중복 제거·검증에서 빠지므로,
    기준 보고서와 같은 장당 5문장(총 45문장)에 닿으려면 장당 약 7.7문장을
    써야 한다. 8은 그 반올림이다.
    ⚠️ 이 값을 되돌리려면 «실측»을 근거로 대라. 문구만 바꾸는 것으로는
      작가가 움직이지 않는다는 것도 실측으로 확인됐다(산출 59→58).
    """

    minimum, maximum = DEFAULT_SENTENCE_RANGE
    assert minimum == 8
    assert maximum == 12
    assert all(
        SECTION_SENTENCE_RANGES[section_id] == DEFAULT_SENTENCE_RANGE
        for section_id in SECTION_IDS
    )


def test_분량을_늘리라면서_해석_천장도_같이_준다():
    """★ 안전선 — 「더 써라」만 있으면 작가가 근거 없이 «해석»으로 채운다.

    두 지시는 «짝»이다. 천장 문구를 지우면 해석 비율이 다시 올라간다.
    """

    prompt = build_section_prompt("가나다전자", "past_changes", fragments_from_raw(_raw_fragments()), _table())

    assert "근거가 없으면 차라리 적게 쓴다" in prompt
    assert (
        f"«해석» 등급은 {MAX_INTERPRETED_SENTENCES_PER_SECTION}문장을 넘기지 않는다"
        in prompt
    )


def test_프롬프트에_회사명과_조각_전체와_실적표가_실린다():
    fragments = fragments_from_raw(_raw_fragments())

    prompt = build_section_prompt("가나다전자", "past_changes", fragments, _table())

    assert "가나다전자" in prompt
    assert SECTION_GUIDES["past_changes"] in prompt
    # 조각 전체가 id와 함께 실린다
    assert "[조각 1]" in prompt
    assert "[조각 2]" in prompt
    assert "[조각 3]" in prompt
    assert "가나다전자는 반도체 검사 장비 전문기업이다." in prompt
    assert "2025 IR자료" in prompt
    # 실적표
    assert "3개년 주요 실적" in prompt
    assert "억원" in prompt
    assert "매출액" in prompt
    # JSON 출력 강제와 라벨 규칙
    assert "JSON" in prompt
    assert GRADE_CONFIRMED in prompt
    assert GRADE_INTERPRETED in prompt


def test_장마다_해당_장의_지침이_실린다():
    ask = _FakeAsk([_good_response()])

    compose_sections("가나다전자", _raw_fragments(), _table(), ask)

    for section_id, prompt in zip(SECTION_IDS, ask.prompts):
        assert SECTION_GUIDES[section_id] in prompt


# ══════════════════════════════════════════════════════════
# 입력 어댑터
# ══════════════════════════════════════════════════════════


def test_원시_조각_dict를_어댑터로_변환한다():
    fragments = fragments_from_raw(_raw_fragments())

    assert [f.fragment_id for f in fragments] == ["1", "2", "3"]
    assert fragments[0].kind == "사업내용"
    assert fragments[1].source_url == "https://www.ganada.example/about"
    assert fragments[2].document_title == "2025 IR자료"
    assert fragments[2].location == "PDF p.3 1문단"


def test_원문이_빈_조각은_어댑터에서_뺀다():
    raw = {1: {"종류": "사업내용", "원문": "  "}, 2: {"종류": "홈페이지", "원문": "본문"}}

    fragments = fragments_from_raw(raw)

    assert [f.fragment_id for f in fragments] == ["2"]


def test_파이프라인_ReportTable을_덕타이핑으로_감싼다():
    duck = SimpleNamespace(
        caption="전자공시 주요 재무계정",
        headers=["항목", "2025"],
        rows=[["매출액", "1,200"]],
        display_unit="억원",
        cite="조각 3·재무",
    )

    table = performance_table_from_report_table(duck)

    assert table.caption == "전자공시 주요 재무계정"
    assert table.headers == ("항목", "2025")
    assert table.rows == (("매출액", "1,200"),)
    assert table.unit == "억원"
    assert table.cite == "조각 3·재무"


def test_어댑터는_CollectedFragment_필드를_보존한다():
    fragment = CollectedFragment(
        fragment_id="7", kind="공식 IR", text="본문", source_url="https://x.example"
    )

    assert fragment.document_title == ""
    assert fragment.location == ""
