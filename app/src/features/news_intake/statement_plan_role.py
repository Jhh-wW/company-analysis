"""검증된 동일 인용의 회사 대표 발언만 정상 미래 작성 후보로 전달한다."""

from dataclasses import replace
import re

from src.features.news_intake import constants as c
from src.features.news_intake import statement_plan_role_constants as pc
from src.features.news_intake.claim_role import sentence_ranges
from src.features.news_intake.identity_names import company_query_names, mentions_target
from src.features.news_intake.models import GroundedNewsExcerpt, NewsCompanyContext


def statement_plan_parts(
    excerpt: GroundedNewsExcerpt, company: NewsCompanyContext,
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
    if excerpt.section_id == 'portfolio' and not any(re.match(
            re.escape(name) + r'(?:은|는|이|가)\s', excerpt.text[current_start:current_end], re.I,
    ) for name in names):
        return None
    match = next((matched for name in names if (matched := re.fullmatch(
        re.escape(name) + pc.REPRESENTATIVE_STATEMENT_SUFFIX, sentence, re.I))), None)
    if match is None or len(sentence) < c.GROUNDED_MIN_EXCERPT_CHARS:
        return None
    quotation = match['quote'][1:-1]
    if (not pc.VOLITION_END_RE.search(quotation)
            or pc.CONDITIONAL_STATEMENT_RE.search(sentence)):
        return None
    # 대표가 타사의 계획을 인용하는 경우는 자기 실행 의지가 아니다.
    allowed_subjects = pc.SELF_SUBJECTS | pc.ATTRIBUTIVE_ACTION_STEMS | frozenset(names)
    if any(subject not in allowed_subjects for subject in pc.EXPLICIT_SUBJECT_RE.findall(quotation)):
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
