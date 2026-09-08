"""뉴스 본문 활용, 1회 후보 보강 및 기사 단위 진단. 검수 승인 자체를 만들지 않는다."""

from __future__ import annotations

import json
import calendar
from collections import Counter
from dataclasses import replace
from datetime import date

from src.features.composer.news_constants import (
    NEWS_ATTRIBUTION_TEMPLATE, NEWS_DECISIONS_KEY, NEWS_PERIOD_MONTHS,
    NEWS_VALID_EXCLUSION_REASONS,
    NEWS_RESEARCH_NOTICES,
    NEWS_NOTICE_REJECTED,
    NEWS_PARTIAL_FAILURE_NOTICE,
    NEWS_REJECTION_REASONS,
    NEWS_RESEARCH_DETAIL_PREFIX,
    NEWS_RESEARCH_DETAIL_NOTICES,
    NEWS_RESEARCH_SEARCH_COMPLETE_NOTICE,
    NEWS_RESEARCH_LIMIT_NOTICE,
    NEWS_RESEARCH_ABSENCE_NOTICE,
    NEWS_RESEARCH_UNKNOWN_NOTICE,
    NEWS_BODY_REJECTION_NOTICE,
)
from src.features.composer.news_block import _is_news_fragment, news_ownership_from_claim_slots
from src.features.composer.port import ComposedSentence
from src.features.composer.constants import GRADE_CONFIRMED
from src.shared.report_evidence.constants import NEWS_EXCLUDED_SECTION_IDS
from src.shared.report_quality.supplementary_prose import news_number_tokens, NEWS_PROTECTED_SECTIONS
from src.shared.report_generation.models import exact_text_sha256


def article_key(fragment) -> str:
    """수집 문서 신원을 먼저 쓰고, 과거 조각은 기사 주소로 묶는다."""
    return fragment.document_identity or fragment.source_url or fragment.fragment_id


def _original_claim_key(fragment):
    """조각 번호가 달라도 같은 기사의 같은 원문 사실은 한 후보로 취급한다."""
    return article_key(fragment), " ".join(fragment.text.split())


def news_metadata(fragment) -> str:
    """AI 지시와 구분되는 메타데이터 JSON."""
    return json.dumps({
        "자료종류": "보조 언론 보도", "기사날짜": fragment.document_date,
        "발행처": fragment.source_publisher,
        "보도종류": fragment.news_claim_kind or "언론 보도",
        "발행처종류": fragment.news_source_category,
        "사건시점": fragment.news_event_on, "시간상태": fragment.news_temporal_status,
    }, ensure_ascii=False)


def attribution_prefix(fragment) -> str:
    return NEWS_ATTRIBUTION_TEMPLATE.format(date=fragment.document_date, publisher=fragment.source_publisher)


def parse_news_decisions(payload) -> tuple[tuple[str, str, str], ...]:
    """작가의 구체적인 제외 사유를 보존한다. 누락·형식 오류는 제외 승인으로 보지 않는다."""
    if not isinstance(payload, dict):
        return ()
    entries = payload.get(NEWS_DECISIONS_KEY, ())
    if not isinstance(entries, list):
        return ()
    decisions = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        fragment_id = str(entry.get("조각") or "").strip()
        reason = str(entry.get("사유") or "").strip()
        explanation = str(entry.get("설명") or "").strip()
        if fragment_id and reason in NEWS_VALID_EXCLUSION_REASONS and explanation:
            decisions[fragment_id] = (fragment_id, reason, explanation)
    return tuple(decisions.values())


