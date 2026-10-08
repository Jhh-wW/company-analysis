"""원본문을 바꾸지 않고 명시적 메타 목록과 자기 문장을 구분한다."""
from src.features.news_intake import article_text_scope_constants as c


def auxiliary_spans(text: str) -> tuple[tuple[int, int], ...]:
    """명사 목록으로 이어진 두 패널 표지만 판정하며 일반 기능 설명은 보존한다."""
    spans = []
    for match in c.AUXILIARY_PANEL_RE.finditer(text):
        keywords = match.group("keywords").strip()
        if (len(keywords.split()) < c.AUXILIARY_MIN_KEYWORD_TOKENS
                or keywords.endswith(c.NARRATIVE_KEYWORD_ENDINGS)
                or c.NARRATIVE_CONNECTOR_RE.search(keywords)
                or c.COMPLETE_STATEMENT_RE.search(keywords)):
            continue
        boundaries = list(c.SENTENCE_BOUNDARY_RE.finditer(text, 0, match.start()))
        prefix = text[boundaries[-1].end() if boundaries else 0:match.start()].strip()
        if c.NARRATIVE_PREFIX_RE.search(prefix):
            continue
        quoted_feature = False
        for opening, closing in c.FEATURE_QUOTE_PAIRS:
            if not prefix.endswith(opening) or not c.NARRATIVE_PREFIX_RE.search(prefix[:-1].rstrip()):
                continue
            close = text.find(closing, match.end())
            if close < 0:
                continue
            suffix = text[close + len(closing):].split("\n", 1)[0]
            if c.QUOTED_FEATURE_SUFFIX_RE.match(suffix) and c.COMPLETE_STATEMENT_RE.search(suffix):
                quoted_feature = True
                break
        if quoted_feature:
            continue
        # 관련기사의 링크 제목도 완결문일 수 있다. 같은 줄의 제목 끝을 추정하지
        # 않고 줄 전체를 목록으로 유지하되 다음 줄의 기자 문단은 포함하지 않는다.
        line_end = text.find("\n", match.end())
        end = len(text) if line_end < 0 else line_end
        spans.append((match.start(), end))
    return tuple(spans)


def outside_auxiliary_parts(text: str) -> tuple[str, ...]:
    """목록 밖의 원래 연속 부분만 반환한다. 합성 본문이나 인용으로 쓰지 않는다."""
    parts, start = [], 0
    for left, right in (*auxiliary_spans(text), (len(text), len(text))):
        parts.append(text[start:left])
        start = right
    return tuple(parts)


def overlaps_auxiliary(body: str, start: int, end: int) -> bool:
    return any(start < right and left < end for left, right in auxiliary_spans(body))


def auxiliary_only_body(text: str) -> bool:
    """목록 밖에 완결문이 없는 단편만 거절한다. 길이와 회사명으로 결정하지 않는다."""
    spans = auxiliary_spans(text)
    if not spans:
        return False
    start = 0
    for left, right in (*spans, (len(text), len(text))):
        if c.COMPLETE_STATEMENT_RE.search(text[start:left]):
            return False
        start = right
    return True
