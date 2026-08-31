"""v2 출고 검증 — 딱 3검사 (엔진 v2 소단계 3-4a, 04장 3-4절 2항).

★ v1 `validate_publishable`은 v2 경로에서 부르지 않는다. 여기의 검사는:
  ① 렌더 텍스트에 영문 내부 키(`^[a-z][a-z0-9_]*$` 전체일치) 노출 0건
     — 단계1이 허용한 유일한 내부 키 검출과 같은 모양이다.
  ② 본문 인용 번호 `[n]`과 출처 부록의 1:1 매핑
  ③ 핵심 요약 3~5문장 존재
  ④ 검사받은 문장과 웹·PDF 표시 문단의 글자 동일성
  이 4개 «만» 본다. ④는 문장 내용을 평가하지 않고, 이미 검사받은 글자를
  표시 단계가 다른 글자로 바꾸지 않았는지만 확인한다. 문장 내용을 어휘·마커·
  어미로 거르는 검사는 없다
  (01_원칙과_금지.md — 새 닫힌 정규식 게이트 금지).
★ ①의 정규식은 «값 전체»가 영문 snake_case 토큰일 때만 걸린다 — 한국어
  산문·캡션·URL·날짜는 절대 걸리지 않는다 (section_content.py의
  _INTERNAL_KEY_SHAPE와 같은 판정).

★ 중복 검사(정본 「출력 전 중복 검사 게이트」)는 여기 없다 — v2에는 아직
  «찾아내는» 단계만 있다. `composer.dup_detect.find_numeric_duplicates`가
  같은 수치가 두 장·두 형식(문장·표)에 있는지 후보를 찾아 «확정/의심»으로
  분류해 돌려주지만, 예외를 던지지 않고 이 파일의 `validate_v2`에도 배선돼
  있지 않다. 실제 보고서로 오탐률을 먼저 사람이 확인한 뒤 막을지 정하기
  위함이다(잘못 막으면 정상 보고서까지 출고가 안 나간다 — 중복이 나가는
  것보다 나쁘다). `composer/tests/test_validate.py`의
  `test_출고를_막지_않는다_중복_검출은_배선되지_않았다`가 이 사실을 못 박는다.
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
    """웹·PDF 공개 후보 문자열을 (위치, 값)으로 편다.

    새 보고서는 ``prose_paragraphs``를 실제로 표시하고, 옛 저장본은
    ``prose_lines``로 되돌아간다. 두 목록은 아래 동일성 검사로 결속하지만
    어느 한쪽에 내부 키가 남아도 조용히 통과하지 않도록 둘 다 확인한다.
    section.cell·source_id 같은 내부 식별자는 화면 글자가 아니므로 제외한다.
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
            yield (f"{where} 검증 문장", text)
        for paragraph in section.prose_paragraphs:
            yield (f"{where} 표시 문단", paragraph)
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


def _paragraph_projection_problems(report: Report) -> list[str]:
    """검사받은 문장과 실제 표시 문단이 같은 글자인지 확인한다.

    문단 경계는 가독성을 위한 표시 정보이므로 달라도 된다. 문단을 순서대로
    이어 붙인 전체 글자만 문장 정본과 정확히 같아야 한다. 빈 문단 목록은 이
    필드가 생기기 전 저장본이며 PDF·웹 모두 ``prose_lines``로 표시한다.
    """

    problems: list[str] = []
    for section in report.sections:
        if not section.prose_paragraphs:
            continue
        sentence_text = " ".join(text for text, _cite in section.prose_lines)
        paragraph_text = " ".join(section.prose_paragraphs)
        if paragraph_text != sentence_text:
            where = section.display_number or section.cell
            problems.append(
                f"{where}장의 검증 문장과 화면 문단 글자가 서로 다릅니다"
            )
    return problems


# ══════════════════════════════════════════════════════════
# ② 본문 인용 번호 ↔ 부록 1:1
# ══════════════════════════════════════════════════════════


def _cited_numbers_in_body(report: Report) -> set[int]:
    """본문·요약 표시 번호와 표가 구조로 보존한 전체 출처 번호."""
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
            seen_source_cites: set[int] = set()
            for raw_cite in table.source_cites:
                source_number = citation_number(raw_cite)
                if (
                    not source_number
                    or raw_cite != f"[{source_number}]"
                    or int(source_number) in seen_source_cites
                ):
                    continue
                seen_source_cites.add(int(source_number))
                numbers.add(int(source_number))
    for item in report.summary_items:
        numbers.update(int(value) for value in CITATION_MARKER_RE.findall(item.text))
    return numbers


def _citation_mapping_problems(report: Report) -> list[str]:
    problems: list[str] = []
    for section in report.sections:
        for index, table in enumerate(section.tables, start=1):
            seen: set[int] = set()
            for raw_cite in table.source_cites:
                number = citation_number(raw_cite)
                if not number or raw_cite != f"[{number}]":
                    problems.append(
                        f"{section.cell} 표 {index}번의 전체 출처 번호가 "
                        f"정본 형식이 아닙니다: {raw_cite!r}"
                    )
                    continue
                parsed = int(number)
                if parsed in seen:
                    problems.append(
                        f"{section.cell} 표 {index}번의 전체 출처 번호 "
                        f"[{parsed}]가 중복됐습니다"
                    )
                    continue
                seen.add(parsed)
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
    """4검사 결과를 사유 목록으로 돌려준다. 비어 있으면 통과다."""
    return tuple(
        [
            *_internal_key_problems(rendered),
            *_paragraph_projection_problems(rendered),
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
