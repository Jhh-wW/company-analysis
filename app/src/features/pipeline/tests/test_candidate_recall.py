"""검색 어댑터부터 주소 비교·AI 재정렬까지 실제 경로의 후보 누락 회귀 시험."""

import json
import uuid
from pathlib import Path

import pytest

from src.features.business_candidate import alias_resolution
from src.features.business_candidate.constants import (
    ALIAS_RESPONSE_KEY,
    ALIAS_STRONG_MATCH_KINDS,
    DART_ALIAS_SOURCE_LABEL,
    MIN_CANDIDATE_SCORE,
    PROVIDER_CALLS_PER_RESOLUTION,
)
from src.features.business_candidate.dart_identity import (
    build_dart_company_index,
    generate_dart_company_matches,
    parse_dart_company_records,
)
from src.features.business_candidate.logic import resolve_candidates
from src.features.business_candidate.providers import configured_local_provider
from src.features.pipeline import real


SM_CODE = "00260930"
SM_ADDRESS = "서울특별시 성동구 왕십리로 83-21 아크로 서울포레스트 디타워"
SM_CATALOG = (
    ("01491917", "에스엠", "SM", "", "20200806"),
    ("01101643", "에스엠", "SM", "", "20170630"),
    ("00238977", "에스엠화진", "SM HWAJIN Co., Ltd.", "134780", "20260604"),
    ("00783246", "글로벌에스엠", "Global SM Tech Limited", "900070", "20260416"),
    ("00147860", "에스엠벡셀", "SM BEXEL CO.,LTD", "010580", "20260227"),
    ("00185046", "SM C&C", "SM Culture & Contents Co., Ltd.", "048550", "20250326"),
    ("00367604", "SM Life Design", "SM Life Design Group Co., Ltd.", "063440", "20250325"),
    ("00577380", "신진에스엠", "SINJIN SM CO.,LTD.", "138070", "20241210"),
    (SM_CODE, "에스엠", "SM ENTERTAINMENT CO., Ltd.", "041510", "20240328"),
)


class ProfileEngine:
    MODEL = ""

    class UsageCounter:
        pass

    def __init__(self, catalog, *, target_code="", target_name="", target_address=""):
        self.calls = []
        self.profiles = {
            row[0]: {
                "status": "000", "corp_code": row[0], "corp_name": row[1],
                "adres": "서울특별시 강남구 테헤란로 83-21", "hm_url": "",
            }
            for row in catalog
        }
        if target_code:
            self.profiles[target_code].update(corp_name=target_name, adres=target_address)

    def load_env(self):
        pass

    def get_json(self, endpoint, params, counter):
        assert endpoint == "company.json"
        self.calls.append(params["corp_code"])
        return self.profiles[params["corp_code"]]


def resolve(
    monkeypatch, catalog, engine, *, query, address, ask=None, alias_ask=None,
    rate_suffix="",
):
    # ★ 후보 검색 횟수 제한은 rate_key 하나를 60초 창에서 센다. 같은 질의를 여러
    #   시험이 반복하면 뒤 시험이 «막힘»으로 조용히 통과하므로 꼬리표로 갈라 준다.
    monkeypatch.setattr(real, "_company_catalog", lambda: catalog)
    monkeypatch.setattr(real, "_engine", lambda: engine)
    return resolve_candidates(
        configured_local_provider(real.RealPipeline()), company=query,
        address_hint=address,
        rate_key=f"검색회귀-{query}-{address}-{rate_suffix}", rerank_ask=ask,
        alias_ask=alias_ask,
    )


@pytest.mark.parametrize("query", ["SM", "sm", "ＳＭ", "에스엠엔터테인먼트", "에스엠 엔터", "에스엠엔터"])
def test_sm_alias_reaches_first_screen_with_detailed_address(monkeypatch, query):
    engine = ProfileEngine(
        SM_CATALOG, target_code=SM_CODE, target_name="(주)에스엠엔터테인먼트",
        target_address=SM_ADDRESS,
    )
    result = resolve(monkeypatch, SM_CATALOG, engine, query=query, address=SM_ADDRESS)
    assert result.candidates[0].candidate_ref == SM_CODE
    assert result.candidates[0].candidate_name == "(주)에스엠엔터테인먼트"
    assert len(result.candidates) <= 3


