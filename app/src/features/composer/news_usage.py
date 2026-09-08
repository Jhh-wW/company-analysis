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
)
from src.features.composer.news_block import _is_news_fragment, news_ownership_from_claim_slots
from src.features.composer.port import ComposedSentence
from src.features.composer.constants import GRADE_CONFIRMED
from src.shared.report_evidence.constants import NEWS_EXCLUDED_SECTION_IDS
from src.shared.report_quality.supplementary_prose import news_number_tokens, NEWS_PROTECTED_SECTIONS


def article_key(fragment) -> str:
    """수집 문서 신원을 먼저 쓰고, 과거 조각은 기사 주소로 묶는다."""
    return fragment.document_identity or fragment.source_url or fragment.fragment_id


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

    작가의 구체적 제외 사유, 이미 사용한 사실, 미검증 수집 조각은 보강하지 않는다.
    반환 후보는 모두 unverified이며 다음 정상 Reviewer를 통과해야 공개할 수 있다.
    """
    ownership = ownership if ownership is not None else news_ownership_from_claim_slots(fragments)
    news = tuple(f for f in fragments if _is_news_fragment(f))
    used = {c for s in report.sections for sentence in s.sentences for c in sentence.citations}
    existing_text = " ".join(sentence.text for s in report.sections for sentence in s.sentences)
    seen_events = {f.news_event_key or " ".join(f.text.split()) for f in news if f.fragment_id in used}
    added = []
    rebuilt = []
    for section in report.sections:
        decisions = {fid for fid, _, _ in section.news_decisions}
        candidates = []
        for fragment in news:
            if (section.section_id in NEWS_EXCLUDED_SECTION_IDS
                or fragment.fragment_id not in ownership.get(section.section_id, ())
                or fragment.fragment_id in used or fragment.fragment_id in decisions
                or not fragment.news_grounded or not fragment.document_date
                or not fragment.source_publisher or not fragment.text.strip()):
                continue
            event = fragment.news_event_key or " ".join(fragment.text.split())
            if event in seen_events or fragment.text.strip() in existing_text:
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
            ))
            added.append(fragment.fragment_id)
            used.add(fragment.fragment_id)
            seen_events.add(event)
        rebuilt.append(replace(section, sentences=section.sentences + tuple(candidates)))
    return replace(report, sections=tuple(rebuilt)), tuple(added)


def retain_verified_news(report, fragments):
    """검수 실패·불명확 뉴스는 해석 등급으로 우회 출고하지 않는다."""
    news = {f.fragment_id: f for f in fragments if _is_news_fragment(f)}
    news_ids = set(news)
    sections = []
    for section in report.sections:
        def publishable(sentence):
            if not news_ids.intersection(sentence.citations):
                return True
            if (sentence.verification_state != "verified" or sentence.grade != GRADE_CONFIRMED
                or section.section_id in NEWS_PROTECTED_SECTIONS
                or not set(sentence.citations).issubset(news_ids)):
                return False
            source = news[sentence.citations[0]]
            prefix = attribution_prefix(source)
            if not source.document_date or not source.source_publisher or not sentence.text.startswith(prefix):
                return False
            return news_number_tokens(sentence.text[len(prefix):]).issubset(
                news_number_tokens(" ".join(news[fid].text for fid in sentence.citations)))

        kept = tuple(sentence for sentence in section.sentences if publishable(sentence))
        sections.append(replace(section, sentences=kept,
            notice=(section.notice or NEWS_NOTICE_REJECTED)
            if section.sentences and not kept else section.notice))
    return replace(report, sections=tuple(sections))


def news_citation_ids(report, fragments):
    """작가 직접 인용과 자동 후보를 구분하지 않고 검수 전 뉴스 인용을 추적한다."""
    news_ids = {fragment.fragment_id for fragment in fragments if _is_news_fragment(fragment)}
    return frozenset(citation for section in report.sections for sentence in section.sentences
                     for citation in sentence.citations if citation in news_ids)


def news_usage_diagnostics(report, fragments, supplemented=(), *, review_candidates=(), today=None):
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
        details.append({"조각": fid, "기사": article_key(fragment), "사유": reason, "설명": explanation})
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


def append_research_notice(report, diagnostics):
    """수집 불능과 적격 자료 부족을 사실 문장이 아닌 공통 확인 범위로 알린다."""
    if diagnostics is None:
        return report
    status = str(diagnostics.get("상태", diagnostics.get("status", "")))
    if diagnostics.get("실패") and status == "ok":
        status = "partial"
    notice = NEWS_RESEARCH_NOTICES.get(status)
    if status == "partial" and diagnostics.get("실패"):
        notice = NEWS_PARTIAL_FAILURE_NOTICE
    if notice is None:
        notice = "확인 범위: 뉴스 조사 완료 상태를 확인하지 못했습니다. 관련 보도가 없다는 뜻은 아닙니다."
    return replace(report, sections=tuple(
        replace(section, notice="\n\n".join(value for value in (section.notice, notice) if value))
        if section.section_id == "identity" and notice not in section.notice else section for section in report.sections
    ))
