"""DART 후보 보강 lookahead의 독립 안전 계약.

외부 DART 호출 대신 로컬 matcher 결과와 기업개황을 주입한다. 시험의 목적은
특정 회사를 1위로 고정하는 것이 아니라, 다섯 번의 공식 profile 예산 안에서
강한 이름 근거를 빠뜨리지 않으면서 모호한 경우는 그대로 남기는 것이다.
"""

from __future__ import annotations

from src.features.business_candidate.dart_identity import (
    DartCompanyMatch,
    DartCompanyRecord,
)
from src.features.pipeline import real


class _ProfileEngine:
    class UsageCounter:
        pass

    MODEL = ""

    def __init__(self, profiles: dict[str, dict[str, str]]):
        self.profiles = profiles
        self.calls: list[str] = []

    def load_env(self) -> None:
        return None

    def get_json(self, _path, params, _counter):
        corp_code = str(params["corp_code"])
        self.calls.append(corp_code)
        return self.profiles[corp_code]


def _match(
    rank: int,
    kind: str,
    *,
    corp_name: str | None = None,
    english_name: str = "",
    stock_code: str = "",
) -> DartCompanyMatch:
    record = DartCompanyRecord(
        corp_code=f"{rank:08d}",
        corp_name=corp_name or f"후보법인{rank}",
        corp_eng_name=english_name,
        stock_code=stock_code,
        modify_date=f"2025{((rank - 1) % 12) + 1:02d}01",
    )
    return DartCompanyMatch(
        record=record,
        match_kind=kind,
        similarity=1.0,
        matched_name=record.corp_name,
        matched_field="corp_name",
    )


def _profiles(matches: list[DartCompanyMatch]) -> dict[str, dict[str, str]]:
    return {
        match.record.corp_code: {
            "status": "000",
            "corp_name": match.record.corp_name,
            "adres": "부산광역시 중구 중앙대로",
            "hm_url": "",
        }
        for match in matches
    }


def _run(monkeypatch, matches, profiles, *, company="SM", address_hint="서울 성동구"):
    engine = _ProfileEngine(profiles)
    monkeypatch.setattr(real, "_engine", lambda: engine)
    monkeypatch.setattr(
        real,
        "generate_dart_company_matches",
        lambda _index, _query, *, limit: tuple(matches[:limit]),
    )
    monkeypatch.setattr(real, "_company_candidate_index", lambda: object())
    rows = real.RealPipeline().search_business_candidates(
        company=company,
        address_hint=address_hint,
        limit=15,
        timeout_sec=8.0,
    )
    return engine, rows


def test_SM_rank9_acronym_reading은_동일한_profile5회안에서_주소비교기회를_얻는다(
    monkeypatch,
):
    """상위권에 다른 kind가 몰려도 최고 acronym_reading 한 건은 보강한다."""
    matches = [
        _match(1, "exact_name", corp_name="에스엠"),
        _match(2, "exact_name", corp_name="에스엠"),
        _match(3, "acronym_token", corp_name="에스엠화진", stock_code="134780"),
        _match(4, "acronym_token", corp_name="글로벌에스엠", stock_code="900070"),
        _match(5, "acronym_token", corp_name="에스엠벡셀", stock_code="010580"),
        _match(6, "token", corp_name="SM C&C", stock_code="048550"),
        _match(7, "token", corp_name="SM Life Design", stock_code="063440"),
        _match(8, "token", corp_name="신진에스엠", stock_code="138070"),
        _match(
            9,
            "acronym_reading",
            corp_name="에스엠",
            english_name="SM ENTERTAINMENT CO., Ltd.",
            stock_code="041510",
        ),
    ]
    profiles = _profiles(matches)
    profiles["00000009"] = {
        "status": "000",
        "corp_name": "(주)에스엠엔터테인먼트",
        "adres": "서울특별시 성동구 왕십리로",
        "hm_url": "smentertainment.com",
    }

    engine, rows = _run(monkeypatch, matches, profiles)

    assert len(engine.calls) == 5
    assert "00000009" in engine.calls
    assert {"00000001", "00000002"}.issubset(engine.calls)
    assert rows[0]["candidate_ref"] == "00000009"


