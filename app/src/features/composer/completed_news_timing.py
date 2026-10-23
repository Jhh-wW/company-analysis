"""완결된 사건 보도는 보도 당시 확인된 과거 사실로만 표시한다."""

from datetime import date

from src.features.composer.completed_news_timing_constants import (
    COMPLETED_NEWS_EVENT_RE, ISO_NEWS_DATE_RE,
    NEWS_EVENT_UNCERTAIN_OR_CURRENT_RE, NEWS_SENTENCE_BOUNDARY_RE,
    REVIEWED_ACRONYM_RE,
)


def _reviewed_wording(value: str) -> str:
    """이미 참으로 검수된 약어 병기와 완료형 어미만 비교용으로 통일한다."""
    return REVIEWED_ACRONYM_RE.sub(r"\1", value).replace("하였다.", "했다.")


def completed_news_as_of(
    claim: str, *, quote: str, publisher: str, published_on: str, baseline_date: str,
) -> str:
    """자기 보도 원문의 완료형과 정확한 출처 접두부가 맞는 경우만 날짜를 반환한다.

    반환값은 사건이 보도로 확인된 날이다. 실제 체결·도입일을 추측하지 않는다.
    이 함수는 원문의 진실성 또는 AI 검수 승인을 대신하지 않는다.
    """
    if not publisher or not all(isinstance(value, str) and ISO_NEWS_DATE_RE.fullmatch(value)
                                for value in (published_on, baseline_date)):
        return ""
    try:
        if date.fromisoformat(published_on) > date.fromisoformat(baseline_date):
            return ""
    except ValueError:
        return ""
    normalized_quote = " ".join(quote.split())
    normalized_claim = " ".join(claim.split())
    prefix = f"{published_on} {publisher} 보도에 따르면, "
    if not normalized_claim.startswith(prefix):
        return ""
    body = normalized_claim.removeprefix(prefix)
    if (not COMPLETED_NEWS_EVENT_RE.fullmatch(body)
            or NEWS_EVENT_UNCERTAIN_OR_CURRENT_RE.search(body)
            or NEWS_EVENT_UNCERTAIN_OR_CURRENT_RE.search(normalized_quote)):
        return ""
    # 원문 전체의 조건·진행 문맥을 위에서 검사한 뒤 완결문장 단위로 대조한다.
    # 문장·인용·원승인은 수정하지 않으며 약어 뜻의 진위도 새로 승인하지 않는다.
    if not any(_reviewed_wording(body) == _reviewed_wording(part)
               and COMPLETED_NEWS_EVENT_RE.fullmatch(part)
               for part in NEWS_SENTENCE_BOUNDARY_RE.split(normalized_quote)):
        return ""
    return published_on
