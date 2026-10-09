"""공식 조각의 실제 행위법인·표 상태를 담은 선택적 제약 원문 계약."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping

from src.shared.company_identity import exact_company_names_equivalent
from src.shared.report_evidence import source_context_constants as c
from src.shared.report_evidence.section_context import parse_section_context
from src.shared.report_evidence.source_context_constants import (
    SOURCE_CONTEXT_COMPLETED_RE, SOURCE_CONTEXT_STAGE_RE,
    SOURCE_CONTEXT_CLAUSE_RE, SOURCE_CONTEXT_PENDING_STAGE_RE,
)

CONTEXT_VERSION = "source-context-v1"
MAX_CONTEXT_TEXT_CHARS = 12_000
CONTEXT_KEYS = frozenset({"version", "text", "location", "text_sha256", "actor", "document_actor", "document_actor_location", "document_actor_sha256", "origin", "status"})
_COMPANY_NAME_RE = re.compile(r"(?:\(주\)|㈜|주식회사)\s*[가-힣A-Za-z0-9&·._-]+|[가-힣A-Za-z0-9&·._-]+\s*(?:주식회사|\(주\)|㈜)")
_COMPANY_HEADING_START_RE = re.compile(r"영업개황\s*(?:[12][0-9]{3}년\s*(?:[1-4]\s*분기|상반기|하반기|연간)\s*(?:누적\s*)?)?")


def parse_source_context(
    raw: str, *, document_id: str = '', document_sha256: str = '',
    fragment_location: str = '', fragment_sha256: str = '',
    section_context_json: str | None = None, binding_scope: str = '',
) -> dict[str, str]:
    if type(raw) is not str:
        raise ValueError("회사 주어 문맥은 문자열이어야 합니다")
    if not raw:
        return {}
    item = json.loads(raw)
    self_section = item.get("origin") == c.SELF_SECTION_ORIGIN if type(item) is dict else False
    allowed_keys = (CONTEXT_KEYS | c.SELF_SECTION_EXTRA_KEYS,) if self_section else (CONTEXT_KEYS, CONTEXT_KEYS | {"item"})
    if (type(item) is not dict or set(item) not in allowed_keys
            or any(type(value) is not str for value in item.values())
            or item["version"] != CONTEXT_VERSION
            or item["origin"] not in ("company_heading", "table_row", c.SELF_SECTION_ORIGIN)
            or not item["actor"] or (not self_section and item["actor"] not in item["text"])
            or not item["document_actor"] or len(item["text"]) > MAX_CONTEXT_TEXT_CHARS
            or item["status"] not in item["text"]):
        raise ValueError("회사 주어 문맥 형식이나 명시 원문이 다릅니다")
    for text_key, location_key, hash_key in (("text", "location", "text_sha256"), ("document_actor", "document_actor_location", "document_actor_sha256")):
        match = re.fullmatch(r"([0-9]{1,10})-([0-9]{1,10})", item[location_key])
        digest = hashlib.sha256(item[text_key].encode("utf-8")).hexdigest()
        if match is None or digest != item[hash_key]:
            raise ValueError("회사 주어 문맥 위치·해시가 다릅니다")
        start, end = map(int, match.groups())
        if end <= start or end - start != len(item[text_key]):
            raise ValueError("회사 주어 문맥 범위 길이가 다릅니다")
    if json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) != raw:
        raise ValueError("회사 주어 문맥의 직렬화가 정본과 다릅니다")
    if self_section:
        required = {
            'document': (document_id, document_sha256),
            'fragment': (document_id, document_sha256, fragment_location, fragment_sha256),
            'fragment_identity': (document_id, fragment_location, fragment_sha256),
        }
        if binding_scope and (binding_scope not in required
                or not all(required[binding_scope]) or section_context_json is None):
            raise ValueError("자기 사업부문 소속 문맥에 실제 사용 대상 결속이 필요합니다")
        section = parse_section_context(
            item["section_context_json"], document_id=document_id,
            document_sha256=document_sha256, fragment_location=fragment_location,
            fragment_sha256=fragment_sha256,
        )
        if section_context_json is not None and item['section_context_json'] != section_context_json:
            raise ValueError("자기 사업부문 소속과 조각의 사업 범위 문맥이 다릅니다")
        parts = {re.sub(r"\s+", "", part) for part in c.SELF_DECLARATION_PART_RE.findall(item["text"])}
        if (item["actor"] != item["document_actor"] or item["status"]
                or c.SELF_DECLARATION_RE.fullmatch(item["text"]) is None
                or c.SELF_DECLARATION_EXCLUDED_RE.search(item["text"])
                or item["declaration_part"] not in parts
                or re.sub(r"\s+", "", section["text"][1:-1]) != item["declaration_part"]
                or int(item["location"].split("-")[1]) >= int(section["location"].split("-")[0])):
            raise ValueError("자기 사업부문 선언과 제품 원문 범위가 결속되지 않았습니다")
    elif item["origin"] == "table_row":
        cells = re.split(r"\n\n| \| ", item["text"])
        if item["actor"] not in cells or (item["status"] and item["status"] not in cells) or (item.get("item") and item["item"] not in cells):
            raise ValueError("회사 주어·상태는 표의 완전한 셀 원문이어야 합니다")
    else:
        heading = _COMPANY_HEADING_START_RE.search(item["text"])
        actor_match = _COMPANY_NAME_RE.match(item["text"], heading.end()) if heading else None
        if actor_match is None or actor_match.group().strip() != item["actor"]:
            raise ValueError("회사 주어는 법인 표제의 완전한 명칭이어야 합니다")
    return item


def source_context_fingerprint(raw: str) -> str:
    parse_source_context(raw)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest() if raw else ""


def source_context_pending_state_problem(candidate_text: str, context: Mapping[str, str]) -> bool:
    """확인된 같은 품목의 예정 단계를 현재·완료로 바꾼 경우만 거절한다."""
    product = context.get("item", "")
    pending = SOURCE_CONTEXT_PENDING_STAGE_RE.search(context["status"])
    if not product or pending is None:
        return False
    product_active = False
    for clause in SOURCE_CONTEXT_CLAUSE_RE.split(candidate_text):
        unit = clause.strip()
        if not unit:
            continue
        # 앞 품목의 생략된 단계 서술만 잇는다. 다른 품목의 완료는 섞지 않는다.
        product_active = product in unit or (
            product_active and bool(SOURCE_CONTEXT_STAGE_RE.match(unit))
        )
        if not product_active:
            continue
        for completed in SOURCE_CONTEXT_COMPLETED_RE.finditer(unit):
            if completed.group().startswith(pending.group(1)):
                return True
    return False


def source_context_company_subject_problem(text: str, raw: str, *, company_name: str) -> str:
    """비교 차선에서 문서 소유자와 다른 원문 당사 주어를 구별한다."""
    context = parse_source_context(raw)
    if not context:
        return ""
    if context["status"] and source_context_pending_state_problem(text, context):
        return "source_status_unbound"
    actor = context["actor"]
    if context["origin"] == "company_heading" and actor.endswith("의"):
        actor = actor[:-1]
    if (exact_company_names_equivalent(context["document_actor"], company_name)
            and exact_company_names_equivalent(actor, company_name)):
        return ""
    # 다른 법인 문단 안에서도 대상 회사의 정확한 명시 주어는 기존 비교 검수로 넘긴다.
    literal_name = r"\s*".join(re.escape(part) for part in company_name.split())
    if (exact_company_names_equivalent(context["document_actor"], company_name)
            and re.match(r"\s*" + literal_name + r"(?:는|은|가|이)\s", text)):
        return ""
    return "source_actor_unbound"
