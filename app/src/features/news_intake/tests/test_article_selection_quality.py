"""명시 명칭·연속 인용 경계·5장 의미·고정 산업 조사 기회를 검증한다."""

from dataclasses import replace
import json

import pytest

from src.features.news_intake.article_identity import article_company_context
from src.features.news_intake.grounded import build_grounded_prompt, validate_grounded_response
from src.features.news_intake.quote_selection import quote_candidates, _selection_subject_supported
from src.features.news_intake import quote_selection_constants as qc
from src.features.news_intake.search_snapshot import body_ranked_candidates, _industry_candidates
from src.features.news_intake.search_snapshot import company_digest
from src.features.news_intake.tests.test_collection import AS_OF, BODY, COMPANY, accepted, collect, item, snapshot
from src.features.news_intake.tests.test_quote_selection import selected, validate
from src.features.news_intake.tests import test_industry_context as industry
from src.features.news_intake.models import NewsCompanyContext

LEGAL = "가나다앤테크놀로지"
SHORT = "가나다"
CONTEXT = NewsCompanyContext(LEGAL)
DECLARATION = f"{LEGAL}({SHORT})는 산업설비 기업이다."
ACTION = f"{SHORT}는 국내 공장의 자동화 공정에 사용할 산업설비 신제품을 개발해 고객사에 공급했다."
UI = "▶ 기사 듣기 00:00 / 구글 선호매체 추가 카카오톡 URL공유 가장작게 가장크게 "


def test_같은기사의_명시명칭만_지역문맥에_추가한다():
    body = DECLARATION + " " + ACTION
    local = article_company_context(body, CONTEXT)
    assert local.aliases == (SHORT,) and CONTEXT.aliases == ()
    assert article_company_context(ACTION, CONTEXT) is CONTEXT
    assert _selection_subject_supported(body, CONTEXT)
    assert _selection_subject_supported(ACTION, CONTEXT, body)


def test_기사지역명칭은_공식회사메타_프롬프트_다른기사로_전달하지않는다():
    candidate = snapshot([item()])[0].candidates[0]
    before = company_digest(CONTEXT)
    first = DECLARATION + " " + ACTION
    quote_candidates(candidate, first, CONTEXT)
    prompt = build_grounded_prompt(CONTEXT, [(candidate, first)], AS_OF, selection=True)
    payload = json.loads(prompt.split("자료 시작:\n")[1])
    assert payload["company"]["aliases"] == []
    assert SHORT not in payload["verified_company_names"]
    assert CONTEXT.aliases == () and company_digest(CONTEXT) == before
    second = "다른기술은 산업설비 제조 기업이다. " + ACTION
    assert article_company_context(second, CONTEXT) is CONTEXT
    raw = selected(candidate, second, company=CONTEXT)
    assert not validate(raw, candidate, second, CONTEXT)[0]


def test_앞문장이있어도_전체명칭의경계에서만_선언을읽는다():
    body = "제품을 소개했다. " + DECLARATION + " " + ACTION
    assert article_company_context(body, CONTEXT).aliases == (SHORT,)
    assert article_company_context("제품을 소개했다. 구" + DECLARATION, CONTEXT) is CONTEXT
    english = NewsCompanyContext("ACME Sensor Technologies")
    assert article_company_context("The product was introduced. ACME Sensor Technologies(ACME Sensor) is a supplier.", english) is english


@pytest.mark.parametrize("declaration", [
    f"{LEGAL}의 자회사({SHORT})는 산업설비를 공급했다.",
    f"{LEGAL}(대표 {SHORT})는 산업설비 기업이다.",
    f"{LEGAL}({SHORT}와 협력)는 산업설비 기업이다.",
    f"{LEGAL}(자회사 {SHORT})는 산업설비 기업이다.",
    f"{LEGAL}(경쟁사 {SHORT})는 산업설비 기업이다.",
    f"{LEGAL}(공동사업 상대 {SHORT})는 산업설비 기업이다.",
    f"구{LEGAL}({SHORT})는 산업설비 기업이다.",
    f"{LEGAL}홀딩스({SHORT})는 산업설비 기업이다.",
    f"다른법인({SHORT})는 산업설비 기업이다.",
    DECLARATION + f" 다른기술({SHORT})는 별도 업체다.",
    DECLARATION + f" 경쟁사 {SHORT}는 별도 업체다.",
    DECLARATION + f" {SHORT}는 별도 법인이다.",
    DECLARATION + f" {SHORT}는 다른 법인이다.",
    DECLARATION + f" {SHORT}는 {LEGAL}의 자회사다.",
    DECLARATION + f" {SHORT}는 {LEGAL}의 경쟁사다.",
])
def test_자회사_인물_협력상대_다른법인_유사명칭은_별칭이아니다(declaration):
    assert article_company_context(declaration + " " + ACTION, CONTEXT) is CONTEXT


