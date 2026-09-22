"""동일 입력·현재 원문 검증·프로세스 메모리 경계를 외부 통신 없이 재현한다."""

from __future__ import annotations

import copy
import datetime as dt
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from src.features.news_intake import analysis_result_cache as cache
from src.features.news_intake import analysis_cache_constants as cc
from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.grounded import build_grounded_prompt, build_grounded_schema
from src.features.news_intake.tests.test_collection import (
    AS_OF, BODY, COMPANY, POLICY, accepted, analyzer, item, snapshot,
)
from src.shared.engine_build_identity import EngineBuildIdentity
from src.shared.report_generation.models import exact_text_sha256


NAMESPACE = cache.AnalysisNamespace(
    "claude-haiku-4-5", EngineBuildIdentity("a" * 40, "deployment-commit-v1:" + "a" * 40),
)


def request(*, count=1, body_suffix=""):
    snap, _ = snapshot([item(i) for i in range(count)])
    articles = [(candidate, BODY.replace("120대", f"{120 + i}대") + body_suffix)
                for i, candidate in enumerate(snap.candidates)]
    return cache.AnalysisRequest(
        COMPANY, AS_OF, POLICY, articles,
        {candidate.source_url: exact_text_sha256(body) for candidate, body in articles},
        build_grounded_prompt(COMPANY, articles, AS_OF), build_grounded_schema(articles),
        POLICY.analysis_max_tokens,
    )


def payload(req):
    return {"items": [accepted({"id": candidate.id, "body": body}) for candidate, body in req.articles]}


def execute(store, req, *, namespace=NAMESPACE, output=None, complete=True, calls=None, hits=None):
    def provider():
        if calls is not None:
            calls.append(1)
        return cache.ProviderAnalysis(payload(req) if output is None else output, complete)
    return store.run(req, namespace, provider, lambda: hits.append(1) if hits is not None else None)


def test_cold_warm_equal_deep_copy_and_no_original_input_in_storage():
    store, req, calls, hits = cache.AnalysisResultCache(), request(), [], []
    expected = payload(req)
    first = execute(store, req, output=expected, calls=calls, hits=hits)
    first["items"][0]["excerpts"][0]["text"] = "오염"
    second = execute(store, req, calls=calls, hits=hits)
    assert second == payload(req) and calls == [1] and hits == [1]
    second["items"].clear()
    assert execute(store, req, calls=calls) == payload(req)
    assert calls == [1]
    stored = repr(store._entries)
    assert BODY not in stored and req.prompt not in stored
    assert COMPANY.identity_context not in stored and COMPANY.domain not in stored
    entry = next(iter(store._entries.values()))
    encoded = json.loads(entry.encoded)
    assert encoded["items"][0]["entity_evidence"] == [0, len(BODY)]
    assert encoded["items"][0]["excerpts"][0]["text"] == [0, len(BODY)]


@pytest.mark.parametrize("body_suffix,prefix,suffix", [
    ("", "event: ", ""),
    ("", "", " / event"),
    (" 설비 명칭은 Café·Cafe\u0301·⚙️다.", "«사건» ", " 🚀"),
    ("\n추가 산업설비를 공급했다.", "사건:\n", "\n끝"),
    (' 설비 명칭은 "A\\B"다.', '"사건": ', ""),
])
@pytest.mark.parametrize("body_index", [0, 1], ids=("own-body", "other-batch-body"))
def test_wrapped_full_body_skips_only_storage(body_suffix, prefix, suffix, body_index):
    store, req, calls, hits = cache.AnalysisResultCache(), request(count=2, body_suffix=body_suffix), [], []
    output = payload(req)
    output["items"][0]["excerpts"][0]["event_key"] = prefix + req.articles[body_index][1] + suffix
    original = copy.deepcopy(output)
    assert req.valid(output)
    for attempt in range(2):
        assert execute(store, req, output=output, calls=calls, hits=hits) is output
        assert output == original and len(calls) == attempt + 1
        assert not store._entries and not hits and req.cache_hits == 0


