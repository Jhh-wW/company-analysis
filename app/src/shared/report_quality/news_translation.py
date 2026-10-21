"""번역 검수의 실제 응답·정확 범위를 본문과 정본 출고에서 함께 검산한다."""

from __future__ import annotations

import hashlib
import json

from src.shared.report_quality import news_translation_constants as c
from src.shared.report_quality.constants import VERIFIED_PROSE_CLAIM_TYPE
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.source_identity import document_identity as source_document_identity


def json_text(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def text_sha256(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def translation_pairs(claim: str, quote: str, prefix: str, rows) -> list[dict] | None:
    """검수자가 반환한 정확 표현에서만 좌표를 계산한다. 번역은 생성하지 않는다."""
    if (not prefix or not claim.startswith(prefix) or not c.KOREAN_RE.search(claim[len(prefix):])
            or c.KOREAN_RE.search(quote) or not c.ENGLISH_STATEMENT_RE.search(quote)
            or not isinstance(rows, list) or not 1 <= len(rows) <= c.TRANSLATION_MAX_PAIRS):
        return None
    pairs = []
    cursor = len(prefix)
    source_cursor = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {'원문', '주장'}:
            return None
        source, target = row['원문'], row['주장']
        if (not isinstance(source, str) or not isinstance(target, str)
                or not source.strip() or not target.strip()
                or quote.count(source) != 1 or claim.count(target) != 1):
            return None
        source_start, target_start = quote.index(source), claim.index(target)
        if (target_start < cursor or claim[cursor:target_start].strip()
                or source_start < source_cursor or quote[source_cursor:source_start].strip()):
            return None
        if bool(c.ENGLISH_NEGATIVE_RE.search(source)) != bool(c.KOREAN_NEGATIVE_RE.search(target)):
            return None
        cursor = target_start + len(target)
        source_cursor = source_start + len(source)
        pairs.append({'source_start': source_start, 'source_end': source_start + len(source),
                      'claim_start': target_start, 'claim_end': cursor,
                      'source_text': source, 'claim_text': target})
    return pairs if not claim[cursor:].strip() and not quote[source_cursor:].strip() else None


def translation_receipt_valid(receipt, *, claim: str, quote: str, fragment_id: str,
                              section_id: str, claim_slot: str, document_identity: str,
                              document_content_sha256: str, source_url: str,
                              source_location: str, prefix: str) -> bool:
    """다른 문장·출처·요청의 승인은 재사용할 수 없다. 빈/구형 증명은 실패한다."""
    try:
        data = json.loads(receipt) if isinstance(receipt, str) else receipt
        if not isinstance(data, dict) or set(data) != {'request', 'request_sha256', 'response', 'response_sha256', 'pairs'}:
            return False
        req, response = data['request'], data['response']
        if not isinstance(req, dict) or set(req) != c.TRANSLATION_REQUEST_FIELDS:
            return False
        expected = dict(version=c.TRANSLATION_VERSION, candidate_sha256=text_sha256(claim),
                        quote_sha256=text_sha256(quote), fragment_id=fragment_id,
                        section_id=section_id, claim_slot=claim_slot, document_identity=document_identity,
                        document_content_sha256=document_content_sha256, source_url=source_url,
                        source_location=source_location, prefix=prefix)
        if (any(req.get(k) != v for k, v in expected.items())
                or section_id in c.TRANSLATION_PROTECTED_SECTIONS
                or not claim_slot.startswith(section_id + ':')
                or not document_identity or not source_url
                or not c.SHA256_RE.fullmatch(document_content_sha256)
                or not c.REQUEST_ID_RE.fullmatch(req['request_id'])
                or type(req['number']) is not int
                or type(response.get('번호')) is not int
                or data['request_sha256'] != text_sha256(json_text(req))
                or not isinstance(response, dict)
                or response.get('번호') != req['number'] or response.get('결과') != '참'
                or ('장' in response and response['장'] != section_id)
                or ('근거' in response and response['근거'] != [fragment_id])
                or data['response_sha256'] != text_sha256(json_text(response))):
            return False
        proof = response.get('검증근거', {}).get(c.TRANSLATION_KEY)
        if (not isinstance(proof, dict) or set(proof) != c.TRANSLATION_PROOF_FIELDS
                or proof['요청SHA'] != data['request_sha256']
                or proof['문장SHA'] != req['candidate_sha256']
                or proof['원문SHA'] != req['quote_sha256']
                or proof['근거'] != fragment_id):
            return False
        pairs = translation_pairs(claim, quote, prefix, proof['대응'])
        return pairs is not None and data['pairs'] == pairs
    except (ValueError, TypeError, KeyError, AttributeError):
        return False


def fact_translation_supported(fact, *, source=None) -> bool:
    """정본의 단일 뉴스 원문과 검수 영수증을 함께 대조한다. Fact 봉인은 호출자도 검사한다."""
    try:
        if (fact.claim_type != VERIFIED_PROSE_CLAIM_TYPE
                or (getattr(fact, 'verification_status', '') or fact.status) != 'verified'
                or not fact.evidence_binding or fact.evidence_binding != fact_evidence_binding(fact)):
            return False
        manifest = json.loads(fact.state_evidence)
        if not isinstance(manifest, list) or len(manifest) != 1:
            return False
        record = manifest[0]
        receipt = record[c.TRANSLATION_MANIFEST_KEY]
        req = receipt['request']
        if (record['exact_sha256'] != text_sha256(record['news_exact_text'])
                or tuple(fact.supporting_source_ids) != (record['source_id'],)
                or tuple(fact.supporting_source_identities) != (record['document_identity'],)
                or tuple(fact.supporting_evidence_hashes) != (record['exact_sha256'],)):
            return False
        if source is not None:
            kind = getattr(source.kind, 'value', source.kind)
            if (kind != '뉴스' or source.source_id != record['source_id']
                    or source_document_identity(source) != record['document_identity']
                    or source.document_content_sha256 != req['document_content_sha256']
                    or record['exact_sha256'] not in source.exact_evidence_hashes
                    or source.url != fact.source_url or source.location != fact.location
                    or source.published_at != fact.source_date or source.publisher != fact.source_publisher):
                return False
        return translation_receipt_valid(
            receipt, claim=fact.claim, quote=record['news_exact_text'], fragment_id=record['fragment_id'],
            section_id=fact.section_owner, claim_slot=fact.claim_slot, document_identity=record['document_identity'],
            document_content_sha256=req['document_content_sha256'], source_url=fact.source_url,
            source_location=fact.location, prefix=f'{fact.source_date} {fact.source_publisher} 보도에 따르면, ',
        )
    except (ValueError, TypeError, KeyError, AttributeError):
        return False
