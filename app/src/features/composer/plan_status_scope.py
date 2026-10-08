"""명시 계획과 같은 대상·활동의 상태를 자기 인용 두 구절에 결속한다.

빈 사유는 승인 표시가 아니다. 이 검사는 plan_status 산문의 배치 예외만 맡고
기존 주체·부문·수치·양태 검수와 미래 표 계약은 그대로 둔다.
"""
from collections.abc import Mapping

from src.features.composer.future_plan_constants import (
    FUTURE_EVIDENCE_MISSING, FUTURE_FIELD_TYPE_INVALID,
    FUTURE_MODE_MISDECLARED, FUTURE_QUOTE_NOT_IN_SOURCE,
    FUTURE_SOURCE_NOT_CITED, FUTURE_TARGET_NOT_BOUND,
    FUTURE_SECTION_NO_FORWARD_STATEMENT,
)
from src.features.composer.future_plan_guard import (
    _bounded_spans, _covering_sentences, _has_bounded, _normalized,
    _sentences, _slot_problem, _source_modality, _subject_problem,
    _target_bound_to_activity, has_forward_marker,
)
from src.features.composer.plan_status_constants import (
    COMPLETED_EXECUTION_SLOT, COMPLETED_EXECUTION_STATE_MISMATCH, PLAN_STATUS_FIELDS, PLAN_STATUS_KEY, PLAN_STATUS_SLOT,
    STATE_RE, STATUS_ACTIVITY_BOUNDARY_RE, STATUS_COMPLETION_FACTS, STATUS_CONDITIONAL_RE,
    STATUS_CURRENT_CONTEXT_RE, STATUS_NEGATION_RE, STATUS_OWNER_MODIFIER_RE,
    STATUS_PAST_CONTEXT_RE,
    STATUS_PLAN_DENIAL_RE,
)


def _state(text: str) -> str:
    text = _normalized(text)
    matches = list(STATE_RE.finditer(text))
    states = {match.lastgroup for match in matches
              if not STATUS_NEGATION_RE.search(STATUS_ACTIVITY_BOUNDARY_RE.split(text[match.end():], maxsplit=1)[0])}
    return next(iter(states)) if len(states) == 1 else ''


def plan_status_fact_state(text: str, claim_slot: str) -> tuple[str, str]:
    """이전 단계가 검증한 상태만 시점 metadata로 옮긴다. 승인 검사는 아니다."""
    if claim_slot != PLAN_STATUS_SLOT or has_forward_marker(text):
        return '', ''
    state = _state(text)
    if not state:
        return '', ''
    current = bool(STATUS_CURRENT_CONTEXT_RE.search(text) or state in {'in_progress', 'paused'})
    return ('present' if current else 'past'), state


def completed_execution_status_problem(text: str, claim_slot: str) -> str:
    """완료 칸에 진행상태만 쓴 산문을 완료 사실로 봉인하지 않는다."""
    if claim_slot != COMPLETED_EXECUTION_SLOT:
        return ''
    states = {match.lastgroup for match in STATE_RE.finditer(text)}
    if states and states <= {'in_progress', 'paused'} and not has_forward_marker(text):
        # 실제 완료·착공 사실과 현재 상태를 함께 적은 문장은 의미 검수에 남긴다.
        if not any(token in text for token in STATUS_COMPLETION_FACTS):
            return COMPLETED_EXECUTION_STATE_MISMATCH
    return ''


def _activity_clause(sentence: str, span: tuple[int, int]) -> str:
    """활동 다음의 첫 절까지만 상태로 읽는다. 뒤 다른 업무의 상태를 빌리지 않는다."""
    boundary = STATUS_ACTIVITY_BOUNDARY_RE.search(sentence, span[1])
    return sentence[:boundary.start()] if boundary else sentence


def _holders(quote: str, source: str, target: str, activity: str, candidate: str):
    quote = _normalized(quote).strip().rstrip('.;!?。')
    sentences = _covering_sentences(source, quote) or _sentences(quote)
    for sentence in sentences:
        if not _has_bounded(quote, target) or not _bounded_spans(quote, activity, verbal=True):
            continue
        for span in _bounded_spans(sentence, activity, verbal=True):
            # 실제 인용 안의 활동 위치만 읽되 원문 문장의 주어·조건 문맥은 유지한다.
            positions = [index for index in range(len(sentence)) if sentence.startswith(quote, index)]
            if not any(index <= span[0] and span[1] <= index + len(quote) for index in positions):
                continue
            if _target_bound_to_activity(sentence, target, span) and not _subject_problem(sentence, target, span[0], (candidate,)):
                yield sentence, span


