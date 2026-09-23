"""감사보고서 «감사인 표준 문구»가 회사의 과제·대응·차별점으로 둔갑하는 것을 막는다.

★ 실측(2026-09-23 5차 유료 실행, 독립 검수 상-3) — 감사보고서 「감사인의 책임」
  단락(주어는 감사인 「우리」)을 인용해, 작가가 5장에 「회사는 상황에 적합한
  감사절차를 설계하고 … 감사증거를 입수하는 방식으로 대처하고 있다」 같은 산문과
  도식 행을 썼다. 검수 AI는 원문에 낱말이 모두 있다며 전부 «참»을 줬고, 공개를
  막은 것은 내용 가드가 아니라 우연한 판독 실패였다.
★ 두 규칙 — 하나라도 걸리면 고정 사유를 돌려준다.
  ① 인용 상용구: 후보가 인용한 원문(실적표 제외)이 «전부» 감사인 표준 문구뿐이다.
     그 문구에는 회사 고유 사실이 없어, 무엇을 썼든 회사의 사업 주장을 뒷받침하지
     못한다.
  ② 행위 전가: 후보의 한 절이 감사인만 하는 감사 행위(감사증거 입수·감사절차 설계·
     합리적 확신 획득 등)나 감사의견 문형(재무제표를 공정하게 표시)을 담았는데, 그
     절의 주어로 감사인이 드러나지 않는다 — 회사의 행위·주장으로 옮겼다는 뜻이다.
★ 경계 — 회사가 주어인 내부회계관리제도 운영 서술, 핵심감사사항의 회사 위험 서술,
  계속기업 불확실성의 회사 사정은 막지 않는다. 표지에 그 짧은 낱말들이 없고, 그런
  원문은 회사 고유 절이 섞여 «전부 상용구»가 아니다.
★ 사유 코드는 새로 만들지 않았다 — audit_boilerplate_constants.AUDIT_BOILERPLATE_PROBLEM.
★ 배선 — scope_guard.scope_problem 이 맨 먼저 부른다. 그 함수는 검수 결속
  (grounding.grounding_problem: 본문·요약·도식)과 도식 칸 검사(flow_scope_problem:
  verify·diagram_check)가 이미 부르므로 verify.py 를 고치지 않아도 걸린다.

★ 관문(2026-09-23 독립 검토 F1) — 두 규칙은 인용 원문에 감사보고서 구조 표지
  (AUDITOR_STRUCTURAL_MARKERS)가 있을 때만 건다. composer 는 조각 종류를 받지 못해,
  감사 소프트웨어·감사 서비스 회사의 사업 문장(「감사증거 수집 자동화」)을 지키려면
  «감사보고서를 인용했는가»를 글로 알아봐야 한다.

⚠️ 빈 문자열은 그 문장이 옳다는 뜻이 아니다. 이 모듈은 «감사인 문구의 둔갑»만 본다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping

from src.features.composer.audit_boilerplate_constants import (
    AUDIT_BOILERPLATE_PROBLEM,
    AUDIT_OPINION_JUDGMENT_PATTERN,
    AUDIT_OPINION_OBJECT_PATTERN,
    AUDITOR_ACT_PATTERNS,
    AUDITOR_BOILERPLATE_EXCLUDED_COMPOUNDS,
    AUDITOR_BOILERPLATE_EXEMPTION_PATTERN,
    AUDITOR_BOILERPLATE_MARKERS,
    AUDITOR_CLAUSE_SPLIT_PATTERN,
    AUDITOR_CONTENT_CHAR_PATTERN,
    AUDITOR_STRUCTURAL_MARKERS,
    AUDITOR_SUBJECT_PATTERN,
    AUDITOR_TRIVIAL_CLAUSE_MAX_CONTENT_CHARS,
    COMPANY_CONTROL_OPERATION_PATTERN,
    REASONABLE_ASSURANCE_ACT_PATTERN,
)
from src.features.composer.grounding_constants import TABLE_SOURCE_ID

#: 관측·시험용 규칙 이름 — 공개 사유 코드는 언제나 하나다.
RULE_CITATION_BOILERPLATE = "인용상용구"
RULE_ACT_TRANSFER = "행위전가"

_CLAUSE_SPLIT_RE = re.compile(AUDITOR_CLAUSE_SPLIT_PATTERN)
_EXEMPTION_RE = re.compile(AUDITOR_BOILERPLATE_EXEMPTION_PATTERN)
_CONTENT_CHAR_RE = re.compile(AUDITOR_CONTENT_CHAR_PATTERN)
_ACT_RES = tuple(re.compile(pattern) for pattern in AUDITOR_ACT_PATTERNS)
_OPINION_OBJECT_RE = re.compile(AUDIT_OPINION_OBJECT_PATTERN)
_OPINION_JUDGMENT_RE = re.compile(AUDIT_OPINION_JUDGMENT_PATTERN)
_AUDITOR_SUBJECT_RE = re.compile(AUDITOR_SUBJECT_PATTERN)
_REASONABLE_ASSURANCE_RE = re.compile(REASONABLE_ASSURANCE_ACT_PATTERN)
_COMPANY_CONTROL_RE = re.compile(COMPANY_CONTROL_OPERATION_PATTERN)


def _surface(text: str) -> str:
    """공백을 지운 정규화 표면형 — 다른 가드와 같은 잣대를 쓴다."""

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
    """원문 «전부»가 감사인 표준 문구인가(내용 글자가 적은 제목 절은 세지 않는다).

    ``audit_report``는 근거 선별 사본과 같은 뜻이다. composer 는 조각 종류를 받지
    못하므로 기본값(None)으로 부르고, 그러면 글에 구조 표지가 있을 때만 판정한다.
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


