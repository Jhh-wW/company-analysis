"""매출 모집단·계약 목록의 수익 추론과 명시 부문의 투자 계획을 검사한다.

빈 사유는 전체 의미 승인이나 다른 시점·주어·수치 검사 면제가 아니다.
부문 제목은 범위를 좁히는 제약이며 긍정 사실의 원문에 합치지 않는다.
"""
import hashlib
import re
import unicodedata
from collections.abc import Mapping, Sequence

from src.features.composer.business_population_scope_constants import (
    ASSERTED_INCREASE_RE, CLAIM_REVENUE_CONTRAST_RE, CLAUSE_BOUNDARY_RE,
    EXPLICIT_SUBJECT_RE, INVESTMENT_PLAN_RE, LOCAL_OWNER_SUFFIX_RE,
    INVESTMENT_WORD_RE, PLAN_SUBJECT_END_RE, PURPOSE_END_RE, QUANTITY_SUBJECT_RE,
    INVESTMENT_PURPOSE_PREFIX_RE, INVESTMENT_PLAN_LABEL_END_RE,
    INVESTMENT_PURPOSE_LIST_SEPARATOR_RE, INVESTMENT_PURPOSE_LIST_MIN_ITEMS,
    REVENUE_METRIC_RE,
    LOCAL_SECTION_RE, OTHER_SECTION_SUBJECT_RE, POPULATION_SOURCE_WINDOW, REVENUE_CONTRAST_RE,
    REVENUE_OWNER_RE, SECTION_PART_RE, SOURCE_INVESTMENT_PLAN_RE, WHOLE_COMPANY_RE,
    CONTRACT_AMOUNT_COLUMN, CONTRACT_AMOUNT_RE, CONTRACT_PERIOD_COLUMN,
    CONTRACT_PERIOD_RE, CONTRACT_ROW_MIN_COLUMNS, EXPLICIT_REVENUE_PRIORITY_RE,
    REVENUE_COMPOSITION_RE, REVENUE_PRIORITY_RE,
    REVENUE_PRIORITY_TARGET_END_RE, REVENUE_PRIORITY_LEADING_SUBJECT_RE,
    REVENUE_PRIORITY_FOREIGN_OWNER_RE, REVENUE_SHARE_RE,
    REVENUE_PRIORITY_POSTPARTICLE_RE, REVENUE_PRIORITY_PREPARTICLE_RE,
    REVENUE_ITEM_COLUMN_RE, REVENUE_AMOUNT_COLUMN_RE, REVENUE_SHARE_COLUMN_RE,
    REVENUE_TABLE_OWNER_RE, REVENUE_TABLE_OWNERS,
    PRODUCT_SECTION_EXCLUSION_RE, PRODUCT_NEGATED_SECTION_RE, PRODUCT_SECTION_HEADING_RE,
    PRODUCT_ITEM_HEADER_RE, PRODUCT_ITEM_SPLIT_RE, PRODUCT_ITEM_IGNORED_WORDS,
    PRODUCT_ITEM_MIN_CHARS, PRODUCT_ASSERTION_BOUNDARY_RE, PRODUCT_ASSERTION_DENIAL_RE,
)
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.shared.report_evidence.section_context import parse_section_context


