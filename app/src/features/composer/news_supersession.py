"""사건 키가 아닌 동일한 원문 행동의 명시적 완료만 계획 대체로 인정한다.

이 함수의 거짓은 계획이 미실행이라는 뜻이 아니다. 좁은 결정론 규칙으로
대체를 증명하지 못했으므로 기존 내용을 보존한다는 뜻이다. 호출자는 완료
원문을 인용한 검증 문장이 실제 공개 본문에 남는지도 별도로 확인해야 한다.
"""

from __future__ import annotations

from datetime import date

from src.features.composer.news_supersession_constants import (
    NEWS_SUPERSESSION_COMPLETED_KINDS,
    NEWS_SUPERSESSION_CONTENT_HASH_RE,
    NEWS_SUPERSESSION_COMPOUND_RE,
    NEWS_SUPERSESSION_DONE_RE,
    NEWS_SUPERSESSION_FRAGMENT_KINDS,
    NEWS_SUPERSESSION_LOCAL_TIME_RE,
    NEWS_SUPERSESSION_PLAN_RE,
    NEWS_SUPERSESSION_SENTENCE_RE,
    NEWS_SUPERSESSION_SOURCE_CATEGORIES,
    NEWS_SUPERSESSION_SUBJECT_RE,
    NEWS_SUPERSESSION_UNCERTAIN_RE,
)
from src.features.composer.port import CollectedFragment
from src.shared.report_evidence.constants import SOURCE_KIND_NEWS
from src.shared.report_evidence.transport_kind import is_typed_transport_kind


def _date(raw: str) -> date | None:
    try:
        parsed = date.fromisoformat(raw)
    except (ValueError, TypeError):
        return None
    return parsed if parsed.isoformat() == raw else None


def _grounded_news(fragment: CollectedFragment) -> bool:
    return bool(
        fragment.news_grounded is True
        and (fragment.kind in NEWS_SUPERSESSION_FRAGMENT_KINDS
             or is_typed_transport_kind(fragment.kind))
        and fragment.formal_source_kind == SOURCE_KIND_NEWS
        and fragment.news_source_category in NEWS_SUPERSESSION_SOURCE_CATEGORIES
        and fragment.source_url and fragment.source_publisher
        and fragment.document_identity
        and NEWS_SUPERSESSION_CONTENT_HASH_RE.fullmatch(fragment.document_content_sha256)
        and fragment.text.strip()
    )


def _action_key(sentence: str, *, planned: bool) -> tuple[str, str] | None:
    # 공백 폭만 통일한다. 공백 삭제·대소문자 통일·인용부호 제거는 하지 않는다.
    surface = " ".join(sentence.split())
    pattern = NEWS_SUPERSESSION_PLAN_RE if planned else NEWS_SUPERSESSION_DONE_RE
    match = pattern.fullmatch(surface)
    if match is None:
        return None
    core = match.group("core").strip()
    if (not NEWS_SUPERSESSION_SUBJECT_RE.match(core)
            or NEWS_SUPERSESSION_COMPOUND_RE.search(core + " ")):
        return None
    core = NEWS_SUPERSESSION_LOCAL_TIME_RE.sub(r"\g<subject> ", core, count=1)
    return core, match.group("action")


def news_plan_is_explicitly_completed(
    planned: CollectedFragment, completed: CollectedFragment,
) -> bool:
    """모든 계획 문장과 같은 주체·대상·조건·행동의 완료 문장이 있을 때만 참.

    발표·공개의 좁은 시제 종결형만 대조한다. 수치, 주체, 부정, 조건, 다른
    행동은 지우지 않는다. 복합 행동·다른 언어·기한을 날짜 계산으로 해석하지
    않고 거짓을 반환한다. 같은 사건 키는 검색 범위일 뿐 동일 행동의 증거가 아니다.
    """
    if not _grounded_news(planned) or not _grounded_news(completed):
        return False
    if (planned.news_claim_kind != "company_plan"
            or planned.news_temporal_status != "planned"
            or completed.news_claim_kind not in NEWS_SUPERSESSION_COMPLETED_KINDS
            or completed.news_temporal_status != "completed"):
        return False
    event_key = planned.news_event_key.strip()
    if not event_key or event_key != completed.news_event_key.strip():
        return False
    planned_published = _date(planned.document_date)
    completed_published = _date(completed.document_date)
    if (planned_published is None or completed_published is None
            or completed_published < planned_published):
        return False
    planned_event = _date(planned.news_event_on) if planned.news_event_on else planned_published
    completed_event = _date(completed.news_event_on) if completed.news_event_on else completed_published
    if planned_event is None or completed_event is None or completed_event < planned_event:
        return False
    if NEWS_SUPERSESSION_UNCERTAIN_RE.search(completed.text):
        return False
    plan_sentences = tuple(part.strip() for part in NEWS_SUPERSESSION_SENTENCE_RE.split(planned.text)
                           if part.strip())
    done_sentences = tuple(part.strip() for part in NEWS_SUPERSESSION_SENTENCE_RE.split(completed.text)
                           if part.strip())
    if not plan_sentences or not done_sentences:
        return False
    plan_keys = tuple(_action_key(sentence, planned=True) for sentence in plan_sentences)
    # 계획 원문의 설명·별도 행동을 조용히 버려 마지막 예정 문장만 대체하지 않는다.
    if any(key is None for key in plan_keys):
        return False
    done_keys = tuple(_action_key(sentence, planned=False) for sentence in done_sentences)
    # 뒤 문장의 정정·의문을 해석하지 못한 채 앞 완료 문장만 골라 쓰지 않는다.
    # 추가 문장도 같은 좁은 완료 문법이어야 한다(보조 안내서 게시까지 허용).
    if any(key is None for key in done_keys):
        return False
    # 다른 완료 문장이 추가돼도 모든 계획 문장이 순서대로 각각 대체돼야 한다.
    cursor = 0
    for plan_key in plan_keys:
        while cursor < len(done_keys) and done_keys[cursor] != plan_key:
            cursor += 1
        if cursor == len(done_keys):
            return False
        cursor += 1
    return True
