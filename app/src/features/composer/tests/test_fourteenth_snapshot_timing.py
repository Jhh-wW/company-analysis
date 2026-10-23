"""실제14의 원시 공시·참 판정은 보존하고 결산 보증 현황의 현재화만 차단한다."""

from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.disclosure_snapshot_timing import (
    disclosure_snapshot_date, disclosure_snapshot_timing_problem,
)
from src.features.composer.disclosure_snapshot_timing_constants import SNAPSHOT_TIMING_UNSUPPORTED
from src.features.composer.port import CollectedFragment, ComposedSentence, ComposedReport, ComposedSection, FilingMeta
from src.features.composer.prose_facts import ProseEvidence, evaluate_verified_prose_fact
from src.features.composer.quality_projection import select_bound_public_sentences
from src.features.composer.render import render_report
from src.features.composer.verify import _apply_grounding
from src.features.provenance.sources import Source, SourceKind, exact_evidence_text_hash
from src.features.storage.reports import report_from_dict, report_to_dict
from src.shared.report_quality.fact_binding import fact_evidence_binding


SAVED = json.loads((Path(__file__).parent / "fixtures/fourteenth_snapshot_timing.json").read_text(encoding="utf-8"))
CLAIM, TEXT = SAVED["claim"], SAVED["source_text"]
BASELINE = SAVED["as_of_date"]
DATED = "2025년 12월 31일 기준, " + CLAIM


def source(**changes):
    meta = SAVED["source"]
    return replace(Source(number=4, kind=SourceKind.FILING, label=meta["label"],
        source_id=meta["source_id"], title=meta["title"], publisher=meta["publisher"],
        host=meta["host"], url=meta["url"], document_id=meta["document_id"],
        location=meta["location"], disclosed_at=meta["disclosed_at"],
        exact_evidence_hashes=[exact_evidence_text_hash(TEXT)]), **changes)


def problem(claim=CLAIM, text=TEXT, **changes):
    return disclosure_snapshot_timing_problem(claim, {"4": text}, {"4": source(**changes)}, baseline_date=BASELINE)


def build(claim=CLAIM, **changes):
    sentence = ComposedSentence(claim, ("4",), "확인", planned_claim_slot=SAVED["claim_slot"], verification_state="verified")
    return evaluate_verified_prose_fact(sentence, section_id=SAVED["section_id"], company_name=SAVED["company"],
        as_of_date=BASELINE, evidence=(ProseEvidence("4", source(**changes), TEXT),), carry_official_exact_text=True)


def test_actual_fourteenth_source_and_original_true_cannot_become_present_fact():
    assert hashlib.sha256(TEXT.encode()).hexdigest() == SAVED["source_sha256"]
    assert SAVED["review_entry"]["결과"] == "참"
    assert problem() == SNAPSHOT_TIMING_UNSUPPORTED
    result = build()
    assert result.fact is None and result.reason_code == SNAPSHOT_TIMING_UNSUPPORTED
    fragment = CollectedFragment("4", "특수관계자", TEXT)
    problems = {}
    # 원형 번호·근거대조·판정을 그대로 쓴다. 새 승인이나 수정 응답은 만들지 않는다.
    verdicts = _apply_grounding(json.dumps({"판정": [SAVED["review_entry"]]}, ensure_ascii=False),
        {56: "참"}, {56: (CLAIM, {"4": TEXT})},
        diagnostic_contexts={56: (SAVED["section_id"], "본문", CLAIM)},
        claim_slots_by_number={56: SAVED["claim_slot"]}, source_fragments_by_id={"4": fragment},
        grounding_problems=problems, baseline_date=BASELINE)
    assert verdicts[56] != "참" and problems[56] == SNAPSHOT_TIMING_UNSUPPORTED


def test_explicit_same_source_period_is_kept_as_past_fact_without_changing_claim():
    assert problem(DATED) == ""
    result = build(DATED)
    assert result.fact is not None, result.reason_code
    fact = result.fact
    assert fact.claim == DATED
    assert (fact.as_of, fact.time_state, fact.source_date) == ("2025-12-31", "past", "2026-03-31")
    assert fact.evidence_binding == fact_evidence_binding(fact)


@pytest.mark.parametrize("prefix", ["2024년 12월 31일 기준, ", "2026년 3월 31일 기준, ",
    "2026년 10월 23일 기준, ", "2025년 2월 30일 기준, "])