@pytest.mark.parametrize("location", ["root-key", "nested-key", "nested-value"])
def test_storage_guard_checks_additional_nested_keys_and_values(location):
    store, req = cache.AnalysisResultCache(), request(count=2, body_suffix="\n추가 설비를 공급했다.")
    output = payload(req)
    wrapped = "사건:\n" + req.articles[1][1] + "\n끝"
    if location == "root-key":
        output[wrapped] = "추가 metadata"
    elif location == "nested-key":
        output["items"][0]["extra"] = [{"nested": {wrapped: "추가 metadata"}}]
    else:
        output["items"][0]["extra"] = [{"nested": [wrapped]}]
    original = copy.deepcopy(output)
    # 현 검증기는 추가 필드를 거절한다. 저장 경계도 독립적으로 원문을 막아야 한다.
    store._put(req.key(NAMESPACE), req, output)
    assert not store._entries and output == original


def test_event_key_equal_to_body_keeps_span_only_cache_hit():
    store, req, calls, hits = cache.AnalysisResultCache(), request(), [], []
    output = payload(req)
    output["items"][0]["excerpts"][0]["event_key"] = req.articles[0][1]
    assert req.valid(output)
    assert execute(store, req, output=output, calls=calls, hits=hits) == output
    assert execute(store, req, output=output, calls=calls, hits=hits) == output
    assert calls == [1] and hits == [1]
    entry = next(iter(store._entries.values()))
    assert req.articles[0][1].encode("utf-8") not in entry.encoded
    assert json.loads(entry.encoded)["items"][0]["excerpts"][0]["event_key"] == [0, len(BODY)]


@pytest.mark.parametrize("field,value", [
    ("company_name", "다른 가나다전자"), ("aliases", ("다른 별칭",)),
    ("domain", "https://other.example"), ("executive_names", ("김대표",)),
    ("identity_context", "기업용 산업설비 제조와 유통"),
])
def test_all_company_fields_miss(field, value):
    req = request()
    assert replace(req, company=replace(COMPANY, **{field: value})).key(NAMESPACE) != req.key(NAMESPACE)


@pytest.mark.parametrize("field", list(POLICY.__dataclass_fields__))
def test_all_policy_fields_miss(field):
    req = request()
    value = getattr(POLICY, field)
    changed = ("other.example",) if isinstance(value, tuple) else (value - 1 if value > 1 else value + 1)
    assert replace(req, policy=replace(POLICY, **{field: changed})).key(NAMESPACE) != req.key(NAMESPACE)


@pytest.mark.parametrize("field", list(request().articles[0][0].__dataclass_fields__))
def test_all_candidate_metadata_miss_even_if_prompt_unchanged(field):
    req = request()
    candidate, body = req.articles[0]
    value = getattr(candidate, field)
    if field == "priority":
        changed = 1 if value != 1 else 2
    elif field == "published_on":
        changed = "2026-08-31"
    elif isinstance(value, bool):
        changed = not value
    elif isinstance(value, tuple):
        changed = ("strategy",)
    else:
        changed = value + "x"
    updated = replace(candidate, **{field: changed})
    hashes = {updated.source_url: req.full_body_hashes[candidate.source_url]}
    assert replace(req, articles=[(updated, body)], full_body_hashes=hashes).key(NAMESPACE) != req.key(NAMESPACE)


@pytest.mark.parametrize("kind", ["date", "prompt", "schema", "tokens", "tail", "input", "order", "model", "build"])
def test_exact_input_variants_call_provider_again(kind):
    store, req, calls = cache.AnalysisResultCache(), request(count=2), []
    execute(store, req, calls=calls)
    altered, namespace = copy.deepcopy(req), NAMESPACE
    if kind == "date":
        altered.as_of += dt.timedelta(days=1)
    elif kind == "prompt":
        altered.prompt += " "
    elif kind == "schema":
        altered.schema["description"] = "정확한 변경"
    elif kind == "tokens":
        altered.max_tokens -= 1
    elif kind == "tail":
        url = altered.articles[0][0].source_url
        altered.full_body_hashes[url] = exact_text_sha256(BODY + " 잘리기 전 뒤쪽 변경")
    elif kind == "input":
        candidate, body = altered.articles[0]
        altered.articles[0] = (candidate, body + " ")
    elif kind == "order":
        altered.articles.reverse()
    elif kind == "model":
        namespace = replace(namespace, model="claude-sonnet-4-6")
    else:
        namespace = replace(namespace, build=EngineBuildIdentity("b" * 40, "deployment-commit-v1:" + "b" * 40))
    assert altered.key(namespace) != req.key(NAMESPACE)
    execute(store, altered, namespace=namespace, calls=calls)
    assert len(calls) == 2


