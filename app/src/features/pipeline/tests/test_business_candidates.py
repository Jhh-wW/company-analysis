"""DART-local 회사 후보의 외부 호출·환경 bootstrap 상한."""

from __future__ import annotations

import functools
import json
import os
import threading
import time
from pathlib import Path

import pytest

from src.features.business_candidate.dart_identity import (
    generate_dart_company_matches,
)
from src.features.pipeline import real
from src.features.pipeline.candidate_profile_constants import (
    DART_PROFILE_ENRICHMENT_LIMIT,
    DART_PROFILE_WORKERS,
)
from src.features.pipeline.constants import CORPCODE_REFRESH_INTERVAL_DAYS
from src.features.pipeline.port import UserInput
from src.shared.company_identity import verified_official_company_names_equivalent


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


def test_dart_enrichment_preserves_raw_candidate_limit_before_display(monkeypatch):
    engine = _CandidateEngine()
    monkeypatch.setattr(real, "_engine", lambda: engine)
    monkeypatch.setattr(real, "_company_catalog", lambda: _catalog(20))

    rows = real.RealPipeline().search_business_candidates(
        company="JYP", address_hint="서울 강동구", limit=15, timeout_sec=8.0
    )

    assert len(rows) == DART_PROFILE_ENRICHMENT_LIMIT == 15
    assert set(engine.calls) == {f"{i:08d}" for i in range(6, 21)}
    assert len(engine.calls) == DART_PROFILE_ENRICHMENT_LIMIT


def test_DART_local후보는_deadline뒤_남은_profile을_계속_부르지_않는다(monkeypatch):
    engine = _CandidateEngine(delay=0.12)
    monkeypatch.setattr(real, "_engine", lambda: engine)
    monkeypatch.setattr(real, "_company_catalog", _catalog)

    with pytest.raises(TimeoutError):
        real.RealPipeline().search_business_candidates(
            company="JYP", address_hint="서울 강동구", limit=3, timeout_sec=0.03
        )
    assert len(engine.calls) <= DART_PROFILE_WORKERS
    assert set(engine.calls) <= {"00000010", "00000009", "00000008"}


def test_sm_official_catalog_collisions_are_compared_within_profile_limit(
    monkeypatch,
):
    target_code = "00260930"
    target_name = "(주)에스엠엔터테인먼트"
    target_address = "서울특별시 성동구 왕십리로 83-21 아크로 서울포레스트 디타워"

    # 2026-09-08 실제 CORPCODE 캐시에서 SM 정답 앞에 있던 공식 레코드다.
    # 정답은 기존 전체 matcher 9위였고 단순 top-5 profile 절단에서는 조회되지 않았다.
    catalog = (
        ("01491917", "에스엠", "SM", "", "20200806"),
        ("01101643", "에스엠", "SM", "", "20170630"),
        ("00238977", "에스엠화진", "SM HWAJIN Co., Ltd.", "134780", "20260604"),
        ("00783246", "글로벌에스엠", "Global SM Tech Limited", "900070", "20260416"),
        ("00147860", "에스엠벡셀", "SM BEXEL CO.,LTD", "010580", "20260227"),
        ("00185046", "SM C&C", "SM Culture & Contents Co., Ltd.", "048550", "20250326"),
        ("00367604", "SM Life Design", "SM Life Design Group Co., Ltd.", "063440", "20250325"),
        ("00577380", "신진에스엠", "SINJIN SM CO.,LTD.", "138070", "20241210"),
        (target_code, "에스엠", "SM ENTERTAINMENT CO., Ltd.", "041510", "20240328"),
    )

    class SmCandidateEngine(_CandidateEngine):
        def get_json(self, _path, params, _counter):
            code = str(params["corp_code"])
            self.calls.append(code)
            if code == target_code:
                return {
                    "status": "000",
                    "corp_name": target_name,
                    "adres": target_address,
                    "hm_url": "smentertainment.com",
                }
            names = {row[0]: row[1] for row in catalog}
            return {
                "status": "000",
                "corp_name": names[code],
                "adres": "",
                "hm_url": "",
            }

    engine = SmCandidateEngine()
    monkeypatch.setattr(real, "_engine", lambda: engine)
    monkeypatch.setattr(real, "_company_catalog", lambda: catalog)

    rows = real.RealPipeline().search_business_candidates(
        company="SM", address_hint=target_address, limit=3, timeout_sec=8.0
    )

    assert set(engine.calls) == {row[0] for row in catalog}
    assert len(engine.calls) == len(catalog) <= DART_PROFILE_ENRICHMENT_LIMIT
    assert rows[0]["candidate_ref"] == target_code
    assert rows[0]["candidate_name"] == target_name
    # 약어 토큰 일치가 한글 읽기 일치보다 앞서므로 첫 행의 종류는 acronym_token이다.
    assert rows[0]["name_match_kind"] == "acronym_token"
    assert verified_official_company_names_equivalent(
        rows[0]["candidate_name"],
        target_name,
        observed_corp_code=rows[0]["candidate_ref"],
        expected_corp_code=target_code,
    )

    # 사용자가 서명된 후보를 고른 뒤에는 같은 DART 코드를 한 번 다시 조회한다.
    # 실제 확인 manifest의 법인명·코드 쌍은 기존 evaluator 등가 경계를 그대로 통과한다.
    selected_engine = SmCandidateEngine()
    monkeypatch.setattr(real, "_engine", lambda: selected_engine)
    selected = real.RealPipeline().find_company_by_ref_metered(
        UserInput(company="SM", job="엔터테인먼트", region=target_address),
        target_code,
    )
    assert selected.failed is False
    assert selected.card is not None
    assert selected.card.ref == target_code
    assert selected.card.legal_name == target_name
    assert selected_engine.calls == [target_code]
    assert verified_official_company_names_equivalent(
        selected.card.legal_name,
        target_name,
        observed_corp_code=selected.card.ref,
        expected_corp_code=target_code,
    )


