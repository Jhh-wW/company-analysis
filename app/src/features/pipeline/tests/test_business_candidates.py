"""DART-local 회사 후보의 외부 호출·환경 bootstrap 상한."""

from __future__ import annotations

import time
from pathlib import Path

from src.features.pipeline import real
from src.features.pipeline.port import UserInput


class _CandidateEngine:
    class UsageCounter:
        pass

    MODEL = ""

    def __init__(self, *, delay: float = 0.0):
        self.delay = delay
        self.calls: list[str] = []
        self.loaded = 0

    def load_env(self):
        self.loaded += 1

    def get_json(self, _path, params, _counter):
        self.calls.append(str(params["corp_code"]))
        if self.delay:
            time.sleep(self.delay)
        return {
            "status": "000",
            "corp_name": f"(주)제이와이피엔터테인먼트{params['corp_code']}",
            "adres": "서울특별시 강동구 강동대로 205",
            "hm_url": "jype.com",
        }


def _catalog(size: int = 10):
    return tuple(
        (
            f"{index + 1:08d}",
            f"후보회사{index}",
            f"JYP Candidate {index} Corporation",
            "",
            f"202501{index + 1:02d}",
        )
        for index in range(size)
    )


def test_DART_local후보는_요청당_company_profile을_최대3건만_부른다(monkeypatch):
    engine = _CandidateEngine()
    monkeypatch.setattr(real, "_engine", lambda: engine)
    monkeypatch.setattr(real, "_company_catalog", _catalog)

    rows = real.RealPipeline().search_business_candidates(
        company="JYP", address_hint="서울 강동구", limit=15, timeout_sec=8.0
    )

    assert len(rows) == 3
    assert engine.calls == ["00000010", "00000009", "00000008"]


def test_DART_local후보는_deadline뒤_남은_profile을_계속_부르지_않는다(monkeypatch):
    engine = _CandidateEngine(delay=0.12)
    monkeypatch.setattr(real, "_engine", lambda: engine)
    monkeypatch.setattr(real, "_company_catalog", _catalog)

    rows = real.RealPipeline().search_business_candidates(
        company="JYP", address_hint="서울 강동구", limit=3, timeout_sec=0.03
    )

    assert len(rows) == 1
    assert engine.calls == ["00000010"]


def test_JYP_강동구는_XML순서와무관하게_현재상장사를_첫후보로_두고_옛법인도_비교시킨다(
    monkeypatch,
):
    class JypCandidateEngine(_CandidateEngine):
        def get_json(self, _path, params, _counter):
            code = str(params["corp_code"])
            self.calls.append(code)
            profiles = {
                "00258689": {
                    "status": "000",
                    "corp_name": "JYP Ent.",
                    "adres": "서울특별시 강동구 강동대로 205",
                    "hm_url": "jype.com",
                },
                "00535454": {
                    "status": "000",
                    "corp_name": "(주)제이와이피",
                    "adres": "서울특별시 강남구 청담동 123-50",
                    "hm_url": "jype.com",
                },
                "00999999": {
                    "status": "000",
                    "corp_name": "제이와이피엔터테인먼트서비스",
                    "adres": "부산광역시 해운대구",
                    "hm_url": "",
                },
                "00888888": {
                    "status": "000",
                    "corp_name": "JYP Holdings",
                    "adres": "제주특별자치도 제주시",
                    "hm_url": "",
                },
            }
            return profiles[code]

    # 정답을 XML 맨 뒤에 둔다. 앞 3건에서 break하던 구현은 이를 조회하지 못했다.
    catalog = (
        ("00999999", "제이와이피엔터테인먼트서비스", "", "", "20250101"),
        ("00888888", "JYP Holdings", "JYP Holdings Inc.", "", "20250102"),
        ("00535454", "(주)제이와이피", "JYP Corporation", "", "20170630"),
        (
            "00258689",
            "JYP Ent.",
            "JYP Entertainment Corporation",
            "035900",
            "20221206",
        ),
    )
    monkeypatch.setattr(real, "_company_catalog", lambda: catalog)
    for typed in ("JYP", "jyp", "Jyp", "ｊYＰ"):
        engine = JypCandidateEngine()
        monkeypatch.setattr(real, "_engine", lambda: engine)

        rows = real.RealPipeline().search_business_candidates(
            company=typed,
            address_hint="서울 강동구",
            limit=3,
            timeout_sec=8.0,
        )

        assert rows[0]["candidate_ref"] == "00258689"
        assert rows[0]["candidate_name"] == "JYP Ent."
        assert rows[0]["stock_code"] == "035900"
        assert any(row["candidate_ref"] == "00535454" for row in rows)
        assert rows[0]["candidate_ref"] != "00535454"
        assert engine.calls[0] == "00258689"
        assert len(engine.calls) <= 3

    exact_old_engine = JypCandidateEngine()
    monkeypatch.setattr(real, "_engine", lambda: exact_old_engine)
    exact_old = real.RealPipeline().search_business_candidates(
        company="제이와이피",
        address_hint="서울 강남구",
        limit=3,
        timeout_sec=8.0,
    )
    assert exact_old[0]["candidate_ref"] == "00535454"


