"""독립 검수의 영어 뉴스 번역 증명에만 적용하는 닫힌 계약."""

import re

TRANSLATION_KEY = '번역'
TRANSLATION_MANIFEST_KEY = 'news_translation_review'
TRANSLATION_VERSION = 'news-translation-review-v1'
TRANSLATION_MAX_PAIRS = 12
TRANSLATION_PROTECTED_SECTIONS = frozenset({'identity', 'competitive_position', 'summary'})
SHA256_RE = re.compile(r'[0-9a-f]{64}')
REQUEST_ID_RE = re.compile(r'[0-9a-f]{32}')
KOREAN_RE = re.compile(r'[가-힣]')
ENGLISH_RE = re.compile(r'[A-Za-z]')
ENGLISH_STATEMENT_RE = re.compile(r'\b(?:said|stated|explained|announced|emphasized|emphasised)\b', re.I)
ENGLISH_NEGATIVE_RE = re.compile(r'\b(?:no|not|never|neither|without|cannot)\b|n[’\']t\b', re.I)
KOREAN_NEGATIVE_RE = re.compile(r'없|않|아니|못했|못하|불가능|부인|부정')
TRANSLATION_REQUEST_FIELDS = frozenset({
    'version', 'request_id', 'number', 'candidate_sha256', 'quote_sha256',
    'fragment_id', 'section_id', 'claim_slot', 'document_identity',
    'document_content_sha256', 'source_url', 'source_location', 'prefix',
})
TRANSLATION_PROOF_FIELDS = frozenset({'요청SHA', '문장SHA', '원문SHA', '근거', '대응'})
TRANSLATION_GUIDE = (
    '  추가 검증 필요: 영어 뉴스 번역. 아래 번역 요청의 SHA를 그대로 돌려준다. '
    '참 판정은 검증근거.번역 객체에 요청SHA, 문장SHA, 원문SHA, 근거, 대응을 반드시 담는다. '
    '대응은 [{"원문":"정확한 연속 영어 표현","주장":"정확한 연속 한국어 표현"}]이다. '
    '영어 원문 전체와 확정된 날짜·매체 접두부를 뺀 주장 전체를 빠짐없이 순서대로 나눠 대응시킨다. '
    '각 표현은 해당 원문/주장에서 유일해야 한다. 주체·행동·부정·조건·미래·시점을 '
    '동시에 대조하고 의미가 다르거나 내용이 추가되면 거짓이다. '
    '원문에 없는 대응을 만들거나 한국어 주장에 영어를 끼워 넣지 않는다.\n'
)
