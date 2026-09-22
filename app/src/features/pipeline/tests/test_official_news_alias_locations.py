"""공개 뉴스룸 HTML → 현행 생산기 → typed 계약 → 뉴스 약칭 결합 회귀."""

from dataclasses import asdict, replace
from pathlib import Path

import pytest

from src.features.chapter_evidence.normalize import to_fragment
from src.features.chapter_evidence.produce import produce_from_collection_envelopes
from src.features.homepage.wide_evidence_mapping import to_evidence_mappings
from src.features.homepage.wide_extract import extract_usable_ranges
from src.features.homepage.wide_fragments import build_fragments, extract_list_items
from src.features.homepage.wide_types import WideCollectionResult, WideDocumentIdentity
from src.features.pipeline.official_news_aliases import official_news_aliases
from src.features.pipeline.tests.test_official_news_aliases import (
    CORP_ID, DEFINITION, OFFICIAL_URL, PROFILE, PUBLIC_TEXT, branch, evidence, sha,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.web.official_evidence_adapter import _classified_evidence_location_bindings


def collected_web(raw, url=OFFICIAL_URL):
    """본문·위치를 직접 꾸미지 않고 운영 생산기와 변환 경계를 사용한다.

    HTML은 공개 자료이고 법인 영수증은 기존 시험용 대역이다. 운영 당시의
    typed 수집 객체나 모델 원응답을 보유했다는 의미는 아니다.
    """
    ranges, title = extract_usable_ranges(raw)
    items = extract_list_items(raw, url)
    if items:
        ranges = tuple(item[0] for item in items) + tuple(
            text for text in ranges if not any(text in item[0] for item in items)
        )
    fields = asdict(evidence().candidates[0].documents[0])
    fields.pop("exact_evidence_hashes")
    fields.update(canonical_url=url, title=title or "공식 본문", usable_ranges=ranges,
                  content_sha256=sha("\n".join(ranges)), list_items=items)
    document = WideDocumentIdentity(**fields)
    envelope = to_evidence_mappings(
        result=WideCollectionResult(company_id=CORP_ID, documents=(document,), attempts=()),
        fragments=build_fragments(document, company_id=CORP_ID),
    )
    _classified_evidence_location_bindings(envelope, company_id=CORP_ID)
    result = OfficialEvidenceCollectionResult(
        company_id=CORP_ID,
        candidates=produce_from_collection_envelopes(
            company_id=CORP_ID, company_type="audit_only", collection_envelopes=(envelope,),
        ),
    )
    return envelope, result


def newsroom_html():
    return (Path(__file__).parents[2] / "homepage/tests/fixtures/wrtn_news_list.html").read_text(encoding="utf-8")


@pytest.mark.parametrize("is_list", [False, True])
def test_current_web_producer_locations_reach_alias_and_news_request(is_list):
    raw = newsroom_html() if is_list else f"<main><p>{PUBLIC_TEXT}</p></main>"
    envelope, result = collected_web(raw)
    raw_by_id = {row["fragment_id"]: row for row in envelope["fragments"]}
    fragments = [fragment for candidate in result.candidates for fragment in candidate.fragments]
    assert fragments
    aliases = official_news_aliases(PROFILE, result)
    assert len(aliases) == 1
    proof = aliases[0]
    assert proof.alias == "뤼튼" and proof.definition_text == DEFINITION
    assert proof.location == raw_by_id[proof.fragment_id]["location"]
    assert proof.range_index == raw_by_id[proof.fragment_id]["range_index"]
    assert "#" not in proof.location
    for fragment in fragments:
        assert fragment.range_index == raw_by_id[fragment.fragment_id]["range_index"]
        assert fragment.location == raw_by_id[fragment.fragment_id]["location"]
        assert fragment.text_sha256 == sha(fragment.text)
    outcome, _, _ = branch(result)
    assert outcome.value.session.company.aliases == ("뤼튼",)
    assert outcome.value.session.policy.max_analysis_calls == 3
    assert outcome.steps[-1]["공식약칭근거"][0]["source_snapshot_sha256"] == result.source_snapshot_sha256


@pytest.mark.parametrize("index", [True, False, -2, "0", 0.0, None])
def test_invalid_range_index_is_rejected_by_typed_conversion(index):
    row = asdict(evidence().candidates[0].fragments[0])
    row["range_index"] = index
    with pytest.raises(ValueError):
        to_fragment(row)


def alter_fragments(result, **changes):
    return replace(result, candidates=tuple(
        replace(candidate, fragments=tuple(replace(fragment, **changes) for fragment in candidate.fragments))
        for candidate in result.candidates
    ))


@pytest.mark.parametrize("changes", [
    {"range_index": -1}, {"range_index": 99},
    {"location": "https://other.example/ · 목록 1번째 항목"},
    {"location": OFFICIAL_URL + " · 목록 2번째 항목"},
    {"location": OFFICIAL_URL + "#0"},
    {"item_url": "https://other.example/news/item"},
])
def test_recomputed_snapshot_cannot_hide_wrong_current_location(changes):
    _, result = collected_web(f"<main><p>{PUBLIC_TEXT}</p></main>")
    assert official_news_aliases(PROFILE, alter_fragments(result, **changes)) == ()


def test_list_url_must_stay_on_document_origin_and_match_location():
    _, result = collected_web(newsroom_html())
    for changes in (
        {"item_url": "https://other.example/news/item"},
        {"item_url": "https://other.example/news/item", "location": "https://other.example/news/item"},
        {"item_url": "", "location": OFFICIAL_URL},
    ):
        assert official_news_aliases(PROFILE, alter_fragments(result, **changes)) == ()
    assert official_news_aliases({**PROFILE, "corp_code": "00999999"}, result) == ()


def test_in_bounds_index_cannot_point_to_a_different_length_range():
    _, result = collected_web(f"<main><p>{'가' * 60}</p><p>{PUBLIC_TEXT}</p></main>")
    assert official_news_aliases(PROFILE, result)[0].range_index == 1
    changed = alter_fragments(result, range_index=0, location=OFFICIAL_URL + " · 목록 1번째 항목")
    assert official_news_aliases(PROFILE, changed) == ()


def test_legacy_mapping_defaults_and_snapshot_bind_new_location_fields():
    result = evidence()
    # fb6cc61에서 얻은 구형 #0 fixture 지문: 새 필드 기본값은 이 캐시를 바꾸지 않는다.
    assert result.source_snapshot_sha256 == "c133914793d502568b6ca16a9d9f04e9fa8466273f9033ee2a45b155c7fbe0c3"
    fragment = result.candidates[0].fragments[0]
    row = asdict(fragment)
    row.pop("range_index", None)
    assert to_fragment(row).range_index == -1
    assert official_news_aliases(PROFILE, result)[0].alias == "뤼튼"
    for changes in ({"range_index": 0}, {"item_url": OFFICIAL_URL + "item/"}):
        assert alter_fragments(result, **changes).source_snapshot_sha256 != result.source_snapshot_sha256


@pytest.mark.parametrize("field,value", [("range_index", True), ("range_index", -2),
                                         ("range_index", 99), ("item_url", OFFICIAL_URL + "changed/")])
def test_mutated_location_metadata_cannot_reuse_original_snapshot(field, value):
    _, result = collected_web(newsroom_html())
    for candidate in result.candidates:
        for fragment in candidate.fragments:
            object.__setattr__(fragment, field, value)
    assert official_news_aliases(PROFILE, result) == ()


def test_missing_definition_and_mutated_text_do_not_create_alias():
    _, missing = collected_web("<main><p>뤼튼테크놀로지스는 인공지능 플랫폼을 운영하며 기업용 소프트웨어 서비스를 제공한다.</p></main>")
    assert official_news_aliases(PROFILE, missing) == ()
    _, result = collected_web(newsroom_html())
    for candidate in result.candidates:
        for fragment in candidate.fragments:
            object.__setattr__(fragment, "text", PUBLIC_TEXT + "변경")
    assert official_news_aliases(PROFILE, result) == ()
