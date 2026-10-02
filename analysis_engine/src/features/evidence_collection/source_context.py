"""직접 회사 사실로 승격할 수 없는 명시 주어·상태를 원문과 별도로 보존한다."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from features.evidence_collection import source_context_constants as c
from features.evidence_collection.table_text import _TableParser, _owned_grid


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SourceContextBudgetExceeded(ValueError):
    """행 주어 문맥 전체를 확인하지 못했으므로 문서 직접 분류를 멈춘다."""


def _payload(*, text: str, start: int, actor: str, document_actor: str,
             document_actor_start: int, origin: str, status: str = "", item: str = "") -> str:
    return json.dumps({
        "version": c.CONTEXT_VERSION, "text": text,
        "location": f"{start}-{start + len(text)}", "text_sha256": _hash(text),
        "actor": actor, "document_actor": document_actor,
        "document_actor_location": f"{document_actor_start}-{document_actor_start + len(document_actor)}",
        "document_actor_sha256": _hash(document_actor), "origin": origin, "status": status,
        **({"item": item} if item else {}),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class _ContextTableParser(_TableParser):
    def __init__(self, markup: str) -> None:
        super().__init__(markup, max_window_chars=c.MAX_CONTEXT_TABLE_CHARS)
        self.grids: list[list[list[str]]] = []
        self.context_budget_exceeded = False
        self.context_chars = 0

    def _finish_table(self) -> None:
        table = self.table
        has_actor_header = bool(table and any(
            re.sub(r"\s+", "", cell.text) in c.TABLE_ACTOR_HEADERS
            for row in table.rows for cell in row
        ))
        if table is not None and has_actor_header and (
            table.invalid or table.row is not None or table.cell_parts is not None
            or table.caption_parts is not None
        ):
            self.context_budget_exceeded = True
        if self.table_count > c.MAX_CONTEXT_TABLES:
            self.context_budget_exceeded = True
        if (table is not None and not table.invalid and table.row is None
                and table.cell_parts is None and table.caption_parts is None
                and self.table_count <= c.MAX_CONTEXT_TABLES):
            grid = _owned_grid(table.rows, max_chars=c.MAX_CONTEXT_TABLE_CHARS)
            if grid is None and has_actor_header:
                self.context_budget_exceeded = True
            if grid and any(re.sub(r"\s+", "", cell) in c.TABLE_ACTOR_HEADERS for row in grid for cell in row):
                size = sum(len(cell) for row in grid for cell in row)
                if len(self.grids) >= c.MAX_CONTEXT_GRIDS or self.context_chars + size > c.MAX_CONTEXT_TOTAL_CHARS:
                    self.context_budget_exceeded = True
                else:
                    self.grids.append(grid)
                    self.context_chars += size
        super()._finish_table()


def table_source_contexts(markup: str, plain_text: str, *, document_actor: str) -> tuple[str, ...]:
    """명시적인 회사 열과 같은 표 행만 원문 위치에 결속한다."""
    actor_start = plain_text.find(document_actor) if document_actor else -1
    if actor_start < 0:
        return ()
    parser = _ContextTableParser(markup)
    try:
        parser.feed(markup)
        parser.close()
    except (ValueError, RecursionError):
        raise SourceContextBudgetExceeded("문서의 회사 주어 표 구조를 끝까지 확인하지 못했습니다") from None
    if parser.table is not None and any(
        re.sub(r"\s+", "", cell.text) in c.TABLE_ACTOR_HEADERS
        for row in parser.table.rows for cell in row
    ):
        parser.context_budget_exceeded = True
    if parser.context_budget_exceeded:
        raise SourceContextBudgetExceeded("문서의 회사 주어 문맥 확인 상한에 도달했습니다")
    result: list[str] = []
    for grid in parser.grids:
        header_index = next((i for i, row in enumerate(grid)
                             if any(re.sub(r"\s+", "", cell) in c.TABLE_ACTOR_HEADERS for cell in row)), None)
        if header_index is None:
            continue
        headers = [re.sub(r"\s+", "", cell) for cell in grid[header_index]]
        actor_indices = [i for i, header in enumerate(headers) if header in c.TABLE_ACTOR_HEADERS]
        if len(actor_indices) != 1:
            continue
        actor_index = actor_indices[0]
        item_indices = [i for i, header in enumerate(headers) if header in c.TABLE_ITEM_HEADERS]
        for row in grid[header_index + 1:]:
            actor = row[actor_index]
            if (not actor or len(actor) > c.MAX_ACTOR_CHARS
                    or re.sub(r"\s+", "", actor) in c.SELF_ACTOR_LABELS):
                continue
            # 평문을 다시 만들지 않는다. 현행 파서에 실제로 있는 행만 문맥으로 쓴다.
            variants = ("\n\n".join(row), " | ".join(row))
            spans = [(plain_text.find(text), text) for text in variants if text]
            spans = [(start, text) for start, text in spans
                     if start >= 0 and plain_text.find(text, start + 1) < 0]
            if len(spans) != 1:
                continue
            start, text = spans[0]
            if len(text) > c.MAX_CONTEXT_TEXT_CHARS:
                raise SourceContextBudgetExceeded("회사 주어 행의 원문 문맥이 확인 상한을 넘었습니다")
            status = next((cell for i, cell in enumerate(row)
                           if i != actor_index and c.PENDING_STATUS_RE.search(cell)), "")
            result.append(_payload(text=text, start=start, actor=actor,
                                   document_actor=document_actor, document_actor_start=actor_start,
                                   origin="table_row", status=status,
                                   item=row[item_indices[0]] if len(item_indices) == 1 else ""))
            if len(result) > c.MAX_CONTEXT_ROWS:
                raise SourceContextBudgetExceeded("문서의 회사 주어 행 확인 상한에 도달했습니다")
    return tuple(result)


@dataclass(frozen=True)
class HeadingSourceScope:
    start: int
    end: int
    context_json: str


def heading_source_scopes(plain_text: str, *, document_actor: str) -> tuple[HeadingSourceScope, ...]:
    """사업 부문 제목 다음 영업개황의 명시 법인만 하위 당사 주어로 유지한다."""
    actor_start = plain_text.find(document_actor) if document_actor else -1
    if actor_start < 0:
        return ()
    boundaries = sorted(set(m.start() for pattern in (c.BUSINESS_SECTION_RE, c.MAIN_SECTION_RE)
                            for m in pattern.finditer(plain_text)))
    result: list[HeadingSourceScope] = []
    for index, start in enumerate(boundaries):
        end = boundaries[index + 1] if index + 1 < len(boundaries) else len(plain_text)
        block = plain_text[start:end]
        # 주어가 회사 소개가 아닌 거래처·과거 인용·관계법인 목록이면 채택하지 않는다.
        match = c.COMPANY_HEADING_START_RE.search(block)
        if match is None:
            continue
        company = c.COMPANY_NAME_RE.match(block, match.end())
        if company is None:
            continue
        actor = company.group().strip()
        context_end = min(len(block), max(company.end(), block.find("\n\n", company.end())))
        if context_end <= company.end():
            context_end = company.end()
        text = block[:context_end]
        if len(text) > c.MAX_CONTEXT_TEXT_CHARS:
            raise SourceContextBudgetExceeded("회사 주어 표제의 원문 문맥이 확인 상한을 넘었습니다")
        result.append(HeadingSourceScope(start, end, _payload(
            text=text, start=start, actor=actor, document_actor=document_actor,
            document_actor_start=actor_start, origin="company_heading",
        )))
    return tuple(result)


def context_for_candidate(*, text: str, start: int, end: int,
                          table_contexts: tuple[HeadingSourceScope, ...], scopes: tuple[HeadingSourceScope, ...]) -> str:
    for scope in table_contexts:
        if scope.start <= start and end <= scope.end:
            return scope.context_json
    for scope in scopes:
        if scope.start <= start < scope.end:
            return scope.context_json
    return ""


def prepare_table_contexts(raw_contexts: tuple[str, ...], *, document_text: str) -> tuple[HeadingSourceScope, ...]:
    result: list[HeadingSourceScope] = []
    for raw in raw_contexts:
        item = validate_source_context(raw, document_text=document_text)
        start, end = map(int, item["location"].split("-"))
        result.append(HeadingSourceScope(start, end, raw))
    return tuple(result)


def different_document_actor(context_json: str) -> bool:
    if not context_json:
        return False
    item = json.loads(context_json)
    def key(value: str) -> str:
        return re.sub(r"\s+|주식회사|\(주\)|㈜", "", value).casefold()
    actor = item["actor"]
    if item["origin"] == "company_heading" and actor.endswith("의") and key(actor[:-1]) == key(item["document_actor"]):
        return False
    return key(actor) != key(item["document_actor"])


def validate_source_context(raw: str, *, document_text: str | None = None) -> dict[str, str]:
    """닫힌 원문 문맥과, 수집 경계에서는 실제 문서 범위까지 대조한다."""
    if not raw:
        return {}
    item = json.loads(raw)
    if (type(item) is not dict or set(item) not in (c.CONTEXT_KEYS, c.CONTEXT_KEYS | {"item"})
            or any(type(value) is not str for value in item.values())
            or item["version"] != c.CONTEXT_VERSION
            or item["origin"] not in ("company_heading", "table_row")
            or not item["actor"] or item["actor"] not in item["text"]
            or not item["document_actor"] or len(item["text"]) > c.MAX_CONTEXT_TEXT_CHARS
            or item["status"] not in item["text"]):
        raise ValueError("회사 주어 문맥 형식이나 명시 원문이 다릅니다")
    for text_key, location_key, hash_key in (("text", "location", "text_sha256"), ("document_actor", "document_actor_location", "document_actor_sha256")):
        match = re.fullmatch(r"([0-9]{1,10})-([0-9]{1,10})", item[location_key])
        if match is None or _hash(item[text_key]) != item[hash_key]:
            raise ValueError("회사 주어 문맥 위치·해시가 다릅니다")
        start, end = map(int, match.groups())
        if end <= start or end - start != len(item[text_key]):
            raise ValueError("회사 주어 문맥 범위 길이가 다릅니다")
        if document_text is not None and document_text[start:end] != item[text_key]:
            raise ValueError("회사 주어 문맥이 실제 문서 원문과 다릅니다")
    if json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) != raw:
        raise ValueError("회사 주어 문맥의 직렬화가 정본과 다릅니다")
    if item["origin"] == "table_row":
        cells = re.split(r"\n\n| \| ", item["text"])
        if item["actor"] not in cells or (item["status"] and item["status"] not in cells) or (item.get("item") and item["item"] not in cells):
            raise ValueError("회사 주어·상태는 표의 완전한 셀 원문이어야 합니다")
    else:
        heading = c.COMPANY_HEADING_START_RE.search(item["text"])
        actor_match = c.COMPANY_NAME_RE.match(item["text"], heading.end()) if heading else None
        if actor_match is None or actor_match.group().strip() != item["actor"]:
            raise ValueError("회사 주어는 법인 표제의 완전한 명칭이어야 합니다")
    return item
