"""공개 명시 정의와 typed 결속을 무료 대역으로 재현하는 회귀 시험."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.features.pipeline import real
from src.features.pipeline.news_research_context import news_generation_digest
from src.features.pipeline.official_news_aliases import official_news_aliases
from src.shared.company_identity import exact_company_name_key
from src.shared.report_evidence.constants import (
    EvidenceReadiness,
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.identity_verified_web import (
    build_dart_filing_url_provenance,
    build_verified_dart_filing_official_web_binding,
)
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates,
    CollectedEvidenceDocument,
    DocumentTextRange,
    EvidenceFragment,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS, collector_slots_for
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult


# 공식 뉴스룸의 2026-09-02 보도자료에 실제로 있는 연속 문구다.
# https://wrtn.io/news/뤼튼테크놀로지스-상장-주관사-선정-착수/
# 아래 법인 ID·typed 영수증은 시험용이며 운영 수집 원본이라고 주장하지 않는다.
CORP_ID = "00123456"
LEGAL_NAME = "뤼튼테크놀로지스"
DEFINITION = "뤼튼테크놀로지스(이하 뤼튼, 대표 이세영)"
PUBLIC_TEXT = f"AI(인공지능) 서비스 플랫폼 기업 ‘{DEFINITION}’가 상장 준비에 본격 착수했다."
OFFICIAL_URL = "https://wrtn.io/news/"
PROFILE = {"status": "000", "corp_code": CORP_ID, "corp_name": LEGAL_NAME,
           "hm_url": "https://wrtn.io", "bizr_no": "202-81-67042"}
AS_OF = dt.date(2026, 9, 22)
RECEIPT = "20260414000008"
ARTICLE_URL = "https://www.edaily.co.kr/News/Read?newsId=02007366645447608"
ARTICLE = "뤼튼은 생성형 AI 플랫폼을 운영하며 기업용 AI 서비스를 새롭게 출시했다."


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def evidence(text=PUBLIC_TEXT, *, kind=SOURCE_KIND_OFFICIAL_WEB_PAGE, parts=None, section="identity"):
    texts = parts or (text,)
    is_web = kind == SOURCE_KIND_OFFICIAL_WEB_PAGE
    url = OFFICIAL_URL if is_web else f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={RECEIPT}"
    document_id = "official-newsroom" if is_web else f"dart:{RECEIPT}"
    ranges, offset = [], 0
    for value in texts:
        ranges.append(DocumentTextRange(offset, offset + len(value)))
        offset += len(value) + 1
    document = CollectedEvidenceDocument(
        company_id=CORP_ID, document_id=document_id, canonical_url=url,
        source_tier=SourceTier.TIER_1_OFFICIAL, source_kind=kind,
        publisher=LEGAL_NAME if is_web else "금융감독원", title="공식 원문 재현",
        published_on="2026-09-02", collected_at="2026-09-22T00:00:00Z",
        content_sha256=sha("\n".join(texts)), exact_evidence_hashes=tuple(dict.fromkeys(map(sha, texts))),
        identity_binding=("DART 기업개황 홈페이지 주소(root)" if is_web else
                          f"corp_code={CORP_ID};rcept_no={RECEIPT};source_kind={kind};identity_check=verified_match"),
        usable_ranges=tuple(ranges), collector_version="시험-수집기", parser_version="시험-파서",
        requirement=SourceRequirement.REQUIRED,
        domain_attestation_source_id=f"dart-company-profile-{CORP_ID}" if is_web else "",
        domain_attestation_evidence=json.dumps(
            {key: PROFILE[key] for key in ("corp_code", "corp_name", "hm_url")},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ) if is_web else "",
    )
    fragments = tuple(EvidenceFragment(
        company_id=CORP_ID, fragment_id=f"fragment-{index}", document_id=document_id,
        location=f"{url}#{index}" if is_web else f"{ranges[index].start}-{ranges[index].end}",
        text_sha256=sha(value), text=value, section_id=section,
        slot_id=collector_slots_for(section)[0], score_millis=900, reason_codes=("test_exact_text",),
    ) for index, value in enumerate(texts))
    return OfficialEvidenceCollectionResult(company_id=CORP_ID, candidates=tuple(
        ChapterEvidenceCandidates(
            company_id=CORP_ID, section_id=key,
            documents=(document,) if key == section else (),
            fragments=fragments if key == section else (), attempts=(),
            candidate_readiness=EvidenceReadiness.READY if key == section else EvidenceReadiness.UNKNOWN,
            reason_codes=(), estimated_tokens=0, max_chars=10000, max_estimated_tokens=10000,
        ) for key in REQUIRED_EVIDENCE_SECTION_IDS
    ))


def change_document(result, **changes):
    candidate = result.candidates[0]
    return replace(result, candidates=(replace(candidate, documents=(replace(candidate.documents[0], **changes),)),
                                       *result.candidates[1:]))


def test_public_definition_keeps_exact_source_and_does_not_use_truncated_context():
    text = "공식 사업 원문입니다. " * 70 + PUBLIC_TEXT
    result = evidence(text, section="future_strategy")
    aliases = official_news_aliases(PROFILE, result)
    assert len(aliases) == 1
    proof = aliases[0]
    assert proof.alias == "뤼튼"
    assert text[proof.definition_start:proof.definition_end] == proof.definition_text == DEFINITION
    assert proof.definition_sha256 == "93e4cb71525659c561dbaae252a4eefd5da5bb5a578afb07beb25ab9df713dc5"
    assert proof.source_snapshot_sha256 == result.source_snapshot_sha256
    assert proof.document_sha256 == proof.fragment_sha256 == sha(text)
    assert proof.location == OFFICIAL_URL + "#0"


@pytest.mark.parametrize("definition", [
    '뤼튼테크놀로지스(이하 "뤼튼")', "뤼튼테크놀로지스(이하 ‘뤼튼’, 대표 이세영)",
    "뤼튼테크놀로지스(이하 '뤼튼')", "주식회사 뤼튼테크놀로지스(이하 뤼튼)",
])
def test_only_exact_legal_name_and_paired_alias_quotes_are_supported(definition):
    assert official_news_aliases(PROFILE, evidence(definition))[0].alias == "뤼튼"


@pytest.mark.parametrize("text", [
    "뤼튼테크놀로지스(이하 회사)", "뤼튼테크놀로지스(이하 당사)",
    "뤼튼테크놀로지스(이하 그룹)", "뤼튼테크놀로지스(이하 서비스)",
    "뤼튼테크놀로지스재팬(이하 뤼튼)", "서울뤼튼테크놀로지스(이하 뤼튼)",
    "뤼튼테크놀로지스가 운영하는 서비스 뤼튼", "크랙(Crack)은 뤼튼테크놀로지스의 제품이다.",
    "뤼튼(이하 뤼튼AI)", '뤼튼테크놀로지스(이하 "뤼튼’)',
    "뤼튼테크놀로지스(이하 뤼튼, 크랙)", "뤼튼테크놀로지스(이하 뤼튼",
    "뤼튼테크놀로지스(이하 뤼튼)라는 표기는 잘못됐다.",
    "'뤼튼테크놀로지스(이하 뤼튼)'라는 약칭을 사용하지 않는다.",
    "다른 법인 '뤼튼테크놀로지스(이하 뤼튼)'를 말한다.",
    "자회사 '뤼튼테크놀로지스(이하 뤼튼)'의 사업이다.",
    "제품명 '뤼튼테크놀로지스(이하 뤼튼)'은 브랜드다.",
    "과거 '뤼튼테크놀로지스(이하 뤼튼)'라는 가칭을 사용했다.",
    "뤼튼테크놀로지스(이하 뤼튼)로 표기하지 말라.",
    "뤼튼테크놀로지스(이하 뤼튼)가 아닌 다른 법인을 뜻한다.",
    "뤼튼테크놀로지스(이하 뤼튼)라는 정의는 없다.",
])
def test_no_inferred_brand_subsidiary_generic_negative_or_partial_alias(text):
    assert official_news_aliases(PROFILE, evidence(text)) == ()


def test_split_or_conflicting_definitions_are_not_reassembled_or_chosen():
    assert official_news_aliases(PROFILE, evidence(parts=("뤼튼테크놀로지스(이하 ", "뤼튼)"))) == ()
    assert official_news_aliases(PROFILE, evidence(parts=(DEFINITION, "뤼튼테크놀로지스(이하 다른약칭)"))) == ()


@pytest.mark.parametrize("profile", [
    {**PROFILE, "corp_code": "00999999"}, {**PROFILE, "corp_name": "뤼튼테크놀로지스재팬"},
    {**PROFILE, "hm_url": "https://unrelated.example"}, {**PROFILE, "status": "013"},
    {"corp_name": LEGAL_NAME},
])
def test_other_corporation_or_profile_contract_cannot_supply_alias(profile):
    assert official_news_aliases(profile, evidence()) == ()


@pytest.mark.parametrize("field,value", [
    ("identity_binding", ""), ("content_sha256", "broken"),
    ("exact_evidence_hashes", (sha("다른 원문"),)), ("company_id", "00999999"),
    ("source_kind", "news"), ("source_kind", "official_unverified_page"),
    ("source_tier", SourceTier.TIER_3_TRUSTED),
])
def test_mutated_typed_document_is_rejected_even_if_attribute_names_look_right(field, value):
    result = evidence()
    object.__setattr__(result.candidates[0].documents[0], field, value)
    assert official_news_aliases(PROFILE, result) == ()


@pytest.mark.parametrize("field,value", [
    ("text", "만들어 낸 약칭 정의"), ("location", OFFICIAL_URL + "#99"),
    ("location", "https://unrelated.example/#0"), ("company_id", "00999999"),
    ("document_id", "없는-문서"),
])
def test_fragment_hash_company_document_and_location_are_bound(field, value):
    result = evidence()
    fragment = result.candidates[0].fragments[0]
    object.__setattr__(fragment, field, value)
    assert official_news_aliases(PROFILE, result) == ()


def test_well_typed_but_wrong_range_or_attestation_still_fails():
    result = evidence()
    assert official_news_aliases(PROFILE, change_document(
        result, usable_ranges=(DocumentTextRange(0, len(PUBLIC_TEXT) - 1),),
    )) == ()
    assert official_news_aliases(PROFILE, change_document(
        result, domain_attestation_source_id="", domain_attestation_evidence="",
    )) == ()
    other_profile = {key: PROFILE[key] for key in ("corp_code", "corp_name", "hm_url")}
    other_profile["corp_code"] = "00999999"
    assert official_news_aliases(PROFILE, change_document(
        result, domain_attestation_source_id="dart-company-profile-00999999",
        domain_attestation_evidence=json.dumps(other_profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
    )) == ()
    assert official_news_aliases(PROFILE, SimpleNamespace(**vars(result))) == ()


def test_dart_requires_verified_current_company_receipt_and_exact_range():
    result = evidence(DEFINITION, kind=SOURCE_KIND_DART_AUDIT_REPORT)
    assert official_news_aliases(PROFILE, result)[0].alias == "뤼튼"
    document = result.candidates[0].documents[0]
    for binding in (
        document.identity_binding.replace("verified_match", "unverifiable_no_fetcher_metadata"),
        document.identity_binding.replace(CORP_ID, "00999999"),
        document.identity_binding.replace(RECEIPT, "20260414000009"),
    ):
        assert official_news_aliases(PROFILE, change_document(result, identity_binding=binding)) == ()
    assert official_news_aliases(PROFILE, change_document(result, canonical_url=OFFICIAL_URL)) == ()
    assert official_news_aliases(PROFILE, evidence('뤼튼테크놀로지스(이하 "회사")', kind=SOURCE_KIND_DART_AUDIT_REPORT)) == ()


def test_cross_domain_keeps_exact_company_and_registration_number_binding():
    def proof_for(company_id):
        provenance = build_dart_filing_url_provenance(
            company_id=company_id, url=OFFICIAL_URL, source_document_id=f"dart:{RECEIPT}",
            source_receipt_no=RECEIPT, source_member_name="공식공시.xml",
            source_location="raw_xml_chars:0-10", source_document_sha256=sha("공시 원문"),
            source_payload_sha256=sha("공시 첨부"),
        )
        return build_verified_dart_filing_official_web_binding(
            provenance_value=provenance, company_id=company_id, company_name=LEGAL_NAME,
            company_registration_numbers=(PROFILE["bizr_no"],), candidate_url=OFFICIAL_URL,
            effective_urls=(OFFICIAL_URL,), scope_sha256=sha("공식 범위"), scope_allows=lambda url: True,
            identity_evidence_sha256=sha("법인명과 사업자번호 원문"),
            matched_name_sha256=sha(exact_company_name_key(LEGAL_NAME)),
            registration_number_sha256=sha("2028167042"),
        )

    cross_profile = {**PROFILE, "hm_url": "https://other-root.example"}
    result = change_document(
        evidence(), source_kind=SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
        identity_binding=proof_for(CORP_ID),
        domain_attestation_source_id="", domain_attestation_evidence="",
    )
    assert official_news_aliases(cross_profile, result)[0].alias == "뤼튼"
    for number in ("", "999-99-99999"):
        assert official_news_aliases({**cross_profile, "bizr_no": number}, result) == ()
    assert official_news_aliases(cross_profile, change_document(result, identity_binding=proof_for("00999999"))) == ()


@pytest.mark.parametrize("kind,location", [
    (SOURCE_KIND_OFFICIAL_WEB_PAGE, OFFICIAL_URL + "#99"),
    (SOURCE_KIND_OFFICIAL_WEB_PAGE, "https://other.example/#0"),
    (SOURCE_KIND_DART_AUDIT_REPORT, f"1-{len(PUBLIC_TEXT) + 1}"),
])
def test_even_recomputed_snapshot_cannot_hide_wrong_fragment_location(kind, location):
    result = evidence(kind=kind)
    candidate = result.candidates[0]
    altered = replace(result, candidates=(replace(candidate, fragments=(
        replace(candidate.fragments[0], location=location),
    )), *result.candidates[1:]))
    assert official_news_aliases(PROFILE, altered) == ()


def branch(result, *, profile=None, article=False):
    queries = []
    reservations = []

    def search(query, **kwargs):
        queries.append(query)
        items = [SimpleNamespace(title="뤼튼 기업용 AI 서비스 출시", description=ARTICLE,
                                 originallink=ARTICLE_URL, link="", pubDate="2026-09-01")]
        return SimpleNamespace(state="success", reason_code="news_search_ok",
                               transport_attempts=1, retry_recovered=False,
                               attempt_reason_codes=("news_search_ok",), items=items if article else [])

    def budget(*, reserved_calls):
        reservations.append(reserved_calls)
        return 3

    outcome = real._run_news_search_branch(
        engine=SimpleNamespace(available_provider_calls=budget), profile=profile or PROFILE,
        official_evidence=result, company_name=LEGAL_NAME,
        business_date=AS_OF, pipeline_news_search=search,
    )
    assert outcome.error is None and not outcome.value.preparation_failed
    return outcome, queries, reservations


def test_news_only_wiring_keeps_name_budget_order_and_no_definition_defaults():
    baseline, queries_before, reserved_before = branch(evidence("뤼튼테크놀로지스는 생성형 AI 플랫폼 기업이다."))
    fixed, queries_after, reserved_after = branch(evidence())
    assert fixed.value.session.company.company_name == LEGAL_NAME
    assert fixed.value.session.company.aliases == ("뤼튼",)
    assert real._official_company_aliases(PROFILE) == ()
    assert "공식약칭근거" not in baseline.steps[-1]
    assert fixed.steps[-1]["공식약칭근거"][0]["definition_text"] == DEFINITION
    assert reserved_after == reserved_before
    assert fixed.value.session.policy.max_analysis_calls == baseline.value.session.policy.max_analysis_calls == 3
    assert len(queries_after) == len(queries_before) + 1
    assert len(queries_after) <= fixed.value.session.policy.max_search_calls
    assert fixed.value.session.snapshot.company_digest != baseline.value.session.snapshot.company_digest
    existing = {**PROFILE, "corp_name_eng": "Wrtn Technologies", "stock_name": "뤼튼"}
    reused, reused_queries, _ = branch(evidence(), profile=existing)
    absent, absent_queries, _ = branch(None, profile=existing)
    assert reused.value.session.company.aliases == absent.value.session.company.aliases == ("Wrtn Technologies", "뤼튼")
    assert "공식약칭근거" not in reused.steps[-1]
    assert reused_queries.count("뤼튼") == absent_queries.count("뤼튼") == 1


@pytest.mark.parametrize("changes", [
    {"content_sha256": sha("다른 공식 문서 전체")},
    {"usable_ranges": (DocumentTextRange(10, 10 + len(PUBLIC_TEXT)),)},
])
def test_source_proof_change_separates_final_cache_even_with_same_alias(changes):
    first = evidence()
    second = change_document(first, **changes)
    one, _, _ = branch(first)
    two, _, _ = branch(second)
    assert one.value.session.company == two.value.session.company
    assert first.source_snapshot_sha256 != second.source_snapshot_sha256
    assert news_generation_digest(first.source_snapshot_sha256, news_snapshot_digest=one.value.digest,
                                  as_of=AS_OF.isoformat()) != news_generation_digest(
        second.source_snapshot_sha256, news_snapshot_digest=two.value.digest, as_of=AS_OF.isoformat())


@pytest.mark.parametrize("case", ["valid", "other_business", "subsidiary", "absent_quote", "model_rejected"])
def test_alias_does_not_bypass_article_identity_or_exact_quote_and_adds_no_model_call(case, monkeypatch):
    # 공식 정의만 공개 원문이며, 아래 기사·분석 응답은 경계 확인용 무료 대역이다.
    outcome, _, _ = branch(evidence("뤼튼테크놀로지스는 생성형 AI 플랫폼 기업이다.\n" + PUBLIC_TEXT), article=True)
    body = ARTICLE
    if case == "other_business":
        body = "뤼튼은 대학 캠퍼스에서 신입생 입시와 복수 전공 신청 상담을 진행했다."
    elif case == "subsidiary":
        body = ARTICLE.replace("뤼튼은", "뤼튼재팬은")
    calls, reads = [], []

    def analyze(prompt, schema, max_tokens):
        calls.append((prompt, schema, max_tokens))
        rows = json.loads(prompt.split("자료 시작:\n", 1)[1])["articles"]
        return {"items": [{"id": row["id"], "same_company": case != "model_rejected", "material": True,
                           "entity_evidence": body, "source_type": "news_report", "excerpts": [{
                               "text": body if case != "absent_quote" else body.replace("출시", "폐기"),
                               "section_id": "portfolio", "claim_slot": "portfolio:product_role",
                               "claim_kind": "reported_fact", "temporal_status": "completed",
                               "topic": "products", "event_key": "ai_release", "event_on": "",
                               "time_evidence": "", "subject": "", "subject_evidence": "",
                           }]} for row in rows]}

    def fetch(url):
        reads.append(url)
        return body

    if case == "valid":
        with monkeypatch.context() as before_fix:
            before_fix.setattr(real, "official_news_aliases", lambda *args, **kwargs: ())
            baseline, _, _ = branch(evidence("뤼튼테크놀로지스는 생성형 AI 플랫폼 기업이다.\n" + PUBLIC_TEXT), article=True)
        before = baseline.value.session.collect(fetch_text=fetch, analyze_grounded=analyze)
        assert not before.fragments
        assert before.diagnostics["법인검증상세"] == {"identity_name_missing": 1}
        assert len(calls) == len(reads) == 1
        calls.clear()
        reads.clear()
    result = outcome.value.session.collect(fetch_text=fetch, analyze_grounded=analyze)
    assert len(result.fragments) == (1 if case == "valid" else 0), result.diagnostics
    assert len(calls) == len(reads) == 1
