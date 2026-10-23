"""상품 HTML의 현재 기능을 제목과 결속하고 예정·나열·행사 맥락을 제외한다."""
from __future__ import annotations

import re
from urllib.parse import urlsplit

from lxml import etree, html

from src.features.homepage.constants import (
    PRODUCT_ROLE_ACTION, PRODUCT_ROLE_DEFINITION, PRODUCT_ROLE_DETAIL_PATH,
    PRODUCT_ROLE_EXCLUDED_PATH, PRODUCT_ROLE_FUTURE_OR_CLOSED, PRODUCT_ROLE_MAX_TITLE_CHARS,
    PRODUCT_ROLE_NON_FUNCTION, PRODUCT_ROLE_OBJECT,
)
from src.features.homepage.wide_extract import extract_usable_ranges
from src.features.homepage.education_product import (
    EDUCATION_PRODUCT_SLOT, education_product_text_range_indices,
)

_FUTURE = re.compile(PRODUCT_ROLE_FUTURE_OR_CLOSED, re.I)
_NON_FUNCTION = re.compile(PRODUCT_ROLE_NON_FUNCTION, re.I)
_OBJECT = re.compile(PRODUCT_ROLE_OBJECT, re.I)
_ACTION = re.compile(PRODUCT_ROLE_ACTION, re.I)
_DEFINITION = re.compile(PRODUCT_ROLE_DEFINITION, re.I)


def _section_context(node) -> str:
    """해당 구역 자체의 문맥만 읽고 별도 하위 section의 상태는 섞지 않는다."""
    parts: list[str] = []

    def visit(current):
        if current.text:
            parts.append(current.text)
        for child in current:
            if child.tag not in {"section", "article"}:
                visit(child)
            if child.tail:
                parts.append(child.tail)

    visit(node)
    return " ".join(" ".join(parts).split())


def product_role_context_required(source_url: str) -> bool:
    """모든 공식 HTML 경로에 같은 상품 상세 문맥 검사를 적용한다."""
    path = urlsplit(source_url).path
    return bool(re.search(PRODUCT_ROLE_DETAIL_PATH, path, re.I))


def is_product_detail_url(source_url: str) -> bool:
    """수집 큐는 이미 발견된 상품 상세를 기사·행사 경로와 구분한다."""
    return product_role_context_required(source_url) and not re.search(
        PRODUCT_ROLE_EXCLUDED_PATH, urlsplit(source_url).path, re.I
    )


def current_product_role_ranges(raw_html: str, *, source_url: str, title: str,
                                usable_ranges: tuple[str, ...]) -> tuple[str, ...]:
    """기존 원문 문단만 선택한다. 제품명은 같은 문서의 원문 title로 함께 운반한다.

    짧은 '출시 예정' 제목이 일반 본문 추출에서 빠져도 DOM section의 상태를
    먼저 검사하므로 그 아래의 현재형 기능 문장을 현재 제품으로 승격하지 않는다.
    FAQ는 제품을 정의하는 답변만 허용하고 지원수단·수수료·해지 나열은 제외한다.
    """
    product_name = re.split(r"[|｜]", title, maxsplit=1)[0].strip()
    if (not is_product_detail_url(source_url)
            or not product_name or len(product_name) > PRODUCT_ROLE_MAX_TITLE_CHARS
            or _FUTURE.search(title) or _NON_FUNCTION.search(title)):
        return ()
    try:
        root = html.fromstring(raw_html)
    except (ValueError, TypeError, etree.ParserError):
        return ()
    for node in root.xpath("//script|//style|//nav|//header|//footer|//aside|//*[@role='dialog']"):
        node.drop_tree()
    accepted: set[str] = set()
    education_ranges = frozenset(usable_ranges[index] for index in education_product_text_range_indices(
        title=title, canonical_url=source_url, ranges=usable_ranges,
        candidate_slots=(EDUCATION_PRODUCT_SLOT,)))
    # 상위 div의 다른 상품·계획을 한 문장으로 뭉치지 않고 원문 leaf 문단만 본다.
    for node in root.xpath("//p|//dd|//div[not(div or p or section or article or ul or ol or table)]"):
        ancestors = list(node.iterancestors())
        sections = [parent for parent in ancestors if parent.tag in {"section", "article"}
                    or (parent.tag == "div" and parent.xpath("./h1|./h2|./h3"))]
        if not sections:
            sections = [node]
        # 안쪽 section이 바깥의 예정·FAQ 상태를 지우지 못하게 모든 조상 문맥을 본다.
        contexts = tuple(_section_context(section) for section in sections)
        if any(_FUTURE.search(context) for context in contexts):
            continue
        is_faq = any(re.search(r"자주\s*묻는|FAQ|Q\.", context, re.I) for context in contexts)
        for text in extract_usable_ranges(html.tostring(node, encoding="unicode"))[0]:
            if (text not in usable_ranges or _FUTURE.search(text) or _NON_FUNCTION.search(text)
                    or text.lstrip().startswith(("Q.", "Q ")) or not _OBJECT.search(text)
                    or not (_ACTION.search(text) or text in education_ranges)):
                continue
            if is_faq and (product_name not in text or not _DEFINITION.search(text)):
                continue
            accepted.add(text)
    return tuple(text for text in usable_ranges if text in accepted)
