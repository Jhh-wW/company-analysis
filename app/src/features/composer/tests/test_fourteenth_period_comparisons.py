"""실제14 당기·전기 비용비교의 원문과 사실 기준일을 독립적으로 검증한다."""

from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.disclosure_comparison_timing import disclosure_comparison_date, disclosure_comparison_timing_problem
from src.features.composer.disclosure_comparison_timing_constants import COMPARISON_PERIOD_UNBOUND
from src.features.composer.port import CollectedFragment, ComposedSentence, ComposedReport, ComposedSection
from src.features.composer.render import render_report
from src.features.composer.prose_facts import ProseEvidence, evaluate_verified_prose_fact
from src.features.provenance.sources import Source, SourceKind, exact_evidence_text_hash
from src.shared.report_quality.constants import OFFICIAL_PROSE_EXACT_TEXT_KEY
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.features.composer.verify import _apply_grounding


SAVED = json.loads((Path(__file__).parent / "fixtures/fourteenth_period_comparisons.json").read_text(encoding="utf-8"))
PERIOD = "2025-01-01/2025-12-31"
BASELINE = SAVED["as_of_date"]


def source(number, *, period=PERIOD, **changes):
    item = SAVED["sources"][number]
    meta, text = item["source"], item["source_text"]
    return replace(Source(number=int(number), kind=SourceKind.FILING, label=meta["label"],
        source_id=meta["source_id"], title=meta["title"], publisher=meta["publisher"],
        host=meta["host"], document_id=meta["document_id"], url=meta["url"], location=meta["location"],
        reporting_period=period, disclosed_at=meta["disclosed_at"],
        exact_evidence_hashes=[exact_evidence_text_hash(text)]), **changes)


def build(fact, *, claim=None, period=PERIOD, **changes):
    number = fact["source_id"].removeprefix("v2-frag-")
    text = SAVED["sources"][number]["source_text"]
    sentence = ComposedSentence(claim or fact["claim"], (number,), "확인", planned_claim_slot=fact["claim_slot"], verification_state="verified")
    return evaluate_verified_prose_fact(sentence, section_id=fact["section_owner"], company_name=SAVED["company"],
        as_of_date=BASELINE, evidence=(ProseEvidence(number, source(number, period=period, **changes), text),), carry_official_exact_text=True)


@pytest.mark.parametrize("fact", SAVED["facts"], ids=lambda fact: fact["fact_id"])
def test_actual_closed_past_comparisons_keep_claim_and_bind_confirmed_xml_period(fact):
    number = fact["source_id"].removeprefix("v2-frag-")
    text = SAVED["sources"][number]["source_text"]
    assert hashlib.sha256(text.encode()).hexdigest() in SAVED["sources"][number]["source"]["exact_evidence_hashes"]
    result = build(fact)
    assert result.fact is not None, result.reason_code
    output = result.fact
    assert output.claim == fact["claim"]
    assert (output.time_state, output.as_of, output.source_date, output.event_date) == ("past", "2025-12-31", "2026-03-31", "")
    assert output.evidence_binding == fact_evidence_binding(output)
    assert json.loads(output.state_evidence)[0][OFFICIAL_PROSE_EXACT_TEXT_KEY] == text
    entry = fact["original_review_entry"]
    assert entry["결과"] == "참"
    number_id = entry["번호"]
    fragment = CollectedFragment(number, "판관비", text, document_title="감사보고서 (2025.12)",
        reporting_period=PERIOD, source_document_id="20260331000296")
    problems = {}
    verdicts = _apply_grounding(json.dumps({"판정": [entry]}, ensure_ascii=False), {number_id: "참"},
        {number_id: (fact["claim"], {number: text})}, diagnostic_contexts={number_id: (fact["section_owner"], "본문", fact["claim"])},
        claim_slots_by_number={number_id: fact["claim_slot"]}, source_fragments_by_id={number: fragment},
        grounding_problems=problems, baseline_date=BASELINE)
    assert verdicts[number_id] == "참", problems


@pytest.mark.parametrize("fact", SAVED["facts"], ids=lambda fact: fact["fact_id"])
def test_original_missing_period_does_not_invent_year_from_title_or_document_day(fact):
    result = build(fact, period="")
    assert result.fact is None and result.reason_code == COMPARISON_PERIOD_UNBOUND


@pytest.mark.parametrize("period", ["2024-01-01/2024-12-31", "2026-01-01/2026-12-31", "2025-12-31/2025-01-01", "2025", "2025-12-31"])
def test_wrong_year_future_or_incomplete_period_does_not_retime_comparison(period):
    assert build(SAVED["facts"][0], period=period).reason_code == COMPARISON_PERIOD_UNBOUND


