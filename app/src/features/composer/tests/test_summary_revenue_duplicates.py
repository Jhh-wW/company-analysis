"""실측 수익 설명 중복과 실제 SHADOW 입력 경계의 보수적 재현.

WRTN_SUMMARY_REPORT_JSON을 지정하면 저장된 검증 사실만 복원한다. 저장본에
없는 검수 상태나 FactRecord는 추정하지 않으므로 전체 생성 실행의 재생은 아니다.
"""

from dataclasses import fields, replace
import json
import os
from pathlib import Path

import pytest

from src.features.composer.constants import (
    CITATION_STYLE_INLINE, CITATION_STYLE_MERGED,
    GRADE_CONFIRMED, GRADE_INTERPRETED, SECTION_IDS,
)
from src.features.composer.extractive_summary import (
    distinct_summary_candidates,
    select_extractive_summary,
)
from src.features.composer.logic import SummaryCandidate
from src.features.composer.pipeline import _legacy_summary_stage, _rule_summary_stage
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, StructuredClaim,
)
from src.features.composer.public_manifest import build_public_structure_seal
from src.features.composer.render import render_report, sentence_display_text
from src.features.composer.structured_claims import NumericSafetyFiltering
from src.features.composer.tests.test_extractive_summary import _fact
from src.features.composer.validate import validate_v2
from src.features.pipeline.port import FactRecord, Grade, SummaryItem
from src.features.storage.reports import report_from_dict, report_to_dict
from src.shared.report_generation.canonical import public_content_digests
from src.shared.report_quality.constants import STRICT_QUALITY_CONTRACT_VERSION
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.models import PublicationPolicy, ReleaseDecision
from src.shared.report_quality.source_identity import document_identity_from_parts
from src.shared.report_quality.summary_binding import summary_evidence_text, summary_verification_binding


COMPANY = "주식회사 뤼튼테크놀로지스"
IDENTITY = "회사의 주된 영업수익은 인공지능 소프트웨어 개발과 관련된 용역 매출과 인공지능 콘텐츠 매출로 구성된다."
PORTFOLIO = "뤼튼테크놀로지스의 영업수익은 인공지능 소프트웨어 개발 용역과 인공지능 콘텐츠 매출 두 가지 형태로 구성된다."
ALTERNATIVE = "회사는 Wrtn Technologies Japan을 종속기업으로 두고 있으며, 당기 중 해당 종속기업에 대여금을 제공하고 영업비용 거래를 수행했다."


def _sentence(text, section="identity", **changes):
    sentence = ComposedSentence(
        text, ("2", "18") if section == "identity" else ("2",), GRADE_CONFIRMED,
        planned_claim_slot=("identity:business_definition" if section == "identity"
                            else "portfolio:product_role"),
        verification_state="verified",
    )
    return replace(sentence, **changes)


def _candidates(first=IDENTITY, second=PORTFOLIO):
    return (
        SummaryCandidate("identity", _sentence(first)),
        SummaryCandidate("portfolio", _sentence(second, "portfolio")),
    )


def test_실측_수익_바꿔쓰기는_같은_인용을_공유할_때만_한번_고른다():
    candidates = _candidates()
    assert distinct_summary_candidates(candidates, company_name=COMPANY) == candidates[:1]
    unrelated = replace(candidates[1], sentence=replace(candidates[1].sentence, citations=("9",)))
    assert distinct_summary_candidates((candidates[0], unrelated), company_name=COMPANY) == (candidates[0], unrelated)
    assert distinct_summary_candidates(candidates, company_name="") == candidates


@pytest.mark.parametrize("first, second", (
    (IDENTITY.replace("인공지능 콘텐츠", "2024년 인공지능 콘텐츠"), PORTFOLIO.replace("인공지능 콘텐츠", "2025년 인공지능 콘텐츠")),
    (IDENTITY.replace("인공지능 콘텐츠", "100억원 인공지능 콘텐츠"), PORTFOLIO.replace("인공지능 콘텐츠", "101억원 인공지능 콘텐츠")),
    (IDENTITY.replace("인공지능 콘텐츠", "10% 인공지능 콘텐츠"), PORTFOLIO.replace("인공지능 콘텐츠", "11% 인공지능 콘텐츠")),
    (IDENTITY.replace("인공지능 콘텐츠", "한 개 인공지능 콘텐츠"), PORTFOLIO.replace("인공지능 콘텐츠", "두 개 인공지능 콘텐츠")),
    (IDENTITY, PORTFOLIO.replace("뤼튼테크놀로지스의", "다른회사의")),
    (IDENTITY.replace("회사의", "자회사의"), PORTFOLIO),
    (IDENTITY, PORTFOLIO.replace("인공지능 콘텐츠", "의료 콘텐츠")),
    (IDENTITY, PORTFOLIO.replace("두 가지", "세 가지")),
    (IDENTITY, PORTFOLIO.replace("구성된다", "구성되지 않는다")),
    (IDENTITY, PORTFOLIO.replace("영업수익은", "영업비용은")),
    (IDENTITY, PORTFOLIO + " 해외 매출이 증가했다."),
    (IDENTITY, PORTFOLIO.replace("구성된다.", "구성될 예정이다.")),
    (IDENTITY.replace("콘텐츠", "'Crack' 콘텐츠"), PORTFOLIO.replace("콘텐츠", "'Crack Japan' 콘텐츠")),
))
def test_수치_시기_회사_주제_부정_추가사실은_합치지_않는다(first, second):
    candidates = _candidates(first, second)
    assert distinct_summary_candidates(candidates, company_name=COMPANY) == candidates