@pytest.mark.parametrize("namespace", [None, "opaque", replace(NAMESPACE, model=""),
    replace(NAMESPACE, model="unknown"), replace(NAMESPACE, model=" claude-haiku-4-5"),
    replace(NAMESPACE, build=EngineBuildIdentity("", "unknown")), replace(NAMESPACE, build=None)])
def test_ambiguous_namespace_never_reads_or_stores(namespace):
    store, req, calls = cache.AnalysisResultCache(), request(), []
    for _ in range(2):
        execute(store, req, namespace=namespace, calls=calls)
    assert len(calls) == 2 and not store._entries


def test_missing_pre_truncation_hash_never_caches():
    store, req = cache.AnalysisResultCache(), request()
    req.full_body_hashes.clear()
    execute(store, req)
    assert not store._entries


def test_ttl_is_from_write_and_hit_does_not_extend_it():
    now = [1.0]
    store, req, calls = cache.AnalysisResultCache(ttl=5, clock=lambda: now[0]), request(), []
    execute(store, req, calls=calls)
    now[0] = 5.999
    execute(store, req, calls=calls)
    assert len(calls) == 1
    now[0] = 6.0
    execute(store, req, calls=calls)
    assert len(calls) == 2


def test_lru_entry_and_byte_capacity_are_bounded():
    store, req = cache.AnalysisResultCache(max_entries=2), request()
    second, third = replace(req, prompt="second"), replace(req, prompt="third")
    for current in (req, second, req, third):
        execute(store, current)
    assert list(store._entries) == [req.key(NAMESPACE), third.key(NAMESPACE)]
    size = len(next(iter(store._entries.values())).encoded)
    bounded = cache.AnalysisResultCache(max_bytes=size)
    execute(bounded, req)
    execute(bounded, second)
    assert list(bounded._entries) == [second.key(NAMESPACE)]
    too_small = cache.AnalysisResultCache(entry_max_bytes=size - 1)
    execute(too_small, req)
    assert not too_small._entries


@pytest.mark.parametrize("damage", ["checksum", "json", "deep_json", "span", "validation", "expiry", "expiry_extension", "entry"])
def test_corruption_is_a_miss(damage):
    store, req, calls = cache.AnalysisResultCache(), request(), []
    execute(store, req, calls=calls)
    key = req.key(NAMESPACE)
    entry = store._entries[key]
    if damage == "entry":
        store._entries[key] = None
    elif damage == "expiry":
        store._entries[key] = replace(entry, expires_at=float("nan"))
    elif damage == "expiry_extension":
        store._entries[key] = replace(entry, expires_at=entry.expires_at + 1000)
    elif damage == "checksum":
        store._entries[key] = replace(entry, checksum="bad")
    else:
        value = json.loads(entry.encoded)
        if damage == "span":
            value["items"][0]["entity_evidence"] = [-1, 500]
        elif damage == "validation":
            value["items"][0]["excerpts"][0]["claim_slot"] = "invalid"
        encoded = b"broken" if damage == "json" else json.dumps(value).encode()
        if damage == "deep_json":
            encoded = b"[" * 2000 + b"]" * 2000
        store._entries[key] = replace(entry, encoded=encoded,
                                      checksum=cache._entry_checksum(key, entry.expires_at, encoded))
    assert execute(store, req, calls=calls) == payload(req)
    assert len(calls) == 2


def test_entry_from_other_key_is_not_accepted_even_with_valid_content():
    store, req, calls = cache.AnalysisResultCache(), request(), []
    other = replace(req, prompt=req.prompt + " ")
    execute(store, req)
    execute(store, other)
    store._entries[req.key(NAMESPACE)] = store._entries[other.key(NAMESPACE)]
    execute(store, req, calls=calls)
    assert len(calls) == 1


