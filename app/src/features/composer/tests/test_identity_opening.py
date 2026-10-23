"""현재 사업 첫 문장의 순서 변경이 기존 공개 결속을 보존하는지 검사한다."""

from dataclasses import replace

import pytest

from src.features.composer.identity_opening import prioritize_identity_opening
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.quality_projection import select_bound_public_sentences
from src.features.composer.tests.test_quality_projection import _fact, _source
from src.features.pipeline.port import Grade, Report, ReportSection
from src.shared.report_quality.fact_binding import fact_evidence_binding


BUSINESS = "가나다 주식회사는 공식 제품인 반도체 검사장비를 제조하는 기업이다."
ORIGIN = "가나다 주식회사는 공식 제품 사업을 위해 자회사를 합병했다."


def _pair(text=BUSINESS, *, slot="identity:business_definition", **fact_values):
    sentence = ComposedSentence(text, ("1",), "확인", slot, "verified")
    fact = replace(
        _fact(sentence, _source()),
        fact_id="fact-" + slot + "-" + text,
        section_owner="identity",
        relationship_or_action=slot.split(":")[-1],
        **fact_values,
    )
    return sentence, replace(fact, evidence_binding=fact_evidence_binding(fact))


def _selection(pairs, *, section_id="identity"):
    sentences = tuple(sentence for sentence, _fact_value in pairs)
    composed = ComposedReport(
        (ComposedSection(section_id, sentences),), summary=sentences,
    )
    rendered = Report(
        company="가나다 주식회사", job="", corp_type="", grade=Grade.PARTIAL,
        sections=[ReportSection(
            cell=section_id, title="기업 정체성",
            prose_lines=[(sentence.text, "[1]") for sentence in sentences],
            fact_ids=[fact.fact_id for _sentence, fact in pairs],
        )],
        citations=[_source()],
        fact_records=[fact for _sentence, fact in pairs],
    )
    return composed, rendered, select_bound_public_sentences(composed, rendered)


def test_결속된_현재사업_문장과_FactID를_함께_안정이동한다():
    origin = _pair(ORIGIN, slot="identity:corporate_identity")
    business = _pair()
    later = _pair("회사는 공식 제품인 검사장비를 공급하는 기업이다.")
    composed, rendered, selection = _selection((origin, business, later))

    assert selection.report.sections[0].sentences == (business[0], origin[0], later[0])
    assert selection.report.sections[0].sentences[0] is business[0]
    assert selection.fact_ids == (business[1].fact_id, origin[1].fact_id, later[1].fact_id)
    assert not selection.excluded
    assert selection.report.summary == ()
    assert composed.sections[0].sentences == (origin[0], business[0], later[0])
    assert rendered.fact_records == [origin[1], business[1], later[1]]
    again = select_bound_public_sentences(selection.report, rendered)
    assert again.report == selection.report
    assert again.fact_ids == selection.fact_ids


@pytest.mark.parametrize("fact_values", [
    {"time_state": "past"}, {"time_state": "future"}, {"time_state": ""},
    {"plan_status": "announced"}, {"fact_status": "planned"},
])
def test_과거_계획_시점미상의_사업문장을_첫현재사업으로_올리지않는다(fact_values):
    origin = _pair(ORIGIN, slot="identity:corporate_identity")
    business = _pair(**fact_values)
    assert prioritize_identity_opening((origin, business)) == (origin, business)


@pytest.mark.parametrize("text", [
    "회사는 공식 제품 판매를 목적으로 설립된 회사다.",
    "회사의 본점은 서울특별시 강남구 테헤란로 131에 있다.",
    "회사는 2020년 8월 1일에 설립되었다.",
    "이는 공식 제품인 검사장비를 제조하는 기업이다.",
    "이후 회사는 공식 제품인 검사장비를 제조한다.",
    "이러한 공식 제품을 제공하는 기업이다.",
])
def test_행정정보_설립목적_종속문장은_새요약으로_승격하지않는다(text):
    origin = _pair(ORIGIN, slot="identity:corporate_identity")
    business = _pair(text)
    assert prioritize_identity_opening((origin, business)) == (origin, business)


