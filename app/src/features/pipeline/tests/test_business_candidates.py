"""DART-local 회사 후보의 외부 호출·환경 bootstrap 상한."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

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


def test_DART_local후보는_화면3개와_분리해_profile을_최대5건만_보강한다(monkeypatch):
    engine = _CandidateEngine()
    monkeypatch.setattr(real, "_engine", lambda: engine)
    monkeypatch.setattr(real, "_company_catalog", _catalog)

    rows = real.RealPipeline().search_business_candidates(
        company="JYP", address_hint="서울 강동구", limit=15, timeout_sec=8.0
    )

    assert len(rows) == 3
    assert engine.calls == [
        "00000010",
        "00000009",
        "00000008",
        "00000007",
        "00000006",
    ]


def test_DART_local후보는_deadline뒤_남은_profile을_계속_부르지_않는다(monkeypatch):
    engine = _CandidateEngine(delay=0.12)
    monkeypatch.setattr(real, "_engine", lambda: engine)
    monkeypatch.setattr(real, "_company_catalog", _catalog)

    rows = real.RealPipeline().search_business_candidates(
        company="JYP", address_hint="서울 강동구", limit=3, timeout_sec=0.03
    )

    assert len(rows) == 1
    assert engine.calls == ["00000010"]


def test_DART_local_profile_형식오류는_후보보강전용_표식으로_구분한다(monkeypatch):
    class InvalidProfileEngine(_CandidateEngine):
        def get_json(self, _path, params, _counter):
            self.calls.append(str(params["corp_code"]))
            return {"status": "013"}

    engine = InvalidProfileEngine()
    monkeypatch.setattr(real, "_engine", lambda: engine)
    monkeypatch.setattr(real, "_company_catalog", _catalog)

    with pytest.raises(real.LocalDartProfileEnrichmentError):
        real.RealPipeline().search_business_candidates(
            company="JYP", address_hint="서울 강동구", limit=3, timeout_sec=8.0
        )
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
    # 다섯 건에서 멈추고, 상장 여부까지 비교한 최종 화면 세 장에는 포함한다.
    assert payload["expected_local_rank"] == 4
    assert engine.calls == [
        "01841468",
        "00249247",
        "00139719",
        "00613318",
        "01931239",
    ]
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