def _modifier(sentence: str, target: str) -> str:
    position = sentence.find(target)
    match = STATUS_OWNER_MODIFIER_RE.search(sentence[:position]) if position >= 0 else None
    return ''.join(match.group().split()) if match else ''


def plan_status_prose_problem(
    text: str, sources: Mapping[str, str], evidence: object,
    *, claim_slot: str, baseline_date: str | None = None,
) -> str:
    """현재형 plan_status만 자기 계획·상태 두 정확 구절을 요구한다.

baseline_date는 호출 계약을 공유하지만 날짜를 새로 추정하지 않는다.
기존 시점·현재화 가드와 원문 기준일 결속은 부르는 쪽에서 계속 적용한다.
"""
    if claim_slot != PLAN_STATUS_SLOT or has_forward_marker(text):
        return ''
    wanted_state = _state(text)
    if not wanted_state:
        return FUTURE_SECTION_NO_FORWARD_STATEMENT
    items = evidence.get(PLAN_STATUS_KEY) if isinstance(evidence, Mapping) else None
    if items is None or items == []:
        return FUTURE_EVIDENCE_MISSING
    if type(items) is not list:
        return FUTURE_FIELD_TYPE_INVALID
    covered = set()
    sentences = _sentences(_normalized(text))
    for item in items:
        if type(item) is not dict or set(item) != PLAN_STATUS_FIELDS or any(type(value) is not str or not value.strip() for value in item.values()):
            return FUTURE_FIELD_TYPE_INVALID
        source = sources.get(item['근거'])
        if not isinstance(source, str):
            return FUTURE_SOURCE_NOT_CITED
        progress, plan = item['진행원문'], item['계획원문']
        if progress not in source or plan not in source:
            return FUTURE_QUOTE_NOT_IN_SOURCE
        target, activity = _normalized(item['대상']), _normalized(item['활동'])
        problem = _slot_problem(target, activity)
        if problem:
            return problem
        candidate_holders = []
        for index, sentence in enumerate(sentences):
            for span in _bounded_spans(sentence, activity, verbal=True):
                clause = _activity_clause(sentence, span)
                if not _target_bound_to_activity(sentence, target, span):
                    continue
                for match in STATE_RE.finditer(clause, span[1]):
                    if match.lastgroup == wanted_state:
                        candidate_holders.append((index, match.start()))
        if not candidate_holders:
            return FUTURE_TARGET_NOT_BOUND
        progress_holders = list(_holders(progress, source, target, activity, text))
        plan_holders = list(_holders(plan, source, target, activity, text))
        valid = False
        for current_sentence, span in progress_holders:
            if STATUS_PAST_CONTEXT_RE.search(current_sentence[:span[0]]) and not STATUS_CURRENT_CONTEXT_RE.search(current_sentence[:span[0]]):
                continue
            current_clause = _activity_clause(current_sentence, span)
            if STATUS_CONDITIONAL_RE.search(current_clause) or _state(current_clause[span[1]:]) != wanted_state:
                continue
            for plan_sentence, plan_span in plan_holders:
                if STATUS_CONDITIONAL_RE.search(plan_sentence) or STATUS_PLAN_DENIAL_RE.search(plan_sentence):
                    continue
                mode, modality_problem, polarity = _source_modality(plan_sentence, plan_span)
                if modality_problem or polarity or mode != '계획':
                    continue
                # 명시 계획 뒤 취소·완료 상태는 계속 진행의 증명이 아니다.
                plan_state = _state(plan_sentence[plan_span[1]:])
                if plan_state and plan_state != wanted_state:
                    continue
                left, right = _modifier(current_sentence, target), _modifier(plan_sentence, target)
                if left != right and (left or right):
                    continue
                valid = True
                break
        if not valid:
            return FUTURE_MODE_MISDECLARED
        covered.update(candidate_holders)
    required = {(index, match.start()) for index, sentence in enumerate(sentences)
                for match in STATE_RE.finditer(sentence)}
    return '' if covered == required else FUTURE_TARGET_NOT_BOUND
