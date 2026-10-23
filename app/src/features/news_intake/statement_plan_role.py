"""검증된 동일 인용의 회사 대표 발언만 정상 미래 작성 후보로 전달한다."""

from dataclasses import replace
import datetime as dt
import re

from src.features.news_intake import constants as c
from src.features.news_intake import statement_plan_role_constants as pc
from src.features.news_intake.claim_role import sentence_ranges
from src.features.news_intake.identity_names import company_query_names, mentions_target
from src.features.news_intake.models import GroundedNewsExcerpt, NewsCompanyContext


def _direct_company_plan_statement(
    sentence: str, company: NewsCompanyContext, *, reject_reported_context: bool = False,
) -> bool:
    """선택 인용 한 문장 안의 동일 회사 발언과 자사 실행 의지만 인정한다."""
    names = company_query_names(company)
    match = next((matched for name in names if (matched := re.fullmatch(
        re.escape(name) + pc.REPRESENTATIVE_STATEMENT_SUFFIX, sentence, re.I))), None)
    if match is None or len(sentence) < c.GROUNDED_MIN_EXCERPT_CHARS:
        return False
    # 앞 인용에서 전달한 타사 계획을 마지막 인용의 '겠다'만으로 자사 의지로
    # 바꾸지 않는다. 새 kind 정리에서만 이 보수적 경계를 추가한다.
    if (reject_reported_context
            and pc.STATEMENT_PLAN_REPORTED_CONTEXT_RE.search(match['context_quote'] or '')):
        return False
    quotation = match['quote'][1:-1]
    if not pc.VOLITION_END_RE.search(quotation) or pc.CONDITIONAL_STATEMENT_RE.search(sentence):
        return False
    allowed = pc.SELF_SUBJECTS | pc.ATTRIBUTIVE_ACTION_STEMS | frozenset(names)
    return not any(subject not in allowed for subject in pc.EXPLICIT_SUBJECT_RE.findall(quotation))


def normalize_statement_plan_kind(
    kind: str, temporal: str, section: str, slot: str, text: str, company: NewsCompanyContext,
) -> str:
    """이미 미래 칸으로 선택한 직접 자사 의지의 kind만 정리한다. 검수 승인은 아니다."""
    if (kind == 'company_statement' and temporal == 'planned'
            and section == pc.STATEMENT_PLAN_SECTION and slot == pc.STATEMENT_PLAN_SLOT
            and not pc.STATEMENT_PLAN_KIND_DATED_RE.search(text)
            and _direct_company_plan_statement(text, company, reject_reported_context=True)):
        return 'company_plan'
    return kind


def _statement_plan_context_allows(
    excerpt: GroundedNewsExcerpt, company: NewsCompanyContext, body: str, as_of: dt.date | None,
) -> bool:
    """정확한 원문 좌표와 직접 계획의 현재 문맥을 함께 요구한다."""
    if (as_of is None or not body or body[excerpt.span_start:excerpt.span_end] != excerpt.text
            or pc.STATEMENT_PLAN_KIND_DATED_RE.search(excerpt.text)):
        return False
    try:
        if dt.date.fromisoformat(excerpt.candidate.published_on) > as_of:
            return False
    except ValueError:
        return False
    # 같은 원문 바로 다음의 명시적인 '이 계획' 철회만 연결한다. 타사·다른 계획은 제외.
    following = sentence_ranges(body[excerpt.span_end:])
    if following:
        start, end = following[0]
        sentence = body[excerpt.span_end:][start:end]
        for name in company_query_names(company):
            sentence = re.sub(r'^(?:(하지만|그러나|다만|이후)\s+)?' + re.escape(name)
                              + r'(?:는|가)\s+', lambda m: (m[1] + ' ') if m[1] else '', sentence)
        if pc.STATEMENT_PLAN_WITHDRAWAL_RE.fullmatch(sentence):
            return False
    return True


