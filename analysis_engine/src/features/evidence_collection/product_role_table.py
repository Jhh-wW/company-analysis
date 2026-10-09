"""회사 자기부문 선언이 정확히 결속된 표의 상품·용도만 보조 지원한다."""

import hashlib
import json
import re

from features.evidence_collection import product_role_table_constants as c
from features.evidence_collection import source_context_constants as source_c
from features.evidence_collection import product_role_relation_constants as relation_c
from features.evidence_collection.source_context import validate_source_context, different_document_actor
from features.evidence_collection.section_context import parse_section_context


def _key(text):
    return re.sub(r'\s+', '', text)


def _hypothetical_active(text):
    events = [(match.start(), True) for match in relation_c.EXAMPLE_MARKER_RE.finditer(text)
              if relation_c.HYPOTHETICAL_MARKER_RE.search(match.group())]
    for pattern in (relation_c.FICTIONAL_COMPANY_DECLARATION_RE, c.FICTIONAL_TABLE_DECLARATION_RE):
        events.extend((match.start(), True) for match in pattern.finditer(text))
    for line in re.finditer(r'[^\n]+', text):
        surface = line.group().strip()
        actual = relation_c.ACTUAL_PREFIX_RE.match(surface)
        if relation_c.ACTUAL_CASE_HEADING_RE.fullmatch(surface) or (
                actual and relation_c.SUBJECT_RE.match(surface[actual.end():])
                and relation_c.ACTUAL_ACTION_RE.search(surface[actual.end():])
                and not relation_c.EXCLUDED_RE.search(surface[actual.end():])):
            events.append((line.start(), False))
    return len(events) > relation_c.MAX_CONTEXT_EVENTS or bool(events and sorted(events)[-1][1])


def product_table_role_supported(text: str, section_heading: str = '') -> bool:
    if (len(text) > c.MAX_TABLE_CHARS or not c.PRODUCT_HEADING_RE.search(section_heading)
            or c.EXCLUDED_RE.search(section_heading) or c.EXCLUDED_RE.search(text)):
        return False
    rows = [tuple(cell.strip() for cell in row.split(' | ')) for row in text.split(' ; ')]
    if len(rows) < 2:
        return False
    header = tuple(_key(cell) for cell in rows[0])
    if any(header.count(name) != 1 for name in (c.ITEM_HEADER, c.USE_HEADER, c.TYPE_HEADER)):
        return False
    item, use, kind = (header.index(name) for name in (c.ITEM_HEADER, c.USE_HEADER, c.TYPE_HEADER))
    return any(len(row) == len(header) and _key(row[kind]) in c.OFFER_TYPES
               and _key(row[item]) not in c.EMPTY_VALUES
               and _key(row[use]) not in c.EMPTY_VALUES
               for row in rows[1:])


def self_section_declarations(document_text: str, *, document_actor: str):
    """공식 회사 개요의 현재 당사 구성 선언만 원문 위치와 함께 보관한다."""
    overview = c.OVERVIEW_RE.search(document_text)
    if overview is None or not document_actor:
        return ()
    next_major = c.MAJOR_RE.search(document_text, overview.end())
    end = next_major.start() if next_major else len(document_text)
    result = []
    for title in c.DECLARATION_TITLE_RE.finditer(document_text, overview.end(), end):
        sentence = c.DECLARATION_SENTENCE_RE.match(document_text, title.end())
        if sentence is None:
            continue
        text = sentence.group()
        prefix = document_text[overview.end():sentence.start()]
        owner = c.LEGAL_NAME_RE.sub('', document_actor)
        ownership = [(match.start(), c.LEGAL_NAME_RE.sub('', match.group(1)) == owner)
                     for pattern in (c.LOCAL_COMPANY_RE, c.COMPANY_LABEL_RE)
                     for match in pattern.finditer(prefix)]
        ownership.extend((match.start(), False)
                         for match in c.RELATED_ACTOR_HEADING_RE.finditer(prefix))
        for match in c.SELF_NAME_RE.finditer(prefix):
            name = source_c.COMPANY_NAME_RE.match(prefix, match.end())
            if name:
                ownership.append((match.start(), c.LEGAL_NAME_RE.sub('', name.group()) == owner))
        if _hypothetical_active(document_text[:sentence.start()]) or (ownership and not sorted(ownership)[-1][1]):
            continue
        if (source_c.SELF_DECLARATION_RE.fullmatch(text)
                and not source_c.SELF_DECLARATION_EXCLUDED_RE.search(text)):
            parts = tuple(_key(part) for part in source_c.SELF_DECLARATION_PART_RE.findall(text))
            if parts:
                result.append((sentence.start(), sentence.end(), text, parts))
                if len(result) > c.MAX_DECLARATIONS:
                    return ()
    # 선언의 중복·충돌은 새 보조 연결만 보수적으로 포기한다.
    return tuple(result) if len(result) == 1 else ()


