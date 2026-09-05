"""DART 원문의 이름 표를 AI·네트워크 없이 읽는 규칙."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from typing import Callable, Iterable, Iterator

from src.shared.report_generation.models import exact_text_sha256

from .constants import (
    ALL_HEADER_KEYS,
    BUSINESS_HEADERS,
    COLUMN_SEPARATOR_RE,
    COMPANY_MARKERS,
    COMPANY_NAME_HEADERS,
    CONTRACT_NAME_HEADERS,
    CONTRACT_PERIOD_HEADERS,
    CONTRACT_PROGRESS_HEADERS,
    DESCRIPTION_HEADERS,
    HEADER_KEY_NOISE_RE,
    MAJOR_CONTRACT_SECTION_TITLES,
    MAX_NAME_CANDIDATES,
    MAX_NAME_CHARS,
    MIN_NAME_CHARS,
    NAMED_SERVICE_NAME_HEADERS,
    NAMED_SERVICE_SECTION_TITLES,
    NAME_EDGE_CHARS,
    NAME_SEPARATOR_RE,
    NUMERIC_OR_UNIT_ONLY_RE,
    PRODUCT_NAME_HEADERS,
    PRODUCT_SERVICE_SECTION_TITLES,
    REJECTED_NAME_KEYS,
    RELATION_HEADERS,
    SECTION_HEADING_RE,
    SEGMENT_HEADERS,
    SUBJECT_CONTRACT,
    SUBJECT_PRODUCT,
    SUBJECT_SEGMENT,
    SUBJECT_SUBSIDIARY,
    SUBSIDIARY_SECTION_TITLES,
    UNSPECIFIED_SOURCE_KIND,
)
from .models import NameCandidate


def _header_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return HEADER_KEY_NOISE_RE.sub("", normalized)


def _name_key(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def _clean_name(value: str) -> str:
    return value.strip(NAME_EDGE_CHARS)


def _is_valid_name(value: str) -> bool:
    if not (MIN_NAME_CHARS <= len(value) <= MAX_NAME_CHARS):
        return False
    key = _header_key(value)
    if not key or key in ALL_HEADER_KEYS or key in REJECTED_NAME_KEYS:
        return False
    if NUMERIC_OR_UNIT_ONLY_RE.fullmatch(value) is not None:
        return False
    return any(character.isalpha() for character in value)


def _split_columns(raw_line: str) -> tuple[str, ...]:
    if not raw_line.strip():
        return ()
    cells = [cell.strip() for cell in COLUMN_SEPARATOR_RE.split(raw_line)]
    # Markdown 모양의 양 끝 ``|``은 열이 아니므로 빈 가장자리만 제거한다.
    if cells and not cells[0]:
        cells.pop(0)
    if cells and not cells[-1]:
        cells.pop()
    return tuple(cells)


def _section_pattern(titles: Iterable[str]) -> re.Pattern[str]:
    alternatives = "|".join(
        re.escape(title) for title in sorted(set(titles), key=len, reverse=True)
    )
    return re.compile(alternatives)


def _section_blocks(
    text: str, titles: tuple[str, ...]
) -> Iterator[tuple[str, str]]:
    if not isinstance(text, str) or not text.strip():
        return
    for match in _section_pattern(titles).finditer(text):
        boundary = SECTION_HEADING_RE.search(text, match.end())
        end = boundary.start() if boundary is not None else len(text)
        yield match.group(0), text[match.end():end]


def _header_index(cells: tuple[str, ...], accepted: frozenset[str]) -> int | None:
    for index, cell in enumerate(cells):
        if _header_key(cell) in accepted:
            return index
    return None


def _matching_header_indexes(
    cells: tuple[str, ...], accepted: frozenset[str]
) -> tuple[int, ...]:
    return tuple(
        index for index, cell in enumerate(cells) if _header_key(cell) in accepted
    )


def _table_rows(
    block: str,
    *,
    accepts_header: Callable[[tuple[str, ...]], bool],
) -> Iterator[tuple[tuple[str, ...], tuple[str, ...], str]]:
    """머리말과 열 수가 정확히 같은 행만 원문 그대로 돌려준다."""

    lines = block.splitlines()
    for header_line_index, raw_header in enumerate(lines):
        headers = _split_columns(raw_header)
        if not headers or not accepts_header(headers):
            continue
        for raw_line in lines[header_line_index + 1 :]:
            if not raw_line.strip():
                continue
            cells = _split_columns(raw_line)
            if accepts_header(cells):
                break
            if len(cells) != len(headers):
                continue
            yield headers, cells, raw_line.rstrip("\r")
        return


def _candidate(
    *,
    name: str,
    subject_kind: str,
    description: str,
    location: str,
    excerpt: str,
) -> NameCandidate | None:
    clean_name = _clean_name(name)
    if not _is_valid_name(clean_name) or clean_name not in excerpt:
        return None
    return NameCandidate(
        name=clean_name,
        subject_kind=subject_kind,
        description=description.strip(),
        source_kind=UNSPECIFIED_SOURCE_KIND,
        location=location,
        excerpt=excerpt,
        excerpt_sha256=exact_text_sha256(excerpt),
    )


def _append_candidate(
    output: list[NameCandidate],
    *,
    name: str,
    subject_kind: str,
    description: str,
    location: str,
    excerpt: str,
) -> None:
    candidate = _candidate(
        name=name,
        subject_kind=subject_kind,
        description=description,
        location=location,
        excerpt=excerpt,
    )
    if candidate is not None:
        output.append(candidate)


def parse_product_service_table(text: str) -> tuple[NameCandidate, ...]:
    """주요 제품·서비스 표의 부문과 금액 없는 이름 열을 읽는다."""

    output: list[NameCandidate] = []
    for location, block in _section_blocks(text, PRODUCT_SERVICE_SECTION_TITLES):
        for headers, cells, excerpt in _table_rows(
            block,
            accepts_header=lambda row: _header_index(row, PRODUCT_NAME_HEADERS)
            is not None,
        ):
            name_index = _header_index(headers, PRODUCT_NAME_HEADERS)
            if name_index is None:  # pragma: no cover - 머리말 관문의 불변식
                continue
            segment_index = _header_index(headers, SEGMENT_HEADERS)
            if segment_index is not None:
                _append_candidate(
                    output,
                    name=cells[segment_index],
                    subject_kind=SUBJECT_SEGMENT,
                    description="",
                    location=location,
                    excerpt=excerpt,
                )
            for name in NAME_SEPARATOR_RE.split(cells[name_index]):
                _append_candidate(
                    output,
                    name=name,
                    subject_kind=SUBJECT_PRODUCT,
                    description="",
                    location=location,
                    excerpt=excerpt,
                )
    return tuple(output)


def parse_named_service_table(text: str) -> tuple[NameCandidate, ...]:
    """금융업의 ``상품명 | 주요 내용`` 2열 표를 읽는다."""

    output: list[NameCandidate] = []

    def accepts_header(cells: tuple[str, ...]) -> bool:
        return (
            len(cells) == 2
            and _header_index(cells, NAMED_SERVICE_NAME_HEADERS) is not None
            and _header_index(cells, DESCRIPTION_HEADERS) is not None
        )

    for location, block in _section_blocks(text, NAMED_SERVICE_SECTION_TITLES):
        for headers, cells, excerpt in _table_rows(
            block, accepts_header=accepts_header
        ):
            name_index = _header_index(headers, NAMED_SERVICE_NAME_HEADERS)
            description_index = _header_index(headers, DESCRIPTION_HEADERS)
            if name_index is None or description_index is None:  # pragma: no cover
                continue
            _append_candidate(
                output,
                name=cells[name_index],
                subject_kind=SUBJECT_PRODUCT,
                description=cells[description_index],
                location=location,
                excerpt=excerpt,
            )
    return tuple(output)


def _is_special_relationship_location(location: str) -> bool:
    return "특수관계자" in location


def _marked_company_index(cells: tuple[str, ...]) -> int | None:
    for index, cell in enumerate(cells):
        if any(marker.casefold() in cell.casefold() for marker in COMPANY_MARKERS):
            return index
    return None


def parse_subsidiary_table(text: str) -> tuple[NameCandidate, ...]:
    """종속회사·종속기업 표의 회사명과 업종/주요 사업을 읽는다."""

    output: list[NameCandidate] = []

    def accepts_header(cells: tuple[str, ...]) -> bool:
        return (
            _header_index(cells, COMPANY_NAME_HEADERS) is not None
            or _header_index(cells, BUSINESS_HEADERS) is not None
        )

    for location, block in _section_blocks(text, SUBSIDIARY_SECTION_TITLES):
        for headers, cells, excerpt in _table_rows(
            block, accepts_header=accepts_header
        ):
            company_index = _header_index(headers, COMPANY_NAME_HEADERS)
            if company_index is None:
                company_index = _marked_company_index(cells)
            if company_index is None:
                continue

            relation_index = _header_index(headers, RELATION_HEADERS)
            if _is_special_relationship_location(location):
                if relation_index is None or "종속" not in _header_key(
                    cells[relation_index]
                ):
                    continue

            descriptions = tuple(
                cells[index]
                for index in _matching_header_indexes(headers, BUSINESS_HEADERS)
                if cells[index].strip()
            )
            _append_candidate(
                output,
                name=cells[company_index],
                subject_kind=SUBJECT_SUBSIDIARY,
                description=" · ".join(descriptions),
                location=location,
                excerpt=excerpt,
            )
    return tuple(output)


def parse_major_contracts(text: str) -> tuple[NameCandidate, ...]:
    """매출액 5% 이상 계약 표의 계약명과 기간·진행률을 읽는다."""

    output: list[NameCandidate] = []
    for location, block in _section_blocks(text, MAJOR_CONTRACT_SECTION_TITLES):
        for headers, cells, excerpt in _table_rows(
            block,
            accepts_header=lambda row: _header_index(row, CONTRACT_NAME_HEADERS)
            is not None,
        ):
            name_index = _header_index(headers, CONTRACT_NAME_HEADERS)
            if name_index is None:  # pragma: no cover - 머리말 관문의 불변식
                continue
            descriptions: list[str] = []
            period_index = _header_index(headers, CONTRACT_PERIOD_HEADERS)
            progress_index = _header_index(headers, CONTRACT_PROGRESS_HEADERS)
            if period_index is not None and cells[period_index].strip():
                descriptions.append(f"계약기간: {cells[period_index].strip()}")
            if progress_index is not None and cells[progress_index].strip():
                descriptions.append(f"진행률: {cells[progress_index].strip()}")
            _append_candidate(
                output,
                name=cells[name_index],
                subject_kind=SUBJECT_CONTRACT,
                description=" · ".join(descriptions),
                location=location,
                excerpt=excerpt,
            )
    return tuple(output)


def collect_name_candidates(
    text: str, *, source_kind: str
) -> tuple[NameCandidate, ...]:
    """네 규칙의 후보를 순서대로 합치고 이름 기준으로 중복·상한을 적용한다."""

    if not isinstance(text, str) or not text.strip():
        return ()
    output: list[NameCandidate] = []
    seen: set[str] = set()
    parsers = (
        parse_product_service_table,
        parse_named_service_table,
        parse_subsidiary_table,
        parse_major_contracts,
    )
    for parser in parsers:
        for candidate in parser(text):
            key = _name_key(candidate.name)
            if not key or key in seen:
                continue
            seen.add(key)
            output.append(replace(candidate, source_kind=str(source_kind)))
            if len(output) >= MAX_NAME_CANDIDATES:
                return tuple(output)
    return tuple(output)


__all__ = [
    "collect_name_candidates",
    "parse_major_contracts",
    "parse_named_service_table",
    "parse_product_service_table",
    "parse_subsidiary_table",
]
