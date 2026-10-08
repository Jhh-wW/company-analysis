"""회사 자신의 결산 당기 영업표에서 정확한 종류 이름을 선택한다."""

from __future__ import annotations

from collections.abc import Iterator

from src.shared.report_evidence import business_activity_table_constants as c


def _header(value: str) -> str:
    return "".join(c.ACTIVITY_HEADER_FOOTNOTE_RE.sub("", value).split())


def _table_item(table: str) -> str:
    rows = [tuple(cell.strip() for cell in row.split(" | ")) for row in table.split(" ; ")]
    if len(rows) < 2:
        return ""
    headers = tuple(_header(cell) for cell in rows[0])
    if set(headers) & c.ACTIVITY_FOREIGN_HEADERS:
        return ""
    names = [i for i, value in enumerate(headers) if value in c.ACTIVITY_ITEM_HEADERS]
    amounts = [i for i, value in enumerate(headers) if value in c.ACTIVITY_REVENUE_HEADERS]
    if len(names) != 1 or len(amounts) != 1:
        return ""
    for row in rows[1:]:
        if len(row) != len(headers):
            return ""
        if any(c.ACTIVITY_INACTIVE_RE.search(row[i]) for i, header in enumerate(headers)
               if header in c.ACTIVITY_STATUS_HEADERS):
            continue
        if any(c.ACTIVITY_ROW_PERIOD_EXCLUDED_RE.search(row[i]) for i, header in enumerate(headers)
               if header in c.ACTIVITY_PERIOD_HEADERS):
            continue
        item, amount = row[names[0]], row[amounts[0]]
        if _header(item) in c.ACTIVITY_GENERIC_ITEMS:
            continue
        if not c.ACTIVITY_POSITIVE_AMOUNT_RE.fullmatch(amount) or not any(char in "123456789" for char in amount):
            continue
        if any(c.ACTIVITY_EXCLUDED_RE.search(cell) for cell in row):
            continue
        site = c.ACTIVITY_SITE_SUFFIX_RE.fullmatch(item)
        if site is not None:
            item = site["item"].strip()
        # 일반 '구분' 열에서는 회계계정/고객명을 사업종류로 읽지 않는다.
        if headers[names[0]] == "구분" and not (headers[amounts[0]] == "누적공사수익" and item.endswith("공사")):
            continue
        if (len(item) <= c.MAX_ACTIVITY_TABLE_ITEM_CHARS and c.ACTIVITY_ITEM_RE.fullmatch(item)
                and not c.ACTIVITY_EXCLUDED_RE.search(item)):
            return item
    return ""


def _table_items(table: str) -> tuple[str, ...]:
    """같은 표의 유효한 모든 종류를 읽되 행·열이 깨진 표는 추정하지 않는다."""
    rows = table.split(" ; ")
    width = len(rows[0].split(" | "))
    if len(rows) < 2 or any(len(row.split(" | ")) != width for row in rows[1:]):
        return ()
    return tuple(dict.fromkeys(
        item for row in rows[1:]
        if (item := _table_item(" ; ".join((rows[0], row))))
    ))


def _other_owner(text: str, company_name: str) -> bool:
    key = c.ACTIVITY_COMPANY_KEY_RE.sub("", company_name).casefold()
    def same_company(value: str) -> bool:
        literal = c.ACTIVITY_COMPANY_KEY_RE.sub("", value).casefold()
        if literal == key:
            return True
        # 표제 안내 앞말은 회사명의 일부가 아니다. 명칭 자체의 부분일치는 허용하지 않는다.
        value = c.ACTIVITY_HEADING_LEAD_RE.sub("", value)
        return c.ACTIVITY_COMPANY_KEY_RE.sub("", value).casefold() == key
    return (
        any(not same_company(match["company"])
            for match in c.ACTIVITY_COMPANY_HEADING_RE.finditer(text))
        or any(c.ACTIVITY_COMPANY_KEY_RE.sub("", match["actor"]).casefold() not in
               c.ACTIVITY_SELF_ACTORS | {key} for match in c.ACTIVITY_DIRECT_ACTOR_RE.finditer(text))
    )


def _activity_tables(
    text: str, company_name: str, *, multiple: bool = False,
) -> Iterator[tuple[int, int, str]]:
    for owner in c.ACTIVITY_OWNER_RE.finditer(text):
        start = text.rfind("\n\n", 0, owner.start()) + 2
        if start == 1:
            start = 0
        limit = min(len(text), start + c.MAX_ACTIVITY_TABLE_WINDOW_CHARS)
        boundary = c.ACTIVITY_MAJOR_HEADING_RE.search(text, owner.end(), limit)
        if boundary is not None:
            limit = boundary.start()
        owner_end = text.find("\n\n", owner.end(), limit)
        owner_end = limit if owner_end < 0 else owner_end
        if (c.ACTIVITY_EXCLUDED_RE.search(text[start:owner_end])
                or c.ACTIVITY_NEGATED_OWNER_RE.search(text[owner.end():owner_end])
                or _other_owner(text[start:owner_end], company_name)):
            continue
        previous = ""
        at = start
        for paragraph in text[start:limit].split("\n\n"):
            if " | " not in paragraph:
                if _other_owner(paragraph, company_name) or c.ACTIVITY_NEGATED_OWNER_RE.search(paragraph):
                    break
                if paragraph.strip() and not paragraph.strip().startswith("(단위:"):
                    previous = paragraph
                at += len(paragraph) + 2
                continue
            # 직전 표제의 당기 표시만 사용한다. 앞 표의 당기/다른 표의 기간을 빌리지 않는다.
            paragraph_end = at + len(paragraph)
            if paragraph_end == limit and limit < len(text) and not text.startswith("\n\n", limit):
                break  # 문자 상한에서 잘린 표를 완결 행으로 확정하지 않는다.
            if (c.ACTIVITY_CURRENT_RE.search(previous) and not c.ACTIVITY_PRIOR_RE.search(previous)
                    and not c.ACTIVITY_EXCLUDED_RE.search(previous)):
                items = _table_items(paragraph) if multiple else (_table_item(paragraph),)
                if any(items):
                    end = paragraph_end
                    # 별도 법인 표제는 앞 당사 설명의 소유를 이어받지 않는다.
                    # 완전한 표 안의 발주처/고객 명칭과 구별한다.
                    if end <= limit:
                        for item in items:
                            if item:
                                yield start, end, item
                    break
            previous = ""
            at += len(paragraph) + 2


def activity_table_ranges(text: str, company_name: str = "") -> Iterator[tuple[int, int]]:
    """주어·당기·정확 표 행을 포함하는 원문의 연속 범위만 전달한다."""
    for start, end, _item in _activity_tables(text, company_name):
        yield start, end


def activity_table_item(text: str, company_name: str = "") -> str:
    """현재 사업 사건/문제 판정 없이 한 종류를 검색용 항목으로 선택한다."""
    return next((item for _start, _end, item in _activity_tables(text, company_name)), "")


def activity_table_items(text: str, company_name: str = "") -> tuple[str, ...]:
    """한 공식 당기 표에 함께 있는 종류도 원문 범위를 유지하며 조사에 전달한다."""
    return tuple(dict.fromkeys(
        item for _start, _end, item in _activity_tables(text, company_name, multiple=True)
    ))
