"""뉴스 대체 후보의 독립 검수·중복·회차 제한에 관한 무과금 반례."""

from dataclasses import replace
import json
import re

import pytest

from src.core import news_intake_switch
from src.core.constants import MAX_AI_CALLS_PER_REQUEST
from src.features.composer.constants import SECTION_IDS
from src.features.composer import pipeline as pipeline_module
from src.features.composer.logic import parse_section_response
from src.features.composer.news_usage import (
    attribution_prefix,
    retain_verified_news,
    supplement_news_candidates,
)
from src.features.composer.pipeline import run_v2
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.tests.test_news_block_channels import _news_fragment, _ThinThenFullWriter
from src.features.composer.tests.injected_program_fixture import make_numeric_performance_evidence
from src.features.composer.tests.test_section_public_manifest import (
    _BoundGroupedReviewer, _CompletePacketWriter, _NoDiagram, _packets,
)
from src.features.composer.verify import verify_report
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_quality.assessment import assess_quality
from src.shared.report_quality.contract import STRICT_CONTRACT


NEWS_ID = "881"
SOURCE_TEXT = "가나다전자는 신규 설비 공급 계약을 체결했다고 밝혔다."
FALSE_TEXT = "다른산업은 신규 설비 공급 계약을 체결했다고 밝혔다."
ITEM_TEXT = re.compile(
    r'\[(\d+)\] \(장: [^\n]+\)\n\s+등급: [^\n]+\n\s+문장\(JSON 문자열\): ([^\n]+)'
)


@pytest.fixture(autouse=True)
def _enabled_news(monkeypatch):
    monkeypatch.setenv("NEWS_INTAKE", "1")
    news_intake_switch._reset_process_news_intake_switch_for_tests()
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()


def _fragment():
    return _news_fragment(NEWS_ID, "2026-09-01", SOURCE_TEXT, section_id="business_model")


class SelectiveReviewer(_BoundGroupedReviewer):
    def __init__(self, decide):
        super().__init__()
        self.decide = decide
        self.seen = []

    def __call__(self, prompt):
        result = json.loads(super().__call__(prompt))
        texts = {int(number): json.loads(text) for number, text in ITEM_TEXT.findall(prompt)}
        self.seen.append(texts)
        kept = []
        for entry in result["판정"]:
            decision = self.decide(texts.get(entry["번호"], ""), entry)
            if decision is not None:
                entry["결과"] = decision
                kept.append(entry)
        return json.dumps({"판정": kept}, ensure_ascii=False)


def _draft(text, fragment=None):
    fragment = fragment or _fragment()
    return ComposedReport((ComposedSection("business_model", (
        ComposedSentence(text, (fragment.fragment_id,), "확인",
                         planned_claim_slot=fragment.supported_claim_slots[0]),
    )),))


def _review(draft, fragments, decide):
    candidate, added = supplement_news_candidates(draft, fragments)
    reviewer = SelectiveReviewer(decide)
    verified = verify_report(candidate, fragments, None, reviewer,
                             allowed_fragment_ids_by_section={
                                 "business_model": frozenset(f.fragment_id for f in fragments),
                             })
    diagnostics = []
    retained = retain_verified_news(verified, fragments, review_input=candidate, diagnostics=diagnostics)
    return retained, candidate, reviewer, diagnostics, added


