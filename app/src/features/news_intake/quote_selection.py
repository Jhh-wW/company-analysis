"""읽은 동일 본문의 연속 범위를 선택 ID로 결속한다. 의미 판정은 바꾸지 않는다."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re

from src.features.news_intake import quote_selection_constants as c
from src.features.news_intake.identity_names import company_query_names, mentions_target
from src.features.news_intake.select import normalize_company_name
from src.features.news_intake.models import NewsCandidate, NewsCompanyContext


def quote_response_sha256(raw: object) -> str | None:
    """파싱 응답의 정규 JSON 지문이며 HTTP 원응답 바이트 지문과 구분한다."""
    from src.features.news_intake.grounded import parse_grounded_payload
    items = parse_grounded_payload(raw)
    if items is None:
        return None
    try:
        encoded = json.dumps(items, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    except (TypeError, ValueError):
        return None


def quote_candidates(candidate: NewsCandidate, body: str,
                     company: NewsCompanyContext | None = None) -> list[dict]:
    """문장·문단과 인접 범위만 제시하며 본문 문자열을 복제하지 않는다."""
    body_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
    units = []
    # 소수점·상호의 점은 문장 경계로 보지 않는다.
    for match in c.QUOTE_SENTENCE_RE.finditer(body):
        start, end = match.span()
        while start < end and body[start].isspace():
            start += 1
        while end > start and body[end - 1].isspace():
            end -= 1
        if start < end:
            units.append((start, end))
    # 앞쪽 회사명에만 후보를 소비하지 않도록 단문 몫을 본문 전체에 고르게 둔다.
    valid_units = [(start, end) for start, end in units if end - start <= c.QUOTE_MAX_CHARS]
    if len(valid_units) > c.QUOTE_UNIT_CANDIDATE_BUDGET:
        valid_units = [valid_units[i * (len(valid_units) - 1) // (c.QUOTE_UNIT_CANDIDATE_BUDGET - 1)]
                       for i in range(c.QUOTE_UNIT_CANDIDATE_BUDGET)]
    ranges: list[tuple[int, int]] = list(valid_units)
    # 대상명으로 시작하는 연속 범위는 선택지일 뿐 해당 사건의 주어라는 판정이 아니다.
    target_starts = [i for i, (start, end) in enumerate(units)
                     if company is not None and mentions_target(body[start:end], company)]
    for i in target_starts:
        for width in range(2, c.QUOTE_MAX_ADJACENT_SENTENCES + 1):
            if i + width <= len(units):
                ranges.append((units[i][0], units[i + width - 1][1]))
    ranges.extend(units)
    for match in re.finditer(r"[^\n]+", body):
        start, end = match.span()
        while start < end and body[start].isspace():
            start += 1
        while end > start and body[end - 1].isspace():
            end -= 1
        ranges.append((start, end))
    for width in range(2, c.QUOTE_MAX_ADJACENT_SENTENCES + 1):
        ranges.extend((units[i][0], units[i + width - 1][1])
                      for i in range(len(units) - width + 1))
    output, seen = [], set()
    for start, end in ranges:
        if (start, end) in seen or not 0 < end - start <= c.QUOTE_MAX_CHARS:
            continue
        seen.add((start, end))
        digest = hashlib.sha256(f"{candidate.id}\n{body_hash}\n{start}:{end}".encode()).hexdigest()
        text = body[start:end]
        output.append({"id": c.QUOTE_ID_PREFIX + digest[:c.QUOTE_ID_HASH_CHARS],
                       "start": start, "end": end,
                       "begin_text": text[:c.QUOTE_BOUNDARY_PREVIEW_CHARS],
                       "end_text": text[-c.QUOTE_BOUNDARY_PREVIEW_CHARS:]})
        if len(output) >= c.QUOTE_MAX_CANDIDATES_PER_ARTICLE:
            break
    return output


def quote_schema(schema: dict, articles: list[tuple[NewsCandidate, str]],
                 company: NewsCompanyContext | None = None) -> dict:
    """긴 직접 인용 필드만 ID로 바꾸고 회사·사건·주어 판단 필드는 보존한다."""
    result = deepcopy(schema)
    ids = [row["id"] for candidate, body in articles
           for row in quote_candidates(candidate, body, company)]
    item = result["properties"]["items"]["items"]
    excerpt = item["properties"]["excerpts"]["items"]
    excerpt["properties"]["subject_is_target"] = {"type": "boolean"}
    excerpt["required"].append("subject_is_target")
    for fields, names in ((item, ("entity_evidence",)),
                          (excerpt, ("text", "time_evidence", "subject_evidence"))):
        for name in names:
            fields["properties"].pop(name)
            selected_name = name + c.QUOTE_FIELD_SUFFIX
            fields["properties"][selected_name] = {"type": "string", "enum": ids + ([""] if name != "text" else [])}
            fields["required"][fields["required"].index(name)] = selected_name
    return result


def _selection_subject_supported(text: str, company: NewsCompanyContext) -> bool:
    """회사명 존재를 행동 주어로 승격시키는 명시 모순만 신규 선택에서 제외한다."""
    if not mentions_target(text, company):
        return True  # 제품·인물의 명시 관계는 기존 주어 결속 검사에서 판정한다.
    names = company_query_names(company)
    for name in names:
        if re.search(re.escape(name) + c.QUOTE_SUBSIDIARY_RE.pattern, text):
            return False
    if c.QUOTE_PAST_EMPLOYMENT_RE.search(text) and c.QUOTE_INDEPENDENT_TRANSITION_RE.search(text):
        return False
    if c.QUOTE_PUBLISHER_RE.search(text):
        content = c.QUOTE_PUBLISHER_RE.sub("", text)
        sentences = list(c.QUOTE_SENTENCE_RE.finditer(content))
        if not any(mentions_target(sentence.group(), company)
                   and c.QUOTE_EXPLICIT_BUSINESS_ACTION_RE.search(sentence.group())
                   for sentence in sentences):
            return False
    for sentence in c.QUOTE_SENTENCE_RE.finditer(text):
        if any(re.search(c.QUOTE_SUBJECT_PREFIX + re.escape(name) + c.QUOTE_KNOWN_NAME_BOUNDARY,
                         sentence.group(), re.I) for name in names):
            continue
        subject = c.QUOTE_CLAUSE_SUBJECT_RE.search(sentence.group()) or c.QUOTE_REVERSED_SUBJECT_RE.search(sentence.group())
        if subject and c.QUOTE_EXPLICIT_BUSINESS_ACTION_RE.search(sentence.group()):
            value = normalize_company_name(subject.group("subject"))
            if value not in c.QUOTE_COMPANY_PRONOUNS and not any(
                value == normalize_company_name(name) for name in names
            ):
                return False
    return True


def restore_quote_response(raw: object, *, articles: list[tuple[NewsCandidate, str]],
                           company: NewsCompanyContext, selection_enabled: bool = True) -> object:
    """구형 응답은 그대로 읽고 요청 후보 밖 ID·혼합 필드는 검증 실패로 닫는다."""
    from src.features.news_intake.grounded import parse_grounded_payload
    items = parse_grounded_payload(raw)
    if items is None:
        return raw
    def has_selection(value: object) -> bool:
        if type(value) is dict:
            return any(type(name) is str and name.endswith(c.QUOTE_FIELD_SUFFIX)
                       or has_selection(item) for name, item in value.items())
        return type(value) is list and any(has_selection(item) for item in value)
    if not has_selection(items):
        return raw
    if not selection_enabled:
        return {"items": [{"invalid_quote_selection": True}]}
    result = deepcopy(items)
    tables = {candidate.id: (body, {row["id"]: row for row in quote_candidates(candidate, body, company)})
              for candidate, body in articles}

    def restore(fields: dict, allowed: tuple[str, ...], body: str, table: dict) -> None:
        selected = [name for name in fields if name.endswith(c.QUOTE_FIELD_SUFFIX)]
        for selected_name in selected:
            name = selected_name[:-len(c.QUOTE_FIELD_SUFFIX)]
            if name not in allowed or name in fields:
                raise ValueError("원문 선택 필드가 구형 필드 또는 허용 범위와 충돌합니다")
            value = fields.pop(selected_name)
            if type(value) is not str:
                raise ValueError("원문 선택 ID 형식이 잘못됐습니다")
            if value == "" and name != "text":
                fields[name] = ""
            else:
                span = table[value]
                fields[name] = body[span["start"]:span["end"]]

    def fail_selection(fields: dict, source_names: tuple[str, ...]) -> None:
        for name in list(fields):
            if type(name) is str and name.endswith(c.QUOTE_FIELD_SUFFIX):
                fields.pop(name)
        fields.pop("subject_is_target", None)
        for name in source_names:
            fields.setdefault(name, "")
        fields["invalid_quote_selection"] = True

    for item in result:
        if type(item) is not dict:
            continue
        if type(item.get("id")) is not str or item["id"] not in tables or not has_selection(item):
            continue
        body, table = tables[item["id"]]
        try:
            if "entity_evidence_quote_id" not in item or "entity_evidence" in item:
                raise ValueError("원문 선택 기사는 긴 근거 필드 전체를 새 계약으로 반환해야 합니다")
            restore(item, ("entity_evidence",), body, table)
        except (KeyError, TypeError, ValueError):
            # 신원 선택 오류는 직접 뉴스 기사에서 복구하지 않으며 산업 검수는 독립한다.
            fail_selection(item, ("entity_evidence",))
            item["entity_evidence"] = ""
        excerpts = item.get("excerpts")
        for excerpt in excerpts if type(excerpts) is list else []:
            if type(excerpt) is dict:
                try:
                    source_fields = c.QUOTE_EXCERPT_SOURCE_FIELDS
                    if (any(name in excerpt or name + c.QUOTE_FIELD_SUFFIX not in excerpt for name in source_fields)
                            or type(excerpt.get("subject_is_target")) is not bool):
                        raise ValueError("인용별 대상 주어 판정 또는 원문 선택 계약이 누락됐습니다")
                    subject_is_target = excerpt.pop("subject_is_target")
                    restore(excerpt, source_fields, body, table)
                    if not subject_is_target or not _selection_subject_supported(excerpt["text"], company):
                        excerpt["invalid_quote_subject"] = True
                except (KeyError, TypeError, ValueError):
                    fail_selection(excerpt, c.QUOTE_EXCERPT_SOURCE_FIELDS)
        problems = item.get("industry_problems")
        for problem in problems if type(problems) is list else []:
            if type(problem) is dict:
                try:
                    if "text_quote_id" not in problem or "text" in problem:
                        raise ValueError("산업 인용의 원문 선택 계약이 누락됐습니다")
                    restore(problem, ("text",), body, table)
                except (KeyError, TypeError, ValueError):
                    fail_selection(problem, ("text",))
    return {"items": result}
