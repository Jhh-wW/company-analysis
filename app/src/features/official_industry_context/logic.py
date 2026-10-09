"""선택된 공식 원문을 단회 검수하고 산업 해석의 원문 결속을 확인한다."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable
from datetime import date

from src.features.official_industry_context import constants as c
from src.shared.business_challenge_context import (
    BusinessActivityAnchor, IndustryProblemEvidence, INDUSTRY_GEOGRAPHIES,
    OFFICIAL_INDUSTRY_GEOGRAPHIES, OFFICIAL_UNSPECIFIED_GEOGRAPHY,
)
from src.shared.official_ir import IR_METADATA_VERIFICATION_VALUES, official_ir_time_is_usable, safe_https_attachment_url
from src.shared.report_evidence.constants import FORMAL_DOCUMENT_SOURCE_KINDS, OFFICIAL_WEB_SOURCE_KINDS, SOURCE_KIND_OFFICIAL_IR_PDF
from src.shared.report_evidence.models import EvidenceFragment, CollectedEvidenceDocument
from src.shared.report_evidence.industry_candidates import OfficialIndustryCandidateEvidence


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _text(value: object) -> bool:
    return type(value) is str and bool(value.strip()) and value == value.strip()


def _digest(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(ch in "0123456789abcdef" for ch in value)


def _note(diagnostics: dict, reason: str, count: int = 1) -> None:
    if count <= 0:
        return
    reasons = diagnostics.setdefault("rejected", {})
    reasons[reason] = reasons.get(reason, 0) + count


def sentence_ranges(fragment: EvidenceFragment) -> tuple[dict, ...]:
    """문자를 바꾸지 않고 원문 좌표에 결속된 문장 선택표를 만든다."""
    text = fragment.text
    spans = []
    start = 0
    for boundary in c.SENTENCE_BOUNDARY_RE.finditer(text):
        spans.append((start, boundary.start()))
        start = boundary.end()
    spans.append((start, len(text)))
    result = []
    for start, end in spans:
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start == end:
            continue
        if len(result) >= c.MAX_SENTENCES:
            return ()
        digest = _hash(_json([fragment.fragment_id, fragment.text_sha256, start, end]))
        result.append({"id": "sentence-" + digest[:c.ID_DIGEST_CHARS], "start": start, "end": end, "text": text[start:end]})
    return tuple(result)


def _location_bound(fragment: EvidenceFragment, document: CollectedEvidenceDocument) -> bool:
    location = c.LOCATION_RE.fullmatch(fragment.location)
    if location is not None:
        start, end = map(int, location.groups())
        return end - start == len(fragment.text) and any(span.start <= start < end <= span.end for span in document.usable_ranges)
    if document.source_kind != SOURCE_KIND_OFFICIAL_IR_PDF:
        return False
    if fragment.range_index >= 0:
        index = fragment.range_index
        if fragment.location != f"{document.canonical_url} · 목록 {index + 1}번째 항목":
            return False
    else:
        prefix = document.canonical_url + "#"
        index_text = fragment.location.removeprefix(prefix)
        if not fragment.location.startswith(prefix) or not c.WEB_INDEX_RE.fullmatch(index_text):
            return False
        index = int(index_text)
    return index < len(document.usable_ranges) and document.usable_ranges[index].end - document.usable_ranges[index].start == len(fragment.text)


def _bound_pair(fragment: EvidenceFragment, document: CollectedEvidenceDocument, company_id: str, reference_date: str) -> bool:
    if type(fragment) not in (EvidenceFragment, OfficialIndustryCandidateEvidence) or type(document) is not CollectedEvidenceDocument:
        return False
    if type(fragment) is OfficialIndustryCandidateEvidence:
        fragment.__post_init__()
        if fragment.document != document:
            return False
    if document.source_kind == SOURCE_KIND_OFFICIAL_IR_PDF and not (
        document.ir_metadata_verification in IR_METADATA_VERIFICATION_VALUES
        and _text(document.domain_attestation_source_id) and _text(document.domain_attestation_evidence)
        and safe_https_attachment_url(document.attachment_url)
        and official_ir_time_is_usable(published_at=document.published_on, reporting_period=document.reporting_period, reference_date=reference_date)
    ):
        return False
    return bool(
        fragment.company_id == document.company_id == company_id
        and fragment.document_id == document.document_id
        and document.source_kind in FORMAL_DOCUMENT_SOURCE_KINDS
        and _text(document.identity_binding)
        and _text(document.canonical_url) and _text(document.publisher) and _text(document.title)
        and _digest(document.content_sha256)
        and _hash(fragment.text) == fragment.text_sha256
        and fragment.text_sha256 in document.exact_evidence_hashes
        and _location_bound(fragment, document)
        and c.DATE_RE.fullmatch(document.published_on)
        and date.fromisoformat(document.published_on) <= date.fromisoformat(reference_date)
    )


def select_candidates(*, candidates: tuple, anchors: tuple, company_id: str, reference_date: str, diagnostics: dict) -> tuple:
    """산업 범위와 앵커 명칭이 있는 원문만 기존 순서·예산 안에서 조사한다."""
    if type(candidates) is not tuple:
        _note(diagnostics, "invalid_candidates")
        return ()
    ids = Counter(pair[0].fragment_id for pair in candidates if type(pair) is tuple and len(pair) == 2 and type(pair[0]) in (EvidenceFragment, OfficialIndustryCandidateEvidence))
    result = []
    chars = 0
    for pair in candidates:
        if type(pair) is not tuple or len(pair) != 2:
            _note(diagnostics, "invalid_pair")
            continue
        fragment, document = pair
        if type(fragment) is EvidenceFragment and (fragment.item_url or fragment.item_title or fragment.item_published_on):
            _note(diagnostics, "item_metadata_out_of_scope")
            continue
        if (type(fragment) is EvidenceFragment and type(document) is CollectedEvidenceDocument
                and document.source_kind in OFFICIAL_WEB_SOURCE_KINDS
                and document.source_kind != SOURCE_KIND_OFFICIAL_IR_PDF
                and c.WEB_LIST_LOCATION_RE.search(fragment.location)):
            _note(diagnostics, "web_list_date_unbound")
            continue
        try:
            bound = _bound_pair(fragment, document, company_id, reference_date)
        except (TypeError, ValueError):
            bound = False
        if not bound or ids[fragment.fragment_id] != 1:
            _note(diagnostics, "unbound_candidate")
            continue
        # 탐색 조건이며 문제·현재성·지역 또는 같은 사업의 의미 승인이 아니다.
        if not c.INDUSTRY_SCOPE_RE.search(fragment.text) or not any(_business_quote_bound(anchor, fragment.text) for anchor in anchors):
            _note(diagnostics, "industry_discovery_unbound")
            continue
        if len(result) >= c.MAX_CANDIDATES or len(fragment.text) > c.MAX_FRAGMENT_CHARS or chars + len(fragment.text) > c.MAX_INPUT_CHARS:
            _note(diagnostics, "input_budget")
            continue
        sentences = sentence_ranges(fragment)
        if not sentences:
            _note(diagnostics, "sentence_budget")
            continue
        result.append((fragment, document, sentences))
        chars += len(fragment.text)
    diagnostics["input_chars"] = chars
    diagnostics["candidate_count"] = len(result)
    return tuple(result)


def _object(properties: dict) -> dict:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


def exact_field_options(selected: tuple, anchors: tuple) -> dict:
    """원문 좌표를 보존한 유한 선택값이며 의미 판정은 기존 검수가 맡는다."""
    result = {}
    for fragment, document, sentences in selected:
        periods = []
        seen = set()
        for text in (document.title, fragment.text):
            for match in c.EXACT_PERIOD_OPTION_RE.finditer(text):
                value = match.group()
                if value in seen:
                    continue
                seen.add(value)
                periods.append(value)
        applications = {}
        for anchor in anchors:
            item = "".join(anchor.business_item.split())
            core = c.BUSINESS_ACTIVITY_SUFFIX_RE.sub("", item)
            if len(core) < c.MIN_BUSINESS_CORE_CHARS:
                core = item
            for match in re.finditer(r"\s*".join(re.escape(char) for char in core), fragment.text):
                covered = [s for s in sentences if s["start"] < match.end() and match.start() < s["end"]]
                if (not covered or len(covered) > c.MAX_ASSESSMENT_SENTENCES
                        or covered[-1]["end"] - covered[0]["start"] > c.MAX_ASSESSMENT_CHARS
                        or not covered[0]["start"] <= match.start() < match.end() <= covered[-1]["end"]):
                    continue
                value = match.group()
                row = applications.setdefault(value, {
                    "text": value, "anchor_ids": [], "sentence_ids": [],
                    "start": match.start(), "end": match.end(),
                })
                if anchor.anchor_id not in row["anchor_ids"]:
                    row["anchor_ids"].append(anchor.anchor_id)
                for sentence in covered:
                    if sentence["id"] not in row["sentence_ids"]:
                        row["sentence_ids"].append(sentence["id"])
        result[fragment.fragment_id] = {
            "exact_period_options": periods, "applicability_quote_options": list(applications.values()),
        }
    return result


def response_schema(selected: tuple, anchors: tuple[BusinessActivityAnchor, ...]) -> dict:
    """각 조각×앵커의 제안/비제안을 한 판정으로 반환하는 닫힌 합집합."""
    if not selected or not anchors:
        return _object({"assessments": {"type": "array", "items": _object({}), "maxItems": 0}})
    options = exact_field_options(selected, anchors)
    periods = list(dict.fromkeys(period for value in options.values() for period in value["exact_period_options"]))
    applications = list(dict.fromkeys(row["text"] for value in options.values() for row in value["applicability_quote_options"]))
    base = {"fragment_id": {"type": "string", "enum": [f.fragment_id for f, _, _ in selected]}, "anchor_id": {"type": "string", "enum": [a.anchor_id for a in anchors]}}
    # 문장 enum을 조각×앵커마다 복제하지 않는다. 실제 닫힌 membership은 서버가
    # 검산하며 SDK가 pattern을 설명으로 옮겨도 해당 검증은 그대로다.
    proposed = {
        **base, "status": {"type": "string", "enum": ["proposed"]},
        "sentence_ids": {"type": "array", "items": {"type": "string", "pattern": c.SENTENCE_ID_PATTERN}, "minItems": 1, "maxItems": c.MAX_ASSESSMENT_SENTENCES},
        **{name: {"type": "boolean"} for name in c.COMMON_BOOL_FIELDS},
        **{name: {"type": "string", "minLength": 1} for name in (*c.COMMON_QUOTE_FIELDS, "observation_period")},
    }
    proposed["observation_period"] = {"type": "string", "enum": periods}
    proposed["applicability_quote"] = {"type": "string", "enum": applications}
    branches = [_object({**base, "status": {"type": "string", "enum": list(c.STATUSES[1:])}}), _object({
        **proposed,
        "geography_supported": {"type": "boolean", "enum": [True]},
        **{name: {"type": "string", "minLength": 1} for name in c.GEOGRAPHY_QUOTE_FIELDS},
        "geography": {"type": "string", "enum": sorted(INDUSTRY_GEOGRAPHIES)},
    }), _object({
        **proposed,
        "geography_supported": {"type": "boolean", "enum": [False]},
        **{name: {"type": "string", "enum": [""]} for name in c.GEOGRAPHY_QUOTE_FIELDS},
        "geography": {"type": "string", "enum": [OFFICIAL_UNSPECIFIED_GEOGRAPHY]},
    })]
    if not periods or not applications:
        branches = branches[:1]
    return _object({"assessments": {"type": "array", "items": {"anyOf": branches}, "maxItems": len(selected) * len(anchors)}})


def build_prompt(selected: tuple, anchors: tuple, company_id: str, reference_date: str) -> str:
    options = exact_field_options(selected, anchors)
    payload = {
        "version": c.PROMPT_VERSION, "company_id": company_id, "reference_date": reference_date,
        "anchors": [{"anchor_id": a.anchor_id, "company_id": a.company_id, "business_item": a.business_item, "exact_text": a.exact_text, "location": a.location, "text_sha256": a.text_sha256, "source_kind": a.source_kind, "document_content_sha256": a.document_content_sha256, "identity_binding": a.identity_binding} for a in anchors],
        "materials": [{"fragment_id": f.fragment_id, "document_id": d.document_id, "source_kind": d.source_kind, "source_url": d.canonical_url, "publisher": d.publisher, "title": d.title, "published_on": d.published_on, "location": f.location, "text_sha256": f.text_sha256, "document_content_sha256": d.content_sha256, "identity_binding": d.identity_binding, "reporting_period": d.reporting_period, "text": f.text, "sentences": list(sentences), **options[f.fragment_id]} for f, d, sentences in selected],
    }
    return c.GUIDE + "\n다음 자료만 이번 검수 대상으로 사용하세요.\n" + _json(payload)


def _assessment_quote(ids: object, sentences: tuple, text: str) -> str:
    if type(ids) is not list or not 1 <= len(ids) <= c.MAX_ASSESSMENT_SENTENCES or any(type(value) is not str for value in ids):
        raise ValueError("평가 문장 선택의 형식이 다릅니다")
    indexes = {row["id"]: index for index, row in enumerate(sentences)}
    selected = [indexes[value] for value in ids]
    if selected != list(range(selected[0], selected[0] + len(selected))):
        raise ValueError("평가 문장이 연속 원문이 아닙니다")
    quote = text[sentences[selected[0]]["start"]:sentences[selected[-1]]["end"]]
    if len(quote) > c.MAX_ASSESSMENT_CHARS or text.count(quote) != 1:
        raise ValueError("평가 원문의 길이 또는 유일성이 다릅니다")
    return quote


def _contradiction(entry: dict, quote: str) -> bool:
    problem = entry["problem"]
    units = [part for part in c.ASSESSMENT_CLAUSE_RE.split(quote) if problem in part]
    if not units or problem in c.GENERIC_PROBLEMS:
        return True
    if all(c.FUTURE_RE.search(unit) or c.PAST_OR_RESOLVED_RE.search(unit) for unit in units):
        return True
    if all(c.DEFINITION_RE.search(unit) for unit in units):
        return True
    if all(c.DOMINANCE_RE.search(unit) and not c.ACTUAL_HARM_RE.search(unit) for unit in units):
        return True
    if all(c.ACCOUNTING_RE.search(unit) and c.POLICY_END_RE.search(unit) for unit in units):
        return True
    if all(c.CUSTOMER_USE_RE.search(unit) and not c.SUPPLIER_IMPACT_RE.search(unit) for unit in units):
        return True
    geography = entry["geography"]
    period_years = set(c.YEAR_RE.findall(entry["observation_period"]))
    for unit in units:
        years = set(c.YEAR_RE.findall(unit))
        if years and not years.intersection(period_years):
            return True
        lowered = unit.casefold()
        domestic = any(marker in lowered for marker in c.DOMESTIC_MARKERS)
        foreign = any(marker in lowered for marker in c.FOREIGN_SCOPE_MARKERS)
        world = any(marker in lowered for marker in c.WORLD_MARKERS)
        if geography == OFFICIAL_UNSPECIFIED_GEOGRAPHY:
            scope = c.REGION_ACTOR_RE.sub("", lowered)
            if (any(marker in scope for marker in (
                *c.DOMESTIC_MARKERS, *c.FOREIGN_SCOPE_MARKERS, *c.WORLD_MARKERS,
            )) or c.NAMED_REGION_SCOPE_RE.search(scope)):
                return True
        if geography == "domestic" and foreign and not domestic:
            return True
        if geography == "foreign" and domestic and not foreign:
            return True
        if geography == "global" and domestic and not world:
            return True
    if geography == OFFICIAL_UNSPECIFIED_GEOGRAPHY:
        return False
    detail = entry["geography_detail"].casefold()
    evidence = entry["geography_evidence"].casefold()
    if detail not in evidence or detail in c.GENERIC_GEOGRAPHIES:
        return True
    if geography == "domestic":
        return not any(marker in detail for marker in c.DOMESTIC_MARKERS)
    if geography == "global":
        return not any(marker in evidence for marker in c.WORLD_MARKERS) or bool(c.GLOBAL_ACTOR_RE.search(quote) and not any(marker in evidence for marker in c.EXPLICIT_WORLD_MARKERS))
    return geography != "foreign" or any(marker in detail for marker in (*c.DOMESTIC_MARKERS, *c.WORLD_MARKERS))


def _unique_object(pairs: list) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("검수 JSON에 중복 필드가 있습니다")
        result[key] = value
    return result


def _business_quote_bound(anchor: BusinessActivityAnchor, quote: str) -> bool:
    """공식 명칭과 다른 제공물은 의미 긍정만으로 연결하지 않는다."""
    item = "".join(anchor.business_item.split())
    core = c.BUSINESS_ACTIVITY_SUFFIX_RE.sub("", item)
    if len(core) < c.MIN_BUSINESS_CORE_CHARS:
        core = item
    return core in "".join(quote.split())


def validate_response(raw: object, *, selected: tuple, anchors: tuple, diagnostics: dict) -> tuple[IndustryProblemEvidence, ...]:
    """원응답과 파생 지문을 분리하고 긍정 상태와 별개로 원문을 재검산한다."""
    if type(raw) is str:
        diagnostics["response_sha256"] = _hash(raw)
        diagnostics["response_sha256_basis"] = "raw_text"
        try:
            payload = json.loads(raw, object_pairs_hook=_unique_object)
        except (TypeError, ValueError):
            _note(diagnostics, "invalid_json")
            return ()
    elif type(raw) is dict:
        payload = raw
        try:
            diagnostics["response_sha256"] = _hash(_json(raw))
        except (TypeError, ValueError):
            _note(diagnostics, "invalid_response")
            return ()
        diagnostics["response_sha256_basis"] = "canonical_mapping"
    else:
        _note(diagnostics, "invalid_response")
        return ()
    try:
        diagnostics["normalized_response_sha256"] = _hash(_json(payload))
    except (TypeError, ValueError):
        _note(diagnostics, "invalid_response")
        return ()
    if type(payload) is not dict or set(payload) != {"assessments"} or type(payload["assessments"]) is not list:
        _note(diagnostics, "invalid_envelope")
        return ()
    expected = {(f.fragment_id, a.anchor_id) for f, _, _ in selected for a in anchors}
    rows = payload["assessments"]
    if len(rows) > len(expected):
        _note(diagnostics, "response_budget")
        return ()
    pairs = Counter((row.get("fragment_id"), row.get("anchor_id")) for row in rows if type(row) is dict and type(row.get("fragment_id")) is str and type(row.get("anchor_id")) is str)
    _note(diagnostics, "missing_assessment", len(expected - pairs.keys()))
    proposed = Counter(row.get("fragment_id") for row in rows if type(row) is dict and row.get("status") == "proposed" and type(row.get("fragment_id")) is str)
    diagnostics["assessment_count"] = len(rows)
    diagnostics["proposed_count"] = sum(proposed.values())
    diagnostics["raw_statuses"] = dict(Counter(row.get("status") for row in rows if type(row) is dict and type(row.get("status")) is str))
    too_many = sum(proposed.values()) > c.MAX_OUTPUT_ITEMS
    by_fragment = {f.fragment_id: (f, d, sentences) for f, d, sentences in selected}
    by_anchor = {a.anchor_id: a for a in anchors}
    result = []
    for row in rows:
        try:
            if type(row) is not dict or any(type(row.get(key)) is not str for key in c.BASE_FIELDS):
                raise ValueError("판정 식별자 형식이 다릅니다")
            pair = (row["fragment_id"], row["anchor_id"])
            if pair not in expected or pairs[pair] != 1 or row["status"] not in c.STATUSES:
                raise ValueError("판정 조합이 요청 밖이거나 중복됐습니다")
            if row["status"] != "proposed":
                if set(row) != set(c.BASE_FIELDS):
                    raise ValueError("비제안 판정에 미지 필드가 있습니다")
                diagnostics.setdefault("statuses", {}).setdefault(row["status"], 0)
                diagnostics["statuses"][row["status"]] += 1
                continue
            if too_many or proposed[row["fragment_id"]] != 1:
                raise ValueError("제안 한도를 초과했습니다")
            if set(row) != set(c.PROPOSED_FIELDS) or any(row[name] is not True for name in c.COMMON_BOOL_FIELDS):
                raise ValueError("닫힌 제안 검수 필드가 미충족입니다")
            unspecified = row["geography"] == OFFICIAL_UNSPECIFIED_GEOGRAPHY
            if row["geography_supported"] is not (not unspecified):
                raise ValueError("지역 범주와 실제 지역 검수 상태가 다릅니다")
            if unspecified and any(row[name] != "" for name in c.GEOGRAPHY_QUOTE_FIELDS):
                raise ValueError("지역 미확인 판정에 임의 지역 근거가 있습니다")
            fragment, document, sentences = by_fragment[row["fragment_id"]]
            anchor = by_anchor[row["anchor_id"]]
            quote = _assessment_quote(row["sentence_ids"], sentences, fragment.text)
            quote_fields = c.COMMON_QUOTE_FIELDS if unspecified else c.QUOTE_FIELDS
            if any(not _text(row[name]) or row[name] not in quote for name in quote_fields):
                raise ValueError("산업 판정이 평가 원문 밖의 문구를 빌렸습니다")
            if not _business_quote_bound(anchor, row["applicability_quote"]):
                raise ValueError("공식 사업 제공물이 적용 인용에 없습니다")
            period = row["observation_period"]
            if not _text(period) or (period not in document.title and period not in quote) or not c.YEAR_RE.search(period):
                raise ValueError("관찰 기간이 공식 제목이나 평가 원문에 없습니다")
            quote_years, period_years = set(c.YEAR_RE.findall(quote)), set(c.YEAR_RE.findall(period))
            if quote_years and period_years and not quote_years.intersection(period_years):
                raise ValueError("평가 원문과 선택 기간의 명시 연도가 다릅니다")
            if any(int(year) > date.fromisoformat(document.published_on).year for year in period_years):
                raise ValueError("공표 이후 기간을 현재 관찰로 선택했습니다")
            if row["geography"] not in OFFICIAL_INDUSTRY_GEOGRAPHIES or _contradiction(row, quote):
                raise ValueError("문제·사업·지역·기간의 닫힌 모순이 있습니다")
            result.append(IndustryProblemEvidence(
                evidence_id="official-industry-" + _hash(_json([fragment.fragment_id, anchor.anchor_id, fragment.text_sha256, quote]))[:c.ID_DIGEST_CHARS],
                business_anchor_id=anchor.anchor_id, document_id=document.document_id,
                source_url=document.canonical_url, publisher=document.publisher, title=document.title,
                published_on=document.published_on, location=fragment.location, exact_text=fragment.text,
                text_sha256=fragment.text_sha256, industry=row["industry"], problem=row["problem"],
                geography=row["geography"], geography_detail=row["geography_detail"], geography_evidence=row["geography_evidence"],
                document_content_sha256=document.content_sha256, analysis_response_sha256=diagnostics["response_sha256"],
                applicability_quote=row["applicability_quote"], source_kind=document.source_kind,
                identity_binding=document.identity_binding, assessment_quote=quote, observation_period=period,
            ))
        except (KeyError, TypeError, ValueError):
            _note(diagnostics, "unbound_assessment")
    diagnostics["verified_count"] = len(result)
    diagnostics["verified_region_unspecified_count"] = sum(
        value.geography == OFFICIAL_UNSPECIFIED_GEOGRAPHY for value in result
    )
    diagnostics["verified_regional_count"] = (
        len(result) - diagnostics["verified_region_unspecified_count"]
    )
    return tuple(result)


def collect_official_industry_context(*, candidates: tuple[tuple[EvidenceFragment, CollectedEvidenceDocument], ...], anchors: tuple[BusinessActivityAnchor, ...], company_id: str, reference_date: str, analyze: Callable[[str, dict, int], object], diagnostics: dict) -> tuple[IndustryProblemEvidence, ...]:
    """계량·원장 콜백을 최대 한 번 호출하며 자체 재시도하지 않는다."""
    diagnostics["version"] = c.PROMPT_VERSION
    diagnostics["model_calls"] = 0
    if not _text(company_id) or not _text(reference_date) or not c.DATE_RE.fullmatch(reference_date):
        _note(diagnostics, "invalid_identity_or_date")
        return ()
    try:
        date.fromisoformat(reference_date)
    except ValueError:
        _note(diagnostics, "invalid_reference_date")
        return ()
    if type(anchors) is not tuple or not 1 <= len(anchors) <= c.MAX_ANCHORS:
        _note(diagnostics, "invalid_anchors")
        return ()
    try:
        for anchor in anchors:
            if type(anchor) is not BusinessActivityAnchor or anchor.company_id != company_id or not _text(anchor.identity_binding) or not _digest(anchor.document_content_sha256):
                raise ValueError("사업 앵커의 회사 결속이 다릅니다")
            anchor.__post_init__()
            if not c.DATE_RE.fullmatch(anchor.published_on) or date.fromisoformat(anchor.published_on) > date.fromisoformat(reference_date):
                raise ValueError("사업 앵커의 공식 공표일이 미확인 또는 미래입니다")
        if len({a.anchor_id for a in anchors}) != len(anchors):
            raise ValueError("사업 앵커가 중복됐습니다")
    except (TypeError, ValueError):
        _note(diagnostics, "unbound_anchor")
        return ()
    selected = select_candidates(candidates=candidates, anchors=anchors, company_id=company_id, reference_date=reference_date, diagnostics=diagnostics)
    if not selected:
        return ()
    prompt = build_prompt(selected, anchors, company_id, reference_date)
    while selected and len(prompt) > c.MAX_PROMPT_CHARS:
        _note(diagnostics, "prompt_budget")
        selected = selected[:-1]
        prompt = build_prompt(selected, anchors, company_id, reference_date)
    diagnostics["input_chars"] = sum(len(f.text) for f, _, _ in selected)
    diagnostics["candidate_count"] = len(selected)
    if not selected:
        return ()
    schema = response_schema(selected, anchors)
    diagnostics["prompt_sha256"] = _hash(prompt)
    diagnostics["schema_sha256"] = _hash(_json(schema))
    diagnostics["model_calls"] = 1
    raw = analyze(prompt, schema, c.MAX_OUTPUT_TOKENS)
    return validate_response(raw, selected=selected, anchors=anchors, diagnostics=diagnostics)
