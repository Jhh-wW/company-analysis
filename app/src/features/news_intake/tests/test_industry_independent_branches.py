"""회사 사건과 산업 문제의 독립 출력 계약을 합성 응답으로 검증한다."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.grounded import build_grounded_prompt, validate_grounded_response
from src.features.news_intake.industry_context import extend_prompt, split_response
from src.features.news_intake.models import NewsCollectionPolicy, NewsSearchSnapshot
from src.features.news_intake.quote_selection import quote_candidates
from src.features.news_intake.search_snapshot import company_digest, policy_digest, snapshot_digest
from src.features.news_intake.tests.test_collection import accepted
from src.features.news_intake.tests.test_industry_context import AS_OF, BODY, CANDIDATE, COMPANY, response
from src.shared.report_generation.models import exact_text_sha256

DIRECT = "예제법인은 산업설비 제조 사업을 운영하며 완성한 자동화 설비 신제품을 국내 고객사에 공급했다."


def selected(body, *, direct_text=None, problem_text=BODY, changes=None, candidate=CANDIDATE):
    """실제 모델 호출 없이 주어·사업 판정이 명시된 선택 응답을 만든다."""
    table = quote_candidates(candidate, body, COMPANY)

    def quote_id(text):
        return next(row["id"] for row in table if body[row["start"]:row["end"]] == text)

    raw = response(text=problem_text, **(changes or {}))
    row = raw["items"][0]
    row["id"] = candidate.id
    if direct_text is not None:
        row.update(accepted({"id": candidate.id, "body": body}, text=direct_text))
        row.pop("entity_evidence")
        row["entity_evidence_quote_id"] = quote_id(direct_text)
        excerpt = row["excerpts"][0]
        excerpt["text_quote_id"] = quote_id(excerpt.pop("text"))
        for name in ("subject_evidence", "time_evidence"):
            assert excerpt.pop(name) == ""
            excerpt[name + "_quote_id"] = ""
        excerpt["subject_is_target"] = True
    else:
        row.pop("entity_evidence")
        row["entity_evidence_quote_id"] = ""
    problem = row["industry_problems"][0]
    problem["text_quote_id"] = quote_id(problem.pop("text"))
    return raw


def check(raw, body, candidate=CANDIDATE):
    before = deepcopy(raw)
    direct, problems, rejected = split_response(
        raw, articles=[(candidate, body)], company=COMPANY, as_of=AS_OF,
        full_body_hashes={candidate.source_url: exact_text_sha256(body)},
    )
    excerpts, direct_rejected = validate_grounded_response(
        direct, articles=[(candidate, body)], company=COMPANY, as_of=AS_OF,
    )
    assert raw == before
    return excerpts, problems, rejected, direct_rejected


def test_동일기사의_독립두검사는_한호출과_기존선택원문을_유지한다():
    body = DIRECT + "\n" + BODY
    raw = selected(body, direct_text=DIRECT)
    policy = NewsCollectionPolicy(max_body_articles=1, max_analysis_calls=1,
                                  trusted_publisher_domains=("media.example",))
    snapshot = NewsSearchSnapshot(
        candidates=(CANDIDATE,), query_attempts=(), company_digest=company_digest(COMPANY),
        policy_digest=policy_digest(policy), as_of=AS_OF.isoformat(), digest="",
        status="success", reason_codes=(), cache_eligible=True,
    )
    snapshot = replace(snapshot, digest=snapshot_digest(snapshot))
    calls = []

    def analyze(prompt, schema, tokens):
        calls.append((prompt, schema, tokens))
        assert "회사명 유무와 same_company/material 판정에 관계없이" in prompt
        assert "한쪽이 비거나 실패했다는 이유로 다른 쪽을 비우지" in prompt
        assert json.loads(prompt.split("자료 시작:\n", 1)[1])["articles"][0]["body"] == body
        fields = schema["properties"]["items"]["items"]
        assert {"excerpts", "industry_problems"} <= set(fields["required"])
        return raw

    result = collect_from_snapshot(snapshot, company=COMPANY, as_of=AS_OF,
                                   fetch_text=lambda _: body, analyze_grounded=analyze, policy=policy)
    assert len(calls) == len(result.fragments) == len(result.industry_problems) == 1
    assert result.industry_problems[0].exact_text == BODY
    assert result.fragments[0].text == DIRECT


@pytest.mark.parametrize("prefix", ("", "예제법인은 산업설비 제조 업계의 구성원이다. "))
@pytest.mark.parametrize("company_flags", ((False, False), (True, True)))
def test_회사직접사건없이_회사명유무와무관하게_산업만_독립생존한다(prefix, company_flags):
    body = prefix + BODY
    raw = selected(body)
    raw["items"][0]["same_company"], raw["items"][0]["material"] = company_flags
    excerpts, problems, rejected, direct_rejected = check(raw, body)
    assert not excerpts and len(problems) == 1 and not rejected
    assert direct_rejected
    if not company_flags[0]:
        assert direct_rejected == {"grounded_wrong_company": 1}


def test_발행처명은_회사주어근거가아니며_산업생존이_회사인용을복구하지않는다():
    candidate = replace(CANDIDATE, publisher="예제법인")
    raw = selected(BODY, direct_text=BODY, candidate=candidate)
    excerpts, problems, rejected, direct_rejected = check(raw, BODY, candidate)
    assert not excerpts and len(problems) == 1 and not rejected and direct_rejected


@pytest.mark.parametrize("text", (
    "한국의 산업설비 제조 업계는 공급 지연 문제를 겪을 수 있다는 전망을 발표했다.",
    "한국의 산업설비 제조 업계는 공급 지연을 2022년에 해소했다.",
    "한국의 산업설비 제조 업계는 운임 계산 방식을 설명했다.",
    "한국의 산업설비 제조 업계는 신규 시장 진출 기회를 설명했다.",
))
def test_독립분기는_미래가능성_옛완료사건_단순어휘_기회를_현재문제로승격하지않는다(text):
    problem = "공급 지연" if "공급 지연" in text else "운임" if "운임" in text else "진출 기회"
    raw = selected(text, problem_text=text, changes={"problem": problem, "problem_present": False})
    assert not check(raw, text)[1]


def test_국내쿼리태그만으로_본문에없는지역을채우지않는다():
    body = BODY.replace("한국의 ", "")
    raw = selected(body, problem_text=body)
    assert CANDIDATE.topics == ("industry_domestic",)
    excerpts, problems, rejected, _ = check(raw, body)
    assert not excerpts and not problems and rejected == {"industry_unbound_problem": 1}


def test_회사인용실패를_자동산업변환하지않고_산업빈제안을보존한다():
    raw = selected(BODY, direct_text=BODY)
    raw["items"][0]["industry_problems"] = []
    excerpts, problems, rejected, direct_rejected = check(raw, BODY)
    assert not excerpts and not problems and not rejected and direct_rejected


def test_앵커없는prompt는_독립산업검사를추가하지않는다():
    plain = replace(COMPANY, business_anchors=())
    prompt = build_grounded_prompt(plain, [(CANDIDATE, BODY)], AS_OF)
    assert extend_prompt(prompt, plain) == prompt
