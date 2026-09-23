"""명시된 상품 행의 조건을 다른 상품·넓은 범위로 옮긴 경우만 찾는다.

빈 문자열은 이 좁은 구조 검사에서 반례를 찾지 못했다는 뜻이다. 의미 검수,
수치·기간·서명·뉴스 검사를 대체하지 않으며 상품명이 누락되면 추측하지 않는다.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.features.composer.scope_constants import (
    BROAD_SCOPE_RE, BULLET_RE, CLAUSE_RE, CONDITION_RE, LOCAL_EXAMPLE_RE, LOCAL_UNIT_RE,
    FLOW_CONDITION_END_RE, FLOW_DIRECT_SCOPE_BRIDGE_RE, FLOW_SENTENCE_SUBJECT_RE,
    OWNER_BRIDGE_RE, RELATIVE_OWNER_RE, ROW_SEPARATOR_RE,
    SCOPE_CONDITION_UNBOUND, TABLE_HEADERS,
    RECOGNITION_CLAUSE_RE, RECOGNITION_SOURCE_BOUNDARY_RE, RECOGNITION_COMMA_RE,
    RECOGNITION_OPEN_CONDITION_RE, RECOGNITION_DEPENDENT_START_RE,
    REVENUE_SUBJECT_RE, RECOGNITION_RE,
    COMPLETION_RE, PROGRESS_RE, DURATION_LIMIT_RE,
    EXPLICIT_EXCLUSION_RE, ENTITY_SUBJECT_RE, CURRENT_SUBSIDIARY_RE,
    HISTORICAL_MEMBERSHIP_RE,
)


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


@dataclass(frozen=True)
class _Unit:
    text: str
    owner: str = ""
    local: bool = False


def _units(text: str) -> list[_Unit]:
    """명시적인 표 구분자·제목 다음 bullet만 묶고 조각 사이 제목은 빌리지 않는다."""
    units: list[_Unit] = []
    previous = ""
    for raw in text.splitlines():
        line = raw.strip().strip("|").strip()
        if not line:
            continue
        pieces = ROW_SEPARATOR_RE.split(line, maxsplit=1)
        owner = ""
        if len(pieces) == 2 and not CONDITION_RE.search(pieces[0]) and pieces[0] not in TABLE_HEADERS:
            owner = pieces[0].strip()
        elif BULLET_RE.match(line) and previous and previous not in TABLE_HEADERS:
            if not CONDITION_RE.search(previous) and not BULLET_RE.match(previous) and not BROAD_SCOPE_RE.search(previous):
                owner = previous
        condition = CONDITION_RE.search(line)
        broad_subject = condition and BROAD_SCOPE_RE.search(line[:condition.start()])
        local = bool(owner or (BULLET_RE.match(line) and LOCAL_UNIT_RE.search(line) and not broad_subject))
        units.append(_Unit(line, owner, local))
        previous = line
    return units


def _conditions(text: str) -> list[tuple[tuple[str, ...], re.Match[str]]]:
    found = []
    for match in CONDITION_RE.finditer(unicodedata.normalize("NFKC", text)):
        if match["grade"]:
            key = ("grade", match["grade"].casefold(), match["grade_op"])
        elif match["number"]:
            key = ("number", match["number"].replace(",", ""), match["unit"], match["number_op"])
        else:
            key = ("target", _surface(match["target"]), _surface(match["target_op"]))
        found.append((key, match))
    return found


def _scopes(text: str) -> set[str]:
    return {_surface(match.group()) for match in BROAD_SCOPE_RE.finditer(text)}


def _owner_bound(clause: str, owner: str, condition: re.Match[str], owners: set[str]) -> bool:
    if not owner:
        return False
    for mention in re.finditer(re.escape(owner), clause, re.IGNORECASE):
        if mention.end() <= condition.start():
            bridge = clause[mention.end():condition.start()].strip()
            other_owner = any(other != owner and other in bridge for other in owners)
            if not other_owner and not OWNER_BRIDGE_RE.search(bridge) and not BROAD_SCOPE_RE.search(bridge):
                return True
        elif mention.start() >= condition.end():
            bridge = clause[condition.end():mention.start()]
            other_owner = any(other != owner and other in bridge for other in owners)
            if not other_owner and RELATIVE_OWNER_RE.search(bridge) and not BROAD_SCOPE_RE.search(bridge):
                return True
    return False


def _recognition_source_clauses(source: str) -> list[str]:
    """완료 용역의 열린 기간 조건만 쉼표 뒤 종속 인식 절에 붙인다."""
    clauses: list[str] = []
    for sentence in RECOGNITION_SOURCE_BOUNDARY_RE.split(source):
        pending: list[str] = []
        for clause in RECOGNITION_COMMA_RE.split(sentence):
            if pending:
                if RECOGNITION_DEPENDENT_START_RE.search(clause) and RECOGNITION_RE.search(clause):
                    clauses.append(", ".join((*pending, clause)))
                    pending = []
                    continue
                if RECOGNITION_OPEN_CONDITION_RE.search(clause) and not RECOGNITION_RE.search(clause):
                    pending.append(clause)
                    continue
                # 완결된 서술이나 독립 주어가 나오면 앞 조건을 빌려주지 않는다.
                clauses.extend(pending)
                pending = []
            if (DURATION_LIMIT_RE.search(clause) and COMPLETION_RE.search(clause)
                    and REVENUE_SUBJECT_RE.search(clause)
                    and RECOGNITION_OPEN_CONDITION_RE.search(clause)
                    and not RECOGNITION_RE.search(clause)):
                pending.append(clause)
            else:
                clauses.append(clause)
        clauses.extend(pending)
    return clauses


def _narrative_scope_problem(text: str, sources: Mapping[str, str]) -> str:
    """한정이 직접 붙은 인식 방식과 명시된 제외 주체만 대조한다.

    일반적인 의미 일치 판정이 아니다. 다른 상품의 조건이나 다른 법인의
    제외를 공유 단어만으로 연결하지 않으며, 모호한 주체는 의미 검수에 남긴다.
    """
    source_clauses = [clause for source in sources.values()
                      for clause in _recognition_source_clauses(source)]
    completion_sources = [clause for clause in source_clauses
                          if COMPLETION_RE.search(clause) and RECOGNITION_RE.search(clause)]
    limited_sources = [clause for clause in completion_sources if DURATION_LIMIT_RE.search(clause)]
    # 원칙(진행)과 한정된 특례(완료)가 실제 자기 인용에 모두 있을 때만 검사한다.
    if (limited_sources and all(DURATION_LIMIT_RE.search(clause) for clause in completion_sources)
        and any(PROGRESS_RE.search(clause) and RECOGNITION_RE.search(clause) for clause in source_clauses)):
        source_limits = {(m["value"], m["unit"]) for clause in limited_sources
                         for m in DURATION_LIMIT_RE.finditer(clause)}
        for clause in RECOGNITION_CLAUSE_RE.split(text):
            if not (REVENUE_SUBJECT_RE.search(clause) and COMPLETION_RE.search(clause)
                    and RECOGNITION_RE.search(clause)):
                continue
            candidate_limits = {(m["value"], m["unit"]) for m in DURATION_LIMIT_RE.finditer(clause)}
            if not candidate_limits.intersection(source_limits):
                return SCOPE_CONDITION_UNBOUND
    for candidate_clause in re.split(r"[.;。\n,，]", text):
        if not CURRENT_SUBSIDIARY_RE.search(candidate_clause) or HISTORICAL_MEMBERSHIP_RE.search(candidate_clause):
            continue
        for source in sources.values():
            for clause in re.split(r"[.;。\n]", source):
                if not EXPLICIT_EXCLUSION_RE.search(clause):
                    continue
                owner_match = ENTITY_SUBJECT_RE.search(clause)
                if owner_match and re.search(
                    r"(?<![A-Za-z0-9가-힣])" + re.escape(owner_match["owner"])
                    + r"(?=(?:은|는|이|가|의|을|를)?(?:\s|[,.;]|$))", candidate_clause,
                ):
                    return SCOPE_CONDITION_UNBOUND
    return ""


def scope_problem(candidate_text: str, sources_mapping: Mapping[str, str]) -> str:
    """고정 코드 또는 빈 문자열을 반환하는 무호출·무저장 범위 방어다.

    실제 조건이 있는 좁은 원문 행을 찾은 다음에만 범위 확대를 판정한다.
    조건이나 채널 키워드만으로 문장을 제거하지 않는다. 원문 자체가 같은
    넓은 주어에 조건을 직접 붙이거나 정확한 상품 예시를 유지하면 통과한다.
    """
    text = unicodedata.normalize("NFKC", candidate_text)
    narrative_problem = _narrative_scope_problem(text, sources_mapping)
    if narrative_problem:
        return narrative_problem
    units = [unit for source in sources_mapping.values() for unit in _units(unicodedata.normalize("NFKC", source))]
    owners = {unit.owner for unit in units if unit.owner}
    for clause in CLAUSE_RE.split(text):
        for key, condition in _conditions(clause):
            matching = [unit for unit in units if any(source_key == key for source_key, _ in _conditions(unit.text))]
            local = [unit for unit in matching if unit.local]
            if not local:
                continue
            if any(_owner_bound(clause, unit.owner, condition, owners) for unit in local):
                continue
            # 제목이 없어도 동일 원문에 있는 상품 유형에 조건을 직접 붙인 예시는 남긴다.
            examples = [match["owner"] for match in LOCAL_EXAMPLE_RE.finditer(clause, condition.end())]
            if any(owner in unit.text and _owner_bound(clause, owner, condition, owners)
                   for owner in examples for unit in local):
                continue
            candidate_scopes = _scopes(clause[:condition.start()])
            # 한 원문 전체에 같은 단어가 있다는 사실로 다른 행의 조건을 승인하지 않는다.
            if any(not unit.local and candidate_scopes & _scopes(unit.text) for unit in matching):
                continue
            different_owner = any(_owner_bound(clause, owner, condition, owners) for owner in owners)
            if candidate_scopes or different_owner:
                return SCOPE_CONDITION_UNBOUND
    return ""


def flow_scope_problem(cells: Sequence[str], sources_mapping: Mapping[str, str]) -> str:
    """한 행의 대상명 칸 → 인접한 자격 조건 칸에서만 적용 범위를 대조한다.

    칸·문장·원문을 이어 붙이지 않는다. 완전한 문장이나 중간의 다른 칸을 넘어
    주어를 빌리지 않으며, 알려지지 않은 조건·불명확한 칸 관계는 기존 검수에 남긴다.
    빈 결과는 전체 도식 승인이나 수치·시점·JSON 검증의 면제를 뜻하지 않는다.
    """
    values = [unicodedata.normalize("NFKC", cell).strip() for cell in cells]
    for cell in values:
        problem = scope_problem(cell, sources_mapping)
        if problem:
            return problem
    units = [unit for source in sources_mapping.values() for unit in _units(unicodedata.normalize("NFKC", source))]
    owners = {unit.owner for unit in units if unit.owner}
    for index, cell in enumerate(values):
        if not index or not FLOW_CONDITION_END_RE.search(cell) or len(CLAUSE_RE.split(cell)) != 1:
            continue
        target = values[index - 1]
        target_key = _surface(target)
        broad = bool(BROAD_SCOPE_RE.fullmatch(target))
        if not broad and not any(target_key == _surface(owner) for owner in owners):
            continue
        for key, condition in _conditions(cell):
            # 자체 주어를 가진 설명 문장에는 앞 칸의 대상을 덧씌우지 않는다.
            if FLOW_SENTENCE_SUBJECT_RE.search(cell[:condition.start()]):
                continue
            matching = [unit for unit in units if any(source_key == key for source_key, _ in _conditions(unit.text))]
            local = [unit for unit in matching if unit.local]
            if not local:
                continue
            if any(target_key == _surface(unit.owner) for unit in local if unit.owner):
                continue
            if any(_owner_bound(cell, unit.owner, condition, owners) for unit in local):
                continue
            direct = False
            for unit in matching:
                for clause in CLAUSE_RE.split(unit.text):
                    for source_key, source_condition in _conditions(clause):
                        if source_key != key:
                            continue
                        prefix = clause[:source_condition.start()]
                        for scope in BROAD_SCOPE_RE.finditer(prefix):
                            bridge = _surface(prefix[scope.end():])
                            if target_key == _surface(scope.group()) and FLOW_DIRECT_SCOPE_BRIDGE_RE.fullmatch(bridge):
                                direct = True
            if not direct:
                return SCOPE_CONDITION_UNBOUND
    return ""