def selected_statement_plan_part(
    excerpt: GroundedNewsExcerpt, company: NewsCompanyContext, *, body: str, as_of: dt.date,
) -> GroundedNewsExcerpt | None:
    """이미 미래 칸에 선택된 복합 인용에서 직접 의지만 분리한다. 승인은 만들지 않는다."""
    if (excerpt.claim_kind != 'company_statement' or excerpt.temporal_status != 'planned'
            or excerpt.section_id != pc.STATEMENT_PLAN_SECTION
            or excerpt.claim_slot != pc.STATEMENT_PLAN_SLOT or excerpt.event_on
            or not _statement_plan_context_allows(excerpt, company, body, as_of)
            or pc.CONDITIONAL_STATEMENT_RE.search(excerpt.text)):
        return None
    ranges = sentence_ranges(excerpt.text)
    if len(ranges) != pc.STATEMENT_PLAN_SENTENCES:
        return None
    current_start, current_end = ranges[0]
    current = excerpt.text[current_start:current_end]
    subject = next((matched for name in company_query_names(company) if (matched := re.match(
        re.escape(name) + r'(?:은|는|이|가)\s+', current, re.I,
    ))), None)
    if not pc.STATEMENT_CURRENT_END_RE.search(current) or subject is None:
        return None
    # 같은 계획을 철회하는 앞 문장을 잘라 오래된 의지만 남기지 않는다.
    if pc.STATEMENT_CURRENT_WITHDRAWAL_RE.fullmatch(current[subject.end():]):
        return None
    start, end = ranges[-1]
    sentence = excerpt.text[start:end]
    if not _direct_company_plan_statement(sentence, company, reject_reported_context=True):
        return None
    return replace(excerpt, text=sentence, span_start=excerpt.span_start + start,
                   span_end=excerpt.span_start + end, split_from=excerpt.span_start)


def statement_plan_parts(
    excerpt: GroundedNewsExcerpt, company: NewsCompanyContext, *, body: str = '', as_of: dt.date | None = None,
) -> tuple[GroundedNewsExcerpt, ...] | None:
    """현재 원후보를 보존하고, 선택 인용 안의 독립 발언 한 문장만 추가한다.

    모델의 새 판정이나 검수 승인이 아니다. 분리된 후보도 기존 writer와 reviewer를
    거친다. 원선택 하나의 조각 예산과 실제 글자 상한은 기존 split_from 계약을 따른다.
    """
    if ((excerpt.section_id, excerpt.claim_slot) not in pc.STATEMENT_CURRENT_SLOTS
            or excerpt.claim_kind != 'company_statement'
            or excerpt.temporal_status != 'ongoing'):
        return None
    ranges = sentence_ranges(excerpt.text)
    if len(ranges) != pc.STATEMENT_PLAN_SENTENCES:
        return None
    current_start, current_end = ranges[0]
    start, end = ranges[-1]
    if not mentions_target(excerpt.text[current_start:current_end], company):
        return None
    sentence = excerpt.text[start:end]
    names = company_query_names(company)
    culture = (excerpt.section_id, excerpt.claim_slot) == pc.STATEMENT_CULTURE_SLOT
    if culture and not _statement_plan_context_allows(excerpt, company, body, as_of):
        return None
    if (excerpt.section_id == 'portfolio' or culture) and not any(re.match(
            re.escape(name) + r'(?:은|는|이|가)\s', excerpt.text[current_start:current_end], re.I,
    ) for name in names):
        return None
    if not _direct_company_plan_statement(sentence, company, reject_reported_context=culture):
        return None
    origin = excerpt.span_start
    current = replace(excerpt, split_from=origin)
    if excerpt.section_id == 'portfolio':
        current_text = excerpt.text[current_start:current_end]
        # 제품 칸에 섞인 미래 발언을 떼고, 기자의 현재 설명은 발언으로 승격하지 않는다.
        # 노력·행사 자체는 제품 기능이 아니므로 운영 후보로만 정상 작성·검수한다.
        completed_event = bool(pc.CURRENT_EVENT_ONLY_RE.search(current_text))
        activity_only = bool(pc.CURRENT_ACTIVITY_ONLY_RE.search(current_text)) or completed_event
        current = replace(
            current, text=current_text, claim_kind='reported_fact',
            section_id='operations_partners' if activity_only else excerpt.section_id,
            claim_slot='operations_partners:operating_role' if activity_only else excerpt.claim_slot,
            temporal_status='completed' if completed_event else excerpt.temporal_status,
            span_start=origin + current_start, span_end=origin + current_end,
        )
    return (
        current,
        replace(excerpt, text=sentence, section_id=pc.STATEMENT_PLAN_SECTION,
                claim_slot=pc.STATEMENT_PLAN_SLOT, claim_kind='company_plan',
                temporal_status='planned', event_on='',
                span_start=origin + start, span_end=origin + end, split_from=origin),
    )
