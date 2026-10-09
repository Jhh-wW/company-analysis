"""현재 상품의 직접 제조·제공 관계만 제품역할 보조 후보로 확인한다."""

import re
from bisect import bisect_left

from features.evidence_collection import product_role_relation_constants as c
from features.evidence_collection.business_slot_scope import business_slot_scope_problem
from features.evidence_collection.source_context import different_document_actor, validate_source_context
from features.evidence_collection import constants as collection_c
from features.evidence_collection import source_context_constants as source_c
from features.evidence_collection.section_context import section_heading_boundaries


def product_role_hypothetical_ranges(document_text: str):
    """같은 공식 절의 강한 예시 가정만 원문 좌표로 제한한다."""
    events = []
    for match in c.EXAMPLE_MARKER_RE.finditer(document_text):
        if c.HYPOTHETICAL_MARKER_RE.search(match.group()):
            events.append((match.start(), True))
            if len(events) > c.MAX_CONTEXT_EVENTS:
                return None
    for match in c.FICTIONAL_COMPANY_DECLARATION_RE.finditer(document_text):
        events.append((match.start(), True))
        if len(events) > c.MAX_CONTEXT_EVENTS:
            return None
    if not events:
        return ()
    for position, _heading in section_heading_boundaries(document_text):
        events.append((position, False))
        if len(events) > c.MAX_CONTEXT_EVENTS:
            return None
    for line in re.finditer(r'[^\n]+', document_text):
        surface = line.group().strip()
        heading = len(surface) <= collection_c.MAX_HEADING_CONTEXT_CHARS and (
            collection_c.DOCUMENT_HEADING_PATTERN.match(surface)
            or collection_c.PARAGRAPH_SUBHEADING_PATTERN.match(surface)
        )
        prefix = c.ACTUAL_PREFIX_RE.match(surface)
        actual = False
        if prefix:
            first_sentence = c.SENTENCE_RE.split(surface[prefix.end():])[0]
            actual = bool(c.SUBJECT_RE.match(first_sentence)
                          and c.ACTUAL_ACTION_RE.search(first_sentence)
                          and not c.EXCLUDED_RE.search(first_sentence))
        if heading or actual or c.ACTUAL_CASE_HEADING_RE.fullmatch(surface):
            events.append((line.start(), False))
            if len(events) > c.MAX_CONTEXT_EVENTS:
                return None
    ranges = []
    active = None
    for position, opens in sorted(events):
        if opens and active is None:
            active = position
        elif not opens and active is not None:
            ranges.append((active, position))
            active = None
    if active is not None:
        ranges.append((active, len(document_text)))
    return tuple(ranges)


def product_role_context_allows(text: str, *, start: int, end: int,
                                document_text: str, hypothetical_ranges) -> bool:
    """좌표 결속이 불명확하거나 가정 구간이면 새 보조 후보만 제외한다."""
    if (hypothetical_ranges is None or not 0 <= start < end <= len(document_text)
            or document_text[start:end] != text):
        return False
    index = bisect_left(hypothetical_ranges, (end,)) - 1
    return index < 0 or hypothetical_ranges[index][1] <= start


def _relation_actors(text: str, section_heading: str):
    """같은 문장의 상품·현재 역할을 가진 명시 행위자만 그대로 반환한다."""
    if not c.OFFICIAL_HEADING_RE.search(section_heading) or c.EXCLUDED_HEADING_RE.search(section_heading):
        return
    if business_slot_scope_problem(text, c.SLOT_ID):
        return
    units = tuple(sentence.strip() for sentence in c.SENTENCE_RE.split(text) if sentence.strip())
    for index, unit in enumerate(units):
        if not unit or len(unit) > c.MAX_SENTENCE_CHARS or c.EXCLUDED_RE.search(unit):
            continue
        subject = c.SUBJECT_RE.match(unit)
        if subject is None:
            continue
        if index + 1 < len(units) and c.CURRENT_ITEM_END_RE.search(units[index + 1]):
            continue
        body = unit[subject.end():]
        manufacturing = c.MANUFACTURING_RE.search(body)
        if (manufacturing and not c.GENERIC_OBJECT_RE.fullmatch(manufacturing['objects'].strip())
                and not c.EMBEDDED_SUBJECT_RE.search(manufacturing['objects'])):
            yield subject['actor']
            continue
        service = c.SERVICE_ROLE_RE.search(body)
        # 사업부 이름은 같은 제공 절의 앞에 적힌 정확 상품 이름과 결속한다.
        if service and re.search(
            rf'(?<![{c.NAME_BOUNDARY_CHARS}]){re.escape(service["name"])}(?![{c.NAME_BOUNDARY_CHARS}])',
            body[:service.start('name')],
        ):
            yield subject['actor']


def current_product_role_relation(text: str, section_heading: str = '') -> bool:
    """상품의 직접 관계 확인과 보고서 대상 법인 소속 판정을 분리한다."""
    return bool(tuple(_relation_actors(text, section_heading)))


def product_role_actor_matches(text: str, section_heading: str = '', *,
                               document_actor: str, source_context_json: str = '') -> bool:
    """관계 미확인 타법인을 대상 회사의 제품 준비 근거로 추가하지 않는다."""
    context = validate_source_context(source_context_json)
    if different_document_actor(source_context_json):
        return False
    def key(name):
        return c.LEGAL_NAME_RE.sub('', name).casefold()
    target = key(document_actor)
    if context and key(context['document_actor']) != target:
        return False
    if context and context['origin'] == source_c.SELF_SECTION_ORIGIN:
        from features.evidence_collection.product_role_table import product_table_role_supported
        import hashlib
        import json
        section = json.loads(context['section_context_json'])
        return (key(context['actor']) == target
                and section['fragment_sha256'] == hashlib.sha256(text.encode()).hexdigest()
                and product_table_role_supported(text, section_heading))
    actors = tuple(_relation_actors(text, section_heading))
    return bool(actors) and all(
        actor in c.SELF_ACTORS or actor.startswith('당사의')
        or (target and key(actor) == target)
        or (actor.endswith(('부문', '사업부')) and context
            and key(context['actor']) == target == key(context['document_actor']))
        for actor in actors
    )
