"""기사×공식 사업의 모델 판정 상태를 근거 검증과 별도로 관측한다."""
from collections import Counter
from copy import deepcopy

from src.features.news_intake import constants as c
from src.features.news_intake import industry_constants as ic
from src.features.news_intake.models import NewsCompanyContext, NewsCandidate


def normalize_assessments(items: list, *, company: NewsCompanyContext) -> tuple[list, dict[str, int]]:
    """단일 판정을 기존 검수 입력으로 투영한다. 원응답·근거·의미 판정은 바꾸지 않는다."""
    result = deepcopy(items)
    rejected = Counter()
    anchor_ids = {anchor.anchor_id for anchor in company.business_anchors}
    for item in result:
        if type(item) is not dict:
            continue
        mixed = "industry_problems" in item
        if mixed:
            rejected["industry_assessment_legacy_conflict"] += 1
        item["industry_problems"] = []
        entries = item.get(ic.INDUSTRY_ASSESSMENT_FIELD)
        if type(entries) is not list:
            continue
        if len(entries) > len(anchor_ids) or len(anchor_ids) > ic.INDUSTRY_ASSESSMENT_MAX_ANCHORS:
            rejected["industry_assessment_invalid_entries"] += 1
            continue
        counts = Counter(entry.get("anchor_id") for entry in entries
                         if type(entry) is dict and type(entry.get("anchor_id")) is str)
        states, proposals = [], []
        for entry in entries:
            if type(entry) is not dict:
                states.append(entry)
                continue
            status = entry.get("status")
            expected = ({*ic.INDUSTRY_REQUIRED_FIELDS, "status"} if status == "proposed"
                        else {"anchor_id", "status"})
            if (set(entry) != expected or type(status) is not str
                    or status not in ic.INDUSTRY_ASSESSMENT_STATUSES
                    or type(entry.get("anchor_id")) is not str
                    or entry["anchor_id"] not in anchor_ids or counts[entry["anchor_id"]] != 1):
                rejected["industry_assessment_invalid_union"] += 1
                states.append(entry)
                continue
            states.append({"anchor_id": entry["anchor_id"], "status": status})
            if status == "proposed":
                proposals.append({name: value for name, value in entry.items() if name != "status"})
        item[ic.INDUSTRY_ASSESSMENT_FIELD] = states
        if len(proposals) > ic.INDUSTRY_MAX_PROBLEMS_PER_ARTICLE:
            # 임의로 첫 제안을 채택하거나 기존 기사당 상한을 늘리지 않는다.
            rejected["industry_assessment_proposal_limit"] += 1
        elif not mixed:
            item["industry_problems"] = proposals
    return result, dict(rejected)


def priority_enabled(company: NewsCompanyContext, articles: list[tuple[NewsCandidate, str]]) -> bool:
    # 현재 수집기의 한 묶음·공식 앵커 상한 안에서만 새 출력 계약을 사용한다.
    return (0 < len(company.business_anchors) <= ic.INDUSTRY_ASSESSMENT_MAX_ANCHORS
            and 0 < len(articles) <= c.GROUNDED_BATCH_SIZE
            and any(any(topic.startswith(ic.INDUSTRY_TOPIC_PREFIX) for topic in candidate.topics)
                    for candidate, _ in articles))


def observe_assessments(items: list, *, articles: list[tuple[NewsCandidate, str]],
                       company: NewsCompanyContext, required: bool,
                       records: list[dict[str, str]] | None = None) -> dict[str, int]:
    """미지·누락·중복 상태는 진단만 한다. 인용 승인이나 회사 기사 거절에 쓰지 않는다."""
    rejected = Counter()
    article_ids = {candidate.id for candidate, _ in articles}
    anchor_ids = {anchor.anchor_id for anchor in company.business_anchors}
    counts = Counter(item.get("id") for item in items
                     if type(item) is dict and type(item.get("id")) is str)
    if required:
        rejected["industry_assessment_missing_article"] += len(article_ids - set(counts))
    for item in items:
        if type(item) is not dict:
            continue
        present = ic.INDUSTRY_ASSESSMENT_FIELD in item
        if not required and not present:
            continue
        article_id = item.get("id")
        if type(article_id) is not str or article_id not in article_ids or counts[article_id] != 1:
            rejected["industry_assessment_invalid_article"] += 1
            continue
        entries = item.get(ic.INDUSTRY_ASSESSMENT_FIELD)
        if type(entries) is not list or len(entries) > len(anchor_ids):
            rejected["industry_assessment_invalid_entries"] += 1
            continue
        entry_counts = Counter(entry.get("anchor_id") for entry in entries
                               if type(entry) is dict and type(entry.get("anchor_id")) is str)
        rejected["industry_assessment_missing_anchor"] += len(anchor_ids - set(entry_counts))
        for entry in entries:
            if (type(entry) is not dict or set(entry) != {"anchor_id", "status"}
                    or type(entry.get("anchor_id")) is not str
                    or entry["anchor_id"] not in anchor_ids or entry_counts[entry["anchor_id"]] != 1
                    or type(entry.get("status")) is not str
                    or entry["status"] not in ic.INDUSTRY_ASSESSMENT_STATUSES):
                rejected["industry_assessment_invalid_entry"] += 1
                continue
            if records is not None:
                records.append({"article_id": article_id, **entry})
            problems = item.get("industry_problems")
            proposed = type(problems) is list and any(
                type(problem) is dict and problem.get("anchor_id") == entry["anchor_id"]
                for problem in problems)
            if proposed != (entry["status"] == "proposed"):
                rejected["industry_assessment_proposal_mismatch"] += 1
    return {code: count for code, count in rejected.items() if count}
