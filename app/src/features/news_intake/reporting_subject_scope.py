"""취재 상대 이름을 그 상대의 사업 행위 주어로 승격하지 않는다."""

import re
import unicodedata

from src.features.news_intake import constants as c
from src.features.news_intake import reporting_subject_scope_constants as rc
from src.features.news_intake.identity_names import company_query_names, mentions_target
from src.features.news_intake.models import NewsCompanyContext


def target_is_only_interview_recipient(text: str, company: NewsCompanyContext) -> bool:
    """대상 이름의 모든 출현이 같은 문장의 취재 상대격인 경우만 닫는다."""
    normalized = unicodedata.normalize("NFKC", text).casefold()
    for designator in c.CORPORATE_DESIGNATORS:
        normalized = normalized.replace(unicodedata.normalize("NFKC", designator).casefold(), " ")
    spans = []
    for name in company_query_names(company):
        key = "".join(char for char in unicodedata.normalize("NFKC", name).casefold() if char.isalnum())
        if not key:
            continue
        pattern = r"(?<![^\W_])" + c.NAME_SEPARATOR_PATTERN.join(re.escape(char) for char in key)
        pattern += rc.INTERVIEW_RECIPIENT_SUFFIX
        for match in re.finditer(pattern, normalized):
            sentence = next((value for value in rc.REPORTING_SENTENCE_RE.finditer(normalized)
                             if value.start() <= match.start() < value.end()), None)
            if sentence is None:
                continue
            # 목적격 만남 직후의 계약 체결은 취재 상대만 표시한 관계가 아니다.
            tail = normalized[match.end():sentence.end()]
            if (rc.MEETING_TRANSACTION_TAIL_RE.match(tail)
                    or rc.RECIPROCAL_TRANSACTION_RE.search(tail)):
                continue
            if rc.REPORTING_STATEMENT_RE.search(sentence.group()):
                spans.append((match.start(), match.end()))
    if not spans:
        return False
    # 다른 절의 명시 대상회사 행동을 취재 상대 관계와 함께 지우지 않는다.
    remaining = normalized
    for start, end in sorted(set(spans), reverse=True):
        remaining = remaining[:start] + " " * (end - start) + remaining[end:]
    return not mentions_target(remaining, company)
