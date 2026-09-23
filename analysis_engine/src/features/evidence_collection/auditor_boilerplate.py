"""감사보고서 «감사인 표준 문구» 절을 사업 신호 채점에서 빼는 결정론 판정.

★ 왜 필요한가(2026-09-23 5차 유료 실행 실측) — 감사보고서 「감사인의 책임」 단락은
  주어가 감사인(「우리」)이다. 이 단락이 「위험」·「대응」 두 낱말만으로
  current_challenges의 과제·대응 칸을 받았고, 작가는 감사인의 감사절차 설계·
  감사증거 입수를 회사의 대응으로 옮겨 적었다. 검수 AI는 원문에 낱말이 모두
  있다며 «참»을 줬다.
★ 판단 단위는 «절»이다. 감사인 표준 문형(constants.AUDITOR_BOILERPLATE_MARKERS)이
  든 절만 채점 입력에서 뺀다. 같은 문단의 회사 고유 서술(핵심감사사항의 회사
  위험, 계속기업 불확실성의 회사 사정)은 그대로 채점한다.
★ 조각 원문·위치·해시는 바꾸지 않는다 — 채점에 넣는 글만 줄인다.
★ 채점 자리는 문서 종류를 모른다. 그래서 문단이나 절 제목에 감사보고서 «구조 표지»
  (constants.AUDITOR_STRUCTURAL_MARKERS)가 있을 때만 가린다(2026-09-23 독립 검토 F1).
  「감사증거」「감사기준」 같은 짧은 표지는 감사 소프트웨어·감사 서비스 회사의 사업
  문장에도 나오므로, 그런 문단은 구조 표지가 없어 그대로 채점된다.

같은 규칙의 app 사본이 chapter_evidence(근거 선별)와 composer(최종 가드)에 있다.
값은 app 시험이 ast로 대조한다(constants.py 주석 참고).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from features.evidence_collection import constants as c

_CLAUSE_SPLIT_RE = re.compile(c.AUDITOR_CLAUSE_SPLIT_PATTERN)
_EXEMPTION_RE = re.compile(c.AUDITOR_BOILERPLATE_EXEMPTION_PATTERN)
_CONTENT_CHAR_RE = re.compile(c.AUDITOR_CONTENT_CHAR_PATTERN)


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
    """표지가 제외 복합어 밖에서 한 번이라도 나타나는지 본다.

    relevance.keyword_has_direct_hit 와 같은 규칙이다 — 제외 복합어가 그 출현을
    완전히 덮으면 세지 않는다.
    """

    if marker not in surface:
        return False
    compounds = c.AUDITOR_BOILERPLATE_EXCLUDED_COMPOUNDS.get(marker, ())
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
    return any(_marker_hit(marker, surface) for marker in c.AUDITOR_BOILERPLATE_MARKERS)


def has_audit_report_structure(text: str) -> bool:
    """감사보고서에만 나오는 구조 표지가 하나라도 있는가 — 문서 종류를 모를 때의 판정 관문."""

    surface = _surface(text)
    return any(marker in surface for marker in c.AUDITOR_STRUCTURAL_MARKERS)


def _is_trivial(clause: str) -> bool:
    """내용 글자가 기준 이하인 제목·접속 꼬리인가(문단 판정에서 세지 않는다)."""

    return len(_CONTENT_CHAR_RE.findall(clause)) <= c.AUDITOR_TRIVIAL_CLAUSE_MAX_CONTENT_CHARS


@dataclass(frozen=True)
class AuditorClauseSplit:
    """한 문단을 감사인 표준 문구 절과 나머지로 나눈 결과."""

    business_text: str
    auditor_clause_count: int
    business_clause_count: int

    @property
    def only_auditor(self) -> bool:
        """감사인 표준 문구 절이 있고, 사소하지 않은 나머지 절이 하나도 없다."""

        return self.auditor_clause_count > 0 and self.business_clause_count == 0


def split_auditor_clauses(text: str, *, structure_context: str = "") -> AuditorClauseSplit:
    """문단을 절로 나눠 감사인 표준 문구 절을 뺀 채점용 글을 만든다.

    Args:
        text: 수집 후보 문단 원문.
        structure_context: 문단 밖에서 함께 볼 글(보통 절 제목). 엔진의 채점 자리는
            문서 종류를 받지 못하므로, 문단이나 이 글에 감사보고서 구조 표지
            (``c.AUDITOR_STRUCTURAL_MARKERS``)가 있을 때만 감사인 절을 가린다
            (2026-09-23 독립 검토 F1 — 감사 소프트웨어·감사 서비스 회사의 사업 문단 보호).

    Returns:
        ``business_text``는 감사인 절을 뺀 나머지 절을 공백 하나로 이은 글이다.
        감사인 절이 없으면 원문 절을 그대로 이은 글이다(호출자는 이때 원문을
        그대로 쓴다). ``business_clause_count``는 사소한 제목 절을 뺀 나머지 수다.
    """

    audit_context = (has_audit_report_structure(text)
                     or has_audit_report_structure(structure_context))
    kept: list[str] = []
    auditor_count = 0
    business_count = 0
    for clause in _CLAUSE_SPLIT_RE.split(text):
        if not clause or not clause.strip():
            continue
        if audit_context and is_auditor_clause(clause):
            auditor_count += 1
            continue
        kept.append(clause.strip())
        if not _is_trivial(clause):
            business_count += 1
    return AuditorClauseSplit(
        business_text=" ".join(kept),
        auditor_clause_count=auditor_count,
        business_clause_count=business_count,
    )


def is_auditor_boilerplate(text: str, *, structure_context: str = "") -> bool:
    """문단 «전부»가 감사인 표준 문구인가(사소한 제목 절은 세지 않는다).

    구조 표지가 문단에도 ``structure_context``에도 없으면 거짓이다(F1 관문).
    """

    return split_auditor_clauses(text, structure_context=structure_context).only_auditor