def _full(news, *, transform=None, decide=None, supplement=False, omit_first=False, packets=None):
    base = packets or _packets()
    packets = replace(base, packets=tuple(
        replace(packet, fragments=packet.fragments + news)
        if packet.section_id == "business_model" else packet for packet in base.packets
    ))
    writer = _ThinThenFullWriter() if supplement else _CompletePacketWriter()
    if supplement:
        writer._THIN_SECTION = "business_model"
    writer_prompts = []

    def write(prompt):
        writer_prompts.append(prompt)
        result = json.loads(writer(prompt))
        if any(f"[조각 {f.fragment_id}]" in prompt for f in news):
            if omit_first and len(writer_prompts) <= len(SECTION_IDS):
                result["뉴스근거판정"] = [
                    {"조각": f.fragment_id, "사유": "중복", "설명": "공식 자료와 겹친다."}
                    for f in news
                ]
            elif transform is not None:
                result["문장들"].extend({
                    "글": transform(f.text), "인용": [f.fragment_id], "등급": "확인",
                    "주장슬롯": f.supported_claim_slots[0],
                } for f in news)
        return json.dumps(result, ensure_ascii=False)

    reviewer = SelectiveReviewer(decide or (lambda _text, _entry: "참"))
    performance_table, _, filing_meta = make_numeric_performance_evidence(
        fragment_number=40
    )
    output = run_v2("가나다전자", (), performance_table, writer_ask=write, reviewer_ask=reviewer,
                    diagram_ask=_NoDiagram(), release_mode=ReleaseMode.FULL,
                    section_evidence_packets=packets, company_id="00123456",
                    filing_meta=filing_meta,
                    build_identity_sha256="b" * 64)
    return output, writer_prompts, reviewer


@pytest.mark.parametrize("source_verdict", ["거짓", "불명확", None])
def test_대체후보표식이_있어도_검수실패나_판정누락을_승인하지않는다(source_verdict):
    report, candidate, reviewer, diagnostics, _ = _review(
        _draft(FALSE_TEXT), (_fragment(),),
        lambda text, _entry: source_verdict if SOURCE_TEXT in text else "거짓",
    )
    assert len(candidate.sections[0].sentences) == 2
    assert candidate.sections[0].sentences[-1].news_source_alternative is True
    assert all(s.verification_state == "unverified" for s in candidate.sections[0].sentences)
    assert report.sections[0].sentences == ()
    # 2026-09-23 — packet 도 평문처럼 계약 밖 판정(«불명확»)·판정 누락은 «그 번호만»
    #   누락 후속으로 1회 다시 묻는다. 후속도 같은 답이면 여전히 승인하지 않는다.
    if source_verdict == "거짓":
        assert len(reviewer.prompts) == 1
    else:
        assert len(reviewer.prompts) == 2
        # 후속은 대체 후보 한 문장만 싣는다 — 이미 «거짓»인 초안은 다시 묻지 않는다.
        (followup_text,) = reviewer.seen[1].values()
        assert SOURCE_TEXT in followup_text and FALSE_TEXT not in followup_text
    assert any(row["원문대체후보"] for row in diagnostics)
    assert SOURCE_TEXT not in json.dumps(diagnostics, ensure_ascii=False)


def test_다른법인으로_변형한_거짓초안은_정확원문승인과_독립적으로_제거한다():
    report, _, reviewer, diagnostics, _ = _review(
        _draft(FALSE_TEXT), (_fragment(),),
        lambda text, _entry: "거짓" if FALSE_TEXT in text else "참",
    )
    assert [s.text for s in report.sections[0].sentences] == [attribution_prefix(_fragment()) + SOURCE_TEXT]
    assert any(FALSE_TEXT in text for text in reviewer.seen[0].values())
    assert diagnostics[0]["원문대체후보"] is False
    assert diagnostics[0]["사유코드"] == "review_removed"


@pytest.mark.parametrize("alternative_verdict", ["참", "거짓"])
def test_정상Writer만_승인하면_대체후보판정과_무관하게_정상문장_하나만_남긴다(alternative_verdict):
    writer_text = SOURCE_TEXT.replace("체결했다고 밝혔다.", "체결했다.")
    report, _, reviewer, _, _ = _review(_draft(writer_text), (_fragment(),),
        lambda text, _entry: alternative_verdict if SOURCE_TEXT in text else "참")
    assert [sentence.text for sentence in report.sections[0].sentences] == [
        attribution_prefix(_fragment()) + writer_text,
    ]
    assert report.sections[0].sentences[0].news_source_alternative is False
    assert len(reviewer.prompts) == 1


