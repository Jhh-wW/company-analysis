"""직접 회사 단정에서 공식 원문의 명시 주어·표 상태를 바꾸지 못하게 한다."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from src.features.composer.entity_scope_constraint_constants import (
    SOURCE_CONTEXT_SELF_RE, SOURCE_CONTEXT_GROUP_RE, SOURCE_CONTEXT_FLOW_STATEMENT_RE,
)
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.shared.company_identity import exact_company_names_equivalent
from src.shared.report_evidence.source_context import parse_source_context, source_context_pending_state_problem


def _name(value: str) -> str:
    return re.sub(r"\s+|주식회사|\(주\)|㈜", "", value).casefold()


def _relation_recorded(actor: str, sources: Mapping[str, str]) -> bool:
    label = re.escape(_name(actor))
    return any(re.search(
        r"(?:종속회사|종속기업|자회사|계열사)(?:인|인회사)?" + label
        + r"|" + label + r"(?:는|은|가|이)(?:종속회사|종속기업|자회사|계열사)(?:입니다|이다|다|로)",
        _name(source),
    ) for source in sources.values())


def source_actor_problem(candidate_text: str, context_json: str, sources: Mapping[str, str] | None = None,
                         *, cells: Sequence[str] | None = None) -> str:
    context = parse_source_context(context_json)
    if not context:
        return ""
    if context["status"] and source_context_pending_state_problem(candidate_text, context):
        return SCOPE_CONDITION_UNBOUND
    actor, document_actor = context["actor"], context["document_actor"]
    if exact_company_names_equivalent(actor, document_actor):
        return ""
    if context["origin"] == "company_heading" and actor.endswith("의") and exact_company_names_equivalent(actor[:-1], document_actor):
        return ""
    # 관계 근거는 기존 역할·주어 검수가 별도로 검사한다. 이 필드는 소속 증명이 아니다.
    if context["origin"] == "company_heading" and actor.endswith("의"):
        actor = actor[:-1]
    actor_key = _name(actor)
    source_texts = sources or {}
    if cells is not None:
        nonempty = tuple(cell.strip() for cell in cells if cell.strip())
        checked = tuple(
            "" if _name(cell) == actor_key else source_actor_problem(cell, context_json, source_texts)
            for cell in nonempty
        )
        # 실제 행위자를 명시한 칸이 있어야 다른 명사형 칸의 주어 생략을 허용한다.
        # 전체 행의 예정 단계 검사는 위에서 먼저 수행했다.
        if nonempty and any(not issue for issue in checked):
            if all(not issue or (
                not SOURCE_CONTEXT_FLOW_STATEMENT_RE.search(cell)
                and not SOURCE_CONTEXT_SELF_RE.search(cell)
            ) for cell, issue in zip(nonempty, checked)):
                return ""
        return SCOPE_CONDITION_UNBOUND
    relation_recorded = _relation_recorded(actor, source_texts)
    units = re.split(r"(?<=[.。;])\s*|\s+(?=(?:회사|당사|동사|본사)(?:는|가|의)\s)", candidate_text)
    for unit in units:
        candidate = _name(unit)
        self_subject = bool(SOURCE_CONTEXT_SELF_RE.search(unit))
        named_subject = bool(re.match(r"(?:종속회사|종속기업|자회사|계열사)?" + re.escape(actor_key) + r"(?:는|은|가|이|의)", candidate))
        through_actor = bool(re.search(re.escape(actor_key) + r"(?:를|을)통해", candidate))
        relation_claimed = bool(re.match(r"(?:종속회사|종속기업|자회사|계열사)", candidate))
        through_recorded = any(re.search(
            r"(?:^|[.。;])(?:회사|당사|동사|본사|" + re.escape(_name(document_actor))
            + r")(?:는|가)" + re.escape(actor_key) + r"(?:를|을)통해",
            _name(source),
        ) for source in source_texts.values())
        if named_subject and (not relation_claimed or relation_recorded):
            continue
        if through_actor and self_subject and through_recorded:
            continue
        if SOURCE_CONTEXT_GROUP_RE.search(unit) and relation_recorded:
            continue
        # 주어를 생략한 본문·도식도 대상 회사 Fact로 승격되므로 동일하게 닫는다.
        if candidate:
            return SCOPE_CONDITION_UNBOUND
    return ""


def source_actor_subject_scope(candidate_text: str, contexts: tuple[str, ...], *,
                               company_name: str, sources: Mapping[str, str]) -> str:
    """검수와 같은 제약을 통과한 명시 주어만 최종 사실의 행위 범위로 보존한다."""
    if not contexts:
        return company_name
    contexts = tuple(raw for raw in contexts if raw)
    if not contexts:
        return company_name
    parsed = [parse_source_context(raw) for raw in contexts]
    if any(source_actor_problem(candidate_text, raw, sources) for raw in contexts):
        return ""
    if SOURCE_CONTEXT_GROUP_RE.search(candidate_text):
        group_recorded = any(SOURCE_CONTEXT_GROUP_RE.search(text) for text in sources.values())
        if all(
            group_recorded if exact_company_names_equivalent(item["actor"], item["document_actor"])
            else _relation_recorded(item["actor"], sources)
            for item in parsed
        ):
            return company_name + " 연결 범위"
        return ""
    actors: list[str] = []
    for item in parsed:
        actor = item["actor"]
        if item["origin"] == "company_heading" and actor.endswith("의"):
            actor = actor[:-1]
        if not any(exact_company_names_equivalent(actor, previous) for previous in actors):
            actors.append(actor)
    if len(actors) != 1:
        return ""
    return company_name if exact_company_names_equivalent(actors[0], company_name) else actors[0]