@pytest.mark.parametrize("name", ["미래", "한빛", "알파"])
def test_homonyms_beyond_five_profiles_are_compared_by_address(monkeypatch, name):
    catalog = tuple((f"{i:08d}", name, "", "", "20250101") for i in range(1, 10))
    engine = ProfileEngine(catalog, target_code="00000009", target_name=name, target_address=SM_ADDRESS)
    result = resolve(monkeypatch, catalog, engine, query=name, address=SM_ADDRESS)
    assert result.candidates[0].candidate_ref == "00000009"


def test_real_dart_adapter_preserves_candidates_until_ai_rerank(monkeypatch):
    catalog = tuple((f"{i:08d}", f"테스트법인{i}", f"ABC Candidate {i}", "", "20250101") for i in range(1, 10))
    engine = ProfileEngine(catalog)
    prompts = []

    def ask(prompt):
        prompts.append(prompt)
        assert "테스트법인9" in prompt
        return json.dumps({"order": [8]})

    result = resolve(monkeypatch, catalog, engine, query="ABC", address="서울", ask=ask)
    assert len(prompts) == 1
    assert result.rerank_status == "applied"
    assert result.candidates[0].candidate_ref == "00000009"


def test_missing_profile_does_not_discard_other_valid_candidates(monkeypatch):
    engine = ProfileEngine(SM_CATALOG, target_code=SM_CODE, target_name="(주)에스엠엔터테인먼트", target_address=SM_ADDRESS)
    engine.profiles["01491917"] = {"status": "013"}
    result = resolve(monkeypatch, SM_CATALOG, engine, query="SM", address=SM_ADDRESS)
    assert result.candidates[0].candidate_ref == SM_CODE


@pytest.mark.parametrize("query", ["SM", "에스엠엔터테인먼트"])
def test_shared_building_preserves_name_and_homepage_score_differences(monkeypatch, query):
    engine = ProfileEngine(SM_CATALOG, target_code=SM_CODE, target_name="(주)에스엠엔터테인먼트", target_address=SM_ADDRESS)
    engine.profiles[SM_CODE]["hm_url"] = "smentertainment.com"
    engine.profiles["00185046"].update(
        corp_name="(주)에스엠컬처앤콘텐츠",
        adres="서울특별시 성동구 왕십리로 83-21 디타워동 14층(성수동1가, 아크로 서울포레스트)",
        hm_url="www.smcultureandcontents.com",
    )
    result = resolve(monkeypatch, SM_CATALOG, engine, query=query, address=SM_ADDRESS)
    assert SM_CODE in {candidate.candidate_ref for candidate in result.candidates}
    assert result.candidates[0].score != result.candidates[1].score
    if query == "에스엠엔터테인먼트":
        assert result.candidates[0].candidate_ref == SM_CODE


# ── 별명 → 정식 법인명 AI 번역 (누락 회귀) ─────────────────────────
#
# 사람이 적은 브랜드명이 등록 상호와 글자가 하나도 겹치지 않으면 결정적 검색은
# 후보를 한 건도 못 만든다. 아래 시험은 그 자리에서만 AI가 «이름을 옮기고»,
# 옮긴 이름으로 같은 로컬 색인을 다시 찾는 경로를 실제 어댑터로 확인한다.

BRAND_CODE = "01088217"
BRAND_LEGAL_NAME = "우아한형제들"
BRAND_ALIAS = "배민"
BRAND_ADDRESS = "서울특별시 송파구 위례성대로 2 장은빌딩"
ALIAS_CATALOG = (
    (BRAND_CODE, BRAND_LEGAL_NAME, "Woowa Brothers Corp.", "", "20260101"),
    ("00111111", "가나다전자", "GANADA ELECTRONICS", "111111", "20250101"),
    ("00222222", "라마바산업", "RAMABA INDUSTRY", "", "20250101"),
)


class 각본_번역_ask:
    """네트워크를 쓰지 않는 번역 ask 대역. 실제 계량 ask와 같은 자리에 들어간다."""

    def __init__(self, response):
        self._response = response
        self.prompts: list[str] = []
        self.reported: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        if isinstance(self._response, BaseException):
            raise self._response
        return self._response

    def report(self, status) -> None:
        self.reported.append(str(status))


def 번역응답(*names) -> str:
    return json.dumps({ALIAS_RESPONSE_KEY: list(names)}, ensure_ascii=False)