def test_DART_local_profile_형식오류는_후보보강전용_표식으로_구분한다(monkeypatch):
    class InvalidProfileEngine(_CandidateEngine):
        def get_json(self, _path, params, _counter):
            self.calls.append(str(params["corp_code"]))
            return {"status": "900"}

    engine = InvalidProfileEngine()
    monkeypatch.setattr(real, "_engine", lambda: engine)
    monkeypatch.setattr(real, "_company_catalog", _catalog)

    with pytest.raises(real.LocalDartProfileEnrichmentError):
        real.RealPipeline().search_business_candidates(
            company="JYP", address_hint="서울 강동구", limit=3, timeout_sec=8.0
        )
    assert 0 < len(engine.calls) <= DART_PROFILE_WORKERS


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


def test_YG는_공식영문명_약어로_무료_DART후보만_내고_유사문자는_거부한다(
    monkeypatch,
):
    class YgCandidateEngine(_CandidateEngine):
        def get_json(self, _path, params, _counter):
            code = str(params["corp_code"])
            self.calls.append(code)
            assert code == "00613318"
            return {
                "status": "000",
                "corp_name": "와이지엔터테인먼트",
                "adres": "",
                "hm_url": "",
            }

    catalog = (
        (
            "00613318",
            "와이지엔터테인먼트",
            "YG Entertainment Inc.",
            "122870",
            "20240401",
        ),
        (
            "00258689",
            "JYP Ent.",
            "JYP Entertainment Corporation",
            "035900",
            "20221206",
        ),
        (
            "00136689",
            "(주)에스엠엔터테인먼트",
            "SM Entertainment Co., Ltd.",
            "041510",
            "20240329",
        ),
    )
    monkeypatch.setattr(real, "_company_catalog", lambda: catalog)

    for typed in ("YG", "yg", "Yg", "ｙＧ"):
        engine = YgCandidateEngine()
        monkeypatch.setattr(real, "_engine", lambda: engine)

        rows = real.RealPipeline().search_business_candidates(
            company=typed,
            address_hint="",
            limit=3,
            timeout_sec=8.0,
        )

        assert [row["candidate_ref"] for row in rows] == ["00613318"]
        assert rows[0]["candidate_name"] == "와이지엔터테인먼트"
        assert rows[0]["english_name"] == "YG Entertainment Inc."
        assert rows[0]["stock_code"] == "122870"
        assert rows[0]["modify_date"] == "20240401"
        assert rows[0]["name_match_kind"] == "acronym_token"
        assert engine.calls == ["00613318"]

    for rejected in ("Y G", "YG1", "ҮG"):
        engine = YgCandidateEngine()
        monkeypatch.setattr(real, "_engine", lambda: engine)

        assert real.RealPipeline().search_business_candidates(
            company=rejected,
            address_hint="",
            limit=3,
            timeout_sec=8.0,
        ) == []
        assert engine.calls == []


