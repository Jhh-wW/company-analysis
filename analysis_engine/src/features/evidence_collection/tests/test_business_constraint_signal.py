"""원문 사업제약→동일 주체 현재 대응의 수집 후보 경계."""
import hashlib

import pytest

from features.evidence_collection import constants as c
from features.evidence_collection.business_constraint_signal import business_constraint_signals
from features.evidence_collection.collect import collect_dart_evidence
from features.evidence_collection import collect as collect_module
from features.evidence_collection.filing_select import DocumentFetchResult, FilingListResult, RawFilingRow
from features.evidence_collection.retention import CandidateRetention
from features.evidence_collection.relevance import SlotScore, business_constraint_scores
from features.evidence_collection.segment import FragmentCandidate
from features.evidence_collection.source_context import heading_source_scopes, context_for_candidate
from features.evidence_collection.tests.fixtures.fake_fetcher import FakeFetcher


SIMPLE = "3) 판매전략당사는 기존 제품의 성장에 한계가 있다고 판단하여 해외시장 개척에 주력하여 성장 기반을 구축하고 있습니다."
PROTO = ("(3) 신규사업 등의 내용 및 전망당사는 새로운 사업을 추진하고 있습니다. "
         "우선 기존 소형 제품의 성장에 한계를 느껴, 대형 제품에 대한 진출을 추진하고 있습니다. "
         "향후 인수 및 협업으로 생산능력을 확보할 계획입니다.")


@pytest.mark.parametrize("source", [SIMPLE, PROTO,
    SIMPLE.replace("기존 제품", "국내외 고객 거래선"),
    SIMPLE.replace("당사는", "연결회사는"),
])
def test_direct_constraint_and_current_response_keep_exact_range(source):
    signal, = business_constraint_signals(source)
    assert source[signal.start:signal.end] == source
    assert "성장" in source[signal.issue_start:signal.issue_end]
    assert "있습니다" in source[signal.response_start:signal.response_end]
    assert "계획" not in source[signal.response_start:signal.response_end]
    assert {score.slot_id for score in business_constraint_scores()} == {
        "current_challenges:issue", "current_challenges:response"}
    assert all(score.score_millis == c.RELEVANCE_KEYWORD_HIT_SCORE_MILLIS
               for score in business_constraint_scores())


@pytest.mark.parametrize("source", [
    SIMPLE.replace("당사는", "산업은"),
    SIMPLE.replace("당사는", "경쟁사는"),
    SIMPLE.replace("기존 제품", "경쟁사의 제품"),
    SIMPLE.replace("기존 제품", "타사의 제품"),
    SIMPLE.replace("기존 제품", "산업"),
    SIMPLE.replace("당사는", "과거 당사는"),
    SIMPLE.replace("당사는", "당시 당사는"),
    SIMPLE.replace("당사는", "향후 당사는"),
    SIMPLE.replace("당사는", "만약 당사는"),
    SIMPLE.replace("구축하고 있습니다", "구축할 계획입니다"),
    SIMPLE.replace("해외시장 개척", "경쟁사는 해외시장 개척"),
    SIMPLE.replace("해외시장 개척", "별도법인(주)은 해외시장 개척"),
    SIMPLE.replace("해외시장 개척", "주식회사 별도법인은 해외시장 개척"),
    SIMPLE.replace("해외시장 개척", "이와 무관하게 해외시장 개척"),
    SIMPLE.replace("해외시장 개척", "한편 다른 사업의 해외시장 개척"),
    SIMPLE.replace("해외시장 개척", "제약을 해소한 뒤 해외시장 개척"),
    SIMPLE.replace("3) 판매전략", "(2) 산업의 특성"),
    SIMPLE.replace("판단하여 해외시장", "판단했습니다. 해외시장"),
    SIMPLE.replace("판단하여 해외시장", "판단하여 (4) 다른 사업 해외시장"),
])
def test_unbound_or_historical_or_planned_relation_does_not_add_signal(source):
    assert business_constraint_signals(source) == ()


def test_named_affiliate_context_is_preserved_without_parent_subject_substitution():
    body = ("가온홀딩스(주)\n\n[기타부문-시제품]\n(1) 영업개황새봄솔루션(주)은 제품 제조업을 영위하고 있습니다.\n\n" +
            PROTO.replace("당사는", "새봄솔루션은"))
    start = body.index("(3)")
    context = context_for_candidate(text=body[start:], start=start, end=len(body),
                                    table_contexts=(), scopes=heading_source_scopes(body, document_actor="가온홀딩스(주)"))
    assert context
    signal, = business_constraint_signals(body[start:], source_context_json=context)
    assert "새봄솔루션은" in body[start:][signal.start:signal.end]
    assert "가온홀딩스" not in body[start:][signal.start:signal.end]


