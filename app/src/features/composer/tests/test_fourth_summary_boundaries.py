"""표현이 다른 수익 구성은 한 번 고르되 추가 사실과 근거 차이는 보존한다."""

from dataclasses import replace

import pytest

from src.features.composer.extractive_summary import distinct_summary_candidates
from src.features.composer.logic import SummaryCandidate
from src.features.composer.port import ComposedSentence


def _candidate(section, text, citations=("1",)):
    return SummaryCandidate(section, ComposedSentence(
        text, citations, "확인", verification_state="verified",
    ))


def test_구성과_발생경로_바꿔쓰기는_같은_원문에서_한번_고른다():
    first = _candidate("identity", "회사의 수익은 용역 매출과 콘텐츠 매출로 구성된다.", ("1", "2"))
    second = _candidate("business_model", "가나다전자의 수익은 콘텐츠와 용역 두 가지 경로에서 발생한다.")
    assert distinct_summary_candidates((first, second), company_name="가나다전자") == (first,)


@pytest.mark.parametrize("changed", (
    "가나다전자의 수익은 콘텐츠와 용역 세 가지 경로에서 발생한다.",
    "가나다전자의 수익은 콘텐츠와 2025년 용역 두 가지 경로에서 발생한다.",
    "가나다전자의 수익은 콘텐츠와 용역으로 구성되며 수출 비중이 증가했다.",
    "다른회사의 수익은 콘텐츠와 용역 두 가지 경로에서 발생한다.",
))
def test_개수_기간_회사_추가사실이_다르면_보존한다(changed):
    first = _candidate("identity", "회사의 수익은 용역 매출과 콘텐츠 매출로 구성된다.")
    second = _candidate("business_model", changed)
    assert distinct_summary_candidates((first, second), company_name="가나다전자") == (first, second)


def test_같은_문구라도_근거가_다르면_자동으로_합치지_않는다():
    first = _candidate("identity", "회사의 수익은 용역 매출과 콘텐츠 매출로 구성된다.")
    second = replace(first, section_id="business_model", sentence=replace(first.sentence, citations=("9",)))
    assert distinct_summary_candidates((first, second), company_name="가나다전자") == (first, second)