def test_YG_전체목록형_동명후보에서도_rank4_상장법인을_최종3개에_남긴다(
    monkeypatch,
):
    fixture_path = (
        Path(__file__).parents[2]
        / "business_candidate"
        / "tests"
        / "fixtures"
        / "dart_yg_full_catalog_slice.json"
    )
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    catalog = tuple(
        (
            row["corp_code"],
            row["corp_name"],
            row["corp_eng_name"],
            row["stock_code"],
            row["modify_date"],
        )
        for row in payload["records"]
    )
    names = {row["corp_code"]: row["corp_name"] for row in payload["records"]}

    class FullCatalogSliceEngine(_CandidateEngine):
        def get_json(self, _path, params, _counter):
            code = str(params["corp_code"])
            self.calls.append(code)
            return {
                "status": "000",
                "corp_name": names[code],
                "adres": "",
                "hm_url": "",
            }

    monkeypatch.setattr(real, "_company_catalog", lambda: catalog)
    engine = FullCatalogSliceEngine()
    monkeypatch.setattr(real, "_engine", lambda: engine)

    rows = real.RealPipeline().search_business_candidates(
        company="YG", address_hint="", limit=3, timeout_sec=8.0
    )

    # 공식 전체 목록에서는 목표 법인이 이름-only 순위 4위다. profile 보강은
    # 원시 후보 상한에서 멈춘다. 정확명·법적접미사 근거를 먼저 지킨 뒤에도 목표 법인은
    # 상장 여부까지 비교한 최종 화면 세 장에 포함된다.
    assert payload["expected_local_rank"] == 4
    assert {
        "01841468",
        "00617086",
        "00249247",
        "00139719",
        "00613318",
    }.issubset(engine.calls)
    assert len(engine.calls) == DART_PROFILE_ENRICHMENT_LIMIT
    assert len(rows) == 3
    assert "00613318" in [str(row["candidate_ref"]) for row in rows]
    target = next(row for row in rows if row["candidate_ref"] == "00613318")
    assert target["candidate_name"] == "와이지엔터테인먼트"
    assert target["stock_code"] == "122870"
    assert target["name_match_kind"] == "acronym_token"

    for rejected in ("ҮG", "JҮP", "ΑG"):
        rejected_engine = FullCatalogSliceEngine()
        monkeypatch.setattr(real, "_engine", lambda: rejected_engine)
        assert real.RealPipeline().search_business_candidates(
            company=rejected,
            address_hint="",
            limit=3,
            timeout_sec=8.0,
        ) == []
        assert rejected_engine.calls == []


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