def test_wrapped_current_predicate_preserves_original_spaces():
    source = SIMPLE.replace("있습니다", "있습니 다")
    signal, = business_constraint_signals(source)
    assert source[signal.start:signal.end] == source
    assert source[signal.response_start:signal.response_end].endswith("있습니 다")


def _harvest(body):
    receipt = "20260101000001"
    row = RawFilingRow(receipt, "사업보고서", "20260101")
    fetcher = FakeFetcher(list_responses_by_pblntf_ty={"A": FilingListResult(state="OK", rows=(row,))},
                          document_responses_by_rcept_no={receipt: DocumentFetchResult(state="OK", text=body,
                              document_actor="가온제조(주)", bytes_downloaded=len(body.encode()))})
    return collect_dart_evidence(fetcher, "00000000", now="2026-01-02T00:00:00+09:00")


def test_collector_adds_separate_exact_issue_response_without_changing_other_fragments():
    existing = "당사는 제품을 생산하여 고객에게 제공하며 수수료를 받는다."
    original = "I. 회사의 개요\n" + existing + "\n\nII. 사업의 내용\n" + PROTO
    before = _harvest(original.replace("성장에 한계를 느껴", "사업에서 기회를 찾아"))
    after = _harvest(original)
    old = [f for f in before.fragments if f.text == existing]
    new = [f for f in after.fragments if f.text == existing]
    assert old == new
    found = [f for f in after.fragments if "direct_pattern:business_growth_constraint" in f.reason_codes]
    assert len(found) == 1
    fragment = found[0]
    start, end = map(int, fragment.location.split("-"))
    assert original[start:end] == fragment.text
    assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()
    assert set(fragment.covered_slot_ids) == {"current_challenges:issue", "current_challenges:response"}
    assert "향후 인수" in fragment.text


def test_same_section_with_two_relations_creates_one_exact_candidate():
    source = SIMPLE + " " + SIMPLE.split("판매전략", 1)[1]
    signal, = business_constraint_signals(source)
    assert source[signal.start:signal.end] == source
    harvest = _harvest("II. 사업의 내용\n" + source)
    derived = [fragment for fragment in harvest.fragments
               if "direct_pattern:business_growth_constraint" in fragment.reason_codes]
    assert len(derived) == 1


def test_added_constraint_does_not_renumber_existing_activity_table(monkeypatch):
    table = ("당기와 전기 중 당사가 시공한 주요 도급공사입니다.\n\n"
             "(3) 당기말 현재 종류별 실적입니다. ① 당기\n\n"
             "구분 | 도급금액 | 누적공사수익 ; 정밀설비공사 현장 | 100 | 60")
    body = "II. 사업의 내용\n" + PROTO + "\n\n" + table
    with monkeypatch.context() as scoped:
        scoped.setattr(collect_module, "business_constraint_signals", lambda *args, **kwargs: ())
        before = _harvest(body)
    after = _harvest(body)
    reason = "direct_pattern:current_company_activity_table"
    old_tables = [item for item in before.fragments if reason in item.reason_codes]
    new_tables = [item for item in after.fragments if reason in item.reason_codes]
    assert old_tables
    assert old_tables == new_tables


def test_default_retention_and_explicit_same_rank_are_identical():
    left = CandidateRetention(frozenset(("current_challenges:issue",)))
    right = CandidateRetention(frozenset(("current_challenges:issue",)))
    score = (SlotScore("current_challenges", "current_challenges:issue", 250, ("direct_pattern:test",)),)
    for index in range(30):
        body = f"회사의 명시 사업 문제 {index}에 관한 충분한 길이의 원문입니다."
        candidate = FragmentCandidate(index*100, index*100+len(body), body, "")
        left.offer_scored(index, candidate, score)
        right.offer_scored(index, candidate, score, rank_index=index)
    assert left.pools == right.pools
    assert left.selected_scored() == right.selected_scored()


def test_supplement_id_is_not_treated_as_latest_document_position():
    retention = CandidateRetention(frozenset(("current_challenges:issue",)))
    score = (SlotScore("current_challenges", "current_challenges:issue", 250, ("direct_pattern:test",)),)
    for index in range(40):
        text = f"회사 문제와 대응을 그대로 담은 충분한 길이의 원문 번호 {index}입니다."
        retention.offer_scored(index, FragmentCandidate(index*100, index*100+len(text), text, ""), score)
    derived = FragmentCandidate(10, 40, "앞부분에서 찾은 정확한 사업제약과 대응의 원문", "")
    retention.offer_scored(10000, derived, score, rank_index=0)
    recent = retention.pools[("current_challenges:issue", "recent")]
    assert all(item.index != 10000 for item in recent)
    assert any(item.index == 10000 for item in retention.pools[("current_challenges:issue", "top")])
    assert all(len(pool) <= retention.slot_count_limit for pool in retention.pools.values())
