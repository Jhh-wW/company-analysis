"""검수한 뉴스 산문의 숫자를 원문·출처·기사 날짜에 재결속한다."""

from __future__ import annotations

import hashlib
import json
import re

from src.shared.report_quality.constants import VERIFIED_PROSE_CLAIM_TYPE
from src.shared.report_quality.numeric_detection import korean_numeric_tokens

NEWS_NUMBER_TOKEN = re.compile(r"[+-]?\d[\d,]*(?:\.\d+)?(?:\s*(?:조원|억원|만원|천원|원|퍼센트|%|건|명|개|곳|대|회|년|월|일|배|톤|달러))?")
NEWS_PROTECTED_SECTIONS = frozenset({"identity", "competitive_position"})


def news_number_tokens(text: str) -> set[str]:
    return {re.sub(r"\s+", "", match.group()) for match in NEWS_NUMBER_TOKEN.finditer(text)} | korean_numeric_tokens(text)


def reviewed_news_prose_problems(fact, sources):
    """뉴스 산문이면 판정 목록, 다른 출처·구조화 계산이면 None을 돌려준다.

    counts=False만으로 수치 검산을 우회할 수 없다. 출처 종류, 정확 원문 해시,
    검수 상태와 산문 종류까지 모두 결속돼야 하며 공식 계산·비교는 대상이 아니다.
    """
    ids = fact.supporting_source_ids or (fact.source_id,)
    bound = [sources.get(source_id) for source_id in ids]
    if not bound or any(source is None or source.source_kind != "news" for source in bound):
        return None
    if fact.claim_type != VERIFIED_PROSE_CLAIM_TYPE:
        return None
    problems = []
    if fact.section_owner in NEWS_PROTECTED_SECTIONS:
        problems.append("뉴스 산문은 법인 정체·공식 비교를 대체할 수 없습니다")
    if not fact.evidence_binding_valid or fact.verification_state != "verified":
        problems.append("뉴스 산문의 원문 결속 또는 검수 상태가 유효하지 않습니다")
    if any(source.counts_toward_document_floor is not False for source in bound):
        problems.append("뉴스 산문의 출처가 공식 문서 수에 포함됐습니다")
    if fact.raw_value or fact.calculation or fact.display_value or fact.numeric_checks or fact.metric:
        return None
    primary = bound[0]
    prefix = f"{primary.published_on} {primary.publisher} 보도에 따르면, "
    if not primary.published_on or not primary.publisher or not fact.claim.startswith(prefix):
        problems.append("뉴스 산문에 정확한 기사 날짜·발행처가 없습니다")
    try:
        manifest = json.loads(fact.state_evidence)
        if not isinstance(manifest, list) or len(manifest) != len(bound):
            raise ValueError
        texts = []
        for record, source in zip(manifest, bound):
            exact_text = record["news_exact_text"]
            if not isinstance(exact_text, str) or not exact_text.strip():
                raise ValueError
            evidence_hash = hashlib.sha256(exact_text.encode("utf-8")).hexdigest()
            if (record["source_id"] != source.source_id
                or record["exact_sha256"] != evidence_hash
                or evidence_hash not in source.exact_evidence_hashes):
                raise ValueError
            texts.append(exact_text)
        claim_text = fact.claim[len(prefix):] if fact.claim.startswith(prefix) else fact.claim
        if not news_number_tokens(claim_text).issubset(news_number_tokens(" ".join(texts))):
            problems.append("뉴스 산문의 숫자·단위가 정확 원문과 다릅니다")
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        problems.append("뉴스 산문의 정확 원문 지문을 재검증할 수 없습니다")
    return tuple(problems)
