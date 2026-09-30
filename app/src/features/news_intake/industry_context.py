"""공식 사업과 산업 문제의 관계만 검수하고 회사 직접 사실과 분리한다."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import datetime as dt
import json

from src.features.news_intake import industry_constants as ic
from src.features.news_intake.grounded import parse_grounded_payload, GROUNDED_ANALYSIS_SCHEMA
from src.features.news_intake.models import NewsCandidate, NewsCompanyContext
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


def extend_schema(schema: dict, company: NewsCompanyContext) -> dict:
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
    item["properties"]["industry_problems"] = {
        "type": "array", "maxItems": ic.INDUSTRY_MAX_PROBLEMS_PER_ARTICLE,
        "items": {"type": "object", "additionalProperties": False,
                  "properties": properties, "required": list(ic.INDUSTRY_REQUIRED_FIELDS)},
    }
    item["required"].append("industry_problems")
    return schema


def extend_prompt(prompt: str, company: NewsCompanyContext) -> str:
    if not company.business_anchors:
        return prompt
    guide = (
        "\n산업문맥 검수 " + ic.INDUSTRY_PROMPT_VERSION + ": 각 기사에 industry_problems 배열을 추가하세요. "
        "회사 직접 사실의 same_company/material/excerpts 규칙은 그대로 지키세요. "
        "산업 기사에서 회사명이 없어도 산업 문제가 원문에 실제 명시되고 공식 사업 앵커의 구체 제품·서비스와 "
        "같은 산업/사업 활동일 때만 산업문맥 1개를 반환합니다. 다른 산업·인접시장·일반 경제·회계 상용구는 제외합니다. "
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
    return guide + "\n" + prompt


def split_response(raw: object, *, articles: list[tuple[NewsCandidate, str]],
                   company: NewsCompanyContext, as_of: dt.date,
                   full_body_hashes: dict[str, str],
                   observations: Counter[str] | None = None) -> tuple[object, tuple[IndustryProblemEvidence, ...], dict[str, int]]:
    if not company.business_anchors:
        return raw, (), {}
    items = parse_grounded_payload(raw)
    if items is None:
        return raw, (), {"industry_invalid_response": 1}
    by_id = {candidate.id: (candidate, body) for candidate, body in articles}
    anchors = {anchor.anchor_id: anchor for anchor in company.business_anchors}
    counts = Counter(item.get("id") for item in items if type(item) is dict and type(item.get("id")) is str)
    direct, problems, rejected = [], [], Counter()
    missing = set(by_id) - set(counts)
    if missing:
        rejected["industry_invalid_missing_result"] += len(missing)
    response_hash = exact_text_sha256(json.dumps(items, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    for item in items:
        if type(item) is not dict:
            direct.append(item)
            continue
        clean = dict(item)
        entries = clean.pop("industry_problems", None)
        direct.append(clean)
        if type(item.get("id")) is not str:
            rejected["industry_invalid_item"] += 1
            continue
        candidate_body = by_id.get(item.get("id"))
        expected_keys = set(GROUNDED_ANALYSIS_SCHEMA["properties"]["items"]["items"]["required"]) | {"industry_problems"}
        if (set(item) != expected_keys or candidate_body is None or counts[item.get("id")] != 1 or type(entries) is not list
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
