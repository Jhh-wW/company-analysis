"""반대 매출 추세의 모집단과 명시 부문의 투자 계획만 검사한다.

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
    REVENUE_METRIC_RE,
    LOCAL_SECTION_RE, OTHER_SECTION_SUBJECT_RE, POPULATION_SOURCE_WINDOW, REVENUE_CONTRAST_RE,
    REVENUE_OWNER_RE, SECTION_PART_RE, SOURCE_INVESTMENT_PLAN_RE, WHOLE_COMPANY_RE,
)
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.shared.report_evidence.section_context import parse_section_context


def _surface(text: str) -> str:
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', text))


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
    return target[subjects[-1].end():] if subjects else target


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


def _local_plan_supports(candidate: str, local: str, source: str) -> bool:
    """명시 투자 목적이 있으면 같은 부문의 자기 원문과만 연결한다."""
    target = _investment_target(candidate)
    source_target = _investment_target(_surface(source))
    if not target or not source_target:
        return True
    owner = re.escape(local) + r'(?:사업부문|사업부|부문|사업|분야)?(?:에서|은|는|이|가|의)?'
    target = re.sub(r'^' + owner, '', target)
    source_target = re.sub(r'^' + owner, '', source_target)
    return bool(target and (target == source_target or target in source_target))


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
                match = re.search(re.escape(local) + r'(?:사업부문|사업부|부문|사업|분야)?(?:은|는|이|가|의|에서)', prefix)
                if (match and not EXPLICIT_SUBJECT_RE.search(prefix[match.end():])
                        and not OTHER_SECTION_SUBJECT_RE.search(prefix[match.end():])
                        and _local_plan_supports(segment, local, source)):
                    bound_local = local
                    break
            if bound_local is None:
                return SCOPE_CONDITION_UNBOUND
    return ''
