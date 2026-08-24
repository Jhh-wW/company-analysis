"""composer 데이터 계약 (엔진 v2 실행계획의 고정 계약).

★ 이 파일의 세 타입(ComposedSentence·ComposedSection·ComposedReport)은
  단계3 모든 소단계가 공유하는 «고정 계약»이다. 필드 변경·삭제 금지,
  꼭 필요하면 필드 «추가»만 허용한다.
★ composer는 pipeline·report_standard를 import 하지 않는다.
  파이프라인 쪽 실측 구조(조각 dict, ReportTable)는 아래 얇은 어댑터로 받는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ComposedSentence:
    """작가 AI가 쓴 문장 하나."""

    #: 문장 본문 (한국어 산문)
    text: str
    #: 근거로 인용한 수집 조각 id들. 순수 «해석» 문장이면 빈 튜플 허용.
    citations: tuple[str, ...]
    #: "확인"(인용 원문에 직접 근거가 있는 사실) | "해석"(공식 자료 기반 분석·의미 부여)
    grade: str


@dataclass(frozen=True)
class ComposedSection:
    """장 하나. 장 삭제 금지 — 자료가 부족해도 안내문으로 남긴다."""

    #: 기존 v3 정본 장 id 재사용 (identity … competitive_position)
    section_id: str
    #: 이 장의 문장들. 생성 실패 시 빈 튜플.
    sentences: tuple[ComposedSentence, ...]
    #: 자료 부족·생성 실패의 정직한 안내문. 문제없으면 "".
    notice: str = ""


@dataclass(frozen=True)
class ComposedReport:
    """v2 보고서 전체. summary는 소단계 3-3이 채운다 (그전까지 빈 튜플)."""

    sections: tuple[ComposedSection, ...]
    summary: tuple[ComposedSentence, ...] = ()


# ══════════════════════════════════════════════════════════
# 입력 어댑터 — 파이프라인 실측 구조를 얇게 감싼다
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CollectedFragment:
    """수집 조각 하나 — real.py의 `frags: dict[int, dict[str, str]]`를 감싼 것.

    실측 필드 대응: fragment_id ← dict 키(int), kind ← "종류", text ← "원문",
    source_url ← "출처"(홈페이지·공식 IR만), document_title ← "문서명"(공식 IR),
    location ← "원문위치"(공식 IR). 없는 필드는 빈 문자열.
    """

    fragment_id: str
    kind: str
    text: str
    source_url: str = ""
    document_title: str = ""
    location: str = ""


def fragments_from_raw(
    raw: Mapping[int, Mapping[str, Any]]
) -> tuple[CollectedFragment, ...]:
    """파이프라인 조각 dict를 CollectedFragment 튜플로 바꾼다.

    ★ 원문이 빈 조각은 뺀다 — 인용해도 대조할 원문이 없어 근거가 못 되기 때문이다.
      (내용을 보고 거르는 게 아니라 «비어 있는가»만 본다.)
    """
    out: list[CollectedFragment] = []
    for number in sorted(raw):
        item = raw[number]
        text = str(item.get("원문") or "").strip()
        if not text:
            continue
        out.append(
            CollectedFragment(
                fragment_id=str(number),
                kind=str(item.get("종류") or "").strip(),
                text=text,
                source_url=str(item.get("출처") or "").strip(),
                document_title=str(item.get("문서명") or "").strip(),
                location=str(item.get("원문위치") or "").strip(),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class PerformanceTable:
    """프로그램이 만든 3개년 실적표 — 작가 AI에게 근거로 주는 표.

    파이프라인 `ReportTable`(canonical_report.py의 table_facts 원천)을
    composer가 직접 import 하지 않으려고 얇게 복사한 모양이다.
    """

    caption: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    unit: str = ""
    cite: str = ""


def performance_table_from_report_table(table: Any) -> PerformanceTable:
    """파이프라인 ReportTable을 덕 타이핑으로 감싼다 (직접 import 회피)."""
    return PerformanceTable(
        caption=str(getattr(table, "caption", "") or ""),
        headers=tuple(str(h) for h in (getattr(table, "headers", None) or ())),
        rows=tuple(
            tuple(str(cell) for cell in row)
            for row in (getattr(table, "rows", None) or ())
        ),
        unit=str(getattr(table, "display_unit", "") or ""),
        cite=str(getattr(table, "cite", "") or ""),
    )
