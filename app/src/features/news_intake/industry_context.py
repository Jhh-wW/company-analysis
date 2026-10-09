"""공식 사업과 산업 문제의 관계만 검수하고 회사 직접 사실과 분리한다."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import datetime as dt
import json

from src.features.news_intake import industry_constants as ic
from src.features.news_intake.industry_assessment import normalize_assessments, observe_assessments
from src.features.news_intake.article_text_scope import overlaps_auxiliary
from src.features.news_intake.grounded import parse_grounded_payload, GROUNDED_ANALYSIS_SCHEMA
from src.features.news_intake.models import NewsCandidate, NewsCompanyContext
from src.features.news_intake.quote_selection import quote_response_sha256, restore_quote_response
from src.shared.business_challenge_context import IndustryProblemEvidence, INDUSTRY_GEOGRAPHIES
from src.shared.report_generation.models import exact_text_sha256


def _has_supported_problem_and_geography(entry: dict[str, object], text: str) -> bool:
    """모델의 긍정 판정과 별개로 원문에 드러난 닫힌 모순만 제외한다."""

    problem = str(entry["problem"])
    if problem in ic.INDUSTRY_GENERIC_PROBLEMS:
        return False
    problem_clauses = tuple(clause for clause in ic.INDUSTRY_PROBLEM_CLAUSE_RE.split(text)
                            if problem in clause)
    if problem_clauses and all(ic.INDUSTRY_ACCOUNTING_POLICY_RE.search(clause) for clause in problem_clauses):
        return False
    if problem_clauses and all(ic.INDUSTRY_POSSIBILITY_RE.search(clause) for clause in problem_clauses):
        return False
    region = str(entry["geography_evidence"]).casefold()
    detail = str(entry["geography_detail"]).casefold()
    geography = entry["geography"]
    if detail not in region:
        return False
    if geography == "domestic":
        return any(marker in detail for marker in ic.INDUSTRY_DOMESTIC_MARKERS)
    if geography == "foreign":
        return not any(marker in detail for marker in (
            *ic.INDUSTRY_DOMESTIC_MARKERS, *ic.INDUSTRY_GLOBAL_MARKERS,
        ))
    if geography != "global" or not any(marker in region for marker in ic.INDUSTRY_GLOBAL_MARKERS):
        return False
    # '글로벌 기업의 국내 공장'에서 글로벌은 기업의 수식어다.
    # 세계 전체의 문제라는 별도 명시 근거가 있을 때만 같은 문장에 허용한다.
    if ic.INDUSTRY_GLOBAL_COMPANY_MODIFIER_RE.search(text) and not any(
        marker in region for marker in ic.INDUSTRY_EXPLICIT_WORLD_SCOPE_MARKERS
    ):
        return False
    return True


def industry_candidate(candidate: NewsCandidate) -> bool:
    return any(topic.startswith(ic.INDUSTRY_TOPIC_PREFIX) for topic in candidate.topics)


def extend_schema(schema: dict, company: NewsCompanyContext, *, priority: bool = False) -> dict:
    if not company.business_anchors:
        return schema
    schema = deepcopy(schema)
    item = schema["properties"]["items"]["items"]
    properties = {name: {"type": "string", "minLength": 1, "maxLength": ic.INDUSTRY_QUOTE_MAX_CHARS}
                  for name in ic.INDUSTRY_REQUIRED_FIELDS}
    properties["anchor_id"] = {"type": "string", "enum": [anchor.anchor_id for anchor in company.business_anchors]}
    properties["geography"] = {"type": "string", "enum": sorted(INDUSTRY_GEOGRAPHIES)}
    for name in ("problem_present", "same_business", "geography_supported"):
        properties[name] = {"type": "boolean"}
    required = list(ic.INDUSTRY_REQUIRED_FIELDS)
    if "entity_evidence_quote_id" in item["properties"]:
        properties.pop("text")
        properties["text_quote_id"] = deepcopy(item["properties"]["excerpts"]["items"]["properties"]["text_quote_id"])
        required[required.index("text")] = "text_quote_id"
    item["properties"]["industry_problems"] = {
        "type": "array", "maxItems": ic.INDUSTRY_MAX_PROBLEMS_PER_ARTICLE,
        "items": {"type": "object", "additionalProperties": False,
                  "properties": properties, "required": required},
    }
    item["required"].append("industry_problems")
    if priority:
        item["properties"].pop("industry_problems")
        item["required"].remove("industry_problems")
        item["properties"][ic.INDUSTRY_ASSESSMENT_FIELD] = {
            "type": "array", "minItems": len(company.business_anchors),
            "maxItems": len(company.business_anchors),
            "items": {"anyOf": [
                {"type": "object", "additionalProperties": False,
                 "properties": {**deepcopy(properties), "status": {"type": "string", "enum": ["proposed"]}},
                 "required": [*required, "status"]},
                {"type": "object", "additionalProperties": False,
                      "properties": {
                          "anchor_id": {"type": "string", "enum": [anchor.anchor_id for anchor in company.business_anchors]},
                          "status": {"type": "string", "enum": list(ic.INDUSTRY_ASSESSMENT_STATUSES[1:])},
                      }, "required": ["anchor_id", "status"]},
            ]},
        }
        item["required"].append(ic.INDUSTRY_ASSESSMENT_FIELD)
    return schema


def extend_prompt(prompt: str, company: NewsCompanyContext, *, priority: bool = False) -> str:
    if not company.business_anchors:
        return prompt
    guide = (
        "\n산업문맥 검수 " + ic.INDUSTRY_PROMPT_VERSION + ": 각 기사에 industry_problems 배열을 추가하세요. "
        "회사 직접 사실의 same_company/material/excerpts 규칙은 그대로 지키세요. "
        "기사마다 회사 직접 사건과 산업 문제를 서로 독립적으로 검사하고, 출력 전에 excerpts와 industry_problems를 각각 확인하세요. "
        "회사 직접 사건은 대상 회사의 행동·피해·대응이 해당 인용에 명시된 경우에만 excerpts로 제안합니다. "
        "업계 관계자의 말이나 업계 전체의 문제를 대상 회사의 issue로 옮기지 마세요. "
        "산업 검사는 회사명 유무와 same_company/material 판정에 관계없이 수행합니다. "
        "회사명이 등장하거나 회사 직접 인용이 있는 기사에도 공식 사업과 같은 업계의 현재 문제가 있으면 별도로 industry_problems를 검사하세요. "
        "두 검사가 각각 충족되면 두 배열에 모두 제안하고, 한쪽이 비거나 실패했다는 이유로 다른 쪽을 비우지 마세요. "
        "회사 직접 사건이 없고 산업 문제만 확인되면 excerpts=[]와 검증 가능한 industry_problems를 반환합니다. "
        "회사 직접 인용의 주어 미확인을 산업 근거의 합격으로 바꾸지 말고 산업의 사업·현재 문제·지역 근거를 별도로 확인하세요. "
        "산업 기사에서 회사명이 없어도 산업 문제가 원문에 실제 명시되고 공식 사업 앵커의 구체 제품·서비스와 "
        "같은 산업/사업 활동일 때만 산업문맥 1개를 반환합니다. 다른 산업·인접시장·일반 경제·회계 상용구는 제외합니다. "
        + ic.INDUSTRY_BUSINESS_ACTIVITY_GUIDE +
        "제목·검색요약·발행처 이름에서 문제나 지역을 추론하지 마세요. text는 본문에 있는 하나의 연속 원문이며 "
        "industry/problem/geography_detail/geography_evidence/applicability_quote는 모두 그 text의 연속부분을 그대로 복사하세요. "
        "applicability_quote는 공식 사업과 같은 구체 사업 활동임을 보여주는 text 안의 연속 원문입니다. "
        "숫자·피해·회사대응·인과관계를 새로 만들거나 확대하지 마세요. "
        "problem_present는 현재 실재 문제가 text에 명시될 때, same_business는 anchor_id의 공식원문과 산업원문의 "
        "구체 사업 활동이 같을 때, geography_supported는 명시된 실제 적용지역이 geography와 일치할 때만 true입니다. "
        "국내=한국 적용 명시, global=세계/전세계 적용 명시, foreign=명시된 해외 특정지역입니다. "
        "한국어 기사라는 이유로 국내, 해외 매체라는 이유로 global을 고르지 마세요. 미래 가능성만 있으면 제외합니다. "
        "검증 불가면 industry_problems=[]입니다. 회사 피해가 실제라는 해석 문구는 생성하지 마세요. "
        "공식 사업 앵커=" + json.dumps([asdict(anchor) for anchor in company.business_anchors], ensure_ascii=False)
    )
    if priority:
        guide = guide.replace("검증 불가면 industry_problems=[]입니다.",
                              "검증 불가면 해당 앵커의 비제안 상태를 반환합니다.")
        guide = guide.replace("같은 산업/사업 활동일 때만 산업문맥 1개를 반환합니다.",
                              "같은 산업/사업 활동일 때만 기사당 산업 근거 최대 1개를 제안합니다. "
                              "근거 제안 여부와 관계없이 모든 공식 앵커의 판정 상태를 반환합니다.")
        guide = guide.replace("두 검사가 각각 충족되면 두 배열에 모두 제안하고, 한쪽이 비거나 실패했다는 이유로 다른 쪽을 비우지 마세요.",
                              "회사 직접 검수와 산업 검수를 각각 수행하고, 회사 인용의 실패로 산업 판정을 생략하지 마세요.")
        guide = guide.replace("industry_problems", ic.INDUSTRY_ASSESSMENT_FIELD)
    priority_guide = ic.INDUSTRY_PRIORITY_GUIDE if priority else ""
    if any(anchor.source_kind == "news" for anchor in company.business_anchors):
        # 출처 종류를 바꾸어 부르거나 인수한 사업을 직접 생산·주력으로 승격하지 않는다.
        guide = guide.replace("공식 사업", "검증된 사업").replace("공식원문", "자기 사업 원문").replace("공식 앵커", "검증된 사업 앵커")
        priority_guide = priority_guide.replace("공식 사업", "검증된 사업").replace("공식 anchor_id", "검증된 anchor_id").replace("공식 앵커", "검증된 사업 앵커")
        guide += (
            "\n뉴스 사업앵커는 검증된 대상 회사의 연속 인용에 나온 현재 사업 또는 "
            "인수한 사업의 관련성만 뜻합니다. 인수 사업을 주력·최초 개시·직접 생산으로 "
            "확대하지 마세요. 산업 문제는 별도 자기 원문과 동일 사업 활동·지역·기간을 검증하세요."
        )
    return priority_guide + guide + "\n" + prompt


def split_response(raw: object, *, articles: list[tuple[NewsCandidate, str]],
                   company: NewsCompanyContext, as_of: dt.date,
                   full_body_hashes: dict[str, str],
                   observations: Counter[str] | None = None,
                   source_response_sha256: str | None = None,
                   assessment_required: bool = False,
                   assessment_records: list[dict[str, str]] | None = None,
                   normalization_traces: list[dict[str, str | None]] | None = None) -> tuple[object, tuple[IndustryProblemEvidence, ...], dict[str, int]]:
    if not company.business_anchors:
        return raw, (), {}
    source_response_sha256 = source_response_sha256 or quote_response_sha256(raw)
    items = parse_grounded_payload(restore_quote_response(raw, articles=articles, company=company))
    if items is None:
        return raw, (), {"industry_invalid_response": 1}
    normalization_rejected = {}
    if assessment_required:
        items, normalization_rejected = normalize_assessments(items, company=company)
        if normalization_traces is not None:
            normalization_traces.append({"원응답정규JSON_SHA256": source_response_sha256,
                                         "정규화응답정규JSON_SHA256": quote_response_sha256({"items": items})})
    by_id = {candidate.id: (candidate, body) for candidate, body in articles}
    anchors = {anchor.anchor_id: anchor for anchor in company.business_anchors}
    counts = Counter(item.get("id") for item in items if type(item) is dict and type(item.get("id")) is str)
    direct, problems, rejected = [], [], Counter()
    rejected.update(normalization_rejected)
    rejected.update(observe_assessments(items, articles=articles, company=company,
                                       required=assessment_required, records=assessment_records))
    missing = set(by_id) - set(counts)
    if missing:
        rejected["industry_invalid_missing_result"] += len(missing)
    response_hash = source_response_sha256 or exact_text_sha256(json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    for item in items:
        if type(item) is not dict:
            direct.append(item)
            continue
        clean = dict(item)
        clean.pop(ic.INDUSTRY_ASSESSMENT_FIELD, None)
        entries = clean.pop("industry_problems", None)
        direct.append(clean)
        if type(item.get("id")) is not str:
            rejected["industry_invalid_item"] += 1
            continue
        candidate_body = by_id.get(item.get("id"))
        expected_keys = set(GROUNDED_ANALYSIS_SCHEMA["properties"]["items"]["items"]["required"]) | {"industry_problems"}
        if (set(item) - {"invalid_quote_selection", ic.INDUSTRY_ASSESSMENT_FIELD} != expected_keys or candidate_body is None or counts[item.get("id")] != 1 or type(entries) is not list
                or type(item.get("same_company")) is not bool or type(item.get("material")) is not bool
                or type(item.get("entity_evidence")) is not str or type(item.get("excerpts")) is not list
                or type(item.get("source_type")) is not str
                or item.get("source_type") not in {"news_report", "official_release"}
                or len(entries) > ic.INDUSTRY_MAX_PROBLEMS_PER_ARTICLE):
            rejected["industry_invalid_item"] += 1
            continue
        candidate, body = candidate_body
        if observations is not None:
            observations["응답기사"] += 1
            observations["빈제안기사"] += int(not entries)
            observations["제안근거"] += len(entries)
        for entry in entries:
            try:
                if type(entry) is not dict or set(entry) != set(ic.INDUSTRY_REQUIRED_FIELDS):
                    raise ValueError("닫힌 산업 검수 필드 불일치")
                if any(entry[name] is not True for name in ("problem_present", "same_business", "geography_supported")):
                    raise ValueError("산업 문제·사업·지역 검수 미충족")
                anchor = anchors[entry["anchor_id"]]
                text = entry["text"]
                if (type(text) is not str or not text.strip() or text != text.strip()
                        or len(text) > ic.INDUSTRY_QUOTE_MAX_CHARS or body.count(text) != 1
                        or candidate.published_on > as_of.isoformat()
                        or not candidate.source_category in {"news_report", "official_release"}
                        or any(type(entry[name]) is not str or not entry[name].strip()
                               or entry[name] not in text for name in ("industry", "problem", "geography_detail", "geography_evidence", "applicability_quote"))):
                    raise ValueError("산업 원문·출처·날짜 결속 미충족")
                start = body.index(text)
                if overlaps_auxiliary(body, start, start + len(text)):
                    raise ValueError("산업 인용이 기사 밖 메타 목록에 걸칩니다")
                if not _has_supported_problem_and_geography(entry, text):
                    raise ValueError("실제 산업 문제 또는 적용 지역의 명시 근거가 없습니다")
                problems.append(IndustryProblemEvidence(
                    evidence_id="industry-" + exact_text_sha256(candidate.id + anchor.anchor_id + text)[:20],
                    business_anchor_id=anchor.anchor_id, document_id=candidate.id,
                    source_url=candidate.source_url, publisher=candidate.publisher, title=candidate.title,
                    published_on=candidate.published_on, location=f"chars:{start}-{start + len(text)}",
                    exact_text=text, text_sha256=exact_text_sha256(text), industry=entry["industry"],
                    problem=entry["problem"], geography=entry["geography"], geography_detail=entry["geography_detail"],
                    geography_evidence=entry["geography_evidence"],
                    applicability_quote=entry["applicability_quote"],
                    document_content_sha256=full_body_hashes[candidate.source_url], analysis_response_sha256=response_hash,
                ))
                if observations is not None:
                    observations["검증생존"] += 1
            except (KeyError, TypeError, ValueError):
                rejected["industry_unbound_problem"] += 1
                if observations is not None:
                    observations["검증탈락"] += 1
    return {"items": direct}, tuple(problems), dict(rejected)