@pytest.mark.parametrize("changes", [{"publisher": "다른회사"}, {"publisher": ""}, {"title": "감사보고서 (2024.12)"}])
def test_other_publisher_or_conflicting_title_cannot_supply_period(changes):
    assert build(SAVED["facts"][0], **changes).reason_code == COMPARISON_PERIOD_UNBOUND


def test_multiple_documents_periods_or_unrelated_current_statement_cannot_supply_period():
    fact = SAVED["facts"][0]
    number = fact["source_id"].removeprefix("v2-frag-")
    text = SAVED["sources"][number]["source_text"]
    for other in (source(number, document_id="20250331000329"), source(number, period="2024-01-01/2024-12-31")):
        assert disclosure_comparison_date(fact["claim"], {number: text, "n": text}, {number: source(number), "n": other},
            company_name=SAVED["company"], baseline_date=BASELINE) == ""
    assert build(fact, claim=fact["claim"] + " 현재 회사는 지급보증을 유지하고 있다.").reason_code == COMPARISON_PERIOD_UNBOUND
    stripped = text.replace("당 기", "금액").replace("전 기", "금액").replace("당기", "금액").replace("전기", "금액")
    assert disclosure_comparison_date(fact["claim"], {number: stripped}, {number: source(number)}, company_name=SAVED["company"], baseline_date=BASELINE) == ""


@pytest.mark.parametrize("claim", ["회사는 현재 PG 서비스를 제공하고 있다.", "회사는 수수료를 수익으로 인식한다.",
    "회사는 판매수수료를 비용으로 계상한다.", "회사는 앞으로 급여 비용을 늘릴 계획이다."])
def test_ordinary_current_pg_accounting_and_future_plans_are_preserved(claim):
    assert disclosure_comparison_timing_problem(claim, {"5": SAVED["sources"]["5"]["source_text"]}, {"5": source("5", period="")}, baseline_date=BASELINE) == ""


@pytest.mark.parametrize("fact", SAVED["merger_facts"], ids=lambda fact: fact["fact_id"])
def test_actual_identity_and_past_changes_merger_share_the_same_explicit_event_day(fact):
    meta, text = SAVED["merger_source"]["source"], SAVED["merger_source"]["source_text"]
    assert hashlib.sha256(text.encode()).hexdigest() in meta["exact_evidence_hashes"]
    original = Source(number=1, kind=SourceKind.FILING, label=meta["label"], source_id=meta["source_id"],
        title=meta["title"], publisher=meta["publisher"], host=meta["host"], url=meta["url"],
        document_id=meta["document_id"], location=meta["location"], disclosed_at=meta["disclosed_at"],
        exact_evidence_hashes=[exact_evidence_text_hash(text)])
    sentence = ComposedSentence(fact["claim"], ("1",), "확인", planned_claim_slot=fact["claim_slot"], verification_state="verified")
    result = evaluate_verified_prose_fact(sentence, section_id=fact["section_owner"], company_name=SAVED["company"],
        as_of_date=BASELINE, evidence=(ProseEvidence("1", original, text),), carry_official_exact_text=True)
    assert result.fact is not None, result.reason_code
    assert (result.fact.claim, result.fact.time_state, result.fact.as_of) == (fact["claim"], "past", "2023-11-21")


def test_actual_typed_dart_publisher_projects_to_company_before_comparison_fact_binding():
    fact = next(item for item in SAVED["facts"] if item["source_id"] == "v2-frag-69")
    meta, text = SAVED["sources"]["69"]["source"], SAVED["sources"]["69"]["source_text"]
    fragment = CollectedFragment("69", "typed-evidence-v3:" + "a" * 64, text,
        source_url=meta["url"], document_title=meta["title"], location=meta["location"], document_date=meta["disclosed_at"],
        document_identity="document:dart.fss.or.kr:" + meta["document_id"], document_content_sha256=meta["document_content_sha256"],
        formal_source_kind=meta["formal_source_kind"], source_document_id=meta["document_id"],
        source_publisher="금융감독원 전자공시시스템(DART)", identity_binding=meta["identity_binding"],
        source_collected_on=BASELINE, reporting_period=PERIOD, supported_claim_slots=(fact["claim_slot"],))
    sentence = ComposedSentence(fact["claim"], ("69",), "확인", planned_claim_slot=fact["claim_slot"], verification_state="verified")
    composed = ComposedReport((ComposedSection(fact["section_owner"], (sentence,)),))
    public = render_report(SAVED["company"], composed, (fragment,), None, as_of_date=BASELINE)
    output, = public.fact_records
    assert (output.time_state, output.as_of, output.source_publisher) == ("past", "2025-12-31", SAVED["company"])
    assert public.citations[0].reporting_period == PERIOD
    from src.features.provenance.sources import has_valid_provenance_seal
    assert has_valid_provenance_seal(public.citations[0])
    assert not has_valid_provenance_seal(replace(public.citations[0], reporting_period="2024-01-01/2024-12-31"))


