"""협력 상대 회사명과 행위주어를 같은 기사 연속 원문으로 결속한다."""
import re

from src.features.news_intake import constants as c
from src.features.news_intake import quote_selection_constants as qc
from src.features.news_intake import relation_subject_scope_constants as rc
from src.features.news_intake.identity_names import company_query_names
from src.features.news_intake.models import NewsCompanyContext


def _activity_terms(text: str) -> set[str]:
    terms = set()
    for match in rc.RELATION_TOKEN_RE.finditer(text):
        word = match.group().casefold()
        for suffix in rc.RELATION_TOKEN_PARTICLES:
            if word.endswith(suffix) and len(word) - len(suffix) >= rc.RELATION_TOKEN_MIN_CHARS:
                word = word[:-len(suffix)]
                break
        if len(word) >= rc.RELATION_TOKEN_MIN_CHARS and word not in rc.RELATION_GENERIC_TOKENS:
            terms.add(word)
    return terms


def _has_other_actor(text: str) -> bool:
    return any(match.group("actor") not in c.SUBJECT_GENERIC_TERMS | rc.RELATION_GENERIC_TOKENS
               and not match.group("actor").endswith(("과", "와"))
               for match in rc.RELATION_INNER_ACTOR_RE.finditer(text))


def starts_relation_target(text: str, company: NewsCompanyContext) -> bool:
    """검증한 회사명 전체와 닫힌 와/과 조사로 시작하는지만 확인한다."""
    return any(re.match(rc.RELATION_SENTENCE_PREFIX + re.escape(name) + r"\s*" + rc.RELATION_TARGET_CASE,
                        text, re.I) for name in company_query_names(company))


def bind_relation_subject(text: str, body: str, company: NewsCompanyContext,
                          start: int, *, max_chars: int) -> tuple[str, int] | None:
    """주어가 빠진 첫 관계절만 직전 동일 활동 문장과 결속한다.

    선택 원문은 수정하지 않는다. 반환값은 최종 인용의 별도 연속 범위다.
    """
    first = qc.QUOTE_SENTENCE_RE.match(text)
    if first is None:
        return text, start
    relation = first.group()
    target = next((match for name in company_query_names(company)
                   if (match := re.match(rc.RELATION_SENTENCE_PREFIX + re.escape(name) + r"\s*" + rc.RELATION_TARGET_CASE,
                                        relation, re.I))), None)
    if target is None:
        return text, start
    # 두 회사가 함께 주어로 명시된 완전한 문장은 앞 문장을 빌리지 않는다.
    if rc.RELATION_ACTOR_RE.match(relation[target.end():]):
        return text, start
    if _has_other_actor(relation[target.end():]):
        return None
    if (rc.RELATION_CONTRAST_RE.match(relation)
            or body[start:start + len(text)] != text or body.find(text, start + 1) >= 0
            or any(mark in relation for mark in rc.RELATION_QUOTE_MARKERS)):
        return None
    previous = next((unit for unit in reversed(list(qc.QUOTE_SENTENCE_RE.finditer(body[:start])))
                     if unit.group().strip()), None)
    if previous is None or body[previous.end():start].strip():
        return None
    if "\n" in body[previous.end():start] or previous.group().rstrip()[-1:] not in ".!?。":
        return None
    actor = rc.RELATION_ACTOR_RE.match(previous.group())
    if (actor is None or actor.group("actor") in c.SUBJECT_GENERIC_TERMS
            or rc.RELATION_CONTRAST_RE.match(previous.group())
            or any(mark in previous.group() for mark in rc.RELATION_QUOTE_MARKERS)):
        return None
    if _has_other_actor(previous.group()[actor.end():]):
        return None
    # 일반 회사/계약 어휘만으로 다른 행위의 주어를 대여하지 않는다.
    if not (_activity_terms(previous.group()[actor.end():]) & _activity_terms(relation[target.end():])):
        return None
    end = start + first.end()
    final_start = previous.start() + len(previous.group()) - len(previous.group().lstrip())
    final = body[final_start:end]
    if len(final) > max_chars or body.find(final, final_start + 1) >= 0:
        return None
    return final, final_start