@pytest.mark.parametrize("kind", ["incomplete", "partial", "wrong_company", "bad_quote", "provider_incomplete", "string"])
def test_failed_or_incomplete_analysis_is_never_stored(kind):
    store, req = cache.AnalysisResultCache(), request(count=2)
    output = payload(req)
    if kind == "incomplete":
        output = {"items": []}
    elif kind == "partial":
        output["items"].pop()
    elif kind == "wrong_company":
        output["items"][0]["same_company"] = False
    elif kind == "bad_quote":
        output["items"][0]["excerpts"][0]["text"] = BODY.replace("120", "900")
    elif kind == "string":
        output = json.dumps(output)
    execute(store, req, output=output, complete=kind != "provider_incomplete")
    assert not store._entries


def test_provider_failure_propagates_without_negative_cache():
    store, req = cache.AnalysisResultCache(), request()
    def fail():
        raise TimeoutError("offline failure")
    with pytest.raises(TimeoutError):
        store.run(req, NAMESPACE, fail, lambda: None)
    assert not store._entries
    assert execute(store, req) == payload(req)


def test_current_validation_is_rerun_on_hit(monkeypatch):
    store, req, calls = cache.AnalysisResultCache(), request(), []
    execute(store, req, calls=calls)
    validations = []
    def reject(*args, **kwargs):
        validations.append(1)
        return (), {"grounded_identity_unverified": 1}
    monkeypatch.setattr(cache, "validate_grounded_response", reject)
    execute(store, req, calls=calls)
    assert len(calls) == 2 and len(validations) == 2 and not store._entries


def test_concurrent_cold_work_does_not_lock_provider_or_cross_contaminate():
    store, req = cache.AnalysisResultCache(), request()
    barrier = threading.Barrier(4)
    def run(_):
        def provider():
            barrier.wait(5)
            return cache.ProviderAnalysis(payload(req), True)
        return store.run(copy.deepcopy(req), NAMESPACE, provider, lambda: None)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(run, range(4)))
    assert results == [payload(req)] * 4 and len(store._entries) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: execute(store, copy.deepcopy(req)), range(12)))
    results[0]["items"].clear()
    assert results[1:] == [payload(req)] * 11


@pytest.mark.parametrize("value,expected", [(None, False), ("1", True), ("0", False), ("true", False), ("", False)])
def test_feature_is_default_off_and_explicit_only(monkeypatch, value, expected):
    monkeypatch.delenv(cc.ANALYSIS_CACHE_ENV, raising=False)
    if value is not None:
        monkeypatch.setenv(cc.ANALYSIS_CACHE_ENV, value)
    assert cache.cache_enabled() is expected


@pytest.mark.parametrize("policy", [replace(POLICY, max_analysis_calls=1, batch_size=1),
    replace(POLICY, sufficient_events=1, sufficient_topics=1, batch_size=1),
    replace(POLICY, batch_size=2), replace(POLICY, max_analysis_calls=0)])
def test_full_collection_equal_with_same_search_fetch_selection_and_logical_limits(monkeypatch, policy):
    monkeypatch.setenv(cc.ANALYSIS_CACHE_ENV, "1")
    monkeypatch.setattr(cache, "PROCESS_ANALYSIS_CACHE", cache.AnalysisResultCache())
    calls, searches, fetches, hits, results = [], [], [], [], []
    def analyze(prompt, schema, max_tokens):
        return cache.analyze_with_cache(
            lambda: cache.ProviderAnalysis(analyzer(calls=calls)(prompt, schema, max_tokens), True),
            namespace=NAMESPACE, reserve_hit=lambda: hits.append(1),
        )
    for _ in range(2):
        snap, search_calls = snapshot([item(i) for i in range(5)], policy=policy)
        fetched = []
        def fetch(url):
            fetched.append(url)
            return BODY.replace("120대", f"{120 + int(url.rsplit('/', 1)[-1])}대")
        results.append(collect_from_snapshot(snap, company=COMPANY, as_of=AS_OF, policy=policy,
                                            fetch_text=fetch, analyze_grounded=analyze))
        searches.append(search_calls)
        fetches.append(fetched)
    cold, warm = results
    assert searches[0] == searches[1] and searches[0]
    assert fetches[0] == fetches[1]
    assert cold == replace(warm, diagnostics={**warm.diagnostics,
        "분석캐시적중": 0, "분석캐시보존호출": 0,
        "분석provider호출": cold.diagnostics["분석provider호출"],
        "분석provider미관측": cold.diagnostics["분석provider미관측"]})
    assert len(calls) == cold.diagnostics["분석AI호출"]
    assert len(hits) == warm.diagnostics["분석AI호출"]
    if policy.max_analysis_calls == 0:
        assert not calls and not hits and not fetches[0]