def 별명검색(monkeypatch, *, query, response, catalog=ALIAS_CATALOG, address=BRAND_ADDRESS):
    engine = ProfileEngine(
        catalog, target_code=BRAND_CODE, target_name=BRAND_LEGAL_NAME,
        target_address=BRAND_ADDRESS,
    )
    ask = 각본_번역_ask(response)
    result = resolve(
        monkeypatch, catalog, engine, query=query, address=address, alias_ask=ask,
        rate_suffix=uuid.uuid4().hex,
    )
    return ask, result


def test_별명은_AI가_옮긴_정식명으로_다시_찾아_후보에_든다(monkeypatch):
    """★ 실측 근거: 결정적 경로만으로는 이 질의가 «빈 결과»였다."""

    없음 = resolve(
        monkeypatch,
        ALIAS_CATALOG,
        ProfileEngine(ALIAS_CATALOG),
        query=BRAND_ALIAS,
        address=BRAND_ADDRESS,
        rate_suffix=uuid.uuid4().hex,
    )
    assert 없음.candidates == ()
    assert 없음.alias_status == ""

    ask, result = 별명검색(
        monkeypatch, query=BRAND_ALIAS, response=번역응답(BRAND_LEGAL_NAME)
    )

    assert len(ask.prompts) == 1
    assert BRAND_ALIAS in ask.prompts[0]
    assert result.alias_status == alias_resolution.ALIAS_STATUS_APPLIED
    첫째 = result.candidates[0]
    assert 첫째.candidate_ref == BRAND_CODE
    assert 첫째.candidate_name == BRAND_LEGAL_NAME
    # 어떤 경로로 들어왔는지 화면이 구분할 수 있어야 한다.
    assert 첫째.alias_source == "ai"
    assert 첫째.alias_name == BRAND_LEGAL_NAME
    assert 첫째.source_label == DART_ALIAS_SOURCE_LABEL
    # 사용자가 적은 별명으로 점수를 재면 여기서 잘려 화면에 닿지 못한다.
    assert 첫째.score >= MIN_CANDIDATE_SCORE


def test_강한_후보가_이미_있으면_AI를_부르지_않는다(monkeypatch):
    ask, result = 별명검색(
        monkeypatch, query=BRAND_LEGAL_NAME, response=번역응답("다른회사")
    )
    assert ask.prompts == []
    assert result.alias_status == alias_resolution.ALIAS_STATUS_NOT_NEEDED
    assert result.candidates[0].candidate_ref == BRAND_CODE
    assert result.candidates[0].alias_source == ""
    assert result.candidates[0].source_label == "전자공시(DART) 기업개황"


def test_영문_약어_질의에_후보가_있으면_AI를_부르지_않는다(monkeypatch):
    engine = ProfileEngine(
        SM_CATALOG, target_code=SM_CODE, target_name="(주)에스엠엔터테인먼트",
        target_address=SM_ADDRESS,
    )
    ask = 각본_번역_ask(번역응답("에스엠엔터테인먼트"))
    result = resolve(
        monkeypatch, SM_CATALOG, engine, query="SM", address=SM_ADDRESS, alias_ask=ask,
        rate_suffix=uuid.uuid4().hex,
    )
    assert ask.prompts == []
    assert result.alias_status == alias_resolution.ALIAS_STATUS_NOT_NEEDED
    assert result.candidates[0].candidate_ref == SM_CODE


def test_결정적_후보가_0건이면_영문_질의도_AI를_부른다(monkeypatch):
    ask, result = 별명검색(
        monkeypatch, query="ZZQXW", response=번역응답(BRAND_LEGAL_NAME)
    )
    assert len(ask.prompts) == 1
    assert result.alias_status == alias_resolution.ALIAS_STATUS_APPLIED
    assert result.candidates[0].candidate_ref == BRAND_CODE


def test_비용_승인을_못_받으면_호출없이_결정적_결과만_돌려준다(monkeypatch):
    """web 계층이 예산 거절 시 ask를 None으로 낮춘 상태를 그대로 모사한다."""

    engine = ProfileEngine(
        ALIAS_CATALOG, target_code=BRAND_CODE, target_name=BRAND_LEGAL_NAME,
        target_address=BRAND_ADDRESS,
    )
    result = resolve(
        monkeypatch, ALIAS_CATALOG, engine, query=BRAND_LEGAL_NAME,
        address=BRAND_ADDRESS, alias_ask=None, rate_suffix=uuid.uuid4().hex,
    )
    assert result.alias_status == ""
    assert result.candidates[0].candidate_ref == BRAND_CODE
    assert result.candidates[0].alias_source == ""