def supplement_news_candidates(report, fragments, ownership=None):
    """기존 검수 전 한 번만, 검증된 유용 근거의 미결정 누락을 원문 후보로 보강한다.

    작가의 구체적 제외 사유, 이미 원문으로 사용한 사실, 미검증 조각은 보강하지 않는다.
    작가가 변형한 뉴스에는 같은 검수 회차에서 판정할 정확 원문 대체 후보를 둔다.
    반환 후보는 모두 unverified이며 다음 정상 Reviewer를 통과해야 공개할 수 있다.
    """
    ownership = ownership if ownership is not None else news_ownership_from_claim_slots(fragments)
    news = tuple(f for f in fragments if _is_news_fragment(f))
    news_by_id = {fragment.fragment_id: fragment for fragment in news}
    # 출처 표기는 모델의 문자열 형식 준수에 맡기지 않는다. 같은 기사만 인용한
    # 후보에 확인된 메타데이터를 붙이며 문장 의미·등급·검수 상태는 바꾸지 않는다.
    report = replace(report, sections=tuple(replace(section, sentences=tuple(
        _attribute_news_candidate(sentence, news_by_id) for sentence in section.sentences
    )) for section in report.sections))
    used = {c for s in report.sections for sentence in s.sentences for c in sentence.citations}
    existing_news_sentences = {(sentence.text.strip(), frozenset(sentence.citations))
                              for section in report.sections for sentence in section.sentences}
    original_claims = {_original_claim_key(fragment) for fragment in news
                       if (attribution_prefix(fragment) + fragment.text.strip(),
                           frozenset({fragment.fragment_id})) in existing_news_sentences}
    seen_events = {f.news_event_key or " ".join(f.text.split()) for f in news if f.fragment_id in used}
    added = []
    rebuilt = []
    for section in report.sections:
        decisions = {fid for fid, _, _ in section.news_decisions}
        candidates = []
        for fragment in news:
            if (section.section_id in NEWS_EXCLUDED_SECTION_IDS
                or fragment.fragment_id not in ownership.get(section.section_id, ())
                or fragment.fragment_id in decisions
                or not fragment.news_grounded or not fragment.document_date
                or not fragment.source_publisher or not fragment.text.strip()):
                continue
            event = fragment.news_event_key or " ".join(fragment.text.split())
            canonical_candidate = (attribution_prefix(fragment) + fragment.text.strip(),
                                   frozenset({fragment.fragment_id}))
            if ((event in seen_events and fragment.fragment_id not in used)
                    or canonical_candidate in existing_news_sentences
                    or _original_claim_key(fragment) in original_claims):
                continue
            slots = tuple(slot for slot in fragment.supported_claim_slots if slot.startswith(section.section_id + ":"))
            if not slots:
                continue
            # 법인 정체 슬롯의 공식 사실을 뉴스로 대체하지 않는다.
            if section.section_id == "identity":
                continue
            candidates.append(ComposedSentence(
                text=attribution_prefix(fragment) + fragment.text.strip(),
                citations=(fragment.fragment_id,), grade=GRADE_CONFIRMED, planned_claim_slot=slots[0],
                news_source_alternative=True,
            ))
            added.append(fragment.fragment_id)
            used.add(fragment.fragment_id)
            seen_events.add(event)
            original_claims.add(_original_claim_key(fragment))
        rebuilt.append(replace(section, sentences=section.sentences + tuple(candidates)))
    return replace(report, sections=tuple(rebuilt)), tuple(added)


def _attribute_news_candidate(sentence, news):
    if not sentence.citations or not set(sentence.citations).issubset(news):
        return sentence
    cited = tuple(news[fid] for fid in sentence.citations)
    if (len({article_key(fragment) for fragment in cited}) != 1
            or any(not fragment.document_date or not fragment.source_publisher for fragment in cited)):
        return sentence
    prefix = attribution_prefix(cited[0])
    return sentence if sentence.text.startswith(prefix) else replace(sentence, text=prefix + sentence.text)


