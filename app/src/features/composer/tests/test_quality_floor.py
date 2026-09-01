"""엔진 v2 품질 하한 시험 (실행계획 05장 소단계 4-A) — 무과금.

★ 방향이 기존 시험과 반대다: 기존 시험은 「게이트가 잘 막는가」를 봤고,
  여기는 「골든 샘플 수준의 입력이 들어오면 보고서가 완성 하한(기준문서
  03장 6절)을 만족한 채 나오는가」를 본다. 시스템이 좋은 입력을 훼손하면
  이 시험이 깨진다.
★ 입력은 골든 샘플 보고서에서 발췌해 만든 기존
  fixture(jyp_fragments.json·jyp_ask_responses.json)를 그대로 재사용한다.
★ test_e2e_offline.py와의 역할 구분 — 그쪽은 real.py 배선(수집→과금→PDF
  바이트)이 끝까지 이어지는지를 못 박고, 여기는 composer 진입 함수(run_v2)
  산출물이 완성 하한 조항 하나하나를 만족하는지를 못 박는다.
  같은 fixture를 쓰지만 단정 항목이 겹치지 않는다.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Final

import pytest

from src.features.composer.constants import (
    CITATION_STYLE_INLINE,
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    NOTICE_COMPOSE_FAILED,
    NOTICE_INSUFFICIENT_EVIDENCE,
    SECTION_GUIDES,
    SECTION_IDS,
)
from src.features.composer.diagram_check import FLOW_REVIEW_PROMPT_HEADER
from src.features.composer.logic import (
    SUMMARY_MAX_SENTENCES,
    SUMMARY_MIN_SENTENCES,
    SUMMARY_PROMPT_HEADER,
)
from src.features.composer.pipeline import V2RunOutput, run_v2
from src.features.composer.render import INTERPRETATION_MARKER
from src.features.composer.verify import (
    NOTICE_ALL_SENTENCES_REJECTED,
    REVIEW_PROMPT_HEADER,
    REWRITE_PROMPT_HEADER,
)
from src.features.pipeline.port import Report, ReportSection
from src.features.provenance.sources import visible_citations
from src.shared.report_quality.assessment import has_public_numeric_token

COMPANY_NAME: Final[str] = "제이와이피엔터테인먼트"

_FIXTURE_DIR: Final[Path] = Path(__file__).resolve().parent / "fixtures"
_FRAGMENTS_FIXTURE: Final[dict[str, Any]] = json.loads(
    (_FIXTURE_DIR / "jyp_fragments.json").read_text(encoding="utf-8")
)
_RESPONSES_FIXTURE: Final[dict[str, Any]] = json.loads(
    (_FIXTURE_DIR / "jyp_ask_responses.json").read_text(encoding="utf-8")
)

# ── 완성 하한 값 — 기준문서 03장 6절(G4)을 숫자 그대로 옮긴 것.
#    ★ 2026-09-01 — 한 번은 이 숫자를 생산 상수 import로 바꿨다가 되돌렸다.
#    이유: 시험을 생산 상수에 묶으면 «생산 상수가 바뀌면 시험도 같이
#    깨진다»는 안전선이 성립하는 것처럼 보이지만, 실제로는 «값이 낮아지는»
#    방향은 못 잡는다 — 누가 40을 17로 낮춰도 import한 시험은 여전히
#    통과한다(순환 검증). 여기 숫자는 구현에서 도출한 매직 넘버가 아니라
#    시험이 독립적으로 못 박는 «제품 약속»이다. 이 시험의 존재 이유가
#    바로 «구현이 이 약속에서 벗어났는지 재는 것»이므로, 일반적인
#    매직 넘버 금지 규칙(rules/general.md)의 의도된 예외다.
#: 실질 문장 40개 이상
MIN_SUBSTANTIVE_SENTENCES: Final[int] = 40
#: «확인» 등급이 전체의 50% 이상
MIN_CONFIRMED_RATIO: Final[float] = 0.5
#: 출처 8건 이상 인용
MIN_CITED_SOURCES: Final[int] = 8
#: 안내문만 있는 장 ≤ 1개
MAX_NOTICE_ONLY_SECTIONS: Final[int] = 1
#: 1~8장은 반드시 실질 내용이 있어야 한다 (9장은 성립 시에만)
REQUIRED_DISPLAY_NUMBERS: Final[frozenset[str]] = frozenset(
    str(number) for number in range(1, 9)
)

#: 화면 노출이 금지된 영문 내부 키 모양 — 05장 4-A-4의 판정 그대로
_INTERNAL_KEY_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*$")
#: 본문 문장 끝의 `[n]` 인용 표기 (render.sentence_display_text가 만드는 모양)
_CITATION_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"\[(\d+)\]")
#: 검수 프롬프트에서 대조 문장 번호를 읽는 모양 (verify._build_review_prompt)
_REVIEW_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"\[(\d+)\] \(등급: [^,\n]+, 인용:"
)
_FLOW_NUMBER_RE: Final[re.Pattern[str]] = re.compile(
    r"^\[(\d+)\] 경로\(JSON 배열\):", re.MULTILINE
)

#: 시스템이 붙일 수 있는 안내문 전부 — «실질 문장» 집계에서 뺀다
_NOTICE_TEXTS: Final[frozenset[str]] = frozenset(
    {
        NOTICE_COMPOSE_FAILED,
        NOTICE_INSUFFICIENT_EVIDENCE,
        NOTICE_ALL_SENTENCES_REJECTED,
    }
)


# ══════════════════════════════════════════════════════════
# fixture 읽기 도우미 — 숫자는 전부 fixture 실측으로 만든다 (매직 넘버 금지)
# ══════════════════════════════════════════════════════════


def _fixture_fragments() -> dict[int, dict[str, str]]:
    """fixture JSON을 real.py 원시 조각 dict[int, dict] 모양으로 바꾼다."""
    return {
        int(number): dict(fields)
        for number, fields in _FRAGMENTS_FIXTURE.items()
        if number.isdigit()
    }


def _fixture_body_sentences() -> list[dict[str, Any]]:
    """fixture가 약속한 본문 초안 문장 전부 (9개 장 순회)."""
    return [
        sentence
        for payload in _RESPONSES_FIXTURE["장별_응답"].values()
        for sentence in payload["문장들"]
    ]


def _fixture_summary_sentences() -> list[dict[str, Any]]:
    """fixture가 약속한 핵심 요약 초안 문장 전부."""
    return list(_RESPONSES_FIXTURE["핵심요약_응답"]["문장들"])


# ══════════════════════════════════════════════════════════
# 가짜 작가·검수 — AI·네트워크 호출 0회
# ══════════════════════════════════════════════════════════


class _GoldenWriter:
    """골든 샘플 발췌 fixture 응답을 그대로 돌려주는 가짜 작가."""

    def __call__(self, prompt: str) -> str:
        if SUMMARY_PROMPT_HEADER in prompt:
            return json.dumps(
                _RESPONSES_FIXTURE["핵심요약_응답"], ensure_ascii=False
            )
        for section_id in SECTION_IDS:
            if SECTION_GUIDES[section_id] in prompt:
                return json.dumps(
                    _RESPONSES_FIXTURE["장별_응답"][section_id],
                    ensure_ascii=False,
                )
        raise AssertionError("작가가 알 수 없는 프롬프트를 받았다")


class _AllTrueReviewer:
    """대조 문장 전부를 «참»으로 판정하는 가짜 검수 — 하한 시험의 기준선.

    재작성 요청은 기록만 한다 — 전부 «참»이면 재작성은 0회여야 하고,
    0회가 아니면 시스템이 좋은 입력을 훼손했다는 신호다.
    """

    def __init__(self) -> None:
        self.rewrite_prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        if REWRITE_PROMPT_HEADER in prompt:
            self.rewrite_prompts.append(prompt)
            return ""
        if FLOW_REVIEW_PROMPT_HEADER in prompt:
            numbers = [int(value) for value in _FLOW_NUMBER_RE.findall(prompt)]
            return json.dumps(
                {"판정": [{"번호": number, "결과": "참"} for number in numbers]},
                ensure_ascii=False,
            )
        assert REVIEW_PROMPT_HEADER in prompt, "검수가 알 수 없는 프롬프트를 받았다"
        numbers = [int(value) for value in _REVIEW_NUMBER_RE.findall(prompt)]
        return json.dumps(
            {"판정": [{"번호": number, "결과": "참"} for number in numbers]},
            ensure_ascii=False,
        )


@pytest.fixture(scope="module")
def floor_run() -> tuple[V2RunOutput, _AllTrueReviewer]:
    """골든 fixture 입력으로 composer 전체(run_v2)를 한 번만 돌린다."""
    reviewer = _AllTrueReviewer()
    output = run_v2(
        COMPANY_NAME,
        _fixture_fragments(),
        # 실적표 없는 회사도 하한을 만족해야 한다 — 표 경로는 e2e 시험이 본다
        None,
        writer_ask=_GoldenWriter(),
        reviewer_ask=reviewer,
        corp_type="상장사",
        as_of_date="2026-08-24",
        # ★ 이 시험은 «문장마다 근거가 붙는가»를 본다. 화면 기본값은 절충안
        #   (같은 출처 묶음은 마지막에만 번호)이라 표기 방식을 여기서 못 박는다
        #   — 기본값이 또 바뀌어도 이 시험의 의도가 흔들리지 않게.
        citation_style=CITATION_STYLE_INLINE,
    )
    return output, reviewer


# ══════════════════════════════════════════════════════════
# 렌더 결과 읽기 도우미
# ══════════════════════════════════════════════════════════


def _substantive_texts(section: ReportSection) -> list[str]:
    """안내문을 뺀 «실질 문장» 렌더 텍스트만 골라낸다."""
    return [
        text for text, _cite in section.prose_lines if text not in _NOTICE_TEXTS
    ]


def _all_sentence_texts(report: Report) -> list[str]:
    """본문 실질 문장 + 핵심 요약의 렌더 텍스트 전부."""
    texts = [
        text
        for section in report.sections
        for text in _substantive_texts(section)
    ]
    texts.extend(item.text for item in report.summary_items)
    return texts


def _displayed_strings(report: Report) -> list[str]:
    """웹·PDF가 화면에 찍는 문자열 전부 — 내부 키 토큰 검사 대상."""
    values: list[str] = [
        report.company,
        report.corp_type,
        report.analysis_period,
        report.latest_performance_period,
    ]
    for section in report.sections:
        values.append(section.title)
        values.append(section.tag)
        values.extend(text for text, _cite in section.prose_lines)
        for table in section.tables:
            values.append(table.caption)
            values.append(table.display_unit)
            values.extend(table.headers)
            for row in table.rows:
                values.extend(row)
    values.extend(item.text for item in report.summary_items)
    for source in visible_citations(report.citations):
        values.extend(
            [
                source.label,
                source.title,
                source.publisher,
                source.location,
                source.collected_at,
                source.url,
            ]
        )
    return [value for value in values if value]


# ══════════════════════════════════════════════════════════
# 4-A-1. 장 구조 — 핵심 요약 + 1~8장 존재, 안내문-전용 장 ≤ 1개
# ══════════════════════════════════════════════════════════


def test_핵심_요약과_1_8장이_실질_내용으로_존재한다(
    floor_run: tuple[V2RunOutput, _AllTrueReviewer],
) -> None:
    output, _reviewer = floor_run
    report = output.report

    assert report.summary_items, "핵심 요약이 없다"
    by_display = {section.display_number: section for section in report.sections}
    assert REQUIRED_DISPLAY_NUMBERS <= set(by_display)
    for number in sorted(REQUIRED_DISPLAY_NUMBERS, key=int):
        assert _substantive_texts(by_display[number]), f"{number}장이 안내문뿐이다"

    notice_only = [
        section.cell
        for section in report.sections
        if not _substantive_texts(section)
    ]
    # 하한: 안내문-전용 장 ≤ 1개. 골든 입력에서는 0개여야 훼손이 없는 것이다.
    assert len(notice_only) <= MAX_NOTICE_ONLY_SECTIONS, notice_only
    assert notice_only == [], notice_only


# ══════════════════════════════════════════════════════════
# 4-A-2. 요약 3~5문장 + 본문 재탕 아님
# ══════════════════════════════════════════════════════════


def test_요약은_3에서_5문장이고_본문_재탕이_아니다(
    floor_run: tuple[V2RunOutput, _AllTrueReviewer],
) -> None:
    output, _reviewer = floor_run
    report = output.report

    assert (
        SUMMARY_MIN_SENTENCES
        <= len(report.summary_items)
        <= SUMMARY_MAX_SENTENCES
    )
    body_texts = {
        text
        for section in report.sections
        for text in _substantive_texts(section)
    }
    summary_texts = [item.text for item in report.summary_items]
    # 하한: 요약 전 문장이 본문과 동일하면 «재탕» — 실패다 (05장 4-A-2)
    assert not all(text in body_texts for text in summary_texts)
    # 옛 골든 fixture의 AI 요약 수치는 의미 결속이 없으므로 새 생성 안전
    # 경계가 제외하고, 부족분은 이미 검증된 본문으로 보충할 수 있다. 보충을
    # 금지해 미결속 수치를 되살리는 것보다 최종 요약에 숫자가 없는지가 정본이다.
    assert all(
        not has_public_numeric_token(_CITATION_MARKER_RE.sub("", text))
        for text in summary_texts
    )


# ══════════════════════════════════════════════════════════
# 4-A-3. 모든 문장에 인용 또는 «해석» 라벨
# ══════════════════════════════════════════════════════════


def test_모든_문장에_인용_또는_해석_라벨이_있다(
    floor_run: tuple[V2RunOutput, _AllTrueReviewer],
) -> None:
    output, _reviewer = floor_run
    for text in _all_sentence_texts(output.report):
        has_citation = bool(_CITATION_MARKER_RE.search(text))
        has_marker = text.endswith(INTERPRETATION_MARKER)
        assert has_citation or has_marker, f"인용도 라벨도 없는 문장: {text}"


# ══════════════════════════════════════════════════════════
# 4-A-4. 렌더 텍스트에 영문 내부 키 전체일치 토큰 0건
# ══════════════════════════════════════════════════════════


def test_렌더_텍스트에_영문_내부_키_전체일치_토큰이_없다(
    floor_run: tuple[V2RunOutput, _AllTrueReviewer],
) -> None:
    output, _reviewer = floor_run
    leaked = [
        token
        for value in _displayed_strings(output.report)
        for token in value.split()
        if _INTERNAL_KEY_TOKEN_RE.fullmatch(token)
    ]
    assert leaked == [], leaked


# ══════════════════════════════════════════════════════════
# 4-A-5. 부록 번호와 본문 [n] 인용의 1:1 매핑 + 출처 8건 이상
# ══════════════════════════════════════════════════════════


def test_부록_번호와_본문_인용이_1대1이다(
    floor_run: tuple[V2RunOutput, _AllTrueReviewer],
) -> None:
    output, _reviewer = floor_run
    report = output.report

    body_numbers: set[int] = set()
    for text in _all_sentence_texts(report):
        body_numbers.update(
            int(value) for value in _CITATION_MARKER_RE.findall(text)
        )
    appendix_numbers = [
        source.number for source in visible_citations(report.citations)
    ]
    # 부록 번호는 중복 없이, 본문이 인용한 번호 집합과 정확히 같다
    assert len(appendix_numbers) == len(set(appendix_numbers))
    assert set(appendix_numbers) == body_numbers
    # 하한(기준문서 6절): 출처 8건 이상 인용
    assert len(appendix_numbers) >= MIN_CITED_SOURCES


# ══════════════════════════════════════════════════════════
# 4-A-6. 기준문서 6절 하한 — fixture가 만족하고, 시스템이 훼손하지 않는다
# ══════════════════════════════════════════════════════════


def test_기준문서_하한은_낮추지_않고_미결속_수치를_제외한_결과는_partial이다(
    floor_run: tuple[V2RunOutput, _AllTrueReviewer],
) -> None:
    output, reviewer = floor_run
    report = output.report

    # ① fixture(입력) 자체가 하한을 만족한다 — 주장 대신 실측
    fixture_body = _fixture_body_sentences()
    fixture_all = fixture_body + _fixture_summary_sentences()
    fixture_confirmed = sum(
        1 for sentence in fixture_all if sentence["등급"] == GRADE_CONFIRMED
    )
    assert len(fixture_body) >= MIN_SUBSTANTIVE_SENTENCES
    assert fixture_confirmed / len(fixture_all) >= MIN_CONFIRMED_RATIO

    # ② 숫자가 없는 문장은 전부 보존한다. 2026-08-29 사용자 결정 ③ 이후에는
    #    «검사를 이미 두 번 통과한» 수치 문장(등급 확인 + 인용 있음 + 검수 AI가
    #    참으로 판정해 verified)까지 살아남는다 — 구조화 근거(NumericBinding)를
    #    요구하는 옛 규칙은 «해석» 등급 수치 문장에만 남는다(해석은 사실
    #    주장이 아니라 애초에 구조화 근거를 만들 길이 없다). 주장이 아니라
    #    fixture 등급 실측으로 확인한다.
    body_texts = [
        text
        for section in report.sections
        for text in _substantive_texts(section)
    ]
    unbound_numeric_body = [
        sentence
        for sentence in fixture_body
        if has_public_numeric_token(sentence["글"])
    ]
    assert unbound_numeric_body
    interpreted_unbound_numeric_body = [
        sentence
        for sentence in unbound_numeric_body
        if sentence["등급"] == GRADE_INTERPRETED
    ]
    confirmed_unbound_numeric_body = [
        sentence
        for sentence in unbound_numeric_body
        if sentence["등급"] == GRADE_CONFIRMED
    ]
    # 이 fixture의 «확인» 등급 수치 문장은 전부 인용이 있다 — 세 조건 중
    # 인용 조건은 걸리지 않고 등급(해석)만 걸린다는 것을 실측으로 못 박는다.
    assert len(confirmed_unbound_numeric_body) + len(
        interpreted_unbound_numeric_body
    ) == len(unbound_numeric_body)
    assert all(sentence["인용"] for sentence in confirmed_unbound_numeric_body)
    # 기존 장 간 중복 소유권 규칙이 숫자 없는 중복 한 문장도 별도로 모은다.
    DEDUPE_MOVED_IN_FIXTURE = 1
    assert len(body_texts) == (
        len(fixture_body)
        - len(interpreted_unbound_numeric_body)
        - DEDUPE_MOVED_IN_FIXTURE
    )
    assert SUMMARY_MIN_SENTENCES <= len(report.summary_items) <= SUMMARY_MAX_SENTENCES
    assert output.composed_sentences == len(fixture_all)
    assert output.verified_sentences == len(body_texts) + len(report.summary_items)
    assert reviewer.rewrite_prompts == []

    # ③ 하한 자체(본문 40문장·확인 비율 50%)는 낮추지 않는다 — 안전선.
    #    2026-08-29 사용자 결정 ③ 이후 이 fixture는 «회복»해 두 지표 모두
    #    하한을 넘긴다. 그런데도 등급은 여전히 PARTIAL이다 — 이유는 이 시험이
    #    다루는 게이트(수치 문장 결속)가 아니라 «구조화 사실(FactRecord) 하한»
    #    이라는 별도 게이트다. 이 시험은 실적표(table=None)를 일부러 안 주므로
    #    report.fact_records가 0건이라 그 게이트를 못 채운다 — 표가 있는
    #    경로는 test_e2e_offline.py가 본다(assessment.py의 substantive_claims는
    #    fact_records만 세지, 렌더된 문장 수를 세지 않는다).
    rendered = _all_sentence_texts(report)
    rendered_confirmed = sum(
        1 for text in rendered if not text.endswith(INTERPRETATION_MARKER)
    )
    assert len(body_texts) >= MIN_SUBSTANTIVE_SENTENCES, "회복 확인: 40문장 하한을 넘겼다"
    assert report.grade.value == "부분 완성"  # ← 안전선: 등급은 여전히 PARTIAL이다
    assert any("숫자·날짜 문장" in reason for reason in report.shortfall_reasons)
    report_facts = output.quality_observation.substantive_claims
    report_sources = output.quality_observation.document_sources
    # ★ 2026-08-29 — 머리말에서 «내부 임계값»(40개·8개)을 뺐다. 그 숫자는 화면
    #   어디에도 설명이 없어 「40점 만점에 3점」으로 오독되고, 독자가 할 수 있는
    #   일도 없다. 대신 «실제 개수»는 그대로 싣는다 — 그게 신뢰도 판단의 근거다.
    #   ⚠️ 여기서 지키는 것은 「임계값이 보이나」가 아니라 「사실이 남았나」다.
    assert any(
        "출처와 뜻이 함께 확인된 사실" in reason
        and f"{report_facts}건" in reason
        for reason in report.shortfall_reasons
    ), "★ 확인된 사실 개수가 독자에게 안 보인다"
    assert any(
        "원문 문서" in reason and f"{report_sources}개" in reason
        for reason in report.shortfall_reasons
    ), "★ 참고한 원문 문서 개수가 독자에게 안 보인다"
    assert rendered_confirmed / len(rendered) >= MIN_CONFIRMED_RATIO, (
        "회복 확인: 확인 비율도 50% 하한을 넘겼다"
    )