def test_미래계획을_실현으로_바꾼_초안은_거절하고_원문계획만_통과한다():
    text = "가나다전자는 신규 설비를 공급할 계획이라고 밝혔다."
    fragment = replace(_fragment(), text=text, news_claim_kind="company_plan",
                       news_temporal_status="planned", news_event_on="2027-01-01")
    changed = text.replace("공급할 계획이라고", "공급을 완료했다고")
    report, _, reviewer, _, _ = _review(_draft(changed, fragment), (fragment,),
        lambda candidate, _entry: "거짓" if "완료" in candidate else "참")
    assert [s.text for s in report.sections[0].sentences] == [attribution_prefix(fragment) + text]
    assert '"시간상태": "planned"' in reviewer.prompts[0]
    assert '"사건시점": "2027-01-01"' in reviewer.prompts[0]


def test_Writer가_대체후보와_검수상태를_응답에_위조해도_파서는_받지않는다():
    sentences = parse_section_response(json.dumps({"문장들": [{
        "글": FALSE_TEXT, "인용": [NEWS_ID], "등급": "확인",
        "news_source_alternative": True, "verification_state": "verified",
    }]}, ensure_ascii=False), "business_model")
    assert sentences[0].news_source_alternative is False
    assert sentences[0].verification_state == "unverified"


def test_뉴스가_전부_거절돼도_FULL은_뉴스할당량을_채우려고_추가호출하지않는다():
    output, writer, reviewer = _full((_fragment(),), transform=lambda _text: FALSE_TEXT,
        decide=lambda _text, entry: "거짓" if NEWS_ID in entry["근거"] else "참")
    assert output.report.public_projection is not None
    assert output.news_usage_diagnostics["본문사용기사수"] == 0
    assert output.news_usage_diagnostics["목록기사수"] == 0
    assert len(writer) == 9 and len(reviewer.prompts) == 1
    assert len(output.generation_evidence.call_ledger.records) == 10
    assert len(output.generation_evidence.call_ledger.records) < MAX_AI_CALLS_PER_REQUEST


def test_FULL_보충에서도_변형초안과_정확원문을_같은추가검수로_구분한다():
    output, writer, reviewer = _full((_fragment(),), transform=lambda _text: FALSE_TEXT,
        decide=lambda text, _entry: "거짓" if FALSE_TEXT in text else "참",
        supplement=True, omit_first=True)
    body = " ".join(text for section in output.report.sections for text, _ in section.prose_lines)
    assert FALSE_TEXT not in body and SOURCE_TEXT in body
    assert len(writer) == 10 and len(reviewer.prompts) == 2
    assert len(output.generation_evidence.call_ledger.records) == 12
    assert len(output.generation_evidence.validation_receipts) == 2
    assert len(output.generation_evidence.call_ledger.records) < MAX_AI_CALLS_PER_REQUEST


def test_같은사건의_복제조각을_초안이_각각인용해도_대체원문은_한번만_공개한다():
    first = _fragment()
    duplicate = replace(first, fragment_id="882")
    output, _, reviewer = _full((first, duplicate), transform=lambda _text: FALSE_TEXT,
        decide=lambda text, _entry: "거짓" if FALSE_TEXT in text else "참")
    body = " ".join(text for section in output.report.sections for text, _ in section.prose_lines)
    assert len(reviewer.prompts) == 1
    assert body.count(SOURCE_TEXT) == 1


