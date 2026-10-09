"""명시 메타 목록이 회사 사건 인용에 재유입되는 경계와 정상 자기 문장을 검사한다."""
from dataclasses import replace

import pytest

from src.features.news_intake.article_text_scope import auxiliary_only_body, auxiliary_spans
from src.features.news_intake.grounded import validate_grounded_response
from src.features.news_intake.tests.test_collection import accepted, BODY, COMPANY, AS_OF
from src.features.news_intake.tests.test_industry_context import CANDIDATE


PANEL = "관련 키워드 가나다전자 산업설비 고객 서비스 자동화 관련 기사 가나다전자, 고객 서비스 제공"


def validate(body, text):
    candidate = replace(CANDIDATE, published_on="2026-09-01", topics=("products",))
    raw = {"items": [accepted({"id": candidate.id, "body": body}, text=text)]}
    return validate_grounded_response(raw, articles=[(candidate, body)], company=COMPANY, as_of=AS_OF)


def test_메타목록안의_회사명을_전체인용의_주어로_빌리지_않는다():
    body = "오후 두 시 차량 안전점검 서비스 진행 행사 개최 " + PANEL
    assert auxiliary_only_body(body)
    excerpts, rejected = validate(body, body)
    assert not excerpts and rejected["grounded_auxiliary_not_body"] == 1


def test_정상기사와_UI가_같이있어도_자기_문장만_보존한다():
    body = BODY + "\n" + PANEL + "\n" + "추가 취재 문단은 고객 서비스가 확대됐다고 설명했다."
    assert not auxiliary_only_body(body)
    assert validate(body, BODY)[0]
    assert not validate(body, body)[0]
    assert body[auxiliary_spans(body)[0][0]:auxiliary_spans(body)[0][1]] == PANEL


@pytest.mark.parametrize("body", (
    BODY,
    "가나다전자는 고객 30명에게 무료 산업설비 점검 서비스를 제공했다.",
    "가나다전자는 짧은 행사에서 산업설비 안전점검 서비스를 고객에게 제공했다.",
    "가나다전자는 관련 키워드 산업설비 고객 서비스를 검색하고 관련 기사를 추천한다.",
    "가나다전자는 관련 키워드와 관련 기사 기능을 개선해 고객에게 제공했다.",
    "관련 키워드 산업설비 고객 서비스와 관련 기사 제공은 회사가 설명한 새로운 기능이다.",
    '가나다전자는 "관련 키워드 산업설비 고객 서비스 관련 기사 자동 추천" 기능을 고객에게 제공했다.',
    "가나다전자는 ‘관련 키워드 산업설비 고객 서비스 관련 기사 자동 추천’ 기능을 고객에게 제공했다.",
))
def test_짧은정상기사와_기능설명은_메타목록으로_제외하지_않는다(body):
    assert not auxiliary_spans(body)
    assert not auxiliary_only_body(body)


def test_같은줄의_완결형_관련기사제목도_UI밖본문으로_승격하지않는다():
    body = PANEL + " " + BODY
    spans = auxiliary_spans(body)
    assert spans and spans[0][1] == len(body)
    assert not validate(body, BODY)[0]
    assert auxiliary_only_body(body)


def test_본문수집에서도_목록뿐인자료를_모델입력으로_넘기지않는다():
    from src.features.news_intake.body_prefetch import CallLease, fetch_article_body
    from src.features.news_intake.tests.test_body_prefetch import candidate, job
    original = "오후 두 시 안전점검 서비스 진행 " + PANEL
    outcome = fetch_article_body(job(lambda _: original), candidate(), CallLease())
    assert outcome.read_candidate is None
    assert outcome.excluded["auxiliary_only_not_body"] == 1
    assert original.endswith(PANEL)
    assert fetch_article_body(job(lambda _: BODY), candidate(), CallLease()).full_body == BODY


def test_회사주체가_UI밖에있는_짧은명사형기사는_수집기회를_유지한다():
    from src.features.news_intake.body_prefetch import CallLease, fetch_article_body
    from src.features.news_intake.tests.test_body_prefetch import candidate, job
    body = "가나다전자, 고객 대상 산업설비 무상 안전점검 서비스 진행 및 지역 주민 음악회 개최 " + PANEL
    outcome = fetch_article_body(job(lambda _: body), candidate(), CallLease())
    assert outcome.full_body == body and outcome.read_candidate is not None
    assert not validate(body, body)[0]
