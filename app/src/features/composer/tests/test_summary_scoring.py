"""실제 PDF의 사소한 요약보다 회사의 구체적 사실을 먼저 고른다."""

from dataclasses import replace

import pytest

from src.features.composer.constants import GRADE_CONFIRMED, GRADE_INTERPRETED
from src.features.composer.extractive_summary import _summary_score, select_extractive_summary
from src.features.composer.port import ComposedReport, ComposedSection
from src.features.composer.tests.test_extractive_summary import _fact, _sentence
from src.shared.report_quality.fact_binding import fact_evidence_binding


MEDILINE_ACCOUNTING = "회사는 영업외수익으로 이자수익과 임대료수입을 인식하고 있다."
MEDILINE_BUSINESS = "메디라인액티브코리아는 의료기기 제조 및 판매업을 영위하며, 제품 매출이 전체 수익의 대부분을 차지하고 상품 매출이 부수적 역할을 한다."
MEDILINE_LIQUIDITY = "당사는 부채상환을 포함하여 합리적으로 예상되는 영업자금수요를 충당할 수 있는 유동성을 예측하고 관리하고 있습니다."
MEDILINE_FACTORY = "회사는 2025년 3월 31일에 중소벤처기업부 산하 스마트제조혁신추진단과 '2025년도 정부일반형 스마트공장 구축사업' 협약을 체결하여 정부보조금을 수령하고 관련 시스템 구축을 진행 중이다."
WRTN_INTERPRETATION = "지급수수료와 광고선전비의 급증은 콘텐츠 플랫폼 확대와 사용자 확보에 집중하는 사업 전략을 시사한다."
WRTN_PLATFORM = "회사는 홈페이지에서 AI 엔터테인먼트 콘텐츠 플랫폼 '크랙(Crack)'을 운영하고 있으며, 청소년 보호 정책 강화를 추진하고 있다."


def _select(pools):
    sections = []
    facts = []
    for section_id, entries in pools:
        sentences = tuple(
            replace(_sentence(section_id), text=text, grade=grade)
            for text, grade in entries
        )
        sections.append(ComposedSection(section_id, sentences))
        for sentence in sentences:
            fact = replace(
                _fact(section_id, sentence),
                fact_id=f"fact-{len(facts)}",
                evidence_support_terms=sentence.text.split()[:2],
            )
            facts.append(replace(fact, evidence_binding=fact_evidence_binding(fact)))
    report = ComposedReport(tuple(sections))
    return report, select_extractive_summary(report, facts)


def test_실제_메디라인과_뤼튼_문장을_점수순으로_고르고_장별_순환을_지킨다():
    report, summary = _select((
        ("business_model", ((MEDILINE_ACCOUNTING, GRADE_CONFIRMED), (MEDILINE_BUSINESS, GRADE_CONFIRMED))),
        ("portfolio", ((WRTN_INTERPRETATION, GRADE_INTERPRETED), (WRTN_PLATFORM, GRADE_CONFIRMED))),
        ("current_challenges", ((MEDILINE_LIQUIDITY, GRADE_CONFIRMED), (MEDILINE_FACTORY, GRADE_CONFIRMED))),
    ))

    assert summary.release_ready
    assert summary.section_ids == (
        "business_model", "portfolio", "current_challenges", "business_model", "portfolio",
    )
    assert tuple(sentence.text for sentence in summary.sentences) == (
        MEDILINE_BUSINESS, WRTN_PLATFORM, MEDILINE_FACTORY, MEDILINE_ACCOUNTING, WRTN_INTERPRETATION,
    )
    originals = [sentence for section in report.sections for sentence in section.sentences]
    assert all(any(sentence is original for original in originals) for sentence in summary.sentences)


@pytest.mark.parametrize("reverse", (False, True))
def test_등급이_달라도_점수가_같으면_원래_문장_순서를_지킨다(reverse):
    entries = (("AI 중심 사업으로 읽힌다.", GRADE_INTERPRETED), (MEDILINE_BUSINESS, GRADE_CONFIRMED))
    if reverse:
        entries = tuple(reversed(entries))
    _, summary = _select((("business_model", entries),))
    assert summary.sentences[0].text == entries[0][0]
    assert len(summary.items) == 1
    assert not summary.release_ready


@pytest.mark.parametrize("text, expected", (
    (MEDILINE_ACCOUNTING, -3),
    (MEDILINE_BUSINESS, 0),
    (MEDILINE_LIQUIDITY, -5),
    (MEDILINE_FACTORY, 5),
    (WRTN_PLATFORM, 2),
    ("수출액은 424.8억원이다.", 3),
    ("판매가는 1,000원이다.", 3),
    ("성장률은 6.9%다.", 3),
    ("인원은 10명이다.", 3),
    ("수주는 10건이다.", 3),
    ("공장은 2개다.", 3),
    ("영업 기간은 2년이다.", 3),
    ("매출은 2배다.", 3),
    ("설립 시점은 2025다.", 3),
    ("문서 식별값은 12345다.", 0),
    ("'크랙'을 운영한다.", 2),
    ('"크랙"을 운영한다.', 2),
    ("‘크랙’을 운영한다.", 2),
    ("“크랙”을 운영한다.", 2),
    ("Crack을 운영한다.", 2),
    ("crack을 운영한다.", 0),
    ("제품입니다. 판매를 시작했다.", -2),
    ("매출 2억원, 직원 10명, 설립 2025년이다.", 3),
))
def test_점수_항목은_문장당_한번씩_합산한다(text, expected):
    assert _summary_score(replace(_sentence("portfolio"), text=text)) == expected


@pytest.mark.parametrize("pattern", (
    "인식한다", "인식하고", "계상", "회계처리", "기준서", "총평균법",
    "공정가치", "상각후원가", "대손충당금", "유동성을 예측", "영업외수익",
    "이자수익", "임대료수입", "승인될 예정", "주주총회",
))
def test_저가치_패턴을_모두_감점한다(pattern):
    assert _summary_score(replace(_sentence("portfolio"), text=pattern)) == -3


def test_해석_등급의_감점은_문장_내용과_별도로_더한다():
    sentence = replace(_sentence("portfolio"), text=WRTN_INTERPRETATION, grade=GRADE_INTERPRETED)
    assert _summary_score(sentence) == -2