def test_동시에_처음_색인을_요청해도_카탈로그_함수는_한번만_실행된다(monkeypatch):
    """``_company_candidate_index()``가 catalog 조회를 잠금 «안»에서 부르는지 보는 배선 시험.

    ``functools.lru_cache``는 캐시가 비어 있을 때(cache miss) 원본 함수를
    **잠금을 놓은 채** 실행하는 구현이다(CPython ``Lib/functools.py``의
    ``_lru_cache_wrapper``, 실측: 같은 lru_cache 함수를 두 스레드에서 동시에
    처음 부르면 ``cache_info().misses == 2``). catalog 조회
    (``real._company_catalog()``)를 ``_COMPANY_CANDIDATE_INDEX_LOCK`` 밖에서
    부르면(예전 코드), 기동 예열 스레드와 첫 검색 요청이 동시에 들어올 때
    두 스레드가 동시에 30MB corpCode XML을 내려받아 파싱한다. 이 시험은
    catalog 조회를 진짜 ``functools.lru_cache``로 감싼 가짜 함수로 바꿔
    끼워, 두 번째 호출자가 첫 번째가 이미 채운 lru_cache를 기다렸다가
    재사용하는지(= catalog 함수 몸체가 한 번만 실행되는지) 확인한다.
    """

    calls: list[int] = []
    catalog_entered = threading.Event()
    release_catalog = threading.Event()

    @functools.lru_cache(maxsize=1)
    def slow_catalog():
        calls.append(1)
        catalog_entered.set()
        assert release_catalog.wait(timeout=2.0), "시험 스레드 조율에 실패했다"
        return (("00000001", "회사"),)

    monkeypatch.setattr(real, "_company_catalog", slow_catalog)
    monkeypatch.setattr(real, "_COMPANY_CANDIDATE_INDEX_SOURCE", None)
    monkeypatch.setattr(real, "_COMPANY_CANDIDATE_INDEX", None)

    results: list[object] = []

    def worker():
        results.append(real._company_candidate_index())

    first = threading.Thread(target=worker)
    first.start()
    assert catalog_entered.wait(timeout=2.0), "첫 호출이 catalog 함수에 들어가지 않았다"

    # 첫 스레드가 catalog 함수 «안»에 멈춰 있는 동안 두 번째 호출을 시작한다.
    # 잠금이 catalog 조회까지 감싸면 두 번째 스레드는 잠금 대기에서 멈추고
    # slow_catalog는 다시 불리지 않아야 한다(= calls는 계속 [1]).
    second = threading.Thread(target=worker)
    second.start()
    time.sleep(0.1)
    assert len(calls) == 1, (
        "catalog 조회가 잠금 밖에서 불려 두 번째 스레드가 또 실행했다 "
        "(_company_candidate_index가 회귀했을 수 있다)"
    )

    release_catalog.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)
    assert not first.is_alive()
    assert not second.is_alive()

    assert len(calls) == 1
    assert len(results) == 2
    assert results[0] is results[1]


def test_기동_예열_진입점은_첫_검색과_같은_색인을_채운다(monkeypatch):
    """``prewarm_business_candidate_index()``가 검색과 같은 lru_cache를 채우는지 본다."""

    catalog = (("00000001", "예열회사"),)
    monkeypatch.setattr(real, "_company_catalog", lambda: catalog)
    monkeypatch.setattr(real, "_COMPANY_CANDIDATE_INDEX_SOURCE", None)
    monkeypatch.setattr(real, "_COMPANY_CANDIDATE_INDEX", None)
    monkeypatch.setattr(real, "_COMPANY_CATALOG_RECORDS", ())

    count = real.prewarm_business_candidate_index()

    # 예열이 채운 색인을, 검색이 쓰는 바로 그 함수로 다시 불러도 새로 만들지 않는다
    # (catalog identity가 같아 재사용된다) — 예열과 검색이 같은 캐시를 본다는 증거다.
    assert real._company_candidate_index() is real._COMPANY_CANDIDATE_INDEX
    assert real._COMPANY_CANDIDATE_INDEX_SOURCE is catalog
    assert count == 0  # _COMPANY_CATALOG_RECORDS는 fake _company_catalog가 채우지 않는다


# ── 법인목록 7일 주기 자동 갱신 ────────────────────────────


def _corpcode_xml(entries: list[tuple[str, str]]) -> bytes:
    """corp_code·corp_name 쌍만으로 최소 유효 corpCode.xml 바이트를 만든다."""

    items = "".join(
        f"<list><corp_code>{code}</corp_code><corp_name>{name}</corp_name>"
        "<corp_eng_name></corp_eng_name><stock_code></stock_code>"
        "<modify_date>20260101</modify_date></list>"
        for code, name in entries
    )
    return f"<result>{items}</result>".encode("utf-8")