FIFTEENTH = json.loads((Path(__file__).parent / "fixtures/fifteenth_financial_comparison.json").read_text(encoding="utf-8"))


def fifteenth_source(**changes):
    fields = {key: value for key, value in FIFTEENTH["source"].items() if key in Source.__dataclass_fields__}
    fields["kind"] = SourceKind.FILING
    return replace(Source(**fields), **changes)


def test_actual_fifteenth_financial_comparison_keeps_original_true_and_binds_existing_period():
    saved = FIFTEENTH
    original = json.dumps(saved, ensure_ascii=False, sort_keys=True)
    fact, text, entry = saved["original_fact"], saved["source_text"], saved["review_row"]
    assert hashlib.sha256(text.encode()).hexdigest() == saved["source_sha256"]
    assert entry["결과"] == "참"
    assert fact["as_of"] == saved["baseline"]
    sentence = ComposedSentence(fact["claim"], ("5",), "확인", planned_claim_slot=fact["claim_slot"], verification_state="verified")
    result = evaluate_verified_prose_fact(sentence, section_id="past_changes", company_name=saved["company"],
        as_of_date=saved["baseline"], evidence=(ProseEvidence("5", fifteenth_source(), text),))
    assert result.fact is not None, result.reason_code
    assert (result.fact.time_state, result.fact.as_of, result.fact.source_date, result.fact.event_date) == (
        "past", "2025-12-31", "2026-03-31", "")
    assert (result.fact.fact_id, result.fact.claim) == (fact["fact_id"], fact["claim"])
    manifest = json.loads(result.fact.state_evidence)
    assert manifest[0].pop(OFFICIAL_PROSE_EXACT_TEXT_KEY) == text
    assert manifest == json.loads(fact["state_evidence"])
    before = asdict(type(result.fact)(**fact))
    changed = {key for key, value in asdict(result.fact).items() if value != before[key]}
    assert changed == {"as_of", "state_evidence", "evidence_binding"}
    assert result.fact.evidence_binding == fact_evidence_binding(result.fact)
    fragment = CollectedFragment("5", "판관비", text, document_title=fifteenth_source().title,
        reporting_period=PERIOD, source_document_id=fifteenth_source().document_id)
    problems = {}
    verdicts = _apply_grounding(json.dumps({"판정": [entry]}, ensure_ascii=False), {30: "참"}, {30: (fact["claim"], {"5": text})},
        diagnostic_contexts={30: ("past_changes", "본문", fact["claim"])}, claim_slots_by_number={30: fact["claim_slot"]},
        source_fragments_by_id={"5": fragment}, grounding_problems=problems, baseline_date=saved["baseline"])
    assert verdicts[30] == "참", problems
    assert json.dumps(saved, ensure_ascii=False, sort_keys=True) == original


@pytest.mark.parametrize("changes", [{"reporting_period": ""}, {"reporting_period": "2026-01-01/2026-12-31"},
    {"reporting_period": "2025-02-30/2025-12-31"}, {"reporting_period": "2025-12-31/2025-01-01"},
    {"publisher": "다른회사"}, {"title": "감사보고서 (2024.12)"}])
def test_fifteenth_financial_comparison_does_not_accept_unbound_period_or_company(changes):
    saved = FIFTEENTH
    assert disclosure_comparison_timing_problem(saved["original_fact"]["claim"], {"5": saved["source_text"]},
        {"5": fifteenth_source(**changes)}, company_name=saved["company"], baseline_date=saved["baseline"]) == COMPARISON_PERIOD_UNBOUND


@pytest.mark.parametrize("claim", ["금융수익은 유효이자율법에 따라 인식한다.", "회사는 금융비용을 비용으로 계상한다.",
    "회사는 현재 금융비용을 관리하고 있다."])
def test_financial_accounting_and_current_business_are_not_treated_as_past_comparison(claim):
    assert disclosure_comparison_timing_problem(claim, {"5": FIFTEENTH["source_text"]},
        {"5": fifteenth_source(reporting_period="")}, baseline_date=BASELINE) == ""
