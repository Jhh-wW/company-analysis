"""검증된 회사 사실과 일반적인 자기소개서·면접 활용 질문을 한 칸에 묶는다.

이 기능은 지원 직무·채용공고·사용자 경험을 받지 않는다. 따라서 개인에게 맞춘
추천이나 회사 내부의 채용 판단을 만들 수 없다. 기존 1~4번 칸에 이미 실린
인용 가능한 사실만 다시 보여 주고, 그 사실을 사용자가 직접 검토할 질문을 붙인다.
"""

from __future__ import annotations

from src.core.constants import CELL_LABELS, COMPANY_USE_CELL
from src.features.pipeline.port import ReportSection

_MAX_POINTS = 4
_CELL_PRIORITY = ("1", "3", "4-1", "4-3", "2", "4-2", "9", "附")
_QUESTION_BY_CELL: dict[str, str] = {
    "1": (
        "활용 질문 — 이 회사의 고객·제품·수익 발생 방식을 근거 범위 안에서 "
        "한 문장으로 설명할 수 있는가?"
    ),
    "2": (
        "활용 질문 — 이 경쟁력이 실제 계약·기술·설비·사업방식 중 무엇으로 "
        "뒷받침되는지 설명할 수 있는가?"
    ),
    "3": (
        "활용 질문 — 실적의 증감과 그 원인을 구분하고, 원인이 근거에 없으면 "
        "추정하지 않을 수 있는가?"
    ),
    "4-1": (
        "활용 질문 — 이 과제를 해결할 수 있다고 단정하지 않고, 회사가 처한 "
        "상황과 확인이 더 필요한 지점을 질문으로 정리할 수 있는가?"
    ),
    "4-2": (
        "활용 질문 — 최근 활동이 단발성인지 전략 실행인지 다른 공식 근거와 "
        "대조해 볼 수 있는가?"
    ),
    "4-3": (
        "활용 질문 — 회사가 발표한 방향과 이미 실행된 사실을 구분하고, 미래 "
        "성과를 확정적으로 말하지 않을 수 있는가?"
    ),
    "9": (
        "활용 질문 — 공개된 거래처·고객 정보를 전체 고객 구성으로 일반화하지 않고, "
        "확인된 범위만 설명할 수 있는가?"
    ),
    "附": (
        "활용 질문 — 참고 지표의 정의와 기준 시점을 먼저 확인하고, 지원 조건이나 "
        "내부 문화를 뜻한다고 확대 해석하지 않을 수 있는가?"
    ),
}


def _first_cited_fact(section: ReportSection) -> tuple[str, str] | None:
    """검증된 표시문을 우선하고, 없으면 원문에서 첫 인용 가능 사실을 고른다."""

    for text, cite in (*section.prose_lines, *section.lines):
        clean_text = (text or "").strip()
        clean_cite = (cite or "").strip()
        if clean_text and clean_cite:
            return clean_text, clean_cite
    return None


def build_company_use_section(sections: list[ReportSection]) -> ReportSection:
    """회사 사실 최대 네 개와 직무 비종속 활용 질문을 짝지어 만든다.

    사실 문장은 원래 출처 표기를 그대로 재사용한다. 질문은 사실 주장이 아닌
    사용 안내이므로 출처 번호를 만들지 않는다. 근거 있는 사실이 하나도 없으면
    억지 조언을 만들지 않고 빈칸 사유를 표시한다.
    """

    by_cell = {section.cell: section for section in sections}
    lines: list[tuple[str, str]] = []
    guidance_lines: list[str] = []
    points = 0
    for cell in _CELL_PRIORITY:
        section = by_cell.get(cell)
        fact = _first_cited_fact(section) if section is not None else None
        if fact is None:
            continue
        text, cite = fact
        # 원문 추적 검사는 저장 문장이 수집 조각에 그대로 존재하는지 본다.
        # 설명용 표시는 렌더러가 별도 안내로 붙이고, 사실 문장은 바꾸지 않는다.
        lines.append((text, cite))
        guidance_lines.append(_QUESTION_BY_CELL[cell])
        points += 1
        if points >= _MAX_POINTS:
            break

    return ReportSection(
        cell=COMPANY_USE_CELL,
        title=CELL_LABELS[COMPANY_USE_CELL],
        lines=lines,
        guidance_lines=guidance_lines,
        empty_reason=(
            "인용 가능한 회사 사실이 없어 활용 질문을 만들지 않았습니다"
            if not lines
            else ""
        ),
    )