class _FakeRefreshEngine:
    """corpCode 다운로드만 흉내 내는 가짜 엔진 — 네트워크 없이 파일로만 오간다."""

    class UsageCounter:
        pass

    def __init__(
        self,
        corpcode_dir: Path,
        *,
        fresh_xml: bytes | None = None,
        fail: Exception | None = None,
    ):
        self.CORPCODE_DIR = corpcode_dir
        self._fresh_xml = fresh_xml
        self._fail = fail
        self.fresh_calls = 0

    def load_env(self):
        return None

    def download_corpcode(self, dest_dir, _counter):
        # 정본이 이미 있다고 가정한다(시험이 미리 파일을 써 둔다) — 운영
        # download_corpcode의 «있으면 재사용» 계약과 같은 모양이다.
        return Path(dest_dir) / "CORPCODE.xml"

    def download_corpcode_fresh(self, dest_dir, _counter):
        self.fresh_calls += 1
        if self._fail is not None:
            raise self._fail
        temp_path = Path(dest_dir) / "CORPCODE.xml.new"
        temp_path.write_bytes(self._fresh_xml)
        return temp_path


def _clear_catalog_globals() -> None:
    real._company_catalog.cache_clear()
    real._COMPANY_CANDIDATE_INDEX_SOURCE = None
    real._COMPANY_CANDIDATE_INDEX = None
    real._PRELOADED_CATALOG = None
    real._COMPANY_CATALOG_RECORDS = ()
    real._COMPANY_CATALOG_METADATA = {}
    real._COMPANY_CATALOG_ENGLISH_NAMES = {}


@pytest.fixture
def _fresh_catalog_state():
    """7일 주기 갱신 시험이 lru_cache·전역 catalog 상태를 다른 시험과 안 섞이게 한다."""

    _clear_catalog_globals()
    yield
    _clear_catalog_globals()


def test_법인목록_나이가_6점9일이면_갱신_안하고_7점1일이면_한다(
    tmp_path, monkeypatch, _fresh_catalog_state
):
    """``refresh_business_candidate_catalog_if_stale``의 나이 경계를 실제 함수로 본다."""

    assert CORPCODE_REFRESH_INTERVAL_DAYS == 7, (
        "이 시험의 6.9일/7.1일 리터럴은 7일 주기를 가정한다 — 상수가 바뀌면 "
        "이 시험의 리터럴도 함께 맞춘다"
    )
    corpcode_dir = tmp_path / "corpcode"
    corpcode_dir.mkdir()
    xml_path = corpcode_dir / "CORPCODE.xml"
    xml_path.write_bytes(_corpcode_xml([("00000001", "옛회사")]))

    fake_engine = _FakeRefreshEngine(
        corpcode_dir, fresh_xml=_corpcode_xml([("00000002", "새회사")])
    )
    monkeypatch.setattr(real, "_engine", lambda: fake_engine)

    now = time.time()
    six_point_nine_days_ago = now - 6.9 * 86_400
    os.utime(xml_path, (six_point_nine_days_ago, six_point_nine_days_ago))
    assert real.refresh_business_candidate_catalog_if_stale() is False
    assert fake_engine.fresh_calls == 0

    seven_point_one_days_ago = now - 7.1 * 86_400
    os.utime(xml_path, (seven_point_one_days_ago, seven_point_one_days_ago))
    assert real.refresh_business_candidate_catalog_if_stale() is True
    assert fake_engine.fresh_calls == 1


