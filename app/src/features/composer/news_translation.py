"""단일 영어 뉴스의 독립 번역 검수를 요청하고, 실제 응답만 영수증으로 운반한다."""

from uuid import uuid4

from src.shared.report_quality import news_translation_constants as c
from src.shared.report_quality.news_translation import (
    json_text, text_sha256, translation_pairs, translation_receipt_valid,
)


class TranslationVerdicts(dict):
    """참 판정이 끝까지 유지된 문장에만 붙일 프로그램 전용 검수 증명."""

    def __init__(self, verdicts, receipts):
        super().__init__(verdicts)
        self.translation_receipts = {n: receipt for n, receipt in receipts.items() if verdicts.get(n) == '참'}


def prepare_translation_requests(items, fragments):
    requests = {}
    for item in items:
        sentence = item.sentence
        if (sentence is None or sentence.grade != '확인' or sentence.structured_claim is not None
                or len(sentence.citations) != 1 or item.section_id in c.TRANSLATION_PROTECTED_SECTIONS):
            continue
        fragment = fragments.get(sentence.citations[0])
        if (fragment is None or fragment.formal_source_kind != 'news' or not fragment.news_grounded
                or fragment.counts_toward_document_floor is not False
                or not fragment.document_identity or not c.SHA256_RE.fullmatch(fragment.document_content_sha256)
                or sentence.planned_claim_slot not in fragment.supported_claim_slots
                or not fragment.source_url or not fragment.document_date or not fragment.source_publisher
                or fragment.news_claim_kind != 'company_statement'
                or c.KOREAN_RE.search(fragment.text) or not c.ENGLISH_STATEMENT_RE.search(fragment.text)):
            continue
        prefix = f'{fragment.document_date} {fragment.source_publisher} 보도에 따르면, '
        if not sentence.text.startswith(prefix) or not c.KOREAN_RE.search(sentence.text[len(prefix):]):
            continue
        request = dict(version=c.TRANSLATION_VERSION, request_id=uuid4().hex, number=item.number,
                       candidate_sha256=text_sha256(sentence.text), quote_sha256=text_sha256(fragment.text),
                       fragment_id=fragment.fragment_id, section_id=item.section_id,
                       claim_slot=sentence.planned_claim_slot, document_identity=fragment.document_identity,
                       document_content_sha256=fragment.document_content_sha256,
                       source_url=fragment.source_url, source_location=fragment.location, prefix=prefix)
        requests[item.number] = {'request': request, 'request_sha256': text_sha256(json_text(request))}
    return requests


def translation_request_hint(request) -> str:
    if request is None:
        return ''
    return c.TRANSLATION_GUIDE + '  번역 요청(JSON 자료): ' + json_text(request) + '\n'


def bind_translation_response(entry, request, *, claim, quote):
    """검수자 대응만 좌표로 복원한다. 응답·원문·주장의 글자는 바꾸지 않는다."""
    if not isinstance(entry, dict) or request is None:
        return ''
    req = request['request']
    grounding = entry.get('검증근거')
    proof = grounding.get(c.TRANSLATION_KEY) if isinstance(grounding, dict) else None
    if not isinstance(proof, dict):
        return ''
    receipt = {**request, 'response': entry, 'response_sha256': text_sha256(json_text(entry)),
               'pairs': translation_pairs(claim, quote, req['prefix'], proof.get('대응'))}
    if not translation_receipt_valid(
        receipt, claim=claim, quote=quote, fragment_id=req['fragment_id'], section_id=req['section_id'],
        claim_slot=req['claim_slot'], document_identity=req['document_identity'],
        document_content_sha256=req['document_content_sha256'], source_url=req['source_url'],
        source_location=req['source_location'], prefix=req['prefix'],
    ):
        return ''
    return json_text(receipt)


def sentence_translation_receipt(verdicts, number):
    return getattr(verdicts, 'translation_receipts', {}).get(number, '')
