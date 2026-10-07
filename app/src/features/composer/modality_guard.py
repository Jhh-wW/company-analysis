"""같은 활동의 계획·가능 한정이 현재·완료 단정으로 바뀐 좁은 구문을 찾는다.

전체 의미 검증기가 아니다. 하다 계열 어근과 바로 앞 활동 대상이 같을 때만
비교하며, 빈 결과는 이 가드가 지원하는 모순을 찾지 못했다는 뜻이다.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import unicodedata

from src.features.composer import modality_constants as c


@dataclass(frozen=True)
class _Activity:
    stem: str
    anchor: str
    qualifier: str
    subject: str


def _normalized(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = c.CITATION_RE.sub("", text)
    # 인용부호만 지워 '선도'한다고 같은 절단을 원래 동사와 연결한다.
    # 제품명 부호를 지워도 제품명 자체를 서술어로 취급하지는 않는다.
    return text.translate(str.maketrans("", "", c.QUOTE_CHARACTERS))


def _activity(clause: str, match) -> _Activity | None:
    prefix = clause[:match.start()]
    words = c.TOKEN_RE.findall(prefix)
    while words and words[-1] in c.ACTIVITY_MODIFIERS:
        words.pop()
    if not words:
        return None
    # 목적어가 없을 때 회사 주어를 활동 대상으로 대신 쓰지 않는다.
    if c.TOPIC_RE.fullmatch(words[-1] + " "):
        return None
    anchor = c.PARTICLE_RE.sub("", words[-1]).casefold()
    if len(anchor) < c.MIN_ACTIVITY_ANCHOR_CHARS:
        return None
    topic = next((
        candidate for candidate in c.TOPIC_RE.finditer(prefix)
        if not c.RELATIVE_TOPIC_STEM_RE.fullmatch(candidate.group(1))
    ), None)
    subject = topic.group(1) if topic else ""
    if subject in c.GENERIC_SUBJECTS:
        # 명시된 보고 대상 주어를 주어 생략과 구별한다. 이를 빈 값으로
        # 지우면 '경쟁사는'의 실적을 '당행은'의 직접 근거로 빌리게 된다.
        subject = c.REPORT_COMPANY_SUBJECT
    qualifier = ""
    if anchor in c.GENERIC_ACTIVITY_OBJECTS and len(words) > 1:
        previous = words[-2]
        # 같은 '서비스'라도 제품명을 생략하거나 다른 제품명으로 바꾸면
        # 같은 활동이라는 확신이 없으므로 단어 하나로 대응시키지 않는다.
        if not c.TOPIC_RE.fullmatch(previous + " "):
            qualifier = previous.casefold()
    return _Activity(match.group("stem"), anchor, qualifier, subject)


def _activities(text: str) -> tuple[list[_Activity], list[_Activity]]:
    plans: list[_Activity] = []
    assertions: list[_Activity] = []
    for clause in c.CLAUSE_BREAK_RE.split(_normalized(text)):
        plan_matches = list(c.PLAN_RE.finditer(clause))
        for match in plan_matches:
            activity = _activity(clause, match)
            if activity:
                plans.append(activity)
        for match in c.ASSERTED_RE.finditer(clause):
            # '선도한다는 목표'처럼 현재형 인용 뒤에 붙은 계획도 그대로 유지한다.
            if any(plan.start() <= match.start() < plan.end() for plan in plan_matches):
                continue
            activity = _activity(clause, match)
            if activity:
                assertions.append(activity)
    return plans, assertions


def _same_activity(left: _Activity, right: _Activity) -> bool:
    return (
        left.stem == right.stem
        and left.anchor == right.anchor
        and left.qualifier == right.qualifier
        and not (left.subject and right.subject and left.subject != right.subject)
    )


def _direct_supports(assertion: _Activity, direct: _Activity) -> bool:
    # 계획과 달리 단정의 면제에는 같은 명시 주어가 필요하다. 주어가
    # 빠진 절을 별도 회사의 현재 실적으로 단정해서 연결하지 않는다.
    return _same_activity(assertion, direct) and assertion.subject == direct.subject


def _nominal_activity(clause: str, match, *, prefix: str) -> _Activity:
    """가능 명사와 실제 추진 명사를 동일한 대상·한정어·주어로 결속한다."""
    anchor = c.PARTICLE_RE.sub("", match.group("object")).replace(" ", "").casefold()
    words = c.TOKEN_RE.findall(prefix)
    qualifier = words[-1].casefold() if words and not c.TOPIC_RE.fullmatch(words[-1] + " ") else ""
    topic = next((item for item in c.TOPIC_RE.finditer(prefix)
                  if not c.RELATIVE_TOPIC_STEM_RE.fullmatch(item.group(1))), None)
    subject = topic.group(1) if topic else ""
    if subject in c.GENERIC_SUBJECTS:
        subject = c.REPORT_COMPANY_SUBJECT
    return _Activity("가능행위", anchor, qualifier, subject)


def _nominal_possibilities(text: str) -> tuple[list[_Activity], list[_Activity]]:
    possible, asserted = [], []
    for clause in c.CLAUSE_BREAK_RE.split(_normalized(text)):
        for match in c.POSSIBILITY_RE.finditer(clause):
            possible.append(_nominal_activity(clause, match, prefix=clause[:match.start()]))
        plans = tuple(c.PLAN_RE.finditer(clause))
        for match in c.ASSERTED_RE.finditer(clause):
            if match.group("stem") not in c.POSSIBILITY_ASSERTED_STEMS or any(
                    plan.start() <= match.start() < plan.end() for plan in plans):
                continue
            prefix = clause[:match.start()]
            target = c.NOMINAL_ACTIVITY_OBJECT_RE.search(prefix)
            if target:
                asserted.append(_nominal_activity(clause, target, prefix=prefix[:target.start()]))
    return possible, asserted


def modality_problem(candidate_text: str, sources_mapping: Mapping[str, str]) -> str:
    """인용한 원문만 받아 계획·가능→현재·완료 한정 손실 코드 또는 빈 문자열 반환.

    별개 활동의 계획, 후보가 유지한 목표, 같은 활동의 직접 현재·완료 근거는
    이 가드로 거절하지 않는다. 형태가 다른 동의어·수치에서 성과를 추론하는
    경우·목적어 생략·긴 병렬절의 계획 범위는 기존 독립 의미 검수에 남긴다.
    명사 가능성은 동일 명사 목적어의 추진·실현 단정만 다룬다.
    """
    _candidate_plans, candidate_assertions = _activities(candidate_text)
    source_plans: list[_Activity] = []
    direct_assertions: list[_Activity] = []
    for source in sources_mapping.values():
        plans, assertions = _activities(source)
        source_plans.extend(plans)
        direct_assertions.extend(assertions)
    for assertion in candidate_assertions:
        if not any(_same_activity(assertion, plan) for plan in source_plans):
            continue
        if any(_direct_supports(assertion, direct) for direct in direct_assertions):
            continue
        return c.MODALITY_PLAN_ASSERTED
    _possible, nominal_assertions = _nominal_possibilities(candidate_text)
    source_possibilities, direct_nominal_assertions = [], []
    for source in sources_mapping.values():
        possible, asserted = _nominal_possibilities(source)
        source_possibilities.extend(possible)
        direct_nominal_assertions.extend(asserted)
    for assertion in nominal_assertions:
        if (any(_same_activity(assertion, possible) for possible in source_possibilities)
                and not any(_direct_supports(assertion, direct) for direct in direct_nominal_assertions)):
            return c.MODALITY_POSSIBILITY_ASSERTED
    return ""