def test_법인목록_갱신_사이클은_옛회사를_빼고_새회사를_찾으며_임시파일을_안남긴다(
    tmp_path, monkeypatch, _fresh_catalog_state
):
    """갱신 뒤 ``_company_candidate_index()``가 새 회사를 찾고 옛 회사는 못 찾는다.

    ``search_business_candidates``가 실제로 쓰는 함수
    (``generate_dart_company_matches`` + ``_company_candidate_index()``)를
    그대로 불러 단정한다 — 운영 함수 그대로 통과.
    """

    corpcode_dir = tmp_path / "corpcode"
    corpcode_dir.mkdir()
    xml_path = corpcode_dir / "CORPCODE.xml"
    xml_path.write_bytes(_corpcode_xml([("00000001", "옛회사이름")]))
    stale_mtime = time.time() - 8 * 86_400
    os.utime(xml_path, (stale_mtime, stale_mtime))

    fake_engine = _FakeRefreshEngine(
        corpcode_dir, fresh_xml=_corpcode_xml([("00000002", "새회사이름")])
    )
    monkeypatch.setattr(real, "_engine", lambda: fake_engine)

    old_index = real._company_candidate_index()
    old_matches = generate_dart_company_matches(old_index, "옛회사이름", limit=5)
    assert any(match.record.corp_name == "옛회사이름" for match in old_matches)

    replaced = real.refresh_business_candidate_catalog_if_stale()

    assert replaced is True
    assert fake_engine.fresh_calls == 1
    assert not (corpcode_dir / "CORPCODE.xml.new").exists()
    assert xml_path.read_bytes() == _corpcode_xml([("00000002", "새회사이름")])

    new_index = real._company_candidate_index()
    assert new_index is not old_index
    new_matches = generate_dart_company_matches(new_index, "새회사이름", limit=5)
    assert any(match.record.corp_name == "새회사이름" for match in new_matches)
    stale_matches = generate_dart_company_matches(new_index, "옛회사이름", limit=5)
    assert not any(match.record.corp_name == "옛회사이름" for match in stale_matches)


def test_스왑_전까지는_옛_색인이_계속_서비스된다(
    tmp_path, monkeypatch, _fresh_catalog_state
):
    """락 밖에서 내려받는 동안, 다른 스레드가 색인을 불러도 옛 색인이 나온다."""

    corpcode_dir = tmp_path / "corpcode"
    corpcode_dir.mkdir()
    xml_path = corpcode_dir / "CORPCODE.xml"
    xml_path.write_bytes(_corpcode_xml([("00000001", "옛회사")]))
    stale_mtime = time.time() - 8 * 86_400
    os.utime(xml_path, (stale_mtime, stale_mtime))

    download_entered = threading.Event()
    release_download = threading.Event()

    class _BlockingEngine(_FakeRefreshEngine):
        def download_corpcode_fresh(self, dest_dir, counter):
            download_entered.set()
            assert release_download.wait(timeout=2.0), "시험이 다운로드를 안 풀어줬다"
            return super().download_corpcode_fresh(dest_dir, counter)

    fake_engine = _BlockingEngine(
        corpcode_dir, fresh_xml=_corpcode_xml([("00000002", "새회사")])
    )
    monkeypatch.setattr(real, "_engine", lambda: fake_engine)

    old_index = real._company_candidate_index()

    refresh_thread = threading.Thread(
        target=real.refresh_business_candidate_catalog_if_stale
    )
    refresh_thread.start()
    assert download_entered.wait(timeout=2.0), "갱신 스레드가 다운로드에 들어가지 않았다"

    # 다운로드가 아직 안 끝났다 — 락 밖이므로 다른 스레드는 안 막히고 옛 색인을 받는다.
    during_index = real._company_candidate_index()
    assert during_index is old_index

    release_download.set()
    refresh_thread.join(timeout=2.0)
    assert not refresh_thread.is_alive()

    after_index = real._company_candidate_index()
    assert after_index is not old_index


def test_다운로드가_실패하면_파일과_색인이_그대로고_비밀이_로그에_없다(
    tmp_path, monkeypatch, caplog, _fresh_catalog_state
):
    """실패 격리: 옛 파일·옛 색인을 그대로 쓰고, 경고 로그에 비밀이 없다."""

    corpcode_dir = tmp_path / "corpcode"
    corpcode_dir.mkdir()
    xml_path = corpcode_dir / "CORPCODE.xml"
    xml_path.write_bytes(_corpcode_xml([("00000001", "옛회사")]))
    stale_mtime = time.time() - 8 * 86_400
    os.utime(xml_path, (stale_mtime, stale_mtime))

    secret = "절대-로그에-남으면-안되는-DART-키-문자열"
    fake_engine = _FakeRefreshEngine(corpcode_dir, fail=RuntimeError(secret))
    monkeypatch.setattr(real, "_engine", lambda: fake_engine)

    old_index = real._company_candidate_index()
    old_bytes = xml_path.read_bytes()

    with caplog.at_level("WARNING", logger=real.logger.name):
        replaced = real.refresh_business_candidate_catalog_if_stale()

    assert replaced is False
    assert xml_path.read_bytes() == old_bytes
    assert real._company_candidate_index() is old_index
    assert secret not in caplog.text
    assert not (corpcode_dir / "CORPCODE.xml.new").exists()


