"""v2 출고 검증 3검사를 못 박는다 (엔진 v2 소단계 3-4a).

★ 여기서 지키는 것:
  ① 정상 렌더 결과는 통과한다 (한국어 산문·URL·날짜는 내부 키로 오인되지 않는다).
  ② 내부 키(`^[a-z][a-z0-9_]*$` 전체일치)가 화면 문자열에 남으면 잡는다.
  ③ 본문 [n] ↔ 부록 번호가 어긋나면 양방향 모두 잡는다.
  ④ 요약이 3~5문장이 아니면 잡는다.
  검사는 이 3개뿐이다 — 문장 내용을 거르는 새 게이트를 만들지 않는다.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from src.features.composer.constants import (
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    NOTICE_INSUFFICIENT_EVIDENCE,
    SECTION_IDS,
)
from src.features.composer.dup_detect import (
    CONFIDENCE_CONFIRMED,
    find_numeric_duplicates,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    PerformanceTable,
)
from src.features.composer.render import render_report
from src.features.composer.validate import (
    V2ValidationError,
    v2_validation_problems,
    validate_v2,
)
from src.features.pipeline.port import SummaryItem


# ══════════════════════════════════════════════════════════
# 시험 재료 — test_render와 같은 재료를 최소로 다시 만든다
# ══════════════════════════════════════════════════════════


def _raw_fragments() -> dict[int, dict[str, Any]]:
    return {
        1: {"종류": "사업내용", "원문": "가나다전자는 반도체 검사 장비 전문기업이다."},
        2: {
            "종류": "홈페이지",
            "원문": "고객의 성공이 최우선 가치다.",
            "출처": "https://www.ganada.example/about",
            "문서일": "2026-08-01",
        },
        3: {
            "종류": "공식 IR",
            "원문": "2025년 매출액은 1,200억원이다.",
            "출처": "https://www.ganada.example/ir.pdf",
            "문서명": "2025 IR자료",
            "원문위치": "PDF p.3 1문단",
        },
    }


def _sentence(
    text: str,
    citations: tuple[str, ...] = (),
    grade: str = GRADE_CONFIRMED,
) -> ComposedSentence:
    return ComposedSentence(text=text, citations=citations, grade=grade)


def _composed_report() -> ComposedReport:
    sections = []
    for section_id in SECTION_IDS:
        if section_id == "identity":
            sections.append(
                ComposedSection(
                    section_id=section_id,
                    sentences=(
                        _sentence("반도체 검사 장비를 주력으로 한다.", ("1", "3")),
                        _sentence(
                            "검사 장비 축이 무게중심으로 읽힌다.",
                            ("1",),
                            GRADE_INTERPRETED,
                        ),
                    ),
                )
            )
        elif section_id == "past_changes":
            sections.append(
                ComposedSection(
                    section_id=section_id,
                    sentences=(
                        _sentence("2025년 매출액은 1,200억원이다.", ("3",)),
                    ),
                )
            )
        else:
            sections.append(
                ComposedSection(
                    section_id=section_id,
                    sentences=(),
                    notice=NOTICE_INSUFFICIENT_EVIDENCE,
                )
            )
    summary = (
        _sentence("반도체 검사 장비 중심의 사업 구조다.", ("1",)),
        _sentence("최근 매출은 성장 흐름이다.", ("3",)),
        _sentence("고객 가치를 앞세운 문화를 내세운다.", ("2",)),
    )
    return ComposedReport(sections=tuple(sections), summary=summary)


def _rendered():
    return render_report(
        "가나다전자",
        _composed_report(),
        _raw_fragments(),
        PerformanceTable(
            caption="3개년 주요 실적",
            headers=("항목", "2023", "2024", "2025"),
            rows=(("매출액", "900", "1,000", "1,200"),),
            unit="억원",
            cite="조각 3·공식 IR",
        ),
        corp_type="상장사",
        as_of_date="2026-08-24",
    )


# ══════════════════════════════════════════════════════════
# ① 정상 통과
# ══════════════════════════════════════════════════════════


def test_정상_렌더_결과는_통과한다():
    rendered = _rendered()
    assert v2_validation_problems(rendered) == ()
    validate_v2(rendered)  # 예외가 없어야 한다


def test_한국어_산문과_URL과_날짜는_내부_키로_오인되지_않는다():
    rendered = _rendered()
    # 부록에는 URL·ISO 날짜·한국어 라벨이 실려 있다 — 전부 통과해야 한다
    assert not [
        problem
        for problem in v2_validation_problems(rendered)
        if "내부 키" in problem
    ]


# ══════════════════════════════════════════════════════════
# ② 내부 키 검출
# ══════════════════════════════════════════════════════════


def test_장_제목에_남은_내부_키를_잡는다():
    rendered = _rendered()
    broken_sections = list(rendered.sections)
    broken_sections[0] = replace(broken_sections[0], title="operating_scope")
    broken = replace(rendered, sections=broken_sections)
    with pytest.raises(V2ValidationError) as caught:
        validate_v2(broken)
    assert any("내부 키 'operating_scope'" in p for p in caught.value.problems)


def test_본문_한_줄이_내부_키_전체일치면_잡는다():
    rendered = _rendered()
    broken_sections = list(rendered.sections)
    section = broken_sections[0]
    broken_sections[0] = replace(
        section,
        prose_lines=[*section.prose_lines, ("revenue_model", "")],
    )
    broken = replace(rendered, sections=broken_sections)
    problems = v2_validation_problems(broken)
    assert any("revenue_model" in p for p in problems)


def test_웹PDF에_보이는_문단의_내부_키도_잡는다():
    rendered = _rendered()
    broken_sections = list(rendered.sections)
    section = broken_sections[0]
    broken_sections[0] = replace(
        section,
        prose_paragraphs=["revenue_model", *section.prose_paragraphs[1:]],
    )
    broken = replace(rendered, sections=broken_sections)

    problems = v2_validation_problems(broken)

    assert any("표시 문단" in p and "revenue_model" in p for p in problems)
    assert any("검증 문장과 화면 문단" in p for p in problems)


def test_검증문장과_화면문단의_글자가_다르면_출고를_막는다():
    rendered = _rendered()
    broken_sections = list(rendered.sections)
    section = broken_sections[0]
    paragraphs = list(section.prose_paragraphs)
    paragraphs[0] = "검사를 받지 않은 다른 문장이다."
    broken_sections[0] = replace(section, prose_paragraphs=paragraphs)
    broken = replace(rendered, sections=broken_sections)

    with pytest.raises(V2ValidationError) as caught:
        validate_v2(broken)

    assert any(
        "검증 문장과 화면 문단" in problem for problem in caught.value.problems
    )


def test_옛저장본의_빈_화면문단은_검증문장_표시로_호환한다():
    rendered = _rendered()
    sections = [
        replace(section, prose_paragraphs=[])
        for section in rendered.sections
    ]

    assert not [
        problem
        for problem in v2_validation_problems(replace(rendered, sections=sections))
        if "검증 문장과 화면 문단" in problem
    ]


def test_부록_라벨의_내부_키도_잡는다():
    rendered = _rendered()
    broken_citations = list(rendered.citations)
    broken_citations[0] = replace(broken_citations[0], label="official_value")
    broken = replace(rendered, citations=broken_citations)
    problems = v2_validation_problems(broken)
    assert any("official_value" in p for p in problems)


# ══════════════════════════════════════════════════════════
# ③ 본문 [n] ↔ 부록 1:1
# ══════════════════════════════════════════════════════════


def test_본문이_인용한_번호가_부록에_없으면_잡는다():
    rendered = _rendered()
    # 부록에서 [1]을 빼면 본문 [1]이 갈 곳을 잃는다
    broken = replace(
        rendered,
        citations=[s for s in rendered.citations if s.number != 1],
    )
    with pytest.raises(V2ValidationError) as caught:
        validate_v2(broken)
    assert any(
        "본문이 인용한 번호가 부록에 없습니다" in p and "[1]" in p
        for p in caught.value.problems
    )


def test_아무_문장도_인용하지_않는_부록_번호를_잡는다():
    rendered = _rendered()
    extra = replace(rendered.citations[0], number=42, source_id="v2-frag-42")
    broken = replace(rendered, citations=[*rendered.citations, extra])
    problems = v2_validation_problems(broken)
    assert any("본문 어디에서도 인용하지 않았습니다" in p and "[42]" in p for p in problems)


def test_부록_번호_중복을_잡는다():
    rendered = _rendered()
    duplicate = replace(rendered.citations[0], label="같은 번호 다른 줄")
    broken = replace(rendered, citations=[*rendered.citations, duplicate])
    problems = v2_validation_problems(broken)
    assert any("중복" in p for p in problems)


def test_실적표_cite_번호도_본문_인용으로_센다():
    rendered = _rendered()
    # 조각 3을 본문 문장 인용에서 지워도, 4장 실적표 cite(조각 3)가 남아 있으면
    # 부록 [3]은 «인용 없는 번호»가 아니어야 한다.
    stripped_sections = []
    for section in rendered.sections:
        stripped_sections.append(
            replace(
                section,
                prose_lines=[
                    (text.replace("[3]", "").rstrip(), cite)
                    for text, cite in section.prose_lines
                ],
            )
        )
    stripped_summary = [
        SummaryItem(text=item.text.replace("[3]", "").rstrip())
        for item in rendered.summary_items
    ]
    stripped = replace(
        rendered, sections=stripped_sections, summary_items=stripped_summary
    )
    problems = v2_validation_problems(stripped)
    assert not any("[3]" in p for p in problems)


# ══════════════════════════════════════════════════════════
# ④ 요약 3~5문장
# ══════════════════════════════════════════════════════════


def test_요약이_3문장_미만이면_잡는다():
    rendered = _rendered()
    broken = replace(rendered, summary_items=rendered.summary_items[:2])
    with pytest.raises(V2ValidationError) as caught:
        validate_v2(broken)
    assert any("핵심 요약" in p and "2문장" in p for p in caught.value.problems)


def test_요약이_5문장을_넘으면_잡는다():
    rendered = _rendered()
    padded = [
        *rendered.summary_items,
        SummaryItem(text="추가 요약 넷째. [1]"),
        SummaryItem(text="추가 요약 다섯째. [1]"),
        SummaryItem(text="추가 요약 여섯째. [1]"),
    ]
    broken = replace(rendered, summary_items=padded)
    problems = v2_validation_problems(broken)
    assert any("핵심 요약" in p and "6문장" in p for p in problems)


def test_검사는_사유를_한꺼번에_모아_알린다():
    rendered = _rendered()
    broken_sections = list(rendered.sections)
    broken_sections[0] = replace(broken_sections[0], title="identity")
    broken = replace(
        rendered,
        sections=broken_sections,
        summary_items=rendered.summary_items[:1],
    )
    with pytest.raises(V2ValidationError) as caught:
        validate_v2(broken)
    assert len(caught.value.problems) >= 2  # 내부 키 + 요약 문장 수


# ══════════════════════════════════════════════════════════
# ⑤ 중복 검출은 출고를 막지 않는다 (엔진 v2 인수 작업 — 아직 «찾기»만 한다)
# ══════════════════════════════════════════════════════════


def test_출고를_막지_않는다_중복_검출은_배선되지_않았다():
    """dup_detect가 잡아내는 중복이 있어도 validate_v2는 그대로 통과시켜야 한다.

    ★ 이 시험 재료(_rendered)는 4장 본문 문장 「2025년 매출액은 1,200억원이다」와
      같은 장의 실적표 행(매출액·2025·1,200)이 값+단위+기간까지 겹치는
      «실제» 확정급 중복을 담고 있다(정본 「같은 사실을 문장·표로 반복하지
      않는다」 위반 후보). find_numeric_duplicates는 이를 잡아내야 하고,
      동시에 validate_v2는 그것과 무관하게 통과해야 한다 — 검출과 차단이
      아직 분리돼 있다는 사실을 코드로 못 박는다. 나중에 누가 실수로 두
      함수를 이어 붙이면 이 시험이 바로 빨간불이 된다.
    """
    rendered = _rendered()
    findings = find_numeric_duplicates(rendered)
    assert findings != ()  # 검출은 된다
    assert any(f.confidence == CONFIDENCE_CONFIRMED for f in findings)
    validate_v2(rendered)  # 그래도 출고 검증은 통과한다(예외 없음)
