"""뉴스 공개 API 진단의 닫힌 집계가 관리자 저장 계약을 통과하는지 검증한다."""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from types import SimpleNamespace

from src.features.news_intake import constants as news_constants
from src.features.news_intake.collection import collect_from_snapshot, collect_search_snapshot
from src.features.news_intake.models import NewsCollectionPolicy, NewsCompanyContext
from src.features.observability import run_diagnostics, run_steps_store


def test_뉴스_주어실패_닫힌집계가_관리자_진단에_왕복한다():
    company = NewsCompanyContext("가상기술")
    policy = NewsCollectionPolicy(trusted_publisher_domains=("media.example",))
    as_of = dt.date(2026, 9, 8)
    body = "가상기술은 산업설비를 제조한다. 이 회사는 신규 공장에 자동화 장치를 공급했다."
    excerpt = "이 회사는 신규 공장에 자동화 장치를 공급했다."
    calls = 0

    def search(_query, **_options):
        nonlocal calls
        calls += 1
        items = [SimpleNamespace(
            title="가상기술 설비 공급", description="가상기술이 산업설비를 공급한다.",
            originallink="https://media.example/article/1", link="", pubDate="2026-09-01",
        )] if calls == 1 else []
        return SimpleNamespace(
            state="success", reason_code="news_search_ok", items=items,
            transport_attempts=1, retry_recovered=False,
            attempt_reason_codes=("news_search_ok",),
        )

    snapshot = collect_search_snapshot(
        search_news=search, company=company, as_of=as_of, policy=policy,
    )

    def analyze(prompt, _schema, _max_tokens):
        article = json.loads(prompt.split("자료 시작:\n", 1)[1])["articles"][0]
        return {"items": [{
            "id": article["id"], "same_company": True, "material": True,
            "entity_evidence": article["body"], "source_type": "news_report",
            "excerpts": [{
                "text": excerpt, "section_id": "operations_partners",
                "claim_slot": "operations_partners:partnership",
                "claim_kind": "reported_fact", "temporal_status": "completed",
                "topic": "operations", "event_key": "설비 공급", "event_on": "",
                "time_evidence": "", "subject": "", "subject_evidence": "",
            }],
        }]}

    result = collect_from_snapshot(
        snapshot, company=company, as_of=as_of, fetch_text=lambda _url: body,
        analyze_grounded=analyze, policy=policy,
    )
    detail = result.diagnostics["주어결속상세"]
    assert detail == {news_constants.SUBJECT_MISSING_OR_GENERIC: 1}
    assert sum(detail.values()) == result.diagnostics["제외"]["grounded_subject_missing"]
    steps = [{"step": "5b_뉴스_수집", "주어결속상세": detail}]

    summary = run_diagnostics.build_summary(steps)
    assert summary["단계"][0]["주어결속상세"] == detail

    with sqlite3.connect(":memory:") as connection:
        assert run_steps_store.record_once(
            connection, run_id="synthetic-news-subject",
            steps=steps, recorded_at="2026-09-27T15:00:00+09:00",
        )
        stored = run_steps_store.load(connection, "synthetic-news-subject")
    assert stored is not None
    assert stored.steps == steps
    assert stored.omitted_count == 0