@pytest.mark.parametrize(
    ("fresh_xml", "label"),
    [
        (b"<not-well-formed", "파싱실패"),
        (b"<result></result>", "레코드0건"),
    ],
)
def test_파싱실패_또는_레코드0건이면_바꿔끼우지_않는다(
    tmp_path, monkeypatch, _fresh_catalog_state, fresh_xml, label
):
    corpcode_dir = tmp_path / "corpcode"
    corpcode_dir.mkdir()
    xml_path = corpcode_dir / "CORPCODE.xml"
    xml_path.write_bytes(_corpcode_xml([("00000001", "옛회사")]))
    stale_mtime = time.time() - 8 * 86_400
    os.utime(xml_path, (stale_mtime, stale_mtime))

    fake_engine = _FakeRefreshEngine(corpcode_dir, fresh_xml=fresh_xml)
    monkeypatch.setattr(real, "_engine", lambda: fake_engine)

    old_index = real._company_candidate_index()
    old_bytes = xml_path.read_bytes()

    replaced = real.refresh_business_candidate_catalog_if_stale()

    assert replaced is False, label
    assert xml_path.read_bytes() == old_bytes, label
    assert real._company_candidate_index() is old_index, label
    assert not (corpcode_dir / "CORPCODE.xml.new").exists(), label


# ── 후보 AI 보조 재정렬 ask ────────────────────────────────


