"""원문 표의 부문·분모·기간을 추출기와 작성 검수기가 함께 보존한다."""
import re
import json
import hashlib
from collections.abc import Mapping

from src.shared import revenue_population_constants as c
from src.shared.report_evidence.section_context import parse_section_context


def revenue_population_context_start(text: str, header_start: int) -> int:
    """현재 절 안의 가까운 부문 머리말만 포함하고 앞 부문의 범위를 빌리지 않는다."""
    start = max(0, header_start - c.POPULATION_LOOKBACK_CHARS)
    prefix = text[start:header_start]
    sections = tuple(c.PARENT_SECTION_RE.finditer(prefix))
    if sections:
        start += sections[-1].end()
        prefix = text[start:header_start]
    scopes = tuple(c.POPULATION_SCOPE_RE.finditer(prefix))
    return start + scopes[-1].start() if scopes else header_start


def revenue_population_heading(source: str) -> str:
    match = c.POPULATION_SCOPE_RE.search(source)
    return match.group().strip("[]") if match else ""


def revenue_population_caption(caption: str, source: str, *, header_text: str | None = None) -> str:
    base = caption.split(c.POPULATION_CAPTION_SEPARATOR, 1)[0]
    # 행 중의 제품명·기술명에서 연결/분기라는 말을 빌리지 않는다.
    header = header_text if header_text is not None else source.split(";", 1)[0]
    heading = revenue_population_heading(header)
    basis_header = c.POPULATION_SCOPE_RE.sub("", header)
    basis = tuple(dict.fromkeys(match.group() for match in c.REPORTING_BASIS_RE.finditer(basis_header)))
    periods = tuple(dict.fromkeys(match.group() for match in c.REPORTING_PERIOD_RE.finditer(header)))
    scope = f"{heading}의 표 합계 기준" if heading else c.POPULATION_UNKNOWN_LABEL
    details = (scope, *basis, *(periods or (c.PERIOD_UNKNOWN_LABEL,)))
    return base + c.POPULATION_CAPTION_SEPARATOR + " · ".join(details)


def revenue_population_header_from_rows(evidence_rows) -> str:
    headers = set()
    for evidence in evidence_rows:
        payload = json.loads(evidence)
        source = payload["source"]
        end = payload["table"]["header"]["end"] - source["start"]
        if type(end) is not int or not 0 < end <= len(source["excerpt"]):
            raise ValueError("매출표의 집계 범위 머리말 좌표가 올바르지 않습니다")
        headers.add(source["excerpt"][:end])
    if len(headers) != 1:
        raise ValueError("매출표 행들의 집계 범위 머리말이 다릅니다")
    return headers.pop()


def revenue_population_caption_matches(caption: str, source: str, *, header_text: str | None = None) -> bool:
    if c.POPULATION_CAPTION_SEPARATOR not in caption:
        # 기존 봉인·저장 표는 그대로 읽는다. 새 표의 강제 표시는 생성 결속기에서 한다.
        return True
    return caption == revenue_population_caption(caption, source, header_text=header_text)