def test_명시별칭이있어도_다른법인_사업행동은_승격하지않는다():
    text = DECLARATION + " 다른기술은 산업설비 신제품을 개발해 공급했다."
    assert not _selection_subject_supported(text, CONTEXT)


def test_기사법인모순은_영문대소문자변형으로_우회하지못한다():
    company = NewsCompanyContext("Nare Technologies")
    body = "Nare Technologies(Nare)는 결제 서비스 회사다. NARE는 별도 법인이다."
    assert article_company_context(body, company) is company


def test_명시별칭의_단독인용은_법인원근거와_주어모델판정을_계속요구한다():
    body = DECLARATION + " " + ACTION
    candidate = snapshot([item()])[0].candidates[0]
    raw = selected(candidate, body, company=CONTEXT)
    quote = next(row for row in quote_candidates(candidate, body, CONTEXT) if body[row["start"]:row["end"]] == ACTION)
    raw["items"][0]["excerpts"][0]["text_quote_id"] = quote["id"]
    result, rejected = validate(raw, candidate, body, CONTEXT)
    assert result and result[0].text == ACTION and not rejected
    raw["items"][0]["excerpts"][0]["subject_is_target"] = False
    assert not validate(raw, candidate, body, CONTEXT)[0]
    raw["items"][0]["excerpts"][0]["subject_is_target"] = True
    raw["items"][0]["same_company"] = False
    assert not validate(raw, candidate, body, CONTEXT)[0]


def test_UI앞부분을_피한_원문offset_ID를_제공한다():
    candidate = snapshot([item()])[0].candidates[0]
    body = UI + BODY
    quotes = quote_candidates(candidate, body, COMPANY)
    clean = next(row for row in quotes if body[row["start"]:row["end"]] == BODY)
    assert clean["start"] == len(UI) and body == UI + BODY
    assert all("기사 듣기" not in body[row["start"]:row["end"]] for row in quotes)
    assert quote_candidates(candidate, body.replace("120", "121"), COMPANY)[0]["id"] != clean["id"]
    assert len(quotes) <= qc.QUOTE_MAX_CANDIDATES_PER_ARTICLE
    encoded = build_grounded_prompt(COMPANY, [(candidate, body)], AS_OF, selection=True)
    assert json.loads(encoded.split("자료 시작:\n")[1])["articles"][0]["body"] == body


def test_닫힌UI패널아닌_회사행동의앞절은_삭제하지않는다():
    candidate = snapshot([item()])[0].candidates[0]
    body = "고객의 요청을 반영해 " + BODY
    assert any(body[row["start"]:row["end"]] == body for row in quote_candidates(candidate, body, COMPANY))


@pytest.mark.parametrize("prefix,start", [
    ("기사 듣기, 선호매체 추가, URL공유 기능의 장애로 이용자 결제가 지연됐다. ", 0),
    (UI + "설비 고장으로 고객 납품 차질이 심화됐다. ", len(UI)),
])
def test_UI표지와함께있는_실제문제앞절은_원문후보에서_보존한다(prefix, start):
    candidate = snapshot([item()])[0].candidates[0]
    body = prefix + BODY
    assert any(row["start"] == start and row["end"] == len(body)
               for row in quote_candidates(candidate, body, COMPANY))


def test_UI패널뒤실제문제는_깨끗한인용으로_생존하고_패널전체인용은_거절한다():
    candidate = snapshot([item()])[0].candidates[0]
    problem = "설비 고장으로 고객 납품 차질이 심화됐다. "
    body = UI + problem + BODY
    raw = selected(candidate, BODY)
    clean = next(row for row in quote_candidates(candidate, body, COMPANY)
                 if body[row["start"]:row["end"]] == problem + BODY)
    raw["items"][0]["entity_evidence_quote_id"] = clean["id"]
    for name in ("text_quote_id", "time_evidence_quote_id", "subject_evidence_quote_id"):
        if raw["items"][0]["excerpts"][0][name]:
            raw["items"][0]["excerpts"][0][name] = clean["id"]
    assert validate(raw, candidate, body)[0]
    legacy = accepted({"id": candidate.id, "body": body}, text=body)
    legacy["entity_evidence"] = BODY
    assert not validate({"items": [legacy]}, candidate, body)[0]