@pytest.mark.parametrize("reverse", (False, True))
def test_확인_우선과_다른_사실이_있는_장의_대체_후보를_보존한다(reverse):
    first, second = _candidates()
    alternative = SummaryCandidate("identity", _sentence(ALTERNATIVE, citations=("4",)))
    candidates = (first, second, alternative)
    if reverse:
        candidates = tuple(reversed(candidates))
    distinct = distinct_summary_candidates(candidates, company_name=COMPANY)
    assert first not in distinct
    assert second in distinct and alternative in distinct
    interpreted = replace(second, sentence=replace(second.sentence, grade=GRADE_INTERPRETED))
    assert distinct_summary_candidates((first, interpreted, alternative), company_name=COMPANY) == (first, alternative)


def _body(with_alternative=True):
    first, second = _candidates()
    identity = (first.sentence,)
    if with_alternative:
        identity += (_sentence(ALTERNATIVE, citations=("4",)),)
    return ComposedReport((
        ComposedSection("identity", identity),
        ComposedSection("portfolio", (second.sentence,)),
        ComposedSection("current_challenges", (
            ComposedSentence("회사는 사용자 보호 정책을 강화하고 있다.", ("8",), GRADE_CONFIRMED,
                             planned_claim_slot="current_challenges:issue", verification_state="verified"),
        )),
    ))


@pytest.mark.parametrize("response", ('{"번호": [1, 2, 3, 4]}', '{}'))
def test_SHADOW_선택과_보충은_안전한_다른_본문을_그대로_고른다(response):
    report = _body()
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        return response

    final, _, filtering = _legacy_summary_stage(
        report, writer_ask=ask, body_numeric_filtering=NumericSafetyFiltering(), company_name=COMPANY,
    )
    assert len(prompts) == 1
    assert IDENTITY not in prompts[0]
    assert {sentence.text for sentence in final.summary} == {ALTERNATIVE, PORTFOLIO, report.sections[2].sentences[0].text}
    assert final.sections == report.sections
    assert filtering.removed_total == 0
    assert all(any(sentence is original for section in report.sections for original in section.sentences) for sentence in final.summary)
    fallback, _ = _rule_summary_stage(report, NumericSafetyFiltering(), company_name=COMPANY)
    assert {sentence.text for sentence in fallback.summary} == {sentence.text for sentence in final.summary}


def test_대체가_없으면_유료_선택_호출이나_세번째_사실을_억지로_만들지_않는다():
    report = _body(with_alternative=False)

    def forbidden_ask(_prompt):
        pytest.fail("두 장만 남으면 요약 AI를 부르면 안 된다")

    final, count, _ = _legacy_summary_stage(
        report, writer_ask=forbidden_ask, body_numeric_filtering=NumericSafetyFiltering(), company_name=COMPANY,
    )
    assert count == 0
    assert len(final.summary) == 2
    assert len({sentence.text for sentence in final.summary} & {IDENTITY, PORTFOLIO}) == 1