def revenue_population_claim_problem(candidate: str, sources: Mapping[str, str]) -> str:
    compact = "".join(candidate.split())
    revenue_claims = tuple(c.WHOLE_REVENUE_CLAIM_RE.finditer(compact))
    qualitative_claims = tuple(match for match in c.QUALITATIVE_REVENUE_CLAIM_RE.finditer(compact)
                               if not any(other.start() <= match.start() < other.end() for other in revenue_claims))
    matches = (*c.WHOLE_BUSINESS_COMPARISON_RE.finditer(compact),
               *c.PORTFOLIO_ROLE_COMPARISON_RE.finditer(compact),
               *c.PRIMARY_BUSINESS_ROLE_RE.finditer(compact))
    business_claims = tuple(match for match in matches if not any(
        other.start() < match.start() < other.end()
        or (other.start() == match.start() and other.end() > match.end()) for other in matches))
    if not revenue_claims and not business_claims and not qualitative_claims:
        return ""
    revenue_sources = [source for source in sources.values() if (
        c.REVENUE_ROW_RE.search(source) and ("|" in source or "%" in source)
        and revenue_population_source_header(source))]
    if not revenue_sources:
        return ""
    # 전사 범위가 원문에 직접 있으면 기존 의미·수치 검수에 맡긴다.
    if any(not revenue_population_heading(revenue_population_source_header(source))
           and c.WHOLE_REVENUE_CLAIM_RE.search("".join(revenue_population_source_header(source).split()))
           for source in revenue_sources):
        return ""
    if not revenue_claims and any(c.WHOLE_BUSINESS_POPULATION_RE.search(
            "".join(revenue_population_source_header(source).split())) for source in revenue_sources):
        return ""
    headings = tuple(filter(None, (
        revenue_population_heading(revenue_population_source_header(source))
        for source in revenue_sources)))
    for claim in (*revenue_claims, *(qualitative_claims if headings else ())):
        position = claim.start() + (1 if claim.group().startswith("의") else 0)
        if _explicit_company_share_support(compact, claim.start(), sources):
            continue
        if c.EXPLICIT_COMPANY_CLAIM_RE.search(claim.group()) or not _claim_bound_to_population(compact, position, headings):
            return "scope_condition_unbound"
    # 제품·서비스 정의의 '주력'은 구성비 추론과 다르다. 명시된 부문표만 있을 때
    # 그 표의 비중을 회사 전체 포트폴리오/사업 순위로 승격하는 경로를 제한한다.
    if headings:
        direct_primary = any(c.EXPLICIT_COMPANY_PRIMARY_BUSINESS_RE.search("".join(source.split()))
                             for source in sources.values() if source not in revenue_sources)
        for claim in business_claims:
            if direct_primary and not c.COMPARATIVE_MAGNITUDE_RE.search(claim.group()):
                continue
            if c.EXPLICIT_COMPANY_CLAIM_RE.search(claim.group()) or not _claim_bound_to_population(compact, claim.start(), headings):
                return "scope_condition_unbound"
    return ""


def revenue_population_context_problem(candidate: str, sources: Mapping[str, str],
                                       section_contexts: Mapping[str, str]) -> str:
    """검증된 자기 표의 부문 제목을 제약으로만 사용한다. 긍정 원문은 그대로 둔다."""
    constrained = {}
    for source_id, source in sources.items():
        serialized = section_contexts.get(source_id)
        if serialized:
            try:
                context = parse_section_context(serialized,
                    fragment_sha256=hashlib.sha256(source.encode('utf-8')).hexdigest())
            except (ValueError, TypeError):
                return 'scope_condition_unbound'
            heading = revenue_population_heading(context['text'])
        else:
            heading = ''
        constrained[source_id] = f'[{heading}]\n{source}' if heading else source
    return revenue_population_claim_problem(candidate, constrained)