def retain_verified_news(report, fragments, *, review_input=None, diagnostics=None):
    """검수 실패·불명확 뉴스는 해석 등급으로 우회 출고하지 않는다."""
    news = {f.fragment_id: f for f in fragments if _is_news_fragment(f)}
    news_ids = set(news)
    def record(sentence, section_id, stage, reason):
        if diagnostics is None:
            return
        for fid in sorted(news_ids.intersection(sentence.citations)):
            diagnostics.append({
                "조각": fid, "장": section_id, "단계": stage, "사유코드": reason,
                "설명": NEWS_REJECTION_REASONS[reason],
                "후보지문": exact_text_sha256(sentence.text),
                "원문대체후보": sentence.news_source_alternative,
            })
    if review_input is not None:
        reviewed = {(section.section_id, sentence.text, sentence.citations)
                    for section in report.sections for sentence in section.sentences}
        for section in review_input.sections:
            for sentence in section.sentences:
                if (section.section_id, sentence.text, sentence.citations) not in reviewed:
                    record(sentence, section.section_id, "본문검수", "review_removed")
    sections = []
    for section in report.sections:
        def publishable(sentence):
            if not news_ids.intersection(sentence.citations):
                return True
            if sentence.verification_state != "verified" or sentence.grade != GRADE_CONFIRMED:
                record(sentence, section.section_id, "게시조건", "not_verified")
                return False
            if section.section_id in NEWS_PROTECTED_SECTIONS:
                record(sentence, section.section_id, "게시조건", "protected_section")
                return False
            if not set(sentence.citations).issubset(news_ids):
                record(sentence, section.section_id, "게시조건", "mixed_sources")
                return False
            source = news[sentence.citations[0]]
            prefix = attribution_prefix(source)
            if not source.document_date or not source.source_publisher or not sentence.text.startswith(prefix):
                record(sentence, section.section_id, "게시조건", "attribution_invalid")
                return False
            supported = news_number_tokens(sentence.text[len(prefix):]).issubset(
                news_number_tokens(" ".join(news[fid].text for fid in sentence.citations)))
            if not supported:
                record(sentence, section.section_id, "게시조건", "unsupported_number")
            return supported

        passed = tuple(sentence for sentence in section.sentences if publishable(sentence))
        writer_news = {fid for sentence in passed if not sentence.news_source_alternative
                       for fid in sentence.citations if fid in news_ids}
        writer_claims = {_original_claim_key(news[fid]) for fid in writer_news}
        # 작가 문장이 통과하면 같은 근거의 대체 후보는 중복 공개하지 않는다.
        # 작가 문장이 탈락해도 대체 후보가 독립적으로 verified일 때만 남는다.
        kept_sentences = []
        alternative_claims = set()
        for sentence in passed:
            cited_claims = {_original_claim_key(news[fid]) for fid in sentence.citations
                            if fid in news_ids}
            if sentence.news_source_alternative:
                if cited_claims.intersection(writer_claims | alternative_claims):
                    record(sentence, section.section_id, "중복정리", "duplicate_source")
                    continue
                alternative_claims.update(cited_claims)
            kept_sentences.append(sentence)
        kept = tuple(kept_sentences)
        sections.append(replace(section, sentences=kept,
            notice=(section.notice or NEWS_NOTICE_REJECTED)
            if section.sentences and not kept else section.notice))
    return replace(report, sections=tuple(sections))


def news_citation_ids(report, fragments):
    """작가 직접 인용과 자동 후보를 구분하지 않고 검수 전 뉴스 인용을 추적한다."""
    news_ids = {fragment.fragment_id for fragment in fragments if _is_news_fragment(fragment)}
    return frozenset(citation for section in report.sections for sentence in section.sentences
                     for citation in sentence.citations if citation in news_ids)


def news_usage_diagnostics(report, fragments, supplemented=(), *, review_candidates=(), today=None,
                           review_rejections=()):
    """본문 사용·목록·사건·기간은 관측값이다. 완성 판정 할당량으로 사용하지 않는다."""
    today = today or date.today()
    news = {f.fragment_id: f for f in fragments if _is_news_fragment(f)}
    body_ids = {c for s in report.sections for sentence in s.sentences for c in sentence.citations if c in news}
    list_ids = {c for s in report.sections for row in s.news_rows for c in row.citations if c in news}
    decisions = {fid: (reason, explanation) for s in report.sections for fid, reason, explanation in s.news_decisions}
    details = []
    for fid, fragment in news.items():
        if fid in body_ids:
            reason, explanation = "본문반영", "날짜·발행처를 포함한 본문 후보가 검수를 통과했습니다."
        elif fid in review_candidates or fid in supplemented:
            reason, explanation = "검수후미반영", "본문 후보가 검수 또는 최종 공개 안전 기준을 통과하지 못해 본문과 목록에서 제외했습니다."
        elif fid in decisions:
            reason, explanation = decisions[fid]
        elif not fragment.news_grounded:
            reason, explanation = "본문검증미확인", "법인·사업사실·원문·시점 검증 통과 표시가 없어 자동 본문 보강하지 않았습니다."
        else:
            reason, explanation = "본문활용미해결", "본문 활용 또는 구체적 제외 사유를 확인하지 못했습니다."
        details.append({"조각": fid, "기사": article_key(fragment), "사유": reason, "설명": explanation,
                        "검증경과": [row for row in review_rejections if row["조각"] == fid]})
    article_dates = {article_key(f): f.document_date for f in news.values()}
    periods = Counter()
    for value in article_dates.values():
        try:
            published = date.fromisoformat(value)
            bucket = "기간외"
            for limit in NEWS_PERIOD_MONTHS:
                month_index = today.year * 12 + today.month - 1 - limit
                year, month_zero = divmod(month_index, 12)
                month = month_zero + 1
                cutoff = date(year, month, min(today.day, calendar.monthrange(year, month)[1]))
                if cutoff <= published <= today:
                    bucket = f"{limit}개월이내"
                    break
        except ValueError:
            bucket = "시점미확인"
        periods[bucket] += 1
    return {
        "수집기사수": len(article_dates), "본문사용기사수": len({article_key(news[c]) for c in body_ids}),
        "목록기사수": len({article_key(news[c]) for c in list_ids}),
        "실질사건수": len({f.news_event_key for f in news.values() if f.news_event_key}),
        "보강후보수": len(supplemented), "기간별기사수": dict(periods), "근거별판정": details,
        "본문상태": "반영확인" if body_ids else ("본문미반영_사유확인필요" if news else "뉴스근거없음"),
    }


