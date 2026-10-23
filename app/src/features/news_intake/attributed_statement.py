"""영문 발언은 명시된 대상 회사·대표 주어와 같은 문장에서만 인정한다."""

import re

from src.features.news_intake import constants as c
from src.features.news_intake.identity_names import company_query_names
from src.features.news_intake.models import NewsCompanyContext
from src.features.news_intake.quote_selection_constants import QUOTE_SENTENCE_RE


def has_attributed_statement(text: str, company: NewsCompanyContext) -> bool:
    """기존 한국어 계약은 유지하며 영어 동사만 다른 회사에 빌려주지 않는다."""
    if any(marker in text for marker in c.ATTRIBUTED_STATEMENT_MARKERS):
        return True
    names = "|".join(re.escape(name) for name in company_query_names(company))
    if not names:
        return False
    company_name = rf"(?:{names})(?![\w])"
    role = c.ATTRIBUTED_ENGLISH_REPRESENTATIVE
    speaker = (
        rf"(?:{company_name}(?:['’]s|['’])?(?:\s+{role})?"
        rf"|(?:a|the)\s+{role}\s+(?:for|of)\s+{company_name})"
    )
    opening = re.compile(rf"^\s*{speaker}\s+{c.ATTRIBUTED_ENGLISH_VERB}\b", re.I)
    sentences = tuple(match.group() for match in QUOTE_SENTENCE_RE.finditer(text))
    # 추가 발언 동사나 별도 주어의 문장을 붙인 선택은 새 양성으로 만들지 않는다.
    return bool(sentences) and all(
        opening.search(sentence) and len(c.ATTRIBUTED_ENGLISH_VERB_RE.findall(sentence)) == 1
        for sentence in sentences
    )