def test_사람이선택한_DART고유번호는_이름식별없이_그번호만_다시조회한다(monkeypatch):
    engine = _CandidateEngine()
    monkeypatch.setattr(real, "_engine", lambda: engine)
    user_input = UserInput(
        company="JYP",
        job="매니지먼트",
        region="서울 강동구",
        posting_text="채용 공고",
    )

    result = real.RealPipeline().find_company_by_ref_metered(
        user_input, "00258689"
    )

    assert result.failed is False
    assert result.card is not None
    assert result.card.ref == "00258689"
    assert result.card.typed_name == "JYP"
    assert engine.calls == ["00258689"]

    invalid = real.RealPipeline().find_company_by_ref_metered(user_input, "00535454x")
    assert invalid.failed is True
    assert engine.calls == ["00258689"]


def test_corpCode_catalog은_DART_download보다_먼저_기존_env_bootstrap을_호출한다(
    monkeypatch, tmp_path: Path
):
    events: list[str] = []

    class CatalogEngine:
        class UsageCounter:
            pass

        def load_env(self):
            events.append("load_env")

        def download_corpcode(self, _directory, _counter):
            events.append("download")
            assert events == ["load_env", "download"]
            return xml_path

        CORPCODE_DIR = "unused"

    xml_path = tmp_path / "CORPCODE.xml"
    xml_path.write_text(
        "<result><list><corp_code>00000001</corp_code><corp_name>회사</corp_name>"
        "<corp_eng_name>Company Inc.</corp_eng_name><stock_code></stock_code>"
        "<modify_date>20250101</modify_date></list></result>",
        encoding="utf-8",
    )
    monkeypatch.setattr(real, "_engine", lambda: CatalogEngine())
    real._company_catalog.cache_clear()
    try:
        assert real._company_catalog() == (("00000001", "회사"),)
    finally:
        real._company_catalog.cache_clear()
    assert events == ["load_env", "download"]


def test_corpCode_catalog은_후보정렬용_종목코드와_갱신일을_보존한다(
    monkeypatch, tmp_path: Path
):
    xml_path = tmp_path / "CORPCODE.xml"
    xml_path.write_text(
        "<result><list><corp_code>00258689</corp_code><corp_name>JYP Ent.</corp_name>"
        "<corp_eng_name>JYP Entertainment Corporation</corp_eng_name>"
        "<stock_code>035900</stock_code><modify_date>20221206</modify_date>"
        "</list></result>",
        encoding="utf-8",
    )

    class CatalogEngine:
        class UsageCounter:
            pass

        CORPCODE_DIR = "unused"

        def load_env(self):
            return None

        def download_corpcode(self, _directory, _counter):
            return xml_path

    monkeypatch.setattr(real, "_engine", lambda: CatalogEngine())
    real._company_catalog.cache_clear()
    try:
        assert real._company_catalog() == (("00258689", "JYP Ent."),)
        assert real._COMPANY_CATALOG_METADATA["00258689"] == (
            "035900",
            "20221206",
        )
        assert real._COMPANY_CATALOG_ENGLISH_NAMES["00258689"] == (
            "JYP Entertainment Corporation"
        )
    finally:
        real._company_catalog.cache_clear()