def append_research_notice(report, diagnostics, *, fragments=(), review_rejections=()):
    """수집 불능과 적격 자료 부족을 사실 문장이 아닌 공통 확인 범위로 알린다."""
    notice = ""
    if diagnostics is not None:
        status = str(diagnostics.get("상태", diagnostics.get("status", "")))
        if diagnostics.get("실패") and status == "ok":
            status = "partial"
        notice = NEWS_RESEARCH_NOTICES.get(status)
        if status == "partial" and diagnostics.get("실패"):
            notice = NEWS_PARTIAL_FAILURE_NOTICE
        if notice is None:
            notice = NEWS_RESEARCH_UNKNOWN_NOTICE
        issues = diagnostics.get("관측문제", ())
        issues = issues if isinstance(issues, (list, tuple)) else ()
        details = [NEWS_RESEARCH_DETAIL_NOTICES[issue] for issue in issues
                   if isinstance(issue, str) and issue in NEWS_RESEARCH_DETAIL_NOTICES]
        if diagnostics.get("상한도달"):
            details.append(NEWS_RESEARCH_LIMIT_NOTICE)
        if details and status not in {"disabled", "unconfigured"}:
            if diagnostics.get("검색완료") and "검색" not in issues:
                details.insert(0, NEWS_RESEARCH_SEARCH_COMPLETE_NOTICE)
            notice = NEWS_RESEARCH_DETAIL_PREFIX + " ".join((*details, NEWS_RESEARCH_ABSENCE_NOTICE))

    news = {fragment.fragment_id: fragment for fragment in fragments if _is_news_fragment(fragment)}
    rebuilt = []
    for section in report.sections:
        # 보충 검수 뒤 안내도 갱신하되, 다른 장의 실제 사실·인용·사용자 안내는 보존한다.
        research_owner = section.section_id == "identity" and diagnostics is not None
        managed_notices = {NEWS_BODY_REJECTION_NOTICE}
        if research_owner:
            managed_notices.update((*NEWS_RESEARCH_NOTICES.values(),
                                    NEWS_PARTIAL_FAILURE_NOTICE, NEWS_RESEARCH_UNKNOWN_NOTICE))
        paragraphs = [part for part in section.notice.split("\n\n") if part
                      and part not in managed_notices
                      and not (research_owner and part.startswith(NEWS_RESEARCH_DETAIL_PREFIX))]
        used_claims = {_original_claim_key(news[fid]) for sentence in section.sentences
                       for fid in sentence.citations if fid in news}
        rejected = any(
            row.get("장") == section.section_id and row.get("조각") in news
            and row.get("사유코드") != "duplicate_source"
            and _original_claim_key(news[row["조각"]]) not in used_claims
            for row in review_rejections
        )
        if section.section_id == "identity" and notice:
            paragraphs.append(notice)
        if rejected:
            paragraphs.append(NEWS_BODY_REJECTION_NOTICE)
        rebuilt.append(replace(section, notice="\n\n".join(paragraphs)))
    return replace(report, sections=tuple(rebuilt))
