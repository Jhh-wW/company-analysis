"""최종 산문·요약만 정규화하며 검증 원문과 공개 봉인을 보존한다."""

import logging
from dataclasses import replace

import pytest

from src.core import news_intake_switch
from src.features.composer.constants import (
    CITATION_STYLE_INLINE, GRADE_CONFIRMED, GRADE_INTERPRETED, SECTION_IDS,
)
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    FlowRow, NewsRow, PerformanceTable,
)
from src.features.composer.public_manifest import build_public_structure_seal
from src.features.composer.render import render_report
from src.features.composer.style_normalizer import SentenceStyleNormalizer
from src.features.composer.tests.test_role_binding_participation_condition import news_fragment
from src.features.composer.tests.test_style_normalizer import (
    MEDILINE_NORMALIZED, MEDILINE_SENTENCE, WRTN_SENTENCE,
)
from src.features.composer.verbatim_news import verbatim_news_source
from src.features.pipeline.port import Grade
from src.shared.report_generation.canonical import public_content_digests
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.source_identity import document_identity_from_parts
from src.shared.report_quality.summary_binding import summary_verification_binding
from src.shared.report_quality.constants import STRICT_QUALITY_CONTRACT_VERSION
from src.shared.report_quality.models import PublicationPolicy, ReleaseDecision


@pytest.fixture(autouse=True)
def _enable_news(monkeypatch):
    monkeypatch.setattr(news_intake_switch, "_PROCESS_SWITCH", news_intake_switch.NewsIntakeSwitch.ON)


def _report(text, *, section_id="identity", grade=GRADE_CONFIRMED):
    sentence = ComposedSentence(text, ("1",), grade)
    return ComposedReport(
        sections=tuple(
            ComposedSection(key, (sentence,) if key == section_id else ())
            for key in SECTION_IDS
        ),
        summary=(sentence,),
    )


def _fragments(text):
    url = "https://company.example/report"
    return (CollectedFragment(
        "1", "사업내용", text, source_url=url,
        document_identity=document_identity_from_parts(url=url),
    ),)


def _news_fragment(text):
    url = "https://media.example/article/1"
    return news_fragment(
        text=text, fragment_id="1", location="기사 본문",
        document_identity=document_identity_from_parts(url=url, document_id=url, host="media.example"),
    )


def _render(report, fragments, **kwargs):
    return render_report(
        "시험회사", report, fragments, None,
        citation_style=CITATION_STYLE_INLINE, **kwargs,
    )


def test_메디라인_본문과_요약_표시만_바꾸고_입력은_보존한다():
    composed = _report(MEDILINE_SENTENCE, grade=GRADE_INTERPRETED)
    rendered = _render(composed, _fragments(MEDILINE_SENTENCE))
    expected = MEDILINE_NORMALIZED + " [1] — 해석"
    assert rendered.sections[0].prose_lines == [(expected, "")]
    assert rendered.sections[0].prose_paragraphs == [expected]
    assert rendered.summary_items[0].text == expected
    assert composed.sections[0].sentences[0].text == MEDILINE_SENTENCE
    assert composed.summary[0].text == MEDILINE_SENTENCE


@pytest.mark.parametrize(("as_of_date", "count"), (("2026-09-22", 1), ("2026-03-01", 0)))
def test_뤼튼_본문과_요약의_동일_일정은_진단_한_건만_남긴다(as_of_date, count, caplog):
    diagnostics = {}
    with caplog.at_level(logging.INFO, logger="src.features.composer.render"):
        rendered = _render(
            _report(WRTN_SENTENCE), _fragments(WRTN_SENTENCE),
            as_of_date=as_of_date, style_diagnostics=diagnostics,
        )
    expected = WRTN_SENTENCE + (" (공시 원문 기준)" if count else "") + " [1]"
    assert rendered.sections[0].prose_lines == [(expected, "")]
    assert rendered.summary_items[0].text == expected
    assert diagnostics == ({"past_dated_future_tense": 1} if count else {})
    records = [record for record in caplog.records if hasattr(record, "style_diagnostics")]
    assert len(records) == count
    if count:
        assert records[0].style_diagnostics == diagnostics
        assert WRTN_SENTENCE not in records[0].getMessage()


def test_원문_인용_자격이_있는_보도는_본문과_요약에서_바뀌지_않는다():
    text = "2026년 3월 승인 예정입니다. 관리를 하고 있습니다."
    fragment = _news_fragment(text)
    candidate = "2026-09-10 mt.co.kr 보도에 따르면, " + text
    assert verbatim_news_source(candidate, ("1",), {"1": fragment}, section_id="business_model")
    composed = _report(candidate, section_id="business_model")
    diagnostics = {}
    rendered = _render(composed, (fragment,), as_of_date="2026-09-22", style_diagnostics=diagnostics)
    section = next(item for item in rendered.sections if item.cell == "business_model")
    assert section.prose_lines == [(candidate + " [1]", "")]
    assert rendered.summary_items[0].text == candidate + " [1]"
    assert diagnostics == {}


def test_보도를_의역한_AI_산문에는_정규화를_적용한다():
    fragment = _news_fragment("서비스를 출시합니다.")
    candidate = "서비스 출시를 진행합니다."
    rendered = _render(_report(candidate, section_id="business_model"), (fragment,))
    assert rendered.summary_items[0].text == "서비스 출시를 진행한다. [1]"