@pytest.mark.parametrize(
    "response",
    ["설명입니다", '{"order":[0]}', 번역응답("https://baemin.com"), RuntimeError("down")],
)
def test_이상한_응답과_예외는_결정적_결과를_그대로_지킨다(monkeypatch, response):
    ask, result = 별명검색(monkeypatch, query=BRAND_ALIAS, response=response)
    assert len(ask.prompts) == 1
    assert result.candidates == ()
    assert result.alias_status in {
        alias_resolution.ALIAS_STATUS_INVALID_RESPONSE,
        alias_resolution.ALIAS_STATUS_FAILED,
    }


def test_색인에_없는_이름을_받으면_못찾음으로_남긴다(monkeypatch):
    ask, result = 별명검색(
        monkeypatch, query=BRAND_ALIAS, response=번역응답("존재하지않는법인명")
    )
    assert len(ask.prompts) == 1
    assert result.candidates == ()
    assert result.alias_status == alias_resolution.ALIAS_STATUS_NO_MATCH


def test_약한_종류로만_걸리는_이름은_후보로_채택하지_않는다(monkeypatch):
    """AI가 준 이름의 오차와 색인의 오차를 곱하지 않는다.

    ★ 이 이름은 색인에서 실제로 `trigram`(약한 종류) 후보를 만든다. 필터가
      없으면 그대로 채택되므로, 이 시험이 곧 필터의 존재 증명이다.
    """

    약한이름 = f"{BRAND_LEGAL_NAME}s"
    ask, result = 별명검색(
        monkeypatch, query=BRAND_ALIAS, response=번역응답(약한이름)
    )
    assert len(ask.prompts) == 1
    assert result.candidates == ()
    assert result.alias_status == alias_resolution.ALIAS_STATUS_NO_MATCH


def test_고유번호_정확일치_질의는_AI를_열지_않고_그_번호가_1위다(monkeypatch):
    """발동 조건이 exact_id를 강한 종류로 세므로 AI 후보가 끼어들 자리가 없다."""

    ask, result = 별명검색(
        monkeypatch, query=BRAND_CODE, response=번역응답("가나다전자")
    )
    assert ask.prompts == []
    assert result.alias_status == alias_resolution.ALIAS_STATUS_NOT_NEEDED
    assert result.candidates[0].candidate_ref == BRAND_CODE
    assert result.candidates[0].name_match_kind == "exact_id"


def test_이미_찾은_고유번호는_AI_경로로_다시_들어오지_않는다(monkeypatch):
    """중복 제거가 없으면 같은 회사가 두 장으로 보여 사람이 헷갈린다."""

    catalog = (
        (BRAND_CODE, BRAND_LEGAL_NAME, "Woowa Brothers Corp.", "", "20260101"),
        ("00333333", "우아한형제", "WOOWA BROTHER", "", "20250101"),
    )
    ask, result = 별명검색(
        monkeypatch, query="우아한형제", response=번역응답(BRAND_LEGAL_NAME),
        catalog=catalog,
    )
    refs = [item.candidate_ref for item in result.candidates]
    assert len(refs) == len(set(refs))
    if ask.prompts:
        assert refs.count(BRAND_CODE) <= 1


def test_공급자는_번역을_해도_여전히_한_번만_불린다(monkeypatch):
    """번역은 어댑터 «안»에서 돈다. 공급자 호출 상한 1회는 그대로다."""

    engine = ProfileEngine(
        ALIAS_CATALOG, target_code=BRAND_CODE, target_name=BRAND_LEGAL_NAME,
        target_address=BRAND_ADDRESS,
    )
    monkeypatch.setattr(real, "_company_catalog", lambda: ALIAS_CATALOG)
    monkeypatch.setattr(real, "_engine", lambda: engine)
    pipeline = real.RealPipeline()
    호출 = []
    원본 = pipeline.search_business_candidates

    def 세면서(**kwargs):
        호출.append(kwargs)
        return 원본(**kwargs)

    pipeline.search_business_candidates = 세면서
    ask = 각본_번역_ask(번역응답(BRAND_LEGAL_NAME))
    result = resolve_candidates(
        configured_local_provider(pipeline), company=BRAND_ALIAS,
        address_hint=BRAND_ADDRESS,
        rate_key=f"검색회귀-공급자1회-{uuid.uuid4().hex}", alias_ask=ask,
    )
    assert len(호출) == PROVIDER_CALLS_PER_RESOLUTION == 1
    assert len(ask.prompts) == 1
    assert result.candidates[0].candidate_ref == BRAND_CODE