@pytest.mark.parametrize("style", (CITATION_STYLE_INLINE, CITATION_STYLE_MERGED))
@pytest.mark.parametrize("with_alternative", (False, True))
def test_중복_제거_후_두개나_세개_요약의_렌더와_공개_봉인이_일치한다(style, with_alternative):
    body = _body(with_alternative)
    by_section = {section.section_id: section for section in body.sections}
    body = replace(body, sections=tuple(by_section.get(sid, ComposedSection(sid, ())) for sid in SECTION_IDS))
    # 합성 근거만 사용한다. 실제 저장본에서 빠진 원문을 복원하지 않는다.
    fragments = tuple(
        CollectedFragment(number, "사업내용", text,
            source_url="https://company.example/report",
            document_identity=document_identity_from_parts(url="https://company.example/report"))
        for number, text in (
            ("2", IDENTITY + " " + PORTFOLIO), ("18", IDENTITY),
            ("4", ALTERNATIVE), ("8", by_section["current_challenges"].sentences[0].text),
        )
    )
    final, _, _ = _legacy_summary_stage(body, writer_ask=lambda _: '{}',
        body_numeric_filtering=NumericSafetyFiltering(), company_name=COMPANY)
    seal = build_public_structure_seal(final, fragments, None,
        filing_meta=None, composition_tables=(), table_presentation="table",
        company_id="00123456", evidence_generation_sha256="a" * 64,
        evidence_packet_sha256s=tuple((sid, str(index) * 64) for index, sid in enumerate(SECTION_IDS, 1)),
        company_name=COMPANY, corp_type="", generated_at="", as_of_date="2026-09-23",
        analysis_period="", latest_performance_period="", citation_style=style)
    rendered = render_report(COMPANY, final, fragments, None,
        citation_style=style, as_of_date="2026-09-23", company_id="00123456",
        public_structure_seal=seal)
    # 봉인 대조용 합성 출력 상태만 맞춘다. 실제 2개 요약의 출고 정책을
    # 엄격 모드로 바꾸거나 하한을 완화하는 검사가 아니다.
    sealed_view = replace(rendered, grade=Grade.COMPLETE,
        quality_contract_version=STRICT_QUALITY_CONTRACT_VERSION,
        safety_decision=ReleaseDecision.RELEASE_ALLOWED.value,
        publication_policy=PublicationPolicy.STRUCTURED_SAFETY.value)
    assert public_content_digests(sealed_view) == (seal.public_content_sha256, seal.section_sha256s)
    assert len(rendered.summary_items) == (3 if with_alternative else 2)
    for item in rendered.summary_items:
        assert item.fact_ids and item.verification_status == "verified"
        assert item.verification_binding == summary_verification_binding(
            item.text, item.section_id, item.fact_ids, item.evidence_text,
            item.verification_status, item.support_terms)


def _bound_facts(report):
    facts = []
    for section in report.sections:
        for sentence in section.sentences:
            fact = replace(_fact(section.section_id, sentence), fact_id=f"fact-{len(facts)}", legal_entity=COMPANY, subject_scope=COMPANY)
            facts.append(replace(fact, evidence_binding=fact_evidence_binding(fact)))
    return facts


def test_엄격_경로도_서로_다른_검증_사실을_고르며_봉인은_그대로다():
    report = _body()
    facts = _bound_facts(report)
    before = tuple(fact.evidence_binding for fact in facts)
    selected = select_extractive_summary(report, facts)
    assert selected.release_ready
    assert {sentence.text for sentence in selected.sentences} == {ALTERNATIVE, PORTFOLIO, report.sections[2].sentences[0].text}
    assert tuple(fact.evidence_binding for fact in facts) == before
    assert all(fact_evidence_binding(fact) == fact.evidence_binding for fact in facts)


@pytest.mark.parametrize("field, value", (
    ("subject_scope", "자회사"), ("time_state", "past"), ("as_of", "2025-09-23"),
    ("fiscal_year", 2024), ("event_date", "2025-03-20"), ("period_start", "2024"),
    ("period_end", "2025"), ("legal_entity", "다른회사"),
))
def test_엄격_경로는_사실의_대상이나_시점이_다르면_보존한다(field, value):
    report = _body(with_alternative=False)
    facts = _bound_facts(report)
    changed = replace(facts[1], **{field: value})
    facts[1] = replace(changed, evidence_binding=fact_evidence_binding(changed))
    selected = select_extractive_summary(report, facts)
    assert {IDENTITY, PORTFOLIO} <= {sentence.text for sentence in selected.sentences}


def _stored_boundary(payload):
    """저장된 사실·구조화 증명만 읽는다. 본문의 미결속 문장을 승격하지 않는다."""
    facts = [FactRecord(**raw) for raw in payload["fact_records"]]
    by_id = {fact.fact_id: fact for fact in facts}
    sections = []
    for section in payload["sections"]:
        sentences = []
        for fact_id in section["fact_ids"]:
            fact = by_id[fact_id]
            assert fact.evidence_binding == fact_evidence_binding(fact)
            assert any(fact.claim in line[0] for line in section["lines"])
            citations = tuple(source.removeprefix("v2-frag-") for source in fact.supporting_source_ids)
            structured = None
            if fact.numeric_checks:
                values = {field.name: getattr(fact, field.name) for field in fields(StructuredClaim) if hasattr(fact, field.name)}
                values.update(source_fragment_id=citations[0], source_identity=fact.supporting_source_identities[0], verification_state=fact.verification_status)
                values["numeric_checks"] = tuple(fact.numeric_checks)
                structured = StructuredClaim(**values)
            sentences.append(ComposedSentence(fact.claim, citations, GRADE_CONFIRMED,
                planned_claim_slot=fact.claim_slot, verification_state=fact.verification_status,
                structured_claim=structured))
        sections.append(ComposedSection(section["cell"], tuple(sentences)))
    return ComposedReport(tuple(sections)), facts


