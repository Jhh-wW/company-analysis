"""원문 표의 부문·분모·기간을 추출기와 작성 검수기가 함께 보존한다."""
import re
import json
from collections.abc import Mapping

from src.shared import revenue_population_constants as c


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
    if not c.WHOLE_REVENUE_CLAIM_RE.search(compact):
        return ""
    revenue_sources = [source for source in sources.values() if (
        c.REVENUE_ROW_RE.search(source) and ("|" in source or "%" in source))]
    if not revenue_sources:
        return ""
    # 전사 범위가 원문에 직접 있으면 기존 의미·수치 검수에 맡긴다.
    if any(c.WHOLE_REVENUE_CLAIM_RE.search("".join(revenue_population_source_header(source).split())) for source in revenue_sources):
        return ""
    for source in revenue_sources:
        heading = revenue_population_heading(revenue_population_source_header(source))
        if heading and re.search(re.escape("".join(heading.split())) + r"(?:내|의|에서의)(?:전체)?매출", compact):
            return ""
    return "scope_condition_unbound"


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
