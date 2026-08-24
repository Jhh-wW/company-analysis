"""v2 출고 검증 — 딱 3검사 (엔진 v2 소단계 3-4a, 04장 3-4절 2항).

★ v1 `validate_publishable`은 v2 경로에서 부르지 않는다. 여기의 검사는:
  ① 렌더 텍스트에 영문 내부 키(`^[a-z][a-z0-9_]*$` 전체일치) 노출 0건
     — 단계1이 허용한 유일한 내부 키 검출과 같은 모양이다.
  ② 본문 인용 번호 `[n]`과 출처 부록의 1:1 매핑
  ③ 핵심 요약 3~5문장 존재
  이 3개 «만» 본다. 문장 내용을 어휘·마커·어미로 거르는 검사는 없다
  (01_원칙과_금지.md — 새 닫힌 정규식 게이트 금지).
★ ①의 정규식은 «값 전체»가 영문 snake_case 토큰일 때만 걸린다 — 한국어
  산문·캡션·URL·날짜는 절대 걸리지 않는다 (section_content.py의
  _INTERNAL_KEY_SHAPE와 같은 판정).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from typing import Final

from src.core.citations import citation_number
from src.features.composer.logic import (
    SUMMARY_MAX_SENTENCES,
    SUMMARY_MIN_SENTENCES,
)
from src.features.pipeline.port import Report
from src.features.provenance.sources import visible_citations

#: 조립·렌더 결함이 남기는 영문 내부 키 모양 — 값 «전체» 일치만 본다.
INTERNAL_KEY_SHAPE: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*$")

#: 본문 문장 끝의 `[n]` 인용 번호 표기 (render.sentence_display_text가 만드는 모양)
CITATION_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"\[(\d+)\]")


class V2ValidationError(ValueError):
    """v2 출고 검증 실패 — 사유 전부를 한국어로 담는다."""

    def __init__(self, problems: Iterable[str]) -> None:
        self.problems: tuple[str, ...] = tuple(problems)
        super().__init__(
            "v2 출고 검증 실패: " + " / ".join(self.problems)
        )


# ══════════════════════════════════════════════════════════
# ① 내부 키 노출
# ══════════════════════════════════════════════════════════


def _rendered_display_values(report: Report) -> Iterator[tuple[str, str]]:
    """웹·PDF가 실제로 «화면에 찍는» 문자열만 (위치, 값)으로 편다.

    section.cell·source_id 같은 내부 식별자는 화면에 글로 찍히지 않으므로
    검사 대상이 아니다 — 그것까지 걸면 정상 동작이 결함으로 보인다.
    """
    yield ("회사명", report.company)
    yield ("회사 유형", report.corp_type)
    yield ("분석 범위", report.analysis_period)
    yield ("최신 실적 기간", report.latest_performance_period)
    for reason in report.shortfall_reasons:
        yield ("미제공 사유", reason)
    for section in report.sections:
        where = f"{section.display_number or section.cell}장"
        yield (f"{where} 제목", section.title)
        yield (f"{where} 태그", section.tag)
        for text, _cite in section.prose_lines:
            yield (f"{where} 본문", text)
        for guidance in section.guidance_lines:
            yield (f"{where} 안내", guidance)
        for table in section.tables:
            yield (f"{where} 표 캡션", table.caption)
            yield (f"{where} 표 단위", table.display_unit)
            for header in table.headers:
                yield (f"{where} 표 머리글", header)
            for row in table.rows:
                for cell in row:
                    yield (f"{where} 표 값", cell)
    for index, item in enumerate(report.summary_items, start=1):
        yield (f"핵심 요약 {index}번", item.text)
    for source in visible_citations(report.citations):
        where = f"부록 [{source.number}]"
        yield (f"{where} 자료명", source.label)
        yield (f"{where} 문서 제목", source.title)
        yield (f"{where} 발행처", source.publisher)
        yield (f"{where} 원문 위치", source.location)
        yield (f"{where} 자료 분류", source.source_type)
        yield (f"{where} 사실 상태", source.fact_status)
        yield (f"{where} 도메인", source.domain)


def _internal_key_problems(report: Report) -> list[str]:
    return [
        f"{where}에 내부 키 '{value.strip()}'가 노출됐습니다"
        for where, value in _rendered_display_values(report)
        if INTERNAL_KEY_SHAPE.fullmatch(str(value or "").strip())
    ]


# ══════════════════════════════════════════════════════════
# ② 본문 인용 번호 ↔ 부록 1:1
# ══════════════════════════════════════════════════════════


def _cited_numbers_in_body(report: Report) -> set[int]:
    """본문·요약·표가 실제로 표시하는 인용 번호 전부."""
    numbers: set[int] = set()
    for section in report.sections:
        for text, cite in section.prose_lines:
            numbers.update(int(value) for value in CITATION_MARKER_RE.findall(text))
            cite_number = citation_number(cite)
            if cite_number:
                numbers.add(int(cite_number))
        for table in section.tables:
            cite_number = citation_number(table.cite)
            if cite_number:
                numbers.add(int(cite_number))
    for item in report.summary_items:
        numbers.update(int(value) for value in CITATION_MARKER_RE.findall(item.text))
    return numbers


def _citation_mapping_problems(report: Report) -> list[str]:
    problems: list[str] = []
    body_numbers = _cited_numbers_in_body(report)
    appendix_numbers: list[int] = [
        source.number for source in visible_citations(report.citations)
    ]
    duplicated = sorted(
        {number for number in appendix_numbers if appendix_numbers.count(number) > 1}
    )
    if duplicated:
        problems.append(
            "부록에 같은 번호가 중복됐습니다: "
            + ", ".join(f"[{number}]" for number in duplicated)
        )
    appendix_set = set(appendix_numbers)
    missing = sorted(body_numbers - appendix_set)
    if missing:
        problems.append(
            "본문이 인용한 번호가 부록에 없습니다: "
            + ", ".join(f"[{number}]" for number in missing)
        )
    unused = sorted(appendix_set - body_numbers)
    if unused:
        problems.append(
            "부록에 있는 번호를 본문 어디에서도 인용하지 않았습니다: "
            + ", ".join(f"[{number}]" for number in unused)
        )
    return problems


# ══════════════════════════════════════════════════════════
# ③ 핵심 요약 존재
# ══════════════════════════════════════════════════════════


def _summary_problems(report: Report) -> list[str]:
    count = len(report.summary_items)
    if SUMMARY_MIN_SENTENCES <= count <= SUMMARY_MAX_SENTENCES:
        return []
    return [
        f"핵심 요약이 {SUMMARY_MIN_SENTENCES}~{SUMMARY_MAX_SENTENCES}문장이어야 "
        f"하는데 {count}문장입니다"
    ]


# ══════════════════════════════════════════════════════════
# 진입 함수
# ══════════════════════════════════════════════════════════


def v2_validation_problems(rendered: Report) -> tuple[str, ...]:
    """3검사 결과를 사유 목록으로 돌려준다. 비어 있으면 통과다."""
    return tuple(
        [
            *_internal_key_problems(rendered),
            *_citation_mapping_problems(rendered),
            *_summary_problems(rendered),
        ]
    )


def validate_v2(rendered: Report) -> None:
    """v2 출고 검증 — 실패하면 한국어 사유가 담긴 V2ValidationError를 던진다.

    Args:
        rendered: render.render_report가 만든 pipeline Report.

    Raises:
        V2ValidationError: ①내부 키 노출 ②인용-부록 매핑 불일치
            ③요약 문장 수 위반 — 사유는 예외의 ``problems``에 전부 담긴다.
    """
    problems = v2_validation_problems(rendered)
    if problems:
        raise V2ValidationError(problems)
