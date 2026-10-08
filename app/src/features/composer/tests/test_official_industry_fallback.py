"""공식 산업 보조가 마지막 부분 경로에만 들어가고 검수 하한을 바꾸지 않는다."""

from dataclasses import replace

import pytest

from src.features.composer import pipeline
from src.features.composer.constants import DEFAULT_CITATION_STYLE
from src.features.composer.evidence_availability import EvidenceAvailability
from src.features.composer.port import AskFatalError, ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.tests.test_official_industry_context import _official_materials
from src.features.composer.tests.test_evidence_available_report import _one_sentence_writer
from src.features.composer.tests.test_pipeline import _FakeReviewer, _raw_fragments
from src.features.composer.tests import test_pipeline as full_fixture
from src.features.composer.validate import V2ValidationError
from src.shared.report_evidence.constants import ReleaseMode


def final_partial(callback, diagnostics=None):
    company, fragments, anchor, problem, composed = _official_materials()
    composed = replace(composed, sections=tuple(
        replace(section, sentences=(ComposedSentence(
            fragments[0].text, (fragments[0].fragment_id,), "확인",
            planned_claim_slot="portfolio:product_role", verification_state="verified",
        ),)) if section.section_id == "portfolio" else section for section in composed.sections
    ))
    output = pipeline._finish_evidence_available(
        company, composed, fragments, None,
        availability=EvidenceAvailability("partial"), degraded_reason="quality_floor",
        degraded_cause_kind="", ai_stages_skipped=(), downgraded_from=ReleaseMode.FULL.value,
        tail_already_applied=True, corp_type="", generated_at="", as_of_date="2026-09-30",
        analysis_period="", latest_performance_period="", table_presentation="table",
        filing_meta=None, composition_tables=(), citation_style=DEFAULT_CITATION_STYLE,
        company_id=anchor.company_id, review_diagnostics=[],
        composition_diagnostics=diagnostics if diagnostics is not None else [],
        draft_body_count=1, news_review_candidates=frozenset(), industry_anchors=(anchor,),
        official_industry_fallback=callback(problem),
    )
    return output


def test_full_downgrade_adds_separate_industry_context_and_keeps_company_facts():
    calls = []
    output = final_partial(lambda problem: lambda selected: calls.append(selected) or (problem,))
    assert len(calls) == 1
    section = next(value for value in output.report.sections if value.cell == "current_challenges")
    assert len(section.industry_contexts) == 1
    assert section.industry_contexts[0].problem.observation_period == "2025"
    assert output.report.publication_policy == "evidence-available-v1"
    assert output.report.grade.value != "FULL"
    assert all(value.claim_slot != "current_challenges:issue" for value in output.report.fact_records)


def test_unbound_optional_result_is_diagnosed_and_body_is_preserved():
    diagnostics = []
    output = final_partial(lambda problem: lambda selected: (
        replace(problem, location="선택되지 않은 위치"),
    ), diagnostics)
    baseline = final_partial(lambda _: None)
    assert [value.prose_lines for value in output.report.sections] == [
        value.prose_lines for value in baseline.report.sections]
    assert not any(value.industry_contexts for value in output.report.sections)
    assert any(value.get("상태") == "결속불가" for value in diagnostics)


@pytest.mark.parametrize("mode", (ReleaseMode.SHADOW, ReleaseMode.FULL))
def test_empty_initial_path_stays_ai_zero(mode):
    calls = []
    def unexpected(*args):
        calls.append(args)
        raise AssertionError("초기 경로에서 AI를 호출했습니다")
    try:
        pipeline.run_v2("합성기업", {}, None, writer_ask=unexpected, reviewer_ask=unexpected,
            official_industry_fallback=unexpected, release_mode=mode,
            evidence_availability=EvidenceAvailability("none"))
    except V2ValidationError:
        assert mode is ReleaseMode.FULL
    assert calls == []


def test_shadow_callback_runs_after_all_writer_review_and_diagram_requests():
    events = []
    writer, reviewer = _one_sentence_writer(), _FakeReviewer()
    def write(prompt):
        events.append("writer")
        return writer(prompt)
    def review(prompt):
        events.append("review")
        return reviewer(prompt)
    def diagram(prompt):
        events.append("diagram")
        return '{"판정": []}'
    def fallback(selected):
        assert selected and events and "review" in events
        events.append("official")
        return ()
    output = pipeline.run_v2("가나다전자", _raw_fragments(), None,
        writer_ask=write, reviewer_ask=review, diagram_ask=diagram,
        official_industry_fallback=fallback, evidence_availability=EvidenceAvailability("partial"))
    assert output.report and events[-1] == "official" and events.count("official") == 1


def test_provider_failure_never_starts_optional_callback():
    calls = []
    def failed(_):
        raise AskFatalError(RuntimeError("공급자 실패"), provider_failure=True)
    output = pipeline.run_v2("가나다전자", _raw_fragments(), None,
        writer_ask=failed, reviewer_ask=_FakeReviewer(), preserve_on_ask_failure=True,
        official_industry_fallback=lambda selected: calls.append(selected) or (),
        evidence_availability=EvidenceAvailability("partial"))
    assert output.degraded_cause_kind == "RuntimeError" and not calls


def test_normal_full_seal_keeps_all_existing_assertions_and_never_calls_fallback(monkeypatch):
    calls = []
    original = pipeline.run_v2
    def run(*args, **kwargs):
        kwargs["official_industry_fallback"] = lambda selected: calls.append(selected) or ()
        return original(*args, **kwargs)
    monkeypatch.setattr(full_fixture, "run_v2", run)
    # 기존 충분한 FULL의 근거·품질·봉인·58사실 단정을 그대로 재사용한다.
    full_fixture.test_엄격모드는_충분한_검증사실만_완성으로_봉인한다()
    assert calls == []


@pytest.mark.parametrize("state,slot,existing", (
    ("verified", "current_challenges:issue", ()),
    ("unverified", "current_challenges:response", ()),
    ("verified", "current_challenges:response", ("기존",)),
))
def test_direct_issue_news_context_and_unreviewed_body_skip(state, slot, existing):
    sentence = ComposedSentence("회사는 부품 공급 지연을 겪고 있다.", ("1",), "확인",
        planned_claim_slot=slot, verification_state=state)
    report = ComposedReport((ComposedSection("current_challenges", (sentence,)),))
    calls = []
    result = pipeline._late_official_industry_problems(
        report, callback=lambda selected: calls.append(selected) or (),
        anchors=(), problems=existing, fragments=(), company_id="", diagnostics=[],
    )
    assert result == existing and calls == []