def test_부적합한_앞사업문장_뒤의_승인된_현재사업을_고른다():
    origin = _pair(ORIGIN, slot="identity:corporate_identity")
    purpose = _pair("회사는 공식 제품 판매를 목적으로 설립된 회사다.")
    business = _pair()
    assert prioritize_identity_opening((origin, purpose, business)) == (business, origin, purpose)


@pytest.mark.parametrize("change", ["unverified", "unbound", "interpreted"])
def test_미승인_미결속_해석문장은_첫사업요약_후보가_아니다(change):
    origin = _pair(ORIGIN, slot="identity:corporate_identity")
    sentence, fact = _pair()
    if change == "unverified":
        sentence = replace(sentence, verification_state="unverified")
    elif change == "unbound":
        sentence = replace(sentence, text=sentence.text + " 추가 주장이다.")
    else:
        sentence = replace(sentence, grade="해석")
    composed, _rendered, selection = _selection((origin, (sentence, fact)))
    assert selection.report.sections[0].sentences == (origin[0],)
    assert selection.fact_ids == (origin[1].fact_id,)
    assert len(selection.excluded) == 1
    assert composed.sections[0].sentences[1] is sentence


def test_다른장의_기존순서와_승인정보는_유지한다():
    first = _pair(ORIGIN, slot="identity:corporate_identity")
    business = _pair()
    pairs = tuple((sentence, replace(fact, section_owner="portfolio")) for sentence, fact in (first, business))
    assert prioritize_identity_opening(pairs) == pairs


def test_후보없으면_본문을_만들지않는다():
    assert prioritize_identity_opening(()) == ()
    origin = _pair(ORIGIN, slot="identity:corporate_identity")
    _composed, _rendered, selection = _selection((origin,))
    assert selection.report.sections[0].sentences == (origin[0],)


@pytest.mark.parametrize("text", [
    "회사는 공식 제품인 검사장비를 제조하는 업체다.",
    "회사는 공식 제품인 검사장비를 만드는 제조사이다.",
    "회사는 공식 제품인 검사장비를 공급하는 공급사다.",
    "회사는 공식 제품의 결제를 중개하는 사업자다.",
    "회사는 공식 제품의 거래를 중개하는 플랫폼이다.",
    "회사는 공식 제품의 결제를 처리하는 대행사입니다.",
    "회사는 공식 제품의 정보를 제공하는 기관이다.",
])
def test_업종을_제한하지않고_기업유형_정의문을_우선한다(text):
    origin = _pair(ORIGIN, slot="identity:corporate_identity")
    business = _pair(text)
    assert prioritize_identity_opening((origin, business)) == (business, origin)


@pytest.mark.parametrize("text", [
    "기능별 수직 조직보다 아티스트별 레이블을 두고 각 IP의 수명주기에 맞춰 콘텐츠와 사업을 운영한다.",
    "회사는 전자지불결제대행업을 통하여 대금을 받은 후 가맹점에 지급하는 구조를 운영한다.",
    "회사는 공식 제품을 제공한다. 회사는 기업 고객을 지원하는 기업이다.",
])
def test_운영설명과_여러문장을_쉬운_기업정의로_승격하지않는다(text):
    introduction = _pair(
        "제이와이피엔터테인먼트는 아티스트를 발굴·육성하고 음악·영상 콘텐츠의 제작·유통, 공연과 IP 라이선싱 사업을 전개하는 엔터테인먼트 기업이다.",
        slot="identity:corporate_identity",
    )
    operational = _pair(text)
    composed, _rendered, selection = _selection((introduction, operational))
    assert selection.report.sections[0].sentences == composed.sections[0].sentences
    assert selection.report.sections[0].sentences[0] is introduction[0]
    assert selection.report.sections[0].sentences[1] is operational[0]
    assert selection.fact_ids == (introduction[1].fact_id, operational[1].fact_id)
    assert not selection.excluded

