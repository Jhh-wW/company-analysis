"""선택적 뉴스 관측은 허용 메타데이터와 선택 지문만 남긴다."""
from types import SimpleNamespace

from src.features.news_intake.collection import collect_from_snapshot, collect_search_snapshot
from src.features.news_intake.tests.test_collection import AS_OF, BODY, COMPANY, POLICY, analyzer, item
from src.shared.report_generation.models import exact_text_sha256


def _snapshot(observer=None):
    calls = []
    row = item()
    row.authorization = "보관하면 안 되는 가짜 인증값"

    def search(query, **options):
        calls.append((query, options))
        return SimpleNamespace(state="success", reason_code="news_search_ok",
                               items=[row] if len(calls) == 1 else [], transport_attempts=1,
                               retry_recovered=False, attempt_reason_codes=("news_search_ok",))

    snapshot = collect_search_snapshot(search_news=search, company=COMPANY, as_of=AS_OF,
                                       policy=POLICY, observer=observer)
    return snapshot, calls


def test_허용검색원행_주제_후보지문과_실제본문선택을_따로_관측한다():
    records = []
    baseline, baseline_calls = _snapshot()
    snapshot, calls = _snapshot(lambda event, payload: records.append((event, payload)))
    assert snapshot == baseline and calls == baseline_calls
    assert records[0][0] == "search_snapshot"
    search = records[0][1]
    assert search["snapshot"]["digest"] == snapshot.digest
    assert search["queries"][0]["attempt"]["topic"] == snapshot.query_attempts[0].topic
    assert search["queries"][0]["parsed_rows"][0]["title"] == item().title
    assert "authorization" not in repr(search)
    assert "가짜 인증값" not in repr(search)
    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF,
                                   fetch_text=lambda _: BODY, analyze_grounded=analyzer(), policy=POLICY,
                                   observer=lambda event, payload: records.append((event, payload)))
    assert result.fragments
    assert [event for event, _ in records] == ["search_snapshot", "body_selection"]
    selected = records[1][1]
    assert selected["snapshot_digest"] == snapshot.digest
    assert selected["attempted_candidate_ids"] == [snapshot.candidates[0].id]
    assert selected["rankings"][0]["ranked_candidate_ids"][0] == snapshot.candidates[0].id
    assert exact_text_sha256(BODY) in selected["document_content_sha256"].values()
    assert BODY not in repr(selected)


def test_관측실패는_검색_본문_검증결과와_호출수에_영향을_주지_않는다():
    def fail(*_):
        raise RuntimeError("보관 실패")

    baseline, baseline_calls = _snapshot()
    snapshot, calls = _snapshot(fail)
    assert snapshot == baseline and calls == baseline_calls
    options = dict(company=COMPANY, as_of=AS_OF, fetch_text=lambda _: BODY,
                   analyze_grounded=analyzer(), policy=POLICY)
    result = collect_from_snapshot(snapshot, **options, observer=fail)
    expected = collect_from_snapshot(baseline, **options)
    assert result == expected


def test_관측용_메타구성_실패도_수집에_영향없이_불완전을_기록한다(monkeypatch):
    from src.features.news_intake import search_snapshot
    baseline, baseline_calls = _snapshot()
    original = search_snapshot.asdict
    calls = 0

    def fail_first(value):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise TypeError("합성 관측 복사 실패")
        return original(value)

    monkeypatch.setattr(search_snapshot, "asdict", fail_first)
    records = []
    snapshot, search_calls = _snapshot(lambda event, payload: records.append(payload))
    assert snapshot == baseline and search_calls == baseline_calls
    assert records[0]["query_capture_failures"] == 1
