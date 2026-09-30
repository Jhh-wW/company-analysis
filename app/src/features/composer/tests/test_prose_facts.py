from __future__ import annotations

from dataclasses import asdict, replace
import hashlib
import json

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.prose_facts import (
    ProseEvidence, build_verified_prose_fact, evaluate_verified_prose_fact,
)
from src.features.composer.render import render_report
from src.features.storage.reports import report_from_json, report_to_json
from src.features.provenance.sources import (
    Source,
    SourceKind,
    exact_evidence_text_hash,
)
from src.shared.report_quality.assessment import assess_safety
from src.shared.report_quality.contract import contract_for_generation
from src.shared.report_quality.dto import (
    ClaimFact,
    ReportCandidate,
    ReportSectionCandidate,
    SourceDocument,
)
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.models import ReleaseDecision
from src.shared.report_quality.source_identity import document_identity


def _source(number: int, text: str) -> Source:
    return Source(
        number=number,
        kind=SourceKind.OTHER,
        label=f"공식 자료 {number}",
        source_id=f"source-{number}",
        title=f"공식 문서 {number}",
        publisher="예시회사",
        url=f"https://example.com/document/{number}",
        exact_evidence_hashes=[exact_evidence_text_hash(text)],
    )


def _sentence() -> ComposedSentence:
    return ComposedSentence(
        text="예시회사는 직접 판매 채널과 공식 제휴 채널을 함께 운영한다.",
        citations=("1", "2"),
        grade=GRADE_CONFIRMED,
        planned_claim_slot="business_model:sales_channel",
        verification_state="verified",
    )


def _evidence() -> tuple[ProseEvidence, ...]:
    texts = (
        "예시회사는 고객에게 제품을 직접 판매하는 공식 온라인 채널을 운영한다.",
        "공식 제휴사는 예시회사의 제품을 고객에게 판매하는 채널이다.",
    )
    return tuple(
        ProseEvidence(str(index), _source(index, text), text)
        for index, text in enumerate(texts, start=1)
    )


def test_검증된_일반문장은_모든_인용의_신원과_원문해시에_결속된다() -> None:
    fact = build_verified_prose_fact(
        _sentence(),
        section_id="business_model",
        company_name="예시회사",
        as_of_date="2026-08-31",
        evidence=_evidence(),
    )

    assert fact is not None
    assert fact.supporting_source_ids == ["source-1", "source-2"]
    assert fact.source_id == fact.supporting_source_ids[0]
    assert fact.supporting_source_identities == [
        document_identity(item.source) for item in _evidence()
    ]
    assert fact.supporting_evidence_hashes == [
        exact_evidence_text_hash(item.exact_text) for item in _evidence()
    ]
    assert fact.evidence_binding == fact_evidence_binding(fact)