def _surface(text: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


def section_product_exclusion_problem(text: str, sources: Mapping[str, str]) -> str:
    """같은 품목이 속한 명시 부문을 부정하는 외부 배치만 제한한다."""
    items_by_owner: dict[str, set[str]] = {}
    for source in sources.values():
        owner = ''
        item_column = None
        for raw in unicodedata.normalize('NFKC', source).splitlines():
            heading = PRODUCT_SECTION_HEADING_RE.fullmatch(raw.strip())
            if heading:
                owner = _surface(heading['owner'])
                item_column = None
                continue
            columns = [cell.strip() for cell in raw.split('|')]
            if not owner or len(columns) < 2:
                continue
            headers = [index for index, cell in enumerate(columns)
                       if PRODUCT_ITEM_HEADER_RE.fullmatch(_surface(cell))]
            if headers and (item_column is None or item_column in headers):
                item_column = headers[0]
                continue
            if item_column is not None and item_column < len(columns):
                items_by_owner.setdefault(owner, set()).update(
                    _surface(item) for item in PRODUCT_ITEM_SPLIT_RE.split(columns[item_column])
                    if len(_surface(item)) >= PRODUCT_ITEM_MIN_CHARS
                    and _surface(item) not in PRODUCT_ITEM_IGNORED_WORDS
                )
    for clause in PRODUCT_ASSERTION_BOUNDARY_RE.split(unicodedata.normalize('NFKC', text)):
        if PRODUCT_ASSERTION_DENIAL_RE.search(clause):
            continue
        candidate = _surface(clause)
        exclusions = [(exclusion['owner'], candidate[exclusion.end():])
                      for exclusion in PRODUCT_SECTION_EXCLUSION_RE.finditer(candidate)]
        # 비~는 실제 단어 경계를 유지해 예비/준비 같은 명사 내부를 부정으로 읽지 않는다.
        exclusions.extend((_surface(exclusion['owner']), _surface(clause[exclusion.end():]))
                          for exclusion in PRODUCT_NEGATED_SECTION_RE.finditer(clause))
        for owner, tail in exclusions:
            for item in items_by_owner.get(owner, ()):
                if item not in tail:
                    continue
                # 같은 품목을 실제 다른 부문에서도 취급하는 자기 표는 유지한다.
                if not any(other != owner and item in items for other, items in items_by_owner.items()):
                    return SCOPE_CONDITION_UNBOUND
    return ''


def _contract_rows(source: str) -> list[str]:
    rows = []
    for row in source.splitlines():
        columns = [_surface(cell) for cell in row.split('|')]
        if (len(columns) >= CONTRACT_ROW_MIN_COLUMNS
                and columns[0] and columns[1]
                and CONTRACT_PERIOD_RE.fullmatch(columns[CONTRACT_PERIOD_COLUMN])
                and CONTRACT_AMOUNT_RE.fullmatch(columns[CONTRACT_AMOUNT_COLUMN])):
            rows.append(row)
    return rows


def contract_revenue_priority_problem(text: str, sources: Mapping[str, str]) -> str:
    """계약 행만으로 수익 우선순위·매출 비중을 새로 단정하는 경로를 닫는다.

    계약의 존재·금액 설명은 보존한다. 해석 표시도 매출 전제의 증명을 면제하지
    않는다. 별도 자기 원문에 직접 수익원 또는 매출 구성 수치가 있으면 기존
    모집단·수치·의미 검수가 판단하며, 여기의 빈 결과는 승인이 아니다.
    """
    candidate = _surface(text)
    if not REVENUE_PRIORITY_RE.search(candidate):
        return ''
    if not any(_contract_rows(source) for source in sources.values()):
        return ''
    for clause in CLAUSE_BOUNDARY_RE.split(candidate):
        for claim in REVENUE_PRIORITY_RE.finditer(clause):
            target = _revenue_priority_target(clause, claim)
            if not target or not _own_revenue_priority_support(target, sources):
                return SCOPE_CONDITION_UNBOUND
    return ''


def _revenue_priority_target(clause: str, claim: re.Match) -> str:
    if EXPLICIT_REVENUE_PRIORITY_RE.fullmatch(claim.group()):
        tail = REVENUE_PRIORITY_POSTPARTICLE_RE.sub('', clause[claim.end():])
        target = REVENUE_PRIORITY_TARGET_END_RE.split(tail, maxsplit=1)[0]
        if target and target not in ('다', '고', '라고'):
            return target
    prefix = REVENUE_PRIORITY_LEADING_SUBJECT_RE.sub('', clause[:claim.start()])
    return REVENUE_PRIORITY_PREPARTICLE_RE.sub('', prefix)


def _own_revenue_priority_support(target: str, sources: Mapping[str, str]) -> bool:
    for source in sources.values():
        contract_rows = set(_contract_rows(source))
        header = None
        table_owner_supported = True
        for row in source.splitlines():
            # 명시 타회사 표제의 소유권은 뒤 매출표에도 이어진다.
            # 새 자기 매출 표제가 나오면 그 표부터 기존 지원 경로를 복구한다.
            owners = tuple(REVENUE_TABLE_OWNER_RE.finditer(unicodedata.normalize('NFKC', row)))
            if owners:
                table_owner_supported = _surface(owners[-1]['owner']) in REVENUE_TABLE_OWNERS
                header = None
            # 프로젝트명·발주처 칸의 말을 매출 증명으로 빌리지 않는다.
            if row in contract_rows:
                header = None
                continue
            columns = [_surface(cell) for cell in row.split('|')]
            if (len(columns) == CONTRACT_ROW_MIN_COLUMNS - 1
                    and REVENUE_ITEM_COLUMN_RE.fullmatch(columns[0])
                    and REVENUE_AMOUNT_COLUMN_RE.fullmatch(columns[1])
                    and REVENUE_SHARE_COLUMN_RE.fullmatch(columns[2])):
                header = columns
                continue
            if (header and table_owner_supported
                    and len(columns) == len(header) and columns[0] == target
                    and CONTRACT_AMOUNT_RE.fullmatch(columns[1])
                    and REVENUE_SHARE_RE.fullmatch(columns[2])):
                return True
            if '|' not in row:
                header = None
            if not table_owner_supported:
                continue
            for unit in CLAUSE_BOUNDARY_RE.split(_surface(row)):
                if REVENUE_PRIORITY_FOREIGN_OWNER_RE.search(unit):
                    continue
                direct = tuple(EXPLICIT_REVENUE_PRIORITY_RE.finditer(unit))
                if any(_revenue_priority_target(unit, claim) == target for claim in direct):
                    return True
                # 같은 제공물의 매출 구성 비율만 비교 대상으로 인정한다.
                # 계약액·자산·이익 수치나 다른 상품의 비율은 면제 근거가 아니다.
                metric = re.search(re.escape(target) + r'(?:의)?매출(?:액|수익|비중|구성)?', unit)
                columns = unit.split('|')
                table_metric = target in columns and REVENUE_COMPOSITION_RE.search(unit)
                if (metric or table_metric) and REVENUE_SHARE_RE.search(unit):
                    return True
    return False


def opposing_revenue_population_problem(text: str, sources: Mapping[str, str]) -> str:
    """총매출 감소 뒤 하위 매출 증가를 같은 주어의 증가로 합친 경우만 찾는다."""
    candidate = _surface(text)
    for source in sources.values():
        source_key = _surface(source)
        for contrast in REVENUE_CONTRAST_RE.finditer(source_key):
            prefix = source_key[max(0, contrast.start() - POPULATION_SOURCE_WINDOW):contrast.start()]
            owners = list(REVENUE_OWNER_RE.finditer(prefix))
            if not owners:
                continue
            broad = owners[-1]['owner']
            narrow = contrast['owner']
            if broad == narrow:
                continue
            # 새 문장마다 주어를 다시 읽는다. 다른 절의 부문명을 빌리지 않는다.
            for clause in CLAUSE_BOUNDARY_RE.split(candidate):
                if not re.match(re.escape(broad) + r'(?:은|는|의|(?:총)?매출)', clause):
                    continue
                for claim in CLAIM_REVENUE_CONTRAST_RE.finditer(clause):
                    tail = claim['tail']
                    growth = list(ASSERTED_INCREASE_RE.finditer(tail))
                    if not growth:
                        continue
                    previous_end = 0
                    metric_owner = broad
                    for increase in growth:
                        segment = tail[previous_end:increase.start()]
                        quantities = list(QUANTITY_SUBJECT_RE.finditer(segment))
                        revenues = list(REVENUE_METRIC_RE.finditer(segment))
                        if quantities and (not revenues or quantities[-1].start() > revenues[-1].start()):
                            metric_owner = 'quantity'
                        elif revenues:
                            revenue = revenues[-1]
                            owner_prefix = segment[:revenue.start()]
                            metric_owner = narrow if re.search(re.escape(narrow) + r'(?:의)?$', owner_prefix) else broad
                        if metric_owner == broad:
                            return SCOPE_CONDITION_UNBOUND
                        previous_end = increase.end()
    return ''


def _investment_target(clause: str) -> str:
    """투자 목적을 명시한 짧은 문법에서만 같은 활동 예외를 비교한다."""
    words = list(INVESTMENT_WORD_RE.finditer(clause))
    if not words:
        return ''
    prefix = clause[:words[-1].start()]
    ends = list(PURPOSE_END_RE.finditer(prefix))
    if not ends:
        return ''
    target = prefix[:ends[-1].start()]
    subjects = list(PLAN_SUBJECT_END_RE.finditer(target))
    target = target[subjects[-1].end():] if subjects else target
    # 시점·계획의 연결어는 투자 목적 자체가 아니다. 날짜와 사업명은 지우지 않는다.
    return INVESTMENT_PURPOSE_PREFIX_RE.sub('', target, count=1)


def _whole_plan_supports(candidate: str, sources: Mapping[str, str]) -> bool:
    target = _investment_target(candidate)
    if not target:
        return False
    for source in sources.values():
        for unit in CLAUSE_BOUNDARY_RE.split(_surface(source)):
            if not WHOLE_COMPANY_RE.search(unit) or not SOURCE_INVESTMENT_PLAN_RE.search(unit):
                continue
            source_target = _investment_target(unit)
            if source_target and (target == source_target or target in source_target):
                return True
    return False


def _same_investment_purpose_list(candidate: str, source: str) -> bool:
    """동일한 목적 전체의 순서를 유지한 쉼표·및 나열만 비교한다."""
    candidate_items = tuple(INVESTMENT_PURPOSE_LIST_SEPARATOR_RE.split(candidate))
    source_items = tuple(INVESTMENT_PURPOSE_LIST_SEPARATOR_RE.split(source))
    return (
        len(candidate_items) >= INVESTMENT_PURPOSE_LIST_MIN_ITEMS
        and all(candidate_items)
        and candidate_items == source_items
    )


def _local_plan_supports(candidate: str, local: str, source: str) -> bool:
    """명시 투자 목적이 있으면 같은 부문의 자기 원문과만 연결한다."""
    target = _investment_target(candidate)
    source_targets = []
    purpose_found = False
    for unit in CLAUSE_BOUNDARY_RE.split(_surface(source)):
        for plan in INVESTMENT_PLAN_RE.finditer(unit):
            prefix = unit[:plan.end()]
            source_target = _investment_target(prefix)
            purpose_found = purpose_found or bool(source_target)
            owners = list(OTHER_SECTION_SUBJECT_RE.finditer(prefix))
            if owners and not re.fullmatch(
                re.escape(local) + r'(?:사업부문|사업부|부문|사업|분야)?(?:에서|은|는|이|가|의)',
                owners[-1].group(),
            ):
                continue
            if source_target:
                source_targets.append(source_target)
    if not target or not purpose_found:
        return True
    owner = re.escape(local) + r'(?:사업부문|사업부|부문|사업|분야)?(?:에서|은|는|이|가|의)?'
    target = re.sub(r'^' + owner, '', target)
    return bool(target and any(
        target == re.sub(r'^' + owner, '', source_target)
        or target in re.sub(r'^' + owner, '', source_target)
        or _same_investment_purpose_list(target, re.sub(r'^' + owner, '', source_target))
        for source_target in source_targets
    ))


def section_investment_plan_problem(
    text: str, sources: Mapping[str, str], contexts: Mapping[str, str],
    *, cells: Sequence[str] | None = None,
) -> str:
    """자기 조각의 검증된 사업부 제목에서 나온 투자 계획의 적용 범위를 유지한다."""
    relevant = []
    for source_id, source in sources.items():
        if not SOURCE_INVESTMENT_PLAN_RE.search(_surface(source)):
            continue
        raw = contexts.get(source_id, '')
        if not raw:
            continue
        try:
            context = parse_section_context(
                raw, fragment_sha256=hashlib.sha256(source.encode('utf-8')).hexdigest(),
            )
        except (ValueError, TypeError):
            return SCOPE_CONDITION_UNBOUND
        title = context.get('text', '')
        if not LOCAL_SECTION_RE.fullmatch(title):
            continue
        local = LOCAL_OWNER_SUFFIX_RE.sub('', _surface(SECTION_PART_RE.split(title[1:-1])[-1]))
        if local:
            relevant.append((local, source))
    if not relevant:
        return ''
    for clause in CLAUSE_BOUNDARY_RE.split(_surface(text)):
        previous_end = 0
        bound_local = None
        for plan in INVESTMENT_PLAN_RE.finditer(clause):
            # '투자 계획으로/계획은'은 뒤 실제 투자 행위의 도입어다.
            if (plan.group().endswith('계획')
                    and INVESTMENT_PLAN_LABEL_END_RE.match(clause[plan.end():])):
                continue
            segment = clause[previous_end:plan.end()]
            prefix = clause[previous_end:plan.start()]
            previous_end = plan.end()
            # 전체 예외는 이 투자 목적 하나에만 적용하고 뒤 계획은 다시 검사한다.
            if _whole_plan_supports(segment, sources):
                bound_local = None
                continue
            if bound_local and not EXPLICIT_SUBJECT_RE.search(prefix) and not OTHER_SECTION_SUBJECT_RE.search(prefix):
                if any(local == bound_local and _local_plan_supports(segment, local, source)
                       for local, source in relevant):
                    continue
            bound_local = None
            for local, source in relevant:
                # 첫 대상 칸이 정확한 부문일 때만 인접 계획 칸의 생략 주어를 허용한다.
                if cells and _surface(cells[0]) in {
                    local, local + '부문', local + '사업', local + '사업부', local + '사업부문',
                } and prefix.startswith(_surface(cells[0])):
                    bridge = prefix[len(_surface(cells[0])):]
                    if (not EXPLICIT_SUBJECT_RE.search(bridge) and not OTHER_SECTION_SUBJECT_RE.search(bridge)
                            and _local_plan_supports(segment, local, source)):
                        bound_local = local
                        break
                match = re.search(re.escape(local) + r'\)?(?:사업부문|사업부|부문|사업|분야)?(?:은|는|이|가|의|에서)', prefix)
                if (match and not EXPLICIT_SUBJECT_RE.search(prefix[match.end():])
                        and not OTHER_SECTION_SUBJECT_RE.search(prefix[match.end():])
                        and _local_plan_supports(segment, local, source)):
                    bound_local = local
                    break
            if bound_local is None:
                return SCOPE_CONDITION_UNBOUND
    return ''
