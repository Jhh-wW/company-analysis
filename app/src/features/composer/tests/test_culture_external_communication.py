"""외부 고객 접점의 소통만으로 내부 문화 소재를 만들지 않는다."""

import json
import re
from dataclasses import replace

import pytest

from src.features.composer.culture_constants import CULTURE_SECTION_EVIDENCE_OFFCONTRACT
from src.features.composer.culture_guard import culture_flow_cells_evidence_problem, culture_section_evidence_problem
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.news_block import augment_news_blocks
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report

EXTERNAL = "회사는 체험형 이벤트를 통해 국내 소비자와 인플루언서, 비즈니스 파트너와의 소통 접점을 확대하고 있다."
INTERNAL = "임직원은 부서 간 소통을 통해 업무 계획을 공유하고 공동 검토 후 의사결정한다."


@pytest.mark.parametrize("source", (
    EXTERNAL,
    "회사는 소비자와의 소통 채널을 확대하며 제품의 브랜드 위상을 높이고 있다.",
    "회사는 고객 소통 창구를 늘려 신규 제품 체험 행사를 안내한다.",
    "회사는 인플루언서와 소통하며 고객 접점 마케팅을 진행한다.",
    "회사는 고객과 소통할 기회를 늘리는 체험 행사를 마련했다.",
))
def test_외부고객소통만있는자기원문은_문화장소재가아니다(source):
    assert culture_section_evidence_problem(source, {"source": source}) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


@pytest.mark.parametrize("source", (
    INTERNAL,
    "회사는 사내 소통을 활성화하고 고객과의 소통 채널을 확대한다.",
    "회사는 부서 간 소통 원칙을 지키며 고객과의 소통 접점을 확대한다.",
    "회사는 고객과 소통하며 임직원의 의견 제안 제도를 운영한다.",
    "직원은 고객과의 소통을 담당하며 매주 의견을 공유하고 공동 검토한다.",
    "개발팀은 고객과의 소통 채널에서 수집한 의견을 정기회의에서 검토한다.",
    "회사는 고객과의 소통을 확대하며 인사제도에 따라 사내 교육훈련을 운영한다.",
    "고객과의 소통은 회사의 조직문화로 명시되어 있으며 직원이 협업 절차를 따른다.",
    "담당 부서가 비즈니스 파트너와의 소통 창구를 운영하며 업무를 분담한다.",
))
def test_사내문화_혼합절_직원외부협력실행은_기존의미검수에남긴다(source):
    assert culture_section_evidence_problem(source, {"source": source}) == ""


def test_다른문장의직원소통으로_고객접점주장을문화사례로살리지않는다():
    source = EXTERNAL + " " + INTERNAL
    assert culture_section_evidence_problem(EXTERNAL, {"source": source}) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT
    assert culture_section_evidence_problem(INTERNAL, {"source": source}) == ""


@pytest.mark.parametrize("external,internal", (
    ("회사는 고객과의 소통 창구를 운영한다.", "회사는 임직원과의 소통 창구를 운영한다."),
    ("회사는 소비자와 소통하며 제품 체험 행사를 연다.",
     "회사는 임직원과 소통하며 사내 협업 행사를 연다."),
))
@pytest.mark.parametrize("same_source", (False, True))
def test_유사한사내절을빌리지않고_후보자체의혼합소재는보존한다(external, internal, same_source):
    sources = {"source": external + " " + internal} if same_source else {
        "external": external, "internal": internal,
    }
    assert culture_section_evidence_problem(external, sources) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT
    assert culture_section_evidence_problem(internal, sources) == ""
    assert culture_section_evidence_problem(external + " " + internal, sources) == ""
    assert culture_flow_cells_evidence_problem((external, "", ""), sources) == CULTURE_SECTION_EVIDENCE_OFFCONTRACT


@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("verdict", ("참", "애매"))
def test_검수긍정에도_외부소통문화배치는제외하고_사내문장은보존한다(grouped, verdict):
    fragments = (CollectedFragment("external", "뉴스", EXTERNAL),
                 CollectedFragment("internal", "공식자료", INTERNAL))
    draft = ComposedReport((ComposedSection("culture", (
        ComposedSentence(EXTERNAL, ("external",), "확인"),
        ComposedSentence(INTERNAL, ("internal",), "확인"),
    )),))
    calls = []

    def ask(prompt):
        calls.append(prompt)
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        assert items
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section, "결과": verdict,
             "근거": [value.strip().removeprefix("조각 ").strip() for value in item.citations]}
            for item in items
        ]}, ensure_ascii=False)

    allowed = {"culture": frozenset(fragment.fragment_id for fragment in fragments)} if grouped else None
    checked = verify_report(draft, fragments, None, ask, allowed_fragment_ids_by_section=allowed)
    assert [sentence.text for sentence in checked.sections[0].sentences] == [INTERNAL]
    assert len(calls) == 1


def _news(fragment_id, text):
    return CollectedFragment(
        fragment_id, "news", text, source_url="https://media.example/article/one",
        document_title="회사 소통 사례", document_date="2026-09-01",
        source_publisher="가상경제", formal_source_kind="news", news_grounded=True,
        supported_claim_slots=("culture:verified_case",),
    )


def test_산문이없는보도표에서도_외부소통문화배치를제외한다():
    fragment = _news("external", EXTERNAL)
    report = ComposedReport((ComposedSection("culture", ()),))
    result = augment_news_blocks(report, (fragment,),
                                 allowed_fragment_ids_by_section={"culture": frozenset({"external"})})
    assert not result.report.sections[0].news_rows
    assert dict(result.blocked_counts_by_reason) == {CULTURE_SECTION_EVIDENCE_OFFCONTRACT: 1}
    assert fragment.text == EXTERNAL


def test_같은기사의정상인용을_빌리지않고_각원문조각을_문화표에서검사한다():
    bad, good = _news("external", EXTERNAL), _news("internal", INTERNAL)
    report = ComposedReport((ComposedSection("culture", ()),))
    result = augment_news_blocks(report, (bad, good), allowed_fragment_ids_by_section={
        "culture": frozenset({"external", "internal"}),
    })
    assert len(result.report.sections[0].news_rows) == 1
    row = result.report.sections[0].news_rows[0]
    assert row.citations == ("internal",) and row.evidence_texts == (INTERNAL,)
    assert dict(result.blocked_counts_by_reason) == {CULTURE_SECTION_EVIDENCE_OFFCONTRACT: 1}


def test_고객마케팅인용은_다른정상장배치에문화가드를적용하지않는다():
    fragment = replace(_news("external", EXTERNAL), supported_claim_slots=("business_model:customer_type",))
    report = ComposedReport((ComposedSection("business_model", ()),))
    result = augment_news_blocks(report, (fragment,), allowed_fragment_ids_by_section={
        "business_model": frozenset({"external"}),
    })
    assert result.report.sections[0].news_rows[0].evidence_texts == (EXTERNAL,)
    assert not result.blocked_counts_by_reason