def test_번역_예약액은_최악_프롬프트의_호출전_추정액을_덮는다():
    """예약이 추정액보다 작으면 번역은 전송 전에 100% 거절된다.

    추정액은 생산 경로(`estimate_request_tokens_exact` → `usage_cost_krw`)로
    직접 잰다. 상수를 낮추거나 프롬프트가 커지면 이 시험이 먼저 깨진다.
    """

    from src.features.budget import provider_budget  # noqa: PLC0415
    from src.features.business_candidate.constants import (  # noqa: PLC0415
        AI_ALIAS_MAX_OUTPUT_TOKENS,
        AI_ALIAS_RESERVE_KRW,
        MAX_ADDRESS_CHARS,
        MAX_NAME_CHARS,
    )

    # 경계 안에서 가장 긴 입력. 이보다 긴 값은 resolver가 먼저 잘라 낸다.
    prompt = alias_resolution.build_alias_prompt(
        query="가" * MAX_NAME_CHARS, address_hint="서" * MAX_ADDRESS_CHARS
    )
    call_kwargs = {
        "model": "claude-haiku-4-5",
        "max_tokens": AI_ALIAS_MAX_OUTPUT_TOKENS,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": real._CANDIDATE_ALIAS_SCHEMA,
            }
        },
    }
    # provider가 입력 token을 세어 주지 못하는 최악의 경우(바이트 추정)를 잰다.
    estimated_input = provider_budget.estimate_request_tokens_exact(
        {"args": (), "kwargs": call_kwargs}, exact_input_tokens=None
    )
    estimate_krw = provider_budget.usage_cost_krw(
        "claude-haiku-4-5", estimated_input, AI_ALIAS_MAX_OUTPUT_TOKENS
    )

    # 2026-09-10 실측 11.10원(고정 여유 4,096 token만으로 5.74원).
    assert 10.0 < estimate_krw <= AI_ALIAS_RESERVE_KRW


def test_번역_스키마는_이름_목록_하나만_받는다():
    """스키마가 느슨해지면 provider가 설명·주소를 실어 보낼 수 있다."""

    from src.features.business_candidate.constants import (  # noqa: PLC0415
        AI_ALIAS_MAX_NAMES,
        MAX_NAME_CHARS,
    )

    schema = real._CANDIDATE_ALIAS_SCHEMA
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {ALIAS_RESPONSE_KEY}
    names = schema["properties"][ALIAS_RESPONSE_KEY]
    assert names["maxItems"] == AI_ALIAS_MAX_NAMES
    assert names["items"]["type"] == "string"
    assert names["items"]["maxLength"] == MAX_NAME_CHARS


def test_어댑터가_폭을_좁혀도_AI_후보가_먼저_잘리지_않는다(monkeypatch):
    """어댑터의 반환 폭(`limit`)은 어댑터 «안»의 점수 순서로 자른다.

    AI 후보가 합류 순서 때문에 뒤로 밀리면 폭이 좁을 때 화면에 닿기 전에 잘린다.
    여기서는 결정적 후보(약한 trigram)보다 AI 후보(정확 일치)가 점수가 높아
    폭 1에서도 살아남아야 한다.
    """

    engine = ProfileEngine(
        ALIAS_CATALOG, target_code=BRAND_CODE, target_name=BRAND_LEGAL_NAME,
        target_address=BRAND_ADDRESS,
    )
    monkeypatch.setattr(real, "_company_catalog", lambda: ALIAS_CATALOG)
    monkeypatch.setattr(real, "_engine", lambda: engine)
    ask = 각본_번역_ask(번역응답("가나다전자"))
    rows = real.RealPipeline().search_business_candidates(
        # 약한(trigram) 후보만 걸리는 질의라 번역 단계가 열린다.
        company=f"{BRAND_LEGAL_NAME}s",
        # 주소 점수를 빼야 «이름 근거» 차이만으로 순서가 정해진다.
        address_hint="",
        limit=1,
        timeout_sec=30.0,
        alias_ask=ask,
    )
    assert len(ask.prompts) == 1
    assert [row["candidate_ref"] for row in rows] == ["00111111"]
    assert rows[0]["alias_source"] == "ai"
    assert rows[0]["alias_name"] == "가나다전자"