def test_동종_kind_동명이인은_주소를_읽기도전에_임의후보를_lookahead하지_않는다(
    monkeypatch,
):
    """kind 다양성으로 풀 수 없는 동명이인은 추가 식별정보가 필요하다."""
    matches = [
        _match(rank, "exact_name", corp_name="미래") for rank in range(1, 7)
    ]
    profiles = _profiles(matches)
    # 6위 주소가 입력과 같다는 사실은 profile을 호출하기 전에는 알 수 없다.
    # 이를 미리 안다고 가정해 선발하면 비공식 주소 추론/정답 누설이 된다.
    profiles["00000006"]["adres"] = "서울특별시 성동구 왕십리로"

    engine, rows = _run(
        monkeypatch,
        matches,
        profiles,
        company="미래",
        address_hint="서울 성동구",
    )

    assert engine.calls == [f"{rank:08d}" for rank in range(1, 6)]
    assert "00000006" not in {row["candidate_ref"] for row in rows}
    assert len(rows) == 3


def test_약한_token과_trigram_다양성은_정확법인명후보를_밀어내지_않는다(monkeypatch):
    matches = [
        *[_match(rank, "exact_name", corp_name="동양") for rank in range(1, 6)],
        _match(6, "token", corp_name="동양 물류 서비스"),
        _match(7, "trigram", corp_name="동얀"),
    ]
    profiles = _profiles(matches)

    engine, _rows = _run(monkeypatch, matches, profiles, company="동양")

    assert engine.calls == [f"{rank:08d}" for rank in range(1, 6)]


def test_정확한_corp와_stock_ID는_다양성보다_먼저보호되고_자동확정하지_않는다(
    monkeypatch,
):
    for query in ("00260930", "041510"):
        matches = [
            _match(
                1,
                "exact_id",
                corp_name="에스엠",
                english_name="SM ENTERTAINMENT CO., Ltd.",
                stock_code="041510",
            ),
            _match(2, "exact_name"),
            _match(3, "legal_suffix"),
            _match(4, "acronym_token"),
            _match(5, "acronym_reading"),
            _match(6, "acronym_cross_script"),
        ]
        profiles = _profiles(matches)
        profiles["00000001"] = {
            "status": "000",
            "corp_name": "(주)에스엠엔터테인먼트",
            "adres": "서울특별시 성동구 왕십리로",
            "hm_url": "smentertainment.com",
        }

        engine, rows = _run(
            monkeypatch,
            matches,
            profiles,
            company=query,
            address_hint="모름",
        )

        assert len(engine.calls) == 5
        assert engine.calls[0] == "00000001"
        assert rows[0]["candidate_ref"] == "00000001"
        # 이 API는 후보 행만 반환한다. 사람이 선택하기 전 확정 card는 만들지 않는다.
        assert "confirmed" not in rows[0]


def test_이미_top5인_다른업종약어후보는_불필요한추가호출없이_유지된다(monkeypatch):
    cases = (
        ("HYBE", 1, "legal_suffix"),
        ("YG", 4, "acronym_token"),
        ("JYP", 1, "acronym_token"),
        ("KT", 3, "acronym_reading"),
    )
    for query, target_rank, target_kind in cases:
        matches = [
            _match(rank, "acronym_token", stock_code=f"{rank:06d}")
            for rank in range(1, 9)
        ]
        matches[target_rank - 1] = _match(
            target_rank,
            target_kind,
            corp_name=f"{query} 정답법인",
            english_name=f"{query} Corporation",
            stock_code="123456",
        )
        profiles = _profiles(matches)

        engine, rows = _run(
            monkeypatch,
            matches,
            profiles,
            company=query,
            address_hint="모름",
        )

        assert len(engine.calls) == 5
        assert f"{target_rank:08d}" in engine.calls
        assert len(rows) == 3
