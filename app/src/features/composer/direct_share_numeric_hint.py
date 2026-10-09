"""같은 자기 직접 비중의 유효한 기존 증명 형식만 검수 입력에 안내한다."""
from collections.abc import Mapping
import json

from src.features.composer import direct_share_numeric_hint_constants as c
from src.shared import revenue_population_constants as population_constants
from src.shared.revenue_population_scope import (
    _explicit_company_share_support, revenue_population_claim_problem,
)


def direct_share_numeric_hint(text: str, sources: Mapping[str, str]) -> str:
    """원문·후보를 고치거나 검수 판정을 생성하지 않는다."""
    compact = ''.join(text.split())
    claims = tuple(population_constants.WHOLE_REVENUE_CLAIM_RE.finditer(compact))
    metric = c.DIRECT_SHARE_COMMON_METRIC_RE.search(text)
    if (len(claims) != 1 or revenue_population_claim_problem(text, sources)
            or not _explicit_company_share_support(compact, claims[0].start(), sources)):
        return ''
    # grounding_hint 호출 시점에는 기존 검증기 모듈이 이미 로드돼 있다.
    from src.features.composer.grounding import _numeric_valid
    for source_id, source in sources.items():
        for unit in population_constants.DIRECT_SHARE_SENTENCE_BOUNDARY_RE.split(source):
            quote = unit.strip()
            direct = population_constants.DIRECT_COMPANY_SHARE_RE.fullmatch(''.join(quote.split()))
            source_metric = c.DIRECT_SHARE_COMMON_METRIC_RE.search(quote)
            value = c.DIRECT_SHARE_PERCENT_RE.search(direct['share']) if direct else None
            if value is None:
                continue
            if not _explicit_company_share_support(compact, claims[0].start(),
                                                   {source_id: source}, direct_unit=quote):
                continue
            metrics = [(metric.group(), source_metric.group())] if metric and source_metric else []
            candidate_denominator = c.DIRECT_SHARE_DENOMINATOR_METRIC_RE.search(text)
            source_denominator = c.DIRECT_SHARE_DENOMINATOR_METRIC_RE.search(quote)
            if candidate_denominator and source_denominator:
                metrics.append((candidate_denominator.group(), source_denominator.group()))
            for candidate_metric, quote_metric in metrics:
                # 상품명이 붙은 공통 항목은 중간 앵커로 제외될 수 있다.
                # 같은 직접 구절의 분자 품목은 위에서 결속했고, 원문·표현 전체는 유지한다.
                proof = {'표현': text, '항목': candidate_metric, '근거': source_id, '원문': quote,
                         '원문항목': quote_metric, '원문값': value.group(), '후보값': value.group()}
                if not _numeric_valid(text, [proof], sources):
                    continue
                return (c.DIRECT_SHARE_NUMERIC_HINT_GUIDE + json.dumps(proof, ensure_ascii=False)
                        + '\n' + c.DIRECT_SHARE_NUMERIC_HINT_TAIL)
    return ''
