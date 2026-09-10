"""검색 어댑터부터 주소 비교·AI 재정렬까지 실제 경로의 후보 누락 회귀 시험."""

import json

import pytest

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


def resolve(monkeypatch, catalog, engine, *, query, address, ask=None):
    monkeypatch.setattr(real, "_company_catalog", lambda: catalog)
    monkeypatch.setattr(real, "_engine", lambda: engine)
    return resolve_candidates(
        configured_local_provider(real.RealPipeline()), company=query,
        address_hint=address, rate_key=f"검색회귀-{query}-{address}", rerank_ask=ask,
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