def _cited_texts(sources: Mapping[str, str] | None) -> tuple[str, ...]:
    """후보가 스스로 인용한 원문 — 모든 후보에 함께 실리는 실적표 원문은 뺀다.

    실적표는 «후보가 인용해서» 들어오는 값이 아니다(verify._grounding_candidate).
    그것을 세면 표가 있는 보고서에서 ① 규칙이 통째로 꺼진다.
    """

    if not isinstance(sources, Mapping):
        return ()
    return tuple(
        str(value) for source_id, value in sources.items()
        if source_id != TABLE_SOURCE_ID and str(value or "").strip()
    )


def cites_audit_report(sources: Mapping[str, str] | None) -> bool:
    """인용 원문(실적표 제외)에 감사보고서 구조 표지가 하나라도 있는가 — 두 규칙의 관문.

    ★ 2026-09-23 독립 검토 F1 — 「감사증거」「감사보고서를 발행」 같은 행위 표지는 감사
      소프트웨어·감사 서비스 회사의 사업 문장에도 나온다. 그런 회사의 자기 소개를 인용한
      후보까지 막지 않도록, 감사보고서를 인용했을 때만 두 규칙을 건다.
    """

    return any(has_audit_report_structure(text) for text in _cited_texts(sources))


def cites_only_auditor_boilerplate(sources: Mapping[str, str] | None) -> bool:
    """① 인용한 원문이 하나 이상 있고, 그 «전부»가 감사인 표준 문구뿐인가.

    원문마다 구조 표지가 있어야 감사인 문구로 센다(판정 함수 기본값).
    """

    cited = _cited_texts(sources)
    return bool(cited) and all(is_auditor_boilerplate(text) for text in cited)


def auditor_act_transferred(text: str) -> bool:
    """② 한 절이 감사인의 감사 행위·감사의견 문형을 감사인 주어 없이 적었는가.

    글자만 본다 — 인용 원문 관문(``cites_audit_report``)은 ``audit_boilerplate_rule``이 건다.
    합리적 확신은 같은 절에 회사의 내부통제 «운영»이 있으면 회사 서술로 본다(F2).
    """

    for clause in _CLAUSE_SPLIT_RE.split(unicodedata.normalize("NFKC", text)):
        surface = _surface(clause)
        if not surface:
            continue
        acted = (
            any(pattern.search(surface) for pattern in _ACT_RES)
            or bool(_REASONABLE_ASSURANCE_RE.search(surface)
                    and not _COMPANY_CONTROL_RE.search(surface))
            or bool(_OPINION_OBJECT_RE.search(surface)
                    and _OPINION_JUDGMENT_RE.search(surface))
        )
        if acted and not _AUDITOR_SUBJECT_RE.search(surface):
            return True
    return False


def audit_boilerplate_rule(text: str, sources: Mapping[str, str] | None) -> str:
    """걸린 규칙 이름(없으면 빈 문자열) — 관측·시험에서 «왜 걸렸는지» 되짚는 용도.

    인용 원문에 감사보고서 구조 표지가 없으면 두 규칙 모두 걸지 않는다(F1 관문).
    """

    if not cites_audit_report(sources):
        return ""
    if cites_only_auditor_boilerplate(sources):
        return RULE_CITATION_BOILERPLATE
    if auditor_act_transferred(text):
        return RULE_ACT_TRANSFER
    return ""


def audit_boilerplate_problem(text: str, sources: Mapping[str, str] | None) -> str:
    """감사인 표준 문구의 둔갑이면 고정 사유, 아니면 빈 문자열.

    Args:
        text: 검수 후보 하나(문장, 도식 칸 하나, 또는 칸을 이은 행).
        sources: 그 후보의 자기 인용 원문(조각 id → 원문). 실적표 원문은 세지 않는다.

    Returns:
        ``AUDIT_BOILERPLATE_PROBLEM`` 또는 빈 문자열.
    """

    return AUDIT_BOILERPLATE_PROBLEM if audit_boilerplate_rule(text, sources) else ""
