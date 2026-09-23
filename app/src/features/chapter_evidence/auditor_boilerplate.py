"""근거 선별 단계의 «감사인 표준 문구뿐인 조각» 판정.

★ 왜 필요한가(2026-09-23 5차 유료 실행 실측) — 감사보고서 「감사인의 책임」 단락이
  5장 과제·대응 칸을 받아, 작가가 감사인의 감사절차 설계·감사증거 입수를 회사의
  대응으로 옮겨 적었다. 수집 엔진(analysis_engine …/auditor_boilerplate.py)이 근원에서
  칸을 주지 않지만, AI 재판정·저장된 수집 결과·다른 수집기 조각도 선별 한 곳을
  지나므로 여기서 한 번 더 거른다.
★ 판단 단위는 «절»이다. 사소하지 않은 절이 «전부» 감사인 표준 문형일 때만 참이다.
★ 문서 종류로 판정 범위를 정한다(2026-09-23 독립 검토 F1) — 감사보고서 조각만 판정하고,
  종류를 모르면 감사보고서 구조 표지가 있을 때만 판정한다(select.py 가 종류를 넘긴다).
  회사 고유 서술(핵심감사사항의 회사 위험, 계속기업 불확실성의 회사 사정, 회사가
  주어인 내부회계관리제도 운영)이 한 절이라도 섞이면 거짓이다.

엔진·composer 사본과 같은 규칙·같은 값이다(constants.py 주석, 대조 시험 참고).
"""

from __future__ import annotations

import re
import unicodedata

from src.features.chapter_evidence.constants import (
    AUDITOR_BOILERPLATE_EXCLUDED_COMPOUNDS,
    AUDITOR_BOILERPLATE_EXEMPTION_PATTERN,
    AUDITOR_BOILERPLATE_MARKERS,
    AUDITOR_CLAUSE_SPLIT_PATTERN,
    AUDITOR_CONTENT_CHAR_PATTERN,
    AUDITOR_STRUCTURAL_MARKERS,
    AUDITOR_TRIVIAL_CLAUSE_MAX_CONTENT_CHARS,
)

_CLAUSE_SPLIT_RE = re.compile(AUDITOR_CLAUSE_SPLIT_PATTERN)
_EXEMPTION_RE = re.compile(AUDITOR_BOILERPLATE_EXEMPTION_PATTERN)
_CONTENT_CHAR_RE = re.compile(AUDITOR_CONTENT_CHAR_PATTERN)


def _surface(text: str) -> str:
    """공백을 지운 정규화 표면형 — 띄어쓰기가 달라도 같은 문형으로 읽는다."""

    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _occurrence_starts(needle: str, text: str) -> list[int]:
    """겹침을 허용한 모든 출현 시작 위치."""

    starts: list[int] = []
    position = text.find(needle)
    while position != -1:
        starts.append(position)
        position = text.find(needle, position + 1)
    return starts


def _marker_hit(marker: str, surface: str) -> bool:
    """표지가 제외 복합어 밖에서 한 번이라도 나타나는지 본다."""

    if marker not in surface:
        return False
    compounds = AUDITOR_BOILERPLATE_EXCLUDED_COMPOUNDS.get(marker, ())
    if not compounds:
        return True
    covered = [
        (start, start + len(compound))
        for compound in compounds
        for start in _occurrence_starts(compound, surface)
    ]
    return any(
        not any(span_start <= start and start + len(marker) <= span_end
                for span_start, span_end in covered)
        for start in _occurrence_starts(marker, surface)
    )


def is_auditor_clause(clause: str) -> bool:
    """이 절이 감사인 표준 문구인가 — 회사 고유 금액·제재 사건이 있으면 아니다."""

    surface = _surface(clause)
    if not surface or _EXEMPTION_RE.search(surface):
        return False
    return any(_marker_hit(marker, surface) for marker in AUDITOR_BOILERPLATE_MARKERS)


def has_audit_report_structure(text: str) -> bool:
    """감사보고서에만 나오는 구조 표지가 하나라도 있는가 — 문서 종류를 모를 때의 판정 관문."""

    surface = _surface(text)
    return any(marker in surface for marker in AUDITOR_STRUCTURAL_MARKERS)


def is_auditor_boilerplate(text: str, *, audit_report: bool | None = None) -> bool:
    """조각 «전부»가 감사인 표준 문구인가(내용 글자가 적은 제목 절은 세지 않는다).

    Args:
        text: 근거 조각 원문.
        audit_report: 문서 종류로 정한 판정 범위(2026-09-23 독립 검토 F1). 참이면 감사
            보고서 조각이라 구조 표지 없이 판정한다. 거짓이면 감사보고서가 아닌 글(홈페이지·
            IR·뉴스)이라 판정하지 않는다. None(모름)이면 글에 감사보고서 구조 표지가 있을
            때만 판정한다.

    Returns:
        감사인 표준 문구 절이 하나 이상 있고, 사소하지 않은 나머지 절이 없으면 참.
        빈 글·감사인 절이 없는 글·판정 범위 밖의 글은 거짓이다.
    """

    if audit_report is False or (audit_report is None and not has_audit_report_structure(text)):
        return False
    auditor_found = False
    for clause in _CLAUSE_SPLIT_RE.split(text):
        if not clause or not clause.strip():
            continue
        if is_auditor_clause(clause):
            auditor_found = True
            continue
        if len(_CONTENT_CHAR_RE.findall(clause)) > AUDITOR_TRIVIAL_CLAUSE_MAX_CONTENT_CHARS:
            return False
    return auditor_found
