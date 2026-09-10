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
    """★ 실측 — 이 지시가 없으면 작가가 최소치 6문장에 머문다.

    실측(현대카드): 장별 작성 8·7·6·6·8·6·6·6·6 — 아홉 장 중 여섯 장이
    범위의 «최소»였다. 장당 평균 4.44문장으로 기준 보고서(진영 5.67 ·
    하이브 5.56)에 못 미쳐 채점 8/15점이었다.
    """

    prompt = build_section_prompt("가나다전자", "past_changes", fragments_from_raw(_raw_fragments()), _table())

    assert "멈추지 마라" in prompt
    assert "아직 쓰지 않은" in prompt


def test_근거가_여러_조각이면_모두_인용하라고_말한다():
    """★ 실측 — 부록 출처 수가 만점 문턱(8개) 바로 아래(7개)였다.

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


def test_조각_라벨은_운반_지문_대신_닫힌_출처_종류를_쓴다():
    """typed 조각의 ``kind``는 운반 지문이라 작가에게 아무 뜻이 없다.

    ★ 왜 필요한가 — 부분 보고서 경로가 typed 조각을 그대로 받게 되면서, 라벨이
      ``news``·``dart_filing`` 같은 «닫힌 종류»에서 ``typed-evidence-v3:<hex>``
      지문으로 바뀔 수 있다. 지문은 작가에게 정보가 아니라 잡음이라 프롬프트가
      실행마다 흔들린다. 닫힌 종류가 있으면 그것을 먼저 쓴다.
    """

    typed = CollectedFragment(
        fragment_id="1",
        kind="typed-evidence-v3:" + "0" * 64,
        text="가나다전자는 물류 자동화 제품군을 넓히고 있다.",
        formal_source_kind="news",
        document_date="2026-09-01",
        source_publisher="가나다경제",
        news_claim_kind="reported_fact",
    )

    prompt = build_section_prompt("가나다전자", "past_changes", (typed,), None)
    line = next(line for line in prompt.splitlines() if "[조각 1]" in line)

    assert "(news · 메타데이터" in line, line
    assert '"기사날짜": "2026-09-01"' in line
    assert '"발행처": "가나다경제"' in line
    assert '"보도종류": "reported_fact"' in line
    assert "typed-evidence-v3" not in prompt


def test_닫힌_종류가_없는_조각은_예전처럼_kind를_라벨로_쓴다():
    """raw dict 경로의 조각은 ``kind``가 곧 「종류」다 — 글자가 안 바뀐다."""

    legacy = CollectedFragment(
        fragment_id="1",
        kind="회사 공식 자료",
        text="가나다전자는 공식 자료에서 사업 구조를 밝혔다.",
    )

    prompt = build_section_prompt("가나다전자", "past_changes", (legacy,), None)
    line = next(line for line in prompt.splitlines() if "[조각 1]" in line)

    assert "(회사 공식 자료)" in line, line


def test_종류를_하나도_모르는_조각은_자료로_적는다():
    """라벨 자리를 비우면 괄호가 빈 채로 나가 프롬프트 모양이 깨진다."""

    unknown = CollectedFragment(
        fragment_id="1",
        kind="",
        text="가나다전자는 물류 자동화 제품군을 넓히고 있다.",
    )

    prompt = build_section_prompt("가나다전자", "past_changes", (unknown,), None)
    line = next(line for line in prompt.splitlines() if "[조각 1]" in line)

    assert "(자료)" in line, line


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


# ══════════════════════════════════════════════════════════
# ④ 요약 보충 순서 — 각 장 «첫» 문장을 마지막 차례로 돌린다
#
# ★ 왜 (실측) — 예전에는 한 바퀴에 장마다 «첫» 문장부터 집었다. 그래서 요약
#   보충이 걸릴 때마다 요약이 「1·2·3장 첫 문장」이라는 똑같은 서명으로 나왔고,
#   실측 실행의 요약 3건이 정확히 본문 2장·3장·1장의 첫 문장과 축자 동일했다.
# ══════════════════════════════════════════════════════════


def _확인문장(text: str):
    from src.features.composer.port import ComposedSentence

    return ComposedSentence(text=text, citations=("1",), grade=GRADE_CONFIRMED)


def _본문(장당_문장수: int, 장수: int = 3):
    from src.features.composer.port import ComposedReport, ComposedSection

    return ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=SECTION_IDS[index],
                sentences=tuple(
                    _확인문장(f"{SECTION_IDS[index]}-{순번}번문장이다.")
                    for 순번 in range(1, 장당_문장수 + 1)
                ),
            )
            for index in range(장수)
        )
    )


def test_확인문장_보충은_각_장_첫문장을_마지막_순위로_돌린다() -> None:
    from src.features.composer.logic import _supplement_summary

    report = _본문(장당_문장수=2)
    첫문장들 = {section.sentences[0].text for section in report.sections}

    고른것 = _supplement_summary((), report)

    texts = [sentence.text for sentence in 고른것]
    assert len(texts) == 3
    assert not (set(texts) & 첫문장들), f"각 장의 첫 문장이 그대로 요약이 됐다: {texts}"
    assert texts == [section.sentences[1].text for section in report.sections]


def test_확인문장_보충은_여전히_장을_번갈아_고른다() -> None:
    """★ 첫 문장을 뒤로 미루면서 «서로 다른 장 우선»을 잃으면 안 된다."""
    from src.features.composer.logic import _supplement_summary
    from src.features.composer.port import ComposedReport, ComposedSection

    몰린_본문 = ComposedReport(
        sections=(
            ComposedSection("identity", tuple(
                _확인문장(f"A{n}이다.") for n in (1, 2, 3)
            )),
            ComposedSection("business_model", (_확인문장("B1이다."),)),
        )
    )

    texts = [s.text for s in _supplement_summary((), 몰린_본문)]

    assert texts == ["A2이다.", "B1이다.", "A3이다."]


def test_한_문장뿐인_장에서는_그_첫_문장을_쓴다() -> None:
    """순서만 바뀌고 «쓸 수 있는 문장 집합»은 같다 — 요약이 빌 위험은 0이다."""
    from src.features.composer.logic import _supplement_summary

    report = _본문(장당_문장수=1)

    texts = [s.text for s in _supplement_summary((), report)]

    assert texts == [section.sentences[0].text for section in report.sections]


def test_되돌리면_안_되는_문장은_보충에서_빠진다() -> None:
    """앞 단계의 안전 검사가 뺀 문장이 이 보충으로 되살아나면 안 된다."""
    from src.features.composer.logic import _normalized_text, _supplement_summary

    report = _본문(장당_문장수=2)
    금지 = report.sections[0].sentences[1].text

    texts = [
        s.text
        for s in _supplement_summary(
            (), report, excluded_keys=frozenset({_normalized_text(금지)})
        )
    ]

    assert 금지 not in texts
    assert len(texts) == 3, "제외 때문에 최소 문장 수를 못 채우면 안 된다"


# ══════════════════════════════════════════════════════════
# 8장 도식 «생성 수» 기록 — 0줄이 왜 0줄인지 되짚을 수 있게 한다
# ══════════════════════════════════════════════════════════

_문화_도식_칸 = ("존중과 신뢰", "경영지원팀 주관", "환위험 관리규정 운영")


def _도식_패킷(*, culture_slots: tuple[str, ...]):
    from src.features.composer.port import (
        CollectedFragment, SectionEvidencePacket, SectionEvidencePacketSet,
    )
    from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION

    generation = "a" * 64
    packets = []
    for index, section_id in enumerate(SECTION_IDS, start=1):
        slots = (
            culture_slots if section_id == "culture"
            else (CLAIM_SLOTS_BY_SECTION[section_id][0],)
        )
        packets.append(
            SectionEvidencePacket(
                company_id="00123456",
                evidence_generation_sha256=generation,
                section_id=section_id,
                fragments=(
                    CollectedFragment(
                        fragment_id=str(index),
                        kind="typed-evidence-v1:test",
                        text=(
                            f"테스트 회사의 {section_id} 공식 원문이다. "
                            "임직원 교육훈련과 조직문화 정착은 인사부서가 담당한다."
                        ),
                        source_url=f"https://example.com/documents/{index}",
                        document_identity=f"document:example.com:doc-{index}",
                        document_content_sha256=f"{index:064x}",
                        supported_claim_slots=slots,
                    ),
                ),
            )
        )
    return SectionEvidencePacketSet(
        company_id="00123456",
        evidence_generation_sha256=generation,
        packets=tuple(packets),
    )


def _도식_작가(*, culture_slot: str):
    from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION

    def ask(prompt: str) -> str:
        section_id = next(
            (sid for sid in SECTION_IDS if SECTION_GUIDES[sid] in prompt),
            SECTION_IDS[0],
        )
        문장 = {
            "글": f"테스트 회사의 {section_id} 공식 원문이다.",
            "인용": [str(SECTION_IDS.index(section_id) + 1)],
            "등급": GRADE_CONFIRMED,
            "주장슬롯": (
                culture_slot if section_id == "culture"
                else CLAIM_SLOTS_BY_SECTION[section_id][0]
            ),
        }
        경로표 = (
            [{"칸": list(_문화_도식_칸), "인용": [str(SECTION_IDS.index("culture") + 1)]}]
            if section_id == "culture"
            else []
        )
        return json.dumps(
            {"문장들": [문장], "경로표": 경로표}, ensure_ascii=False
        )

    return ask


def _도식_기록(sink: list[dict]) -> list[dict]:
    from src.shared.report_quality.composition_diagnostics import (
        observed_composition_steps,
    )
    from src.shared.report_quality.composition_diagnostic_constants import (
        DIAGRAM_ROW_COUNT_STEP,
    )

    return [
        step for step in observed_composition_steps(sink)
        if step["step"] == DIAGRAM_ROW_COUNT_STEP
    ]


def test_장별_도식_생성수가_단계에_기록된다() -> None:
    """작가가 낸 줄 수가 «작성» 단계 기록에 장별로 남는다."""

    sink: list[dict] = []
    compose_sections(
        "테스트 회사", (), None,
        _도식_작가(culture_slot="culture:work_principle"),
        section_evidence_packets=_도식_패킷(
            culture_slots=(
                "culture:work_principle", "culture:verified_case",
            )
        ),
        composition_diagnostics=sink,
    )

    기록 = _도식_기록(sink)
    작성 = [step for step in 기록 if step["단계"] == "작성"]
    assert len(작성) == 1
    assert 작성[0]["장별행수"]["culture"] == 1
    assert set(작성[0]["장별행수"]) == set(SECTION_IDS)
    # 원문·칸 내용은 절대 실리지 않는다.
    assert all(칸 not in repr(기록) for 칸 in _문화_도식_칸)


def test_슬롯_미지원_도식행_제외는_기록을_남긴다() -> None:
    """★ 조용한 탈락을 관측 가능하게 만든다.

    8장 「확인된 사례」 칸이 요구하는 의미 칸을 인용 조각이 지원하지 않으면
    그 줄은 사라지는데, 예전에는 안내문도 진단도 남지 않아 «작가가 안 냈는지
    우리가 걸렀는지»를 되짚을 방법이 없었다. 두 단계 기록의 «차이»가 그
    답이다.
    """

    sink: list[dict] = []
    report = compose_sections(
        "테스트 회사", (), None,
        _도식_작가(culture_slot="culture:work_principle"),
        section_evidence_packets=_도식_패킷(
            culture_slots=("culture:work_principle",)
        ),
        composition_diagnostics=sink,
    )

    culture = next(s for s in report.sections if s.section_id == "culture")
    assert culture.flow_rows == (), "이 시험의 전제 — 그 줄은 실제로 사라진다"

    기록 = _도식_기록(sink)
    단계별 = {step["단계"]: step["장별행수"]["culture"] for step in 기록}
    assert 단계별 == {"작성": 1, "장근거정리": 0}
