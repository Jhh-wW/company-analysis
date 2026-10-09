"""명시 공사 분류의 종류를 자기 계약명·같은 표 행에서 확인한다.

시설 종류가 없는 계약명 열거에는 적용하지 않는다. 빈 결과는 의미 승인이
아니며, 지원되지 않는 분류는 기존 근거 재작성 경로에 돌려준다.
"""
from collections.abc import Mapping
import unicodedata

from src.features.composer import project_business_scope_constants as c
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


def _surface(text: str) -> str:
    return ''.join(char for char in unicodedata.normalize('NFKC', text).casefold()
                   if not char.isspace() and not unicodedata.category(char).startswith('P'))


def _classifications(text: str) -> tuple[str, ...]:
    values = [value for match in c.CLASSIFICATION_RE.finditer(text)
              for part in c.CLASS_LIST_SPLIT_RE.split(match['classes'])
              if (value := _surface(part)) not in c.GENERIC_CLASSES]
    # 개별 계약과 종합 목록의 시설 분류는 같은 자기 계약 관계로 대조한다.
    if c.PROJECT_CONTEXT_RE.search(text):
        values.extend(_surface(match['kind']) for match in c.FACILITY_KIND_RE.finditer(text))
        values.extend(c.FACILITY_SUFFIX_RE.sub('', _surface(part))
                      for match in c.PROJECT_FIELD_LIST_RE.finditer(text)
                      for part in c.CLASS_LIST_SPLIT_RE.split(match['classes']))
    return tuple(dict.fromkeys(value for value in values if value not in c.GENERIC_CLASSES))


def _contract_rows(source: str) -> tuple[tuple[str, str], ...]:
    rows = []
    headers = ()
    own = True
    for raw in c.TABLE_SPLIT_RE.split(unicodedata.normalize('NFKC', source)):
        other, self_owner = tuple(c.OTHER_OWNER_RE.finditer(raw)), tuple(c.SELF_OWNER_RE.finditer(raw))
        if other or self_owner:
            last_other = other[-1].start() if other else -1
            last_self = self_owner[-1].start() if self_owner else -1
            own = last_self > last_other
            headers = ()
        if '|' not in raw:
            headers = ()
            continue
        cells = tuple(value.strip() for value in raw.strip().strip('|').split('|'))
        project_cols = [i for i, cell in enumerate(cells) if _surface(cell) in c.PROJECT_HEADERS]
        if project_cols:
            headers = cells if len(project_cols) == 1 else ()
            continue
        if not own or all(c.ALIGNMENT_RE.fullmatch(cell) for cell in cells):
            continue
        if headers:
            if len(cells) != len(headers):
                continue
            project_cols = [i for i, cell in enumerate(headers) if _surface(cell) in c.PROJECT_HEADERS]
            class_cols = [i for i, cell in enumerate(headers) if _surface(cell) in c.CLASS_HEADERS]
            if len(project_cols) != 1 or len(class_cols) > 1:
                continue
            project = cells[project_cols[0]]
            classification = cells[class_cols[0]] if class_cols else ''
        elif len(cells) == 4 and c.CONTRACT_PERIOD_RE.search(cells[2]):
            # 수집기의 단일 계약행은 계약명·발주처·기간·도급금액 순서다.
            project, classification = cells[0], ''
        else:
            continue
        if not project or c.NON_ACTUAL_RE.search(classification):
            continue
        rows.append((project, classification))
    return tuple(rows)


def _category_supported(category: str, project: str, classification: str) -> bool:
    # 분류 칸은 같은 행의 명시 종류다. 공장→시설 표현은 분류의 핵심 명사를 보존한다.
    if classification:
        declared = tuple(c.FACILITY_SUFFIX_RE.sub('', _surface(part))
                         for part in c.CLASS_LIST_SPLIT_RE.split(classification))
        if category in declared or category in _classifications(classification):
            return True
    # 발주처·금액·표제는 보지 않는다. 계약명 자체에 있는 분류 표현만 읽는다.
    name = _surface(project)
    if c.CONCRETE_FACILITY_RE.search(category):
        return category in name
    start = name.find(category)
    if start < 0:
        return False
    tail = name[start + len(category):]
    return bool(c.CLASSIFIED_FACILITY_SUFFIX_RE.match(tail)
                and not c.FACILITY_OWNER_TAIL_RE.match(tail))


def _claim_units(text: str):
    """새 문장·명시 공사 주어가 시작하면 앞 공사의 분류와 분리한다."""
    for sentence in c.SENTENCE_SPLIT_RE.split(text):
        boundaries = sorted({0, len(sentence),
                             *(match.start() for match in c.PROJECT_SUBJECT_RE.finditer(sentence))})
        for start, end in zip(boundaries, boundaries[1:]):
            if sentence[start:end].strip():
                yield sentence[start:end]


def _unit_problem(text: str, own_sources: Mapping[str, str], rows: tuple[tuple[str, str], ...]) -> str:
    classifications = _classifications(text)
    if not classifications:
        return ''
    normalized = _surface(text)
    mentioned = tuple(row for row in rows if _surface(row[0]) in normalized)
    subjects = tuple(project for match in c.PROJECT_SUBJECT_RE.finditer(text)
                     if (project := _surface(c.PROJECT_SUBJECT_PREFIX_RE.sub('', match['project'])))
                     not in c.GENERIC_PROJECTS)
    if subjects:
        matching = tuple(row for row in rows if any(subject == _surface(row[0]) for subject in subjects))
    else:
        matching = mentioned or rows
    for category in classifications:
        if any(_category_supported(category, *row) for row in matching):
            continue
        supported = False
        for source in own_sources.values():
            own = True
            for unit in _claim_units(unicodedata.normalize('NFKC', source)):
                other, self_owner = tuple(c.OTHER_OWNER_RE.finditer(unit)), tuple(c.SELF_OWNER_RE.finditer(unit))
                if other or self_owner:
                    own = (self_owner[-1].start() if self_owner else -1) > (other[-1].start() if other else -1)
                if not own or '|' in unit or c.NON_ACTUAL_RE.search(unit):
                    continue
                if category not in _classifications(unit):
                    continue
                # 특정 계약에 관한 후보는 같은 계약을 명시한 산문만 추가 지원으로 쓴다.
                project_names = subjects or tuple(_surface(row[0]) for row in mentioned)
                if project_names and not any(name in _surface(unit) for name in project_names):
                    continue
                supported = True
                break
            if supported:
                break
        if not supported:
            return SCOPE_CONDITION_UNBOUND
    return ''


def project_business_scope_problem(text: str, own_sources: Mapping[str, str]) -> str:
    """시설·업종 수식이 있는 공사 산문만 자기 인용과 대조한다."""
    normalized = unicodedata.normalize('NFKC', text)
    if not _classifications(normalized):
        return ''
    rows = tuple(row for source in own_sources.values() for row in _contract_rows(source))
    for unit in _claim_units(normalized):
        problem = _unit_problem(unit, own_sources, rows)
        if problem:
            return problem
    return ''