def _actor_evidence() -> ProseEvidence:
    exact = "정밀 센서모듈을 제조하여 고객에게 공급한다."
    context_text = "센서모듈 | 다온제조 | " + exact
    owner = "예시회사"
    start = len(owner) + 1
    sha = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    context = json.dumps({
        "version": "source-context-v1", "origin": "table_row",
        "text": context_text, "location": f"{start}-{start + len(context_text)}",
        "text_sha256": sha(context_text), "actor": "다온제조", "status": "",
        "document_actor": owner, "document_actor_location": f"0-{len(owner)}",
        "document_actor_sha256": sha(owner),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return ProseEvidence("1", _source(1, exact), exact, context)


def test_explicit_source_actor_survives_fact_scope_and_binding():
    evidence = _actor_evidence()
    sentence = replace(_sentence(), text="다온제조는 " + evidence.exact_text,
                       citations=("1",), planned_claim_slot="operations_partners:operating_role")
    fact = build_verified_prose_fact(
        sentence, section_id="operations_partners", company_name="예시회사",
        as_of_date="2026-09-30", evidence=(evidence,),
    )
    assert fact is not None
    assert fact.legal_entity == "예시회사"
    assert fact.subject_scope == "다온제조"
    assert fact.evidence_binding == fact_evidence_binding(fact)
    record, = json.loads(fact.state_evidence)
    assert record["source_context_sha256"] == hashlib.sha256(evidence.source_context_json.encode()).hexdigest()
    assert record["exact_sha256"] == exact_evidence_text_hash(evidence.exact_text)


def test_target_or_omitted_subject_cannot_reenter_during_fact_build():
    evidence = _actor_evidence()
    for prefix in ("회사는 ", "예시회사는 ", "", "계열사 다온제조는 "):
        result = evaluate_verified_prose_fact(
            replace(_sentence(), text=prefix + evidence.exact_text, citations=("1",),
                    planned_claim_slot="operations_partners:operating_role"),
            section_id="operations_partners", company_name="예시회사",
            as_of_date="2026-09-30", evidence=(evidence,),
        )
        assert result.fact is None
        assert result.reason_code == "prose_source_actor_scope_unbound"


def test_empty_context_keeps_existing_fact_bytes():
    arguments = dict(section_id="business_model", company_name="예시회사", as_of_date="2026-08-31")
    original = build_verified_prose_fact(_sentence(), evidence=_evidence(), **arguments)
    # 문맥 필드 추가 전 817606 구현으로 만든 동일 fixture의 정본 바이트다.
    payload = json.dumps(asdict(original), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert hashlib.sha256(payload.encode()).hexdigest() == "1d60051ff70c6ea53c764511904f9c70df7c5567ecc1da27cae66c38d5490175"
    assert "source_context" not in original.state_evidence


def test_render_carries_actual_actor_into_published_fact():
    from src.features.composer.port import CollectedFragment
    evidence = _actor_evidence()
    sentence = replace(_sentence(), text="다온제조는 " + evidence.exact_text,
                       citations=("1",), planned_claim_slot="operations_partners:operating_role")
    fragment = CollectedFragment(
        "1", "공시", evidence.exact_text,
        source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260102000001",
        document_title="사업보고서", source_context_json=evidence.source_context_json,
    )
    report = render_report(
        "예시회사", ComposedReport((ComposedSection("operations_partners", (sentence,)),)),
        (fragment,), None,
    )
    fact, = report.fact_records
    assert fact.subject_scope == "다온제조"
    assert fact.claim == sentence.text
    assert json.loads(fact.state_evidence)[0]["source_context_sha256"]


def test_prose_builder_reports_missing_slot_without_inventing_one():
    result = evaluate_verified_prose_fact(
        replace(_sentence(), planned_claim_slot=""), section_id="business_model",
        company_name="예시회사", as_of_date="2026-08-31", evidence=_evidence(),
    )
    assert result.fact is None
    assert result.reason_code == "prose_claim_slot_missing"


def test_future_plan_in_past_section_is_not_sealed_as_completed_execution():
    from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
    text = "예시회사는 보호 정책을 강화할 계획을 밝혔다."
    sentence = replace(_sentence(), text=text, citations=("1",),
                       planned_claim_slot=CLAIM_SLOTS_BY_SECTION["past_changes"][0])
    fact = build_verified_prose_fact(
        sentence, section_id="past_changes", company_name="예시회사", as_of_date="2026-08-31",
        evidence=(ProseEvidence("1", _source(1, text), text),),
    )
    assert fact is not None
    assert fact.time_state == "future"
    assert fact.fact_status == "provisional"
    assert fact.evidence_binding == fact_evidence_binding(fact)


def test_completed_planning_activity_remains_actual():
    from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
    text = "예시회사는 보호 정책 계획을 수립했다."
    fact = build_verified_prose_fact(
        replace(_sentence(), text=text, citations=("1",),
                planned_claim_slot=CLAIM_SLOTS_BY_SECTION["past_changes"][0]),
        section_id="past_changes", company_name="예시회사", as_of_date="2026-08-31",
        evidence=(ProseEvidence("1", _source(1, text), text),),
    )
    assert fact is not None and fact.time_state == "past" and fact.fact_status == "actual"


def test_optional_section_observation_survives_report_storage():
    from src.shared.report_quality.generation import assess_and_observe_generation
    report = render_report("예시회사", ComposedReport((ComposedSection("competitive_position", ()),)), {}, None)
    _, observation = assess_and_observe_generation(ReportCandidate(
        sections=(ReportSectionCandidate("competitive_position", (), public_sentence_count=0, notice_only=True),),
        facts=(), sources=(),
    ))
    report = replace(report, quality_observation=observation)
    restored = report_from_json(report_to_json(report))
    assert restored.quality_observation == observation


def test_인용순서나_원문바이트가_바뀌면_같은_사실로_가장할수없다() -> None:
    sentence = _sentence()
    evidence = _evidence()
    original = build_verified_prose_fact(
        sentence,
        section_id="business_model",
        company_name="예시회사",
        as_of_date="2026-08-31",
        evidence=evidence,
    )
    reordered = build_verified_prose_fact(
        replace(sentence, citations=("2", "1")),
        section_id="business_model",
        company_name="예시회사",
        as_of_date="2026-08-31",
        evidence=tuple(reversed(evidence)),
    )
    changed_text = replace(evidence[1], exact_text=evidence[1].exact_text + " 변경")

    assert original is not None and reordered is not None
    assert reordered.fact_id != original.fact_id
    assert (
        build_verified_prose_fact(
            sentence,
            section_id="business_model",
            company_name="예시회사",
            as_of_date="2026-08-31",
            evidence=(evidence[0], changed_text),
        )
        is None
    )


def test_미검증_또는_계획밖_문장은_사실장부를_만들지않는다() -> None:
    for sentence in (
        replace(_sentence(), verification_state="unverified"),
        replace(_sentence(), planned_claim_slot="business_model:없는자리"),
        replace(_sentence(), citations=()),
    ):
        assert (
            build_verified_prose_fact(
                sentence,
                section_id="business_model",
                company_name="예시회사",
                as_of_date="2026-08-31",
                evidence=_evidence(),
            )
            is None
        )


def test_검수표식만_참이고_원문과_무관한_문장은_사실이_되지않는다() -> None:
    unrelated = replace(
        _sentence(),
        text="화성 탐사선의 착륙 방식은 대기 밀도에 따라 달라진다.",
    )

    assert (
        build_verified_prose_fact(
            unrelated,
            section_id="business_model",
            company_name="예시회사",
            as_of_date="2026-08-31",
            evidence=_evidence(),
        )
        is None
    )


def test_품질안전검사는_두번째_출처까지_따로_대조한다() -> None:
    evidence = _evidence()
    fact = build_verified_prose_fact(
        _sentence(),
        section_id="business_model",
        company_name="예시회사",
        as_of_date="2026-08-31",
        evidence=evidence,
    )
    assert fact is not None
    claim = ClaimFact(
        fact_id=fact.fact_id,
        section_owner=fact.section_owner,
        source_id=fact.source_id,
        source_identity=fact.supporting_source_identities[0],
        verification_state=fact.verification_status,
        claim_slot=fact.claim_slot,
        evidence_binding_valid=fact.evidence_binding == fact_evidence_binding(fact),
        claim=fact.claim,
        supporting_source_ids=tuple(fact.supporting_source_ids),
        supporting_source_identities=tuple(fact.supporting_source_identities),
        supporting_evidence_hashes=tuple(fact.supporting_evidence_hashes),
    )
    sources = tuple(
        SourceDocument(
            source_id=item.source.source_id,
            document_identity=document_identity(item.source),
            exact_evidence_hashes=tuple(item.source.exact_evidence_hashes),
        )
        for item in evidence
    )
    candidate = ReportCandidate(
        sections=(
            ReportSectionCandidate(
                "business_model", (fact.fact_id,), public_sentence_count=1
            ),
        ),
        facts=(claim,),
        sources=sources,
    )

    assert (
        assess_safety(candidate, contract_for_generation()).decision
        is ReleaseDecision.RELEASE_ALLOWED
    )
    tampered = replace(
        candidate,
        sources=(
            sources[0],
            replace(sources[1], exact_evidence_hashes=("0" * 64,)),
        ),
    )
    result = assess_safety(tampered, contract_for_generation())
    assert result.decision is ReleaseDecision.BLOCKED
    assert any("원문 조각 해시" in problem for problem in result.problems)


def test_render가_검증문장과_실제_부록출처를_같은_장부로_만든다() -> None:
    sentence = ComposedSentence(
        text="예시회사는 공식 온라인 판매 채널을 운영한다.",
        citations=("1",),
        grade=GRADE_CONFIRMED,
        planned_claim_slot="business_model:sales_channel",
        verification_state="verified",
    )
    report = render_report(
        "예시회사",
        ComposedReport(
            sections=(ComposedSection("business_model", (sentence,)),),
            summary=(sentence,),
        ),
        {
            1: {
                "종류": "공식 홈페이지",
                "원문": "예시회사는 공식 온라인 판매 채널을 운영한다.",
                "출처": "https://example.com/business",
                "문서명": "사업 소개",
            }
        },
        None,
        as_of_date="2026-08-31",
    )

    assert len(report.fact_records) == 1
    assert report.sections[0].fact_ids == [report.fact_records[0].fact_id]
    assert report.fact_records[0].supporting_source_ids == ["v2-frag-1"]
    assert report.citations[0].exact_evidence_hashes
    restored = report_from_json(report_to_json(report))
    assert restored.fact_records == report.fact_records
    assert restored.citations == report.citations