class _RerankAskEngine:
    """계량 껍데기 «안쪽»의 1판 엔진 자리에 들어가는 가짜. 네트워크를 쓰지 않는다."""

    MODEL = "engine-default"

    def __init__(self, payload, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.loaded = 0
        self.asked: list[dict] = []

    def load_env(self):
        self.loaded += 1

    def _client(self):
        # 계량 껍데기는 `client.messages` 경계를 요구한다. 가짜도 그 계약을 갖춰야
        # 0원으로 새는 구멍을 숨기지 않는다.
        engine = self

        class _Messages:
            @staticmethod
            def create(*_args, **_kwargs):  # pragma: no cover - 부르면 실패다
                raise AssertionError("가짜 _ask는 provider를 부르지 않는다")

        class _Client:
            messages = _Messages()

        engine.client = _Client()
        return engine.client

    def _ask(self, client, prompt, schema, max_tokens):
        self.asked.append(
            {"prompt": prompt, "schema": schema, "max_tokens": max_tokens}
        )
        if self.error is not None:
            raise self.error
        return self.payload, {}


def _record_spent(monkeypatch, amount: float) -> list[tuple[str, str]]:
    """`_request_spent_krw`가 불릴 때의 모델·단계를 기록하고 고정 금액을 돌려준다."""

    seen: list[tuple[str, str]] = []

    def fake(metered):
        seen.append((str(metered.MODEL), str(metered.current_stage)))
        return amount

    monkeypatch.setattr(real, "_request_spent_krw", fake)
    return seen


def test_후보재정렬_ask는_haiku로_짧은_JSON만_받고_비용을_모은다(monkeypatch):
    engine = _RerankAskEngine({"order": [2, 0]})
    monkeypatch.setattr(real, "_engine", lambda: engine)
    seen = _record_spent(monkeypatch, 1.5)

    ask = real.RealPipeline().make_candidate_rerank_ask()
    answer = ask("후보 순서를 정해 주세요")

    assert answer == '{"order":[2,0]}'
    assert engine.loaded == 1
    assert engine.asked[0]["prompt"] == "후보 순서를 정해 주세요"
    assert engine.asked[0]["max_tokens"] == 200
    assert engine.asked[0]["schema"]["required"] == ["order"]
    assert engine.asked[0]["schema"]["additionalProperties"] is False
    # 모델·단계는 provider에 나가는 경계에서 이 요청 로컬 값으로 확정돼야 한다.
    assert seen == [("claude-haiku-4-5", "candidate_rerank")]
    assert ask.spent_krw == 1.5
    assert ask.calls == 1

    ask("한 번 더")
    assert ask.spent_krw == 3.0
    assert ask.calls == 2


def test_후보재정렬_ask는_payload가_없으면_빈문자열을_돌려준다(monkeypatch):
    monkeypatch.setattr(real, "_engine", lambda: _RerankAskEngine(None))
    _record_spent(monkeypatch, 0.0)

    ask = real.RealPipeline().make_candidate_rerank_ask()

    assert ask("후보 순서를 정해 주세요") == ""


def test_후보재정렬_ask가_실패해도_이미_나간_호출의_비용은_남는다(monkeypatch):
    engine = _RerankAskEngine(None, error=RuntimeError("provider 오류"))
    monkeypatch.setattr(real, "_engine", lambda: engine)
    _record_spent(monkeypatch, 0.9)

    ask = real.RealPipeline().make_candidate_rerank_ask()
    with pytest.raises(RuntimeError):
        ask("후보 순서를 정해 주세요")

    assert ask.spent_krw == 0.9
    assert ask.calls == 1


def test_재정렬_예약액은_후보_상한_프롬프트의_호출전_추정액을_덮는다():
    """예약이 추정액보다 작으면 재정렬은 전송 전에 100% 거절된다.

    추정액은 생산 경로(`estimate_request_tokens_exact` → `usage_cost_krw`)로
    직접 잰다. 상수를 낮추거나 프롬프트가 커지면 이 시험이 먼저 깨진다.
    """

    from src.features.budget import provider_budget  # noqa: PLC0415
    from src.features.business_candidate import ai_rerank  # noqa: PLC0415
    from src.features.business_candidate.constants import (  # noqa: PLC0415
        AI_RERANK_MAX_CANDIDATES,
        AI_RERANK_RESERVE_KRW,
    )
    from src.features.business_candidate.logic import (  # noqa: PLC0415
        BusinessCandidate,
    )

    def candidate(index: int) -> BusinessCandidate:
        return BusinessCandidate(
            candidate_name=f"주식회사가나다전자네트웍스코리아{index}",
            address=f"서울특별시 강남구 테헤란로 {index}길 123 가나다빌딩 {index}층",
            homepage="",
            source_label="전자공시(DART) 기업개황",
            source_url="https://opendart.fss.or.kr/",
            provider_name="DART",
            attributions=(),
            score=0.44,
            evidence=(),
            candidate_ref=f"0000000{index}",
            stock_code="123456",
            modify_date="20250101",
            english_name=f"GANADA ELECTRONICS NETWORKS KOREA {index} CO., LTD.",
            name_match_kind="",
            name_similarity=0.0,
        )

    prompt = ai_rerank.build_rerank_prompt(
        query="가나다전자",
        address_hint="서울 강남구",
        candidates=[candidate(index) for index in range(AI_RERANK_MAX_CANDIDATES)],
    )
    call_kwargs = {
        "model": "claude-haiku-4-5",
        "max_tokens": 200,
        "temperature": 0,
        "messages": [{"role": "user", "content": prompt}],
        "output_config": {
            "format": {
                "type": "json_schema",
                "schema": real._CANDIDATE_RERANK_SCHEMA,
            }
        },
    }
    # provider가 입력 token을 세어 주지 못하는 최악의 경우(바이트 추정)를 잰다.
    estimated_input = provider_budget.estimate_request_tokens_exact(
        {"args": (), "kwargs": call_kwargs}, exact_input_tokens=None
    )
    estimate_krw = provider_budget.usage_cost_krw(
        "claude-haiku-4-5", estimated_input, 200
    )

    # 2026-09-07 실측 13.78원(고정 여유 4,096 token만으로 5.74원).
    assert 12.0 < estimate_krw <= AI_RERANK_RESERVE_KRW
