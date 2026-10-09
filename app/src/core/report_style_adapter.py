"""공개 문체 변환을 생성과 최종 원문 대조에서 같은 구현으로 연결한다."""


def styled_report_claim(
    text: str, *, as_of_date: str, news_exact_text: str = "",
    source_date: str = "", source_publisher: str = "",
) -> str:
    """검증 문장을 고치지 않고 실제 표시기가 만드는 글자만 계산한다."""
    from src.features.composer.style_normalizer import (
        annotate_past_dated_future,
        normalize_sentence_style,
    )
    from src.features.composer.news_constants import NEWS_ATTRIBUTION_TEMPLATE

    # 실제 표시기는 출처 접두사 뒤에 단일 뉴스 원문 전체를 옮긴 경우 문체를 유지한다.
    # 여기서도 그 예외를 보존한다. 출처 자격과 봉인은 호출자의 별도 검사가 맡는다.
    if news_exact_text and source_date and source_publisher:
        prefix = NEWS_ATTRIBUTION_TEMPLATE.format(date=source_date, publisher=source_publisher)
        if text.startswith(prefix) and text[len(prefix):].strip() == news_exact_text.strip():
            return text

    annotated, _count = annotate_past_dated_future(text, as_of_date)
    return normalize_sentence_style(annotated)