def test_changed_tail_after_truncation_misses_in_actual_collection(monkeypatch):
    monkeypatch.setenv(cc.ANALYSIS_CACHE_ENV, "1")
    monkeypatch.setattr(cache, "PROCESS_ANALYSIS_CACHE", cache.AnalysisResultCache())
    policy = replace(POLICY, max_body_chars=len(BODY))
    calls, prompts = [], []
    def analyze(prompt, schema, max_tokens):
        prompts.append(prompt)
        return cache.analyze_with_cache(
            lambda: cache.ProviderAnalysis(analyzer(calls=calls)(prompt, schema, max_tokens), True),
            namespace=NAMESPACE, reserve_hit=lambda: None,
        )
    results = []
    for tail in (" 첫 번째 뒤쪽", " 바뀐 두 번째 뒤쪽", " 바뀐 두 번째 뒤쪽"):
        snap, _ = snapshot([item()], policy=policy)
        results.append(collect_from_snapshot(snap, company=COMPANY, as_of=AS_OF, policy=policy,
            fetch_text=lambda _: BODY + tail, analyze_grounded=analyze))
    assert prompts[0] == prompts[1] == prompts[2]
    assert len(calls) == 2
    assert results[0].document_hashes != results[1].document_hashes
    assert results[1].document_hashes == results[2].document_hashes


def test_current_collection_deadline_is_not_bypassed_by_warm_cache(monkeypatch):
    from src.features.news_intake import collection
    monkeypatch.setenv(cc.ANALYSIS_CACHE_ENV, "1")
    monkeypatch.setattr(cache, "PROCESS_ANALYSIS_CACHE", cache.AnalysisResultCache())
    calls = []
    def analyze(prompt, schema, max_tokens):
        return cache.analyze_with_cache(
            lambda: cache.ProviderAnalysis(analyzer(calls=calls)(prompt, schema, max_tokens), True),
            namespace=NAMESPACE, reserve_hit=lambda: pytest.fail("만료 후 적중 금지"),
        )
    snap, _ = snapshot([item()])
    collect_from_snapshot(snap, company=COMPANY, as_of=AS_OF, policy=POLICY,
                          fetch_text=lambda _: BODY, analyze_grounded=analyze)
    now = [0.0]
    monkeypatch.setattr(collection.time, "monotonic", lambda: now[0])
    def fetch(_):
        now[0] = POLICY.max_collection_seconds + 1
        return BODY
    result = collect_from_snapshot(snap, company=COMPANY, as_of=AS_OF, policy=POLICY,
                                   fetch_text=fetch, analyze_grounded=analyze)
    assert result.diagnostics["분석캐시적중"] == 0 and len(calls) == 1
    assert not result.fragments


def test_analysis_context_is_reset_after_exception_and_nested_scope():
    from src.shared.news_analysis_port import analyze_with_cache, record_news_provider_calls
    req = request()
    arguments = {name: getattr(req, name) for name in req.__dataclass_fields__
                 if name not in {"cache_hits", "provider_calls"}}
    with cache.analysis_request(**arguments) as outer:
        record_news_provider_calls(1)
        with pytest.raises(RuntimeError):
            with cache.analysis_request(**arguments) as inner:
                record_news_provider_calls(2)
                raise RuntimeError("중첩 문맥 종료")
        record_news_provider_calls(3)
    assert outer.provider_calls == 3 and inner.provider_calls == 2
    assert analyze_with_cache(lambda: cache.ProviderAnalysis("문맥 밖"), namespace=NAMESPACE,
        reserve_hit=lambda: pytest.fail("문맥 밖 적중 금지")) == "문맥 밖"