@pytest.mark.local_integration
def test_실제_저장본_SHADOW_경계에서_중복을_재현하고_안전하게_제거한다():
    path = os.environ.get("WRTN_SUMMARY_REPORT_JSON")
    if not path:
        pytest.skip("WRTN_SUMMARY_REPORT_JSON으로 승인된 실제 보고서 JSON을 지정")
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    pdf_text = " ".join((Path(path).parent / "wrtn-pdf-review" / "text.txt").read_text(encoding="utf-8").split())
    assert IDENTITY in pdf_text and PORTFOLIO in pdf_text
    report, facts = _stored_boundary(payload)
    original_bindings = tuple(fact.evidence_binding for fact in facts)
    assert payload.get("release_mode", "") in ("", "shadow")
    assert payload["summary_items"][0]["text"].startswith(IDENTITY)
    assert payload["summary_items"][2]["text"].startswith(PORTFOLIO)
    # 회사 맥락이 없는 호환 기본값은 기존 경로와 같은 후보를 내보낸다.
    # 고를 번호만 응답하는 무료 stub으로 배포본의 선택 순서를 재현한다.
    def original_selection(prompt):
        lines = prompt.splitlines()
        texts = (IDENTITY, facts[4].claim, PORTFOLIO)
        numbers = [int(next(line for line in lines if line.endswith(text)).split(".", 1)[0]) for text in texts]
        return json.dumps({"번호": numbers})

    before, _, _ = _legacy_summary_stage(report, writer_ask=original_selection, body_numeric_filtering=NumericSafetyFiltering())
    assert tuple(sentence.text for sentence in before.summary) == (IDENTITY, facts[4].claim, PORTFOLIO)

    def no_call(_prompt):
        pytest.fail("실제 검증 사실만으로는 중복 제거 뒤 세 장을 채울 수 없다")

    after, count, filtering = _legacy_summary_stage(report, writer_ask=no_call,
        body_numeric_filtering=NumericSafetyFiltering(), company_name=payload["company"])
    assert count == 0 and len(after.summary) == 2
    assert len({sentence.text for sentence in after.summary} & {IDENTITY, PORTFOLIO}) == 1
    assert filtering.removed_total == 0
    assert tuple(fact.evidence_binding for fact in facts) == original_bindings
    assert all(any(sentence is original for section in report.sections for original in section.sentences) for sentence in after.summary)
    # 이 실측본은 이미 evidence-available 정책이다. renderer의 공개 문장·
    # 요약 결속 함수를 이용하되 새 FactRecord/정책/원문 증거는 만들지 않는다.
    # 원문 전체가 저장본에 없으므로 전체 render 재생은 위 합성 시험이 맡는다.
    stored = report_from_dict(payload)
    by_id = {fact.fact_id: fact for fact in facts}
    numbers = {source.source_id.removeprefix("v2-frag-"): source.number for source in stored.citations}
    summaries = []
    for sentence in after.summary:
        fact = next(fact for fact in facts if fact.claim == sentence.text)
        item = SummaryItem(text=sentence_display_text(sentence, numbers),
            section_id=fact.section_owner, fact_ids=[fact.fact_id],
            evidence_text=summary_evidence_text([fact.fact_id], by_id),
            verification_status=fact.verification_status,
            support_terms=list(dict.fromkeys(fact.evidence_support_terms)))
        summaries.append(replace(item, verification_binding=summary_verification_binding(
            item.text, item.section_id, item.fact_ids, item.evidence_text,
            item.verification_status, item.support_terms)))
    projected = replace(stored, summary_items=summaries)
    assert projected.publication_policy == PublicationPolicy.EVIDENCE_AVAILABLE.value
    validate_v2(projected)
    restored = report_from_dict(report_to_dict(projected))
    validate_v2(restored)
    assert restored.summary_items == summaries
    assert projected.fact_records == stored.fact_records
    assert projected.citations == stored.citations
    for item in summaries:
        assert item.verification_binding == summary_verification_binding(
            item.text, item.section_id, item.fact_ids, item.evidence_text,
            item.verification_status, item.support_terms)