def test_약한_후보가_보강_상한을_채워도_AI_후보가_밀려나지_않는다(monkeypatch):
    """정확 근거 후보를 먼저 담는 lookahead 덕에 AI 후보가 자리를 얻는다."""

    다수 = tuple(
        (f"{index:08d}", f"가나다{index}", "", "", "20250101")
        for index in range(1, 30)
    )
    catalog = ((BRAND_CODE, BRAND_LEGAL_NAME, "", "", "20260101"),) + 다수
    engine = ProfileEngine(
        catalog, target_code=BRAND_CODE, target_name=BRAND_LEGAL_NAME,
        target_address=BRAND_ADDRESS,
    )
    ask = 각본_번역_ask(번역응답(BRAND_LEGAL_NAME))
    result = resolve(
        monkeypatch, catalog, engine, query="가나다", address=BRAND_ADDRESS,
        alias_ask=ask, rate_suffix=uuid.uuid4().hex,
    )
    assert len(ask.prompts) == 1
    assert result.alias_status == alias_resolution.ALIAS_STATUS_APPLIED
    assert BRAND_CODE in {item.candidate_ref for item in result.candidates}


@pytest.mark.local_integration
def test_로컬통합_실제_CORPCODE에서_별명이_상위3에_든다():
    """각본 ask가 정식명을 주면 실제 12만 건 색인에서도 상위 3 안에 드는가.

    ★ 기대 고유번호는 지어내지 않았다. 같은 CORPCODE 사본에서 정식명을 찾아
      2026-09-10에 실측한 값이다(`.local-artifacts/alias-ai/lookup.txt`).
    """

    app_root = Path(__file__).resolve().parents[4]
    cached_xml = tuple(
        (app_root / ".local_evaluation_runs").glob(
            "*/analysis_engine/corpcode/CORPCODE.xml"
        )
    )
    if not cached_xml:
        pytest.fail(
            "로컬 통합 시험을 선택했지만 DART CORPCODE.xml cache를 찾지 못했습니다"
        )
    newest_xml = max(cached_xml, key=lambda item: item.stat().st_mtime_ns)
    records = parse_dart_company_records(newest_xml)
    index = build_dart_company_index(records)
    assert len(records) >= 100_000

    # (사람이 적는 별명, 실제 DART 등록명, 실측 고유번호)
    사례 = (
        ("배민", "우아한형제들", "01063273"),
        ("토스", "비바리퍼블리카", "01212921"),
        ("오늘의집", "버킷플레이스", "01381054"),
        ("롯데온", "롯데쇼핑", "00120526"),
        ("강남언니", "힐링페이퍼", "01548941"),
        ("스타벅스", "에스씨케이컴퍼니", "00359386"),
        ("GS25", "GS리테일", "00140177"),
        ("업비트", "두나무", "01310241"),
        ("현대차", "현대자동차", "00164742"),
        ("카뱅", "카카오뱅크", "01133217"),
        ("케뱅", "케이뱅크", "01203312"),
        ("네이버", "NAVER", "00266961"),
    )
    실패: list[str] = []
    번역이_구한_건수 = 0
    for 별명, 정식명, 기대코드 in 사례:
        결정적 = generate_dart_company_matches(index, 별명, limit=15)
        if 기대코드 in [item.record.corp_code for item in 결정적[:3]]:
            # 결정적 경로가 이미 찾았다면 번역이 돌 이유가 없다.
            continue
        names, status = alias_resolution.alias_names_from_ask(
            query=별명,
            address_hint="",
            ask=lambda _prompt, 이름=정식명: 번역응답(이름),
            match_kinds=[item.match_kind for item in 결정적],
        )
        if status != alias_resolution.ALIAS_STATUS_APPLIED:
            실패.append(f"{별명}: 번역 단계가 {status}")
            continue
        재검색 = [
            item
            for name in names
            for item in generate_dart_company_matches(index, name, limit=15)
            if item.match_kind in ALIAS_STRONG_MATCH_KINDS
        ]
        코드 = [item.record.corp_code for item in 재검색[:3]]
        if 기대코드 not in 코드:
            실패.append(f"{별명}: 상위3 코드 {코드}에 {기대코드} 없음")
            continue
        번역이_구한_건수 += 1

    assert not 실패, 실패
    # 전부 결정적으로 찾혔다면 이 시험은 아무것도 확인하지 않은 것이다.
    # 실측(2026-09-10)에서는 12건 모두 번역 경로로 구했다.
    assert 번역이_구한_건수 >= 10, 번역이_구한_건수