def test_표_칸과_안내문은_정규화하지_않는다():
    notice = "확인된 자료가 없습니다."
    composed = ComposedReport(sections=(
        ComposedSection(
            "business_model", (), notice=notice,
            flow_rows=(FlowRow(("관리합니다.", "생산합니다.", "판매합니다.", "확정됩니다."), ("1",)),),
            news_rows=(NewsRow(("2026-03-01", "매체", WRTN_SENTENCE), ("1",)),),
        ),
        ComposedSection("past_changes", ()),
    ))
    table = PerformanceTable("실적", ("항목", "값"), (("확인합니다.", "있습니다."),), "", "[1]")
    diagnostics = {}
    rendered = render_report(
        "시험회사", composed, _fragments(WRTN_SENTENCE), table,
        as_of_date="2026-09-22", style_diagnostics=diagnostics,
    )
    assert rendered.sections[0].prose_lines == [(notice, "")]
    assert rendered.sections[0].tables[0].rows[0] == list(composed.sections[0].flow_rows[0].cells)
    assert rendered.sections[0].tables[1].rows[0] == list(composed.sections[0].news_rows[0].cells)
    assert rendered.sections[1].tables[0].rows == [["확인합니다.", "있습니다."]]
    assert diagnostics == {}


def test_프로그램이_결속한_문장은_AI_산문으로_고치지_않는다():
    composed = _report("매출 비중은 70%입니다.")
    sentence = replace(composed.summary[0], verified_fact_id="program-fact")
    composed = replace(composed, sections=(ComposedSection("identity", (sentence,)),), summary=(sentence,))
    normalizer = SentenceStyleNormalizer(composed, _fragments(sentence.text), as_of_date="2026-09-22")
    assert normalizer.normalize(sentence) is sentence


def test_요약의_원문_사실_결속은_정규화된_표시와_함께_유효하다():
    sentence = ComposedSentence(
        MEDILINE_SENTENCE, ("1",), GRADE_CONFIRMED,
        planned_claim_slot=CLAIM_SLOTS_BY_SECTION["current_challenges"][0],
        verification_state="verified",
    )
    composed = ComposedReport(sections=(ComposedSection("current_challenges", (sentence,)),))
    fragments = _fragments(MEDILINE_SENTENCE)
    first = _render(composed, fragments)
    fact = first.fact_records[0]
    composed = replace(composed, summary=(replace(sentence, verified_fact_id=fact.fact_id),))
    rendered = _render(composed, fragments)
    item = rendered.summary_items[0]
    assert item.text == MEDILINE_NORMALIZED + " [1]"
    assert rendered.fact_records[0].claim == MEDILINE_SENTENCE
    assert rendered.fact_records[0].evidence_binding == fact_evidence_binding(fact)
    assert item.fact_ids == [fact.fact_id]
    assert item.evidence_text == f"{fact.fact_id}: {MEDILINE_SENTENCE}"
    assert item.verification_binding == summary_verification_binding(
        item.text, item.section_id, item.fact_ids, item.evidence_text,
        item.verification_status, item.support_terms,
    )


@pytest.mark.parametrize("source_kind", ("mediline", "wrtn", "verbatim_news"))
def test_FULL_사전_봉인과_렌더_후_공개지문이_같다(source_kind):
    if source_kind == "verbatim_news":
        fragment = _news_fragment("2026년 3월 승인 예정입니다.")
        text = "2026-09-10 mt.co.kr 보도에 따르면, " + fragment.text
        section_id = "business_model"
    else:
        text = MEDILINE_SENTENCE if source_kind == "mediline" else WRTN_SENTENCE
        fragment = _fragments(text)[0]
        section_id = "identity"
    composed = _report(text, section_id=section_id)
    seal = build_public_structure_seal(
        composed, (fragment,), None, filing_meta=None, composition_tables=(),
        table_presentation="table", company_id="00123456", evidence_generation_sha256="a" * 64,
        evidence_packet_sha256s=tuple((key, str(index) * 64) for index, key in enumerate(SECTION_IDS, 1)),
        company_name="시험회사", corp_type="", generated_at="", as_of_date="2026-09-22",
        analysis_period="", latest_performance_period="", citation_style=CITATION_STYLE_INLINE,
    )
    rendered = _render(
        composed, (fragment,), as_of_date="2026-09-22", company_id="00123456",
        public_structure_seal=seal,
    )
    rendered = replace(
        rendered, grade=Grade.COMPLETE,
        quality_contract_version=STRICT_QUALITY_CONTRACT_VERSION,
        safety_decision=ReleaseDecision.RELEASE_ALLOWED.value,
        publication_policy=PublicationPolicy.STRUCTURED_SAFETY.value,
    )
    assert public_content_digests(rendered)[0] == seal.public_content_sha256
    if source_kind == "verbatim_news":
        assert rendered.summary_items[0].text == text + " [1]"
    else:
        assert rendered.summary_items[0].text != text + " [1]"


def test_FULL_전체_파이프라인에서_문체_변환_뒤_원문_재대조가_실패하지_않는다(monkeypatch):
    from src.features.composer.tests import test_section_public_manifest as fixture

    original = fixture._section_sentence

    def polite_sentence(*args, **kwargs):
        return original(*args, **kwargs).replace("확인했다.", "확인했습니다.")

    monkeypatch.setattr(fixture, "_section_sentence", polite_sentence)
    output, _writer, reviewer, _diagram = fixture._run_full()
    assert any("확인했습니다." in prompt for prompt in reviewer.prompts)
    assert any("확인했습니다." in fact.claim for fact in output.report.fact_records)
    lines = [text for section in output.report.sections for text, _ in section.prose_lines]
    assert any("확인했다." in text for text in lines)
    assert not any("확인했습니다." in text for text in lines)
