"""DART 원문의 이름 표를 AI·네트워크 없이 읽는 규칙."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from typing import Callable, Iterable, Iterator

from src.shared.report_generation.models import exact_text_sha256

from .constants import (
    ALL_HEADER_KEYS,
    ARTIST_GROUP_HEADERS,
    ARTIST_MEMBER_HEADERS,
    BUSINESS_HEADERS,
    COLUMN_SEPARATOR_RE,
    COMPANY_MARKERS,
    COMPANY_NAME_HEADERS,
    CONTRACT_FIELD_HEADERS,
    CONTRACT_NAME_HEADERS,
    CONTRACT_PERIOD_HEADERS,
    CONTRACT_PROGRESS_HEADERS,
    DESCRIPTION_HEADERS,
    EXCLUDED_TABLE_TITLE_KEYWORDS,
    HEADER_KEY_NOISE_RE,
    IP_NAME_HEADERS,
    IP_STAGE_HEADERS,
    IP_TABLE_TITLE_KEYWORDS,
    MAJOR_CONTRACT_SECTION_TITLES,
    MAX_HEADER_ROWS,
    MAX_NAME_CANDIDATES,
    MAX_NAME_CHARS,
    MIN_NAME_CHARS,
    NAMED_SERVICE_NAME_HEADERS,
    NAMED_SERVICE_SECTION_TITLES,
    NAME_BRACKET_CLOSERS,
    NAME_BRACKET_OPENERS,
    NAME_EDGE_CHARS,
    NAME_SEPARATOR_RE,
    NUMERIC_OR_UNIT_ONLY_RE,
    NON_PERSON_NAME_HEADERS,
    PERSON_NAME_HEADERS,
    PERSON_NAME_HEADER_SUFFIXES,
    PERSON_ROLE_HEADER_SUFFIXES,
    PERSON_ROLE_HEADER_WORDS,
    PRODUCT_NAME_HEADERS,
    PRODUCT_SERVICE_SECTION_TITLES,
    REJECTED_NAME_KEYS,
    REJECTED_NAME_PREFIXES,
    RELATION_HEADERS,
    SECTION_HEADING_RE,
    SEGMENT_HEADERS,
    SUBJECT_CONTRACT,
    SUBJECT_IP,
    SUBJECT_PRODUCT,
    SUBJECT_SEGMENT,
    SUBJECT_SUBSIDIARY,
    SUBSIDIARY_SECTION_TITLES,
    TABLE_CELL_JOINER,
    TABLE_ROW_LOCATION_SUFFIX,
    UNSPECIFIED_SOURCE_KIND,
    UNTITLED_TABLE_LOCATION,
)
from .models import NameCandidate
from .tables import FilingTable


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
    if key.startswith(REJECTED_NAME_PREFIXES):
        return False
    if NUMERIC_OR_UNIT_ONLY_RE.fullmatch(value) is not None:
        return False
    return any(character.isalpha() for character in value)


def _split_names(value: str) -> tuple[str, ...]:
    """한 칸에 여러 이름이 붙어 있으면 나눈다. 괄호 안은 나누지 않는다.

    괄호 밖에서만 나누는 이유는 「기타(A/S) 등」처럼 이름 안에 든 ``/``까지
    쪼개면 없는 이름(``S) 등``)이 생기기 때문이다.
    """

    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    for character in value:
        if character in NAME_BRACKET_OPENERS:
            depth += 1
        elif character in NAME_BRACKET_CLOSERS:
            depth = max(0, depth - 1)
        if depth == 0 and NAME_SEPARATOR_RE.fullmatch(character):
            parts.append("".join(buffer))
            buffer = []
            continue
        buffer.append(character)
    parts.append("".join(buffer))
    return tuple(parts)


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
    """머리말과 열 수가 정확히 같은 행만 원문 그대로 돌려준다.

    ★ 사람 이름 열이 있는 행만 예외다 — 그 열을 뺀 값으로 발췌를 다시 잇는다.
      표 구조 경로(`_row_excerpt`)는 처음부터 그렇게 했는데 이 평문 경로는
      안 그래서, 「제품명 | 성명」 같은 표에서 실명이 발췌·작가 프롬프트·부록
      까지 그대로 갔다(정확 일치 머리글에서도 샜다).
    """

    lines = block.splitlines()
    for header_line_index, raw_header in enumerate(lines):
        headers = _split_columns(raw_header)
        if not headers or not accepts_header(headers):
            continue
        person_indexes = _person_column_indexes(headers)
        for raw_line in lines[header_line_index + 1 :]:
            if not raw_line.strip():
                continue
            cells = _split_columns(raw_line)
            if accepts_header(cells):
                break
            if len(cells) != len(headers):
                continue
            # 사람 열이 없으면 원문 줄을 «글자 그대로» 넘긴다(기존 동작 보존).
            excerpt = (
                _row_excerpt(cells, headers)
                if person_indexes
                else raw_line.rstrip("\r")
            )
            yield headers, cells, excerpt
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
            for name in _split_names(cells[name_index]):
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


# ─────────────────────────────────────────────────────────────────────
# 표 구조 입력 규칙
#
# 위 네 규칙은 「공백이 한 칸으로 접힌 평문」을 읽는다. 파이프라인이 실제로
# 넘기는 ``filing_text``가 바로 그 모양이라 표의 칸 경계가 남지 않고, 실측에서
# 후보가 0건이었다. 아래 규칙은 같은 판정을 **원문 표 구조**에 대고 돌린다.
# ─────────────────────────────────────────────────────────────────────


def _is_person_header_key(key: str) -> bool:
    """정규화된 머리글 키가 사람 이름 열인가 — 닫힌 어휘 규칙.

    「대표이사 성명」처럼 앞말이 붙은 복합 머리글까지 막는다. 정확 일치만
    보던 옛 규칙은 그런 열을 놓쳐 실명이 발췌·프롬프트까지 갔다.

    Args:
        key: `_header_key`로 정규화한 머리글.

    Returns:
        사람 이름 열이면 True. 「상품명」·「회사명」 같은 진짜 이름 열은 False.
    """

    if not key or key in NON_PERSON_NAME_HEADERS:
        return False
    if key in PERSON_NAME_HEADERS or key in ARTIST_MEMBER_HEADERS:
        return True
    if key.endswith(PERSON_NAME_HEADER_SUFFIXES):
        return True
    return any(
        word in key and (key == word or key.endswith(PERSON_ROLE_HEADER_SUFFIXES))
        for word in PERSON_ROLE_HEADER_WORDS
    )


def _person_column_indexes(headers: tuple[str, ...]) -> frozenset[int]:
    """사람 이름이 들어가는 열 번호를 모은다."""

    return frozenset(
        index
        for index, cell in enumerate(headers)
        if _is_person_header_key(_header_key(cell))
    )


def _row_excerpt(cells: tuple[str, ...], headers: tuple[str, ...]) -> str:
    """행의 칸을 이어 원문 발췌를 만든다. 사람 이름 열은 빼고 잇는다.

    이름 후보로 안 쓰는 것만으로는 부족하다 — 발췌는 작가 프롬프트와 부록의
    근거 원문에 그대로 실리므로, 멤버 본명이 거기까지 갈 이유가 없다.
    """

    skipped = _person_column_indexes(headers)
    kept = tuple(
        cell for index, cell in enumerate(cells) if index not in skipped
    )
    return TABLE_CELL_JOINER.join(kept).strip()


def _row_location(table: FilingTable, row_index: int) -> str:
    title = table.title.strip() or UNTITLED_TABLE_LOCATION
    return f"{title} · {row_index + 1}{TABLE_ROW_LOCATION_SUFFIX}"


def _is_header_like_row(cells: tuple[str, ...]) -> bool:
    """빈 칸을 뺀 모든 칸이 머리행 어휘면 자료 행이 아니라 머리행이다."""

    filled = [cell for cell in cells if cell.strip()]
    if not filled:
        return True
    return all(_header_key(cell) in ALL_HEADER_KEYS for cell in filled)


def _table_header_row(
    table: FilingTable, accepts: Callable[[tuple[str, ...]], bool]
) -> tuple[int, tuple[str, ...]] | None:
    """표 맨 위 몇 줄 안에서 조건에 맞는 머리행을 찾는다."""

    for index, cells in enumerate(table.rows[:MAX_HEADER_ROWS]):
        if accepts(cells):
            return index, cells
    return None


def _table_data_rows(
    table: FilingTable, header_index: int
) -> Iterator[tuple[int, tuple[str, ...]]]:
    for index in range(header_index + 1, len(table.rows)):
        cells = table.rows[index]
        if _is_header_like_row(cells):
            continue
        yield index, cells


def _named_index(
    headers: tuple[str, ...], accepted: frozenset[str]
) -> int | None:
    """이름 칸 후보를 고른다. 사람 이름 열은 절대 이름 칸으로 쓰지 않는다."""

    index = _header_index(headers, accepted)
    if index is None:
        return None
    if _is_person_header_key(_header_key(headers[index])):
        return None
    return index


def _title_says_ip(title: str) -> bool:
    return any(keyword in title for keyword in IP_TABLE_TITLE_KEYWORDS)


def _is_person_name(group: str, member: str) -> bool:
    """그룹 칸 값이 사람 이름인지 본다.

    솔로 활동은 그룹 칸에 활동명이 들어가고 아티스트 칸에 본명이 들어간다.
    두 값이 같거나 그룹 값이 본명 안에 통째로 들어 있으면(「민현」/「황민현」)
    그것은 그룹 이름이 아니라 사람 이름이므로 이름 후보로 내지 않는다.
    """

    group_key = _name_key(group)
    member_key = _name_key(member)
    if not group_key or not member_key:
        return False
    return group_key in member_key


def _is_excluded_name_table(table: FilingTable) -> bool:
    """재고·원재료·설비 명세는 제품 이름 표가 아니다."""

    return any(
        keyword in table.title for keyword in EXCLUDED_TABLE_TITLE_KEYWORDS
    )


def _ip_name_index(table: FilingTable, headers: tuple[str, ...]) -> int | None:
    """이 표의 이름 칸을 「대표 IP」로 읽어야 하면 그 칸 번호를 돌려준다."""

    index = _named_index(headers, IP_NAME_HEADERS)
    if index is not None:
        return index
    product_index = _named_index(headers, PRODUCT_NAME_HEADERS)
    if (
        product_index is not None
        and _header_index(headers, IP_STAGE_HEADERS) is not None
    ):
        # 「제품명 + 개발단계」는 바이오·제약의 파이프라인 표 모양이다.
        return product_index
    if not _title_says_ip(table.title):
        return None
    for accepted in (PRODUCT_NAME_HEADERS, NAMED_SERVICE_NAME_HEADERS):
        found = _named_index(headers, accepted)
        if found is not None:
            return found
    return None


def parse_product_service_tables(
    tables: Iterable[FilingTable],
) -> tuple[NameCandidate, ...]:
    """제품·서비스 표에서 사업부문과 제품·서비스 이름을 읽는다."""

    output: list[NameCandidate] = []
    for table in tables:
        if _is_excluded_name_table(table):
            continue
        found = _table_header_row(
            table,
            lambda cells: _named_index(cells, PRODUCT_NAME_HEADERS) is not None,
        )
        if found is None:
            continue
        header_index, headers = found
        if _ip_name_index(table, headers) is not None:
            continue  # 대표 IP 표는 아래 규칙이 따로 읽는다.
        name_index = _named_index(headers, PRODUCT_NAME_HEADERS)
        if name_index is None:  # pragma: no cover - 머리행 관문의 불변식
            continue
        segment_index = _named_index(headers, SEGMENT_HEADERS)
        for row_index, cells in _table_data_rows(table, header_index):
            excerpt = _row_excerpt(cells, headers)
            location = _row_location(table, row_index)
            if segment_index is not None and segment_index != name_index:
                _append_candidate(
                    output,
                    name=cells[segment_index],
                    subject_kind=SUBJECT_SEGMENT,
                    description="",
                    location=location,
                    excerpt=excerpt,
                )
            for name in _split_names(cells[name_index]):
                _append_candidate(
                    output,
                    name=name,
                    subject_kind=SUBJECT_PRODUCT,
                    description="",
                    location=location,
                    excerpt=excerpt,
                )
    return tuple(output)


def parse_named_service_tables(
    tables: Iterable[FilingTable],
) -> tuple[NameCandidate, ...]:
    """``상품명 | 주요 내용``처럼 이름과 설명이 함께 있는 표를 읽는다."""

    def accepts(cells: tuple[str, ...]) -> bool:
        return (
            _named_index(cells, NAMED_SERVICE_NAME_HEADERS) is not None
            and _header_index(cells, DESCRIPTION_HEADERS) is not None
        )

    output: list[NameCandidate] = []
    for table in tables:
        if _is_excluded_name_table(table):
            continue
        found = _table_header_row(table, accepts)
        if found is None:
            continue
        header_index, headers = found
        if _ip_name_index(table, headers) is not None:
            continue
        name_index = _named_index(headers, NAMED_SERVICE_NAME_HEADERS)
        description_index = _header_index(headers, DESCRIPTION_HEADERS)
        if name_index is None or description_index is None:  # pragma: no cover
            continue
        for row_index, cells in _table_data_rows(table, header_index):
            _append_candidate(
                output,
                name=cells[name_index],
                subject_kind=SUBJECT_PRODUCT,
                description=cells[description_index],
                location=_row_location(table, row_index),
                excerpt=_row_excerpt(cells, headers),
            )
    return tuple(output)


def parse_artist_contract_tables(
    tables: Iterable[FilingTable],
) -> tuple[NameCandidate, ...]:
    """``회사명 | 그룹 | 아티스트`` 표에서 **그룹 이름만** 읽는다.

    아티스트 칸은 사람 이름이라 이름 후보로 내보내지 않는다. ROWSPAN 채움 때문에
    같은 그룹이 여러 행에 걸치므로 값이 바뀌는 첫 행만 남긴다.
    """

    def accepts(cells: tuple[str, ...]) -> bool:
        return (
            _header_index(cells, COMPANY_NAME_HEADERS) is not None
            and _header_index(cells, ARTIST_GROUP_HEADERS) is not None
            and _header_index(cells, ARTIST_MEMBER_HEADERS) is not None
        )

    output: list[NameCandidate] = []
    for table in tables:
        found = _table_header_row(table, accepts)
        if found is None:
            continue
        header_index, headers = found
        company_index = _header_index(headers, COMPANY_NAME_HEADERS)
        group_index = _header_index(headers, ARTIST_GROUP_HEADERS)
        member_index = _header_index(headers, ARTIST_MEMBER_HEADERS)
        if company_index is None or group_index is None:  # pragma: no cover
            continue
        previous_group = ""
        for row_index, cells in _table_data_rows(table, header_index):
            group = _clean_name(cells[group_index])
            company = _clean_name(cells[company_index])
            if not group or group == previous_group:
                continue
            previous_group = group
            if _name_key(group) == _name_key(company):
                continue
            if member_index is not None and _is_person_name(
                group, cells[member_index]
            ):
                continue
            _append_candidate(
                output,
                name=group,
                subject_kind=SUBJECT_IP,
                description=f"{company} 소속" if company else "",
                location=_row_location(table, row_index),
                excerpt=_row_excerpt(cells, headers),
            )
    return tuple(output)


def parse_ip_tables(tables: Iterable[FilingTable]) -> tuple[NameCandidate, ...]:
    """브랜드·게임 타이틀·개발 파이프라인처럼 회사가 내세우는 이름을 읽는다."""

    output: list[NameCandidate] = []
    for table in tables:
        found = _table_header_row(
            table, lambda cells: _ip_name_index(table, cells) is not None
        )
        if found is None:
            continue
        header_index, headers = found
        name_index = _ip_name_index(table, headers)
        if name_index is None:  # pragma: no cover - 머리행 관문의 불변식
            continue
        description_index = _header_index(headers, DESCRIPTION_HEADERS)
        stage_index = _header_index(headers, IP_STAGE_HEADERS)
        for row_index, cells in _table_data_rows(table, header_index):
            descriptions = [
                cells[index].strip()
                for index in (description_index, stage_index)
                if index is not None and cells[index].strip()
            ]
            _append_candidate(
                output,
                name=cells[name_index],
                subject_kind=SUBJECT_IP,
                description=" · ".join(descriptions),
                location=_row_location(table, row_index),
                excerpt=_row_excerpt(cells, headers),
            )
    return tuple(output)


def parse_subsidiary_tables(
    tables: Iterable[FilingTable],
) -> tuple[NameCandidate, ...]:
    """``회사명 + 업종/주요 사업`` 두 칸을 모두 가진 표만 종속회사로 읽는다."""

    def accepts(cells: tuple[str, ...]) -> bool:
        return (
            _named_index(cells, COMPANY_NAME_HEADERS) is not None
            and _header_index(cells, BUSINESS_HEADERS) is not None
        )

    output: list[NameCandidate] = []
    for table in tables:
        found = _table_header_row(table, accepts)
        if found is None:
            continue
        header_index, headers = found
        company_index = _named_index(headers, COMPANY_NAME_HEADERS)
        if company_index is None:  # pragma: no cover - 머리행 관문의 불변식
            continue
        relation_index = _header_index(headers, RELATION_HEADERS)
        special = _is_special_relationship_location(table.title)
        business_indexes = _matching_header_indexes(headers, BUSINESS_HEADERS)
        for row_index, cells in _table_data_rows(table, header_index):
            if special and (
                relation_index is None
                or "종속" not in _header_key(cells[relation_index])
            ):
                continue
            descriptions = tuple(
                cells[index] for index in business_indexes if cells[index].strip()
            )
            _append_candidate(
                output,
                name=cells[company_index],
                subject_kind=SUBJECT_SUBSIDIARY,
                description=" · ".join(descriptions),
                location=_row_location(table, row_index),
                excerpt=_row_excerpt(cells, headers),
            )
    return tuple(output)


def parse_major_contract_tables(
    tables: Iterable[FilingTable],
) -> tuple[NameCandidate, ...]:
    """계약명 칸이 있는 표에서 계약 이름과 기간·진행률을 읽는다."""

    def accepts(cells: tuple[str, ...]) -> bool:
        if _named_index(cells, CONTRACT_NAME_HEADERS) is None:
            return False
        # 계약명 칸 하나뿐인 표는 세로로 세운 계약 요약이다. 그 표의 아래 행은
        # 「만기일」·「거래상대방」처럼 항목 이름이라 계약명이 아니다.
        return any(
            _header_index(cells, accepted) is not None
            for accepted in (
                CONTRACT_PERIOD_HEADERS,
                CONTRACT_PROGRESS_HEADERS,
                CONTRACT_FIELD_HEADERS,
                DESCRIPTION_HEADERS,
                COMPANY_NAME_HEADERS,
            )
        )

    output: list[NameCandidate] = []
    for table in tables:
        found = _table_header_row(table, accepts)
        if found is None:
            continue
        header_index, headers = found
        name_index = _named_index(headers, CONTRACT_NAME_HEADERS)
        if name_index is None:  # pragma: no cover - 머리행 관문의 불변식
            continue
        period_index = _header_index(headers, CONTRACT_PERIOD_HEADERS)
        progress_index = _header_index(headers, CONTRACT_PROGRESS_HEADERS)
        for row_index, cells in _table_data_rows(table, header_index):
            descriptions: list[str] = []
            if period_index is not None and cells[period_index].strip():
                descriptions.append(f"계약기간: {cells[period_index].strip()}")
            if progress_index is not None and cells[progress_index].strip():
                descriptions.append(f"진행률: {cells[progress_index].strip()}")
            _append_candidate(
                output,
                name=cells[name_index],
                subject_kind=SUBJECT_CONTRACT,
                description=" · ".join(descriptions),
                location=_row_location(table, row_index),
                excerpt=_row_excerpt(cells, headers),
            )
    return tuple(output)


_TableParser = Callable[[Iterable[FilingTable]], tuple[NameCandidate, ...]]
TABLE_PARSERS: tuple[_TableParser, ...] = (
    parse_product_service_tables,
    parse_artist_contract_tables,
    parse_ip_tables,
    parse_named_service_tables,
    parse_subsidiary_tables,
    parse_major_contract_tables,
)


def collect_name_candidates_from_tables(
    tables: Iterable[FilingTable], *, source_kind: str
) -> tuple[NameCandidate, ...]:
    """표 규칙의 후보를 순서대로 합치고 이름 기준으로 중복·상한을 적용한다.

    상한은 «규칙마다» 건다. 전체 합계로 끊으면 제품 표가 큰 회사(실측: 대형
    제조사·은행은 제품 목록만으로 상한에 닿는다)에서 뒤 규칙의 대표 IP·종속회사·
    계약 후보가 통째로 사라진다. 최종 자리 배분은 조각 예산이 따로 한다.
    """

    materialized = tuple(
        table for table in tables if isinstance(table, FilingTable) and table.rows
    )
    if not materialized:
        return ()
    output: list[NameCandidate] = []
    seen: set[str] = set()
    for parser in TABLE_PARSERS:
        taken = 0
        for candidate in parser(materialized):
            if taken >= MAX_NAME_CANDIDATES:
                break
            key = _name_key(candidate.name)
            if not key or key in seen:
                continue
            seen.add(key)
            output.append(replace(candidate, source_kind=str(source_kind)))
            taken += 1
    return tuple(output)


__all__ = [
    "collect_name_candidates",
    "collect_name_candidates_from_tables",
    "parse_artist_contract_tables",
    "parse_ip_tables",
    "parse_major_contract_tables",
    "parse_major_contracts",
    "parse_named_service_table",
    "parse_named_service_tables",
    "parse_product_service_table",
    "parse_product_service_tables",
    "parse_subsidiary_table",
    "parse_subsidiary_tables",
]