def _explicit_company_share_support(candidate: str, position: int, sources: Mapping[str, str]) -> bool:
    """표의 추정 분모와 별개인 회사 명시 비중을 같은 품목·비율에서만 인정한다.

    원문 표·수치는 바꾸지 않으며 수치와 의미 검수도 면제하지 않는다.
    부문 표제의 자료는 호출자가 제외하고, 표행의 상품명은 직접 서술로 읽지 않는다.
    """
    sentence_start = 0
    for boundary in c.DIRECT_SHARE_SENTENCE_BOUNDARY_RE.finditer(candidate[:position]):
        sentence_start = boundary.end()
    prefix = candidate[sentence_start:position]
    tail = candidate[position:]
    for source in sources.values():
        if revenue_population_heading(source):
            continue
        unit_start = 0
        units = []
        for boundary in c.DIRECT_SHARE_SENTENCE_BOUNDARY_RE.finditer(source):
            units.append((unit_start, source[unit_start:boundary.start()]))
            unit_start = boundary.end()
        units.append((unit_start, source[unit_start:]))
        for start, unit in units:
            if "|" in unit:
                continue
            direct = c.DIRECT_COMPANY_SHARE_RE.match("".join(unit.split()))
            if not direct or not tail.startswith(direct["share"]):
                continue
            # 표와 별개인 직접 비중도 연결/개별 범위는 바꿀 수 없다.
            # 명시 후보 기준을 해당 직접 문장 앞의 가장 가까운 원기준에 결속한다.
            candidate_bases = {_share_reporting_basis(match['basis'])
                               for match in c.DIRECT_SHARE_REPORTING_BASIS_RE.finditer(prefix)}
            source_bases = tuple(c.DIRECT_SHARE_REPORTING_BASIS_RE.finditer(source, 0, start))
            source_basis = _share_reporting_basis(source_bases[-1]['basis']) if source_bases else ''
            if candidate_bases and candidate_bases != {source_basis}:
                continue
            item = c.DIRECT_SHARE_ITEM_SUFFIX_RE.sub("", direct["item"])
            prefixes = [prefix]
            # 쉼표 뒤 회사 자신으로 명시 전환한 해당 비중 절만 읽는다.
            # 앞의 일반 산업 설명이나 다른 회사 주어를 자기 증명으로 쓰지 않는다.
            tail_prefix = prefix.rsplit(',', 1)[-1]
            if tail_prefix != prefix and c.DIRECT_SHARE_CASE_START_RE.match(tail_prefix):
                prefixes.append(tail_prefix)
            for own_prefix in prefixes:
                subject = re.match(c.DIRECT_SHARE_SUBJECT_PREFIX + re.escape(item)
                                   + c.DIRECT_SHARE_CANDIDATE_SUBJECT, own_prefix) if item else None
                if subject and not c.DIRECT_SHARE_OTHER_SUBJECT_RE.search(own_prefix[subject.end():]):
                    return True
    return False


def _share_reporting_basis(basis: str) -> str:
    """별도·개별은 같은 법인 범위이고 연결은 별도 모집단이다."""
    return '연결' if basis == '연결' else '개별'


def _claim_bound_to_population(candidate: str, position: int, headings: tuple[str, ...]) -> bool:
    """각 비교 표현 바로 앞의 범위만 쓴다. 다른 절의 부문명으로 면제하지 않는다."""
    for heading in headings:
        label = "".join(heading.split())
        prefix = candidate[max(0, position - len(label) - c.POPULATION_CLAIM_LOOKBACK_CHARS):position]
        resets = tuple(c.POPULATION_SUBJECT_RESET_RE.finditer(prefix))
        if resets and c.COMPARATIVE_MAGNITUDE_RE.search(prefix[:resets[-1].start()]):
            continue  # 앞 부문 비교가 끝난 뒤 다시 명시된 회사 주어로 범위를 대여하지 않는다.
        pattern = re.escape(label) + r"(?:내|안|의|에서는|에서의|에서)(?:전체사업|사업|전체)?" + c.LOCAL_COMPARISON_SUBJECT_PATTERN + "$"
        if re.search(pattern, prefix):
            return True
    return False


def revenue_population_source_header(source: str) -> str:
    """행의 상품명을 전사 분모의 증명으로 빌리지 않는다."""
    match = c.REVENUE_SOURCE_HEADER_END_RE.search(source)
    return source[:match.start()] if match else ""


def revenue_product_name_span(raw_name: str, header: str) -> tuple[int, int] | None:
    """명시된 품목 열이 있을 때만 같은 원문 행의 그 칸을 표시명으로 고른다."""
    header_row = header.splitlines()[-1] if header.splitlines() else header
    labels = header_row.split("|")
    columns = raw_name.split("|")
    indices = [index for index, label in enumerate(labels) if c.REVENUE_ITEM_HEADER_RE.fullmatch(label.strip())]
    if len(indices) != 1 or indices[0] >= len(columns):
        return None
    index = indices[0]
    value = columns[index]
    start = sum(len(column) + 1 for column in columns[:index]) + len(value) - len(value.lstrip())
    end = start + len(value.strip())
    return (start, end) if end > start else None
