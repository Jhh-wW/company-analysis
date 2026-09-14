"""집단 수·전량 표현을 관계의 상대편 자리에 놓은 후보만, 그 관계를 뒷받침한다는
«원문 구절»을 대조하는 순수 검사.

`direct_support.causal_relation_problem`과 같은 구조다 — 후보가 발동 꼴을 쓰지
않으면 아무 판정도 하지 않는다. 발동했다면 검수 응답이 검증근거의 «관계» 배열에
유형="결합" 항목으로 «어느 인용의 어느 구절이 그 집단 수와 그 관계를 함께 담는지»를
대야 하고, 그 구절은 인용 원문에 «글자 그대로» 있어야 하며, 한 «절» 안에서 범위와
관계가 끊기지 않아야 한다.

지키는 선:
  · 유사도 문턱·회사명·업종·특정 숫자를 쓰지 않는다. 문법 표지와 닫힌 어휘,
    그리고 문자열 포함만 본다.
  · 발동 꼴이 없으면 아무 판정도 하지 않는다 — 정상적인 집단 수 서술·관계 서술을
    이 검사로 막지 않는다.
  · 빈 문자열은 «문제 없음»이지 승인이 아니다. 나머지 판정은 기존 검수가 한다.
  · `combined_relation_report`는 스위치(`COMBINED_RELATION_ENFORCED`)와 무관하게
    «항상» 계산한다 — 시험과 관측 로그는 이 함수를 쓴다. `combined_relation_problem`
    만 스위치를 본다(진단 우선 모드, 설계안 §4).
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from src.features.composer.combined_relation_constants import (
    COMBINED_RELATION_ENFORCED,
    COMBINED_RELATION_HINT_HEAD,
    COMBINED_RELATION_HINT_ITEM_TEMPLATE,
    COMBINED_RELATION_REVIEW_GUIDE_ENFORCED,
    COMBINED_RELATION_REVIEW_GUIDE_OBSERVED,
    COMBINED_RELATION_RULE_VERSION,
    COMBINED_RELATION_TRIGGER_RE,
    COMBINED_RELATION_WORD_KEY,
    COMBINED_SCOPE_CLAIM_NOT_COVERED,
    COMBINED_SCOPE_EVIDENCE_MISSING,
    COMBINED_SCOPE_FIELD_TYPE_INVALID,
    COMBINED_SCOPE_KEY,
    COMBINED_SCOPE_NEGATED_IN_SOURCE,
    COMBINED_SCOPE_QUOTE_NOT_IN_SOURCE,
    COMBINED_SCOPE_RANGE_NOT_IN_QUOTE,
    COMBINED_SCOPE_RELATION_NOT_IN_QUOTE,
    COMBINED_SCOPE_SOURCE_NOT_CITED,
    COMBINED_SCOPE_SPLIT_ACROSS_CLAUSES,
    RELATION_COMBINED,
)
from src.features.composer.direct_support_constants import (
    QUOTE_STRIP_RE,
    RELATION_KEY,
    RELATION_QUOTE_KEY,
    RELATION_SOURCE_KEY,
    RELATION_TYPE_KEY,
    SOURCE_HEDGED_NEGATION_RE,
    SOURCE_NEGATION_RE,
    SOURCE_SENTENCE_SPLIT_RE,
    WHITESPACE_RE,
)
from src.features.composer.grounding_constants import TABLE_SOURCE_ID
from src.features.composer.role_binding_constants import CLAUSE_SPLIT_RE


def combined_relation_review_guide() -> str:
    """검수 프롬프트에 실을 결합 안내문 — 스위치를 «부를 때» 읽는다.

    ★ 진단 모드에서는 판정 지시를 빼고 «항목을 내 달라»까지만 싣는다. 코드가 막지
      않는데 프롬프트만 판정을 바꾸면 A단계가 관측이 아니라 조용한 차단이 된다
      (설계안 §4, 독립 검토 §3-A). 모듈 전역을 그때그때 읽으므로 시험이 스위치를
      갈아 끼우면 두 모드의 프롬프트를 각각 고정할 수 있다.
    """

    return (COMBINED_RELATION_REVIEW_GUIDE_ENFORCED if COMBINED_RELATION_ENFORCED
            else COMBINED_RELATION_REVIEW_GUIDE_OBSERVED)


def _surface(value: object) -> str:
    """공백·인용부호를 지우고 대소문자를 통일한 대조용 표면형(direct_support와 동일)."""

    normalized = unicodedata.normalize("NFKC", str(value or ""))
    return QUOTE_STRIP_RE.sub("", WHITESPACE_RE.sub("", normalized)).casefold()


def _text_field(item: Mapping, key: str) -> str:
    """항목의 칸을 «문자열일 때만» 읽는다. bool·목록을 문자열로 바꾸지 않는다."""

    value = item.get(key)
    return value if isinstance(value, str) else ""


@dataclass(frozen=True)
class CombinedRelationTrigger:
    """후보 문장에서 발동한 자리 하나. 판정이 아니라 «검사를 켤지»만 정한다."""

    matched: str
    scope: str
    relation: str


@dataclass(frozen=True)
class CombinedRelationReport:
    """스위치와 무관하게 항상 계산하는 판정 결과. 시험과 관측 로그가 이 값을 쓴다."""

    problem: str
    triggers: tuple[CombinedRelationTrigger, ...]
    rule_version: str


def combined_relation_triggers(text: str) -> tuple[CombinedRelationTrigger, ...]:
    """절 경계 안에서 발동 자리를 찾는다.

    ★ 절 경계는 `role_binding_constants.CLAUSE_SPLIT_RE`를 그대로 쓴다 — 원문·산문을
      «스스로 끊은» 자리에서만 나누고 쉼표는 절을 나누지 않는다. 같은 잣대를 두 곳에서
      다시 정의하면 한쪽만 고쳐 어긋나는 사고가 난다.
    """

    normalized = unicodedata.normalize("NFKC", str(text or ""))
    triggers: list[CombinedRelationTrigger] = []
    for clause in CLAUSE_SPLIT_RE.split(normalized):
        for match in COMBINED_RELATION_TRIGGER_RE.finditer(clause):
            triggers.append(CombinedRelationTrigger(
                match.group(), match.group("scope"), match.group("relation"),
            ))
    return tuple(triggers)


def combined_relation_hint(triggers: tuple[CombinedRelationTrigger, ...]) -> str:
    """검수 프롬프트의 후보 밑에 붙일 «어느 자리에서 발동했는지» 줄.

    발동이 없으면 빈 문자열이다(기존 프롬프트 바이트 불변 — `role_binding_hint_lines`와
    같은 계약). 같은 표면형의 반복은 한 번만 적는다.
    """

    if not triggers:
        return ""
    entries = dict.fromkeys(
        COMBINED_RELATION_HINT_ITEM_TEMPLATE.format(scope=trigger.scope, relation=trigger.relation)
        for trigger in triggers
    )
    return COMBINED_RELATION_HINT_HEAD + ", ".join(entries) + "\n"


def _combined_entries(entries: object) -> tuple[Mapping, ...]:
    """검증근거에서 관계 배열만 꺼낸다. 모양이 다르면 빈 값으로 둔다."""

    if not isinstance(entries, Mapping):
        return ()
    values = entries.get(RELATION_KEY)
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return ()
    return tuple(item for item in values if isinstance(item, Mapping))


def _same_clause(quote: str, scope_key: str, relation_key: str) -> bool:
    """범위와 관계가 «구절 안의 같은 절»에 함께 있는가.

    ★ 구절 자체가 두 문장을 이어 붙인 것이면(「…577개사입니다. 배당수익을
      수취합니다.」) 전체 표면형에는 둘 다 있어도 같은 절에는 없다. 그래서 구절을
      다시 절 단위로 쪼개 «어느 한 절»이 둘 다 담는지 본다.
    """

    normalized = unicodedata.normalize("NFKC", str(quote or ""))
    return any(
        scope_key in _surface(clause) and relation_key in _surface(clause)
        for clause in CLAUSE_SPLIT_RE.split(normalized)
    )


def _negation_in_covering_sentence(source: str, quote_key: str) -> str:
    """제시 구절이 걸쳐 있는 원문 «문장»이 그 관계를 부정·유보하면 사유를 돌려준다.

    ★ 부정을 구절 안에서만 찾으면 구절의 앞부분만 잘라 내는 우회가 통한다. 그래서
      그 구절이 속한 문장 전체를 본다(direct_support._relation_denial_after_roles와
      같은 이유). 부정과 유보를 같은 사유 코드로 묶는다 — 설계안 §3.3이 이 두 가지를
      한 코드(`combined_scope_negated_in_source`)로 정했다.
    """

    normalized = unicodedata.normalize("NFKC", str(source or ""))
    for sentence in SOURCE_SENTENCE_SPLIT_RE.split(normalized):
        sentence_key = _surface(sentence)
        if not sentence_key:
            continue
        if quote_key in sentence_key or sentence_key in quote_key:
            if SOURCE_NEGATION_RE.search(sentence_key) or SOURCE_HEDGED_NEGATION_RE.search(sentence_key):
                return COMBINED_SCOPE_NEGATED_IN_SOURCE
    return ""


def _combined_item_problem(
    item: Mapping, claim_key: str, sources_mapping: Mapping[str, str],
) -> tuple[str, str, str]:
    """관계 항목 하나를 설계안 §3.3의 순서대로 대조한다.

    통과하면 그 항목이 증명한 (범위 표면형, 관계 표면형)을 함께 준다 — 이후 «후보의
    어느 발동 자리를 덮었는지»를 세는 데 쓴다. 실패하면 두 표면형은 빈 문자열이다.
    """

    for key in (RELATION_SOURCE_KEY, COMBINED_SCOPE_KEY, COMBINED_RELATION_WORD_KEY,
                RELATION_QUOTE_KEY, RELATION_TYPE_KEY):
        if key in item and not isinstance(item[key], str):
            return COMBINED_SCOPE_FIELD_TYPE_INVALID, "", ""
    # ① 근거 id가 그 후보가 인용한 조각이어야 한다(빈 값 금지). sources_mapping은
    #   호출부가 이미 «그 후보 자신이 인용한 조각»만으로 좁혀 준 것이라, 그 안에 없으면
    #   인용하지 않은 조각을 댄 것이다.
    source_id = _text_field(item, RELATION_SOURCE_KEY).strip()
    source = (
        sources_mapping.get(source_id)
        if source_id and isinstance(sources_mapping, Mapping) else None
    )
    if not source_id or source is None:
        return COMBINED_SCOPE_SOURCE_NOT_CITED, "", ""
    # ② 제시 구절이 그 조각 원문에 «축자로» 있어야 한다.
    quote = _text_field(item, RELATION_QUOTE_KEY)
    quote_key = _surface(quote)
    if not quote.strip() or quote_key not in _surface(source):
        return COMBINED_SCOPE_QUOTE_NOT_IN_SOURCE, "", ""
    # ③ 범위가 후보에도, 그 원문 구절에도 있어야 한다.
    scope_key = _surface(_text_field(item, COMBINED_SCOPE_KEY))
    if not scope_key or scope_key not in claim_key or scope_key not in quote_key:
        return COMBINED_SCOPE_RANGE_NOT_IN_QUOTE, "", ""
    # ④ 관계(서술어)가 후보에도, 그 원문 구절에도 있어야 한다.
    relation_key = _surface(_text_field(item, COMBINED_RELATION_WORD_KEY))
    if not relation_key or relation_key not in claim_key or relation_key not in quote_key:
        return COMBINED_SCOPE_RELATION_NOT_IN_QUOTE, "", ""
    # ⑤ 원문 구절 «안에서» 범위와 관계가 같은 절에 있어야 한다(절을 넘어 이어 붙인
    #   두 사실이 아니어야 한다).
    if not _same_clause(quote, scope_key, relation_key):
        return COMBINED_SCOPE_SPLIT_ACROSS_CLAUSES, "", ""
    # ⑥ 그 구절이 걸린 원문 «문장»이 이 관계를 부정·유보하지 않아야 한다.
    denial = _negation_in_covering_sentence(source, quote_key)
    if denial:
        return denial, "", ""
    return "", scope_key, relation_key


def combined_relation_report(
    text: str,
    sources_mapping: Mapping[str, str],
    entries: object = None,
    cells: Sequence[str] | None = None,
) -> CombinedRelationReport:
    """수량 범위 결속을 «항상» 계산한다(스위치와 무관, 설계안 §4). 시험은 이 함수를 부른다.

    ★ ``cells``가 있으면(도식 후보) 1단계에서는 아무 판정도 하지 않는다 — 칸을 이어
      붙인 문자열로 걸면 서로 다른 칸의 수량과 술어가 하나로 묶인다. 칸마다 따로 거는
      2단계 확장은 이 설계안의 범위 밖이다.
    ★ 발동 자리가 여럿이면 «자리마다» 뒷받침이 있어야 한다. 항목을 더 넣거나 같은
      쌍을 되풀이해도 «같은 자리»만 다시 덮을 뿐 다른 자리는 덮이지 않는다.
    ★ 실적표 결속 원문(`TABLE_SOURCE_ID`)은 결합 근거의 «원문 후보»에서 뺀다
      (설계안 §4). 그 원문은 후보가 인용해서 들어온 값이 아니라 보고서에 표가 있으면
      모든 후보에 함께 실리는 값이라(`verify._grounding_candidate`), 그대로 두면
      검수 응답이 근거 id 를 실적표로 적는 것만으로 1단계를 통과한다.
    """

    if cells is not None:
        return CombinedRelationReport("", (), COMBINED_RELATION_RULE_VERSION)
    triggers = combined_relation_triggers(text)
    if not triggers:
        return CombinedRelationReport("", (), COMBINED_RELATION_RULE_VERSION)
    sources_mapping = {
        source_id: source_text
        for source_id, source_text in (
            sources_mapping.items() if isinstance(sources_mapping, Mapping) else ()
        )
        if source_id != TABLE_SOURCE_ID
    }
    claim_key = _surface(text)
    relations = [
        item for item in _combined_entries(entries)
        if str(item.get(RELATION_TYPE_KEY) or "").strip() == RELATION_COMBINED
    ]
    if not relations:
        return CombinedRelationReport(
            COMBINED_SCOPE_EVIDENCE_MISSING, triggers, COMBINED_RELATION_RULE_VERSION)
    accepted: list[tuple[str, str]] = []
    for item in relations:
        problem, scope_key, relation_key = _combined_item_problem(item, claim_key, sources_mapping)
        if problem:
            return CombinedRelationReport(problem, triggers, COMBINED_RELATION_RULE_VERSION)
        accepted.append((scope_key, relation_key))
    # ⑦ 후보의 «각 발동 자리»가 그 자리를 덮는 항목을 가져야 한다. 항목의 범위·관계가
    #   그 자리의 표면형 «안에» 있을 때만 그 자리를 덮은 것으로 본다 — 후보 전체 어딘가
    #   에 있다는 것만으로는(이미 위에서 확인함) 다른 자리를 덮지 못한다.
    for trigger in triggers:
        matched_key = _surface(trigger.matched)
        if not any(scope_key in matched_key and relation_key in matched_key
                   for scope_key, relation_key in accepted):
            return CombinedRelationReport(
                COMBINED_SCOPE_CLAIM_NOT_COVERED, triggers, COMBINED_RELATION_RULE_VERSION)
    return CombinedRelationReport("", triggers, COMBINED_RELATION_RULE_VERSION)


def combined_relation_problem(
    text: str,
    sources_mapping: Mapping[str, str],
    entries: object = None,
    cells: Sequence[str] | None = None,
) -> str:
    """`combined_relation_report`의 사유 코드만 돌려준다. 빈 문자열은 «문제 없음»이지
    승인이 아니다.

    ★ `COMBINED_RELATION_ENFORCED`가 False면(진단 우선 모드) 계산 자체를 하지 않고
      빈 문자열을 돌려준다 — 이 단계에서는 막지 않고 관측만 한다(설계안 §4).
    """

    if not COMBINED_RELATION_ENFORCED:
        return ""
    return combined_relation_report(text, sources_mapping, entries, cells).problem