def self_section_product_context(text: str, section_heading: str, *, start: int, end: int,
                                 document_text: str, document_actor: str,
                                 source_context_json: str, section_context_json: str,
                                 declarations) -> str:
    if not product_table_role_supported(text, section_heading) or not declarations:
        return ''
    if (not document_actor or document_text[start:end] != text
            or different_document_actor(source_context_json)):
        return ''
    from features.evidence_collection.product_role_relation import (
        product_role_context_allows, product_role_hypothetical_ranges,
    )
    if not product_role_context_allows(
        text, start=start, end=end, document_text=document_text,
        hypothetical_ranges=product_role_hypothetical_ranges(document_text),
    ):
        return ''
    section = parse_section_context(section_context_json, document_text=document_text)
    if not section:
        return ''
    scope_start = int(section['scope_location'].split('-')[0])
    prefix = document_text[scope_start:start]
    if len(prefix) > c.MAX_SCOPE_PREFIX_CHARS:
        return ''
    if c.TABLE_ATTRIBUTION_EXCLUDED_RE.search(prefix):
        return ''
    # 선언 뒤의 강한 가상 전환은 형식 절 제목만으로 실제 제품표가 되지 않는다.
    declaration_start, declaration_end, declaration_text, parts = declarations[0]
    example_prefix = document_text[declaration_end:start]
    if len(example_prefix) > c.MAX_SCOPE_PREFIX_CHARS:
        return ''
    if _hypothetical_active(example_prefix):
        return ''
    actor_key = c.LEGAL_NAME_RE.sub('', document_actor)
    if any(c.LEGAL_NAME_RE.sub('', match.group(1)) != actor_key
           for pattern in (c.LOCAL_COMPANY_RE, c.COMPANY_LABEL_RE)
           for match in pattern.finditer(prefix)):
        return ''
    table_rows = [tuple(cell.strip() for cell in row.split(' | ')) for row in text.split(' ; ')]
    actor_columns = [i for i, name in enumerate(table_rows[0]) if _key(name) in c.ACTOR_HEADERS]
    for row in table_rows[1:]:
        for index in actor_columns:
            if index < len(row):
                names = source_c.COMPANY_NAME_RE.findall(row[index])
                if any(c.LEGAL_NAME_RE.sub('', name) != actor_key for name in names):
                    return ''
    # 새 절의 앞 타법인 '당사'는 scope에 넣지 않는다. 현 절의 자기호칭 전환도
    # 새 선언으로 간주하지 않으며, 명시 다른 법인 주어를 우선한다.
    part = _key(section['text'][1:-1])
    declaration_start, declaration_end, declaration_text, parts = declarations[0]
    if part not in parts or declaration_end >= scope_start:
        return ''
    actor_start = document_text.find(document_actor)
    if actor_start < 0:
        return ''
    digest = lambda value: hashlib.sha256(value.encode()).hexdigest()
    raw = json.dumps(dict(version=source_c.CONTEXT_VERSION, origin=source_c.SELF_SECTION_ORIGIN,
                          text=declaration_text, location=f'{declaration_start}-{declaration_end}',
                          text_sha256=digest(declaration_text), actor=document_actor,
                          document_actor=document_actor,
                          document_actor_location=f'{actor_start}-{actor_start + len(document_actor)}',
                          document_actor_sha256=digest(document_actor), status='',
                          declaration_part=part, section_context_json=section_context_json),
                     ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    validate_source_context(raw, document_text=document_text)
    return raw