def test_other_period_disclosure_day_collection_day_or_invalid_day_cannot_retime(prefix):
    assert problem(prefix + CLAIM) == SNAPSHOT_TIMING_UNSUPPORTED
    assert build(prefix + CLAIM).fact is None


@pytest.mark.parametrize("suffix", [" 현재도 제공하고 있다.", " 이후에도 보증을 유지한다.", " 여전히 보증하고 있다."])
def test_dated_prefix_does_not_release_reaffirmed_present(suffix):
    assert problem(DATED + suffix) == SNAPSHOT_TIMING_UNSUPPORTED


@pytest.mark.parametrize("period", ["2024-01-01/2024-12-31", "2025-01-01/2025-06-30", "bad", "2025-12-31/2025-01-01"])
def test_changed_or_unknown_source_period_cannot_supply_claim_date(period):
    assert problem(DATED, reporting_period=period) == SNAPSHOT_TIMING_UNSUPPORTED


def test_explicit_period_wins_over_document_day_and_legacy_needs_matching_document_title():
    assert problem(DATED, reporting_period="2025-01-01/2025-12-31") == ""
    assert problem(DATED, title="감사보고서 (2024.12)") == SNAPSHOT_TIMING_UNSUPPORTED
    assert problem(DATED, title="") == SNAPSHOT_TIMING_UNSUPPORTED
    assert problem(DATED, text=TEXT.replace("보고기간종료일 현재", "전기말 현재")) == SNAPSHOT_TIMING_UNSUPPORTED


def test_same_current_official_claim_is_required_when_extra_citation_is_added():
    current = "현재 비바리퍼블리카는 토스페이먼츠에 지급보증을 제공하고 있다."
    extra = CollectedFragment("n", "official_identity_verified_web_page", current,
        formal_source_kind="official_identity_verified_web_page", source_collected_on=BASELINE,
        source_publisher=SAVED["company"])
    def mixed(statement=current, **changes):
        return disclosure_snapshot_timing_problem(current, {"4": TEXT, "n": statement},
            {"4": source(), "n": replace(extra, text=statement, **changes)}, baseline_date=BASELINE)
    assert mixed() == ""
    for statement in (current.replace("비바리퍼블리카", "다른회사"), current.replace("토스페이먼츠", "다른수혜자"),
                      "현재 비바리퍼블리카는 고객지원 서비스를 제공하고 있다.",
                      "2025년 12월 31일 기준, " + current, "감사자료: 2025년 12월 31일 기준, " + current,
                      "보고기간종료일 " + current, "만약 " + current, current[:-1] + "는 뜻은 아니다.",
                      "조건이 충족되는 경우 " + current):
        assert mixed(statement) == SNAPSHOT_TIMING_UNSUPPORTED
    assert mixed(source_collected_on="2025-10-23") == SNAPSHOT_TIMING_UNSUPPORTED
    assert mixed(formal_source_kind="news", kind="news") == SNAPSHOT_TIMING_UNSUPPORTED
    assert mixed(source_publisher="OtherCompany") == SNAPSHOT_TIMING_UNSUPPORTED
    assert mixed(source_publisher="") == SNAPSHOT_TIMING_UNSUPPORTED


@pytest.mark.parametrize("claim", ["회사는 현재 전자결제 서비스를 제공하고 있다.",
    "회사는 신주인수권부사채를 전액 상환하였으며 연대보증을 제공받았다.",
    "비바리퍼블리카는 유상증자 방식으로 자금을 공급하는 역할을 수행하였다.",
    "회사는 차입금 보증을 제공받을 계획이다."])
def test_ordinary_business_completed_events_and_plans_are_preserved(claim):
    assert problem(claim) == ""


def test_same_baseline_snapshot_is_current_and_source_date_is_not_fact_date():
    sources = {"4": TEXT}
    fragments = {"4": source(reporting_period="2026-01-01/2026-10-23")}
    assert disclosure_snapshot_timing_problem(CLAIM, sources, fragments, baseline_date=BASELINE) == ""
    assert disclosure_snapshot_date(CLAIM, sources, fragments) == ""
    assert problem(collected_at=BASELINE) == SNAPSHOT_TIMING_UNSUPPORTED


