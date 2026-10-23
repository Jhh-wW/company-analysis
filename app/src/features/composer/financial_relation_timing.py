"""자기 인용의 금융 결산표만으로 현재 관계 지속을 단정하지 않는다."""

import re

from src.features.composer import financial_relation_timing_constants as c


def _financial_roles(text):
    return frozenset(name for name, pattern in (
        ('guarantee', c.FINANCIAL_GUARANTEE_ROLE_RE),
        ('investment', c.FINANCIAL_INVESTMENT_ROLE_RE),
    ) if pattern.search(text))


def _mentions_provider(text, name):
    pattern = re.compile(r'(?<!\w)' + re.escape(name) + c.FINANCIAL_PROVIDER_MENTION_SUFFIX)
    return any(not c.FINANCIAL_PROVIDER_OTHER_ACTOR_RE.match(text[match.end():])
               for match in pattern.finditer(text))


def _same_current_official_relation(text, fragments, extra_sources, providers, baseline_date):
    roles = _financial_roles(text)
    required = {name for name in providers if _mentions_provider(text, name)}
    if not required:
        return False
    covered = set()
    for source_id, original in extra_sources:
        fragment = fragments.get(source_id)
        if (getattr(fragment, 'formal_source_kind', '') not in c.FINANCIAL_CURRENT_OFFICIAL_KINDS
                or not baseline_date
                or getattr(fragment, 'source_collected_on', '') != baseline_date):
            continue
        for clause in c.FINANCIAL_SOURCE_CLAUSE_RE.split(original):
            if (not c.FINANCIAL_CURRENT_SOURCE_RE.search(clause)
                    or not c.FINANCIAL_CURRENT_RELATION_RE.search(clause)
                    or c.FINANCIAL_SOURCE_UNCERTAIN_RE.search(clause)
                    or not roles <= _financial_roles(clause)):
                continue
            covered.update(name for name in required if _mentions_provider(clause, name))
    # 한 곳의 현재 보증을 다른 곳의 현재 관계로 빌리지 않는다.
    return required <= covered


def financial_relation_timing_problem(text: str, sources, fragments, *, section_id: str,
                                      baseline_date: str | None = None) -> str:
    """금융관계의 현재 지속과 결산 snapshot 표만 조합된 실제 경로에 한정한다."""
    if (section_id != c.FINANCIAL_RELATION_SECTION
            or not c.FINANCIAL_RELATION_RE.search(text)
            or not c.FINANCIAL_CURRENT_RELATION_RE.search(text)):
        return ''
    historical = c.FINANCIAL_HISTORICAL_PREFIX_RE.match(text)
    if historical and not c.FINANCIAL_REAFFIRMED_CURRENT_RE.search(text[historical.end():]):
        return ''
    if not sources:
        return ''
    period_ends = []
    providers = set()
    extra_sources = []
    for source_id, original in sources.items():
        fragment = fragments.get(source_id)
        kind = (getattr(fragment, 'formal_source_kind', '')
                or getattr(fragment, 'kind', ''))
        # 무관한 PG 소개나 MOU를 추가 인용해도 결산 금융표의 시점은 바뀌지 않는다.
        if (kind not in c.FINANCIAL_FILING_KINDS or '|' not in original
                or not c.FINANCIAL_SNAPSHOT_TABLE_RE.search(original)):
            extra_sources.append((source_id, original))
            continue
        period = getattr(fragment, 'reporting_period', '')
        period_ends.append(period.split('/')[-1] if period else '')
        for match in c.FINANCIAL_PROVIDER_COLUMN_RE.finditer(original):
            name = c.FINANCIAL_PROVIDER_FOOTNOTE_RE.sub('', match[1]).strip()
            if len(name) >= c.FINANCIAL_PROVIDER_MIN_CHARS and not c.FINANCIAL_PROVIDER_LABEL_RE.search(name):
                providers.add(name)
    if not period_ends:
        return ''  # 금융 결산표가 없는 보통 현재 사업·계약 검수는 그대로 둔다.
    if baseline_date and all(end == baseline_date for end in period_ends):
        return ''  # 모든 자기 표의 명시 보고기간 말일 자체가 기준일이면 과거 자료로 보지 않는다.
    if _same_current_official_relation(text, fragments, extra_sources, providers, baseline_date):
        return ''
    return c.FINANCIAL_SNAPSHOT_CURRENT_UNSUPPORTED