def test_역방향타법인모순이있으면_단축명칭인용이_출고되지않는다():
    candidate = snapshot([item()])[0].candidates[0]
    body = DECLARATION + f" {SHORT}는 {LEGAL}의 자회사다. " + ACTION
    raw = selected(candidate, body, company=CONTEXT)
    quote = next(row for row in quote_candidates(candidate, body, CONTEXT)
                 if body[row["start"]:row["end"]] == ACTION)
    raw["items"][0]["excerpts"][0]["text_quote_id"] = quote["id"]
    assert not validate(raw, candidate, body, CONTEXT)[0]


def test_후보0으로구형응답을사용해도_UI잡음을_공개하지않는다():
    body = UI + BODY.rstrip(".") + " 세부 규격" * 300
    candidate = snapshot([item()])[0].candidates[0]
    assert quote_candidates(candidate, body, COMPANY) == []
    calls = []
    def analyze(prompt, schema, max_tokens):
        calls.append(schema)
        article = json.loads(prompt.split("자료 시작:\n")[1])["articles"][0]
        row = accepted(article, text=UI + BODY.rstrip("."))
        row["entity_evidence"] = BODY.rstrip(".")
        return {"items": [row]}
    result = collect(fetch=lambda url: body, analyze=analyze)
    assert len(calls) == 1 and not result.fragments
    assert "entity_evidence" in calls[0]["properties"]["items"]["items"]["properties"]
    assert result.diagnostics["제외"].get("grounded_ui_prefix", 0) == 1


def test_수상과_PPA긍정성과만으로_5장대응을_채우지않는다():
    text = "가나다전자는 산업설비 제조 부문의 세계 최고의 기업에 선정됐다. 회사는 재생에너지 PPA 체결로 ESG 성과를 거뒀다고 밝혔다."
    candidate = snapshot([item()])[0].candidates[0]
    raw = selected(candidate, text)
    excerpt = raw["items"][0]["excerpts"][0]
    excerpt.update(section_id="current_challenges", claim_slot="current_challenges:response", claim_kind="company_statement")
    result, rejected = validate(raw, candidate, text)
    assert not result and rejected.get("challenge_response_problem_unbound") == 1


def test_현재사업문제와_해당대응의_정상뉴스는_남는다():
    text = "가나다전자는 현재 산업설비 제품의 불량으로 국내 납품 차질이 지속되고 있어 생산설비 점검과 품질 개선을 진행하고 있다고 밝혔다."
    candidate = snapshot([item()])[0].candidates[0]
    raw = selected(candidate, text)
    excerpt = raw["items"][0]["excerpts"][0]
    excerpt.update(section_id="current_challenges", claim_slot="current_challenges:response", claim_kind="company_statement")
    result, rejected = validate(raw, candidate, text)
    assert result and result[0].text == text and not rejected


def test_산업주제만달린_타산업후보는_예약기회를_독점하지않는다():
    anchor_id = industry.ANCHOR.anchor_id
    wrong = replace(industry.CANDIDATE, id="wrong", title="식품회사 대표 산업공학과 이력", description="주차장 공급난",
                    topics=("industry_domestic:" + anchor_id,), published_on="2026-09-05")
    problem = replace(wrong, id="problem", title="산업설비 제조 부품 공급 지연", description="현재 생산 차질", published_on="2026-09-01")
    ordinary = [replace(wrong, id=f"normal-{index}", topics=("business",)) for index in range(4)]
    assert [row.id for row in _industry_candidates([wrong, problem], company=industry.COMPANY)] == ["problem", "wrong"]
    ranked = body_ranked_candidates([wrong, problem, *ordinary], attempt_budget=6, probe_budget=1, company=industry.COMPANY)
    assert ranked[0].id == "problem" and {row.id for row in ranked} == {"wrong", "problem", *(row.id for row in ordinary)}
    # 메타가 약한 후보도 제한된 탐색 대상이며 본문 관련성 승인은 별도다.
    assert _industry_candidates([wrong], company=industry.COMPANY) == [wrong]


def test_사업명과문제는_메타의같은절에_있어야_문제우선순위를준다():
    topic = ("industry_domestic:" + industry.ANCHOR.anchor_id,)
    business = industry.ANCHOR.business_item
    historical = replace(industry.CANDIDATE, id="historical", title=f"{business} 기업의 창업자 이야기",
                         description=f"그 기업은 {business} 업체다. 전쟁 직전에 원료 공급난을 예상했다.",
                         topics=topic, published_on="2026-09-05")
    current = replace(historical, id="current", title=f"{business} 업계 원가 상승 부담", description="현재 원가 부담",
                      published_on="2026-09-01")
    assert _industry_candidates([historical, current], company=industry.COMPANY)[0].id == "current"