def test_diagnostic_classifies_snapshot_failure_as_timing():
    # 진단 형식의 상세 검증은 기존 공통 테스트가 맡는다. 사유의 분류만 확인한다.
    from src.shared.report_quality.review_diagnostic_constants import REVIEW_SCOPE_ITEMS
    assert REVIEW_SCOPE_ITEMS[SNAPSHOT_TIMING_UNSUPPORTED] == "시점"


@pytest.mark.parametrize("year", [2023, 2024, 2025])
def test_period_year_and_legacy_annual_end_must_agree(year):
    text = TEXT.replace("2025년", f"{year}년")
    claim = f"{year}년 12월 31일 기준, " + CLAIM
    fragments = {"4": source(title=f"감사보고서 ({year}.12)", reporting_period=str(year))}
    assert disclosure_snapshot_date(claim, {"4": text}, fragments) == f"{year}-12-31"
    fragments["4"] = replace(fragments["4"], reporting_period=str(year - 1))
    assert disclosure_snapshot_timing_problem(claim, {"4": text}, fragments, baseline_date=BASELINE) == SNAPSHOT_TIMING_UNSUPPORTED


def test_baseline_snapshot_cannot_exempt_a_wrong_stated_date():
    fragments = {"4": source(reporting_period="2026-01-01/2026-10-23")}
    assert disclosure_snapshot_timing_problem(DATED, {"4": TEXT}, fragments, baseline_date=BASELINE) == SNAPSHOT_TIMING_UNSUPPORTED


def test_dated_prefix_cannot_cover_a_separate_explicit_present_clause():
    assert problem(DATED + " 현재 비바리퍼블리카는 보증을 제공하고 있다.") == SNAPSHOT_TIMING_UNSUPPORTED


@pytest.mark.parametrize("claim,expected", [(CLAIM, None), (DATED, "2025-12-31")])
def test_verified_sentence_render_quality_projection_and_storage_keep_the_same_timing(claim, expected):
    original = ComposedSentence(claim, ("4",), "확인", planned_claim_slot=SAVED["claim_slot"], verification_state="verified")
    composed = ComposedReport((ComposedSection(SAVED["section_id"], (original,)),), summary=(original,))
    meta = SAVED["source"]
    public = render_report(SAVED["company"], composed, (CollectedFragment("4", "특수관계자", TEXT),), None,
        as_of_date=BASELINE, filing_meta=FilingMeta(meta["document_id"], meta["title"], meta["disclosed_at"]))
    if expected is None:
        assert not public.fact_records
        selected = select_bound_public_sentences(composed, public)
        assert selected.excluded
        # pipeline의 사실 결속 후 재렌더 경로를 그대로 지난 최종 표시도 확인한다.
        final = render_report(SAVED["company"], selected.report, (CollectedFragment("4", "특수관계자", TEXT),), None,
            as_of_date=BASELINE, filing_meta=FilingMeta(meta["document_id"], meta["title"], meta["disclosed_at"]))
        assert all(CLAIM not in text for section in final.sections for text, _ in section.prose_lines)
    else:
        fact, = public.fact_records
        assert (fact.time_state, fact.as_of, fact.claim) == ("past", expected, claim)
        assert not select_bound_public_sentences(composed, public).excluded
        assert report_from_dict(report_to_dict(public)).fact_records == public.fact_records
    assert original.text == claim


def test_writer_and_reviewer_share_only_the_confirmed_snapshot_date():
    from src.features.composer.logic import _render_fragments
    from src.features.composer.verify import _review_fragment_metadata
    fragment = CollectedFragment("4", "특수관계자", TEXT, document_title="감사보고서 (2025.12)",
        reporting_period="2025-01-01/2025-12-31", document_date="2026-03-31", source_collected_on=BASELINE)
    for rendered in (_render_fragments((fragment,)), _review_fragment_metadata(fragment)):
        assert '공시 현황 기준일' in rendered and '2025-12-31' in rendered
        assert '자료 발행일·수집일이 아니다' in rendered
    unknown = replace(fragment, reporting_period="", document_title="특수관계자")
    assert '공시 현황 기준일' not in _review_fragment_metadata(unknown)


def test_future_snapshot_date_cannot_be_used_before_the_report_baseline():
    claim = "2027년 12월 31일 기준, " + CLAIM
    future = source(reporting_period="2027-01-01/2027-12-31")
    assert disclosure_snapshot_timing_problem(claim, {"4": TEXT}, {"4": future}, baseline_date=BASELINE) == SNAPSHOT_TIMING_UNSUPPORTED