def test_별도조각의_정상Writer가_통과하면_동일원문_대체후보만_제거한다():
    first = _fragment()
    duplicate = replace(first, fragment_id="882")
    writer_text = SOURCE_TEXT.replace("체결했다고 밝혔다.", "체결했다.")
    draft = ComposedReport((ComposedSection("business_model", (
        _draft(FALSE_TEXT, first).sections[0].sentences[0],
        _draft(writer_text, duplicate).sections[0].sentences[0],
    )),))
    report, candidate, reviewer, diagnostics, added = _review(
        draft, (first, duplicate),
        lambda text, _entry: "거짓" if FALSE_TEXT in text else "참",
    )
    assert added == (first.fragment_id,)
    assert len(candidate.sections[0].sentences) == 3
    assert candidate.sections[0].sentences[-1].citations == (first.fragment_id,)
    retained = report.sections[0].sentences
    assert len(retained) == 1
    assert retained[0].text == attribution_prefix(duplicate) + writer_text
    assert retained[0].citations == (duplicate.fragment_id,)
    assert retained[0].news_source_alternative is False
    assert retained[0].verification_state == "verified"
    assert len(reviewer.prompts) == 1
    duplicates = [row for row in diagnostics if row["사유코드"] == "duplicate_source"]
    assert len(duplicates) == 1
    assert duplicates[0]["원문대체후보"] is True
    assert any(row["사유코드"] == "review_removed" for row in diagnostics)


def test_같은기사의_서로다른_원문사실은_각각_검수하여_FULL에_보존한다():
    first = _fragment()
    second_text = "가나다전자는 해당 계약의 공급 기간이 내년까지라고 밝혔다."
    second = replace(first, fragment_id="882", text=second_text,
                     news_event_key="별도_공급기간_사실")
    output, writer, reviewer = _full((first, second))
    body = " ".join(text for section in output.report.sections for text, _ in section.prose_lines)
    assert body.count(SOURCE_TEXT) == 1
    assert body.count(second_text) == 1
    assert output.news_usage_diagnostics["본문사용기사수"] == 1
    assert len(writer) == len(SECTION_IDS)
    assert len(reviewer.prompts) == 1
    assert len(output.generation_evidence.call_ledger.records) == len(SECTION_IDS) + 1


def test_뉴스원문대체후보를_공식8문서하한에_포함하지않는다(monkeypatch):
    captured = []
    original = pipeline_module.build_generation_quality_candidate

    def capture(*args, **kwargs):
        candidate = original(*args, **kwargs)
        captured.append(candidate)
        return candidate

    monkeypatch.setattr(pipeline_module, "build_generation_quality_candidate", capture)
    output, _, _ = _full((_fragment(),))
    # 기본 공식 문서에 검산된 4장 실적표 문서 한 건이 추가된다.
    assert output.quality_observation.document_sources == 10
    candidate = captured[-1]
    news = tuple(source for source in candidate.sources if source.source_kind == "news")
    official = tuple(source for source in candidate.sources if source.source_kind != "news")
    assert news and all(source.counts_toward_document_floor is False for source in news)
    # 실적표 추가 뒤 공식 독립 문서는 열 건이다. 실제 문서로 세는 서로 다른
    # 세 문서만 한 문서의 신원으로 묶어 일곱 건으로 만든다. 뉴스는 그대로 둔다.
    counted_indices = tuple(
        index for index, source in enumerate(official)
        if source.counts_toward_document_floor and source.document_content_sha256
    )
    assert len(counted_indices) == 10
    anchor = official[counted_indices[0]]
    repeated_indices = frozenset(counted_indices[-3:])
    reduced_official = tuple(
        replace(
            source,
            document_identity=anchor.document_identity,
            document_content_sha256=anchor.document_content_sha256,
        ) if index in repeated_indices else source
        for index, source in enumerate(official)
    )
    reduced = replace(candidate, sources=reduced_official + news)
    assessment = assess_quality(reduced, STRICT_CONTRACT)
    assert assessment.document_sources == 7
    assert any("독립 문서 출처가 7건" in reason for reason in assessment.shortfall_reasons)


@pytest.mark.parametrize("text,published", [
    (FALSE_TEXT, "2026-09-01"), (SOURCE_TEXT, "2027-09-01"),
])
def test_법인이나_시점을_검증하지못한_조각을_자동원문후보로_만들지않는다(text, published):
    fragment = replace(_fragment(), text=text, document_date=published, news_grounded=False)
    candidate, added = supplement_news_candidates(
        ComposedReport((ComposedSection("business_model", ()),)), (fragment,)
    )
    assert added == ()
    assert candidate.sections[0].sentences == ()
